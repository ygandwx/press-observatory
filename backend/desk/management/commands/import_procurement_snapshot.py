"""Recheck previously retrieved CCGP pages without contacting either host."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from desk import procurement
from desk.collection import COUNTERS, source_signature
from desk.domain import record
from desk.external import ExternalError
from desk.models import Article, Change, Run, Source


def read_page(directory, entry):
    name = entry["file"]
    path = directory / name
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.html", name) or path.is_symlink():
        raise ValueError("留存页面必须是包内独立 HTML 文件。")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("留存页面过大。")
    raw = path.read_bytes()
    if not raw or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("留存页面摘要不符。")
    stamp = datetime.fromisoformat(entry["at"])
    if timezone.is_naive(stamp) or stamp > timezone.now():
        raise ValueError("留存时间无效。")
    origin = "https://" + urlsplit(entry["url"]).netloc
    rules = entry["robots"][origin]
    if (
        rules["robots"] != origin + "/robots.txt"
        or rules["status"] not in (200, 404, 410)
        or not re.fullmatch(r"[0-9a-f]{64}", rules["sha256"])
        or rules["minimum_seconds"] < 2
    ):
        raise ValueError("留存页面缺少原始抓取规则记录。")
    return raw


class Command(BaseCommand):
    help = "离线核验已留存的采购公告，以待审状态登记。默认预检。"

    def add_arguments(self, parser):
        parser.add_argument("index", type=Path)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, index, apply, **options):
        try:
            pages = json.loads(index.read_bytes())
            if not isinstance(pages, list) or not pages:
                raise ValueError("需要留存页面清单。")
            seen = set()
            new = repeated = prior = 0
            for page in pages:
                if page["url"] in seen:
                    raise ValueError("留存包重复列出同一查询。")
                seen.add(page["url"])
                source = Source.objects.select_for_update().get(url=page["url"])
                if source.adapter != "html" or not procurement.applies(source):
                    raise ValueError("采购清单与登记来源不符。")
                signature = source_signature(source)
                stamp = hashlib.sha256(
                    json.dumps([signature, page], sort_keys=True).encode()
                ).hexdigest()
                key = "snapshot:" + stamp
                raw = read_page(index.parent, page)
                # No network calls. Inspect the captured first page, including rows
                # beyond the online adapter's per-run article limit.
                local = Source(url=source.url, profile={**source.profile, "limit": 30})
                audit = {}
                _, matches = procurement.listing(raw, local, audit)
                listed = {url: title for title, url in matches}
                docs = page["articles"]
                if not docs or len({d["url"] for d in docs}) != len(docs):
                    raise ValueError("需提供互不重复的留存公告。")
                checked = []
                for doc in docs:
                    url = doc["url"]
                    if url not in listed or procurement.notice_url(url) != url:
                        raise ValueError("公告不在合格的留存查询结果内。")
                    html = read_page(index.parent, doc)
                    title, _, day = procurement.parse_notice(html, local, listed[url])
                    checked.append((url, title, day))
                if Run.objects.filter(key=key, state="done").exists():
                    prior += len(checked)
                    continue
                counts = dict.fromkeys(COUNTERS, 0)
                counts.update({"发现": len(docs), "命中": len(checked)})
                for url, title, day in checked:
                    if Article.objects.filter(url=url, events__isnull=False).exists():
                        counts["重复"] += 1
                        continue
                    event, created = record(
                        {"title": title, "url": url, "published": day},
                        "采购留存页面核验",
                        source,
                        verified=True,
                    )
                    if created and (event.state != "pending" or event.auto):
                        raise ValueError("留存公告仅可进入待审。")
                    counts["待审" if created else "重复"] += 1
                now = timezone.now()
                Run.objects.create(
                    key=key,
                    source=source,
                    trigger="留存页面核验",
                    state="done",
                    source_signature=signature,
                    started=now,
                    finished=now,
                    attempts=1,
                    progress=100,
                    counts=counts,
                    message="已离线复核清单和公告原文，各页原始采集时间见证据。",
                    crawl_evidence={"snapshot": page, "query": audit["query"]},
                )
                new += counts["待审"]
                repeated += counts["重复"]
            if new:
                Change.objects.create(
                    actor="采购留存页面核验",
                    action="留存公告待审",
                    detail={"new": new, "queries": len(seen)},
                )
            if not apply:
                transaction.set_rollback(True)
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            Source.DoesNotExist,
            ExternalError,
        ) as exc:
            raise CommandError(str(exc))
        self.stdout.write(
            f"{'已导入' if apply else '预检'} {len(seen)} 个查询，新增待审 {new}，"
            f"重复 {repeated}，相同凭据此前已核验 {prior} 篇"
        )
