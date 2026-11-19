"""Project published events into a topic timeline without creating duplicate events."""

from django.utils import timezone

from . import editorial


def geo_entry(event, needle=""):
    media = {}
    sources = {}
    hits = {}
    evidence = {}
    matches_query = not needle
    for part in [event, *event.merged.all()]:
        if part.is_noise or part.state != "approved":
            continue
        for item in part.media.all():
            media[item.pk] = item.name
        fields = [
            ("标题", part.title, ""),
            ("摘要", part.summary, ""),
            ("事件标签", " ".join(str(tag) for tag in (part.tags or [])), ""),
        ]
        for article in part.articles.all():
            sources[article.url] = article.title
            fields.extend(
                [
                    ("原文标题", article.title, article.url),
                    ("原文摘要", article.excerpt, article.url),
                ]
            )
        for label, text, source_url in fields:
            if needle and needle in editorial.compact(text):
                matches_query = True
            for term in editorial.geo_terms(text):
                hits.setdefault(term, set()).add(label)
                if source_url:
                    evidence.setdefault(term, set()).add(source_url)
    if not hits or not (
        matches_query
        or any(needle in editorial.compact(name) for name in media.values())
    ):
        return None
    occurred = event.occurred or (
        timezone.localdate(event.approved) if event.approved else None
    )
    return {
        "id": event.pk,
        "title": event.title,
        "summary": event.summary,
        "category": event.category,
        "date": str(occurred) if occurred else "",
        "date_kind": "发生日"
        if event.occurred
        else "入库日"
        if event.approved
        else "日期待核",
        "media": [{"id": pk, "name": name} for pk, name in sorted(media.items())],
        "sources": [
            {"url": url, "title": title} for url, title in sorted(sources.items())
        ],
        "matches": [
            {
                "term": term,
                "fields": sorted(fields),
                "source_urls": sorted(evidence.get(term, ())),
            }
            for term, fields in hits.items()
        ],
    }
