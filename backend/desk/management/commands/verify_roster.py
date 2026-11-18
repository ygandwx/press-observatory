"""Import individually sourced public facts without altering the original archive capture."""

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from desk.catalog import CHANNELS
from desk.models import Media, Change

FIELDS = {"简介": "description", "主体": "owner", "地区": "region", "类型": "kind"}
ALL_FIELDS = {*FIELDS, *CHANNELS}


def check_url(value):
    if not isinstance(value, str):
        raise ValueError("证据网址须为字符串")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("证据需要完整公共页面地址")


def validate(row):
    if not isinstance(row["name"], str) or not row["name"].strip():
        raise ValueError("档案名称缺失")
    date.fromisoformat(row["date"])
    if set(row["checks"]) != ALL_FIELDS or not all(
        isinstance(v, str) and v.strip() for v in row["checks"].values()
    ):
        raise ValueError("每个字段均须记录核验结果或缺项原因")
    if (
        not isinstance(row["facts"], list)
        or not isinstance(row["searches"], list)
        or not row["searches"]
    ):
        raise ValueError("缺少逐家检索记录")
    for item in row["facts"]:
        if item["field"] not in ALL_FIELDS or not all(
            isinstance(item[k], str) and item[k].strip()
            for k in ("value", "basis", "date")
        ):
            raise ValueError("档案字段缺少值、日期或依据")
        date.fromisoformat(item["date"])
        check_url(item["url"])
        if item["field"] == "官网":
            check_url(item["value"])
    for item in row["searches"] + row.get("candidates", []):
        check_url(item["url"])
        if not isinstance(item["note"], str) or not item["note"].strip():
            raise ValueError("查阅记录缺少说明")
    return UUID(row["id"])


def enrich(media, row):
    values = {}
    for item in row["facts"]:
        values.setdefault(item["field"], item["value"])
    channels = dict(media.channels)
    previous = media.verification.get("applied", {})
    applied = {}
    for field, value in values.items():
        if field in CHANNELS:
            current = channels.get(field, "")
            if not current or current == previous.get(field):
                channels[field] = value
                applied[field] = value
        else:
            key = FIELDS[field]
            current = getattr(media, key)
            limit = media._meta.get_field(key).max_length
            if (not current or current == previous.get(field)) and (
                not limit or len(value) <= limit
            ):
                setattr(media, key, value)
                applied[field] = value
    # A withdrawn verification must not silently erase local editorial work.
    media.channels = channels
    media.verification = {"record": row, "applied": applied}


class Command(BaseCommand):
    help = "预检或事务导入有出处的逐家核验台账。导出保留完整证据与查阅结果。"

    def add_arguments(self, p):
        p.add_argument("mode", choices=["import", "export"])
        p.add_argument("file", type=Path)
        p.add_argument("--expected", type=int)
        p.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, mode, file, expected, apply, **options):
        if mode == "export":
            rows = [
                m.verification["record"]
                for m in Media.objects.exclude(verification={}).order_by("origin_id")
            ]
            file.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
            )
            self.stdout.write(f"导出 {len(rows)} 家")
            return
        try:
            rows = json.loads(file.read_text(encoding="utf-8-sig"))
            if (
                not isinstance(rows, list)
                or expected is not None
                and len(rows) != expected
            ):
                raise ValueError("名册数量与预期不符")
            seen = set()
            changed = 0
            for row in rows:
                identifier = validate(row)
                if identifier in seen:
                    raise ValueError("核验名册含重复标识")
                seen.add(identifier)
                media = Media.objects.select_for_update().get(origin_id=identifier)
                if media.name != row["name"]:
                    raise ValueError("原站标识与机构名称不一致")
                if media.verification.get("record") == row:
                    continue
                enrich(media, row)
                media.full_clean(
                    exclude=["origin_record", "verification", "channels", "tags"]
                )
                changed += 1
                if apply:
                    media.save(
                        update_fields=[
                            "verification",
                            "channels",
                            *FIELDS.values(),
                            "updated",
                        ]
                    )
            if changed and apply:
                Change.objects.create(
                    actor="档案核验导入",
                    action="核验机构资料",
                    detail={"count": changed, "total": len(rows)},
                )
        except (OSError, KeyError, TypeError, ValueError, Media.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"{len(rows)} 家，{'已更新' if apply else '预计更新'} {changed}，未变 {len(rows) - changed}"
        )
