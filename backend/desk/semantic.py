"""Model search over a stable candidate set with bounded, resumable requests."""

import json
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError, NotFound
from .external import analyze, ExternalError
from .models import SemanticRun
from .domain import query_events, check_state
from .snapshots import load, freeze, consistent_read, project_events, filter_events


def start(user, filters, question):
    if (
        not isinstance(filters, dict)
        or not isinstance(question, str)
        or not question.strip()
        or len(question) > 1200
    ):
        raise ValidationError("请提供有效筛选条件及 1–1200 字的问题。")
    scope = filters.get("state") or "approved"
    check_state(user, scope)
    if filters.get("snapshot"):
        rows = filter_events(load(user, filters["snapshot"], scope).rows, filters)
    else:
        with consistent_read():
            rows = project_events(
                query_events(user, filters).order_by("-approved", "-pk"), user
            )
    source = freeze(user, scope, rows)
    return SemanticRun.objects.create(candidates=source, question=question.strip())


def batch(rows, cursor):
    items = []
    size = 0
    for i in range(cursor, min(len(rows), cursor + 50)):
        row = rows[i]
        item = {
            k: row[k]
            for k in (
                "id",
                "title",
                "summary",
                "category",
                "subtype",
                "importance",
                "occurred",
            )
        }
        item["media"] = [m["name"] for m in row["media"]]
        length = len(json.dumps(item, ensure_ascii=False))
        if length > 28000:
            if items:
                break
            raise ExternalError(
                f"检索未完成（{cursor}/{len(rows)}）。事件 #{row['id']} 超出单批模型输入上限。"
            )
        if items and size + length > 28000:
            break
        items.append(item)
        size += length
    return items


def advance(user, run_id, expected_cursor, model=analyze):
    try:
        run = SemanticRun.objects.select_related("candidates").get(
            pk=run_id, candidates__owner=user
        )
    except (SemanticRun.DoesNotExist, ValueError, ModelValidationError):
        raise NotFound("检索任务不存在。")
    check_state(user, run.candidates.scope)
    rows = run.candidates.rows
    if not run.result_id and run.cursor == expected_cursor:
        items = batch(rows, run.cursor)
        ids = []
        if items:
            try:
                reply = model(
                    "按语义选择与问题相关的事件，返回本批材料内的 ids 整数数组。",
                    {"question": run.question, "events": items},
                )
                if not isinstance(reply.get("ids"), list) or any(
                    type(i) is not int for i in reply["ids"]
                ):
                    raise ExternalError("模型检索结果格式无效。")
            except ExternalError as exc:
                raise ExternalError(
                    f"检索未完成（{run.cursor}/{len(rows)}）。{exc}"
                ) from exc
            wanted = set(reply["ids"])
            ids = [r["id"] for r in items if r["id"] in wanted]
        with transaction.atomic():
            locked = SemanticRun.objects.select_for_update().get(pk=run.pk)
            if not locked.result_id and locked.cursor == run.cursor:
                locked.matched.extend(ids)
                locked.cursor += len(items)
                if locked.cursor == len(rows):
                    keep = set(locked.matched)
                    locked.result = freeze(
                        user, run.candidates.scope, [r for r in rows if r["id"] in keep]
                    )
                locked.save(update_fields=["cursor", "matched", "result"])
            run = locked
    return {
        "search": str(run.pk),
        "scanned": run.cursor,
        "total": len(rows),
        "complete": bool(run.result_id),
        "snapshot": run.result_id,
        "ids": run.matched if run.result_id else [],
    }
