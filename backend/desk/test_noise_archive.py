from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from threading import Event as ThreadEvent
from unittest import skipUnless
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase

from .domain import review
from .models import (
    Article,
    Change,
    Event,
    IntakeDecision,
    Run,
    Source,
    WeeklyNoiseEdition,
)
from .noise_archive import archive_noise_weeks

ZONE = ZoneInfo("Asia/Shanghai")
WEEK = date(2026, 10, 5)


def local(day, hour=12):
    return datetime.combine(day, datetime.min.time(), ZONE).replace(hour=hour)


class NoiseArchiveTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="合成社务栏目", url="https://press.invalid/desk"
        )
        self.n = 0

    def observation(
        self,
        day,
        url,
        *,
        event=None,
        outcome="pending",
        noise="",
        manual=False,
        time=None,
    ):
        self.n += 1
        run = (
            None
            if manual
            else Run.objects.create(key=f"fixture{self.n}", source=self.source)
        )
        d = IntakeDecision.objects.create(
            run=run,
            source=None if manual else self.source,
            event=event,
            title=f"合成稿{self.n}",
            url=url,
            outcome=outcome,
            noise_reason=noise,
            filtered_by="排除词" if noise else "",
            terms=["命中"] if noise else [],
            actor="合成核验者",
        )
        IntakeDecision.objects.filter(pk=d.pk).update(at=time or local(day))
        return d

    def action(self, day, event, outcome, *, noise="", reason="需要校准原始主体"):
        d = IntakeDecision.objects.create(
            event=event,
            source=self.source,
            url="https://press.invalid/a",
            title=event.title,
            outcome=outcome,
            noise_reason=noise,
            filtered_by="人工复核",
            actor="合成审核",
            analysis={"reason": reason},
        )
        IntakeDecision.objects.filter(pk=d.pk).update(at=local(day, 14))
        return d

    def report(self, day=WEEK):
        return WeeklyNoiseEdition.objects.filter(week=day).latest("version")

    def test_first_empty_week_catchup_and_stability(self):
        self.assertEqual(archive_noise_weeks(WEEK), 1)
        report = self.report(WEEK - timedelta(weeks=1))
        self.assertEqual(report.content["coverage"]["status"], "no_evidence")
        self.assertEqual(report.content["total"], 0)
        self.assertEqual(archive_noise_weeks(WEEK), 0)
        self.assertEqual(archive_noise_weeks(WEEK + timedelta(weeks=3)), 3)
        self.assertEqual(WeeklyNoiseEdition.objects.count(), 4)

    def test_shanghai_boundary_dedup_and_unlabelled_gate(self):
        url = "https://press.invalid/a"
        self.observation(
            WEEK,
            url,
            outcome="filtered",
            noise="招聘",
            time=local(WEEK + timedelta(days=6), 23),
        )
        self.observation(
            WEEK,
            url,
            outcome="filtered",
            noise="招聘",
            time=local(WEEK + timedelta(days=6), 23),
        )
        self.observation(WEEK, "https://press.invalid/weak", outcome="filtered")
        self.observation(WEEK, "https://press.invalid/failed", outcome="failed")
        self.observation(WEEK, "https://press.invalid/duplicate", outcome="duplicate")
        self.observation(WEEK, "https://press.invalid/blocked", outcome="blocked")
        self.observation(
            WEEK + timedelta(weeks=1),
            "https://press.invalid/current",
            noise="广告",
            outcome="filtered",
            time=local(WEEK + timedelta(weeks=1), 0),
        )
        self.assertEqual(archive_noise_weeks(WEEK + timedelta(weeks=1)), 1)
        c = self.report().content
        self.assertEqual((c["total"], c["noise"], c["returned"]), (2, 1, 0))
        self.assertEqual(len(c["negative_samples"]), 1)
        self.assertEqual(
            c["reasons"], [{"noise_reason": "招聘", "count": 1, "percent": 100.0}]
        )
        self.assertEqual(self.report().version, 1)

    def test_manual_and_unknown_legacy_do_not_distort_source_rank(self):
        for i in range(10):
            self.observation(
                WEEK,
                f"https://press.invalid/{i}",
                outcome="filtered" if i < 8 else "pending",
                noise="招标采购" if i < 8 else "",
            )
        for i in range(15):
            self.observation(
                WEEK,
                f"https://manual.invalid/{i}",
                outcome="filtered",
                noise="广告",
                manual=True,
            )
        archive_noise_weeks(WEEK + timedelta(weeks=1))
        c = self.report().content
        self.assertEqual(
            (c["total"], c["noise"], c["manual"], c["captured"]), (25, 23, 15, 10)
        )
        self.assertEqual(
            [(r["source_id"], r["total"], r["rate"]) for r in c["top_sources"]],
            [(self.source.pk, 10, 80.0)],
        )
        self.assertEqual(len(c["sources"]), 2)
        for item in c["negative_samples"]:
            self.assertIn("source_key", item)
            self.assertIsNone(item["last_review"])

    def test_cross_week_corrections_create_immutable_revisions(self):
        event = Event.objects.create(title="合成自有项目", created=local(WEEK))
        first = self.observation(WEEK, "https://press.invalid/a", event=event)
        archive_noise_weeks(WEEK + timedelta(weeks=1))
        initial = self.report()
        self.action(WEEK + timedelta(days=9), event, "rejected")
        self.assertEqual(archive_noise_weeks(WEEK + timedelta(days=10)), 1)
        returned = self.report().content
        self.assertEqual((returned["noise"], returned["returned"]), (0, 1))
        self.assertEqual(returned["negative_samples"][0]["original"]["id"], first.pk)
        self.action(WEEK + timedelta(days=11), event, "rejected", noise="第三方新闻")
        archive_noise_weeks(WEEK + timedelta(days=12))
        self.assertEqual(self.report().content["noise"], 1)
        self.action(WEEK + timedelta(days=13), event, "pending")
        archive_noise_weeks(WEEK + timedelta(days=13))
        final = self.report()
        self.assertEqual(final.version, 4)
        self.assertEqual(final.content["negative_samples"], [])
        self.assertEqual(final.content["corrections"][-1]["after"], "clean")
        self.assertEqual(
            final.content["corrections"][-1]["before_reason"], "第三方新闻"
        )
        initial.refresh_from_db()
        self.assertEqual(initial.content["corrections"], [])
        self.assertEqual(archive_noise_weeks(WEEK + timedelta(days=13)), 0)

    def test_prefetch_gate_attributed_to_later_event_and_correction(self):
        link = "https://press.invalid/gate"
        self.observation(WEEK, link, outcome="filtered", noise="招标采购")
        archive_noise_weeks(WEEK + timedelta(weeks=1))
        old = self.report()
        event = Event.objects.create(
            title="后补确认为媒体技术新闻", created=local(WEEK + timedelta(days=8))
        )
        self.observation(WEEK + timedelta(days=8), link, event=event)
        self.action(
            WEEK + timedelta(days=9), event, "confirmed", reason="实际为已上线产品报道"
        )
        archive_noise_weeks(WEEK + timedelta(days=10))
        final = self.report().content
        self.assertEqual(
            (final["total"], final["noise"], final["negative_samples"]), (1, 0, [])
        )
        correction = final["corrections"][-1]
        self.assertEqual(
            (
                correction["event_id"],
                correction["review"]["authority"],
                correction["before_reason"],
            ),
            (event.pk, "human", "招标采购"),
        )
        self.assertIsNone(old.content["negative_samples"][0]["event_id"])
        self.assertEqual(
            [r["authority"] for r in correction["history"]], ["automatic", "human"]
        )
        self.assertEqual(archive_noise_weeks(WEEK + timedelta(weeks=1)), 0)
        self.assertEqual(self.report().content["noise"], 0)

    def test_legacy_review_has_evidence_and_archiving_is_not_a_label(self):
        negative = Event.objects.create(
            title="历史候选", created=local(WEEK), state="archived"
        )
        a = Article.objects.create(
            url="https://archive.invalid/old", title=negative.title, source=self.source
        )
        negative.articles.add(a)
        old = Change.objects.create(
            event=negative,
            action="reject",
            actor="历史审核人",
            detail={"reason": "核对执行主体"},
        )
        Change.objects.filter(pk=old.pk).update(at=local(WEEK + timedelta(days=3)))
        other = Event.objects.create(
            title="正常归档", created=local(WEEK), state="archived"
        )
        Change.objects.create(event=other, action="历史归档", actor="历史归档命令")
        archive_noise_weeks(WEEK + timedelta(weeks=1))
        c = self.report().content
        self.assertEqual((c["total"], c["returned"], c["noise"]), (1, 1, 0))
        item = c["negative_samples"][0]
        self.assertEqual((item["origin"], item["url"]), ("legacy", a.url))
        self.assertEqual(
            (
                item["last_review"]["record_type"],
                item["last_review"]["id"],
                item["last_review"]["reason"],
            ),
            ("Change", old.pk, "核对执行主体"),
        )

    def test_regular_review_writes_durable_feedback_and_counts_only_once(self):
        user = User.objects.create_superuser(username="fixture", password=None)
        event = Event.objects.create(title="合成新闻", created=local(WEEK))
        article = Article.objects.create(
            title="合成新闻", url="https://press.invalid/news", source=self.source
        )
        event.articles.add(article)
        self.observation(WEEK, article.url, event=event)
        review(
            user,
            [{"id": event.pk, "version": event.version}],
            "reject",
            "主办单位需补证",
        )
        log = IntakeDecision.objects.get(analysis__decision="reject")
        self.assertEqual(
            (log.url, log.source_id, log.noise_reason),
            (article.url, self.source.pk, ""),
        )
        change = Change.objects.get(action="reject")
        self.assertEqual(change.detail["intake_decision"], log.pk)
        at = local(WEEK + timedelta(days=5))
        IntakeDecision.objects.filter(pk=log.pk).update(at=at)
        Change.objects.filter(pk=change.pk).update(at=at)
        archive_noise_weeks(WEEK + timedelta(weeks=1))
        c = self.report().content
        self.assertEqual(
            (c["total"], c["returned"], len(c["negative_samples"][0]["history"])),
            (1, 1, 1),
        )


class ConcurrentNoiseArchiveTests(TransactionTestCase):
    def test_two_simultaneous_first_runs_create_one_edition(self):
        start = Barrier(2)

        def create():
            close_old_connections()
            try:
                start.wait(timeout=10)
                return archive_noise_weeks(WEEK)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda unused: create(), range(2)))
        self.assertEqual(sorted(result), [0, 1])
        self.assertEqual(WeeklyNoiseEdition.objects.count(), 1)

    @skipUnless(
        connection.vendor == "postgresql", "PostgreSQL repeatable-read snapshot"
    )
    def test_a_review_committed_mid_archive_is_read_by_the_next_edition(self):
        source = Source.objects.create(
            name="范围样本", url="https://press.invalid/column"
        )
        item = Event.objects.create(title="合成待审条目", created=local(WEEK))
        run = Run.objects.create(source=source, key="weekly-snapshot-test")
        d = IntakeDecision.objects.create(
            source=source,
            run=run,
            event=item,
            url="https://press.invalid/snapshot",
            outcome="pending",
            actor="测试",
        )
        IntakeDecision.objects.filter(pk=d.pk).update(at=local(WEEK))
        ready, committed = ThreadEvent(), ThreadEvent()

        def write_review():
            close_old_connections()
            try:
                if not ready.wait(10):
                    raise AssertionError("Archive did not read initial intake data")
                with transaction.atomic():
                    c = Change.objects.create(
                        event_id=item.pk,
                        action="reject",
                        actor="合成审核",
                        detail={"reason": "等待核实"},
                    )
                    Change.objects.filter(pk=c.pk).update(
                        at=local(WEEK + timedelta(days=5))
                    )
                committed.set()
            finally:
                close_old_connections()

        def pause_before_changes(execute, sql, params, many, context):
            if 'FROM "desk_change"' in sql and not ready.is_set():
                ready.set()
                if not committed.wait(10):
                    raise AssertionError(
                        "Archive lock incorrectly blocked the live review"
                    )
            return execute(sql, params, many, context)

        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(write_review)
            with connection.execute_wrapper(pause_before_changes):
                archive_noise_weeks(WEEK + timedelta(weeks=1))
            writer.result()
        first = WeeklyNoiseEdition.objects.get(week=WEEK)
        self.assertEqual(first.content["negative_count"], 0)
        self.assertEqual(archive_noise_weeks(WEEK + timedelta(weeks=1)), 1)
        self.assertEqual(
            WeeklyNoiseEdition.objects.get(week=WEEK, version=2).content["returned"], 1
        )
