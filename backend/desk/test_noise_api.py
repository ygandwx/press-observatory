import csv
from datetime import date
from io import StringIO

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .models import WeeklyNoiseEdition


class WeeklyNoiseAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap", stdout=StringIO())
        for name, role in (("operator", "运营编辑"), ("leader", "社领导")):
            user = User.objects.create_user(username=name)
            user.groups.add(Group.objects.get(name=role))
            setattr(cls, name, user)
        cls.week = date(2026, 6, 1)
        evidence = {
            "record_type": "decision",
            "id": 7,
            "at": "2026-06-01T10:00:00+08:00",
            "actor": "fixture",
            "outcome": "filtered",
            "gate": "排除词",
            "reason": "采购",
            "terms": ["采购"],
        }
        source = {
            "source_key": "feed:1",
            "source__name": "示例栏目",
            "total": 10,
            "noise": 9,
            "rate": 90,
            "returned": 0,
            "negative_count": 9,
        }
        cls.sample = {
            "key": "candidate:7",
            "event_id": None,
            "title": "=采购测试",
            "url": "https://example.invalid/tender",
            "source_name": "示例栏目",
            "origin": "采集",
            "classification": "noise",
            "noise_reason": "招标采购",
            "original": evidence,
            "last_review": None,
            "history": [evidence],
        }
        cls.contents = {
            "week": "2026-06-01",
            "through": "2026-06-07",
            "total": 10,
            "noise": 9,
            "noise_rate": 90,
            "returned": 0,
            "negative_count": 9,
            "coverage": {"note": "隔离样本"},
            "reasons": [{"noise_reason": "招标采购", "count": 9, "percent": 100}],
            "sources": [source],
            "top_sources": [source],
            "negative_samples": [
                {
                    **cls.sample,
                    "key": f"candidate:{n}",
                    "url": f"https://example.invalid/tender/{n}",
                }
                for n in range(9)
            ],
            "corrections": [],
        }
        WeeklyNoiseEdition.objects.create(
            week=cls.week, version=1, digest="1" * 64, content=cls.contents
        )
        WeeklyNoiseEdition.objects.create(
            week=cls.week,
            version=2,
            digest="2" * 64,
            content={
                **cls.contents,
                "negative_samples": [],
                "negative_count": 0,
                "noise": 0,
                "noise_rate": 0,
                "reasons": [],
                "top_sources": [],
                "sources": [{**source, "noise": 0, "rate": 0, "negative_count": 0}],
            },
        )
        cls.path = "/api/v1/monitor/noise-weeks"

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.operator)

    def test_list_reads_latest_and_exports_from_the_exact_version(self):
        rows = self.client.get(self.path).json()
        self.assertEqual(
            (
                rows["count"],
                rows["items"][0]["version"],
                rows["items"][0]["sample_count"],
            ),
            (1, 2, 0),
        )
        path = self.path + "/2026-06-01"
        detail = self.client.get(path).json()
        self.assertEqual((detail["versions"], detail["digest"]), ([2, 1], "2" * 64))

        def export(v, kind):
            res = self.client.get(path, {"version": v, "export": kind})
            self.assertEqual(res.status_code, 200)
            self.assertIn(f"v{v}.csv", res["Content-Disposition"])
            self.assertIn("no-store", res["Cache-Control"])
            return list(csv.reader(StringIO(res.content.decode("utf-8-sig"))))

        data = export(1, "samples")
        self.assertEqual(len(data), 10)
        self.assertEqual(dict(zip(data[0], data[1]))["标题"], "'=采购测试")
        self.assertEqual(len(export(2, "samples")), 1)
        report = export(1, "report")
        self.assertIn("噪音类型", {row[4] for row in report[1:]})
        self.assertEqual(next(row for row in report[1:] if row[4] == "来源")[-2], "1")

    def test_private_scope_validation_and_read_only_routes(self):
        path = self.path + "/2026-06-01"
        for query, code in [
            ({"version": 0}, 400),
            ({"version": 3}, 404),
            ({"export": "unknown"}, 400),
        ]:
            self.assertEqual(self.client.get(path, query).status_code, code)
        self.assertEqual(self.client.get(self.path + "/2026-06-02").status_code, 400)
        self.assertEqual(self.client.get(self.path + "/2026-06-08").status_code, 404)
        self.assertEqual(self.client.post(path, {}).status_code, 405)
        self.leader.user_permissions.add(Permission.objects.get(content_type__app_label="desk", codename="edit"))
        self.client.force_login(self.leader)
        for endpoint in (
            self.path,
            path,
            path + "?version=1&export=samples",
            path + "?export=report",
        ):
            self.assertEqual(self.client.get(endpoint).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(path + "?export=samples").status_code, 403)
