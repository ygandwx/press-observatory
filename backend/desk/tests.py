"""All article content and accounts in these tests are synthetic and isolated."""

from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Event, Media, Run, Source, Policy, Article, Edition, Change, Report
from .domain import record, review
from .collection import enqueue, schedule, work_once, ManualCheck
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

    def test_historical_drill_survives_merge_split_and_archive_edits(self):
        parent, child = self.approved(1), self.approved(2)
        viewer, reviewer = self.use("阅读"), self.use("审核")

        def card():
            return viewer.get("/api/v1/dashboard").json()["cards"][0]

        def rows(card, **extra):
            return viewer.get("/api/v1/events", {**card["filters"], **extra}).json()

        before = card()
        baseline = rows(before)
        self.assertEqual(baseline["count"], 2)
        self.assertEqual(card()["filters"], before["filters"])
        for action in ("merge", "split"):
            child.refresh_from_db()
            r = self.post(
                reviewer,
                f"events/{child.pk}",
                {"target": parent.pk, "version": child.version, "action": action},
            )
            self.assertEqual(r.status_code, 200)
            if action == "merge":
                merged = card()
                self.assertEqual(merged["value"], 1)
                self.assertEqual(len(rows(merged)["items"][0]["articles"]), 2)
            self.assertEqual(rows(before), baseline)
        self.assertEqual(card()["value"], 2)
        self.assertEqual(rows(merged)["count"], 1)
        self.media.region = "新区域"
        self.media.save()
        self.assertEqual(rows(before, region="示例省")["count"], 2)
        self.assertEqual(rows(card(), region="示例省")["count"], 0)
        page = rows(before, size=1, page=2)
        self.assertEqual(page["items"], baseline["items"][1:])
        csv = viewer.get(
            "/api/v1/events", {**before["filters"], "export": "csv"}
        ).content.decode()
        self.assertIn(child.articles.get().url, csv)
        self.assertIn(parent.articles.get().url, csv)
        self.assertEqual(
            reviewer.get("/api/v1/events", before["filters"]).status_code, 404
        )
        self.assertEqual(
            viewer.get(
                "/api/v1/events", {"as_of": timezone.now().isoformat()}
            ).status_code,
            400,
        )

    def test_historical_queue_and_media_verify_current_permissions(self):
        pending = self.candidate(3)
        reviewer = self.use("审核")
        media = self.media
        media.focus = "重点关注"
        media.save()
        cards = reviewer.get("/api/v1/dashboard").json()["cards"]
        old_queue, old_archive = cards[1], cards[3]
        self.assertEqual(old_queue["value"], 1)
        self.assertEqual(old_archive["value"], 1)
        media.focus = "常规参照"
        media.save()
        review(
            self.users["审核"],
            [{"id": pending.pk, "version": pending.version}],
            "approve",
        )
        self.assertEqual(
            reviewer.get("/api/v1/events", old_queue["filters"]).json()["items"][0][
                "state"
            ],
            "pending",
        )
        self.assertEqual(
            reviewer.get("/api/v1/media", old_archive["filters"]).json()["items"][0][
                "focus"
            ],
            "重点关注",
        )
        self.assertEqual(
            reviewer.get(
                "/api/v1/events", {**old_archive["filters"], "state": "media"}
            ).status_code,
            400,
        )
        new = reviewer.get("/api/v1/dashboard").json()["cards"]
        self.assertEqual([new[i]["value"] for i in (1, 3)], [0, 0])
        self.users["审核"].groups.set([Group.objects.get(name="阅读")])
        self.assertEqual(
            reviewer.get("/api/v1/events", old_queue["filters"]).status_code, 403
        )
        self.assertEqual(
            reviewer.get(
                "/api/v1/events", {**old_queue["filters"], "state": "approved"}
            ).status_code,
            404,
        )

    def test_six_daily_sections_keep_original_evidence_and_unknowns(self):
        editor = self.use("编辑")
        market = "头部市场化纸媒"
        cases = [
            ("中央级主流媒体", "中央机关党报党刊", "中央党报党刊"),
            ("地方党媒体系", "省级党委机关报", "地方党报"),
            ("中央级主流媒体", "中央行业专业报", "行业专业报"),
            (market, "都市报晚报", "地方商业报"),
            (market, "都市报晚报", "晚报类"),
            ("中央级主流媒体", "中央重点传媒期刊", "重点传媒期刊"),
        ]
        expected = [x[2] for x in cases]
        members = {}
        sources = {}
        for i, (category, sub, section) in enumerate(cases):
            media = Media.objects.create(
                name=f"合成机构 {i}",
                category=category,
                subcategory=sub,
                reference=f"https://example.org/type/{i}",
                digest_section=section if category == market else "",
            )
            event = self.approved(20 + i)
            event.media.set([media])
            members[section] = event.pk
            sources[event.pk] = event.articles.get().url
        unknown = self.approved(80)
        unknown_media = Media.objects.create(
            name="合成未核实晚报", category=market, subcategory="都市报晚报"
        )
        unknown.media.set([unknown_media])
        no_media = self.approved(81)
        no_media.media.clear()
        report = self.post(editor, "reports", {"kind": "daily"}).json()
        content = report["content"]
        self.assertEqual(content["section_order"], expected)
        self.assertEqual(
            content["section_events"], {s: [p] for s, p in members.items()}
        )
        self.assertEqual(
            {e["id"]: e["media"] for e in content["unclassified"]},
            {unknown.id: [unknown_media.name], no_media.id: []},
        )
        evidence = {e["id"]: e for e in content["events"]}
        for section, pk in members.items():
            self.assertEqual(evidence[pk]["sources"], [sources[pk]])
            self.assertEqual(len(content["categories"][section]), 1)
        market_proof = evidence[members["晚报类"]]["classification_sources"]
        self.assertEqual(
            market_proof,
            [
                {
                    "media": "合成机构 4",
                    "section": "晚报类",
                    "url": "https://example.org/type/4",
                }
            ],
        )
        signed = self.post(
            self.use("签发"),
            f"reports/{report['id']}",
            {"action": "issue", "version": report["version"]},
        ).json()
        Media.objects.filter(category=market).update(digest_section="", reference="")
        self.assertEqual(
            self.use("阅读").get(f"/api/v1/reports/{report['id']}").json()["content"],
            signed["content"],
        )

    def test_daily_empty_sections_priority_and_editor_evidence_validation(self):
        editor = self.use("编辑")
        media_path = f"/api/v1/media/{self.media.pk}"
        changes = {
            "digest_section": "地方商业报",
            "category": "头部市场化纸媒",
            "subcategory": "都市报晚报",
        }
        self.assertEqual(
            editor.patch(media_path, changes, format="json").status_code, 400
        )
        self.assertEqual(
            editor.patch(
                media_path,
                {**changes, "reference": "https://example.org/verified-type"},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            editor.patch(
                media_path,
                {"category": "地方党媒体系", "subcategory": "省级党委机关报"},
                format="json",
            ).status_code,
            400,
        )
        top = self.approved(10)
        Event.objects.filter(pk=top.pk).update(
            importance="核心", summary="核心事件摘要"
        )
        self.approved(11)
        self.approved(12)
        data = self.post(editor, "reports", {"kind": "daily"}).json()
        c = data["content"]
        self.assertEqual(len(c["categories"]["地方商业报"]), 2)
        self.assertEqual(c["categories"]["地方商业报"][0], "核心事件摘要")
        self.assertEqual(c["categories"]["晚报类"], [])
        self.assertEqual(
            self.post(
                editor,
                f"reports/{data['id']}",
                {
                    "action": "edit",
                    "categories": {**c["categories"], "地方商业报": ["一", "二", "三"]},
                    "version": data["version"],
                },
            ).status_code,
            400,
        )

    def test_search_entire_filtered_set_and_more_than_500_matches(self):
        viewer = self.use("阅读")
        now = timezone.now()
        events = Event.objects.bulk_create(
            [
                Event(
                    title=f"合成检索 {i}",
                    state="approved",
                    approved=now,
                    category="技术类",
                )
                for i in range(523)
            ]
        )
        self.approved(999)
        target = events[0]
        visited = []

        def last_only(prompt, material):
            batch = [e["id"] for e in material["events"]]
            visited.extend(batch)
            return {"ids": [target.pk, 999999] if target.pk in batch else []}

        data = {"q": "目标", "filters": {"category": "技术类"}}
        with patch("desk.events_api.analyze", side_effect=last_only):
            run = self.post(viewer, "events/search", data).json()
        self.assertEqual(visited, [e.pk for e in reversed(events)])
        self.assertEqual(run["scanned"], 523)
        self.assertEqual(run["ids"], [target.pk])
        with patch(
            "desk.events_api.analyze",
            side_effect=lambda _, m: {"ids": [e["id"] for e in m["events"]]},
        ):
            result = self.post(viewer, "events/search", {**data, "q": "全部"}).json()[
                "snapshot"
            ]
        page = viewer.get(
            "/api/v1/events", {"result": result, "size": 100, "page": 6}
        ).json()
        self.assertEqual(page["count"], 523)
        self.assertEqual(len(page["items"]), 23)
        self.assertEqual(page["items"][-1]["id"], target.pk)
        csv = viewer.get(
            "/api/v1/events", {"result": result, "export": "csv"}
        ).content.decode()
        self.assertEqual(len(csv.splitlines()), 524)
        self.assertEqual(
            self.use("编辑").get("/api/v1/events", {"result": result}).status_code, 404
        )

    def test_partial_search_failure_and_retry_do_not_publish_partial_hits(self):
        viewer = self.use("阅读")
        now = timezone.now()
        Event.objects.bulk_create(
            [
                Event(title=f"合成 {i}", state="approved", approved=now)
                for i in range(65)
            ]
        )
        with patch("desk.events_api.analyze", return_value={"ids": []}) as api:
            first = self.post(
                viewer, "events/search", {"q": "合成", "incremental": True}
            ).json()
            self.assertFalse(first["complete"])
            self.assertIsNone(first["snapshot"])
            step = {"search": first["search"], "incremental": True}
            self.assertEqual(
                self.post(viewer, "events/search", {**step, "cursor": 0}).json(), first
            )
            self.assertEqual(api.call_count, 1)
        self.approved(882)
        with patch(
            "desk.events_api.analyze", side_effect=ExternalError("模型暂不可用")
        ):
            fail = self.post(
                viewer, "events/search", {**step, "cursor": first["scanned"]}
            )
        self.assertEqual(fail.status_code, 400)
        self.assertIn("检索未完成（50/65）", str(fail.json()))
        self.assertEqual(
            self.post(
                self.use("编辑"), "events/search", {**step, "cursor": 50}
            ).status_code,
            404,
        )
        with patch("desk.events_api.analyze", return_value={"ids": []}) as api:
            done = self.post(viewer, "events/search", {**step, "cursor": 50}).json()
            self.assertTrue(done["complete"])
            self.assertEqual(done["total"], 65)
            self.assertEqual(len(api.call_args.args[1]["events"]), 15)
        self.assertEqual(
            self.post(
                viewer, "events/search", {**step, "cursor": 50, "search": "无效"}
            ).status_code,
            404,
        )

    def test_legacy_signed_layout_and_new_multi_institution_digest(self):
        editor, signer, reader = self.use("编辑"), self.use("签发"), self.use("阅读")
        day = timezone.localdate() - timedelta(days=2)
        legacy = {
            "headline": "历史样稿",
            "categories": {
                "中央级主流媒体": ["原摘要"],
                "地方党媒体系": [],
                "头部市场化纸媒": [],
            },
        }
        old = Report.objects.create(kind="daily", day=day, content=legacy)
        path = f"reports/{old.pk}"
        issued = self.post(
            signer, path, {"action": "issue", "version": old.version}
        ).json()
        response = self.post(
            editor,
            "reports",
            {
                "kind": "daily",
                "day": str(day),
                "regenerate": True,
                "version": issued["version"],
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(reader.get("/api/v1/" + path).json()["content"], legacy)
        self.media.subcategory = "省级党委机关报"
        self.media.save()
        parent, child = self.approved(42), self.approved(43)
        child.media.set(
            [
                Media.objects.create(
                    name="合成期刊",
                    category="中央级主流媒体",
                    subcategory="中央重点传媒期刊",
                )
            ]
        )
        response = self.post(
            self.use("审核"),
            f"events/{child.pk}",
            {"action": "merge", "target": parent.pk, "version": child.version},
        )
        self.assertEqual(response.status_code, 200)
        c = self.post(editor, "reports", {"kind": "daily"}).json()["content"]
        self.assertEqual(c["snapshot"]["本期入库"], 1)
        self.assertEqual(c["section_events"]["地方党报"], [parent.pk])
        self.assertEqual(c["section_events"]["重点传媒期刊"], [parent.pk])
        self.assertCountEqual(
            c["events"][0]["sources"],
            [parent.articles.get().url, child.articles.get().url],
        )

    def test_collection_receipt_tracks_only_its_jobs_and_approved_events(self):
        editor = self.use("编辑")
        reader = self.use("阅读")
        self.approved(5)
        Source.objects.create(
            name="合成人工信源",
            media=self.media,
            url="https://example.org/manual",
            adapter="人工",
        )
        Policy.objects.create(pk=1, enabled=True, threshold=85)
        receipt = self.post(editor, "monitor/collect", {"key": "receipt"}).json()
        self.assertEqual(len(receipt["jobs"]), 2)
        self.assertEqual(
            receipt, self.post(editor, "monitor/collect", {"key": "receipt"}).json()
        )
        self.post(
            editor,
            "monitor/collect",
            {"key": "receipt:other", "source": self.source.pk},
        )
        path = "/api/v1/monitor/batch"
        params = {"key": receipt["key"]}
        first = editor.get(path, params).json()
        self.assertEqual([j["id"] for j in first["jobs"]], receipt["jobs"])
        self.assertFalse(first["complete"])
        self.assertIsNone(first["snapshot"])
        self.assertEqual(reader.get(path, params).status_code, 403)
        self.assertEqual(editor.get(path, {"key": "missing"}).status_code, 404)
        title, link = "媒体上线独立采编工具", "https://example.org/new-tool"

        def source_items(source):
            if source.adapter == "人工":
                raise ManualCheck("合成巡检")
            return [(title, link)]

        with (
            patch("desk.collection.adapter_items", side_effect=source_items),
            patch("desk.collection.document", return_value=(link, title, title)),
            patch(
                "desk.collection.inference",
                return_value={
                    "score": 95,
                    "relevant": True,
                    "category": "技术类",
                    "summary": title,
                    "importance": "常规",
                    "subtype": "",
                },
            ),
        ):
            self.assertTrue(work_once())
            self.assertTrue(work_once())
        final = editor.get(path, params).json()
        self.assertTrue(final["complete"])
        self.assertEqual(final["finished"], 2)
        self.assertEqual(final["counts"]["自动入库"], 1)
        self.assertEqual(final["counts"]["AI 分析"], 1)
        self.assertEqual([j["state"] for j in final["jobs"]], ["done", "manual"])
        events = editor.get("/api/v1/events", {"snapshot": final["snapshot"]}).json()
        self.assertEqual(events["count"], 1)
        self.assertEqual(events["items"][0]["title"], title)
        # A later attempt may rediscover the same event as a duplicate. Its original audit still links it to this receipt.
        Run.objects.filter(pk=receipt["jobs"][0]).update(counts={"重复": 1})
        final = editor.get(path, params).json()
        self.assertEqual([final["counts"]["自动入库"], final["event_count"]], [1, 1])

    def test_week_comparison_respects_approval_window_and_signed_version(self):
        monday = timezone.localdate() - timedelta(
            days=timezone.localdate().weekday() + 7
        )

        def at(day):
            return datetime.combine(
                day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")
            )

        for index, offset, category, state in [
            (0, 0, "技术类", "approved"),
            (1, 6, "政策类", "approved"),
            (2, -7, "技术类", "approved"),
            (3, -1, "政策类", "pending"),
            (4, -8, "改革类", "approved"),
        ]:
            Event.objects.create(
                title=f"合成周比较 {index}",
                approved=at(monday + timedelta(days=offset)),
                state=state,
                category=category,
            )
        data = self.post(
            self.use("编辑"), "reports", {"kind": "weekly", "day": str(monday)}
        ).json()
        c = data["content"]["week_comparison"]
        self.assertEqual(
            [c["total"], c["change"], c["start"], c["end"]],
            [1, 1, str(monday - timedelta(days=7)), str(monday - timedelta(days=1))],
        )
        self.assertEqual(
            c["categories"],
            {
                "技术类": {"current": 1, "previous": 1},
                "政策类": {"current": 1, "previous": 0},
            },
        )
        path = f"reports/{data['id']}"
        self.post(
            self.use("签发"), path, {"action": "issue", "version": data["version"]}
        )
        Event.objects.filter(title="合成周比较 3").update(state="approved")
        self.assertEqual(
            self.use("阅读")
            .get("/api/v1/" + path)
            .json()["content"]["week_comparison"],
            c,
        )

    def test_automatic_routing_requires_a_linked_media_and_checks_older_duplicates(
        self,
    ):
        Policy.objects.create(pk=1, enabled=True, threshold=85)
        now = timezone.now()
        older_title = "日报上线地方采编协作工具"
        Event.objects.create(title=older_title, state="approved", approved=now)
        Event.objects.bulk_create(
            [
                Event(title=f"合成历史 {i}", state="approved", approved=now)
                for i in range(501)
            ]
        )
        for index, (title, media) in enumerate(
            [(older_title, self.media), ("媒体改革新业务机制", None)]
        ):
            self.source.media = media
            self.source.save()
            url = f"https://example.org/routing/{index}"
            enqueue("测试", f"routing-{index}")
            with (
                patch("desk.collection.adapter_items", return_value=[(title, url)]),
                patch(
                    "desk.collection.document", return_value=(url, title, "合成内容")
                ),
                patch(
                    "desk.collection.inference",
                    return_value={
                        "score": 99,
                        "relevant": True,
                        "category": "改革类",
                        "summary": title,
                    },
                ),
            ):
                self.assertTrue(work_once())
            self.assertEqual(Article.objects.get(url=url).events.get().state, "pending")
