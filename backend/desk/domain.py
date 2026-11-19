from datetime import date
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied, APIException
from .labels import text_labels
from .models import Event, Article, Change, Media, IntakeDecision
from . import editorial
from .catalog import EVENTS, IMPORTANCE
from .external import normalize_url


class Conflict(APIException):
    status_code = 409
    default_detail = "记录已变化，请刷新后重试。"


def require(user, right):
    if not user.has_perm("desk." + right):
        raise PermissionDenied("当前账号没有此操作权限。")


def parse_day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError("日期格式应为 YYYY-MM-DD。")


def bounded_int(value, low, high):
    try:
        n = int(value)
    except (ValueError, TypeError):
        raise ValidationError("请输入整数。")
    if not low <= n <= high:
        raise ValidationError(f"数值须在 {low} 至 {high} 之间。")
    return n


def check_version(obj, value):
    if str(obj.version) != str(value):
        raise Conflict()


def check_state(user, state):
    if state not in ("approved", "pending", "rejected", "filtered"):
        raise ValidationError("无效的审核状态。")
    if state != "approved" and not (
        user.has_perm("desk.edit") or user.has_perm("desk.review")
    ):
        raise PermissionDenied()


def query_events(user, filters):
    state = filters.get("state") or "approved"
    check_state(user, state)
    if filters.get("as_of") or filters.get("snapshot"):
        raise ValidationError(
            "历史查询须使用已保存的快照。旧版时间链接请从看板重新打开。"
        )
    qs = Event.objects.filter(merged_into=None, state=state)
    for key, field in {
        "category": "category",
        "subtype": "subtype",
        "importance": "importance",
        "region": "media__region",
        "primary": "media__category",
        "secondary": "media__subcategory",
        "focus": "media__focus",
    }.items():
        if filters.get(key):
            if key in ("region", "primary") and filters[key] == "未关联":
                qs = qs.exclude(Q(media__isnull=False) | Q(merged__media__isnull=False))
                continue
            if (key, filters[key]) in (("region", "未标注"), ("primary", "未分类")):
                qs = qs.filter(Q(**{field: ""}) | Q(**{"merged__" + field: ""}))
                continue
            qs = (
                qs.filter(
                    Q(**{field: filters[key]}) | Q(**{"merged__" + field: filters[key]})
                )
                if field.startswith("media__")
                else qs.filter(**{field: filters[key]})
            )
    if filters.get("media"):
        mid = bounded_int(filters["media"], 1, 2**53)
        qs = qs.filter(Q(media=mid) | Q(merged__media=mid))
    if filters.get("tag"):
        qs = qs.filter(tags__contains=[str(filters["tag"])[:100]])
    if filters.get("auto") == "1":
        qs = qs.filter(auto=True)
    if filters.get("star") == "1":
        qs = qs.filter(stars=user)
    field = "approved__date" if filters.get("clock") == "approved" else "occurred"
    for key, suffix in (("start", "gte"), ("end", "lte")):
        if filters.get(key):
            qs = qs.filter(**{field + "__" + suffix: parse_day(filters[key])})
    if filters.get("q"):
        q = filters["q"][:250]
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(summary__icontains=q)
            | Q(media__name__icontains=q)
            | Q(merged__media__name__icontains=q)
        )
    return qs.distinct()


def media_row(m):
    return {
        k: getattr(m, k)
        for k in (
            "id",
            "name",
            "owner",
            "level",
            "category",
            "subcategory",
            "region",
            "kind",
            "focus",
            "tags",
            "channels",
            "reference",
            "digest_section",
            "description",
            "archive_status",
            "high_priority",
            "origin_id",
        )
    }


def event_row(e, user=None):
    articles = {a.id: a for a in e.articles.all()}
    media = {m.id: m for m in e.media.all()}
    for child in e.merged.all():
        articles.update({a.id: a for a in child.articles.all()})
        media.update({m.id: m for m in child.media.all()})
    return {
        "id": e.pk,
        "title": e.title,
        "summary": e.summary,
        "category": e.category,
        "subtype": e.subtype,
        "importance": e.importance,
        "occurred": e.occurred,
        "approved": e.approved,
        "approval_day": str(timezone.localdate(e.approved)) if e.approved else None,
        "state": e.state,
        "auto": e.auto,
        "tags": text_labels(e.tags),
        "subject_type": e.subject_type,
        "event_nature": e.event_nature,
        "is_noise": e.is_noise,
        "noise_reason": e.noise_reason,
        "filtered_by": e.filtered_by,
        "relevance_score": e.score,
        "ai_reasoning": e.reasoning,
        "source_zone": e.source_zone,
        "capture_method": e.capture_method,
        "reasoning": e.reasoning,
        "score": e.score,
        "version": e.version,
        "creator": e.creator,
        "created": e.created,
        "starred": e.viewer_star
        if hasattr(e, "viewer_star")
        else bool(user and e.stars.filter(id=user.pk).exists()),
        "media": [
            {
                "id": m.id,
                "name": m.name,
                "region": m.region,
                "category": m.category,
                "subcategory": m.subcategory,
                "focus": m.focus,
                "high_priority": m.high_priority,
                "digest_section": m.digest_section,
                "reference": m.reference,
            }
            for m in media.values()
        ],
        "articles": [
            {
                "id": a.id,
                "url": a.url,
                "title": a.title,
                "status": a.status,
                "published": a.published,
                "checked": a.checked,
            }
            for a in articles.values()
        ],
        "merged": [
            {"id": c.id, "title": c.title, "version": c.version} for c in e.merged.all()
        ],
    }


@transaction.atomic
def record(data, actor, source=None, verified=False):
    title = str(data.get("title", "")).strip()
    cat = data.get("category", "其他类")
    importance = data.get("importance", "常规")
    sub = data.get("subtype", "")
    if (
        not title
        or len(title) > 500
        or cat not in EVENTS
        or importance not in IMPORTANCE
        or (sub and sub not in EVENTS[cat])
    ):
        raise ValidationError("请检查标题、分类、子项及重要度。")
    url = normalize_url(data.get("url", ""))
    article, _ = Article.objects.get_or_create(
        url=url, defaults={"title": title, "source": source}
    )
    article = Article.objects.select_for_update().get(pk=article.pk)
    if verified:
        article.status = "已核验"
        article.checked = timezone.now()
        article.save(update_fields=["status", "checked"])
    existing = article.events.first()
    if existing:
        return existing, False
    screening = editorial.assess(title, str(data.get("summary", "")), url)
    if screening["gate"] == "参评非获奖结果":
        screening["reason"] = "其他"
    method = (
        "自动采集"
        if source
        else (
            "AI 辅助录入" if data.get("capture_method") == "AI 辅助录入" else "人工录入"
        )
    )
    event = Event.objects.create(
        state="filtered" if screening["reason"] else "pending",
        is_noise=bool(screening["reason"]),
        noise_reason=screening["reason"],
        filtered_by=screening["gate"] if screening["reason"] else "",
        capture_method=method,
        source_zone=source.zone if source else "",
        title=title,
        category=cat,
        subtype=sub,
        importance=importance,
        summary=str(data.get("summary", ""))[:6000],
        occurred=parse_day(data["occurred"]) if data.get("occurred") else None,
        creator=actor,
    )
    if data.get("published"):
        article.published = parse_day(data["published"])
        article.save(update_fields=["published"])
    event.articles.add(article)
    media = data.get("media") or (source.media_id if source else None)
    if media:
        media_obj = Media.objects.filter(pk=bounded_int(media, 1, 2**53)).first()
        if not media_obj:
            raise ValidationError("机构不存在。")
        event.media.add(media_obj)
    Change.objects.create(event=event, actor=actor, action="录入")
    if source is None:
        IntakeDecision.objects.create(
            event=event,
            title=title,
            url=url,
            outcome=event.state,
            filtered_by=event.filtered_by,
            noise_reason=event.noise_reason,
            terms=screening["terms"],
            actor=actor,
        )
    return event, True


@transaction.atomic
def review(user, items, action, reason="", noise_reason=""):
    require(user, "review")
    if action not in ("approve", "reject") or (
        action == "reject" and not reason.strip()
    ):
        raise ValidationError("退回须填写原因，请检查操作。")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValidationError("每次选择 1 至 100 件。")
    if any(
        not isinstance(i, dict) or type(i.get("id")) is not int or "version" not in i
        for i in items
    ):
        raise ValidationError("审核请求缺少有效编号或版本。")
    for item in sorted(items, key=lambda i: i["id"]):
        e = Event.objects.select_for_update().get(pk=item["id"])
        check_version(e, item.get("version"))
        can_recall = (
            action == "reject"
            and e.state == "approved"
            and e.auto
            and not e.changes.filter(action="approve").exists()
        )
        if e.merged_into_id or (
            e.state not in ("pending", "rejected") and not can_recall
        ):
            raise Conflict()
        if action == "approve" and not e.articles.filter(status="已核验").exists():
            raise ValidationError("请先核验至少一个可访问的真实原文。")
        if action == "approve" and any(
            editorial.assess(a.title, a.excerpt, a.url)["gate"] == "参评非获奖结果"
            for a in e.articles.all()
        ):
            raise ValidationError("推荐参评材料不能作为获奖结果入库。")
        if action == "approve" and (
            e.is_noise or editorial.historical_assessment(e)["reason"]
        ):
            raise ValidationError("该事件命中噪音规则，请核对原始证据。")
        if noise_reason:
            if action != "reject" or noise_reason not in (
                *editorial.NOISE_TERMS,
                "其他",
            ):
                raise ValidationError("噪音原因无效。")
            e.noise_reason, e.is_noise, e.filtered_by = noise_reason, True, "人工退回"
            article = e.articles.order_by("pk").first()
            IntakeDecision.objects.create(
                event=e,
                source=article.source if article else None,
                url=article.url if article else "",
                title=e.title,
                source_zone=e.source_zone,
                outcome="rejected",
                filtered_by=e.filtered_by,
                noise_reason=noise_reason,
                actor=user.get_username(),
                analysis={"reason": reason},
            )
        e.state = "approved" if action == "approve" else "rejected"
        if action == "approve":
            e.approved = e.approved or timezone.now()
        e.version += 1
        e.save()
        Change.objects.create(
            event=e, actor=user.get_username(), action=action, detail={"reason": reason}
        )


@transaction.atomic
def merge(user, child_id, target_id, version, undo=False):
    require(user, "review")
    if child_id == target_id:
        raise ValidationError("请选择不同事件。")
    rows = {
        e.pk: e
        for e in Event.objects.select_for_update()
        .filter(pk__in=[child_id, target_id])
        .order_by("pk")
    }
    if len(rows) != 2:
        raise ValidationError("事件不存在。")
    child, target = rows[child_id], rows[target_id]
    check_version(child, version)
    if undo:
        if child.merged_into_id != target.pk:
            raise Conflict()
        child.merged_into = None
    else:
        if (
            child.merged_into_id
            or child.merged.exists()
            or target.merged_into_id
            or target.state != "approved"
            or child.state != "approved"
        ):
            raise ValidationError("请选择两件独立的已批准事件。")
        child.merged_into = target
    child.version += 1
    child.save()
    target.version += 1
    target.save()
    Change.objects.create(
        event=target,
        actor=user.username,
        action="拆回" if undo else "归并",
        detail={"child": child.pk},
    )
