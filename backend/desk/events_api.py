import csv
from collections import Counter
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .api import endpoint
from .domain import (
    require,
    query_events,
    event_row,
    bounded_int,
    record,
    review,
    merge,
    parse_day,
    check_version,
)
from .models import Event, Change, Media, Insight
from .catalog import EVENTS, IMPORTANCE
from .external import analyze, document, ExternalError


def page_rows(req, qs, transform):
    page = bounded_int(req.GET.get("page", 1), 1, 1000000)
    size = bounded_int(req.GET.get("size", 20), 1, 100)
    return {
        "count": qs.count(),
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
    qs = query_events(req.user, req.GET)
    if req.GET.get("ids"):
        ids = [bounded_int(i, 0, 2**53) for i in req.GET["ids"].split(",")[:500]]
        qs = qs.filter(pk__in=ids)
    qs = qs.prefetch_related("media", "articles", "merged__media", "merged__articles")
    if req.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = 'attachment; filename="events.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["事件编号", "标题", "分类", "发生日", "机构", "摘要", "原文"])
        for e in qs.iterator(chunk_size=100):
            row = event_row(e)
            vals = [
                e.id,
                e.title,
                e.category,
                e.occurred,
                "、".join(m["name"] for m in row["media"]),
                e.summary,
                " ".join(a["url"] for a in row["articles"]),
            ]
            writer.writerow(
                [
                    ("'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v)
                    if isinstance(v, str)
                    else v
                    for v in vals
                ]
            )
        return response
    return Response(page_rows(req, qs, lambda e: event_row(e, req.user)))


def visible(req, pk):
    qs = Event.objects.all()
    if not (req.user.has_perm("desk.review") or req.user.has_perm("desk.edit")):
        qs = qs.filter(state="approved")
    return get_object_or_404(qs, pk=pk)


@endpoint(["GET", "POST"])
def event_detail(req, pk):
    e = visible(req, pk)
    if req.method == "POST":
        action = req.data.get("action")
        if action == "star":
            if req.data.get("on"):
                e.stars.add(req.user)
            else:
                e.stars.remove(req.user)
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
    )
    return Response({"ok": True})


@endpoint(["POST"])
def semantic(req):
    question = str(req.data.get("q", "")).strip()
    if not question:
        raise ValidationError("请填写检索问题。")
    qs = query_events(req.user, req.data.get("filters", {}))
    rows = list(qs.order_by("-approved").values("id", "title", "summary")[:200])
    if not rows:
        return Response({"ids": []})
    result = analyze(
        "按语义选择与问题相关的事件。返回 ids 整数数组，只能选择材料中提供的编号。",
        {"question": question, "events": rows},
    )
    ids = result.get("ids")
    if not isinstance(ids, list):
        raise ExternalError("模型检索结果格式无效。")
    valid = {r["id"] for r in rows}
    return Response({"ids": [i for i in ids if type(i) is int and i in valid]})


@endpoint(["GET", "POST"])
def dashboard(req):
    day = timezone.localdate()
    span = bounded_int(req.GET.get("days", 7), 1, 90)
    cutoff = timezone.now().isoformat()
    period = {
        "as_of": cutoff,
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
    today_filter = {
        "as_of": cutoff,
        "start": str(day),
        "end": str(day),
        "clock": "approved",
    }
    today = query_events(req.user, today_filter)
    yesterday = day - timedelta(days=1)
    prior = query_events(
        req.user, {"start": str(yesterday), "end": str(yesterday), "clock": "approved"}
    )
    auto = today.filter(auto=True).count()
    count = today.count()
    cards = [
        {
            "name": "今日入库动态",
            "value": count,
            "change": count - prior.count(),
            "path": "/news",
            "filters": today_filter,
        },
        {
            "name": "媒体档案总数",
            "value": Media.objects.count(),
            "path": "/archives",
            "filters": {},
        },
        {
            "name": "重点追踪数",
            "value": Media.objects.filter(focus="重点关注").count(),
            "path": "/archives",
            "filters": {"focus": "重点关注"},
        },
        {
            "name": "AI 自动入库占比",
            "value": f"{auto / count:.0%}" if count else "—",
            "note": f"{auto} / {count} 件",
            "path": "/news",
            "filters": {**today_filter, "auto": "1"},
        },
    ]
    if req.user.has_perm("desk.review") or req.user.has_perm("desk.edit"):
        cards.insert(
            1,
            {
                "name": "待审核条数",
                "value": query_events(req.user, {"state": "pending"}).count(),
                "path": "/news",
                "filters": {"state": "pending"},
            },
        )
    else:
        cards.insert(
            1,
            {
                "name": "待审核条数",
                "value": Event.objects.filter(
                    state="pending", merged_into=None
                ).count(),
                "note": "队列内容限运营查看",
            },
        )
    categories = {
        k: query_events(req.user, {**period, "category": k}).count() for k in EVENTS
    }
    prev_end = day - timedelta(days=span)
    prev = query_events(
        req.user,
        {
            "clock": "approved",
            "start": str(prev_end - timedelta(days=span - 1)),
            "end": str(prev_end),
        },
    )
    changes = {k: categories[k] - prev.filter(category=k).count() for k in EVENTS}
    regions = Counter()
    types = Counter()
    daily = Counter()
    for e in qs.prefetch_related("media", "merged__media"):
        institutions = list(e.media.all()) + [
            m for child in e.merged.all() for m in child.media.all()
        ]
        regions.update({m.region or "未标注" for m in institutions} or {"未关联"})
        types.update({m.category or "未分类" for m in institutions} or {"未关联"})
        daily[str(timezone.localtime(e.approved).date())] += 1
    trend = {
        str(day - timedelta(days=i)): daily[str(day - timedelta(days=i))]
        for i in reversed(range(span))
    }
    focused = (
        Event.objects.filter(state="approved", merged_into=None)
        .filter(Q(stars=req.user) | Q(importance__in=["重点", "核心"]))
        .distinct()[:6]
    )
    insight = Insight.objects.order_by("-id").first()
    return Response(
        {
            "cards": cards,
            "categories": categories,
            "changes": changes,
            "alerts": [
                {"category": k, "change": delta}
                for k, delta in changes.items()
                if abs(delta) >= 3 and abs(delta) / max(1, categories[k] - delta) >= 0.5
            ],
            "period": period,
            "types": types,
            "regions": regions,
            "trend": trend,
            "focused": [event_row(e, req.user) for e in focused],
            "insight": insight.content
            if insight
            and set(insight.event_ids) <= set(qs.values_list("id", flat=True))
            else "",
        }
    )
