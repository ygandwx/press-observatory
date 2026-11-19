import re
from secrets import compare_digest

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .api import endpoint
from .domain import parse_day
from .events_api import page_rows
from .models import Report
from .reports import (
    generate,
    report_action,
    report_row,
    weekly_share_key,
    weekly_share_views,
)


def can_manage(user):
    return user.has_perm("desk.edit") or user.has_perm("desk.sign")


def visible_reports(req):
    qs = Report.objects.all()
    if not can_manage(req.user):
        qs = qs.filter(editions__isnull=False).distinct()
    return qs


def reader_row(r, detail, manage):
    row = report_row(r, detail, manage)
    if not manage:
        edition = r.editions.latest("version")
        row.update(
            headline=edition.content["headline"],
            version=edition.version,
            issuer=edition.issuer,
            issued=edition.issued,
            state="archived" if r.state == "archived" else "published",
        )
        if detail:
            row["content"] = edition.content
    return row


@endpoint(["GET", "POST"])
def reports(req, pk=None):
    admin = can_manage(req.user)
    if req.method == "POST":
        if pk:
            r = report_action(req.user, pk, req.data.get("action"), req.data)
        else:
            r = generate(
                req.user,
                req.data.get("kind", "daily"),
                parse_day(req.data.get("day", timezone.localdate())),
                req.data.get("regenerate") is True,
                req.data.get("version"),
            )
        return Response(report_row(r, True, admin))
    qs = visible_reports(req)
    if pk:
        r = qs.get(pk=pk)
        row = reader_row(r, True, admin)
        if req.GET.get("edition"):
            edition = r.editions.get(version=req.GET["edition"])
            row.update(
                content=edition.content,
                headline=edition.content["headline"],
                version=edition.version,
                issued=edition.issued,
                issuer=edition.issuer,
                state="published",
                share=None,
                share_views=None,
            )
            if (
                req.user.has_perm("desk.sign")
                and r.kind == "weekly"
                and r.state in ("published", "archived")
            ):
                row["share_management_version"] = r.version
                if r.share_token:
                    row["share_views"] = weekly_share_views(r, edition.version)
        return Response(row)
    for key in ("kind", "state"):
        if req.GET.get(key):
            if key == "state" and not admin and req.GET[key] == "published":
                qs = qs.exclude(state="archived")
            elif key == "state" and not admin and req.GET[key] != "archived":
                qs = qs.none()
            else:
                qs = qs.filter(**{key: req.GET[key]})
    if req.GET.get("day"):
        qs = qs.filter(day=parse_day(req.GET["day"]))
    if req.GET.get("month"):
        qs = qs.filter(day__startswith=req.GET["month"][:7])
    if req.GET.get("q"):
        needle = req.GET["q"][:180]
        if admin:
            qs = qs.filter(
                Q(content__headline__icontains=needle)
                | Q(day__icontains=needle)
                | Q(issuer__icontains=needle)
            )
        else:
            qs = qs.filter(
                Q(editions__content__headline__icontains=needle)
                | Q(day__icontains=needle)
                | Q(editions__issuer__icontains=needle)
            ).distinct()
    return Response(
        page_rows(
            req, qs.order_by("-day", "-id"), lambda r: reader_row(r, False, admin)
        )
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def public(req, token):
    view = None
    scoped = re.fullmatch(
        r"w-(\d{1,15})-(\d{1,15})-(leader|detail)-[0-9a-f]{64}", token
    )
    if scoped:
        pk, version, view = scoped.groups()
        r = Report.objects.get(
            pk=pk,
            kind="weekly",
            state__in=["published", "archived"],
            share_token__isnull=False,
        )
        if not compare_digest(token, weekly_share_key(r, int(version), view)):
            raise NotFound()
        e = r.editions.get(version=version)
    else:
        r = Report.objects.get(share_token=token, state__in=["published", "archived"])
        e = r.editions.latest("version")
    content = e.content
    if view == "leader":
        content = {
            **content,
            "events": [
                {k: v for k, v in row.items() if k in ("id", "title", "sources")}
                for row in content["events"]
            ],
        }
        if "week_comparison" in content:
            content["week_comparison"] = {
                k: v for k, v in content["week_comparison"].items() if k != "categories"
            }
        if "geo_progress" in content:
            content["geo_progress"] = [
                {k: v for k, v in row.items() if k in ("id", "title", "sources")}
                for row in content["geo_progress"]
            ]
    return Response(
        {
            "id": r.pk,
            "kind": r.kind,
            "day": r.day,
            "version": e.version,
            "issuer": e.issuer,
            "issued": e.issued,
            "state": "published",
            "content": content,
            "view": view,
        }
    )
