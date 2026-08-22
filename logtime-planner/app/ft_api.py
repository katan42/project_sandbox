"""Client for the 42 intra API — the source of truth for hours already clocked.

Two endpoints do all the work:

  GET /v2/users/<login>/locations_stats
      {"2026-07-30": "04:12:54.190362", ...}  one entry per day you logged in

  GET /v2/users/<login>/locations
      individual sessions with begin_at / end_at, newest first. A session with
      end_at == null is still running, and its hours are NOT yet included in
      locations_stats — we add them back by hand.
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta, timezone

import httpx

from .config import settings

TOKEN_URL = "https://api.intra.42.fr/oauth/token"
API_ROOT = "https://api.intra.42.fr/v2"

_DURATION = re.compile(r"^(\d+):(\d{2}):(\d{2})(?:\.(\d+))?$")


class FtApiError(RuntimeError):
    pass


def parse_duration(raw: str) -> timedelta:
    """'04:12:54.190362' -> timedelta."""
    match = _DURATION.match(raw.strip())
    if not match:
        raise FtApiError(f"Unrecognised duration from intra: {raw!r}")
    hours, minutes, seconds, _fraction = match.groups()
    return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))


class FtClient:
    """Caches the client-credentials token until shortly before it expires."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at = datetime.now(timezone.utc)
        self._lock = threading.Lock()
        self.secret_days_left: int | None = None
        self.secret_expires_on: datetime | None = None

    def _access_token(self) -> str:
        with self._lock:
            if self._token and datetime.now(timezone.utc) < self._expires_at:
                return self._token
            response = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.ft_uid,
                    "client_secret": settings.ft_secret,
                },
                timeout=20,
            )
            if response.status_code != 200:
                raise FtApiError(
                    f"Token request failed ({response.status_code}). "
                    "Check FT_UID and FT_SECRET."
                )
            payload = response.json()

            # 42 rotates client secrets on roughly a 30-day cycle and tells us
            # the deadline here. Without this the app just starts 401-ing one
            # morning with no explanation.
            valid_until = payload.get("secret_valid_until")
            if valid_until:
                expires_on = datetime.fromtimestamp(int(valid_until), timezone.utc)
                self.secret_expires_on = expires_on
                self.secret_days_left = (expires_on - datetime.now(timezone.utc)).days
                if self.secret_days_left < 7:
                    print(
                        f"[logtime] 42 client secret expires in "
                        f"{self.secret_days_left} day(s), on "
                        f"{expires_on.date().isoformat()}. Regenerate it at "
                        "profile.intra.42.fr and update FT_SECRET in .env."
                    )

            self._token = payload["access_token"]
            lifetime = int(payload.get("expires_in", 7200))
            self._expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=lifetime - 120
            )
            return self._token

    def _get(self, path: str, params: dict | None = None):
        response = httpx.get(
            f"{API_ROOT}{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params=params or {},
            timeout=30,
        )
        if response.status_code == 401:
            self._token = None  # force a refresh on the next call
            raise FtApiError("Intra rejected the token. Try again.")
        if response.status_code == 429:
            raise FtApiError("Rate limited by intra (2 req/sec, 1200/hour).")
        if response.status_code >= 400:
            raise FtApiError(f"{path} returned {response.status_code}")
        return response.json()

    def sessions_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Raw login sessions overlapping the window, clipped to it.

        locations_stats is a per-day rollup whose treatment of a session that
        is still running is not something we should have to guess at. Sessions
        are unambiguous: a null end_at means "still going", so we substitute
        now, and a session spanning midnight gets split by the caller.
        """
        now = datetime.now(settings.tz)
        found: list[tuple[datetime, datetime]] = []

        for page in range(1, 6):  # 500 sessions is far more than a week needs
            rows = self._get(
                f"/users/{settings.ft_login}/locations",
                {"page[size]": 100, "page[number]": page},
            )
            if not rows:
                break

            reached_the_past = False
            for row in rows:
                begin = datetime.fromisoformat(
                    row["begin_at"].replace("Z", "+00:00")
                ).astimezone(settings.tz)
                finish = (
                    datetime.fromisoformat(row["end_at"].replace("Z", "+00:00")).astimezone(
                        settings.tz
                    )
                    if row.get("end_at")
                    else now
                )
                if finish <= start:
                    reached_the_past = True  # newest-first, so we can stop
                    continue
                if begin >= end:
                    continue
                found.append((max(begin, start), min(finish, end)))

            if reached_the_past:
                break

        return found

    def all_logtime(self) -> dict[date, timedelta]:
        """Every day intra has a record for. One call feeds both the week and
        the month view, which keeps us well clear of the rate limit."""
        raw = self._get(f"/users/{settings.ft_login}/locations_stats")
        out: dict[date, timedelta] = {}
        for day_str, duration_str in raw.items():
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue
            out[day] = parse_duration(duration_str)
        return out

    def daily_logtime(self, start: date, end: date) -> dict[date, timedelta]:
        """Hours clocked per day, inclusive of both ends."""
        return {
            day: duration
            for day, duration in self.all_logtime().items()
            if start <= day <= end
        }

    def open_session_started_at(self) -> datetime | None:
        """Start time of a session that is still running, if any."""
        recent = self._get(
            f"/users/{settings.ft_login}/locations",
            {"page[size]": 10, "sort": "-begin_at"},
        )
        for entry in recent:
            if entry.get("end_at") is None:
                begin = entry["begin_at"].replace("Z", "+00:00")
                return datetime.fromisoformat(begin).astimezone(settings.tz)
        return None


client = FtClient()
