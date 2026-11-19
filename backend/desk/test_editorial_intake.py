"""Synthetic, offline cases for scope, editorial judgment and monitoring."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from . import collection, editorial
from .coverage import noise_report
from .external import ExternalError
from .models import Article, Event, IntakeDecision, Media, Policy, Rule, Run, Source


class EditorialEvidenceTests(SimpleTestCase):
    def test_geo_boundaries(self):
        self.assertEqual(
            editorial.geo_terms("GEO 与生成式 搜索优化，再谈geo"),
            ["GEO", "生成式搜索优化"],
        )
        for text in ("geopolitics", "AGEO", "GEO3", "2019geo", "CHAIR"):
            self.assertFalse(editorial.is_geo(text))

    def test_technical_procurement_and_incidental_mention(self):
        for title in (
            "甲报社新闻客户端公有云采购项目中标公告",
            "甲日报GEO技术项目询价函",
            "甲日报社媒体融合平台招标代理机构征集公告",
            "甲报社AI试验平台已上线项目招标公告",
        ):
            self.assertEqual(editorial.assess(title)["reason"], "招标采购")
        title = "甲日报上线自研GEO传播分析平台"
        for body in (
            "项目由报社采编团队自主开发，曾经采购通用服务器。",
            "新的研究工具正式投入运行，团队介绍了前期招标情况。",
        ):
            self.assertFalse(editorial.assess(title, body, exclusions=["采购"])["gate"])
        self.assertEqual(
            editorial.assess(title, "采购人信息：甲日报社。投标截止时间：本月。")[
                "reason"
            ],
            "招标采购",
        )
        self.assertEqual(
            editorial.assess(title, url="https://press.invalid/caigou/x")["gate"],
            "信源黑名单",
        )

    def test_people_awards_and_reporting_attribution(self):
        self.assertFalse(editorial.assess("甲日报社任命新总编辑")["gate"])
        self.assertEqual(editorial.assess("甲日报社招聘采编主管")["reason"], "招聘")
        self.assertFalse(editorial.assess("甲日报社获评全国报业创新奖")["gate"])
        self.assertEqual(
            editorial.assess("甲日报社推荐参评新闻奖作品公示")["gate"], "参评非获奖结果"
        )
        self.assertEqual(
            editorial.assess("甲日报报道：乙医院上线AI问诊")["reason"], "第三方新闻"
        )
        self.assertFalse(editorial.assess("甲日报社停刊启事")["gate"])

    def test_hidden_keywords_cannot_starve_low_budget(self):
        s = Source(
            name="测试社内动态",
            zone="媒体官网",
            url="https://press.invalid/news",
            profile={"subject_terms": ["甲报社"], "limit": 1},
        )
        links = [
            ("其他部门上线新名单", "https://press.invalid/unrelated"),
            ("甲报社技术平台公开招标公告", "https://press.invalid/bid"),
            ("甲报社年度值班安排", "https://press.invalid/notice"),
            ("甲报社上线新的采编平台", "https://press.invalid/live"),
        ]
        self.assertEqual(collection.ordered_items(links, s, 1), [links[-1]])

    def test_verified_source_sections_and_blocklist(self):
        s = Source(
            url="https://example.invalid/news",
            name="综合新闻",
            zone="媒体官网",
            profile={},
        )
        self.assertTrue(editorial.source_block(s))
        s.profile = {"section": "融媒动态"}
        self.assertFalse(editorial.source_block(s))
        s.url = "https://example.invalid/careers/list"
        self.assertTrue(editorial.source_block(s))
        s.url = "https://example.invalid/policy"
        s.zone = "监管平台"
        s.profile = {"section": "监管发布"}
        self.assertFalse(editorial.source_block(s))
        s.profile = {"section": "协会动态"}
        self.assertTrue(editorial.source_block(s))

    def test_model_invalid_date_conflict_and_unusable_payload(self):
        base = dict(
            category="技术类",
            relevance_score=88,
            is_noise=False,
            subject_type="媒体自身",
            event_nature="技术 AI",
            summary="上线摘要",
            ai_reasoning="确认为媒体发布",
            noise_reason="",
        )
        for delta in (
            {"occurred": "not-a-date"},
            {"noise_reason": "招聘"},
            {"relevance_score": True},
            {"subject_type": ""},
        ):
            with (
                patch.object(collection, "analyze", return_value={**base, **delta}),
                self.assertRaises(ExternalError),
            ):
                collection.inference("甲日报上线采编平台", "报道正文")


class IntakeRoutingTests(TestCase):
    def setUp(self):
        self.org = Media.objects.create(name="合成报社", category="地方党媒体系")
        self.src = Source.objects.create(
            name="合成报社·社内动态",
            url="https://press.invalid/news",
            zone="媒体官网",
            media=self.org,
            trusted=True,
        )
        self.policy = Policy.objects.create(pk=1, enabled=True, ai_enabled=True)
        self.analysis = dict(
            subject_type="媒体自身",
            event_nature="技术 AI",
            relevance_score=98,
            is_noise=False,
            noise_reason="",
            ai_reasoning="此为本社产品发布。",
            category="技术类",
            summary="该机构正式推出合成平台。",
            occurred="2026-11-18",
        )

    def collect(
        self,
        title,
        tail,
        analysis=None,
        body="本社已经完成开发并启用新的内容生产平台。",
    ):
        link = "https://press.invalid/" + tail
        pk = collection.enqueue("测试", "unit-" + tail, [self.src.pk])[0]
        with (
            patch("desk.collection.adapter_items", return_value=[(title, link)]),
            patch("desk.collection.document", return_value=(link, title, body)) as doc,
            patch(
                "desk.collection.inference",
                return_value=self.analysis if analysis is None else analysis,
            ) as model,
        ):
            self.assertTrue(collection.work_once())
        run = Run.objects.get(pk=pk)
        self.assertEqual(run.state, "done", run.message)
        return run, doc, model

    def test_source_role_dates_and_closed_ai(self):
        for i, (title, actor, day, expected) in enumerate(
            (
                ("合成报社上线自研智能采编平台", "媒体自身", "2026-11-18", "approved"),
                ("甲行业协会发布传媒改革约定", "传媒行业", "2026-11-16", "pending"),
                ("主管部门发布媒体融合政策", "政府主管部门", "2026-11-14", "pending"),
                ("乙日报完成报网整合与重组", "媒体自身", "", "pending"),
            )
        ):
            r, _, _ = self.collect(
                title, str(i), {**self.analysis, "subject_type": actor, "occurred": day}
            )
            self.assertEqual(r.intakedecision_set.get().event.state, expected)
        self.policy.ai_enabled = False
        self.policy.save()
        r, _, model = self.collect("合成报社任命新的总编辑", "chief")
        self.assertFalse(model.called)
        self.assertEqual(r.intakedecision_set.get().outcome, "pending")

    def test_precise_block_and_manual_feedback(self):
        r, doc, model = self.collect("合成报社采购AI编辑系统招标公告", "notice")
        self.assertFalse(doc.called)
        self.assertFalse(model.called)
        log = r.intakedecision_set.get()
        self.assertEqual(log.noise_reason, "招标采购")
        self.assertTrue(log.terms)
        r, _, _ = self.collect("合成报社正式上线独立客户端", "app")
        e = r.intakedecision_set.get().event
        IntakeDecision.objects.create(
            event=e,
            source=self.src,
            url="https://press.invalid/app",
            outcome="rejected",
            noise_reason="第三方新闻",
            actor="测试审核者",
        )
        report = noise_report(1)
        self.assertEqual(
            (report["total"], report["noise"], report["feedback"]), (2, 2, 1)
        )

    def test_archived_url_is_duplicate_but_new_article_is_eligible(self):
        title = "合成报社上线自己的AI平台"
        old = Article.objects.create(title=title, url="https://press.invalid/archived")
        event = Event.objects.create(title=title, state="archived")
        event.articles.add(old)
        r, doc, model = self.collect(title, "archived")
        self.assertFalse(doc.called)
        self.assertFalse(model.called)
        event.refresh_from_db()
        self.assertEqual(event.state, "archived")
        self.assertEqual(r.counts["重复"], 1)
        r, _, _ = self.collect(title, "fresh")
        self.assertEqual(r.intakedecision_set.get().outcome, "approved")

    def test_reclaimed_run_preserves_decision_and_counters(self):
        title = "合成报社上线自研技术平台"
        run, _, _ = self.collect(title, "reclaim")
        previous = run.counts.copy()
        run.state = "running"
        run.expires = timezone.now() - timedelta(minutes=1)
        run.save()
        candidate = (title, "https://press.invalid/reclaim")
        with (
            patch.object(collection, "adapter_items", return_value=[candidate]),
            patch.object(collection, "document") as doc,
        ):
            self.assertTrue(collection.work_once())
        doc.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.counts, previous)
        self.assertEqual(run.intakedecision_set.get().outcome, "approved")

    def test_latest_restore_clears_feedback_without_counting_new_capture(self):
        run, _, _ = self.collect("合成报社发布采编软件", "review")
        e = run.intakedecision_set.get().event
        for outcome, reason in (("filtered", "其他"), ("pending", "")):
            IntakeDecision.objects.create(
                source=self.src,
                event=e,
                filtered_by="人工复核",
                outcome=outcome,
                noise_reason=reason,
                actor="合成编辑",
            )
        stats = noise_report(1)
        self.assertEqual((stats["total"], stats["noise"], stats["feedback"]), (1, 0, 2))

    def test_weekly_throttle_uses_real_distinct_observations(self):
        self.src.scheduled = True
        self.src.save()
        now = datetime(2026, 11, 18, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        for i in range(10):
            run = Run.objects.create(source=self.src, key=f"sample-{i}", trigger="测试")
            row = IntakeDecision.objects.create(
                run=run,
                source=self.src,
                outcome="filtered" if i < 8 else "pending",
                url=f"https://press.invalid/observed/{i}",
                noise_reason="招标采购" if i < 8 else "",
                actor="测试",
            )
            IntakeDecision.objects.filter(pk=row.pk).update(at=now - timedelta(hours=2))
        self.assertEqual(collection.schedule(now), [])
        self.assertEqual(noise_report(7, now.date())["sources"][0]["rate"], 80)
        self.assertEqual(len(collection.schedule(now + timedelta(days=5))), 1)
        self.src.refresh_from_db()
        self.assertNotIn("cadence", self.src.profile)

    def test_disabled_keyword_applies_to_its_media_class(self):
        Rule.objects.create(
            word="独家信号",
            kind="动作词",
            enabled=True,
            media_category=self.org.category,
        )
        r, _, _ = self.collect("合成报社独家信号", "custom")
        self.assertEqual(r.intakedecision_set.get().outcome, "approved")
        Rule.objects.filter(word="独家信号").update(enabled=False)
        r, doc, _ = self.collect("合成报社独家信号", "custom-off")
        self.assertFalse(doc.called)
        self.assertEqual(r.intakedecision_set.get().filtered_by, "双命中不足")
