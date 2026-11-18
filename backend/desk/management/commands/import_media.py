from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from desk.models import Media
from desk.management_api import MediaSerializer


class Command(BaseCommand):
    help = "Validate a UTF-8 CSV of verified media; pass --apply to write all rows atomically"

    def add_arguments(self, p):
        p.add_argument("csv")
        p.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, csv, apply, **opts):
        import csv as csv_module

        with open(csv, encoding="utf-8-sig", newline="") as stream:
            rows = list(csv_module.DictReader(stream))
        seen = set()
        for number, row in enumerate(rows, 2):
            name = (row.get("name") or "").strip()
            if not name or not row.get("reference") or name in seen:
                raise CommandError(f"第 {number} 行缺少名称或出处，或该机构重复。")
            seen.add(name)
            data = {
                k: v
                for k, v in row.items()
                if k
                in (
                    "name",
                    "owner",
                    "level",
                    "category",
                    "subcategory",
                    "region",
                    "kind",
                    "focus",
                    "reference",
                    "digest_section",
                )
                and v
            }
            data["name"] = name
            form = MediaSerializer(
                Media.objects.filter(name=name).first(), data=data, partial=True
            )
            if not form.is_valid():
                raise CommandError(f"第 {number} 行：{form.errors}")
            if apply:
                form.save()
        self.stdout.write(f"{len(rows)} 行" + ("已导入" if apply else "通过预检"))
