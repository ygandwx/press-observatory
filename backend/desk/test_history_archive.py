import hashlib
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .models import Article, Change, Event, Report


class HistoricalArchiveTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plan = Path(self.directory.name) / "review.json"
        self.editorial = Event.objects.create(
            title="样报社上线客户端", state="approved", approved=timezone.now()
        )
        self.notice = Event.objects.create(title="样报社设备采购公告")
        self.notice.articles.add(
            Article.objects.create(title=self.notice.title, url="https://example.org/n")
        )
        self.command = "archive_event_batch"

    def run_command(self, mode, digest=""):
        call_command(self.command, mode, self.plan, sha256=digest, stdout=StringIO())

    def preview(self):
        self.run_command("preview")
        return hashlib.sha256(self.plan.read_bytes()).hexdigest()

    def test_archive_replay_and_unarchive_preserve_approval(self):
        digest = self.preview()
        published = self.editorial.approved
        self.run_command("apply", digest)
        self.run_command("apply", digest)
        self.assertEqual(Event.objects.filter(state="archived").count(), 2)
        self.assertEqual(Change.objects.filter(action="历史归档").count(), 2)
        self.assertEqual(Article.objects.count(), 1)
        self.run_command("undo", digest)
        self.run_command("undo", digest)
        self.editorial.refresh_from_db()
        self.assertEqual(
            (self.editorial.state, self.editorial.approved), ("approved", published)
        )
        self.assertEqual(Event.objects.get(pk=self.notice.pk).state, "pending")

    def test_stale_versions_abort_even_after_first_row(self):
        digest = self.preview()
        self.notice.version += 1
        self.notice.save()
        with self.assertRaises(CommandError):
            self.run_command("apply", digest)
        self.assertEqual(Event.objects.filter(state="archived").count(), 0)

    def test_report_added_after_preview_protects_whole_batch(self):
        digest = self.preview()
        Report.objects.create(
            kind="daily",
            day=timezone.localdate(),
            content={"events": [{"id": self.notice.pk}]},
        )
        with self.assertRaises(CommandError):
            self.run_command("apply", digest)
        self.assertFalse(Change.objects.exists())

    def test_noise_preview_ignores_positive_and_retains_human_approval(self):
        self.command = "editorial_history"
        other = Event.objects.create(
            title="样报社中标公告", state="approved", approved=timezone.now()
        )
        digest = self.preview()
        rows = json.loads(self.plan.read_text())["rows"]
        self.assertEqual({r["id"] for r in rows}, {other.pk, self.notice.pk})
        self.assertTrue(next(r for r in rows if r["id"] == other.pk)["protected"])
        self.run_command("apply", digest)
        self.assertEqual(Event.objects.get(pk=self.notice.pk).state, "filtered")
        self.assertEqual(Event.objects.get(pk=other.pk).state, "approved")
        self.run_command("undo", digest)
        self.assertFalse(Event.objects.get(pk=self.notice.pk).is_noise)
