"""LOGTIME_IGNORE: time intra recorded wrongly, cut out before anything sees it.

Run with:  python -m pytest tests/  (or just: python tests/test_ignore.py)

No network: FtClient._get is swapped for a fake intra. settings is frozen,
hence the object.__setattr__.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.config import settings  # noqa: E402
from app.ft_api import FtClient  # noqa: E402

TZ = settings.tz


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class FakeIntra(FtClient):
    """Two real sessions either side of one that never logged out."""

    SESSIONS = [  # newest first, as intra returns them
        (at(24, 11), at(24, 18)),
        (at(22, 16), at(23, 11, 40)),  # the stuck one
        (at(22, 14), at(22, 16)),
    ]
    STATS = {
        "2026-10-21": "04:00:00.000000",
        "2026-10-22": "10:00:00.000000",
        "2026-10-23": "11:40:00.000000",
        "2026-10-24": "07:00:00.000000",
    }

    def _get(self, path, params=None):
        if path.endswith("locations_stats"):
            return self.STATS
        if params.get("page[number]", 1) > 1:
            return []
        return [
            {"begin_at": utc(begin), "end_at": utc(end)} for begin, end in self.SESSIONS
        ]


def ignoring(*windows) -> None:
    object.__setattr__(settings, "logtime_ignore", [
        (start.replace(tzinfo=None), end.replace(tzinfo=None)) for start, end in windows
    ])


def test_windows_parse_times_and_whole_days():
    os.environ["TEST_WINDOWS"] = "2026-10-22T16:00/2026-10-23T11:40, 2026-10-01/2026-10-02"
    assert config._windows("TEST_WINDOWS") == [
        (datetime(2026, 10, 22, 16), datetime(2026, 10, 23, 11, 40)),
        (datetime(2026, 10, 1), datetime(2026, 10, 3)),  # end date is inclusive
    ]


def test_windows_reject_backwards_spans():
    os.environ["TEST_WINDOWS"] = "2026-10-23T10:00/2026-10-22T10:00"
    try:
        config._windows("TEST_WINDOWS")
    except ValueError:
        return
    raise AssertionError("a span ending before it starts was accepted")


def test_sessions_lose_the_ignored_span():
    ignoring((at(22, 16), at(23, 11, 40)))
    sessions = FakeIntra().sessions_between(at(19, 0), at(26, 0))
    assert sorted(sessions) == [(at(22, 14), at(22, 16)), (at(24, 11), at(24, 18))]


def test_daily_totals_rebuild_only_the_touched_days():
    ignoring((at(22, 16), at(23, 11, 40)))
    totals = FakeIntra().all_logtime()
    assert totals[date(2026, 10, 21)] == timedelta(hours=4)   # untouched: intra's figure
    assert totals[date(2026, 10, 22)] == timedelta(hours=2)   # only the real session
    assert date(2026, 10, 23) not in totals                   # nothing real left
    assert totals[date(2026, 10, 24)] == timedelta(hours=7)


def test_nothing_ignored_leaves_intra_alone():
    ignoring()
    assert FakeIntra().all_logtime()[date(2026, 10, 23)] == timedelta(hours=11, minutes=40)


if __name__ == "__main__":
    for name, test in sorted(dict(globals()).items()):
        if name.startswith("test_") and callable(test):
            test()
            print("ok", name)
