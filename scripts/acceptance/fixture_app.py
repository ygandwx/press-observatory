"""Test-only transport and seed data. Production processes never import this module."""

import csv
import os
import re
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import django

if (
    os.getenv("POSTGRES_HOST") != "127.0.0.1"
    or not re.fullmatch(r"press_e2e_[0-9a-f]{32}", os.getenv("POSTGRES_DB", ""))
    or os.getenv("DEV_SQLITE") == "1"
):
    raise RuntimeError(
        "Use the disposable PostgreSQL database created by scripts/acceptance/run.py"
    )
django.setup()
from desk import collection, external
from desk.models import Article, Event, Media, Source
from desk.reports import generate
from django.contrib.auth.models import Group, User
from django.core.management import CommandError, call_command
from django.core.wsgi import get_wsgi_application
from django.utils import timezone

ORIGIN = "https://acceptance.invalid"
AUTO_TITLE = "合成日报上线移动采编工具"


def fixture_fetch(url, redirects=3):
    parts = urlsplit(url)
    if parts.hostname != "acceptance.invalid":
        raise external.ExternalError("隔离验收只读取合成来源。")
    if parts.path == "/feed":
        html = (
            f'<a href="/tender-note">合成报社智能采编系统公开招标公告</a><a href="/staff-note">合成报社招聘采编主管</a>'
            f'<a href="/auto">{AUTO_TITLE}</a>'
        )
    elif parts.path in ("/auto", "/entry"):
        title = AUTO_TITLE if parts.path == "/auto" else "合成晚报改版手记"
        html = f"<article><h1>{title}</h1><p>隔离验收专用的合成资料。该机构正式试行新的编辑流程并调整纸质版面。</p></article>"
    else:
        raise external.ExternalError("合成原文不可用。")
    return url, html.encode()


def seed_noise_editions(source, today):
    """Generate two real revisions from older, isolated collection and review records."""
    from desk.models import Change, IntakeDecision, Run
    from desk.noise_archive import archive_noise_weeks

    week = today - timedelta(days=today.weekday() + 21)
    collected = timezone.make_aware(datetime.combine(week, time(9)))
    run = Run.objects.create(
        key="synthetic:archived-noise",
        source=source,
        trigger="隔离历史样本",
        state="done",
        created=collected,
        finished=collected,
    )

    # All three events stay outside active lists and queues used by the other
    # browser cases. Intake decisions supply the dated evidence for this report.
    def capture(slug, title, noise_reason):
        article = Article.objects.create(
            url=ORIGIN + "/quality/" + slug, title=title, source=source
        )
        event = Event.objects.create(title=title, state="archived")
        Event.objects.filter(pk=event.pk).update(created=collected)
        event.articles.add(article)
        intake = IntakeDecision.objects.create(
            run=run,
            event=event,
            source=source,
            url=article.url,
            title=title,
            actor="隔离归档采集员",
            outcome="filtered" if noise_reason else "pending",
            filtered_by="规则过滤" if noise_reason else "",
            noise_reason=noise_reason,
            terms=[noise_reason] if noise_reason else [],
        )
        IntakeDecision.objects.filter(pk=intake.pk).update(at=collected)
        return event, article

    restored, article = capture("restored", "合成归档纠正样本", "广告")
    capture("procurement", "合成归档采购样本", "招标采购")
    returned, _ = capture("returned", "合成归档待核样本", "")
    for n in range(9):
        observation = IntakeDecision.objects.create(
            run=run,
            source=source,
            title=f"合成归档排除公告 {n + 1}",
            url=f"{ORIGIN}/quality/archive-{n}",
            actor="隔离归档采集员",
            outcome="filtered",
            filtered_by="规则过滤",
            noise_reason="招聘",
            terms=["招聘"],
        )
        IntakeDecision.objects.filter(pk=observation.pk).update(at=collected)
    rejected = Change.objects.create(
        event=returned,
        action="reject",
        actor="隔离归档审核员",
        detail={"reason": "需要补充来源出处"},
    )
    Change.objects.filter(pk=rejected.pk).update(at=collected + timedelta(days=1))
    archive_noise_weeks(today=week + timedelta(days=7))

    restored_at = collected + timedelta(days=9)
    correction = IntakeDecision.objects.create(
        event=restored,
        source=source,
        title=restored.title,
        url=article.url,
        outcome="pending",
        filtered_by="人工复核",
        actor="隔离归档审核员",
        analysis={
            "reason": "原文为报社新栏目说明，撤回此前分类",
            "decision": "restore",
        },
    )
    IntakeDecision.objects.filter(pk=correction.pk).update(at=restored_at)
    archive_noise_weeks(today=today)


def seed():
    assert not User.objects.exists() and not Event.objects.exists()
    call_command("bootstrap", verbosity=0)
    for role in ("阅读", "编辑", "审核", "签发", "配置"):
        u = User.objects.create_user(
            "验收" + role, password=os.environ["ACCEPTANCE_PASSWORD"]
        )
        u.groups.add(Group.objects.get(name="社领导" if role == "阅读" else "运营编辑"))
    with tempfile.TemporaryDirectory() as folder:
        csv_path = Path(folder) / "synthetic.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "category", "subcategory", "reference"])
            for name in ("合成甲报", "合成乙报"):
                writer.writerow(
                    [name, "地方党媒体系", "省级党委机关报", ORIGIN + "/entry"]
                )
        call_command("import_media", str(csv_path))
        assert not Media.objects.exists()
        for _ in range(2):
            call_command("import_media", str(csv_path), apply=True)
        assert Media.objects.count() == 2
        csv_path.write_text(
            "name,reference\n合成回滚," + ORIGIN + "/entry\n合成缺出处,\n",
            encoding="utf-8",
        )
        try:
            call_command("import_media", str(csv_path), apply=True)
        except CommandError:
            assert not Media.objects.filter(name="合成回滚").exists()
        else:
            raise AssertionError("Invalid CSV committed")
    paper = Media.objects.get(name="合成甲报")
    paper.verification = {
        "record": {
            "date": "2026-11-18",
            "facts": [
                {
                    "field": "类型",
                    "value": "样本报纸",
                    "url": ORIGIN + "/directory",
                    "basis": "虚构的核验素材，供浏览器验收",
                    "date": "2026-11-18",
                }
            ],
            "checks": {"类型": "样本核对完成"},
            "searches": [],
            "candidates": [],
        }
    }
    paper.save(update_fields=["verification"])
    Source.objects.create(
        name="合成官网",
        media=paper,
        url=ORIGIN + "/feed",
        trusted=True,
        zone="媒体官网",
        profile={"section": "产品与平台"},
    )
    Source.objects.create(
        name="合成人工渠道", media=paper, url=ORIGIN + "/manual", adapter="人工"
    )
    now = timezone.now()
    today = timezone.localdate()
    previous = today - timedelta(days=today.weekday() + 7)
    editor = User.objects.get(username="验收编辑")
    for n in range(26):
        e = Event.objects.create(
            title=f"合成分页事件 {n:02}",
            state="approved",
            category="改革类",
            approved=now,
            occurred=today,
        )
        e.media.add(paper)
        e.articles.add(
            Article.objects.create(
                title=e.title, url=f"{ORIGIN}/article/{n}", status="已核验"
            )
        )
    week_event = Event.objects.create(
        title="合成周报样本",
        state="approved",
        category="技术类",
        approved=now - timedelta(days=today.weekday() + 7),
        occurred=previous,
    )
    week_event.media.add(paper)
    week_event.articles.add(
        Article.objects.create(
            title=week_event.title,
            url=ORIGIN + "/week",
            status="已核验",
            excerpt="合成周报样本：该报社发布生成式引擎优化研究，测量 AI 可见度。",
        )
    )
    for title in ("合成待审一", "合成待审二"):
        e = Event.objects.create(title=title, category="其他类")
        e.articles.add(
            Article.objects.create(
                title=title, url=f"{ORIGIN}/review/{e.pk}", status="已核验"
            )
        )
    print(
        "合成日报 / 周报",
        generate(editor, "daily", today).pk,
        generate(editor, "weekly", previous).pk,
    )
    seed_noise_editions(Source.objects.get(name="合成官网"), today)


# Replace only the outbound source byte transport, leaving parsing, validation,
# model HTTP, database writes, session middleware and worker leases intact.
external.fetch = collection.fetch = fixture_fetch
application = get_wsgi_application()

if __name__ == "__main__":
    import sys

    if sys.argv[1] == "seed":
        seed()
    elif sys.argv[1] == "worker":
        call_command("worker")
