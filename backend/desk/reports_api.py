from django.utils import timezone
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .api import endpoint
from .domain import parse_day
from .models import Report
from .reports import generate, report_action, report_row
from .events_api import page_rows


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
                version=edition.version,
                issued=edition.issued,
                issuer=edition.issuer,
                state="published",
                share=None,
            )
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
    r = Report.objects.get(share_token=token, state__in=["published", "archived"])
    e = r.editions.latest("version")
    return Response(
        {
            "id": r.pk,
            "kind": r.kind,
            "day": r.day,
            "version": e.version,
            "issuer": e.issuer,
            "issued": e.issued,
            "state": "published",
            "content": e.content,
        }
    )
