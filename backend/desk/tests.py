"""All article content and accounts in these tests are synthetic and isolated."""

from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Event, Media, Run, Source, Policy, Article, Edition, Change
from .domain import record, review
from .collection import enqueue, schedule, work_once
from .external import ExternalError, fetch


class PlatformTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap", verbosity=0)
        cls.users = {}
        for role in ("阅读", "编辑", "审核", "签发", "配置"):
            u = User.objects.create_user(username=role, password="only-TestCase-1234")
            u.groups.add(Group.objects.get(name=role))
            cls.users[role] = u
        cls.media = Media.objects.create(
            name="测试机构", category="地方党媒体系", region="示例省"
        )
        cls.source = Source.objects.create(
            name="测试信源",
            media=cls.media,
            url="https://example.org/feed",
            trusted=True,
        )

    def use(self, role):
        client = APIClient()
        client.force_login(self.users[role])
        return client

    def candidate(self, n=1, verified=True):
        return record(
            {
                "title": f"测试报社改革 {n}",
                "url": f"https://example.org/article/{n}",
                "category": "改革类",
                "occurred": str(timezone.localdate()),
            },
            "测试",
            self.source,
            verified,
        )[0]

    def approved(self, n=1):
        e = self.candidate(n)
        review(self.users["审核"], [{"id": e.pk, "version": e.version}], "approve")
        e.refresh_from_db()
        return e

    def post(self, client, path, data):
        return client.post("/api/v1/" + path, data, format="json")

    def test_real_login_requires_csrf_and_logout_ends_session(self):
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.get("/api/v1/events").status_code, 403)
        c.get("/api/v1/session")
        login = {"username": "阅读", "password": "only-TestCase-1234"}
        self.assertEqual(c.post("/api/v1/login", login).status_code, 403)
        self.assertEqual(
            c.post(
                "/api/v1/login", login, HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value
            ).status_code,
            200,
        )
        self.assertEqual(c.get("/api/v1/session").json()["rights"], ["read"])
        self.assertEqual(
            c.post(
                "/api/v1/logout", HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value
            ).status_code,
            200,
        )
        self.assertEqual(c.get("/api/v1/events").status_code, 403)

    def test_role_boundaries(self):
        viewer = self.use("阅读")
        pending = self.candidate()
        self.assertEqual(viewer.get(f"/api/v1/events/{pending.id}").status_code, 404)
        for path in ("events?state=pending", "monitor"):
            self.assertEqual(viewer.get("/api/v1/" + path).status_code, 403)
        for path in ("events", "monitor/collect", "monitor/policy", "reports"):
            self.assertEqual(self.post(viewer, path, {}).status_code, 403)
        self.assertEqual(
            self.post(self.use("编辑"), "events/review", {}).status_code, 403
        )
        self.assertEqual(
            self.post(self.use("审核"), "monitor/policy", {}).status_code, 403
        )

    def test_link_verification_and_transactional_batch(self):
        a = self.candidate()
        b = self.candidate(2, False)
        items = [{"id": e.id, "version": 1} for e in (a, b)]
        c = self.use("审核")
        self.assertEqual(
            self.post(
                c, "events/review", {"items": items, "action": "approve"}
            ).status_code,
            400,
        )
        a.refresh_from_db()
        self.assertEqual(a.state, "pending")
        self.assertEqual(
            self.post(
                c, "events/review", {"items": items, "action": "reject", "reason": " "}
            ).status_code,
            400,
        )
        with patch(
            "desk.events_api.document", side_effect=ExternalError("unavailable")
        ):
            self.post(
                c,
                f"events/{b.id}",
                {"action": "verify", "article": b.articles.get().id},
            )
        self.assertEqual(b.articles.get().status, "可能失效")
        response = self.post(
            c,
            "events/review",
            {"items": items, "action": "reject", "reason": "需核对日期"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Change.objects.filter(action="reject", detail__reason="需核对日期").count(),
            2,
        )
        self.assertEqual(
            self.post(
                c, "events/review", {"items": items, "action": "approve"}
            ).status_code,
            409,
        )

    def test_merge_split_and_filters(self):
        a, b = self.approved(), self.approved(2)
        m2 = Media.objects.create(name="另一个测试机构", region="其他省")
        b.media.set([m2])
        c = self.use("审核")
        self.assertEqual(
            self.post(
                c,
                f"events/{b.id}",
                {"action": "merge", "target": a.id, "version": b.version},
            ).status_code,
            200,
        )
        viewer = self.use("阅读")
        detail = viewer.get(f"/api/v1/events/{a.id}").json()
        self.assertEqual(len(detail["articles"]), 2)
        self.assertEqual(viewer.get(f"/api/v1/events?media={m2.pk}").json()["count"], 1)
        self.assertEqual(
            viewer.get("/api/v1/events?region=其他省").json()["items"][0]["id"], a.id
        )
        b.refresh_from_db()
        self.assertEqual(
            self.post(
                c,
                f"events/{b.pk}",
                {"action": "split", "target": a.pk, "version": b.version},
            ).status_code,
            200,
        )
        self.assertEqual(viewer.get("/api/v1/events").json()["count"], 2)

    def test_stat_drill_and_date_boundary(self):
        e = self.approved()
        e.occurred = timezone.localdate() - timedelta(days=1)
        e.save()
        client = self.use("审核")
        stats = client.get("/api/v1/dashboard").json()
        today = stats["cards"][0]
        self.assertEqual(today["value"], 1)
        self.assertEqual(
            client.get("/api/v1/events", today["filters"]).json()["count"], 1
        )
        for category, count in stats["categories"].items():
            self.assertEqual(
                client.get(
                    "/api/v1/events", {**stats["period"], "category": category}
                ).json()["count"],
                count,
            )
        self.assertEqual(
            client.get("/api/v1/events", {"start": str(timezone.localdate())}).json()[
                "count"
            ],
            0,
        )

    def test_star_semantic_and_csv_scope(self):
        e = self.approved()
        c = self.use("阅读")
        self.post(c, f"events/{e.id}", {"action": "star", "on": True})
        self.assertEqual(c.get("/api/v1/events?star=1").json()["count"], 1)
        self.assertEqual(
            self.use("编辑").get("/api/v1/events?star=1").json()["count"], 0
        )
        with patch("desk.events_api.analyze", return_value={"ids": [e.pk, 99999]}):
            self.assertEqual(
                self.post(c, "events/search", {"q": "改革"}).json()["ids"], [e.pk]
            )
        self.assertEqual(c.get("/api/v1/events?ids=0").json()["count"], 0)
        self.assertIn(e.title, c.get("/api/v1/events?export=csv").content.decode())

    def test_worker_retries_and_idempotency(self):
        ids = enqueue("手动", "test")
        self.assertEqual(ids, enqueue("手动", "test"))
        run = Run.objects.get(id=ids[0])
        run.state = "running"
        run.expires = timezone.now() - timedelta(minutes=3)
        run.attempts = 1
        run.save()
        payload = [("测试报社改革", "https://example.org/a")]
        with (
            patch("desk.collection.adapter_items", return_value=payload),
            patch(
                "desk.collection.document",
                return_value=(payload[0][1], payload[0][0], "内容"),
            ),
            patch("desk.collection.inference", side_effect=ExternalError("未配置")),
        ):
            self.assertTrue(work_once())
            enqueue("手动", "again")
            self.assertTrue(work_once())
        run.refresh_from_db()
        self.assertEqual(run.attempts, 2)
        self.assertEqual(run.state, "done")
        self.assertEqual(Event.objects.get().state, "pending")
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(Run.objects.latest("pk").counts["重复"], 1)

    def test_worker_routing(self):
        Policy.objects.create(pk=1, enabled=True, threshold=85)
        for i, (title, score, expected) in enumerate(
            [
                ("媒体上线独立采编系统", 95, "approved"),
                ("报社改革工作协调机制", 60, "pending"),
                ("期刊转型事项", 10, "filtered"),
            ]
        ):
            link = "https://example.org/" + str(i)
            result = {
                "score": score,
                "relevant": True,
                "category": "改革类",
                "summary": "测试",
                "importance": "常规",
                "subtype": "",
            }
            enqueue("手动", str(i))
            with (
                patch("desk.collection.adapter_items", return_value=[(title, link)]),
                patch("desk.collection.document", return_value=(link, title, "测试")),
                patch("desk.collection.inference", return_value=result),
            ):
                work_once()
            self.assertEqual(Event.objects.get(title=title).state, expected)

    def test_restricted_channel_and_due_slots(self):
        self.source.adapter = "公众号"
        self.source.save()
        now = datetime(2026, 11, 18, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(schedule(now), schedule(now))
        self.assertNotEqual(schedule(now), schedule(now + timedelta(hours=10)))
        work_once()
        job = Run.objects.first()
        self.assertEqual(job.state, "manual")
        c = self.use("编辑")
        self.assertEqual(
            self.post(
                c, "monitor/inspect", {"id": job.id, "note": "检查完成"}
            ).status_code,
            200,
        )
        self.assertEqual(Run.objects.get(id=job.id).state, "inspected")

    def test_report_publication_and_revoke(self):
        e = self.approved()
        edit = self.use("编辑")
        sign = self.use("签发")
        draft = self.post(edit, "reports", {"kind": "daily"}).json()
        path = "reports/" + str(draft["id"])
        self.assertEqual(self.use("阅读").get("/api/v1/" + path).status_code, 404)
        self.assertEqual(
            self.post(
                edit, path, {"action": "issue", "version": draft["version"]}
            ).status_code,
            403,
        )
        signed = self.post(
            sign, path, {"action": "issue", "version": draft["version"]}
        ).json()
        shared = self.post(
            sign, path, {"action": "share", "version": signed["version"]}
        ).json()
        e.title = "源数据更正"
        e.save()
        public = "/api/v1/shared/" + shared["share"]
        self.assertNotEqual(
            APIClient().get(public).json()["content"]["events"][0]["title"], e.title
        )
        self.assertEqual(
            self.post(
                sign, path, {"action": "revoke", "version": shared["version"]}
            ).status_code,
            200,
        )
        self.assertEqual(APIClient().get(public).status_code, 404)
        self.assertEqual(Edition.objects.count(), 1)

    def test_weekly_period_and_evidence(self):
        e = self.approved()
        edit = self.use("编辑")
        self.assertEqual(
            self.post(edit, "reports", {"kind": "weekly"}).status_code, 400
        )
        e.approved -= timedelta(days=7)
        e.save()
        date = timezone.localtime(e.approved).date()
        report = self.post(edit, "reports", {"kind": "weekly", "day": str(date)}).json()
        self.assertEqual(
            report["content"]["events"][0]["sources"], [e.articles.get().url]
        )
        self.assertEqual(report["content"]["snapshot"]["本期入库"], 1)
        self.assertEqual(
            self.post(edit, "reports", {"kind": "weekly", "day": str(date)}).json()[
                "id"
            ],
            report["id"],
        )

    def test_archive_completion_config_and_permissions(self):
        editor = self.use("编辑")
        path = f"/api/v1/media/{self.media.pk}"
        self.assertEqual(
            editor.patch(
                path, {"channels": {"官网": "https://example.org"}}, format="json"
            ).status_code,
            200,
        )
        stats = editor.get("/api/v1/monitor").json()
        self.assertEqual(len(stats["missing"]), 4)
        c = self.use("配置")
        self.assertEqual(
            self.post(
                c, "monitor/policy", {"enabled": True, "threshold": 55, "low": 56}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post(
                c, "monitor/policy", {"enabled": True, "threshold": 85, "low": 40}
            ).status_code,
            200,
        )

    def test_private_source_address_rejected(self):
        with self.assertRaises(ExternalError):
            fetch("http://127.0.0.1/")

    def test_correction_preserves_reader_access_and_hides_draft(self):
        editor, signer, reader = self.use("编辑"), self.use("签发"), self.use("阅读")
        draft = self.post(editor, "reports", {"kind": "daily"}).json()
        path = "reports/" + str(draft["id"])
        signed = self.post(
            signer, path, {"action": "issue", "version": draft["version"]}
        ).json()
        revision = self.post(
            signer, path, {"action": "correct", "version": signed["version"]}
        ).json()
        self.post(
            editor,
            path,
            {
                "action": "edit",
                "headline": "测试库未发布更正内容",
                "version": revision["version"],
            },
        )
        public = reader.get("/api/v1/" + path).json()
        self.assertEqual(public["state"], "published")
        self.assertEqual(public["headline"], signed["headline"])
        self.assertEqual(
            reader.get("/api/v1/reports?q=未发布更正内容").json()["count"], 0
        )

    def test_public_source_import_is_idempotent(self):
        call_command("bootstrap", public_sources=True, verbosity=0)
        before = (Media.objects.count(), Source.objects.count(), Event.objects.count())
        call_command("bootstrap", public_sources=True, verbosity=0)
        self.assertEqual(
            before,
            (Media.objects.count(), Source.objects.count(), Event.objects.count()),
        )
        self.assertEqual(Event.objects.count(), 0)

    def test_dashboard_cutoff_is_carried_to_list(self):
        first = self.approved()
        viewer = self.use("阅读")
        card = viewer.get("/api/v1/dashboard").json()["cards"][0]
        self.approved(2)
        rows = viewer.get("/api/v1/events", card["filters"]).json()
        self.assertEqual(rows["count"], card["value"])
        self.assertEqual(rows["items"][0]["id"], first.id)
