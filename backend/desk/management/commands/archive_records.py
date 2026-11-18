"""Import read-only archive detail captures or export the unchanged source records."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from desk.catalog import CHANNELS
from desk.models import Media

FIELD_MAP = {
    "媒体名称": "name",
    "主管主办单位": "owner",
    "一级分类": "category",
    "二级子类": "subcategory",
    "媒体层级": "level",
    "地区": "region",
    "关注等级": "focus",
    "档案状态": "archive_status",
    "定位简介": "description",
    "高优先级标注": "high_priority",
    "tags": "tags",
}
EMPTY = {"", "暂无", "该渠道信息待补充"}
ORIGIN = "4m96d46usftbd.doubaoapps.com"
PREFIX = "/app/app_17gxuzrs2fg/archives/"


def capture(row):
    parsed = urlsplit(row["url"])
    identifier = UUID(parsed.path.split("/")[-1])
    if (parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment) != (
        "https",
        ORIGIN,
        PREFIX + str(identifier),
        "",
        "",
    ):
        raise ValueError("原站详情标识或网址无效")
    form = row["form"]
    values = {field: form[label] for label, field in FIELD_MAP.items()}
    if not values["name"] or values["name"] != row["name"]:
        raise ValueError("卡片与详情名称不一致")
    if (
        not isinstance(values["tags"], list)
        or not all(isinstance(tag, str) for tag in values["tags"])
        or not isinstance(values["high_priority"], bool)
    ):
        raise ValueError("标签或高优先级字段格式无效")
    if not all(
        isinstance(v, str)
        for k, v in values.items()
        if k not in ("tags", "high_priority")
    ):
        raise ValueError("档案文字字段格式无效")
    channels = {}
    for kind in CHANNELS:
        matrix = row["metadata"][kind]
        text, links = matrix["text"], matrix["links"]
        if (
            not isinstance(text, str)
            or not isinstance(links, list)
            or not all(
                isinstance(a, list)
                and len(a) == 2
                and all(isinstance(s, str) for s in a)
                for a in links
            )
        ):
            raise ValueError("渠道原值格式无效")
        raw = form.get("公众号" if kind == "官方公众号" else kind, "")
        if not isinstance(raw, str):
            raise ValueError("渠道字段格式无效")
        link = links[0][1] if links else ""
        display_value = text if text not in EMPTY else ""
        value = raw or (
            (link or display_value) if kind == "官网" else (display_value or link)
        )
        if value:
            channels[kind] = value
    return identifier, values, channels


class Command(BaseCommand):
    help = "Preserve original archive IDs, form fields and five channel records; dry-run by default"

    def add_arguments(self, p):
        p.add_argument("mode", choices=["import", "export"])
        p.add_argument("file", type=Path)
        p.add_argument("--apply", action="store_true")
        p.add_argument("--expected", type=int)

    @transaction.atomic
    def handle(self, mode, file, apply, expected, **options):
        if mode == "export":
            rows = list(
                Media.objects.exclude(origin_id=None)
                .order_by("origin_id")
                .values_list("origin_record", flat=True)
            )
            file.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            self.stdout.write(f"{len(rows)} 份原始档案已导出")
            return
        try:
            rows = json.loads(file.read_text(encoding="utf-8-sig"))
            if not isinstance(rows, list) or (
                expected is not None and len(rows) != expected
            ):
                raise ValueError("文件数目与预期不符")
            seen_ids, seen_names = set(), set()
            created = changed = 0
            for i, row in enumerate(rows, 1):
                identifier, values, channels = capture(row)
                name = values["name"]
                if identifier in seen_ids or name in seen_names:
                    raise ValueError(f"第 {i} 行重复的原站标识或机构名称")
                seen_ids.add(identifier)
                seen_names.add(name)
                existing = (
                    Media.objects.select_for_update()
                    .filter(origin_id=identifier)
                    .first()
                )
                same_name = Media.objects.select_for_update().filter(name=name).first()
                if same_name and (
                    (existing and existing.pk != same_name.pk)
                    or same_name.origin_id not in (None, identifier)
                ):
                    raise ValueError(f"第 {i} 行与已有机构标识冲突")
                media = existing or same_name or Media(kind="", focus="")
                if media.origin_record == row:
                    continue
                if media.pk:
                    changed += 1
                else:
                    created += 1
                for key, value in values.items():
                    setattr(media, key, value)
                media.origin_id = identifier
                media.origin_record = row
                media.channels = {**media.channels, **channels}
                if not media.reference:
                    media.reference = row["url"]
                media.full_clean(exclude=["channels", "tags"])
                if apply:
                    media.save()
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"{len(rows)} 份，新增 {created}，更新 {changed}，未变 {len(rows) - created - changed}，"
            + ("已导入" if apply else "预检通过")
        )
