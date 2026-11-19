"""Archive an explicitly approved, immutable inventory of historical events."""

import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from desk.history import lock_publications, report_ids
from desk.models import Change, Edition, Event, FrozenList, Insight, Report

FORMAT = "press-event-archive-v1"


def dependencies(ids):
    reports = [
        {
            "id": r.pk,
            "kind": r.kind,
            "state": r.state,
            "version": r.version,
            "shared": bool(r.share_token),
        }
        for r in Report.objects.all()
        if ids & report_ids(r.content)
    ]
    editions = [
        {"report": e.report_id, "version": e.version}
        for e in Edition.objects.all()
        if ids & report_ids(e.content)
    ]
    frozen = sum(
        any(row.get("id") in ids for row in f.rows)
        for f in FrozenList.objects.filter(
            scope__in=("approved", "pending", "filtered", "rejected")
        )
    )
    insights = sum(
        bool(ids.intersection(item))
        for item in Insight.objects.values_list("event_ids", flat=True)
    )
    return {
        "reports": reports,
        "editions": editions,
        "frozen_lists": frozen,
        "insights": insights,
    }


def entry(event):
    return {
        "id": event.pk,
        "version": event.version,
        "state": event.state,
        "title": event.title,
        "approved": event.approved.isoformat() if event.approved else None,
        "urls": sorted(a.url for a in event.articles.all()),
    }


class Command(BaseCommand):
    help = "私有逐条预览、版本校验后归档或撤回事件；保留原文和审核审计。"

    def add_arguments(self, parser):
        parser.add_argument("mode", choices=["preview", "apply", "undo"])
        parser.add_argument("file", type=Path)
        parser.add_argument("--sha256", default="")

    @transaction.atomic
    def handle(self, mode, file, sha256, **kwargs):
        if mode == "preview":
            rows = [
                entry(e)
                for e in Event.objects.exclude(state="archived")
                .prefetch_related("articles")
                .order_by("pk")
            ]
            data = json.dumps(
                {
                    "format": FORMAT,
                    "created": timezone.now().isoformat(),
                    "rows": rows,
                    "dependencies": dependencies({r["id"] for r in rows}),
                },
                indent=2,
                ensure_ascii=False,
            ).encode()
            with file.open("xb") as output:
                file.chmod(0o600)
                output.write(data)
            self.stdout.write(
                f"{len(rows)} 条，SHA-256 {hashlib.sha256(data).hexdigest()}"
            )
            return
        data = file.read_bytes()
        batch = hashlib.sha256(data).hexdigest()
        if sha256 != batch:
            raise CommandError("文件与获批摘要不一致。")
        plan = json.loads(data)
        if plan.get("format") != FORMAT:
            raise CommandError("归档清单格式无效。")
        lock_publications()
        selected = {r["id"]: r for r in plan["rows"]}
        if len(selected) != len(plan["rows"]):
            raise CommandError("归档清单有重复编号。")
        events = list(
            Event.objects.select_for_update()
            .filter(pk__in=selected)
            .order_by("pk")
            .prefetch_related("articles")
        )
        if len(events) != len(selected):
            raise CommandError("清单记录有变化。")
        impact = dependencies(set(selected))
        if impact["reports"] or impact["editions"]:
            raise CommandError("存在草稿或签发版引用，须另行逐期评阅，已取消整批操作。")
        changed = 0
        for event in events:
            row = selected[event.pk]
            log = event.changes.filter(detail__batch=batch, action="历史归档").first()
            undone = event.changes.filter(
                detail__batch=batch, action="撤回历史归档"
            ).exists()
            if mode == "apply":
                if log:
                    if (
                        undone
                        or event.state != "archived"
                        or event.version != log.detail["version"]
                    ):
                        raise CommandError("此批次已变化或撤回，需要重新预览。")
                    continue
                if row != entry(event):
                    raise CommandError(f"事件 {event.pk} 与预览有差异，整批取消。")
                action, new_state = "历史归档", "archived"
            else:
                if undone:
                    continue
                if (
                    not log
                    or event.state != "archived"
                    or event.version != log.detail["version"]
                ):
                    raise CommandError(f"事件 {event.pk} 未归档或已经变化，整批取消。")
                action, new_state = "撤回历史归档", row["state"]
            old_state = event.state
            event.version += 1
            event.state = new_state
            event.save(update_fields=["state", "version"])
            Change.objects.create(
                event=event,
                actor="历史归档命令",
                action=action,
                detail={
                    "batch": batch,
                    "before": old_state,
                    "after": new_state,
                    "version": event.version,
                },
            )
            changed += 1
        self.stdout.write(f"{action if changed else mode}，实际更新 {changed} 条。")
