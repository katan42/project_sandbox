"""One-way push of planned blocks into a dedicated iCloud calendar.

Deliberately one-way. The plan calendar is treated as a rendering of the
database, not as an input: every push rewrites the week from scratch, so an
edit you make in Calendar.app will be overwritten. Edit in the planner, read on
your phone.

Event UIDs are derived from the block id, which makes the push idempotent —
re-pushing an unchanged block updates it in place instead of duplicating it.
"""

from __future__ import annotations

from datetime import datetime

from . import store
from .calendars import plan_calendar
from .config import settings

UID_SUFFIX = "@logtime-planner.local"


def _uid(block_id: str) -> str:
    return f"{block_id}{UID_SUFFIX}"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(settings.tz).strftime("%Y%m%dT%H%M%S")


def _ics(block: store.Block) -> str:
    title = block.note.strip() or "42 logtime"
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//logtime-planner//EN",
            "BEGIN:VEVENT",
            f"UID:{_uid(block.id)}",
            f"DTSTAMP:{datetime.now(settings.tz).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID={settings.timezone}:{_stamp(block.start)}",
            f"DTEND;TZID={settings.timezone}:{_stamp(block.end)}",
            f"SUMMARY:{title} · {block.hours:.1f}h",
            "CATEGORIES:42",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


def push_week(start: datetime, end: datetime) -> dict:
    """Make the plan calendar match the database for this week exactly."""
    if not settings.icloud_enabled:
        return {"ok": False, "detail": "iCloud credentials are not configured."}

    calendar = plan_calendar()
    blocks = store.list_between(start, end)
    wanted = {_uid(block.id): block for block in blocks}

    removed = 0
    for existing in calendar.search(start=start, end=end, event=True):
        component = existing.icalendar_component
        uid = str(component.get("UID", ""))
        if uid.endswith(UID_SUFFIX) and uid not in wanted:
            existing.delete()
            removed += 1

    written = 0
    for block in blocks:
        calendar.save_event(_ics(block))
        store.mark_pushed(block.id)
        written += 1

    return {"ok": True, "written": written, "removed": removed}
