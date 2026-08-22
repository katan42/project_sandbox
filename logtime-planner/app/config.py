"""Configuration, loaded once from .env at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _time(name: str, default: str) -> time:
    hour, minute = os.getenv(name, default).split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class Settings:
    ft_uid: str = os.getenv("FT_UID", "")
    ft_secret: str = os.getenv("FT_SECRET", "")
    ft_login: str = os.getenv("FT_LOGIN", "")

    timezone: str = os.getenv("TIMEZONE", "Asia/Singapore")
    weekly_target_hours: float = float(os.getenv("WEEKLY_TARGET_HOURS", "20"))
    monthly_target_hours: float = float(os.getenv("MONTHLY_TARGET_HOURS", "90"))
    week_start_day: int = int(os.getenv("WEEK_START_DAY", "0"))

    day_window_start: time = _time("DAY_WINDOW_START", "08:00")
    day_window_end: time = _time("DAY_WINDOW_END", "23:00")

    min_block_minutes: int = int(os.getenv("MIN_BLOCK_MINUTES", "60"))
    max_hours_per_day: float = float(os.getenv("MAX_HOURS_PER_DAY", "10"))
    travel_buffer_minutes: int = int(os.getenv("TRAVEL_BUFFER_MINUTES", "30"))

    google_ics_urls: list[str] = field(default_factory=lambda: _csv("GOOGLE_ICS_URLS"))

    icloud_username: str = os.getenv("ICLOUD_USERNAME", "")
    icloud_app_password: str = os.getenv("ICLOUD_APP_PASSWORD", "")
    icloud_plan_calendar: str = os.getenv("ICLOUD_PLAN_CALENDAR", "42 Plan")
    icloud_busy_calendars: list[str] = field(
        default_factory=lambda: _csv("ICLOUD_BUSY_CALENDARS")
    )

    db_path: str = os.getenv("DB_PATH", "logtime.db")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def icloud_enabled(self) -> bool:
        return bool(self.icloud_username and self.icloud_app_password)

    @property
    def ft_enabled(self) -> bool:
        return bool(self.ft_uid and self.ft_secret and self.ft_login)


settings = Settings()
