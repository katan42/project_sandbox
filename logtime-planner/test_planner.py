"""Run with:  python -m pytest tests/  (or just: python tests/test_planner.py)"""

import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import planner  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


@dataclass
class FakeBlock:
    id: str
    start: datetime
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@dataclass
class FakeEvent:
    start: datetime
    end: datetime
    title: str = "Class"
    source: str = "Work"


def test_merge_collapses_overlaps():
    merged = planner.merge(
        [(at(3, 9), at(3, 11)), (at(3, 10), at(3, 12)), (at(3, 14), at(3, 15))]
    )
    assert merged == [(at(3, 9), at(3, 12)), (at(3, 14), at(3, 15))]


def test_subtract_carves_holes():
    free = planner.subtract((at(3, 8), at(3, 20)), [(at(3, 12), at(3, 14))])
    assert free == [(at(3, 8), at(3, 12)), (at(3, 14), at(3, 20))]


def test_subtract_handles_full_cover():
    assert planner.subtract((at(3, 9), at(3, 17)), [(at(3, 8), at(3, 18))]) == []


def test_free_windows_respects_now():
    windows = planner.free_windows(
        days=[date(2026, 8, 3)],
        day_start=time(8, 0),
        day_end=time(23, 0),
        blocked=[],
        tz=TZ,
        not_before=at(3, 15),
    )
    assert windows == [(at(3, 15), at(3, 23))]


def test_conflicts_flag_overlap_and_turnaround():
    block = FakeBlock("b1", at(3, 13), at(3, 17))
    overlapping = planner.find_conflicts([block], [FakeEvent(at(3, 12), at(3, 14))])
    assert overlapping[0].reason == "overlaps"

    tight = planner.find_conflicts(
        [block], [FakeEvent(at(3, 11), at(3, 12, 45))], timedelta(minutes=30)
    )
    assert tight[0].reason == "tight turnaround"


def test_summary_splits_past_and_future_plans():
    now = at(5, 12)
    blocks = [FakeBlock("done", at(4, 9), at(4, 13)), FakeBlock("todo", at(6, 9), at(6, 17))]
    summary = planner.summarise(20, {date(2026, 8, 4): timedelta(hours=4)}, blocks, now)
    assert summary.clocked_hours == 4
    assert summary.planned_past_hours == 4  # already happened, already counted by intra
    assert summary.planned_future_hours == 8
    assert summary.deficit_hours == 8


def test_the_friday_shortfall_scenario():
    """Planned 4h Fri / 8h Sat / 8h Sun. Only clocked 3h on Friday.
    By Saturday morning the gap should be 17h, not 20h or 12h."""
    now = at(8, 8)  # Saturday morning
    clocked = {date(2026, 8, 7): timedelta(hours=3)}
    blocks = [
        FakeBlock("fri", at(7, 18), at(7, 22)),  # in the past now
        FakeBlock("sat", at(8, 9), at(8, 17)),
        FakeBlock("sun", at(9, 9), at(9, 17)),
    ]
    summary = planner.summarise(20, clocked, blocks, now)
    assert summary.clocked_hours == 3
    assert summary.planned_future_hours == 16
    assert summary.deficit_hours == 1  # the hour Friday lost


def test_autofill_closes_the_gap_within_day_caps():
    windows = planner.free_windows(
        days=[date(2026, 8, 8), date(2026, 8, 9)],
        day_start=time(9, 0),
        day_end=time(22, 0),
        blocked=[(at(9, 12), at(9, 14))],
        tz=TZ,
    )
    placed = planner.autofill(
        deficit_hours=12,
        windows=windows,
        clocked={},
        planned=[],
        max_hours_per_day=8,
        min_block_minutes=60,
    )
    total = sum((end - start).total_seconds() / 3600 for start, end in placed)
    assert total == 12
    by_day = {}
    for start, end in placed:
        by_day[start.date()] = by_day.get(start.date(), 0) + (end - start).total_seconds() / 3600
    assert all(value <= 8 for value in by_day.values())


def test_autofill_will_not_exceed_the_deficit():
    windows = planner.free_windows(
        days=[date(2026, 8, 8)], day_start=time(9, 0), day_end=time(22, 0),
        blocked=[], tz=TZ,
    )
    placed = planner.autofill(2.5, windows, {}, [], 10, 60)
    total = sum((end - start).total_seconds() / 3600 for start, end in placed)
    assert total == 2.5


def test_autofill_accounts_for_hours_already_clocked_that_day():
    windows = planner.free_windows(
        days=[date(2026, 8, 8)], day_start=time(9, 0), day_end=time(22, 0),
        blocked=[], tz=TZ,
    )
    placed = planner.autofill(
        deficit_hours=10,
        windows=windows,
        clocked={date(2026, 8, 8): timedelta(hours=6)},
        planned=[],
        max_hours_per_day=8,
        min_block_minutes=60,
    )
    total = sum((end - start).total_seconds() / 3600 for start, end in placed)
    assert total == 2  # only 2h of headroom left on that day


if __name__ == "__main__":
    passed = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"  ok  {name}")
            passed += 1
    print(f"\n{passed} passed")
