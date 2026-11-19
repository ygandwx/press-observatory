from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError

from .domain import review
from .models import Article, Edition, Event, Report
from .reports import build_content, generate, report_action, schedule_weekly

WEEK = date(2026, 11, 9)
NEXT_WEEK = date(2026, 11, 16)
MONDAY_0800 = "2026-11-16T00:00:00Z"
MONDAY_1800 = "2026-11-16T10:00:00Z"


def at_utc(instant):
    return patch(
        "django.utils.timezone.now", return_value=datetime.fromisoformat(instant)
    )


def timer_tick(instant):
    with at_utc(instant):
        call_command("enqueue", stdout=StringIO())


def approved(title, instant, **fields):
    return Event.objects.create(
        title=title,
        state="approved",
        approved=datetime.fromisoformat(instant),
        **fields,
    )


def ids(content):
    return [row["id"] for row in content["events"]]


class WeeklyBoundariesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap", verbosity=0, stdout=StringIO())
        cls.editor = User.objects.create_user("weekly-boundaries-editor")
        cls.editor.groups.add(Group.objects.get(name="运营编辑"))

    def test_utc_instants_straddling_both_beijing_week_boundaries(self):
        self.assertEqual(settings.TIME_ZONE, "Asia/Shanghai")
        self.assertTrue(settings.USE_TZ)
        approved("上周日最后一微秒", "2026-11-08T15:59:59.999999Z")
        first = approved(
            "本周一零点", "2026-11-08T16:00:00Z", occurred=date(2025, 1, 1)
        )
        last = approved("本周日最后一微秒", "2026-11-15T15:59:59.999999Z")
        approved("下周一零点", "2026-11-15T16:00:00Z", occurred=WEEK)

        with at_utc(MONDAY_0800):
            content = build_content("weekly", date(2026, 11, 12))
        self.assertEqual(
            (content["start"], content["end"]), ("2026-11-09", "2026-11-15")
        )
        self.assertCountEqual(ids(content), [first.pk, last.pk])
        self.assertEqual(content["snapshot"]["本期入库"], 2)
        self.assertEqual(content["week_comparison"]["total"], 1)

    def test_sunday_stays_open_until_midnight_and_first_regular_tick_is_monday_0800(
        self,
    ):
        event = approved("报社周日发布", "2026-11-15T09:00:00Z")
        timer_tick("2026-11-15T10:00:00Z")  # Sunday 18:00 in Beijing.
        with (
            at_utc("2026-11-15T15:59:59.999999Z"),
            self.assertRaisesMessage(ValidationError, "已结束"),
        ):
            generate(self.editor, "weekly", WEEK)
        self.assertFalse(Report.objects.exists())

        with at_utc("2026-11-15T16:00:00Z"):
            self.assertEqual(ids(build_content("weekly", WEEK)), [event.pk])
        self.assertFalse(Report.objects.exists())
        timer_tick(MONDAY_0800)
        report = Report.objects.get(kind="weekly", day=WEEK)
        self.assertEqual((report.state, ids(report.content)), ("draft", [event.pk]))
        self.assertIsNone(report.issued)
        self.assertIsNone(report.share_token)
        self.assertFalse(Edition.objects.exists())

    def test_late_first_approval_belongs_to_next_week_even_for_older_event(self):
        original = approved("上周已批准事件", "2026-11-12T03:00:00Z")
        late = Event.objects.create(
            title="日报社推出融媒体平台", occurred=date(2026, 11, 15)
        )
        late.articles.add(
            Article.objects.create(
                title=late.title, url="https://example.invalid/weekly", status="已核验"
            )
        )
        timer_tick(MONDAY_0800)
        with at_utc("2026-11-17T01:00:00Z"):
            review(self.editor, [{"id": late.pk, "version": late.version}], "approve")
            self.assertEqual(ids(build_content("weekly", WEEK)), [original.pk])
        timer_tick("2026-11-23T00:00:00Z")
        later = Report.objects.get(kind="weekly", day=NEXT_WEEK)
        self.assertEqual(ids(later.content), [late.pk])
        self.assertEqual(later.content["week_comparison"]["total"], 1)
        self.assertEqual(ids(Report.objects.get(day=WEEK).content), [original.pk])

    def test_empty_week_can_retry_after_an_earlier_approval_is_imported(self):
        parent = approved("更早一周的主事件", "2026-11-04T04:00:00Z")
        approved("已归并事件", "2026-11-10T04:00:00Z", merged_into=parent)
        approved("噪声事件", "2026-11-12T04:00:00Z", is_noise=True)
        Event.objects.create(title="待审稿", state="pending", occurred=WEEK)
        Event.objects.create(title="批准日期缺失", state="approved", occurred=WEEK)
        timer_tick(MONDAY_0800)
        with (
            at_utc(MONDAY_0800),
            self.assertRaisesMessage(ValidationError, "尚无已批准事件"),
        ):
            generate(self.editor, "weekly", WEEK)
        self.assertFalse(Report.objects.exists())

        imported = approved("补录带上周批准时间的历史记录", "2026-11-13T02:00:00Z")
        timer_tick(MONDAY_1800)
        report = Report.objects.get()
        self.assertEqual((report.day, ids(report.content)), (WEEK, [imported.pk]))

    def test_repeated_ticks_preserve_manual_draft_and_signed_editions(self):
        original = approved("签发依据", "2026-11-13T03:00:00Z")
        timer_tick(MONDAY_0800)
        report = Report.objects.get()
        report = report_action(
            self.editor,
            report.pk,
            "edit",
            {"version": report.version, "headline": "值班编辑核定稿"},
        )
        approved("草稿生成后才补录的上周记录", "2026-11-13T08:00:00Z")
        for state, action, tick in [
            ("draft", None, MONDAY_1800),
            ("pending", "submit", "2026-11-17T00:00:00Z"),
            ("published", "issue", "2026-11-17T10:00:00Z"),
            ("archived", "archive", "2026-11-22T10:00:00Z"),
        ]:
            with self.subTest(state=state), at_utc(tick):
                if action:
                    report = report_action(
                        self.editor, report.pk, action, {"version": report.version}
                    )
                before = (report.version, report.updated, report.content)
                call_command("enqueue", stdout=StringIO())
                report.refresh_from_db()
                self.assertEqual(
                    (report.version, report.updated, report.content), before
                )
                self.assertEqual(
                    (report.state, report.content["headline"], ids(report.content)),
                    (state, "值班编辑核定稿", [original.pk]),
                )
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(Edition.objects.get().content, report.content)

    def test_recovery_only_targets_latest_finished_week(self):
        old = approved("停机前一周", "2026-11-13T01:00:00Z")
        recent = approved("最近结束的自然周", "2026-11-18T01:00:00Z")
        timer_tick("2026-11-23T10:00:00Z")
        only = Report.objects.get()
        self.assertEqual((only.day, ids(only.content)), (NEXT_WEEK, [recent.pk]))
        with at_utc("2026-11-23T11:00:00Z"):
            older = generate(self.editor, "weekly", WEEK)
        self.assertEqual(ids(older.content), [old.pk])


class ConcurrentWeeklyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_ticks_persist_only_one_complete_draft(self):
        event = approved("同时投递的依据", "2026-11-12T03:00:00Z")
        gate = Barrier(2, timeout=10)

        def tick():
            try:
                gate.wait()
                result = schedule_weekly()
                return result.pk if result else None
            finally:
                connections.close_all()

        with at_utc(MONDAY_0800), ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(tick) for _ in range(2)]
            observed = {task.result(timeout=20) for task in tasks}
        report = Report.objects.get()
        self.assertEqual(observed - {None}, {report.pk})
        self.assertEqual(
            (report.day, report.state, ids(report.content)), (WEEK, "draft", [event.pk])
        )
        self.assertFalse(Edition.objects.exists())
