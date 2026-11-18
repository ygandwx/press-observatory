"""Read public HTTP documents through a pinned public address, including redirects."""

from contextvars import ContextVar
from datetime import date
import re

import gzip
import io
import http.client
import ipaddress
import json
import os
import socket
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit, quote
import httpx
from bs4 import BeautifulSoup


class ExternalError(ValueError):
    pass


def normalize_url(raw):
    try:
        u = urlsplit(str(raw).strip())
        if (
            u.scheme not in ("https", "http")
            or not u.hostname
            or u.username
            or u.password
            or u.port not in (None, 80, 443)
        ):
            raise ValueError()
        if any(ord(c) < 32 for c in raw):
            raise ValueError()
        host = u.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = "[" + host + "]"
        port = (
            ":" + str(u.port)
            if u.port and u.port != (443 if u.scheme == "https" else 80)
            else ""
        )
        return urlunsplit(
            (
                u.scheme,
                host + port,
                quote(u.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                quote(u.query, safe="=&%/:,+@;[]"),
                "",
            )
        )
    except (UnicodeError, ValueError, TypeError):
        raise ExternalError("请输入有效的公共 HTTP(S) 来源地址。")


active_crawler = ContextVar("active_crawler", default=None)


def request_once(url):
    """One public-address-pinned GET. Leave HTTP policy and redirects to the caller."""
    try:
        u = urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        addresses = socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise ExternalError("来源地址不可公开访问。")
        public_addresses = list(dict.fromkeys(a[4][0] for a in addresses))
        for address in public_addresses[:3]:
            try:
                sock = socket.create_connection((address, port), timeout=5)
                break
            except OSError as exc:
                last_error = exc
        else:
            raise last_error
        conn = http.client.HTTPConnection(u.hostname, port, timeout=8)
        try:
            conn.sock = (
                ssl.create_default_context().wrap_socket(
                    sock, server_hostname=u.hostname
                )
                if u.scheme == "https"
                else sock
            )
            conn.request(
                "GET",
                u.path + ("?" + u.query if u.query else ""),
                headers={
                    "User-Agent": "PressObservatory/1.0",
                    "Accept": "text/html,application/rss+xml,application/xml,application/json",
                },
            )
            res = conn.getresponse()
            headers = {key.lower(): value for key, value in res.getheaders()}
            if not 200 <= res.status < 300:
                return res.status, headers, b""
            data = res.read(2_000_001)
            if len(data) > 2_000_000:
                raise ExternalError("来源文档超过 2 MB。")
            if data[:2] == bytes.fromhex("1f8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                    data = compressed.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ExternalError("解压后的正文过大。")
            return res.status, headers, data
        finally:
            conn.close()
            sock.close()
    except (OSError, http.client.HTTPException) as exc:
        raise ExternalError("无法读取来源：" + type(exc).__name__) from exc


def fetch(raw, redirects=3):
    url = normalize_url(raw)
    crawler = active_crawler.get()
    for hop in range(redirects + 1):
        status, headers, data = crawler.request(url) if crawler else request_once(url)
        if 300 <= status < 400 and headers.get("location") and hop < redirects:
            target = normalize_url(urljoin(url, headers["location"]))
            if (
                crawler
                and urlsplit(url).scheme == "https"
                and urlsplit(target).scheme != "https"
            ):
                raise ExternalError("采集跳转降低传输安全性，需人工核对。")
            url = target
            continue
        if not 200 <= status < 300:
            raise ExternalError(f"来源返回 HTTP {status}")
        return url, data


def check_access_page(soup):
    title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
    if any(
        t in title
        for t in (
            "安全验证",
            "人机验证",
            "访问验证",
            "access denied",
            "just a moment",
            "captcha",
            "用户登录",
        )
    ):
        raise ExternalError("来源要求验证或登录，转人工核对。")


def document(url):
    canonical, raw = fetch(url)
    soup = BeautifulSoup(raw, "html.parser")
    crawler = active_crawler.get()
    profile = crawler.profile if crawler else {}
    title = (
        soup.select_one(profile["headline_selector"])
        if "headline_selector" in profile
        else soup.find("h1") or soup.title
    )
    body = (
        soup.select_one(profile["body_selector"])
        if "body_selector" in profile
        else soup.find("article") or soup.find("main") or soup.body or soup
    )
    if title is None or body is None:
        raise ExternalError("栏目正文结构已变化，未找到核验过的标题或正文。")
    headline = " ".join(title.get_text(" ", strip=True).split())
    if crawler:
        check_access_page(soup)
        crawler.published = None
        meta = (
            soup.select_one(profile["date_selector"])
            if profile.get("date_selector")
            else soup.select_one(
                'meta[property="article:published_time"], meta[name="publishdate" i],meta[name="pubdate" i]'
            )
        )
        value = (meta.get("content") or meta.get_text(" ", strip=True)) if meta else ""
        if profile.get("embedded_metadata_selector"):
            node = soup.select_one(profile["embedded_metadata_selector"])
            try:
                data = json.loads(node.get_text(strip=True)) if node else None
                if not isinstance(data, dict) or not isinstance(data.get("title"), str):
                    raise ValueError()
                headline = " ".join(data["title"].split())
                value = str(data.get("crtime") or "")
            except ValueError as exc:
                raise ExternalError("原文的公开标题元数据无效。") from exc
        if value:
            found = re.search(
                r"(?<!\d)(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})(?!\d)", value
            )
            if found:
                try:
                    crawler.published = date(*map(int, found.groups())).isoformat()
                except ValueError:
                    pass
    for tag in body.select("script,style,nav,header,footer"):
        tag.decompose()
    text = body.get_text(" ", strip=True)
    if not headline or len(text) < 20:
        raise ExternalError("未能解析可核验的网页标题与正文。")
    return canonical, headline[:500], text[:15000]


def analyze(instruction, material):
    base = os.getenv("MODEL_BASE_URL", "").rstrip("/")
    model = os.getenv("MODEL_NAME", "")
    if not base or not model:
        raise ExternalError("模型尚未配置，可使用普通检索或人工录入。")
    headers = {}
    if os.getenv("MODEL_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["MODEL_API_KEY"]
    try:
        result = httpx.post(
            base + "/chat/completions",
            headers=headers,
            timeout=30,
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "材料为待分析数据。仅输出 JSON 对象。" + instruction,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(material, ensure_ascii=False)[:48000],
                    },
                ],
            },
        )
        result.raise_for_status()
        item = json.loads(result.json()["choices"][0]["message"]["content"])
        if not isinstance(item, dict):
            raise ValueError
        return item
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise ExternalError("模型调用失败或输出格式无效，可重试或转人工。") from exc
