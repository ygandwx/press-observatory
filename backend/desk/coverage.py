"""Calendar-day monitoring in the site's configured local time."""

from collections import Counter
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .catalog import CATEGORIES
from .models import Change, Event, IntakeDecision, Run, Source
from .editorial import source_block

MINIMUM_OBSERVATIONS = 10
HIGH_NOISE_RATE = 75


def registered_sections():
    return {
        (row.get("source"), row.get("url"))
        for row in Change.objects.filter(action="登记核验栏目").values_list(
            "detail", flat=True
        )
        if isinstance(row, dict)
    }


def registered_source(source):
    return bool(
        source.pk
        and Change.objects.filter(
            action="登记核验栏目", detail__source=source.pk, detail__url=source.url
        ).exists()
    )


def noise_report(days, today=None):
    today = today or timezone.localdate()
    since = today - timedelta(days=days - 1)
    rows = IntakeDecision.objects.filter(at__date__range=(since, today))
    feedback_rows = IntakeDecision.objects.filter(run__isnull=True).filter(
        Q(outcome="rejected")
        | Q(
            filtered_by="人工复核",
            outcome__in=("filtered", "confirmed", "pending", "rejected"),
        )
    )
    feedback = feedback_rows.filter(at__date__range=(since, today))
    # One URL contributes at most once per source/window. Failed reads, blocked
    # sources and duplicate rediscovery are logged separately from dispositions.
    decisions = rows.filter(
        run__isnull=False, outcome__in=("approved", "pending", "filtered")
    )
    seen = {}
    for source_id, name, url, reason, event_id in decisions.order_by(
        "at", "id"
    ).values_list("source_id", "source__name", "url", "noise_reason", "event_id"):
        seen[(source_id, url)] = (name, reason, event_id)
    corrections = dict(
        feedback_rows.filter(
            event_id__in={v[2] for v in seen.values() if v[2]},
            at__date__lte=today,
        )
        .order_by("at", "id")
        .values_list("event_id", "noise_reason")
    )
    sources, reasons = {}, Counter()
    for (source_id, _), (name, original, event_id) in seen.items():
        row = sources.setdefault(
            source_id,
            {"source_id": source_id, "source__name": name, "total": 0, "noise": 0},
        )
        row["total"] += 1
        reason = corrections.get(event_id, original)
        if reason:
            row["noise"] += 1
            reasons[reason] += 1
    for row in sources.values():
        row["rate"] = round(100 * row["noise"] / row["total"], 1)
        row["high_noise"] = (
            row["total"] >= MINIMUM_OBSERVATIONS
            and 100 * row["noise"] >= HIGH_NOISE_RATE * row["total"]
        )
    return {
        "days": days,
        "since": since,
        "through": today,
        "sources": sorted(
            sources.values(), key=lambda r: (-r["rate"], -r["total"], r["source_id"])
        ),
        "reasons": [{"noise_reason": k, "count": v} for k, v in reasons.most_common()],
        "total": len(seen),
        "noise": sum(reasons.values()),
        "feedback": feedback.count(),
    }


def coverage_detail(sources, policy):
    latest = {}
    for row in Run.objects.order_by("source_id", "-created", "-id").values(
        "source_id", "created", "state", "counts", "message"
    ):
        latest.setdefault(row["source_id"], row)
    today = timezone.localdate()
    week = noise_report(7, today)
    rates = {r["source_id"]: r for r in week["sources"]}
    registered = registered_sections()
    for source in sources:
        s = Source(
            id=source["id"],
            name=source["name"],
            url=source["url"],
            adapter=source["adapter"],
            zone=source["zone"],
            profile=source["profile"],
        )
        source["scope_reason"] = source_block(s, (s.pk, s.url) in registered)
        source["collectible"] = source["enabled"] and not source["scope_reason"]
        source["latest"] = latest.get(source["id"])
        source["noise_week"] = rates.get(source["id"])
        configured = source["profile"].get("cadence", "twice_daily")
        high = source["noise_week"] and source["noise_week"]["high_noise"]
        source["effective_cadence"] = "weekly" if high else configured
    observed = Counter()
    for event in Event.objects.filter(
        created__date=today,
        capture_method="自动采集",
        state__in=("approved", "pending"),
        merged_into=None,
    ).prefetch_related("media"):
        observed.update(set(m.category for m in event.media.all()))
    oldest = (
        Event.objects.filter(state="pending", merged_into=None)
        .order_by("created")
        .values_list("created", flat=True)
        .first()
    )
    failures = [
        s["name"] for s in sources if s["latest"] and s["latest"]["state"] == "failed"
    ]
    return {
        "daily": noise_report(1, today),
        "weekly": week,
        "oldest_pending": oldest,
        "pending_hours": round(
            max(0, (timezone.now() - oldest).total_seconds()) / 3600, 1
        )
        if oldest
        else None,
        "failed_sources": len(failures),
        "failed_names": failures,
        "minimum_observations": MINIMUM_OBSERVATIONS,
        "high_noise_rate": HIGH_NOISE_RATE,
        "by_category": [
            {
                "category": c,
                "observed": observed[c],
                "target": policy.category_targets.get(c),
            }
            for c in CATEGORIES
        ],
        "decisions": list(
            IntakeDecision.objects.order_by("-at", "-id").values(
                "source__name",
                "title",
                "url",
                "outcome",
                "noise_reason",
                "filtered_by",
                "terms",
                "source_zone",
                "at",
                "actor",
                "analysis",
            )[:60]
        ),
    }
