from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .domain import require
from .external import normalize_url
from .models import Change, Media, MediaMetric


class MetricEntry(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("发行量", "营收", "用户数"))
    day = serializers.DateField()
    value = serializers.DecimalField(max_digits=20, decimal_places=4, min_value=0)
    unit = serializers.CharField(max_length=16)
    source_url = serializers.URLField(max_length=1500)

    def validate_day(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("请选择已披露指标所属的日期。")
        return value


@transaction.atomic
def save_metric(user, pk, body):
    require(user, "edit")
    m = Media.objects.select_for_update().get(pk=pk)
    form = MetricEntry(data=body)
    form.is_valid(raise_exception=True)
    data = dict(form.validated_data)
    data["source_url"] = normalize_url(data["source_url"])
    metric, created = MediaMetric.objects.get_or_create(
        media=m, kind=data["kind"], day=data["day"], defaults=data
    )
    if not created:
        before = {
            "value": str(metric.value),
            "unit": metric.unit,
            "source_url": metric.source_url,
        }
        for field in before:
            setattr(metric, field, data[field])
        metric.save(update_fields=list(before))
    else:
        before = {}
    after = {
        "value": str(metric.value),
        "unit": metric.unit,
        "source_url": metric.source_url,
    }
    if before != after:
        Change.objects.create(
            actor=user.username,
            action="更新经营指标",
            detail={
                "media": m.pk,
                "kind": metric.kind,
                "day": str(metric.day),
                "before": before,
                "after": after,
            },
        )
    return {"ok": True}
