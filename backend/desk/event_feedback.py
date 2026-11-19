from urllib.parse import urlsplit

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .domain import bounded_int, check_version, require
from .editorial import NOISE_TERMS
from .external import normalize_url
from .models import Article, Change, Event, IntakeDecision


@transaction.atomic
def review_automatic(user, pk, data):
    require(user, "review")
    e = Event.objects.select_for_update().get(pk=pk)
    check_version(e, data.get("version"))
    action = data.get("decision")
    reason = str(data.get("reason", "")).strip()
    if not reason or len(reason) > 1000:
        raise ValidationError("请填写不超过1000字的复核理由。")
    if e.merged_into_id:
        raise ValidationError("请先核对该事件的归并状态。")
    if action == "restore" and e.state in ("filtered", "rejected") and e.is_noise:
        e.state, e.is_noise, e.noise_reason = "pending", False, ""
    elif (
        e.state == "approved"
        and e.auto
        and not e.changes.filter(action="approve").exists()
        and action in ("confirm", "pending", "noise")
    ):
        if action == "pending":
            e.state = "pending"
        if action == "noise":
            classification = data.get("noise_reason")
            if classification not in (*NOISE_TERMS, "其他"):
                raise ValidationError("请选择噪音原因。")
            e.state, e.is_noise, e.noise_reason = "filtered", True, classification
    else:
        raise ValidationError("仅可复核已自动批准的事件或恢复被标噪音的候选。")
    if action != "confirm":
        e.filtered_by = "人工复核"
    e.version += 1
    e.save()
    article = e.articles.order_by("pk").first()
    IntakeDecision.objects.create(
        event=e,
        source=article.source if article else None,
        title=e.title,
        url=article.url if article else "",
        source_zone=e.source_zone,
        outcome={
            "restore": "pending",
            "pending": "pending",
            "noise": "rejected",
            "confirm": "confirmed",
        }[action],
        filtered_by="人工复核",
        noise_reason=e.noise_reason,
        actor=user.username,
        analysis={"reason": reason, "decision": action},
    )
    Change.objects.create(
        event=e,
        actor=user.username,
        action="人工复核",
        detail={"reason": reason, "decision": action, "noise_reason": e.noise_reason},
    )
    return e


@transaction.atomic
def correct_source(user, pk, data):
    require(user, "edit")
    event = Event.objects.select_for_update().get(pk=pk)
    check_version(event, data.get("version"))
    if event.state not in ("pending", "rejected") or event.merged_into_id:
        raise ValidationError("仅待审核或已退回事件可以更正原文。")
    old = event.articles.filter(pk=bounded_int(data.get("article"), 1, 2**53)).first()
    if not old:
        raise ValidationError("该链接未关联本事件。")
    url = normalize_url(data.get("url", ""))
    if len(url) > Article._meta.get_field("url").max_length:
        raise ValidationError("原文地址过长。")
    if url == old.url:
        return event
    known_source = (
        old.source if urlsplit(old.url).netloc == urlsplit(url).netloc else None
    )
    replacement, _ = Article.objects.get_or_create(
        url=url, defaults={"title": event.title, "source": known_source}
    )
    replacement = Article.objects.select_for_update().get(pk=replacement.pk)
    if replacement.events.exclude(pk=pk).exists():
        raise ValidationError("该原文已关联另一事件，请核对归并关系。")
    event.articles.add(replacement)
    event.articles.remove(old)
    event.state = "pending"
    event.version += 1
    event.save(update_fields=["state", "version"])
    Change.objects.create(
        event=event,
        actor=user.username,
        action="更正原文链接",
        detail={"old": old.url, "new": url},
    )
    return event
