"""Configuration, loaded once from .env at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str) -> list[str]:
    """Read a comma-separated env var into a list, dropping blank entries
    (so a trailing comma or an unset var don't leave `[""]` around)."""
    raw = os.getenv(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _windows(name: str) -> list[tuple[datetime, datetime]]:
    """Read comma-separated `start/end` pairs, local time, no timezone.

    Either side may be a bare date: a start date means its midnight, an end
    date means the *end* of that day, so `2026-10-22/2026-10-23` is both days
    whole. Naive here because the timezone is itself a setting; Settings
    attaches it."""
    windows: list[tuple[datetime, datetime]] = []
    for part in _csv(name):
        try:
            raw_start, raw_end = (side.strip() for side in part.split("/"))
            start = datetime.fromisoformat(raw_start)
            end = datetime.fromisoformat(raw_end)
        except ValueError as exc:
            raise ValueError(
                f"{name}: can't read {part!r} — expected start/end, e.g. "
                "2026-10-22T16:00/2026-10-23T12:00"
            ) from exc
        if "T" not in raw_end and " " not in raw_end:
            end += timedelta(days=1)
        if end <= start:
            raise ValueError(f"{name}: {part!r} ends before it starts.")
        windows.append((start, end))
    return windows


def _time(name: str, default: str) -> time:
    hour, minute = os.getenv(name, default).split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)  # frozen: config is read once at import and never mutated
class Settings:
    ft_uid: str = os.getenv("FT_UID", "")
    ft_secret: str = os.getenv("FT_SECRET", "")
    ft_login: str = os.getenv("FT_LOGIN", "")

    timezone: str = os.getenv("TIMEZONE", "Asia/Singapore")
    weekly_target_hours: float = float(os.getenv("WEEKLY_TARGET_HOURS", "20"))
    monthly_target_hours: float = float(os.getenv("MONTHLY_TARGET_HOURS", "90"))
    week_start_day: int = int(os.getenv("WEEK_START_DAY", "0"))
    # Where the summary tab's history begins.
    summary_since: date = date.fromisoformat(os.getenv("SUMMARY_SINCE", "2025-05-01"))

    day_window_start: time = _time("DAY_WINDOW_START", "08:00")
    day_window_end: time = _time("DAY_WINDOW_END", "23:00")

    # What the grid *draws*, as opposed to where auto-fill may place blocks.
    # Kept as strings because FullCalendar's end-of-day is "24:00", which
    # datetime.time cannot represent.
    grid_start: str = os.getenv("GRID_START", "00:00")
    grid_end: str = os.getenv("GRID_END", "24:00")

    min_block_minutes: int = int(os.getenv("MIN_BLOCK_MINUTES", "60"))

    # Two ceilings, because "how much will this thing suggest" and "how much
    # am I allowed to commit to" are different questions. Auto-fill stops at
    # the first; a block you drag out by hand is only stopped by the second.
    plan_max_hours_per_day: float = float(os.getenv("PLAN_MAX_HOURS_PER_DAY", "14"))
    manual_max_hours_per_day: float = float(os.getenv("MANUAL_MAX_HOURS_PER_DAY", "24"))
    travel_buffer_minutes: int = int(os.getenv("TRAVEL_BUFFER_MINUTES", "30"))

    google_ics_urls: list[str] = field(default_factory=lambda: _csv("GOOGLE_ICS_URLS"))

    icloud_username: str = os.getenv("ICLOUD_USERNAME", "")
    icloud_app_password: str = os.getenv("ICLOUD_APP_PASSWORD", "")
    icloud_plan_calendar: str = os.getenv("ICLOUD_PLAN_CALENDAR", "42 Plan")
    icloud_busy_calendars: list[str] = field(
        default_factory=lambda: _csv("ICLOUD_BUSY_CALENDARS")
    )

    db_path: str = os.getenv("DB_PATH", "logtime.db")

    # Spans whose clocked time intra got wrong — a logout that never reached
    # it, say — and that you'd rather plan around than wait out. Every view
    # treats time inside them as never clocked. Delete the entry once intra
    # corrects itself, or real time inside the span stays hidden too.
    logtime_ignore: list[tuple[datetime, datetime]] = field(
        default_factory=lambda: _windows("LOGTIME_IGNORE")
    )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def ignored_windows(self) -> list[tuple[datetime, datetime]]:
        return [
            (start.replace(tzinfo=self.tz), end.replace(tzinfo=self.tz))
            for start, end in self.logtime_ignore
        ]

    @property
    def icloud_enabled(self) -> bool:
        # No separate on/off flag — presence of both credentials *is* "enabled".
        return bool(self.icloud_username and self.icloud_app_password)

    @property
    def ft_enabled(self) -> bool:
        return bool(self.ft_uid and self.ft_secret and self.ft_login)


settings = Settings()

# MAX_HOURS_PER_DAY was split in two. Say so rather than silently ignoring a
# value someone deliberately set.
if os.getenv("MAX_HOURS_PER_DAY"):
    print(
        "[logtime] MAX_HOURS_PER_DAY is no longer read. It is now "
        f"PLAN_MAX_HOURS_PER_DAY (auto-fill, currently "
        f"{settings.plan_max_hours_per_day:g}h) and MANUAL_MAX_HOURS_PER_DAY "
        f"(blocks you place yourself, currently "
        f"{settings.manual_max_hours_per_day:g}h). Update your .env."
    )
