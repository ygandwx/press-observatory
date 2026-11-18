from datetime import timedelta
from difflib import SequenceMatcher
import json
import os
import re
import uuid
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from django.db import connection, transaction
from django.db.models import Q, F
from django.utils import timezone
from .catalog import EVENTS, IMPORTANCE
from .domain import record
from .external import analyze, document, fetch, normalize_url, ExternalError
from .models import Source, Run, Rule, Policy, Event, Change

ADAPTERS = ["html", "rss", "公众号", "微博", "头条号", "百家号", "短视频", "人工"]
COUNTERS = ["发现", "命中", "AI 分析", "自动入库", "待审", "过滤", "重复"]


@transaction.atomic
def enqueue(trigger, key, ids=None):
    sources = Source.objects.filter(enabled=True)
    if ids is not None:
        sources = sources.filter(pk__in=ids)
    return [
        Run.objects.get_or_create(
            key=f"{key}:{s.pk}", defaults={"source": s, "trigger": trigger}
        )[0].id
        for s in sources
    ]


def schedule(now=None):
    local = timezone.localtime(now or timezone.now())
    slots = [h for h in (8, 18) if h <= local.hour]
    if slots:
        hour = slots[-1]
        return enqueue("定时采集", f"schedule:{local.date()}:{hour}")
    return enqueue("定时采集", f"schedule:{local.date() - timedelta(days=1)}:18")


def adapter_items(source):
    if source.adapter not in ("html", "rss"):
        settings = json.loads(os.getenv("CHANNEL_FEEDS", "{}"))
        endpoint = settings.get(str(source.pk))
        if not endpoint:
            raise ManualCheck("该渠道需授权数据源或人工巡检。")
        _, data = fetch(endpoint)
        rows = json.loads(data)
        if not isinstance(rows, list):
            raise ExternalError("授权数据源需返回文章数组。")
        return [
            (r["title"], normalize_url(r["url"]))
            for r in rows[:30]
            if isinstance(r, dict) and r.get("title") and r.get("url")
        ]
    landing, raw = fetch(source.url)
    if source.adapter == "rss":
        root = ElementTree.fromstring(raw)
        return [
            (node.findtext("title", ""), urljoin(landing, node.findtext("link", "")))
            for node in root.findall(".//item")[:30]
        ]
    soup = BeautifulSoup(raw, "html.parser")
    if soup.h1 and (
        soup.find("article")
        or re.search(r"(content_|/\d{4}[-/]|\.s?html?$)", urlsplit(landing).path)
    ):
        return [(soup.h1.get_text(" ", strip=True), landing)]
    found = {}
    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        url = urljoin(landing, a.get("href", ""))
        if (
            8 <= len(title) <= 500
            and urlsplit(url).scheme in ("http", "https")
            and urlsplit(url).hostname == urlsplit(landing).hostname
        ):
            found.setdefault(url, title)
    return [(t, u) for u, t in found.items()][:30]


class ManualCheck(ValueError):
    pass


def inference(title, body):
    result = analyze(
        "判断是否媒体自身动态。返回 title, summary, category, subtype, importance, occurred (YYYY-MM-DD 或空字符串), relevant (布尔), score (0–100 整数), reasoning。分类与子项见材料。不得编造。",
        {
            "title": title,
            "text": body[:7500],
            "categories": EVENTS,
            "importance": IMPORTANCE,
        },
    )
    if (
        result.get("category") not in EVENTS
        or type(result.get("score")) is not int
        or not 0 <= result["score"] <= 100
        or type(result.get("relevant")) is not bool
        or not isinstance(result.get("summary"), str)
    ):
        raise ExternalError("模型返回无效分类、摘要或评分。")
    if result.get("importance") not in IMPORTANCE:
        result["importance"] = "常规"
    if result.get("subtype") not in EVENTS[result["category"]]:
        result["subtype"] = ""
    return result


def ingest(source, title, url, rules, counts, run, lease):
    hits = [r for r in rules if r.word in title]
    kinds = {r.kind for r in hits}
    if "排除词" in kinds or not {"主体词", "动作词"} <= kinds:
        counts["过滤"] += 1
        return
    counts["命中"] += 1
    canonical, headline, body = document(url)
    payload = {
        "title": headline or title,
        "url": canonical,
        "category": next((r.category for r in hits if r.category in EVENTS), "其他类"),
    }
    analysis = {}
    try:
        analysis = inference(payload["title"], body)
        counts["AI 分析"] += 1
        payload.update(
            {
                k: v
                for k, v in analysis.items()
                if k in ["summary", "category", "subtype", "importance", "occurred"]
            }
        )
    except ExternalError as exc:
        payload["summary"] = ""
        analysis = {"reasoning": str(exc)}
    policy, _ = Policy.objects.get_or_create(pk=1)
    with transaction.atomic():
        if (
            not Run.objects.select_for_update()
            .filter(id=run.id, lease=lease, state="running")
            .exists()
        ):
            raise ConflictLease()
        e, new = record(payload, "采集工作进程", source, verified=True)
        if not new:
            counts["重复"] += 1
            return
        Rule.objects.filter(id__in=[r.id for r in hits]).update(hits=F("hits") + 1)
        e.score = analysis.get("score")
        e.reasoning = str(analysis.get("reasoning", ""))[:3000]
        candidates = (
            Event.objects.filter(merged_into=None)
            .exclude(pk=e.pk)
            .order_by("-created")
            .values_list("title", flat=True)
            .iterator(chunk_size=500)
        )
        similar = any(
            SequenceMatcher(None, e.title, t).ratio() >= 0.82 for t in candidates
        )
        key = "待审"
        if analysis.get("relevant") is False or (
            e.score is not None and e.score < policy.low
        ):
            e.state = "filtered"
            key = "过滤"
        elif (
            policy.enabled
            and source.trusted
            and source.media_id is not None
            and e.score is not None
            and e.score >= policy.threshold
            and not similar
        ):
            e.state = "approved"
            e.approved = timezone.now()
            e.auto = True
            key = "自动入库"
        elif similar:
            e.reasoning += "；疑似重复，需人工归并。"
        e.save()
        counts[key] += 1
        Change.objects.create(
            event=e,
            actor="采集工作进程",
            action=e.state,
            detail={
                "score": e.score,
                "threshold": policy.threshold,
                "model": os.getenv("MODEL_NAME", ""),
                "run": run.pk,
            },
        )


class ConflictLease(Exception):
    pass


def work_once():
    now = timezone.now()
    with transaction.atomic():
        qs = Run.objects.filter(
            Q(state="queued") | Q(state="running", expires__lte=now)
        ).order_by("created")
        run = qs.select_for_update(
            skip_locked=connection.features.has_select_for_update_skip_locked
        ).first()
        if run is None:
            return False
        lease = uuid.uuid4().hex
        run.lease = lease
        run.state = "running"
        run.started = now
        run.expires = now + timedelta(minutes=10)
        run.attempts += 1
        run.save()
    counts = {k: 0 for k in COUNTERS}
    try:
        items = adapter_items(run.source)
        counts["发现"] = len(items)
        rules = list(Rule.objects.filter(enabled=True))
        failures = []
        for i, (title, url) in enumerate(items, 1):
            try:
                ingest(run.source, title, url, rules, counts, run, lease)
            except (ValueError, ExternalError) as exc:
                failures.append(str(exc)[:160])
            if not Run.objects.filter(pk=run.pk, lease=lease).update(
                progress=i * 100 // len(items),
                counts=counts,
                expires=timezone.now() + timedelta(minutes=10),
            ):
                raise ConflictLease()
        state = "failed" if failures else "done"
        message = "；".join(failures[:3]) or "采集完成"
    except ManualCheck as exc:
        state, message = "manual", str(exc)
    except ConflictLease:
        return True
    except Exception as exc:
        state, message = "failed", str(exc)[:220]
    Run.objects.filter(pk=run.pk, lease=lease).update(
        state=state,
        message=message,
        counts=counts,
        progress=100,
        finished=timezone.now(),
    )
    return True
