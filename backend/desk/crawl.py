"""Per-request policy for the public HTML/RSS worker, shared by all workers on a host."""

import hashlib
import math
import re
import time
from contextlib import contextmanager
from datetime import timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone
from protego import Protego
from soupsieve import compile as compile_selector
from soupsieve.util import SelectorSyntaxError

from .external import ExternalError, active_crawler, request_once
from .models import CrawlHost

AGENT = "PressObservatory"
DEFAULT_DELAY = 2
MAX_WAIT = 180


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) - {
        "selector",
        "headline_selector",
        "body_selector",
        "date_selector",
        "cadence",
        "prefix",
        "interval",
        "limit",
        "title_terms",
        "publisher",
    }:
        raise ValueError("栏目采集配置无效。")
    for key in ("selector", "headline_selector", "body_selector", "date_selector"):
        if key not in profile:
            continue
        value = profile[key]
        if not isinstance(value, str) or not value or len(value) > 250:
            raise ValueError("请输入有效的栏目或正文选择器。")
        try:
            compile_selector(value)
        except SelectorSyntaxError as exc:
            raise ValueError("栏目或正文选择器无效。") from exc
    for key, minimum, maximum, default in (
        ("interval", 2, 3600, 2),
        ("limit", 1, 30, 30),
    ):
        value = profile.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("采集间隔或每批上限无效。")
    if not isinstance(profile.get("prefix", "/"), str) or not profile.get(
        "prefix", "/"
    ).startswith("/"):
        raise ValueError("文章路径前缀应从 / 开始。")
    terms = profile.get("title_terms", [])
    if not isinstance(terms, list) or not all(
        isinstance(t, str) and 0 < len(t) <= 40 for t in terms
    ):
        raise ValueError("栏目标题词无效。")
    if (
        not isinstance(profile.get("publisher", ""), str)
        or len(profile.get("publisher", "")) > 160
    ):
        raise ValueError("栏目运营主体无效。")
    if profile.get("cadence", "twice_daily") not in ("twice_daily", "daily", "weekly"):
        raise ValueError("定时周期无效。")
    return profile


def parse_rules(status, body):
    if status in (404, 410):
        return Protego.parse("")
    if status != 200:
        raise ExternalError(f"robots 预检返回 HTTP {status}，暂停抓取")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ExternalError("robots 编码无法确认") from exc
    if len(body) > 512000 or "<html" in text.lower() or "<!doctype" in text.lower():
        raise ExternalError("robots 返回网页或规则超长，暂停抓取")
    lines = [line.partition("#")[0].strip() for line in text.splitlines()]
    names = []
    for line in filter(None, lines):
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep or not re.fullmatch("[a-z][a-z0-9_-]*", key):
            raise ExternalError("robots 内容不是可识别的规则")
        if key == "crawl-delay" and not re.fullmatch(
            r"[0-9]+(?:\.[0-9]+)?", value.strip()
        ):
            raise ExternalError("robots 间隔无法确认")
        if key == "request-rate" and not re.fullmatch(
            r"[1-9]\d*/[1-9]\d*(?:[smh])?", value.strip(), re.I
        ):
            raise ExternalError("robots 频率或访问时段需人工核验")
        if key == "visit-time":
            raise ExternalError("robots 限定访问时段，需人工核验")
        names.append(key)
    if names and "user-agent" not in names and set(names) != {"sitemap"}:
        raise ExternalError("robots 规则缺少适用对象")
    policy = Protego.parse(text)
    if "request-rate" in names and policy.request_rate(AGENT) is None:
        # A different crawler might be named. Fail closed when units are ambiguous.
        if any(re.search(r"(?i)^request-rate:\s*\d+/\d+[smh]", line) for line in lines):
            raise ExternalError("robots 频率单位需人工核验")
    return policy


def wait_slot(host, minimum):
    """Record the actual start time before the request; sleep outside the row lock."""
    while True:
        with transaction.atomic():
            row, _ = CrawlHost.objects.select_for_update().get_or_create(host=host)
            now = timezone.now()
            spacing = max(float(minimum), row.spacing)
            if not math.isfinite(spacing) or spacing > MAX_WAIT:
                raise ExternalError("站点要求长间隔，保留人工巡检")
            due = max(
                filter(
                    None,
                    [
                        now,
                        row.hold_until,
                        row.last_request + timedelta(seconds=spacing)
                        if row.last_request
                        else None,
                    ],
                )
            )
            wait = (due - now).total_seconds()
            if wait <= 0:
                row.last_request = now
                row.spacing = spacing
                row.save(update_fields=["last_request", "spacing"])
                return
        if wait > MAX_WAIT + 1:
            raise ExternalError("站点处于等待期，稍后再试")
        time.sleep(wait)


def retry_delay(value):
    try:
        if value and value.strip().isdigit():
            return min(int(value.strip()), 31_536_000)
        if value:
            return max(
                1,
                math.ceil(
                    (parsedate_to_datetime(value) - timezone.now()).total_seconds()
                ),
            )
    except (OverflowError, TypeError, ValueError):
        pass
    return 60


class Crawler:
    def __init__(self, source):
        self.host = urlsplit(source.url).hostname
        self.profile = validate_profile(source.profile)
        self.policies = {}
        self.audit = {}
        self.published = None

    def get(self, url, delay):
        host = urlsplit(url).hostname
        for attempt in range(2):
            wait_slot(host, delay)
            status, headers, body = request_once(url)
            if status not in (429, 502, 503, 504):
                return status, headers, body
            pause = retry_delay(headers.get("retry-after"))
            with transaction.atomic():
                row = CrawlHost.objects.select_for_update().get(host=host)
                until = timezone.now() + timedelta(seconds=pause)
                if not row.hold_until or row.hold_until < until:
                    row.hold_until = until
                    row.save(update_fields=["hold_until"])
            if attempt or pause > 15:
                raise ExternalError(f"来源 HTTP {status}，依退避时限暂停")
        raise ExternalError("来源暂不可用")

    def request(self, url):
        parts = urlsplit(url)
        if parts.hostname != self.host:
            raise ExternalError("跨站链接需另行核验，已停止")
        origin = f"{parts.scheme}://{parts.netloc}"
        minimum = self.profile.get("interval", DEFAULT_DELAY)
        if origin not in self.policies:
            status, _, body = self.get(origin + "/robots.txt", minimum)
            self.audit[origin] = {
                "robots": origin + "/robots.txt",
                "status": status,
                "sha256": hashlib.sha256(body).hexdigest(),
                "minimum_seconds": minimum,
            }
            self.policies[origin] = parse_rules(status, body)
        policy = self.policies[origin]
        if not policy.can_fetch(url, AGENT):
            raise ExternalError("robots 禁止此路径")
        rate = policy.request_rate(AGENT)
        spacing = max(
            minimum,
            policy.crawl_delay(AGENT) or 0,
            rate.seconds / rate.requests if rate else 0,
        )
        self.audit[origin]["minimum_seconds"] = spacing
        return self.get(url, spacing)


@contextmanager
def guarded(source):
    token = active_crawler.set(Crawler(source))
    try:
        yield active_crawler.get()
    finally:
        active_crawler.reset(token)
