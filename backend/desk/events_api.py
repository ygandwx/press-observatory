from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import semantic as search_service
from .api import endpoint
from .catalog import EVENTS, IMPORTANCE
from .dashboard_metrics import business_alerts, stock_comparisons
from .domain import (
    bounded_int,
    check_state,
    check_version,
    event_row,
    merge,
    parse_day,
    query_events,
    record,
    require,
    review,
)
from .event_export import export_events
from .external import ExternalError, analyze, document
from .models import Change, Event, Insight, Report
from .snapshots import (
    consistent_read,
    filter_events,
    freeze,
    load,
    project_events,
    project_media,
)


def page_rows(req, qs, transform):
    page = bounded_int(req.GET.get("page", 1), 1, 1000000)
    size = bounded_int(req.GET.get("size", 20), 1, 100)
    return {
        "count": len(qs) if isinstance(qs, list) else qs.count(),
        "page": page,
        "size": size,
        "items": [transform(e) for e in qs[(page - 1) * size : page * size]],
    }


@endpoint(["GET", "POST"])
def events(req):
    if req.method == "POST":
        require(req.user, "edit")
        e, new = record(req.data, req.user.username)
        return Response(event_row(e, req.user), status=201 if new else 200)
    ids = None
    if req.GET.get("ids"):
        ids = {bounded_int(i, 0, 2**53) for i in req.GET["ids"].split(",")}
    snapshot_id = req.GET.get("result") or req.GET.get("snapshot")
    if snapshot_id:
        state = req.GET.get("state") or "approved"
        check_state(req.user, state)
        frozen = load(req.user, snapshot_id, state)
        rows = filter_events(frozen.rows, req.GET)
        if ids is not None:
            rows = [r for r in rows if r["id"] in ids]
        if req.GET.get("export") not in ("csv", "excel"):
            return Response(page_rows(req, rows, lambda row: row))
    else:
        qs = query_events(req.user, req.GET).prefetch_related(
            "media", "articles", "merged__media", "merged__articles"
        )
        if ids is not None:
            qs = qs.filter(pk__in=ids)
        if req.GET.get("export") not in ("csv", "excel"):
            return Response(page_rows(req, qs, lambda e: event_row(e, req.user)))
        rows = (event_row(e, req.user) for e in qs.iterator(chunk_size=100))
    return export_events(rows, req.GET["export"])


def visible(req, pk):
    qs = Event.objects.exclude(state="archived")
    if not (req.user.has_perm("desk.review") or req.user.has_perm("desk.edit")):
        qs = qs.filter(state="approved")
    return get_object_or_404(qs, pk=pk)


@endpoint(["GET", "POST"], personal="star")
def event_detail(req, pk):
    e = visible(req, pk)
    if req.method == "POST":
        action = req.data.get("action")
        if action == "star":
            if req.data.get("on"):
                e.stars.add(req.user)
            else:
                e.stars.remove(req.user)
        elif action == "feedback":
            from .event_feedback import review_automatic

            e = review_automatic(req.user, pk, req.data)
        elif action in ("merge", "split"):
            merge(
                req.user,
                pk,
                bounded_int(req.data.get("target"), 1, 2**53),
                req.data.get("version"),
                action == "split",
            )
            e.refresh_from_db()
        elif action == "edit":
            require(req.user, "edit")
            with transaction.atomic():
                e = Event.objects.select_for_update().get(pk=pk)
                check_version(e, req.data.get("version"))
                if e.state not in ("pending", "rejected") or e.merged_into_id:
                    raise ValidationError("仅待审或退回动态可修改。")
                title = str(req.data.get("title", e.title)).strip()
                category = req.data.get("category", e.category)
                sub = req.data.get("subtype", e.subtype)
                importance = req.data.get("importance", e.importance)
                if (
                    not title
                    or len(title) > 500
                    or category not in EVENTS
                    or sub
                    and sub not in EVENTS[category]
                    or importance not in IMPORTANCE
                ):
                    raise ValidationError("请检查标题、分类、子项或重要度。")
                e.title, e.category, e.subtype, e.importance = (
                    title,
                    category,
                    sub,
                    importance,
                )
                if "tags" in req.data:
                    tags = req.data["tags"]
                    if (
                        not isinstance(tags, list)
                        or len(tags) > 20
                        or any(
                            not isinstance(tag, str) or len(tag) > 100 for tag in tags
                        )
                    ):
                        raise ValidationError("趋势标签最多20个，每个不超过100字。")
                    e.tags = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
                e.summary = str(req.data.get("summary", e.summary))[:6000]
                if "occurred" in req.data:
                    e.occurred = (
                        parse_day(req.data["occurred"])
                        if req.data["occurred"]
                        else None
                    )
                e.state = "pending"
                e.version += 1
                e.save()
                Change.objects.create(
                    event=e, actor=req.user.username, action="编辑并提交复核"
                )
        elif action == "correct_source":
            from .event_feedback import correct_source

            e = correct_source(req.user, pk, req.data)
        elif action == "verify":
            require(req.user, "review")
            article = get_object_or_404(e.articles, pk=req.data.get("article"))
            try:
                document(article.url)
                article.status = "已核验"
            except ExternalError:
                article.status = "可能失效"
            article.checked = timezone.now()
            article.save()
            Change.objects.create(
                event=e,
                actor=req.user.username,
                action="核验原文",
                detail={"status": article.status, "article": article.id},
            )
        else:
            raise ValidationError("未知操作。")
    row = event_row(e, req.user)
    related = query_events(req.user, {}).exclude(pk=e.pk)
    media_ids = {m["id"] for m in row["media"]}
    candidates = (
        related.filter(
            Q(media__in=media_ids)
            | Q(merged__media__in=media_ids)
            | Q(category=e.category)
        )
        .distinct()
        .order_by("-occurred", "-pk")
    )
    row["related"] = [
        {"id": r.pk, "title": r.title, "category": r.category} for r in candidates[:3]
    ]
    last_review = (
        e.changes.filter(action__in=["approve", "reject", "approved", "人工复核"])
        .order_by("-at", "-pk")
        .first()
    )
    row["can_recheck"] = bool(
        e.auto
        and e.state == "approved"
        and not e.changes.filter(action="approve").exists()
    )
    row["reviewer"] = last_review.actor if last_review else ""
    row["reviewed_at"] = last_review.at if last_review else None
    row["history"] = (
        list(e.changes.order_by("-id").values("action", "actor", "at", "detail"))
        if req.user.has_perm("desk.review")
        else []
    )
    return Response(row)


@endpoint(["POST"])
def bulk(req):
    review(
        req.user,
        req.data.get("items"),
        req.data.get("action"),
        str(req.data.get("reason", "")),
        str(req.data.get("noise_reason", "")),
    )
    return Response({"ok": True})


@endpoint(["POST"])
def semantic(req):
    if req.data.get("search"):
        result = search_service.advance(
            req.user,
            req.data["search"],
            bounded_int(req.data.get("cursor"), 0, 2**53),
            analyze,
        )
    else:
        run = search_service.start(
            req.user, req.data.get("filters", {}), req.data.get("q")
        )
        result = search_service.advance(req.user, run.pk, 0, analyze)
    # Interactive clients request a bounded batch per HTTP request.
    while not req.data.get("incremental") and not result["complete"]:
        result = search_service.advance(
            req.user, result["search"], result["scanned"], analyze
        )
    return Response(result)


@endpoint(["GET", "POST"])
def dashboard(req):
    day = timezone.localdate()
    span = bounded_int(req.GET.get("days", 7), 1, 90)
    period = {
        "clock": "approved",
        "start": str(day - timedelta(days=span - 1)),
        "end": str(day),
    }
    qs = query_events(req.user, period)
    if req.method == "POST":
        require(req.user, "edit")
        rows = list(qs.values("id", "title", "summary")[:80])
        if not rows:
            raise ValidationError("所选区间暂无已批准动态。")
        result = analyze(
            "根据材料给出可供报社阅读的变化研判，以事件编号注明依据。返回 conclusion 字符串。",
            rows,
        )
        if (
            not isinstance(result.get("conclusion"), str)
            or not result["conclusion"].strip()
        ):
            raise ExternalError("模型未返回研判文本。")
        Insight.objects.create(
            content=result["conclusion"][:5000], event_ids=[r["id"] for r in rows]
        )
    prev_end = day - timedelta(days=span)
    can_review = req.user.has_perm("desk.review") or req.user.has_perm("desk.edit")
    with consistent_read():
        approved = project_events(qs, req.user)
        media = project_media(req.user)
        queue = Event.objects.filter(state="pending", merged_into=None)
        pending = project_events(queue, req.user) if can_review else []
        pending_count = len(pending) if can_review else queue.count()
        previous = query_events(
            req.user,
            {
                "clock": "approved",
                "start": str(prev_end - timedelta(days=span - 1)),
                "end": str(prev_end),
            },
        )
        prior_counts = Counter(previous.values_list("category", flat=True))
        yesterday = str(day - timedelta(days=1))
        prior_today = query_events(
            req.user, {"clock": "approved", "start": yesterday, "end": yesterday}
        )
        prior_auto = prior_today.filter(auto=True).count()
        prior_count = prior_today.count()
        today_events = project_events(
            query_events(req.user, {"start": str(day), "end": str(day)}), req.user
        )
    period["snapshot"] = freeze(req.user, "approved", approved).pk
    archive_filters = {"snapshot": freeze(req.user, "media", media).pk}
    today_filter = {**period, "start": str(day)}
    today = [row for row in approved if row["approval_day"] == str(day)]
    auto = sum(row["auto"] for row in today)
    count = len(today)
    audit_card = {
        "name": "待审核条数",
        "value": pending_count,
        "note": "队列内容限运营查看",
    }
    if can_review:
        audit_card.update(
            path="/news",
            filters={
                "state": "pending",
                "snapshot": freeze(req.user, "pending", pending).pk,
            },
            note="待复核事件",
        )
    stocks = stock_comparisons(
        req.user, day, {"queue": pending_count, "media": len(media)}
    )
    audit_card.update(stocks["queue"])
    cards = [
        {
            "name": "今日入库动态",
            "value": count,
            "change": count - prior_count,
            "comparison": "昨日",
            "path": "/news",
            "filters": today_filter,
        },
        audit_card,
        {
            "name": "媒体档案总数",
            "value": len(media),
            **stocks["media"],
            "path": "/archives",
            "filters": archive_filters,
        },
        {
            "name": "今日自动入库数",
            "value": auto,
            "change": auto - prior_auto,
            "comparison": "昨日",
            "path": "/news",
            "filters": {**today_filter, "auto": "1"},
        },
    ]
    totals = Counter(row["category"] for row in approved)
    categories = {k: totals[k] for k in EVENTS}
    changes = {k: categories[k] - prior_counts[k] for k in EVENTS}
    regions = Counter()
    types = Counter()
    daily = Counter()
    for row in approved:
        regions.update({m["region"] or "未标注" for m in row["media"]} or {"未关联"})
        types.update({m["category"] or "未分类" for m in row["media"]} or {"未关联"})
        daily[row["approval_day"]] += 1
    trend = {
        str(day - timedelta(days=i)): daily[str(day - timedelta(days=i))]
        for i in reversed(range(span))
    }
    focused = list(
        qs.filter(Q(stars=req.user) | Q(importance__in=["重点", "核心"]))
        .distinct()
        .order_by("-approved", "-id")[:3]
    )
    reports = Report.objects.filter(kind="daily", day=day)
    if not req.user.has_perm("desk.edit"):
        reports = reports.filter(editions__isnull=False)
    todays_report = reports.values_list("pk", flat=True).first()
    alerts = [
        {
            "category": name,
            "count": len(items),
            "filters": {"snapshot": freeze(req.user, "approved", items).pk},
        }
        for name, items in business_alerts(today_events)
    ]
    insight = Insight.objects.order_by("-id").first()
    return Response(
        {
            "cards": cards,
            "categories": categories,
            "changes": changes,
            "alerts": alerts,
            "auto_share": {
                "approved": count,
                "auto": auto,
                "filters": {**today_filter, "auto": "1"},
            },
            "today_report": f"/summary/{todays_report}"
            if todays_report
            else f"/summary?day={day}",
            "period": period,
            "types": types,
            "regions": regions,
            "trend": trend,
            "focused": [event_row(e, req.user) for e in focused],
            "insight": insight.content
            if insight and set(insight.event_ids) <= {row["id"] for row in approved}
            else "",
        }
    )
