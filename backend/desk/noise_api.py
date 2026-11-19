"""Operations-only access to immutable weekly quality editions."""

import csv
import json

from django.db.models import Exists, F, OuterRef
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from .accounts import business_role
from .api import endpoint
from .domain import bounded_int, parse_day
from .events_api import page_rows
from .models import WeeklyNoiseEdition


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def report_rows(content):
    yield [
        "行类型",
        "项目",
        "处理数",
        "噪音数",
        "百分比",
        "普通退回数",
        "负样本数",
        "高噪排名",
        "说明",
    ]
    yield [
        "全周",
        content["week"],
        content["total"],
        content["noise"],
        content["noise_rate"],
        content["returned"],
        content["negative_count"],
        "",
        content["coverage"]["note"],
    ]
    for item in content["reasons"]:
        yield [
            "噪音类型",
            item["noise_reason"],
            "",
            item["count"],
            item["percent"],
            "",
            "",
            "",
            "分母为当周噪音数",
        ]
    ranks = {r["source_key"]: i for i, r in enumerate(content["top_sources"], 1)}
    for source in content["sources"]:
        yield [
            "来源",
            source["source__name"],
            source["total"],
            source["noise"],
            source["rate"],
            source["returned"],
            source["negative_count"],
            ranks.get(source["source_key"], ""),
            "分母为本来源处理数",
        ]
    for item in content["corrections"]:
        yield [
            {"automatic": "自动判定变更", "historical_rule": "历史规则复核"}.get(
                item["review"].get("authority"), "人工复核"
            ),
            item["key"],
            "",
            "",
            "",
            "",
            "",
            "",
            json.dumps(item["review"], ensure_ascii=False),
        ]


def sample_rows(content):
    yield [
        "样本标识",
        "事件编号",
        "标题",
        "原文",
        "来源",
        "来源方式",
        "样本标签",
        "噪音原因",
        "原始证据类型",
        "原始记录号",
        "原始时间",
        "判定者",
        "原结果",
        "过滤依据",
        "原理由",
        "命中词",
        "最近人工记录号",
        "人工复核时间",
        "复核人",
        "复核理由",
        "完整判定历史",
    ]
    for sample in content["negative_samples"]:
        original, review = sample["original"], sample["last_review"] or {}
        yield [
            sample["key"],
            sample["event_id"],
            sample["title"],
            sample["url"],
            sample["source_name"],
            sample["origin"],
            "噪音" if sample["classification"] == "noise" else "普通退回待校准",
            sample["noise_reason"],
            original["record_type"],
            original["id"],
            original["at"],
            original["actor"],
            original["outcome"],
            original["gate"],
            original["reason"],
            "、".join(original["terms"]),
            review.get("id"),
            review.get("at"),
            review.get("actor"),
            review.get("reason"),
            json.dumps(sample["history"], ensure_ascii=False),
        ]


def export_edition(edition, kind):
    if kind not in ("samples", "report"):
        raise ValidationError("未知导出类型。")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="noise-{kind}-{edition.week}-v{edition.version}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    rows = (sample_rows if kind == "samples" else report_rows)(edition.content)
    writer.writerow(["周起始日", "周结束日", "版本", "归档时间", *next(rows)])
    prefix = [
        edition.week,
        edition.content["through"],
        edition.version,
        edition.created.isoformat(),
    ]
    for row in rows:
        writer.writerow([*prefix, *(csv_cell(value) for value in row)])
    return response


@endpoint(["GET"])
def noise_weeks(req, week=None):
    if business_role(req.user) != "运营编辑":
        raise PermissionDenied("周度质检报告仅对运营编辑开放。")
    if week is None:
        newer = WeeklyNoiseEdition.objects.filter(
            week=OuterRef("week"), version__gt=OuterRef("version")
        )
        latest = (
            WeeklyNoiseEdition.objects.filter(~Exists(newer))
            .order_by("-week")
            .values(
                "week",
                "version",
                "created",
                total=F("content__total"),
                noise=F("content__noise"),
                sample_count=F("content__negative_count"),
            )
        )
        response = Response(page_rows(req, latest, lambda row: row))
    else:
        day = parse_day(week)
        if day.weekday():
            raise ValidationError("请选择自然周的周一。")
        editions = WeeklyNoiseEdition.objects.filter(week=day).order_by("-version")
        if req.GET.get("version") is not None:
            version = bounded_int(req.GET["version"], 1, 2**31 - 1)
            edition = get_object_or_404(editions, version=version)
        else:
            edition = editions.first()
            if edition is None:
                raise NotFound("本周尚无噪音报告。")
        if "export" in req.GET:
            response = export_edition(edition, req.GET["export"])
        else:
            response = Response(
                {
                    "week": day,
                    "version": edition.version,
                    "created": edition.created,
                    "digest": edition.digest,
                    "content": edition.content,
                    "versions": list(editions.values_list("version", flat=True)),
                }
            )
    response["Cache-Control"] = "private, no-store"
    return response
