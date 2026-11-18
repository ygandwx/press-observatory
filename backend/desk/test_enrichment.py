import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command, CommandError
from django.test import TestCase
from django.utils import timezone
from . import collection, external
from .crawl import Crawler, guarded, parse_rules
from .management.commands.verify_roster import ALL_FIELDS
from .management_api import SourceSerializer
from .models import Article, Change, CrawlHost, Media, Run, Source


class VerifiedRosterTests(TestCase):
    def test_atomic_roundtrip_and_retained_manual_field(self):
        org = Media.objects.create(
            name="样本",
            kind="",
            origin_id=uuid4(),
            origin_record={"官网": "未填"},
            channels={"官网": "https://existing.invalid/"},
        )
        item = {
            "id": str(org.origin_id),
            "name": "样本",
            "date": "2026-11-18",
            "checks": {k: "已查样本" for k in ALL_FIELDS},
            "searches": [{"url": "https://evidence.invalid/", "note": "查阅主办方"}],
            "candidates": [],
        }

        def fact(field, value):
            return {
                "field": field,
                "value": value,
                "basis": "主办方目录明确列出",
                "url": "https://evidence.invalid/list",
                "date": item["date"],
            }

        item["facts"] = [
            fact("类型", "期刊"),
            fact("官网", "https://evidence.invalid/"),
            fact("官方公众号", "样本杂志"),
        ]
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "data.json"
            out = Path(tmp) / "export.json"
            p.write_text(json.dumps([item]))
            call_command("verify_roster", "import", p, expected=1)
            org.refresh_from_db()
            self.assertEqual(org.verification, {})
            for _ in range(2):
                call_command("verify_roster", "import", p, expected=1, apply=True)
            self.assertEqual(Change.objects.filter(action="核验机构资料").count(), 1)
            call_command("verify_roster", "export", out)
            self.assertEqual(json.loads(out.read_bytes()), [item])
            org.refresh_from_db()
            self.assertEqual(
                org.channels,
                {"官网": "https://existing.invalid/", "官方公众号": "样本杂志"},
            )
            self.assertEqual(org.origin_record, {"官网": "未填"})
            org.channels["官方公众号"] = "编辑核定账号"
            org.save()
            item["facts"][2] = fact("官方公众号", "修订后的许可")
            item["facts"].append(fact("客户端", "样本客户端"))
            p.write_text(json.dumps([item, dict(item, id=str(uuid4()))]))
            with self.assertRaises(CommandError):
                call_command("verify_roster", "import", p, apply=True)
            org.refresh_from_db()
            self.assertEqual(
                org.verification["record"]["facts"][2]["value"], "样本杂志"
            )
            p.write_text(json.dumps([item]))
            call_command("verify_roster", "import", p, apply=True)
            org.refresh_from_db()
            self.assertEqual(org.channels["官方公众号"], "编辑核定账号")
            item["review_history"] = item["facts"]
            item["facts"] = []
            p.write_text(json.dumps([item]))
            call_command("verify_roster", "import", p, apply=True)
            org.refresh_from_db()
            self.assertEqual(org.channels["官方公众号"], "编辑核定账号")
            self.assertEqual(org.channels["官网"], "https://existing.invalid/")
            self.assertNotIn("客户端", org.channels)
            self.assertEqual(org.kind, "")


class CrawlPolicyTests(TestCase):
    def setUp(self):
        self.src = Source.objects.create(
            name="报社动态",
            url="https://press.invalid/section",
            profile={
                "selector": "main a[href]",
                "prefix": "/article/",
                "interval": 3,
                "limit": 2,
                "title_terms": ["报社合作"],
            },
        )

    def test_rules_paths_hosts_redirects_and_delay(self):
        calls = []

        def reply(url):
            calls.append(url)
            if url.endswith("robots.txt"):
                return (
                    200,
                    {},
                    b"User-agent: *\nDisallow: /private\nAllow: /private/open\nCrawl-delay: 120\n",
                )
            if url.endswith("/jump"):
                return 302, {"location": "/private/data"}, b""
            return 200, {}, b"body"

        with (
            patch("desk.crawl.request_once", side_effect=reply),
            patch("desk.crawl.wait_slot") as slots,
            guarded(self.src),
        ):
            self.assertEqual(
                external.fetch("https://press.invalid/private/open")[1], b"body"
            )
            with self.assertRaisesRegex(external.ExternalError, "robots"):
                external.fetch("https://press.invalid/jump")
            with self.assertRaisesRegex(external.ExternalError, "跨站"):
                external.fetch("https://else.invalid/doc")
        self.assertEqual(
            calls,
            [
                "https://press.invalid/robots.txt",
                "https://press.invalid/private/open",
                "https://press.invalid/jump",
            ],
        )
        self.assertEqual([c.args[1] for c in slots.call_args_list], [3, 120, 120])
        for status, body in [
            (302, b""),
            (403, b""),
            (200, b"<html>soft 404</html>"),
            (200, b"User-agent: *\nCrawl-delay: soon"),
        ]:
            with self.assertRaises(external.ExternalError):
                parse_rules(status, body)
        self.assertTrue(
            parse_rules(404, b"").can_fetch(self.src.url, "PressObservatory")
        )

    def test_throttle_and_future_retry_persist(self):
        CrawlHost.objects.create(
            host="press.invalid", last_request=timezone.now(), spacing=120
        )
        crawler = Crawler(self.src)
        with (
            patch(
                "desk.crawl.time.sleep", side_effect=RuntimeError("would wait")
            ) as sleep,
            patch("desk.crawl.request_once") as io,
        ):
            with self.assertRaisesRegex(RuntimeError, "would wait"):
                crawler.request(self.src.url)
            self.assertGreater(sleep.call_args.args[0], 119)
            io.assert_not_called()
        with (
            patch("desk.crawl.wait_slot"),
            patch(
                "desk.crawl.request_once",
                return_value=(429, {"retry-after": "300"}, b""),
            ),
        ):
            with self.assertRaisesRegex(external.ExternalError, "429"):
                crawler.request(self.src.url)
        self.assertGreater(
            (
                CrawlHost.objects.get(pk="press.invalid").hold_until - timezone.now()
            ).total_seconds(),
            298,
        )

    def test_section_scope_link_dedup_and_schedule(self):
        html = b'<main><a href="http://press.invalid/article/1" title="Press Association launches">first</a><a href="/article/1">same article</a><a href="/other">out of scope item</a><a href="https://external.invalid/article/2">external item</a></main>'
        with patch("desk.collection.fetch", return_value=(self.src.url, html)):
            self.assertEqual(
                collection.adapter_items(self.src),
                [("Press Association launches", "https://press.invalid/article/1")],
            )

        self.assertFalse(
            SourceSerializer(
                self.src, data={"scheduled": True}, partial=True
            ).is_valid()
        )
        Run.objects.create(
            key="old", source=self.src, state="done", source_signature="old"
        )
        self.assertFalse(
            SourceSerializer(
                self.src, data={"scheduled": True}, partial=True
            ).is_valid()
        )
        Run.objects.create(
            key="current",
            counts={"命中": 1},
            source=self.src,
            state="done",
            source_signature=collection.source_signature(self.src),
        )
        no_article = SourceSerializer(self.src, data={"scheduled": True}, partial=True)
        self.assertFalse(no_article.is_valid())
        Article.objects.create(
            source=self.src, title="样本原文", url="https://press.invalid/article/1"
        )
        form = SourceSerializer(self.src, data={"scheduled": True}, partial=True)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(len(collection.schedule()), 1)
        edit = SourceSerializer(
            self.src, data={"url": "https://press.invalid/section2"}, partial=True
        )
        self.assertTrue(edit.is_valid(), edit.errors)
        edit.save()
        self.assertFalse(Source.objects.get(pk=self.src.pk).scheduled)

    def test_public_embedded_list_keeps_only_scoped_same_host_articles(self):
        self.src.profile = {
            "embedded_list_selector": "#public-list",
            "subject_terms": ["Press Association"],
            "prefix": "/article/",
            "limit": 1,
        }
        data = [
            {"title": "Other institution announces project", "url": "/article/other"},
            {
                "title": "Press Association announces project",
                "url": "https://other.invalid/article/1",
            },
            {
                "title": "Press Association announces in print",
                "url": "/article/verified",
            },
        ]
        with patch(
            "desk.collection.fetch",
            return_value=(
                self.src.url,
                '<div id="public-list">' + json.dumps(data) + "</div>",
            ),
        ):
            self.assertEqual(
                collection.adapter_items(self.src),
                [
                    (
                        "Press Association announces in print",
                        "https://press.invalid/article/verified",
                    )
                ],
            )
        with patch(
            "desk.collection.fetch",
            return_value=(self.src.url, '<div id="public-list">unavailable</div>'),
        ):
            with self.assertRaises(external.ExternalError):
                collection.adapter_items(self.src)

    def test_directory_requires_listed_url_matching_title_and_publisher(self):
        doc = {
            "url": "https://publisher.invalid/public/report",
            "title": "Press annual report",
            "media": "报社",
            "headline_selector": "h1",
            "body_selector": "article",
        }
        self.src.profile = {"documents": [doc], "title_terms": ["report"]}
        self.src.save()
        org = Media.objects.create(name="报社")
        self.src.coverage_media.add(org)
        page = '<a href="http://publisher.invalid/public/report">cover</a><a href="https://other.invalid/report">unknown</a>'
        with patch("desk.collection.fetch", return_value=(self.src.url, page)):
            self.assertEqual(
                collection.adapter_items(self.src), [(doc["title"], doc["url"])]
            )

        def reply(url):
            if url.endswith("/robots.txt"):
                return 200, {}, b"User-agent: *\nDisallow: /private\n"
            if url == self.src.url:
                return 200, {}, page.encode()
            if url == doc["url"]:
                return (
                    200,
                    {},
                    b"<h1>Press annual report</h1><article>Original source publication details, dated and signed.</article>",
                )
            self.fail(url)

        with (
            patch("desk.crawl.request_once", side_effect=reply),
            patch("desk.crawl.wait_slot"),
        ):
            job = collection.enqueue("栏目实测", "annual", [self.src.pk])[0]
            self.assertTrue(collection.work_once())
            run = Run.objects.get(pk=job)
            self.assertEqual(run.state, "done", run.message)
            self.assertEqual(
                set(run.crawl_evidence),
                {"https://press.invalid", "https://publisher.invalid"},
            )
            self.assertEqual(
                Article.objects.get(url=doc["url"]).events.get().media.get(), org
            )
            self.assertFalse(collection.verified_for_schedule(self.src))
        doc["url"] = "https://publisher.invalid/public/other"
        self.src.profile["documents"] = [doc]
        with patch("desk.collection.fetch", return_value=(self.src.url, page)):
            self.assertEqual(collection.adapter_items(self.src), [])

    def test_public_metadata_preserves_original_date_and_headline(self):
        self.src.profile = {
            "embedded_metadata_selector": "#info",
            "body_selector": "article",
        }
        raw = '<title>publisher</title><div id="info">{"title":"Paper report","crtime":"2026-05-20 08:00:00"}</div><article>A full original publication body.</article>'
        with (
            guarded(self.src) as crawler,
            patch("desk.external.fetch", return_value=(self.src.url, raw)),
        ):
            self.assertEqual(external.document(self.src.url)[1], "Paper report")
            self.assertEqual(crawler.published, "2026-05-20")
