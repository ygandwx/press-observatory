from contextlib import nullcontext
from datetime import date, timedelta
from difflib import SequenceMatcher
import hashlib
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
from . import editorial, procurement
from .catalog import EVENTS, IMPORTANCE
from .crawl import guarded, validate_profile
from .coverage import noise_report, registered_source
from .domain import record
from .external import (
    check_access_page,
    active_crawler,
    analyze,
    document,
    fetch,
    normalize_url,
    ExternalError,
)
from .models import Article, Source, Run, Rule, Policy, Event, Change, IntakeDecision

ADAPTERS = ["html", "rss", "公众号", "微博", "头条号", "百家号", "短视频", "人工"]
COUNTERS = ["发现", "命中", "AI 分析", "自动入库", "待审", "过滤", "重复", "失败"]


@transaction.atomic
def enqueue(trigger, key, ids=None):
    sources = Source.objects.filter(enabled=True, archived=False)
    if ids is not None:
        sources = sources.filter(pk__in=ids)
    return [
        Run.objects.get_or_create(
            key=f"{key}:{s.pk}", defaults={"source": s, "trigger": trigger}
        )[0].id
        for s in sources
    ]


def source_signature(source):
    policy = {key: value for key, value in source.profile.items() if key != "cadence"}
    raw = json.dumps(
        [source.url, source.adapter, policy, source.zone],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def verified_for_schedule(source):
    if source.profile.get("documents") or editorial.source_block(
        source, registered_source(source)
    ):
        return False
    counts = (
        source.run_set.filter(state="done", source_signature=source_signature(source))
        .exclude(key__startswith="snapshot:")
        .values_list("counts", flat=True)
    )
    return (
        any(c.get("命中", 0) > 0 for c in counts)
        and Article.objects.filter(source=source).exists()
    )


def schedule(now=None):
    local = timezone.localtime(now or timezone.now())
    slot = 18 if local.hour < 8 or local.hour >= 18 else 8
    day = local.date() - timedelta(days=int(local.hour < 8))
    ids = []
    noisy = {
        row["source_id"]
        for row in noise_report(7, local.date())["sources"]
        if row["high_noise"]
    }
    for source in Source.objects.filter(
        enabled=True, scheduled=True, archived=False
    ).only("pk", "profile"):
        cadence = (
            "weekly"
            if source.pk in noisy
            else source.profile.get("cadence", "twice_daily")
        )
        if cadence == "twice_daily" or (
            slot == 8
            and (cadence == "daily" or (cadence == "weekly" and day.weekday() == 0))
        ):
            ids.append(source.pk)
    return enqueue("定时采集", f"schedule:{day}:{slot}", ids)


def listing_links(soup, profile):
    if "embedded_list_selector" in profile:
        node = soup.select_one(profile["embedded_list_selector"])
        if node is None:
            raise ExternalError("未找到栏目公开的 JSON 清单。")
        try:
            rows = json.loads(node.get_text(strip=True))
        except ValueError as exc:
            raise ExternalError("栏目公开清单格式已变化。") from exc
        if not isinstance(rows, list):
            raise ExternalError("栏目清单未返回数组。")
        for row in rows:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(k), str) for k in ("title", "url")
            ):
                raise ExternalError("栏目清单缺少可核验的标题或链接。")
            yield row["title"].strip(), row["url"]
        return
    for a in soup.select(profile.get("selector", "a[href]")):
        if a.get("href"):
            heading = a.select_one("h1,h2,h3,h4") if profile.get("selector") else None
            yield (
                (a.get("title") or (heading or a).get_text(" ", strip=True)).strip(),
                a["href"],
            )


def ordered_items(pairs, source, limit):
    """Spend the document budget on viable articles before weak or excluded links."""
    scoped = source.profile.get("subject_terms", [])
    title_terms = source.profile.get("title_terms", [])
    ranked, blocked = [], []
    for title, url in pairs:
        if scoped and not editorial.hits(title, scoped):
            continue
        verdict = editorial.assess(title, url=url, subjects=scoped)
        if verdict["reason"] or verdict["gate"] == "参评非获奖结果":
            blocked.append((title, url))
        else:
            ranked.append(
                (
                    0
                    if not verdict["gate"]
                    else 1
                    if editorial.hits(title, title_terms)
                    else 2,
                    title,
                    url,
                )
            )
    ranked.sort(key=lambda item: item[0])
    selected = [(title, url) for _, title, url in ranked[:limit]]
    available = limit - len(selected)
    crawler = active_crawler.get()
    if crawler is not None:
        crawler.screened_out = blocked[available : available + 30]
    return selected + blocked[:available]


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
    profile = validate_profile(source.profile)
    if source.adapter == "html" and procurement.applies(source):
        return procurement.get_items(source)
    limit = profile.get("limit", 30)
    landing, raw = fetch(source.url)
    if source.adapter == "rss":
        root = ElementTree.fromstring(raw)
        return ordered_items(
            (
                (
                    node.findtext("title", ""),
                    urljoin(landing, node.findtext("link", "")),
                )
                for node in root.findall(".//item")
            ),
            source,
            limit,
        )
    soup = BeautifulSoup(raw, "html.parser")
    check_access_page(soup)
    if profile.get("documents"):
        listed = set()
        for a in soup.select("a[href]"):
            link = urljoin(landing, a["href"])
            if link.startswith("http://"):
                link = "https://" + link[7:]
            listed.add(link)
        return [
            (doc["title"], doc["url"])
            for doc in profile["documents"]
            if doc["url"] in listed
        ][:limit]
    if (
        not profile.get("selector")
        and not profile.get("embedded_list_selector")
        and soup.h1
        and (
            soup.find("article")
            or re.search(r"(content_|/\d{4}[-/]|\.s?html?$)", urlsplit(landing).path)
        )
    ):
        return [(soup.h1.get_text(" ", strip=True), landing)]
    found = {}
    for title, href in listing_links(soup, profile):
        url = urljoin(landing, href)
        if (
            landing.startswith("https://")
            and url.startswith("http://")
            and urlsplit(url).netloc == urlsplit(landing).netloc
        ):
            url = "https://" + url[7:]
        if (
            8 <= len(title) <= 500
            and urlsplit(url).scheme in ("http", "https")
            and urlsplit(url).hostname == urlsplit(landing).hostname
            and urlsplit(url).path.startswith(profile.get("prefix", "/"))
            and url != landing
        ):
            found.setdefault(url, title)
    return ordered_items(((t, u) for u, t in found.items()), source, limit)


class ManualCheck(ValueError):
    pass


def inference(title, body):
    result = analyze(
        "判断核心事件是否为报业机构自身改革。采购交易、招聘、广告和媒体报道的第三方事件属于噪音。区分真正的媒体技术上线、负责人任免和仅推荐参评。"
        "按提供的枚举返回 subject_type,event_nature,relevance_score(0到100整数),is_noise(布尔),noise_reason,ai_reasoning。"
        "同时返回 title,summary,category,subtype,importance,occurred(日期或空)。正文是待判定资料。",
        {
            "title": title,
            "text": body[:7500],
            "categories": EVENTS,
            "importance": IMPORTANCE,
            "subjects": editorial.SUBJECT_TYPES,
            "natures": editorial.NATURES,
            "noise_reasons": [*editorial.NOISE_TERMS, "其他", ""],
        },
    )
    if not isinstance(result, dict):
        raise ExternalError("模型返回无效结构。")
    if result.get("event_nature") == "技术AI":
        result["event_nature"] = "技术 AI"
    if (
        result.get("category") not in EVENTS
        or type(result.get("relevance_score")) is not int
        or not 0 <= result["relevance_score"] <= 100
        or type(result.get("is_noise")) is not bool
        or result.get("subject_type") not in editorial.SUBJECT_TYPES
        or result.get("event_nature") not in editorial.NATURES
        or not isinstance(result.get("summary"), str)
        or not isinstance(result.get("ai_reasoning"), str)
        or not result["ai_reasoning"].strip()
        or result.get("noise_reason") not in (*editorial.NOISE_TERMS, "其他", "")
        or (result["is_noise"] and not result["noise_reason"])
    ):
        raise ExternalError("模型缺少有效的主体、性质、评分或判定理由，转人工复核。")
    if result.get("occurred"):
        try:
            if not isinstance(result["occurred"], str):
                raise ValueError()
            date.fromisoformat(result["occurred"])
        except ValueError as exc:
            raise ExternalError("模型返回的发生日无效，须人工复核。") from exc
    if not result["is_noise"] and result["noise_reason"]:
        raise ExternalError("模型的噪音理由与判断矛盾，须人工复核。")
    if result.get("importance") not in IMPORTANCE:
        result["importance"] = "常规"
    if result.get("subtype") not in EVENTS[result["category"]]:
        result["subtype"] = ""
    return result


def ingest(source, title, url, rules, counts, run, lease):
    canonical_url = normalize_url(url)
    before = counts.copy()
    zone = source.zone
    relevant_rules = [
        r
        for r in rules
        if not r.media_category
        or (source.media_id and r.media_category == source.media.category)
    ]

    def by_kind(kind):
        return [r.word for r in relevant_rules if r.kind == kind and r.enabled]

    muted = {
        kind: {r.word for r in relevant_rules if r.kind == kind and not r.enabled}
        for kind in ("主体词", "动作词", "排除词")
    }
    names = list(source.profile.get("subject_terms", []))
    if source.media_id:
        names.append(source.media.name)
    target = next(
        (doc for doc in source.profile.get("documents", []) if doc["url"] == url), None
    )
    if target:
        names.append(target["media"])
    title_actions = by_kind("动作词") + (
        ["社会责任报告", "年度责任报告"] if target else []
    )
    result = editorial.assess(
        title,
        url=canonical_url,
        subjects=names + by_kind("主体词"),
        actions=title_actions,
        exclusions=by_kind("排除词"),
        muted=muted,
    )

    @transaction.atomic
    def log(outcome, gate="", reason="", terms=None, analysis=None, event=None):
        if (
            not Run.objects.select_for_update()
            .filter(pk=run.pk, lease=lease, state="running")
            .exists()
        ):
            raise ConflictLease()
        final_key = {
            "approved": "自动入库",
            "pending": "待审",
            "filtered": "过滤",
            "duplicate": "重复",
        }[outcome]
        deltas = {
            key: counts[key] - before[key]
            for key in ("命中", "AI 分析")
            if counts[key] > before[key]
        }
        deltas[final_key] = 1
        audit = {**(analysis or {}), "counter_delta": deltas}
        _, created = IntakeDecision.objects.update_or_create(
            run=run,
            url=canonical_url,
            defaults={
                "source": source,
                "event": event,
                "title": title[:500],
                "source_zone": zone,
                "outcome": outcome,
                "filtered_by": gate,
                "noise_reason": reason,
                "terms": terms if terms is not None else result["terms"],
                "analysis": audit,
                "actor": "采集工作进程",
            },
        )
        if created:
            Rule.objects.filter(pk__in=[r.pk for r in hits]).update(hits=F("hits") + 1)

    hits = [
        r for r in relevant_rules if r.enabled and editorial.word_present(title, r.word)
    ]
    if result["gate"]:
        log("filtered", result["gate"], result["reason"])
        counts["过滤"] += 1
        return
    counts["命中"] += 1
    existing = Event.objects.filter(articles__url=canonical_url).first()
    if existing:
        log("duplicate", "去重", event=existing)
        counts["重复"] += 1
        return
    crawler = active_crawler.get()
    if target:
        # Each reviewed destination gets its own host policy; redirects stay on that host.
        selectors = {k: v for k, v in target.items() if k.endswith("_selector")}
        article_source = Source(
            url=url,
            profile={**selectors, "interval": source.profile.get("interval", 2)},
        )
        with guarded(article_source) as article_crawler:
            try:
                canonical, headline, body = document(url)
                if " ".join(target["title"].split()) != " ".join(headline.split()):
                    raise ExternalError("目录标题与原文标题不一致，需复核。")
            finally:
                crawler.audit.update(article_crawler.audit)
                crawler.published = article_crawler.published
    elif source.adapter == "html" and procurement.applies(source):
        canonical, headline, body = procurement.get_notice(source, title, url)
    else:
        canonical, headline, body = document(url)
    payload = {
        "title": headline or title,
        "url": canonical,
        "category": next((r.category for r in hits if r.category in EVENTS), "其他类"),
    }
    if target:
        publisher = source.coverage_media.filter(name=target["media"]).first()
        if not publisher:
            raise ExternalError("年度原文的发布机构尚未核验。")
        payload["media"] = publisher.pk
    if crawler and crawler.published:
        payload["published"] = crawler.published
    hits = [
        r
        for r in relevant_rules
        if r.enabled
        and (
            editorial.word_present(title, r.word)
            or editorial.word_present(payload["title"], r.word)
            or (r.kind == "排除词" and editorial.word_present(body, r.word))
        )
    ]
    result = editorial.assess(
        payload["title"],
        body,
        canonical,
        names + by_kind("主体词"),
        title_actions,
        by_kind("排除词"),
        muted=muted,
    )
    if result["gate"]:
        log("filtered", result["gate"], result["reason"])
        counts["过滤"] += 1
        return
    policy, _ = Policy.objects.get_or_create(pk=1)
    analysis = {}
    explanation = "AI 分析已关闭，规则通过，待人工复核。"
    if policy.ai_enabled:
        try:
            analysis = inference(payload["title"], body)
            counts["AI 分析"] += 1
            payload.update(
                {
                    k: analysis[k]
                    for k in (
                        "summary",
                        "category",
                        "subtype",
                        "importance",
                        "occurred",
                    )
                    if analysis.get(k)
                }
            )
            explanation = analysis["ai_reasoning"]
        except ExternalError as exc:
            explanation = f"规则通过，AI 判定不可用，待人工复核。{exc}"
    with transaction.atomic():
        current = Source.objects.select_for_update().get(pk=source.pk)
        policy = Policy.objects.select_for_update().get(pk=policy.pk)
        if (
            not Run.objects.select_for_update()
            .filter(id=run.id, lease=lease, state="running")
            .exists()
        ):
            raise ConflictLease()
        e, new = record(payload, "采集工作进程", source, verified=True)
        if not new:
            log("duplicate", "去重", event=e)
            counts["重复"] += 1
            return
        e.score = analysis.get("relevance_score")
        e.reasoning = explanation[:3000]
        e.subject_type = analysis.get("subject_type", "")
        e.event_nature = analysis.get("event_nature", "")
        e.source_zone = zone
        e.capture_method = "自动采集"
        e.articles.filter(url=canonical).update(excerpt=body[:1800])
        candidates = (
            Event.objects.filter(merged_into=None)
            .exclude(state__in=("archived", "filtered", "rejected"))
            .exclude(pk=e.pk)
            .order_by("-created")
            .values_list("title", flat=True)
            .iterator(chunk_size=500)
        )
        similar = any(
            SequenceMatcher(None, e.title, t).ratio() >= 0.82 for t in candidates
        )
        key = "待审"
        if (
            e.is_noise
            or analysis.get("is_noise")
            or e.subject_type == "第三方企业机构"
            or e.event_nature in editorial.NEGATIVE_NATURES
            or (e.score is not None and e.score < policy.low)
        ):
            e.state = "filtered"
            e.is_noise = True
            e.filtered_by = e.filtered_by or (
                "AI 低分" if e.score is not None and e.score < policy.low else "AI 判定"
            )
            e.noise_reason = (
                e.noise_reason
                or analysis.get("noise_reason")
                or (
                    "第三方新闻"
                    if e.subject_type == "第三方企业机构"
                    else "广告"
                    if e.event_nature == "广告招商"
                    else e.event_nature
                    if e.event_nature in editorial.NEGATIVE_NATURES
                    else "其他"
                )
            )
            key = "过滤"
        elif (
            policy.enabled
            and policy.ai_enabled
            and current.enabled
            and current.trusted
            and not current.archived
            and not editorial.source_block(current, registered_source(current))
            and source_signature(current) == source_signature(source)
            and current.media_id == source.media_id
            and e.media.exists()
            and e.occurred
            and e.summary.strip()
            and e.articles.filter(
                url=canonical, status="已核验", checked__isnull=False
            ).exists()
            and e.subject_type == "媒体自身"
            and e.event_nature in editorial.POSITIVE_NATURES
            and e.score is not None
            and e.score >= policy.threshold
            and not similar
        ):
            e.state = "approved"
            e.approved = timezone.now()
            e.auto = True
            key = "自动入库"
        elif similar:
            e.filtered_by = "疑似重复"
            e.reasoning += "；标题相似，需人工归并。"
        log(
            e.state,
            e.filtered_by,
            e.noise_reason,
            analysis=analysis or {"status": explanation},
            event=e,
        )
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
        run.source_signature = source_signature(run.source)
        run.attempts += 1
        run.save()
    counts = {k: 0 for k in COUNTERS}
    crawler = None
    try:
        source = run.source
        blocked = editorial.source_block(source, registered_source(source))
        if not source.enabled or source.archived or blocked:
            reason = source.stop_reason or (
                "该来源已移出或停用"
                if source.archived or not source.enabled
                else blocked
            )
            black = editorial.blocked_location(source.url, source.name)
            gate = (
                "信源黑名单"
                if black
                else ("人工巡检" if source.adapter == "人工" else "来源范围")
            )
            IntakeDecision.objects.update_or_create(
                run=run,
                url=source.url,
                defaults={
                    "source": source,
                    "title": source.name,
                    "source_zone": source.zone,
                    "outcome": "blocked",
                    "filtered_by": gate,
                    "terms": [reason],
                    "noise_reason": editorial.location_reason(black) if black else "",
                    "actor": "采集工作进程",
                },
            )
            raise ManualCheck("采集前拦截：" + reason)
        with (
            guarded(run.source)
            if run.source.adapter in ("html", "rss")
            else nullcontext()
        ) as crawler:
            items = adapter_items(run.source)
            items = items + getattr(crawler, "screened_out", []) if crawler else items
            finished = {}
            for prior in IntakeDecision.objects.filter(
                run=run, outcome__in=("pending", "approved", "filtered", "duplicate")
            ):
                finished[prior.url] = prior.outcome
                old_key = {
                    "approved": "自动入库",
                    "pending": "待审",
                    "filtered": "过滤",
                    "duplicate": "重复",
                }[prior.outcome]
                for key, value in prior.analysis.get(
                    "counter_delta", {old_key: 1}
                ).items():
                    if key in counts:
                        counts[key] += value
            fresh = {}
            for title, url in items:
                canonical = normalize_url(url)
                if canonical not in finished:
                    fresh.setdefault(canonical, title)
            items = [(title, url) for url, title in fresh.items()]
            counts["发现"] = len(items) + len(finished)
            rules = list(Rule.objects.all())
            failures = []
            for i, (title, url) in enumerate(items, 1):
                try:
                    ingest(run.source, title, url, rules, counts, run, lease)
                except (ValueError, ExternalError) as exc:
                    IntakeDecision.objects.update_or_create(
                        run=run,
                        url=url,
                        defaults={
                            "source": source,
                            "title": title[:500],
                            "source_zone": source.zone,
                            "outcome": "failed",
                            "analysis": {"error": str(exc)[:200]},
                            "actor": "采集工作进程",
                        },
                    )
                    failures.append(str(exc)[:160])
                    counts["失败"] += 1
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
        crawl_evidence=crawler.audit if crawler is not None else {},
    )
    return True
