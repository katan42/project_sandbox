"""Reads every calendar you care about and flattens them into busy intervals.

Google calendars come in as secret .ics feeds (no OAuth needed). iCloud comes
in over CalDAV, which we need anyway for writing the plan back.

Repeating events are the whole reason this file is more than ten lines: a
weekly class shows up once in the raw .ics with an RRULE, so it has to be
expanded across the window before it can be treated as busy time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import httpx

from .config import settings

PLAN_TAG = "X-LOGTIME-PLANNER"


@dataclass
class BusyEvent:
    start: datetime
    end: datetime
    title: str
    source: str

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "title": self.title,
            "source": self.source,
        }


def _as_datetime(value) -> datetime:
    """All-day events arrive as `date`; treat them as the whole local day."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=settings.tz)
        return value.astimezone(settings.tz)
    return datetime.combine(value, time.min, tzinfo=settings.tz)


def _google_busy(start: datetime, end: datetime) -> list[BusyEvent]:
    if not settings.google_ics_urls:
        return []

    import icalendar
    import recurring_ical_events

    events: list[BusyEvent] = []
    for url in settings.google_ics_urls:
        url = url.replace("webcal://", "https://")
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)
            response.raise_for_status()
            calendar = icalendar.Calendar.from_ical(response.text)
        except Exception as exc:  # a dead feed shouldn't kill the whole week
            events.append(
                BusyEvent(start, start, f"Could not read feed: {exc}", "error")
            )
            continue

        name = str(calendar.get("X-WR-CALNAME", "Google"))
        for event in recurring_ical_events.of(calendar).between(start, end):
            events.append(
                BusyEvent(
                    start=_as_datetime(event["DTSTART"].dt),
                    end=_as_datetime(event["DTEND"].dt)
                    if "DTEND" in event
                    else _as_datetime(event["DTSTART"].dt) + timedelta(hours=1),
                    title=str(event.get("SUMMARY", "Busy")),
                    source=name,
                )
            )
    return events


def _icloud_calendars():
    import caldav

    dav = caldav.DAVClient(
        url="https://caldav.icloud.com/",
        username=settings.icloud_username,
        password=settings.icloud_app_password,
    )
    return dav.principal().calendars()


def plan_calendar():
    """The one calendar the planner is allowed to write to."""
    wanted = settings.icloud_plan_calendar.strip().lower()
    for calendar in _icloud_calendars():
        if str(calendar.name or "").strip().lower() == wanted:
            return calendar
    raise RuntimeError(
        f"No iCloud calendar named {settings.icloud_plan_calendar!r}. "
        "Create it in Calendar.app first — the planner will not create it for you."
    )


def _icloud_busy(start: datetime, end: datetime) -> list[BusyEvent]:
    if not settings.icloud_enabled:
        return []

    plan_name = settings.icloud_plan_calendar.strip().lower()
    allowed = {name.strip().lower() for name in settings.icloud_busy_calendars}

    events: list[BusyEvent] = []
    for calendar in _icloud_calendars():
        name = str(calendar.name or "iCloud")
        if name.strip().lower() == plan_name:
            continue  # our own blocks are not "busy"
        if allowed and name.strip().lower() not in allowed:
            continue
        try:
            found = calendar.search(start=start, end=end, event=True, expand=True)
        except Exception as exc:
            events.append(BusyEvent(start, start, f"{name}: {exc}", "error"))
            continue

        for item in found:
            component = item.icalendar_component
            if not component.get("DTSTART"):
                continue
            begins = _as_datetime(component["DTSTART"].dt)
            if component.get("DTEND"):
                finishes = _as_datetime(component["DTEND"].dt)
            elif component.get("DURATION"):
                finishes = begins + component["DURATION"].dt
            else:
                finishes = begins + timedelta(hours=1)
            events.append(
                BusyEvent(
                    start=begins,
                    end=finishes,
                    title=str(component.get("SUMMARY", "Busy")),
                    source=name,
                )
            )
    return events


def busy_between(start: datetime, end: datetime) -> list[BusyEvent]:
    events = _google_busy(start, end) + _icloud_busy(start, end)
    events.sort(key=lambda item: item.start)
    return events


def week_bounds(anchor: date) -> tuple[datetime, datetime]:
    """The logtime week containing `anchor`, as local-midnight boundaries."""
    offset = (anchor.weekday() - settings.week_start_day) % 7
    first = anchor - timedelta(days=offset)
    start = datetime.combine(first, time.min, tzinfo=settings.tz)
    return start, start + timedelta(days=7)
