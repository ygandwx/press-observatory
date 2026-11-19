import hashlib
import hmac
import secrets
from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .catalog import CATEGORIES, DIGEST_BY_SUBCATEGORY, DIGEST_SECTIONS, MARKET_SECTIONS
from .domain import Conflict, check_version, event_row, require
from .geo import geo_entry
from .models import Change, Edition, Event, Media, Report


def daily_sections(rows):
    categories = {name: [] for name in DIGEST_SECTIONS}
    evidence = {name: [] for name in DIGEST_SECTIONS}
    unresolved = []
    for row in rows:
        assigned = set()
        unknown = []
        for m in row["media"]:
            group = None
            if m["subcategory"] in CATEGORIES.get(m["category"], ()):
                group = DIGEST_BY_SUBCATEGORY.get(m["subcategory"])
            if (
                m["category"] == "头部市场化纸媒"
                and m["digest_section"] in MARKET_SECTIONS
                and m["reference"]
            ):
                group = m["digest_section"]
            if group:
                assigned.add(group)
            else:
                unknown.append(m["name"])
        if unknown or not row["media"]:
            unresolved.append({"id": row["id"], "media": unknown})
        for name in assigned:
            if len(categories[name]) < 2:
                categories[name].append(row["summary"] or row["title"])
                evidence[name].append(row["id"])
    return {
        "categories": categories,
        "section_order": list(DIGEST_SECTIONS),
        "section_events": evidence,
        "unclassified": unresolved,
    }


def build_content(kind, day, *, allow_empty=False):
    start = day if kind == "daily" else day - timedelta(days=day.weekday())
    end = start if kind == "daily" else start + timedelta(days=6)
    if kind == "weekly" and end >= timezone.localdate():
        raise ValidationError("请选择已结束的自然周。")
    selected = (
        Event.objects.filter(
            state="approved",
            merged_into=None,
            is_noise=False,
            approved__date__range=(start, end),
        )
        .prefetch_related("media", "articles", "merged__media", "merged__articles")
        .order_by("-approved", "pk")
    )
    rows = [event_row(e) for e in selected]
    priority = {"核心": 0, "重点": 1, "常规": 2}
    rows.sort(key=lambda r: priority.get(r["importance"], 3))
    if kind == "weekly" and not rows and not allow_empty:
        raise ValidationError("本周尚无已批准事件。")
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "category": r["category"],
            "subtype": r["subtype"],
            "media": [m["name"] for m in r["media"]],
            "sources": [a["url"] for a in r["articles"]],
            **(
                {
                    "classification_sources": [
                        {
                            "media": m["name"],
                            "section": m["digest_section"],
                            "url": m["reference"],
                        }
                        for m in r["media"]
                        if m["category"] == "头部市场化纸媒"
                        and m["digest_section"] in MARKET_SECTIONS
                        and m["reference"]
                    ]
                }
                if kind == "daily"
                else {}
            ),
        }
        for r in rows
    ]
    by_type = Counter(r["category"] for r in rows)
    headline = (
        f"本期入库 {len(items)} 件动态"
        + (
            "，" + "、".join(f"{k} {v} 件" for k, v in by_type.most_common(3))
            if items
            else ""
        )
        + "。"
    )
    report_sections = (
        daily_sections(rows)
        if kind == "daily"
        else {
            "categories": {
                cat: [
                    r["title"]
                    for r in rows
                    if any(m["category"] == cat for m in r["media"])
                ][:2]
                for cat in CATEGORIES
            }
        }
    )
    watch = {
        "停刊休刊": ["停刊", "休刊"],
        "合并调整": ["合并", "整合"],
        "机构改革与撤并重组": ["机构调整", "机构改革", "重组"],
    }
    special = {
        k: [
            r["title"]
            for r in rows
            if any(w in r["subtype"] + r["title"] for w in words)
        ]
        for k, words in watch.items()
    }
    if kind == "weekly":
        previous_start = start - timedelta(days=7)
        previous_end = start - timedelta(days=1)
        previous = dict(
            Event.objects.filter(
                state="approved",
                merged_into=None,
                is_noise=False,
                approved__date__range=(previous_start, previous_end),
            )
            .order_by()
            .values_list("category")
            .annotate(n=Count("id"))
        )
        previous_total = sum(previous.values())
        report_sections["week_comparison"] = {
            "start": str(previous_start),
            "end": str(previous_end),
            "total": previous_total,
            "change": len(items) - previous_total,
            "categories": {
                k: {"current": by_type[k], "previous": previous.get(k, 0)}
                for k in sorted(set(previous) | set(by_type))
            },
        }
        report_sections["geo_progress"] = [
            entry
            for event in sorted(
                selected, key=lambda item: priority.get(item.importance, 3)
            )
            if (entry := geo_entry(event))
        ]
    return {
        "start": str(start),
        "end": str(end),
        "headline": headline,
        "key_changes": [r["title"] for r in rows[:5]],
        **report_sections,
        "special": special,
        "snapshot": {
            "本期入库": len(rows),
            "本周累计": Event.objects.filter(
                state="approved",
                merged_into=None,
                is_noise=False,
                approved__date__range=(start - timedelta(days=start.weekday()), end),
            ).count(),
            "在库媒体": Media.objects.count(),
            "生成时待审核": Event.objects.filter(
                state="pending", merged_into=None
            ).count(),
        },
        "events": items,
    }


def generate(user, kind, day, regenerate=False, version=None):
    require(user, "edit")
    return draft_for_period(kind, day, regenerate, version)


@transaction.atomic
def draft_for_period(kind, day, regenerate=False, version=None):
    if kind not in ("daily", "weekly"):
        raise ValidationError("无效报告类型。")
    if kind == "weekly":
        day -= timedelta(days=day.weekday())
    r, created = Report.objects.select_for_update().get_or_create(kind=kind, day=day)
    if not created and not regenerate:
        return r
    if not created:
        check_version(r, version)
    if r.state not in ("draft", "archived") or r.editions.exists():
        raise Conflict("已签发版本已锁定。")
    r.content = build_content(kind, day)
    r.version += 1
    r.save()
    return r


@transaction.atomic
def schedule_weekly(today=None):
    """The latest complete Beijing week is retried on subsequent timer ticks."""
    today = today or timezone.localdate()
    start = today - timedelta(days=today.weekday() + 7)
    if Report.objects.filter(kind="weekly", day=start).exists():
        return None
    if not Event.objects.filter(
        state="approved",
        is_noise=False,
        merged_into=None,
        approved__date__range=(start, start + timedelta(days=6)),
    ).exists():
        return None
    return draft_for_period("weekly", start)


def weekly_share_key(report, edition_version, view):
    payload = f"{report.pk}-{edition_version}-{view}"
    signature = hmac.new(
        report.share_token.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"w-{payload}-{signature}"


def weekly_share_views(report, version):
    return {
        view: weekly_share_key(report, version, view) for view in ("leader", "detail")
    }


@transaction.atomic
def report_action(user, pk, action, data):
    r = Report.objects.select_for_update().get(pk=pk)
    right = (
        "sign"
        if action in ("issue", "share", "revoke", "archive", "correct")
        else "edit"
    )
    require(user, right)
    check_version(r, data.get("version"))
    change = {}
    if action == "rebuild_correction" and r.state == "draft" and r.editions.exists():
        reason = str(data.get("reason") or "").strip()
        if not reason or len(reason) > 500:
            raise ValidationError("请填写 1 至 500 字的更正依据。")
        previous = r.editions.latest("version")
        old = {e["id"] for e in previous.content.get("events", [])}
        r.content = build_content(r.kind, r.day, allow_empty=True)
        current = {e["id"] for e in r.content["events"]}
        change = {
            "previous_edition": previous.version,
            "reason": reason,
            "removed": sorted(old - current),
            "added": sorted(current - old),
        }
        r.content["correction"] = change
    elif action == "edit" and r.state == "draft":
        for key in ("headline", "key_changes", "categories", "special"):
            if key in data:
                val = data[key]
                if key == "headline":
                    valid = isinstance(val, str) and 0 < len(val) <= 2000
                elif key == "key_changes":
                    valid = (
                        isinstance(val, list)
                        and len(val) <= 5
                        and all(isinstance(v, str) for v in val)
                    )
                else:
                    valid = (
                        isinstance(val, dict)
                        and set(val) == set(r.content[key])
                        and all(
                            isinstance(arr, list)
                            and all(isinstance(v, str) for v in arr)
                            and (
                                key != "categories"
                                or "section_order" not in r.content
                                or len(arr) <= 2
                            )
                            for arr in val.values()
                        )
                    )
                if not valid:
                    raise ValidationError("报告字段格式无效。")
                r.content[key] = val
    elif action == "submit" and r.state == "draft":
        r.state = "pending"
    elif action == "issue" and r.state in ("draft", "pending"):
        r.issuer = user.get_username()
        r.issued = timezone.now()
        r.state = "published"
        Edition.objects.create(
            report=r,
            content=r.content,
            issuer=r.issuer,
            issued=r.issued,
            version=r.version + 1,
        )
    elif action == "archive" and r.state == "published":
        r.state = "archived"
    elif action == "correct" and r.state in ("published", "archived"):
        r.state = "draft"
        r.issuer = ""
        r.issued = None
        r.share_token = None
    elif action == "share" and r.state in ("published", "archived"):
        r.share_token = r.share_token or secrets.token_urlsafe(32)
    elif action == "revoke":
        r.share_token = None
    else:
        raise Conflict("当前报告状态不支持此操作。")
    r.version += 1
    r.save()
    Change.objects.create(
        actor=user.username,
        action=action,
        detail={"report": r.pk, "version": r.version, **change},
    )
    return r


def report_row(r, detail=False, manage=False):
    result = {
        "id": r.id,
        "kind": r.kind,
        "day": r.day,
        "state": r.state,
        "issuer": r.issuer,
        "issued": r.issued,
        "version": r.version,
        "headline": r.content.get("headline", ""),
    }
    if detail:
        result["content"] = r.content
        result["history"] = list(
            r.editions.order_by("-version").values("version", "issuer", "issued")
        )
    if manage:
        result["share"] = r.share_token
        if (
            r.kind == "weekly"
            and r.share_token
            and r.state in ("published", "archived")
        ):
            version = r.editions.latest("version").version
            result["share_views"] = weekly_share_views(r, version)
    return result
