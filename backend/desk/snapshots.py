"""Immutable, account-scoped projections used by dashboard drills and search."""

import hashlib
import json
from contextlib import contextmanager
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from rest_framework.exceptions import NotFound
from .models import FrozenList, Event, Media
from .domain import event_row, media_row, parse_day, check_state, bounded_int


@contextmanager
def consistent_read():
    # Capture parent rows and prefetched relations from one MVCC view.
    outer = not connection.in_atomic_block
    with transaction.atomic():
        if outer and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        yield


def freeze(user, scope, rows):
    # Stable ordering and content addressing reuse an unchanged observation.
    raw = json.dumps(
        [user.pk, scope, rows],
        cls=DjangoJSONEncoder,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    obj, _ = FrozenList.objects.get_or_create(
        pk=digest, defaults={"owner": user, "scope": scope, "rows": json.loads(raw)[2]}
    )
    return obj


def load(user, key, scope):
    if scope != "media":
        check_state(user, scope)
    try:
        return FrozenList.objects.get(pk=key, owner=user, scope=scope)
    except FrozenList.DoesNotExist:
        raise NotFound("该快照不存在或当前账号无权读取。")


def project_events(qs, user):
    qs = qs.annotate(
        viewer_star=Exists(
            Event.stars.through.objects.filter(event_id=OuterRef("pk"), user_id=user.pk)
        )
    )
    qs = qs.prefetch_related("media", "articles", "merged__media", "merged__articles")
    return [event_row(e, user) for e in qs]


def project_media(user):
    qs = Media.objects.annotate(
        viewer_follow=Exists(
            Media.followers.through.objects.filter(
                media_id=OuterRef("pk"), user_id=user.pk
            )
        )
    ).order_by("name", "id")
    return [{**media_row(m), "following": m.viewer_follow} for m in qs]


def filter_events(rows, f):
    start = parse_day(f["start"]) if f.get("start") else None
    end = parse_day(f["end"]) if f.get("end") else None
    mid = bounded_int(f["media"], 1, 2**53) if f.get("media") else None
    query = f.get("q", "")[:250].casefold()
    result = []
    for row in rows:
        if any(
            f.get(k) and row[k] != f[k] for k in ("category", "subtype", "importance")
        ):
            continue
        if (
            f.get("auto") == "1"
            and not row["auto"]
            or f.get("star") == "1"
            and not row["starred"]
        ):
            continue
        media = row["media"]
        if mid and not any(m["id"] == mid for m in media):
            continue
        if any(
            f.get(k) and not any(m[field] == f[k] for m in media)
            for k, field in (
                ("region", "region"),
                ("primary", "category"),
                ("secondary", "subcategory"),
                ("focus", "focus"),
            )
        ):
            continue
        if query and not any(
            query in value.casefold()
            for value in (row["title"], row["summary"], *(m["name"] for m in media))
        ):
            continue
        if start or end:
            day = (
                row["approval_day"] if f.get("clock") == "approved" else row["occurred"]
            )
            if (
                not day
                or start
                and str(day) < str(start)
                or end
                and str(day) > str(end)
            ):
                continue
        result.append(row)
    return result


def filter_media(rows, f):
    terms = ("focus", "level", "category", "subcategory", "region", "kind")
    query = f.get("q", "").casefold()
    return [
        m
        for m in rows
        if all(not f.get(k) or f[k] == m[k] for k in terms)
        and (f.get("following") != "1" or m["following"])
        and (query in m["name"].casefold() or query in m["owner"].casefold())
    ]
