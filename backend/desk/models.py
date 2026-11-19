import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from .catalog import PERMISSIONS


class Media(models.Model):
    origin_id = models.UUIDField(null=True, unique=True, editable=False)
    origin_record = models.JSONField(default=dict, blank=True, editable=False)
    verification = models.JSONField(default=dict, blank=True, editable=False)
    description = models.TextField(blank=True)
    archive_status = models.CharField(max_length=40, blank=True)
    high_priority = models.BooleanField(default=False)
    name = models.CharField(max_length=200, unique=True)
    owner = models.CharField(max_length=240, blank=True)
    level = models.CharField(max_length=40, blank=True)
    category = models.CharField(max_length=80, blank=True)
    subcategory = models.CharField(max_length=80, blank=True)
    region = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=40, default="报纸", blank=True)
    focus = models.CharField(max_length=40, default="常规参照", blank=True)
    tags = models.JSONField(default=list)
    channels = models.JSONField(default=dict)
    digest_section = models.CharField(max_length=40, blank=True)
    reference = models.URLField(max_length=1500, blank=True)
    updated = models.DateTimeField(auto_now=True)
    followers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="followed_media"
    )

    class Meta:
        permissions = PERMISSIONS


class Source(models.Model):
    zone = models.CharField(max_length=40, blank=True)
    stop_reason = models.CharField(max_length=240, blank=True)
    archived = models.BooleanField(default=False)
    name = models.CharField(max_length=200, unique=True)
    media = models.ForeignKey(Media, null=True, on_delete=models.SET_NULL)
    url = models.URLField(max_length=1500)
    adapter = models.CharField(max_length=30, default="html")
    trusted = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    level = models.CharField(max_length=50, default="补充信源")
    profile = models.JSONField(default=dict, blank=True)
    scheduled = models.BooleanField(default=False)
    coverage_media = models.ManyToManyField(
        Media, blank=True, related_name="coverage_sources"
    )


class CrawlHost(models.Model):
    host = models.CharField(max_length=253, primary_key=True)
    last_request = models.DateTimeField(null=True)
    hold_until = models.DateTimeField(null=True)
    spacing = models.FloatField(default=2)


class Rule(models.Model):
    media_category = models.CharField(max_length=80, blank=True)
    word = models.CharField(max_length=100)
    kind = models.CharField(max_length=20)
    category = models.CharField(max_length=40, blank=True)
    enabled = models.BooleanField(default=True)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["word", "kind"], name="unique_keyword_role")
        ]


class Policy(models.Model):
    ai_enabled = models.BooleanField(default=True)
    category_targets = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False)
    threshold = models.PositiveIntegerField(default=80)
    low = models.PositiveIntegerField(default=40)


class Article(models.Model):
    url = models.URLField(max_length=1800, unique=True)
    source = models.ForeignKey(Source, null=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=500)
    published = models.DateField(null=True)
    status = models.CharField(max_length=30, default="待核验")
    checked = models.DateTimeField(null=True)
    excerpt = models.TextField(blank=True)


class Event(models.Model):
    subject_type = models.CharField(max_length=32, blank=True)
    event_nature = models.CharField(max_length=32, blank=True)
    is_noise = models.BooleanField(default=False)
    noise_reason = models.CharField(max_length=40, blank=True)
    filtered_by = models.CharField(max_length=40, blank=True)
    source_zone = models.CharField(max_length=80, blank=True)
    capture_method = models.CharField(max_length=30, blank=True)
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True)
    media = models.ManyToManyField(Media, related_name="events")
    articles = models.ManyToManyField(Article, related_name="events")
    category = models.CharField(max_length=60, default="其他类")
    subtype = models.CharField(max_length=60, blank=True)
    importance = models.CharField(max_length=20, default="常规")
    occurred = models.DateField(null=True)
    state = models.CharField(max_length=20, default="pending", db_index=True)
    approved = models.DateTimeField(null=True, db_index=True)
    auto = models.BooleanField(default=False)
    reasoning = models.TextField(blank=True)
    score = models.PositiveSmallIntegerField(null=True)
    tags = models.JSONField(default=list)
    merged_into = models.ForeignKey(
        "self", null=True, related_name="merged", on_delete=models.PROTECT
    )
    created = models.DateTimeField(default=timezone.now)
    creator = models.CharField(max_length=150, blank=True)
    version = models.PositiveIntegerField(default=1)
    stars = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="stars")

    class Meta:
        ordering = ["-occurred", "-id"]


class Change(models.Model):
    event = models.ForeignKey(
        Event, null=True, on_delete=models.PROTECT, related_name="changes"
    )
    action = models.CharField(max_length=60)
    actor = models.CharField(max_length=150)
    at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict)


class Run(models.Model):
    source_signature = models.CharField(max_length=64, blank=True)
    crawl_evidence = models.JSONField(default=dict, blank=True)
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    key = models.CharField(max_length=180, unique=True)
    trigger = models.CharField(max_length=50)
    state = models.CharField(max_length=30, default="queued", db_index=True)
    created = models.DateTimeField(default=timezone.now)
    started = models.DateTimeField(null=True)
    finished = models.DateTimeField(null=True)
    lease = models.CharField(max_length=40, blank=True)
    expires = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    counts = models.JSONField(default=dict)
    message = models.TextField(blank=True)


class Report(models.Model):
    kind = models.CharField(max_length=12)
    day = models.DateField()
    state = models.CharField(max_length=20, default="draft")
    content = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    issuer = models.CharField(max_length=150, blank=True)
    issued = models.DateTimeField(null=True)
    share_token = models.CharField(max_length=96, unique=True, null=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "day"], name="one_period_report")
        ]


class Edition(models.Model):
    report = models.ForeignKey(
        Report, on_delete=models.PROTECT, related_name="editions"
    )
    content = models.JSONField()
    issuer = models.CharField(max_length=150)
    issued = models.DateTimeField()
    version = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["report", "version"], name="unique_edition")
        ]


class Insight(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    content = models.TextField()
    event_ids = models.JSONField(default=list)


class FrozenList(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scope = models.CharField(max_length=20)
    rows = models.JSONField()
    created = models.DateTimeField(auto_now_add=True)


class SemanticRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    candidates = models.ForeignKey(FrozenList, on_delete=models.CASCADE)
    question = models.TextField()
    cursor = models.PositiveIntegerField(default=0)
    matched = models.JSONField(default=list)
    result = models.ForeignKey(
        FrozenList, null=True, related_name="+", on_delete=models.CASCADE
    )


class IntakeDecision(models.Model):
    run = models.ForeignKey(Run, null=True, on_delete=models.PROTECT)
    source = models.ForeignKey(Source, null=True, on_delete=models.PROTECT)
    event = models.ForeignKey(Event, null=True, on_delete=models.PROTECT)
    url = models.TextField(blank=True)
    title = models.CharField(max_length=500, blank=True)
    source_zone = models.CharField(max_length=80, blank=True)
    outcome = models.CharField(max_length=24)
    filtered_by = models.CharField(max_length=40, blank=True)
    noise_reason = models.CharField(max_length=40, blank=True)
    terms = models.JSONField(default=list)
    analysis = models.JSONField(default=dict)
    actor = models.CharField(max_length=150)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "url"], name="one_intake_decision_per_run_url"
            )
        ]


class MediaMetric(models.Model):
    media = models.ForeignKey(Media, related_name="metrics", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    day = models.DateField()
    value = models.DecimalField(max_digits=20, decimal_places=4)
    unit = models.CharField(max_length=16)
    source_url = models.URLField(max_length=1500)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["media", "kind", "day"], name="one_metric_per_period"
            )
        ]
