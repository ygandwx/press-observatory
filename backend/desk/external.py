"""Read public HTTP documents through a pinned public address, including redirects."""

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


def fetch(raw, redirects=3):
    url = normalize_url(raw)
    try:
        u = urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        addresses = socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise ExternalError("来源地址不可公开访问。")
        sock = socket.create_connection((addresses[0][4][0], port), timeout=6)
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
            if 300 <= res.status < 400 and redirects and res.getheader("Location"):
                return fetch(urljoin(url, res.getheader("Location")), redirects - 1)
            if not 200 <= res.status < 300:
                raise ExternalError(f"来源返回 HTTP {res.status}")
            data = res.read(2_000_001)
            if len(data) > 2_000_000:
                raise ExternalError("来源文档超过 2 MB。")
            if data[:2] == bytes.fromhex("1f8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                    data = compressed.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ExternalError("解压后的正文过大。")
            return url, data
        finally:
            conn.close()
            sock.close()
    except (OSError, http.client.HTTPException) as exc:
        raise ExternalError("无法读取来源：" + type(exc).__name__) from exc


def document(url):
    canonical, raw = fetch(url)
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.find("h1") or soup.title
    body = soup.find("article") or soup.find("main") or soup.body or soup
    for tag in body.select("script,style,nav,header,footer"):
        tag.decompose()
    headline = title.get_text(" ", strip=True) if title else ""
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
