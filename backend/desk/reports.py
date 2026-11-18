from collections import Counter
from datetime import timedelta
import secrets
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import Event, Media, Report, Edition, Change
from .catalog import CATEGORIES, DIGEST_SECTIONS, DIGEST_BY_SUBCATEGORY, MARKET_SECTIONS
from .domain import event_row, require, check_version, Conflict


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


def build_content(kind, day):
    start = day if kind == "daily" else day - timedelta(days=day.weekday())
    end = start if kind == "daily" else start + timedelta(days=6)
    if kind == "weekly" and end >= timezone.localdate():
        raise ValidationError("请选择已结束的自然周。")
    selected = (
        Event.objects.filter(
            state="approved", merged_into=None, approved__date__range=(start, end)
        )
        .prefetch_related("media", "articles", "merged__media", "merged__articles")
        .order_by("-approved", "pk")
    )
    rows = [event_row(e) for e in selected]
    priority = {"核心": 0, "重点": 1, "常规": 2}
    rows.sort(key=lambda r: priority.get(r["importance"], 3))
    if kind == "weekly" and not rows:
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
                approved__date__range=(start - timedelta(days=start.weekday()), end),
            ).count(),
            "在库媒体": Media.objects.count(),
            "生成时待审核": Event.objects.filter(
                state="pending", merged_into=None
            ).count(),
        },
        "events": items,
    }


@transaction.atomic
def generate(user, kind, day, regenerate=False, version=None):
    require(user, "edit")
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
def report_action(user, pk, action, data):
    r = Report.objects.select_for_update().get(pk=pk)
    right = (
        "sign"
        if action in ("issue", "share", "revoke", "archive", "correct")
        else "edit"
    )
    require(user, right)
    check_version(r, data.get("version"))
    if action == "edit" and r.state == "draft":
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
        detail={"report": r.pk, "version": r.version},
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
    return result
