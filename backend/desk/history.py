"""Shared publication safeguards for reversible historical operations."""

from django.db import connection


def lock_publications():
    # A report might otherwise acquire its snapshot while the batch is being
    # classified. The lock is held only for the short all-or-nothing write.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '4s'")
            cursor.execute(
                "LOCK TABLE desk_report, desk_edition IN SHARE ROW EXCLUSIVE MODE"
            )


def report_ids(content):
    ids = {row["id"] for row in content.get("events", []) if "id" in row}
    for values in content.get("section_events", {}).values():
        ids.update(values)
    return ids
