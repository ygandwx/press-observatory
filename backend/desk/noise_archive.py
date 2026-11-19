"""Versioned Shanghai calendar-week dispositions, separate from rolling monitoring."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from time import sleep
from zoneinfo import ZoneInfo

from django.db import OperationalError, connection, transaction
from django.db.models import Prefetch
from django.utils import timezone

from .coverage import HIGH_NOISE_RATE, MINIMUM_OBSERVATIONS
from .models import Article, Change, Event, IntakeDecision, WeeklyNoiseEdition

SHANGHAI = ZoneInfo("Asia/Shanghai")
FINAL = ("pending", "approved", "filtered")
REVIEW_GATES = ("人工复核", "人工退回", "人工审核")
REVIEW_CHANGES = ("reject", "approve", "人工复核", "历史降噪", "撤回历史降噪")


def monday(day):
    return day - timedelta(days=day.weekday())


def stamp(value):
    return value.astimezone(SHANGHAI).isoformat()


def evidence(
    kind,
    pk,
    at,
    actor,
    outcome,
    *,
    gate="",
    noise="",
    reason="",
    terms=(),
    url="",
    title="",
    source=None,
):
    return {
        "record_type": kind,
        "id": pk,
        "at": stamp(at),
        "actor": actor,
        "outcome": outcome,
        "gate": gate,
        "noise_reason": noise,
        "reason": reason,
        "terms": list(terms or ()),
        "url": url,
        "title": title,
        "source_id": source,
    }


def decision_evidence(decision):
    info = decision.analysis or {}
    result = info.get("decision") or decision.outcome
    return evidence(
        "IntakeDecision",
        decision.pk,
        decision.at,
        decision.actor,
        result,
        gate=decision.filtered_by,
        noise=decision.noise_reason,
        reason=info.get("reason")
        or info.get("ai_reasoning")
        or info.get("status")
        or "",
        terms=decision.terms,
        url=decision.url,
        title=decision.title,
        source=decision.source_id,
    )


def disposition(item, previous="clean", previous_reason=""):
    action = item["outcome"]
    if action == "撤回历史降噪":
        return (
            ("noise", item["noise_reason"])
            if item["noise_reason"]
            else (item.get("restore_to", "clean"), "")
        )
    if action in ("restore", "confirm", "confirmed", "approve"):
        return "clean", ""
    if action == "pending" and item["gate"] in REVIEW_GATES:
        return "clean", ""
    if item["noise_reason"]:
        return "noise", item["noise_reason"]
    if action in ("reject", "rejected"):
        # An unclassified return does not retract an existing positive noise determination.
        return ("noise", previous_reason) if previous == "noise" else ("returned", "")
    return "clean", ""


def source_identity(origin, source_id, name):
    if origin == "manual":
        return "manual", "手工录入"
    if origin == "legacy":
        return "legacy", "历史记录（采集方式未留存）"
    return f"source:{source_id}", name or "未留存采集来源"


def assemble(cutoff):
    """Replay immutable audit records. Event.state alone never supplies a noise label."""
    originals = {}
    reviewed = defaultdict(list)
    by_event = defaultdict(list)
    intake_ids = {}
    mirrors = defaultdict(list)
    duplicates = []

    def observe(origin, source_id, source_name, item, event_id):
        key_group, label = source_identity(origin, source_id, source_name)
        key = f"{key_group}|{item['url'] or 'event:' + str(event_id)}"
        if key not in originals:
            originals[key] = {
                "key": key,
                "source_key": key_group,
                "source_name": label,
                "source_id": source_id,
                "origin": origin,
                "event_id": event_id,
                "url": item["url"],
                "title": item["title"],
                "original": item,
            }
        if event_id and not originals[key]["event_id"]:
            originals[key]["event_id"] = event_id
        if event_id and key not in by_event[event_id]:
            by_event[event_id].append(key)
        return key

    decisions = (
        IntakeDecision.objects.filter(at__lt=cutoff)
        .exclude(outcome__in=("failed", "blocked"))
        .select_related("source")
        .order_by("at", "pk")
    )
    for decision in decisions.iterator():
        if decision.outcome == "duplicate":
            if decision.event_id and decision.run_id and decision.url:
                duplicates.append(
                    (f"source:{decision.source_id}|{decision.url}", decision.event_id)
                )
            continue
        item = decision_evidence(decision)
        if decision.run_id is None and (
            decision.filtered_by in REVIEW_GATES
            or decision.outcome in ("rejected", "confirmed")
        ):
            if decision.event_id:
                reviewed[decision.event_id].append(item)
                intake_ids[decision.pk] = decision.event_id
                mirrors[(decision.event_id, decision.actor)].append(item)
            continue
        if decision.outcome not in FINAL:
            continue
        origin = "capture" if decision.run_id else "manual"
        key = observe(
            origin,
            decision.source_id,
            decision.source.name if decision.source_id else "",
            item,
            decision.event_id,
        )
        first = originals[key]
        if item["id"] != first["original"]["id"]:
            later = first.setdefault("observations", [])
            previous = later[-1] if later else first["original"]
            if any(item[k] != previous[k] for k in ("outcome", "gate", "noise_reason")):
                later.append(item)
    for key, event_id in duplicates:
        if key in originals:
            if not originals[key]["event_id"]:
                originals[key]["event_id"] = event_id
            if key not in by_event[event_id]:
                by_event[event_id].append(key)

    changes = list(
        Change.objects.filter(
            event_id__isnull=False, at__lt=cutoff, action__in=("录入", *REVIEW_CHANGES)
        ).order_by("at", "pk")
    )
    event_ids = {c.event_id for c in changes} | set(reviewed)
    missing = event_ids - set(by_event)
    events = {
        e.pk: e
        for e in Event.objects.filter(
            pk__in=missing, created__lt=cutoff
        ).prefetch_related(
            Prefetch(
                "articles",
                queryset=Article.objects.select_related("source").order_by("pk"),
            )
        )
    }
    initial_changes = {c.event_id: c for c in reversed(changes) if c.action == "录入"}
    for event_id, event in events.items():
        created = initial_changes.get(event_id)
        article = next(iter(event.articles.all()), None)
        if event.capture_method == "自动采集" or (
            created and created.actor == "采集工作进程"
        ):
            origin = "capture"
        elif event.capture_method in ("人工录入", "AI 辅助录入"):
            origin = "manual"
        else:
            origin = "legacy"
        source_id = article.source_id if article else None
        item = evidence(
            "Change" if created else "Event",
            created.pk if created else event_id,
            created.at if created else event.created,
            created.actor if created else event.creator,
            "录入" if created else "历史建档",
            reason="未留存最初的规则或模型判断。",
            url=article.url if article else "",
            title=article.title if article else event.title,
            source=source_id,
        )
        observe(
            origin,
            source_id,
            article.source.name if article and source_id else "",
            item,
            event_id,
        )

    # Older review() writes Change only for ordinary returns. Other write paths
    # put both records in the same transaction. Retain just the richer audit row.
    used = set()
    historical_reasons = {
        (c.event_id, c.detail.get("batch")): c.detail.get("before", {}).get(
            "noise_reason"
        )
        for c in changes
        if c.action == "撤回历史降噪"
    }
    historical_before = {
        (c.event_id, c.detail.get("batch")): c.detail.get("before", {})
        for c in changes
        if c.action == "历史降噪"
    }
    for c in changes:
        if c.action == "录入" or c.event_id not in by_event:
            continue
        detail = c.detail or {}
        if intake_ids.get(detail.get("intake_decision")) == c.event_id:
            continue
        action = detail.get("decision", c.action)
        compatible = {
            "reject": {"reject", "rejected"},
            "approve": {"approve", "confirmed"},
            "restore": {"restore"},
            "noise": {"noise"},
            "confirm": {"confirm", "confirmed"},
            "pending": {"pending"},
        }.get(action, set())
        match = next(
            (
                m
                for m in reversed(mirrors[(c.event_id, c.actor)])
                if m["id"] not in used
                and m["outcome"] in compatible
                and m["reason"] == detail.get("reason", "")
                and abs((c.at - datetime.fromisoformat(m["at"])).total_seconds()) < 5
            ),
            None,
        )
        if match:
            used.add(match["id"])
            continue
        base = originals[by_event[c.event_id][0]]
        reason = detail.get("noise_reason", "")
        if c.action == "历史降噪":
            # The old cleaning command saved the original state and its matching
            # terms, but not always the resulting category. Never infer it from a title.
            reason = historical_reasons.get((c.event_id, detail.get("batch"))) or "其他"
        item = evidence(
            "Change",
            c.pk,
            c.at,
            c.actor,
            action,
            gate="历史规则复核" if "历史降噪" in c.action else "人工审核",
            noise=reason,
            reason=detail.get("reason", ""),
            terms=detail.get("terms", []),
            url=base["url"],
            title=base["title"],
            source=base["source_id"],
        )
        if c.action == "撤回历史降噪":
            saved = historical_before.get((c.event_id, detail.get("batch")), {})
            item["noise_reason"] = (
                saved.get("noise_reason", "") if saved.get("is_noise") else ""
            )
            item["restore_to"] = (
                "returned" if saved.get("state") == "rejected" else "clean"
            )
        reviewed[c.event_id].append(item)
    return originals, reviewed


def new_week(start):
    return {
        "week": start.isoformat(),
        "through": (start + timedelta(days=6)).isoformat(),
        "coverage": {
            "status": "no_evidence",
            "note": "本周没有可追溯的采集或录入处置。现有审计不能证明部署前或停采时段的实际噪音量。",
        },
        "thresholds": {
            "minimum_observations": MINIMUM_OBSERVATIONS,
            "high_noise_rate": HIGH_NOISE_RATE,
        },
        "total": 0,
        "noise": 0,
        "returned": 0,
        "negative_count": 0,
        "noise_rate": 0,
        "captured": 0,
        "manual": 0,
        "legacy": 0,
        "sources": [],
        "top_sources": [],
        "reasons": [],
        "negative_samples": [],
        "corrections": [],
    }


def week_content(start, records, reviews):
    content = new_week(start)
    reasons = Counter()
    sources = {}
    for sample in sorted(records, key=lambda s: s["key"]):
        event_id = sample["event_id"]
        original = sample["original"]
        group = sample["source_key"]
        origin = sample["origin"]
        row = sources.setdefault(
            group,
            {
                "source_key": group,
                "source_id": sample["source_id"] if origin == "capture" else None,
                "source__name": sample["source_name"],
                "origin": origin,
                "total": 0,
                "noise": 0,
                "returned": 0,
                "negative_count": 0,
            },
        )
        row["total"] += 1
        content["total"] += 1
        content[
            {"capture": "captured", "manual": "manual", "legacy": "legacy"}[origin]
        ] += 1
        status, reason = disposition(original)
        snapshots = []
        timeline = [
            (
                item,
                "historical_rule"
                if item["outcome"] in ("历史降噪", "撤回历史降噪")
                else "human",
            )
            for item in reviews.get(event_id, ())
        ]
        timeline.extend((item, "automatic") for item in sample.get("observations", ()))
        trace = {
            k: v for k, v in sample.items() if k not in ("original", "observations")
        }
        last_human = None
        for item, authority in sorted(
            timeline,
            key=lambda pair: (pair[0]["at"], pair[0]["record_type"], pair[0]["id"]),
        ):
            before, old_reason = status, reason
            if authority != "automatic" or (
                not last_human and (item["noise_reason"] or before == "clean")
            ):
                status, reason = disposition(item, before, old_reason)
            entry = {
                **item,
                "authority": authority,
                "before": before,
                "after": status,
                "before_reason": old_reason,
                "after_reason": reason,
            }
            snapshots.append(entry)
            if authority == "human":
                last_human = entry
            if (before, old_reason) != (status, reason):
                content["corrections"].append(
                    {
                        **trace,
                        "before": before,
                        "after": status,
                        "before_reason": old_reason,
                        "after_reason": reason,
                        "review": entry,
                        "original": original,
                        "history": list(snapshots),
                    }
                )
        if status in ("noise", "returned"):
            content["negative_samples"].append(
                {
                    **trace,
                    "classification": status,
                    "noise_reason": reason,
                    "original": original,
                    "last_review": last_human,
                    "history": snapshots,
                }
            )
            content["negative_count"] += 1
            row["negative_count"] += 1
            row[status] += 1
            content[status] += 1
            if status == "noise":
                reasons[reason] += 1
    if content["total"]:
        content["coverage"] = {
            "status": "observed",
            "note": "按首次可追溯处置归周；采集、手工和来源未明的历史材料分别统计，后续改判修订该周版本。",
        }
        content["noise_rate"] = round(100 * content["noise"] / content["total"], 1)
    for row in sources.values():
        row["rate"] = round(100 * row["noise"] / row["total"], 1)
        row["high_noise"] = (
            row["origin"] == "capture"
            and row["source_id"] is not None
            and row["total"] >= MINIMUM_OBSERVATIONS
            and 100 * row["noise"] >= HIGH_NOISE_RATE * row["total"]
        )
    content["sources"] = sorted(
        sources.values(), key=lambda r: (-r["rate"], -r["total"], r["source_key"])
    )
    content["top_sources"] = [r for r in content["sources"] if r["high_noise"]]
    content["reasons"] = [
        {"noise_reason": k, "count": v, "percent": round(100 * v / content["noise"], 1)}
        for k, v in sorted(reasons.items(), key=lambda t: (-t[1], t[0]))
    ]
    content["corrections"].sort(
        key=lambda c: (c["review"]["at"], c["key"], c["review"]["id"])
    )
    timestamps = [s["original"]["at"] for s in records]
    timestamps.extend(c["review"]["at"] for c in content["corrections"])
    timestamps.extend(
        item["at"]
        for sample in content["negative_samples"]
        for item in sample["history"]
    )
    content["coverage"]["latest_evidence_at"] = max(timestamps, default=None)
    return content


@transaction.atomic
def _archive_noise_weeks(today, outermost):
    """Seal all completed weeks or append revisions; return the number of new editions."""
    if today is None:
        today = timezone.localdate(timezone=SHANGHAI)
    elif isinstance(today, datetime):
        today = (today.astimezone(SHANGHAI) if today.tzinfo else today).date()
    if not isinstance(today, date):
        raise TypeError("须提供有效的上海本地日期。")
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            # Acquire the archive writer lock before any data SELECT takes the
            # repeatable-read snapshot. Normal intake and review writes continue.
            if outermost:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            table = connection.ops.quote_name(WeeklyNoiseEdition._meta.db_table)
            cursor.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
        elif connection.vendor == "sqlite":
            table = connection.ops.quote_name(WeeklyNoiseEdition._meta.db_table)
            cursor.execute(f"UPDATE {table} SET version = version WHERE 0=1")
    current = monday(today)
    last = current - timedelta(weeks=1)
    cutoff = datetime.combine(today + timedelta(days=1), time.min, tzinfo=SHANGHAI)
    originals, reviews = assemble(cutoff)
    by_week = defaultdict(list)
    for sample in originals.values():
        week = monday(date.fromisoformat(sample["original"]["at"][:10]))
        if week <= last:
            by_week[week].append(sample)
    versions = {}
    for week, version, digest in (
        WeeklyNoiseEdition.objects.filter(week__lte=last)
        .order_by("week", "version")
        .values_list("week", "version", "digest")
    ):
        versions[week] = (version, digest)
    start = min([last, *by_week, *versions])
    added = 0
    while start <= last:
        content = week_content(start, by_week.get(start, ()), reviews)
        digest = hashlib.sha256(
            json.dumps(
                content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        previous, old = versions.get(start, (0, ""))
        if previous and digest != old:
            latest_at = (
                WeeklyNoiseEdition.objects.filter(week=start, version=previous)
                .values_list("content__coverage__latest_evidence_at", flat=True)
                .get()
            )
            if latest_at and datetime.fromisoformat(latest_at) >= cutoff:
                # A deliberately older as-of invocation cannot undo a recorded, later correction.
                start += timedelta(weeks=1)
                continue
        if digest != old:
            WeeklyNoiseEdition.objects.create(
                week=start, version=previous + 1, digest=digest, content=content
            )
            added += 1
        start += timedelta(weeks=1)
    return added


def archive_noise_weeks(today=None):
    """SQLite shared-cache contenders may receive SQLITE_LOCKED before its busy timer."""
    for attempt in range(7):
        try:
            return _archive_noise_weeks(today, not connection.in_atomic_block)
        except OperationalError as exc:
            if (
                connection.vendor != "sqlite"
                or "locked" not in str(exc).lower()
                or attempt == 6
            ):
                raise
            sleep(0.025 * 2**attempt)
