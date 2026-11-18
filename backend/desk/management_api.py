import re
import uuid
from collections import Counter
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from .api import endpoint
from .domain import require, media_row, event_row, query_events, bounded_int
from .events_api import page_rows
from .models import Media, Source, Rule, Policy, Change, Run
from .catalog import MARKET_SECTIONS, CHANNELS, CATEGORIES, FOCUS, LEVELS
from .collection import ADAPTERS, COUNTERS, enqueue, inference
from .snapshots import consistent_read, project_events, freeze, load, filter_media
from .external import normalize_url, document


class MediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Media
        exclude = ["updated", "followers"]

    def validate(self, data):
        category = data.get("category", getattr(self.instance, "category", ""))
        sub = data.get("subcategory", getattr(self.instance, "subcategory", ""))
        if (
            category
            and category not in CATEGORIES
            or sub
            and sub not in CATEGORIES.get(category, [])
        ):
            raise serializers.ValidationError("媒体一级、二级分类不匹配。")
        if (
            data.get("focus", FOCUS[-1]) not in FOCUS
            or data.get("level")
            and data["level"] not in LEVELS
        ):
            raise serializers.ValidationError("无效的层级或关注等级。")
        section = data.get(
            "digest_section", getattr(self.instance, "digest_section", "")
        )
        reference = data.get("reference", getattr(self.instance, "reference", ""))
        if section and (
            section not in MARKET_SECTIONS
            or category != "头部市场化纸媒"
            or not reference
        ):
            raise serializers.ValidationError(
                "市场化纸媒的日报归栏须选择有效栏目，并填写可核验的分类资料出处。"
            )
        channels = data.get("channels", {})
        if not isinstance(channels, dict) or any(
            k not in CHANNELS or not isinstance(v, str) for k, v in channels.items()
        ):
            raise serializers.ValidationError("渠道名称或字段无效。")
        if not isinstance(data.get("tags", []), list):
            raise serializers.ValidationError("垂直领域应为标签列表。")
        return data


class SourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Source
        fields = "__all__"

    def validate_url(self, value):
        return normalize_url(value)

    def validate_adapter(self, value):
        if value not in ADAPTERS:
            raise serializers.ValidationError("未知适配器。")
        return value


class RuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rule
        fields = "__all__"
        read_only_fields = ["hits"]

    def validate_kind(self, value):
        if value not in ("主体词", "动作词", "排除词"):
            raise serializers.ValidationError("规则类型无效。")
        return value


def save_form(cls, payload, instance=None):
    form = cls(instance, data=payload, partial=instance is not None)
    form.is_valid(raise_exception=True)
    return form.save()


@endpoint(["GET", "POST", "PATCH"])
def archives(req, pk=None):
    if req.method == "POST" and pk and req.data.get("action") == "follow":
        m = Media.objects.get(pk=pk)
        if req.data.get("on") is True:
            m.followers.add(req.user)
        else:
            m.followers.remove(req.user)
        return Response({"following": m.followers.filter(pk=req.user.id).exists()})
    if req.method != "GET":
        require(req.user, "edit")
        with transaction.atomic():
            m = Media.objects.select_for_update().get(pk=pk) if pk else None
            m = save_form(MediaSerializer, req.data, m)
            Change.objects.create(
                actor=req.user.username, action="更新档案", detail={"media": m.pk}
            )
        return Response(media_row(m))
    if pk:
        m = Media.objects.get(pk=pk)
        qs = query_events(req.user, {"media": m.pk})
        return Response(
            {
                **media_row(m),
                "following": m.followers.filter(pk=req.user.pk).exists(),
                "count": qs.count(),
                "timeline": [event_row(e) for e in qs[:30]],
            }
        )
    if req.GET.get("snapshot"):
        rows = filter_media(load(req.user, req.GET["snapshot"], "media").rows, req.GET)
        return Response(page_rows(req, rows, lambda row: row))
    qs = Media.objects.order_by("name")
    for k in ("focus", "level", "category", "subcategory", "region", "kind"):
        if req.GET.get(k):
            qs = qs.filter(**{k: req.GET[k]})
    if req.GET.get("following") == "1":
        qs = qs.filter(followers=req.user)
    if req.GET.get("q"):
        qs = qs.filter(
            Q(name__icontains=req.GET["q"]) | Q(owner__icontains=req.GET["q"])
        )
    return Response(page_rows(req, qs, media_row))


def ops(user):
    if not any(user.has_perm("desk." + p) for p in ["edit", "review", "configure"]):
        raise PermissionDenied()


@endpoint(["GET"])
def monitoring(req):
    ops(req.user)
    sources = list(Source.objects.order_by("name").values())
    rules = list(Rule.objects.order_by("kind", "word").values())
    runs = list(
        Run.objects.select_related("source")
        .order_by("-id")
        .values(
            "id",
            "source__name",
            "source_id",
            "trigger",
            "state",
            "created",
            "started",
            "finished",
            "progress",
            "counts",
            "message",
            "attempts",
        )[:100]
    )
    policy, _ = Policy.objects.get_or_create(pk=1)
    media = list(Media.objects.order_by("name"))
    missing = [
        {"media": m.id, "name": m.name, "channel": c}
        for m in media
        for c in CHANNELS
        if not m.channels.get(c)
    ]
    return Response(
        {
            "sources": sources,
            "rules": rules,
            "runs": runs,
            "adapters": ADAPTERS,
            "policy": {
                "enabled": policy.enabled,
                "threshold": policy.threshold,
                "low": policy.low,
            },
            "media": [{"id": m.id, "name": m.name} for m in media],
            "missing": missing,
            "coverage": {
                "关联信源机构": len(
                    {row["media_id"] for row in sources if row["media_id"]}
                ),
                "在库机构": len(media),
                "启用信源": sum(row["enabled"] for row in sources),
                "信源总数": len(sources),
            },
            "slots": len(media) * len(CHANNELS),
            "pending": query_events(req.user, {"state": "pending"}).count()
            if req.user.has_perm("desk.edit") or req.user.has_perm("desk.review")
            else None,
        }
    )


@endpoint(["GET"])
def collection_batch(req):
    ops(req.user)
    key = req.GET.get("key", "")
    if not key or len(key) > 80:
        raise ValidationError("请提供有效的采集批次。")
    with consistent_read():
        jobs = list(
            Run.objects.filter(
                trigger="手动采集",
                key__startswith=f"manual:{key}:",
                key__regex=rf"^manual:{re.escape(key)}:[0-9]+$",
            )
            .order_by("id")
            .values("id", "source__name", "state", "counts", "progress", "message")
        )
        if not jobs:
            raise NotFound("未找到本次采集。")
        finished = sum(job["state"] not in ("queued", "running") for job in jobs)
        counts = Counter({k: 0 for k in COUNTERS})
        for job in jobs:
            counts.update(job.pop("counts"))
        complete = finished == len(jobs)
        rows = []
        if complete:
            # Use the durable creation audit even when a worker reclaims a lease.
            new_ids = Change.objects.filter(
                actor="采集工作进程",
                action="approved",
                detail__run__in=[j["id"] for j in jobs],
            ).values_list("event_id", flat=True)
            qs = query_events(req.user, {}).filter(
                Q(pk__in=new_ids) | Q(merged__pk__in=new_ids)
            )
            rows = project_events(qs, req.user)
            counts["自动入库"] = new_ids.distinct().count()
    return Response(
        {
            "jobs": jobs,
            "counts": counts,
            "finished": finished,
            "complete": complete,
            "event_count": len(rows),
            "snapshot": freeze(req.user, "approved", rows).pk if rows else None,
        }
    )


@endpoint(["POST"])
def operations(req, action):
    data = req.data
    if action in ("source", "rule", "policy"):
        require(req.user, "configure")
        with transaction.atomic():
            if action == "policy":
                policy, _ = Policy.objects.select_for_update().get_or_create(pk=1)
                policy.threshold = bounded_int(data.get("threshold"), 1, 100)
                policy.low = bounded_int(data.get("low", 40), 0, policy.threshold - 1)
                if type(data.get("enabled")) is not bool:
                    raise ValidationError("请指定策略开关。")
                policy.enabled = data["enabled"]
                policy.save()
            else:
                model, serializer = (
                    (Source, SourceSerializer)
                    if action == "source"
                    else (Rule, RuleSerializer)
                )
                instance = (
                    model.objects.select_for_update().get(pk=data["id"])
                    if data.get("id")
                    else None
                )
                save_form(serializer, data, instance)
            Change.objects.create(
                actor=req.user.username, action=action, detail={"id": data.get("id")}
            )
        return Response({"ok": True})
    require(req.user, "edit")
    if action == "collect":
        token = str(data.get("key") or uuid.uuid4().hex)[:80]
        return Response(
            {
                "key": token,
                "jobs": enqueue(
                    "手动采集",
                    "manual:" + token,
                    [data["source"]] if data.get("source") else None,
                ),
            },
            status=202,
        )
    if action == "retry":
        job = Run.objects.get(pk=data.get("id"))
        if job.state not in ("failed", "manual"):
            raise ValidationError("仅失败或人工巡检任务可重试。")
        return Response(
            {"jobs": enqueue("人工重试", f"retry:{job.id}", [job.source_id])},
            status=202,
        )
    if action == "inspect":
        with transaction.atomic():
            job = Run.objects.select_for_update().get(pk=data.get("id"))
            note = str(data.get("note", "")).strip()
            if job.state not in ("manual", "failed") or not note:
                raise ValidationError("请选择待巡检任务并填写结论。")
            job.state = "inspected"
            job.message = note[:1000]
            job.finished = timezone.now()
            job.save()
            Change.objects.create(
                actor=req.user.username,
                action="人工巡检",
                detail={"job": job.pk, "note": job.message},
            )
        return Response({"ok": True})
    if action == "extract":
        text = str(data.get("text", ""))
        title = ""
        if not text and data.get("url"):
            _, title, text = document(data["url"])
        if not text:
            raise ValidationError("请输入正文或有效链接。")
        return Response(inference(title, text))
    raise ValidationError("未知操作。")
