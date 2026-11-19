from datetime import timedelta
from xml.etree import ElementTree

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .dashboard_metrics import stock_comparisons
from .models import Article, Change, Event, IntakeDecision, Media


class DashboardWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap", verbosity=0)
        cls.editor = User.objects.create_user("ui-operator")
        cls.editor.groups.add(Group.objects.get(name="运营编辑"))
        cls.reader = User.objects.create_user("ui-reader")
        cls.reader.groups.add(Group.objects.get(name="社领导"))
        cls.paper = Media.objects.create(
            name="样本报社",
            category="地方党媒体系",
            subcategory="省级党委机关报",
            focus="核心必看",
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.editor)

    def add(self, title, sub, auto=False):
        e = Event.objects.create(
            title=title,
            category="其他类",
            subtype=sub,
            state="approved",
            auto=auto,
            approved=timezone.now(),
            occurred=timezone.localdate(),
            tags=["整合"],
        )
        e.media.add(self.paper)
        return e

    def test_alerts_cards_frozen_graph_and_tag_export(self):
        self.add("头版停刊", "停刊休刊")
        self.add("第一次调整", "机构调整")
        self.add("第二次调整", "机构调整")
        self.add("样本报社营收同比下降", "营收", True)
        stats = self.api.get("/api/v1/dashboard").json()
        self.assertEqual(len(stats["cards"]), 4)
        self.assertEqual(
            (stats["auto_share"]["auto"], stats["auto_share"]["approved"]), (1, 4)
        )
        self.assertEqual(sorted(a["count"] for a in stats["alerts"]), [1, 1, 2])
        for alert in stats["alerts"]:
            self.assertEqual(
                self.api.get("/api/v1/events", alert["filters"]).json()["count"],
                alert["count"],
            )
        query = {
            **stats["period"],
            "region": "未标注",
            "tag": "整合",
            "export": "excel",
        }
        xml = self.api.get("/api/v1/events", query)
        csv = self.api.get("/api/v1/events", {**query, "export": "csv"})
        self.assertEqual(csv.content.count(b"\xef\xbb\xbf"), 1)
        doc = ElementTree.fromstring(xml.content)
        self.assertEqual(
            len(doc.findall(".//{urn:schemas-microsoft-com:office:spreadsheet}Row")), 5
        )
        self.assertEqual(
            self.api.get("/api/v1/events", {"tag": "无此标签"}).json()["count"], 0
        )

    def test_daily_stock_baseline(self):
        day = timezone.localdate()
        stock_comparisons(
            self.editor, day - timedelta(days=1), {"media": 1, "queue": 2}
        )
        result = stock_comparisons(self.editor, day, {"media": 2, "queue": 0})
        self.assertEqual(result["media"]["change"], 1)
        self.assertEqual(result["queue"]["change"], -2)

    def test_metric_observations_and_access(self):
        path = f"/api/v1/media/{self.paper.pk}"
        metric = {
            "action": "metric",
            "kind": "发行量",
            "day": "2024-12-31",
            "value": "100",
            "unit": "份/日",
            "source_url": "https://example.org/report",
        }
        self.assertEqual(self.api.post(path, metric, format="json").status_code, 200)
        self.assertEqual(
            self.api.post(path, {**metric, "value": "110"}, format="json").status_code,
            200,
        )
        rows = self.api.get(path).json()["metrics"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]["value"]), 110)
        self.api.force_authenticate(self.reader)
        self.assertEqual(self.api.post(path, metric, format="json").status_code, 403)

    def test_feedback_and_archived_sources(self):
        e = self.add("本报项目", "", True)
        path = f"/api/v1/events/{e.pk}"
        data = {
            "action": "feedback",
            "decision": "noise",
            "noise_reason": "招标采购",
            "reason": "采购结果原文",
            "version": e.version,
        }
        self.assertEqual(self.api.post(path, data, format="json").status_code, 200)
        e.refresh_from_db()
        self.assertTrue(e.is_noise)
        self.assertEqual(e.state, "filtered")
        self.assertEqual(
            self.api.post(
                path,
                {**data, "decision": "restore", "version": e.version},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(Change.objects.filter(event=e, action="人工复核").count(), 2)
        Event.objects.filter(pk=e.pk).update(state="archived")
        self.assertEqual(self.api.get(path).status_code, 404)

    def test_title_and_tag_alerts_and_malformed_labels(self):
        self.add("报社正式宣布停刊", "")
        self.add("报社澄清停刊传闻", "停刊休刊")
        self.add("报社否认营收下滑", "")
        self.add("报社宣布组织架构调整", "")
        tagged = self.add("编辑部发布公告", "")
        Event.objects.filter(pk=tagged.pk).update(tags=[{"旧值": 1}, 12, "机构调整"])
        bad = self.add("地方报社对外发布报告", "")
        Event.objects.filter(pk=bad.pk).update(tags={"旧值": 1})
        self.assertEqual(
            self.api.get(f"/api/v1/events/{tagged.pk}").json()["tags"], ["机构调整"]
        )
        self.assertEqual(self.api.get(f"/api/v1/events/{bad.pk}").json()["tags"], [])
        payload = self.api.get("/api/v1/dashboard").json()
        self.assertEqual(
            {a["category"]: a["count"] for a in payload["alerts"]},
            {"媒体停刊休刊": 1, "媒体机构调整": 2},
        )
        self.assertEqual(
            self.api.get("/api/v1/events", {"export": "excel"}).status_code, 200
        )

    def test_correct_source_lookup_history_and_permissions(self):
        pending = Event.objects.create(title="待审核原文", state="pending")
        old = Article.objects.create(url="https://example.org/old", title="旧原文")
        pending.articles.add(old)
        path = f"/api/v1/events/{pending.pk}"
        payload = {
            "action": "correct_source",
            "url": "https://example.org/new",
            "article": old.pk,
            "version": pending.version,
        }
        self.api.force_authenticate(self.reader)
        self.assertEqual(self.api.post(path, payload, format="json").status_code, 403)
        self.api.force_authenticate(self.editor)
        other = Event.objects.create(title="另一事件")
        duplicate = Article.objects.create(
            url="https://example.org/duplicate", title="其他事件原文"
        )
        other.articles.add(duplicate)
        self.assertEqual(
            self.api.post(
                path, {**payload, "url": duplicate.url}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(self.api.post(path, payload, format="json").status_code, 200)
        self.assertEqual(
            list(pending.articles.values_list("url", flat=True)), [payload["url"]]
        )
        self.assertTrue(Article.objects.filter(pk=old.pk).exists())
        self.assertTrue(
            Change.objects.filter(
                event=pending, action="更正原文链接", detail__old=old.url
            ).exists()
        )
        self.assertEqual(self.api.post(path, payload, format="json").status_code, 409)

    def test_review_decision_permissions_and_audit(self):
        event = self.add("管理业务", "", True)
        path = f"/api/v1/events/{event.pk}"
        action = {
            "action": "feedback",
            "decision": "noise",
            "reason": "归类为采购公告",
            "noise_reason": "招标采购",
            "version": event.version,
        }
        self.api.force_authenticate(self.reader)
        self.assertEqual(self.api.post(path, action, format="json").status_code, 403)
        self.api.force_authenticate(self.editor)
        self.assertEqual(self.api.post(path, action, format="json").status_code, 200)
        self.assertEqual(IntakeDecision.objects.get(event=event).outcome, "rejected")
        signed = self.add("人工已批准", "", True)
        Change.objects.create(
            actor=self.editor.username, event=signed, action="approve"
        )
        self.assertEqual(
            self.api.post(
                f"/api/v1/events/{signed.pk}",
                {**action, "version": signed.version},
                format="json",
            ).status_code,
            400,
        )
