"""Daily dashboard comparisons and evidence behind business-day alerts."""

import hashlib
import re
from datetime import timedelta

from .labels import text_labels
from .models import FrozenList


def stock_comparisons(user, day, current):
    def key(d):
        return hashlib.sha256(f"dashboard-inventory:{user.pk}:{d}".encode()).hexdigest()

    FrozenList.objects.update_or_create(
        pk=key(day),
        defaults={"owner": user, "scope": "dashboard-inventory", "rows": current},
    )
    for days, label in [(1, "昨日最近观测"), (7, "上周同日最近观测")]:
        past = (
            FrozenList.objects.filter(pk=key(day - timedelta(days=days)), owner=user)
            .values_list("rows", flat=True)
            .first()
        )
        if past is not None:
            return {
                name: {"change": value - past[name], "comparison": label}
                for name, value in current.items()
            }
    return {name: {"note": "暂无昨日或上周同日观测"} for name in current}


ALERT_PATTERNS = {
    "媒体停刊休刊": re.compile(r"停刊|休刊|停止(?:纸质版|纸版|纸质报刊)?出版"),
    "媒体机构调整": re.compile(
        r"(?:机构|组织架构|组织机构|内部部门)(?:调整|重组|整合|改革|撤并)|(?:撤并|合并|重组)(?:部门|机构)"
    ),
    "重点媒体经营承压": re.compile(
        r"经营承压|(?:营收|营业收入)(?:同比)?(?:下滑|下降|减少|负增长)|(?:连续|经营|净)亏损"
    ),
}
UNCONFIRMED = re.compile(
    r"澄清|辟谣|传闻|传言|是否|网传|并未|没有|未出现|否认|未曾|未见|并非|并无|不再|尚未|未计划|未停刊|未休刊|[？?]"
)


def business_alerts(rows):
    groups = {name: [] for name in ALERT_PATTERNS}
    for row in rows:
        if row.get("is_noise"):
            continue
        title = row.get("title") or ""
        evidence = [row.get("subtype") or "", *text_labels(row.get("tags")), title]
        for name, pattern in ALERT_PATTERNS.items():
            if name == "重点媒体经营承压" and not any(
                m.get("high_priority") or m.get("focus") in ("核心必看", "重点关注")
                for m in row.get("media", [])
            ):
                continue
            # A denial in the headline outranks a broad category or tag.
            clauses = re.split(r"[，。；;！!]", title)
            if any(pattern.search(c) and UNCONFIRMED.search(c) for c in clauses):
                continue
            if any(pattern.search(x) and not UNCONFIRMED.search(x) for x in evidence):
                groups[name].append(row)
    return [
        (name, items)
        for name, items in groups.items()
        if len(items) >= (2 if name == "媒体机构调整" else 1)
    ]
