"""One-way push of planned blocks into a dedicated iCloud calendar.

Deliberately one-way. The plan calendar is treated as a rendering of the
database, not as an input: every push rewrites the week from scratch, so an
edit you make in Calendar.app will be overwritten. Edit in the planner, read on
your phone.

Event UIDs are derived from the block id, which makes the push idempotent —
re-pushing an unchanged block updates it in place instead of duplicating it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import store
from .calendars import _as_datetime, plan_calendar
from .config import settings

UID_SUFFIX = "@logtime-planner.local"
SECRET_REMINDER_UID_SUFFIX = "-secret-reminder@logtime-planner.local"


def _uid(block_id: str) -> str:
    return f"{block_id}{UID_SUFFIX}"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(settings.tz).strftime("%Y%m%dT%H%M%S")


def _escape(text: str) -> str:
    """iCalendar TEXT escaping (RFC 5545 §3.3.11). Fixed titles never needed
    this; a goal you typed yourself may well contain a comma or a semicolon,
    and an unescaped one would split the property into two values."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ics(block: store.Block) -> str:
    # "42 planned hours · 1.8h", plus the goal when there is one. The hours
    # stay in the title either way — the goal is an addition to the label,
    # not a replacement for it.
    parts = ["42 planned hours", f"{block.hours:.1f}h"]
    if block.goal:
        parts.append(block.goal)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//logtime-planner//EN",
        "BEGIN:VEVENT",
        f"UID:{_uid(block.id)}",
        f"DTSTAMP:{datetime.now(settings.tz).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;TZID={settings.timezone}:{_stamp(block.start)}",
        f"DTEND;TZID={settings.timezone}:{_stamp(block.end)}",
        f"SUMMARY:{_escape(' · '.join(parts))}",
        # Tags ride along as extra categories. Calendar.app doesn't show them,
        # but they survive the round trip and keep the event searchable.
        "CATEGORIES:" + ",".join(["42"] + [_escape(tag) for tag in block.tags]),
    ]
    if block.tags:
        lines.append("DESCRIPTION:" + _escape(" ".join("#" + t for t in block.tags)))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)


def _secret_reminder_ics(expires_on: date) -> str:
    """All-day event on the expiry date itself. UID is keyed off the date, so
    clicking the button again for the same date upserts instead of
    duplicating, but a freshly-rotated secret with a new expiry date gets its
    own event rather than silently replacing the old one."""
    day = expires_on.strftime("%Y%m%d")
    next_day = (expires_on + timedelta(days=1)).strftime("%Y%m%d")
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//logtime-planner//EN",
            "BEGIN:VEVENT",
            f"UID:{day}{SECRET_REMINDER_UID_SUFFIX}",
            f"DTSTAMP:{datetime.now(settings.tz).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{day}",
            f"DTEND;VALUE=DATE:{next_day}",
            "SUMMARY:Renew 42 client secret (FT_SECRET)",
            "DESCRIPTION:profile.intra.42.fr -> regenerate the app secret, then "
            "update FT_SECRET in logtime-planner's .env",
            "CATEGORIES:42",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


def push_secret_reminder(expires_on: date) -> dict:
    """Drop a same-day all-day reminder onto the plan calendar for when the
    42 client secret expires — a calendar notification reaches you even when
    the planner itself isn't open that day."""
    if not settings.icloud_enabled:
        return {"ok": False, "detail": "iCloud credentials are not configured."}

    calendar = plan_calendar()
    calendar.save_event(_secret_reminder_ics(expires_on))  # same UID each time, so this is an upsert
    return {"ok": True, "expiresOn": expires_on.isoformat()}


def pull_week(start: datetime, end: datetime) -> dict:
    """Reconcile the database with what is actually on the iCloud plan calendar:
    adopt blocks you moved there, and drop ones you deleted.

    Only ever touches blocks that were previously pushed. A block you just made
    in the planner and haven't synced yet is absent from iCloud for an innocent
    reason, and must not be mistaken for a deletion. A moved block is skipped
    (left at its old time) rather than adopted if the new time would overlap
    another block — planned blocks may never overlap.
    """
    if not settings.icloud_enabled:
        return {"ok": False, "detail": "iCloud credentials are not configured."}

    calendar = plan_calendar()
    # Map block id -> its current (start, end) in iCloud, but only for events
    # this app created (UID carries our suffix) — anything else on the plan
    # calendar isn't ours to reconcile.
    found: dict[str, tuple[datetime, datetime]] = {}
    for existing in calendar.search(start=start, end=end, event=True):
        component = existing.icalendar_component
        uid = str(component.get("UID", ""))
        if not uid.endswith(UID_SUFFIX):
            continue
        if not component.get("DTSTART") or not component.get("DTEND"):
            continue
        found[uid[: -len(UID_SUFFIX)]] = (
            _as_datetime(component["DTSTART"].dt),
            _as_datetime(component["DTEND"].dt),
        )

    removed = 0
    moved = 0
    skipped = 0
    for block in store.list_between(start, end):
        if not block.pushed_at:
            continue  # never synced yet — its absence from iCloud is expected
        if block.id not in found:
            store.delete(block.id)  # was pushed before, gone now — you deleted it
            removed += 1
            continue

        new_start, new_end = found[block.id]
        if new_start == block.start and new_end == block.end:
            continue  # unchanged, nothing to reconcile

        # Adopting the iCloud time might now overlap a different block — check
        # before committing to it, same rule the API enforces on manual edits.
        clash = next(
            (
                other
                for other in store.list_between(new_start, new_end)
                if other.id != block.id
            ),
            None,
        )
        if clash is not None:
            skipped += 1
            continue

        store.update(block.id, new_start, new_end)
        moved += 1

    return {"ok": True, "removed": removed, "moved": moved, "skipped": skipped}


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
        # One of our events whose block no longer exists in this week —
        # e.g. it was deleted in the planner since the last push.
        if uid.endswith(UID_SUFFIX) and uid not in wanted:
            existing.delete()
            removed += 1

    written = 0
    for block in blocks:
        calendar.save_event(_ics(block))  # same UID each time, so this is an upsert
        store.mark_pushed(block.id)
        written += 1

    return {"ok": True, "written": written, "removed": removed}
