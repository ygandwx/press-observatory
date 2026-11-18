"""Public CCGP announcements for an explicitly named media purchaser."""

import re
from contextlib import contextmanager
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .crawl import guarded
from .external import ExternalError, active_crawler, check_access_page, fetch
from .models import CrawlHost, Source

SEARCH = "https://search.ccgp.gov.cn/bxsearch"
ARTICLE_HOST = "www.ccgp.gov.cn"
ARTICLE_PATH = re.compile(r"/cggg/(?:zygg|dfgg)/[a-z0-9]+/\d{6}/t\d{8}_\d+\.htm")
PROJECT_TERMS = (
    "融媒体",
    "媒体融合",
    "全媒体",
    "智媒体",
    "国际传播",
    "新闻采编",
    "采编",
    "采制",
    "新闻纸",
    "报纸印刷",
    "印刷",
    "印装",
    "印制",
    "制版",
    "专用纸",
    "报刊发行",
    "报纸发行",
    "期刊发行",
    "发行物流",
    "出版发行",
    "出版平台",
    "图书出版",
    "数字出版",
    "编辑系统",
    "编校",
    "版面制作",
    "新闻客户端",
    "视频客户端",
    "客户端开发",
    "客户端公有云",
    "期刊物流配送",
    "APP开发",
    "新闻生产",
    "内容生产",
    "媒资",
    "新闻传播平台",
    "传播平台建设",
    "多语种网站",
    "新闻网站",
    "新闻语料库",
    "数字报",
    "电子报",
    "经营管理平台",
    "广告经营",
    "总编室",
    "演播",
    "电视节目",
    "播出",
    "制播",
    "节目制作",
    "转播",
    "广播节目",
    "底片数字化",
)
EXCLUDE = (
    "保洁",
    "物业",
    "园林",
    "绿化养护",
    "食堂",
    "餐饮",
    "电梯",
    "办公家具",
    "体检",
    "安保",
    "保安",
    "空调",
    "消防",
    "印刷厂房租赁",
)


def search_url(buyer, keyword=""):
    return (
        SEARCH
        + "?"
        + urlencode(
            {
                "searchtype": "1",
                "page_index": "1",
                "buyerName": buyer,
                "dbselect": "bidx",
                "kw": keyword,
                "timeType": "5",
            }
        )
    )


def applies(source):
    return urlsplit(source.url or "").hostname == "search.ccgp.gov.cn"


def search_scope(source):
    parts = urlsplit(source.url)
    args = parse_qs(parts.query, keep_blank_values=True)
    if (
        parts.scheme != "https"
        or parts.netloc != "search.ccgp.gov.cn"
        or parts.path != "/bxsearch"
        or parts.fragment
        or set(args)
        != {"searchtype", "page_index", "buyerName", "dbselect", "kw", "timeType"}
        or any(len(vals) != 1 for vals in args.values())
        or any(
            args[k] != [v]
            for k, v in (
                ("searchtype", "1"),
                ("page_index", "1"),
                ("dbselect", "bidx"),
                ("timeType", "5"),
            )
        )
    ):
        raise ExternalError("政府采购源须使用已核准的公开采购人查询。")
    buyer = args["buyerName"][0]
    keyword = args["kw"][0]
    if (
        not re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9（）()《》·]{2,100}", buyer)
        or not re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9（）()《》·]{0,40}", keyword)
        or source.profile.get("publisher") != buyer
        or not source.profile.get("title_terms")
    ):
        raise ExternalError("采购人名称、标题规则或机构配置无效。")
    return buyer, keyword


def notice_url(raw):
    parts = urlsplit(raw)
    if (
        parts.scheme not in ("http", "https")
        or parts.netloc != ARTICLE_HOST
        or parts.query
        or parts.fragment
        or not ARTICLE_PATH.fullmatch(parts.path)
    ):
        return None
    return urlunsplit(("https", ARTICLE_HOST, parts.path, "", ""))


def compact(text):
    return "".join(text.split())


def relevant(title, buyer, profile):
    # A name such as “中国印刷集团” alone makes no claim about the actual purchase.
    project = compact(title).replace(compact(buyer), "")
    subjects = profile.get("subject_terms", [])
    return (
        (not subjects or any(t.casefold() in project.casefold() for t in subjects))
        and not any(t in project for t in EXCLUDE)
        and any(t.casefold() in project.casefold() for t in PROJECT_TERMS)
        and any(t.casefold() in project.casefold() for t in profile["title_terms"])
    )


class ProcurementRateLimit(ExternalError):
    """CCGP sometimes returns its frequency notice with HTTP 200."""


def check_page(soup):
    heading = compact(soup.title.get_text() if soup.title else "")
    if "频繁访问" in heading or "您的访问过于频繁" in compact(soup.get_text()):
        raise ProcurementRateLimit("政府采购网提示访问过于频繁，暂停访问。")
    check_access_page(soup)


@contextmanager
def respect_site_limit(crawler):
    try:
        yield
    except ProcurementRateLimit:
        # The page gives no Retry-After. Share a one-day pause across workers
        # on the two official hosts and preserve any longer outstanding hold.
        until = timezone.now() + timedelta(days=1)
        with transaction.atomic():
            for host in ("search.ccgp.gov.cn", ARTICLE_HOST):
                CrawlHost.objects.get_or_create(host=host)
            CrawlHost.objects.filter(
                Q(hold_until__isnull=True) | Q(hold_until__lt=until),
                host__in=["search.ccgp.gov.cn", ARTICLE_HOST],
            ).update(hold_until=until)
        crawler.audit.setdefault("https://" + crawler.host, {})["frequency_limit"] = {
            "reason": "HTTP 200 内的官方访问频率提示",
            "local_pause_until": until.isoformat(),
        }
        raise


def listing(raw, source, audit=None):
    buyer, keyword = search_scope(source)
    soup = BeautifulSoup(raw, "html.parser")
    check_page(soup)
    count = re.search(r"共找到\s*([0-9,]+)\s*条内容", soup.get_text(" ", strip=True))
    if not count:
        raise ExternalError("政府采购查询未提供可验证的结果数量。")
    total = int(count[1].replace(",", ""))
    rows = soup.select(".vT-srch-result-list-bid > li")
    if total and not rows:
        raise ExternalError("政府采购查询结果结构已变化。")
    found = {}
    for row in rows:
        link = row.find("a", href=True)
        meta = row.find("span", recursive=False)
        purchaser = re.search(
            r"(?:^|\|)\s*采购人\s*[:：]\s*([^|]+?)\s*(?=\||$)",
            meta.get_text(" ", strip=True) if meta else "",
        )
        if not link or not purchaser:
            raise ExternalError("采购公告清单缺少标题或采购人字段。")
        title = " ".join(link.get_text("", strip=True).split())
        url = notice_url(link["href"])
        if (
            compact(purchaser[1]) == buyer
            and url
            and keyword in compact(title)
            and relevant(title, buyer, source.profile)
        ):
            found.setdefault(url, title)
    if audit is not None:
        audit["query"] = {
            "total": total,
            "page_rows": len(rows),
            "eligible": len(found),
            "limit": source.profile.get("limit", 4),
        }
    return total, [(title, url) for url, title in found.items()][
        : source.profile.get("limit", 4)
    ]


def get_items(source):
    search_scope(source)
    crawler = active_crawler.get()
    if crawler is None:
        raise ExternalError("政府采购访问须先实行站点规则检查。")
    _, raw = fetch(source.url, redirects=0)
    evidence = crawler.audit.setdefault("https://search.ccgp.gov.cn", {})
    with respect_site_limit(crawler):
        return listing(raw, source, evidence)[1]


def parse_notice(raw, source, listed_title):
    buyer, keyword = search_scope(source)
    soup = BeautifulSoup(raw, "html.parser")
    check_page(soup)
    heading = soup.select_one(".vF_detail_header h2")
    table = soup.select_one(".vF_detail_main .table")
    content = soup.select_one(".vF_detail_content")
    stamp = soup.select_one("#pubTime")
    if not all((heading, table, content, stamp)):
        raise ExternalError("采购公告标题、采购概要、正文或发布时间缺失。")
    title = " ".join(heading.get_text(" ", strip=True).split())
    # The standard summary frequently repeats “采购单位” in the contacts block.
    # Every labelled occurrence must identify the same purchaser.
    buyers = []
    project = None
    for cell in table.select("td.title"):
        key = compact(cell.get_text())
        value = cell.find_next_sibling("td")
        if key == "采购单位":
            buyers.append(compact(value.get_text()) if value is not None else "")
        if key == "采购项目名称" and value is not None:
            project = value.get_text(" ", strip=True)
    if not buyers or set(buyers) != {buyer}:
        raise ExternalError("公告概要中的采购单位与已核验采购人不一致。")
    if compact(title) != compact(listed_title) or keyword not in compact(title):
        raise ExternalError("采购公告正文标题与清单不一致。")
    if not project or not relevant(project, buyer, source.profile):
        raise ExternalError("采购项目名称与已核验的媒体业务范围不符。")
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", stamp.get_text(strip=True))
    if not match:
        raise ExternalError("采购公告发布时间无效。")
    try:
        published = date(*(int(piece) for piece in match.groups())).isoformat()
    except ValueError as exc:
        raise ExternalError("采购公告发布时间无效。") from exc
    text = content.get_text(" ", strip=True)
    # Match a labelled purchaser through the next labelled field. A prefix
    # match would also accept a subsidiary or a similarly named supplier.
    body_buyers = re.findall(
        r"(?:采购人信息名称|采购人名称|采购单位名称|采购人)[:：]([^:：]{2,120}?)"
        r"(?=(?:地址|项目名称|联系方式|联系人|联系电话|项目编号)[:：])",
        compact(text),
    )
    if set(body_buyers) != {buyer}:
        raise ExternalError("公告正文未确认与清单一致的采购人。")
    if len(text) < 30:
        raise ExternalError("采购公告没有可核验的实质正文。")
    return title[:500], text[:15000], published


def get_notice(source, listed_title, url):
    if not url or notice_url(url) != url:
        raise ExternalError("政府采购详情仅接受经核准的 HTTPS 公告地址。")
    search_scope(source)
    parent = active_crawler.get()
    if parent is None:
        raise ExternalError("政府采购访问须先实行站点规则检查。")
    child = Source(url=url, profile={"interval": source.profile.get("interval", 3)})
    with guarded(child) as crawler:
        try:
            canonical, raw = fetch(url, redirects=0)
            with respect_site_limit(crawler):
                title, text, published = parse_notice(raw, source, listed_title)
        finally:
            parent.audit.update(crawler.audit)
    parent.published = published
    return canonical, title, text
