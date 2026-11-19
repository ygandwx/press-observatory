from django.db.models import Q
from django.db.models.functions import Coalesce, TruncDate
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .api import endpoint
from .domain import bounded_int, parse_day
from .editorial import compact
from .events_api import page_rows
from .geo import geo_entry
from .models import Event, Media


@endpoint(["GET"])
def timeline(req):
    query = req.GET
    events = Event.objects.filter(state="approved", is_noise=False, merged_into=None)
    if query.get("media"):
        pk = bounded_int(query["media"], 1, 2**53)
        events = events.filter(Q(media=pk) | Q(merged__media=pk))
    if query.get("category"):
        events = events.filter(category=query["category"])
    start = parse_day(query["start"]) if query.get("start") else None
    end = parse_day(query["end"]) if query.get("end") else None
    if start and end and start > end:
        raise ValidationError("开始日期不能晚于结束日期。")
    events = events.annotate(timeline_day=Coalesce("occurred", TruncDate("approved")))
    if start:
        events = events.filter(timeline_day__gte=start)
    if end:
        events = events.filter(timeline_day__lte=end)
    events = (
        events.distinct()
        .order_by("-timeline_day", "-id")
        .prefetch_related("media", "articles", "merged__media", "merged__articles")
    )
    needle = compact(query.get("q", "")[:250])
    rows = []
    for event in events.iterator(chunk_size=150):
        row = geo_entry(event, needle)
        if row:
            rows.append(row)
    return Response(
        {
            **page_rows(req, rows, lambda row: row),
            "media": list(Media.objects.order_by("name", "pk").values("id", "name")),
        }
    )
