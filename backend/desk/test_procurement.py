import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from . import procurement
from .collection import adapter_items, verified_for_schedule
from .external import ExternalError
from .models import Article, Event, Run, Source


class ProcurementTests(SimpleTestCase):
    buyer = "甲日报社"
    title = "甲日报社新闻纸印刷采购公告"
    url = "https://www.ccgp.gov.cn/cggg/zygg/zbgg/202611/t20261118_12345678.htm"

    def setUp(self):
        self.source = Source(
            adapter="html",
            url=procurement.search_url(self.buyer, "印刷"),
            profile={"publisher": self.buyer, "title_terms": ["印刷"], "limit": 2},
        )

    def listing(self, title=None, buyer=None, url=None):
        return f"""<p>共找到 1 条内容</p>
        <ul class="vT-srch-result-list-bid"><li>
        <a href="{url or self.url}">{title or self.title}</a>
        <span>发布日期|采购人：{buyer or self.buyer}|代理机构：{self.buyer}</span>
        </li></ul>"""

    def notice(self, project="新闻纸印刷", buyer=None, extra="", day="18"):
        return f"""<div class="vF_detail_header"><h2>{self.title}</h2></div>
        <div id="pubTime">2026年11月{day}日 08:09</div>
        <div class="vF_detail_main"><table class="table"><tr>
        <td class="title">采购单位</td><td>{buyer or self.buyer}</td></tr>
        <tr><td class="title">采购项目名称</td><td>{project}</td></tr>
        <tr><td class="title">代理机构名称</td><td>{self.buyer}</td></tr>
        {extra}</table></div>
        <div class="vF_detail_content">采购人发布了本次新闻纸印刷项目的采购公告，现就印制范围、采购数量和交付时间予以公示。<p>采购人信息 名称：{buyer or self.buyer} 地址：示例地址</p></div>"""

    def test_buyer_is_exact_and_agents_do_not_match(self):
        for candidate in ["甲日报社下属公司", "乙采购办公室"]:
            self.assertEqual(
                procurement.listing(self.listing(buyer=candidate), self.source), (1, [])
            )
            with self.assertRaisesRegex(ExternalError, "采购单位"):
                procurement.parse_notice(
                    self.notice(buyer=candidate), self.source, self.title
                )
        extra = '<tr><td class="title">采购单位</td><td>乙单位</td></tr>'
        with self.assertRaisesRegex(ExternalError, "采购单位"):
            procurement.parse_notice(self.notice(extra=extra), self.source, self.title)

    def test_body_must_independently_name_exact_purchaser(self):
        notice = self.notice()
        for alternate in [
            "采购代理机构名称：甲日报社",
            "采购人信息名称：甲日报社印务公司",
        ]:
            html = notice.replace("采购人信息 名称：甲日报社", alternate)
            with self.assertRaisesRegex(ExternalError, "公告正文"):
                procurement.parse_notice(html, self.source, self.title)

    def test_verifies_project_title_body_and_publication_date(self):
        title, body, date = procurement.parse_notice(
            self.notice(), self.source, self.title
        )
        self.assertEqual((title, date), (self.title, "2026-11-18"))
        self.assertIn("印制范围", body)
        for html, submitted in [
            (self.notice(), "不同的公告标题"),
            (self.notice(day="31"), self.title),
            (self.notice(project="印刷技术中心食堂餐饮"), self.title),
            ("<p>未知结构</p>", self.title),
        ]:
            with self.assertRaises(ExternalError):
                procurement.parse_notice(html, self.source, submitted)

    def test_group_title_cannot_name_a_newspaper_only_as_its_buyer(self):
        self.source.profile["subject_terms"] = ["甲日报"]
        self.assertEqual(procurement.listing(self.listing(), self.source), (1, []))
        self.assertEqual(
            len(
                procurement.listing(
                    self.listing(title="甲日报社《甲日报》印刷采购"), self.source
                )[1]
            ),
            1,
        )

    def test_requires_expected_host_scheme_article_path_and_query(self):
        for url in [
            self.url.replace("www.", "other."),
            self.url.replace("/cggg/", "/else/"),
            self.url + "?x=1",
            self.url.replace("https", "ftp"),
        ]:
            self.assertEqual(
                procurement.listing(self.listing(url=url), self.source), (1, [])
            )
        self.assertEqual(
            procurement.notice_url(self.url.replace("https", "http")), self.url
        )
        for query in [
            self.source.url + "&page_index=2",
            self.source.url.replace("https:", "http:"),
            self.source.url.replace("/bxsearch", "/wrong"),
        ]:
            with self.assertRaises(ExternalError):
                procurement.search_scope(Source(url=query, profile=self.source.profile))
        self.source.profile["publisher"] = "另一个单位"
        with self.assertRaises(ExternalError):
            procurement.search_scope(self.source)

    def test_http_200_frequency_page_is_not_a_zero_result(self):
        with self.assertRaises(procurement.ProcurementRateLimit):
            procurement.listing("<title>频繁访问!中国政府采购网</title>", self.source)
        with self.assertRaises(procurement.ProcurementRateLimit):
            procurement.parse_notice(
                "<p>您的访问过于频繁，请稍后再试</p>", self.source, self.title
            )

    def test_frequency_signal_persists_both_host_holds_and_audit(self):
        crawler = SimpleNamespace(audit={}, host="search.ccgp.gov.cn")
        with (
            patch.object(procurement.transaction, "atomic"),
            patch.object(procurement.CrawlHost, "objects") as hosts,
            self.assertRaises(procurement.ProcurementRateLimit),
            procurement.respect_site_limit(crawler),
        ):
            raise procurement.ProcurementRateLimit("频繁访问")
        self.assertEqual(
            [call.kwargs["host"] for call in hosts.get_or_create.call_args_list],
            ["search.ccgp.gov.cn", "www.ccgp.gov.cn"],
        )
        until = hosts.filter.return_value.update.call_args.kwargs["hold_until"]
        self.assertGreater((until - procurement.timezone.now()).total_seconds(), 86390)
        self.assertEqual(
            crawler.audit["https://search.ccgp.gov.cn"]["frequency_limit"][
                "local_pause_until"
            ],
            until.isoformat(),
        )

    def test_empty_result_and_invalid_structure(self):
        self.assertEqual(
            procurement.listing("<p>共找到 0 条内容</p>", self.source), (0, [])
        )
        for raw in ["<p>没有总数</p>", "<p>共找到 1 条内容</p>"]:
            with self.assertRaises(ExternalError):
                procurement.listing(raw, self.source)

    def test_worker_routing_and_child_policy(self):
        with self.assertRaisesRegex(ExternalError, "站点规则"):
            adapter_items(self.source)
        parent = SimpleNamespace(audit={})
        child = SimpleNamespace(audit={"www.ccgp.gov.cn": {"status": 404}})
        with (
            patch.object(procurement, "active_crawler") as context,
            patch.object(procurement, "guarded") as guard,
            patch.object(procurement, "fetch") as fetch,
        ):
            context.get.return_value = parent
            guard.return_value.__enter__.return_value = child
            fetch.return_value = (self.source.url, self.listing())
            self.assertEqual(adapter_items(self.source), [(self.title, self.url)])
            fetch.assert_called_once_with(self.source.url, redirects=0)
            fetch.return_value = (self.url, self.notice())
            canonical, title, _ = procurement.get_notice(
                self.source, self.title, self.url
            )
            self.assertEqual((canonical, title), (self.url, self.title))
            self.assertEqual(guard.call_args.args[0].url, self.url)
            self.assertEqual(
                parent.audit["www.ccgp.gov.cn"], child.audit["www.ccgp.gov.cn"]
            )
            self.assertEqual(
                parent.audit["https://search.ccgp.gov.cn"]["query"]["eligible"], 1
            )
            self.assertEqual(parent.published, "2026-11-18")
            fetch.assert_called_with(self.url, redirects=0)


class ProcurementSnapshotTests(TestCase):
    def test_preview_replay_and_tamper_check(self):
        fixture = ProcurementTests()
        fixture.setUp()
        self.source = fixture.source
        self.source.enabled = False
        self.source.save()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def page(url, filename, raw):
                host = url.split("/")[2]
                (root / filename).write_bytes(raw)
                return dict(
                    url=url,
                    file=filename,
                    at=timezone.now().isoformat(),
                    sha256=sha256(raw).hexdigest(),
                    robots={
                        "https://" + host: {
                            "robots": "https://" + host + "/robots.txt",
                            "status": 404,
                            "sha256": sha256(b"").hexdigest(),
                            "minimum_seconds": 3,
                        }
                    },
                )

            listing = page(self.source.url, "list.html", fixture.listing().encode())
            listing["articles"] = [
                page(fixture.url, "article.html", fixture.notice().encode())
            ]
            index = root / "index.json"
            index.write_text(json.dumps([listing]))
            with patch(
                "desk.procurement.fetch", side_effect=AssertionError("离线禁止联网")
            ):
                call_command("import_procurement_snapshot", index)
                self.assertEqual(Run.objects.count(), 0)
                self.assertEqual(Article.objects.count(), 0)
                call_command("import_procurement_snapshot", index, apply=True)
                self.assertEqual(Event.objects.get().state, "pending")
                self.assertFalse(verified_for_schedule(self.source))
                call_command("import_procurement_snapshot", index, apply=True)
                self.assertEqual((Run.objects.count(), Article.objects.count()), (1, 1))
                (root / "article.html").write_bytes(b"changed")
                with self.assertRaisesMessage(CommandError, "摘要不符"):
                    call_command("import_procurement_snapshot", index, apply=True)
