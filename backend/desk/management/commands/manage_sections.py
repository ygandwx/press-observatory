"""Stage reviewed public sections and require a matching successful sample for scheduling."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from desk.collection import enqueue, source_signature
from desk.crawl import validate_profile
from desk.external import normalize_url
from desk.models import Article, Change, Media, Source


class Command(BaseCommand):
    help = "栏目配置预检、手动小样本投递与实测后定时启用。默认只预检。"

    def add_arguments(self, p):
        p.add_argument("file", type=Path)
        p.add_argument("--apply", action="store_true")
        mode = p.add_mutually_exclusive_group()
        mode.add_argument("--trial-key")
        mode.add_argument("--schedule", action="store_true")

    @transaction.atomic
    def handle(self, file, apply, trial_key, schedule, **options):
        if (trial_key or schedule) and not apply:
            raise CommandError("修改定时或投递任务时请显式指定 --apply")
        if trial_key and len(trial_key) > 60:
            raise CommandError("批次标识过长")
        try:
            rows = json.loads(file.read_text(encoding="utf8"))
            if not isinstance(rows, list):
                raise ValueError("需要栏目数组")
            seen = set()
            for row in rows:
                url = normalize_url(row["url"])
                name = row["name"]
                if url in seen or not isinstance(name, str) or not name.strip():
                    raise ValueError("栏目地址重复或名称无效")
                seen.add(url)
                profile = validate_profile(row["profile"])
                if type(row["regular"]) is not bool:
                    raise ValueError("栏目须明确是否适合定时")
                direct = (
                    Media.objects.get(name=row["media"]) if row.get("media") else None
                )
                covered = list(Media.objects.filter(name__in=row.get("covered", [])))
                if len(covered) != len(set(row.get("covered", []))):
                    raise ValueError(f"{name} 的覆盖机构不完整")
                matches = list(Source.objects.select_for_update().filter(url=url))
                if len(matches) > 1:
                    raise ValueError("数据库已有重复的栏目地址")
                source = matches[0] if matches else None
                if source and (
                    source.name != name
                    or source.profile != profile
                    or source.media_id != (direct.pk if direct else None)
                    or source.adapter != "html"
                    or set(source.coverage_media.values_list("pk", flat=True))
                    != {m.pk for m in covered}
                ):
                    raise ValueError(f"已有栏目配置与清单不一致，请先核对 {name}")
                if not source and Source.objects.filter(name=name).exists():
                    raise ValueError(f"{name} 已用于其他地址")
                if not source and apply:
                    source = Source.objects.create(
                        url=url,
                        name=name,
                        profile=profile,
                        media=direct,
                        adapter="html",
                        enabled=True,
                        scheduled=False,
                        trusted=False,
                    )
                    source.coverage_media.set(covered)
                    Change.objects.create(
                        actor="栏目接入",
                        action="登记核验栏目",
                        detail={"source": source.pk, "url": url},
                    )
                if source and trial_key:
                    if not source.enabled:
                        raise ValueError(f"{name} 已由维护人员停用")
                    self.stdout.write(
                        f"{name} 任务 {enqueue('栏目实测', 'section:' + trial_key, [source.pk])}"
                    )
                elif schedule and row["regular"] and not source.scheduled:
                    jobs = source.run_set.filter(
                        state="done", source_signature=source_signature(source)
                    )
                    if (
                        not source.enabled
                        or not any(
                            j.get("命中", 0) > 0
                            for j in jobs.values_list("counts", flat=True)
                        )
                        or not Article.objects.filter(source=source).exists()
                    ):
                        raise ValueError(
                            f"{name} 的当前配置尚无完成且产出原文的实测结果"
                        )
                    source.scheduled = True
                    source.save(update_fields=["scheduled"])
                    Change.objects.create(
                        actor="栏目接入",
                        action="纳入定时",
                        detail={"source": source.pk, "url": url},
                    )
        except (KeyError, ValueError, TypeError, OSError, Media.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{len(rows)} 个栏目{'已处理' if apply else '预检通过'}")
