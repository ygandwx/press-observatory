from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Article, Edition, Event, Media, Report
from .reports import schedule_weekly


class TopicAndPublicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap", verbosity=0)
        cls.clients = {}
        for username, role in [
            ("leader-a", "社领导"),
            ("leader-b", "社领导"),
            ("editor", "运营编辑"),
        ]:
            user = User.objects.create_user(username=username)
            user.groups.add(Group.objects.get(name=role))
            cls.clients[username] = user
        cls.when = timezone.now() - timedelta(days=timezone.localdate().weekday() + 7)
        cls.paper = Media.objects.create(name="专题测试报")
        cls.event = Event.objects.create(
            title="报社推出新项目",
            summary="提高 AI 可见度",
            state="approved",
            category="技术类",
            approved=cls.when,
            occurred=timezone.localdate(cls.when),
        )
        cls.event.media.add(cls.paper)
        cls.event.articles.add(
            Article.objects.create(
                title="来源说明",
                url="https://example.invalid/topic",
                excerpt="launched GEO strategy，改进智能体检索优化",
            )
        )

    def actor(self, name):
        c = APIClient()
        c.force_login(self.clients[name])
        return c

    def test_topic_reuses_events_filters_evidence_and_date(self):
        r = self.actor("leader-a")
        child = Event.objects.create(
            title="生成式引擎优化",
            state="approved",
            approved=self.when,
            merged_into=self.event,
        )
        second = Media.objects.create(name="双报来源")
        child.media.add(second)
        for title, state, noise in [
            ("Geography today", "approved", False),
            ("GEO 暂稿", "pending", False),
            ("GEO 招采", "approved", True),
            ("GEO 淘汰", "rejected", False),
        ]:
            Event.objects.create(
                title=title, state=state, is_noise=noise, approved=self.when
            )
        day = timezone.localdate(self.when).isoformat()
        page = r.get(
            "/api/v1/geo",
            {
                "start": day,
                "end": day,
                "media": second.pk,
                "category": "技术类",
                "q": "智能体检索优化",
            },
        ).json()
        self.assertEqual([x["id"] for x in page["items"]], [self.event.pk])
        item = page["items"][0]
        matches = {x["term"]: x["fields"] for x in item["matches"]}
        self.assertIn("原文摘要", matches["GEO"])
        self.assertEqual(
            next(m["source_urls"] for m in item["matches"] if m["term"] == "GEO"),
            ["https://example.invalid/topic"],
        )
        self.assertEqual(r.get("/api/v1/geo", {"q": "launched"}).json()["count"], 1)
        self.assertIn("摘要", matches["AI可见度"])
        self.assertIn("标题", matches["生成式引擎优化"])
        self.assertEqual(item["sources"][0]["url"], "https://example.invalid/topic")
        self.assertEqual(r.get("/api/v1/geo").json()["count"], 1)
        self.assertEqual(
            r.get("/api/v1/geo", {"category": "政策类"}).json()["count"], 0
        )
        self.assertEqual(
            r.get(
                "/api/v1/geo", {"start": "2026-01-08", "end": "2026-01-01"}
            ).status_code,
            400,
        )
        normal = r.get(f"/api/v1/events/{self.event.pk}").json()
        self.assertEqual(normal["category"], item["category"])

    def test_weekly_timer_scoped_shares_and_correction(self):
        editor = self.actor("editor")
        output = StringIO()
        call_command("enqueue", stdout=output)
        r = Report.objects.get(kind="weekly")
        self.assertIn(r.day.isoformat(), output.getvalue())
        self.assertEqual(
            (r.state, r.content["geo_progress"][0]["id"]), ("draft", self.event.id)
        )
        self.assertIsNone(schedule_weekly())
        self.assertEqual(Report.objects.filter(kind="weekly").count(), 1)
        path = f"/api/v1/reports/{r.pk}"

        def act(action, **body):
            r.refresh_from_db()
            return editor.post(
                path, {"action": action, "version": r.version, **body}, format="json"
            )

        self.assertEqual(act("issue").status_code, 200)
        first = Edition.objects.get(report=r)
        links = act("share").json()["share_views"]
        self.assertNotEqual(links["leader"], links["detail"])
        for name, token in links.items():
            view = APIClient().get("/api/v1/shared/" + token).json()
            self.assertEqual(view["view"], name)
            self.assertEqual(
                "summary" in view["content"]["events"][0], name == "detail"
            )
        self.assertIsNone(schedule_weekly())
        self.assertEqual(Report.objects.get(pk=r.pk).state, "published")
        self.assertEqual(act("correct").status_code, 200)
        self.assertEqual(
            APIClient().get("/api/v1/shared/" + links["detail"]).status_code, 404
        )
        self.assertEqual(act("rebuild_correction", reason="").status_code, 400)
        Event.objects.filter(pk=self.event.pk).update(state="rejected", is_noise=True)
        corrected = act("rebuild_correction", reason="复核原文后移出噪声").json()[
            "content"
        ]
        self.assertEqual(corrected["events"], [])
        self.assertEqual(corrected["geo_progress"], [])
        self.assertEqual(corrected["correction"]["removed"], [self.event.pk])
        self.assertEqual(
            self.actor("leader-a").get(path).json()["content"], first.content
        )
        self.assertEqual(act("edit", headline="更正后结论").status_code, 200)
        self.assertEqual(act("issue").status_code, 200)
        older = self.actor("leader-a").get(path, {"edition": first.version}).json()
        self.assertEqual(older["content"], first.content)
        self.assertEqual(older["headline"], first.content["headline"])
        self.assertEqual(Edition.objects.filter(report=r).count(), 2)
        new_links = act("share").json()["share_views"]
        selected = editor.get(path, {"edition": first.version}).json()["share_views"]
        self.assertNotEqual(selected["leader"], new_links["leader"])
        archived_view = APIClient().get("/api/v1/shared/" + selected["detail"]).json()
        self.assertEqual(
            (archived_view["version"], archived_view["content"]),
            (first.version, first.content),
        )
        self.assertEqual(act("revoke").status_code, 200)
        for token in [*new_links.values(), *selected.values()]:
            self.assertEqual(
                APIClient().get("/api/v1/shared/" + token).status_code, 404
            )

    def test_personal_actions_do_not_grant_editor_access(self):
        reader, peer = self.actor("leader-a"), self.actor("leader-b")
        targets = [
            ("events", self.event.pk, "star", "star"),
            ("media", self.paper.pk, "follow", "following"),
        ]
        for resource, pk, action, f in targets:
            path = f"/api/v1/{resource}/{pk}"
            for on in [True, True, False]:
                self.assertEqual(
                    reader.post(
                        path, {"action": action, "on": on}, format="json"
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    reader.get("/api/v1/" + resource, {f: "1"}).json()["count"], int(on)
                )
                self.assertEqual(
                    peer.get("/api/v1/" + resource, {f: "1"}).json()["count"], 0
                )
            self.assertEqual(
                reader.post(
                    path, {"action": "edit", "title": "越权"}, format="json"
                ).status_code,
                403,
            )
        self.assertEqual(
            reader.post(
                "/api/v1/reports", {"kind": "daily"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(APIClient().get("/api/v1/geo").status_code, 403)
