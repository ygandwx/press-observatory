"""Preview, apply and undo version-checked editorial classifications."""

import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from desk.editorial import historical_assessment
from desk.history import lock_publications, report_ids
from desk.models import Change, Edition, Event, Report

FORMAT = "editorial-history-v1"
FIELDS = ("state", "is_noise", "noise_reason", "filtered_by", "event_nature")


def references():
    ids = set()
    for model in (Report, Edition):
        for content in model.objects.values_list("content", flat=True):
            ids.update(report_ids(content))
    return ids


def inventory():
    protected = references()
    approvals = set(
        Change.objects.filter(action__in=["approve", "approved"]).values_list(
            "event_id", flat=True
        )
    )
    rows = []
    for event in Event.objects.order_by("pk").prefetch_related("articles"):
        verdict = historical_assessment(event)
        if not verdict["reason"]:
            continue
        safeguards = []
        if event.approved or event.state == "approved" or event.pk in approvals:
            safeguards.append("已批准")
        if event.pk in protected:
            safeguards.append("简报引用")
        if event.merged_into_id or event.merged.exists():
            safeguards.append("归并关系")
        if event.state != "pending" and not safeguards:
            safeguards.append("非待审")
        rows.append(
            {
                "id": event.pk,
                "version": event.version,
                "title": event.title,
                "urls": sorted(a.url for a in event.articles.all()),
                "state": event.state,
                "reason": verdict["reason"],
                "terms": verdict["terms"],
                "gate": verdict["gate"],
                "protected": safeguards,
            }
        )
    return {"format": FORMAT, "rows": rows}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Command(BaseCommand):
    help = "生成私有逐条预览。经审批后按文件摘要应用或撤回，整批原子执行。"

    def add_arguments(self, parser):
        parser.add_argument("mode", choices=("preview", "apply", "undo"))
        parser.add_argument("file", type=Path)
        parser.add_argument("--sha256", default="")

    @transaction.atomic
    def handle(self, mode, file, sha256, **options):
        if mode == "preview":
            data = (
                json.dumps(inventory(), ensure_ascii=False, indent=2) + "\n"
            ).encode()
            with file.open("xb") as output:
                file.chmod(0o600)
                output.write(data)
            self.stdout.write(f"{digest(data)}  {file}")
            return
        data = file.read_bytes()
        if digest(data) != sha256:
            raise CommandError("文件与批准的 SHA-256 不符。")
        manifest = json.loads(data)
        if manifest.get("format") != FORMAT:
            raise CommandError("清单版本无效。")
        lock_publications()
        eligible = [r for r in manifest["rows"] if not r["protected"]]
        if len({r["id"] for r in eligible}) != len(eligible):
            raise CommandError("清单包含重复事件。")
        list(
            Event.objects.select_for_update()
            .filter(pk__in=[r["id"] for r in eligible])
            .order_by("pk")
        )
        current = {r["id"]: r for r in inventory()["rows"]} if mode == "apply" else {}
        protected = references()
        changed = 0
        for row in eligible:
            event = Event.objects.get(pk=row["id"])
            if (
                event.pk in protected
                or event.approved
                or event.merged_into_id
                or event.merged.exists()
            ):
                raise CommandError(f"事件 {event.pk} 受保护，已取消整批操作。")
            earlier = Change.objects.filter(
                event=event, action="历史降噪", detail__batch=sha256
            ).first()
            undone = Change.objects.filter(
                event=event, action="撤回历史降噪", detail__batch=sha256
            ).exists()
            if mode == "apply":
                if earlier:
                    if undone:
                        raise CommandError("已撤回的批次需重新预览。")
                    continue
                if current.get(event.pk) != row:
                    raise CommandError(f"事件 {event.pk} 已变化，须重新预览。")
                before = {k: getattr(event, k) for k in FIELDS}
                event.state = "filtered"
                event.is_noise = True
                event.noise_reason = row["reason"]
                event.event_nature = {"广告": "广告招商"}.get(
                    row["reason"], row["reason"]
                )
                event.filtered_by = row["gate"]
                action = "历史降噪"
            else:
                if undone:
                    continue
                if (
                    not earlier
                    or event.version != earlier.detail["version"]
                    or event.state != "filtered"
                ):
                    raise CommandError(f"事件 {event.pk} 未应用或已变化，取消撤回。")
                before = {k: getattr(event, k) for k in FIELDS}
                for key, value in earlier.detail["before"].items():
                    setattr(event, key, value)
                action = "撤回历史降噪"
            event.version += 1
            event.save(update_fields=[*FIELDS, "version"])
            Change.objects.create(
                event=event,
                action=action,
                actor="历史清洗命令",
                detail={
                    "batch": sha256,
                    "version": event.version,
                    "before": before,
                    "terms": row["terms"],
                },
            )
            changed += 1
        self.stdout.write(f"本批实际更新 {changed} 条。")
