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


def test_summary_splits_a_block_currently_in_progress():
    """A block you're mid-way through shouldn't count its already-elapsed
    portion as still 'planned' — that slice is already sitting in clocked,
    from whatever session intra says is open right now."""
    now = at(5, 11)
    blocks = [FakeBlock("live", at(5, 9), at(5, 13))]  # 9am-1pm, now is 11am
    summary = planner.summarise(20, {date(2026, 8, 5): timedelta(hours=2)}, blocks, now)
    assert summary.clocked_hours == 2
    assert summary.planned_future_hours == 2  # only the remaining 11am-1pm
    assert summary.planned_past_hours == 2  # the elapsed 9am-11am
    assert summary.covered_hours == 4  # not 6 — the overlap isn't double-counted


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


def test_hours_by_day_splits_a_session_at_midnight():
    """21:00 Saturday to 03:00 Sunday is three Saturday hours and three Sunday
    hours, not six of either."""
    split = planner.hours_by_day([(at(22, 21), at(23, 3))], TZ)
    assert split[date(2026, 8, 22)] == timedelta(hours=3)
    assert split[date(2026, 8, 23)] == timedelta(hours=3)


def test_hours_by_day_totals_multiple_sessions():
    split = planner.hours_by_day(
        [(at(22, 9), at(22, 11, 30)), (at(22, 14), at(22, 17))], TZ
    )
    assert split[date(2026, 8, 22)] == timedelta(hours=5, minutes=30)


def test_flag_unlogged_ignores_fully_clocked_blocks():
    block = FakeBlock("b1", at(3, 9), at(3, 11))
    flagged = planner.flag_unlogged([block], [(at(3, 9), at(3, 11))], now=at(3, 12))
    assert flagged == []


def test_flag_unlogged_catches_a_block_with_no_session_at_all():
    block = FakeBlock("b1", at(3, 9), at(3, 11))
    flagged = planner.flag_unlogged([block], [], now=at(3, 12))
    assert len(flagged) == 1
    assert flagged[0].block_id == "b1"
    assert flagged[0].hours == 2


def test_flag_unlogged_measures_the_partial_gap():
    block = FakeBlock("b1", at(3, 9), at(3, 13))
    # Only clocked the first half of the planned block.
    flagged = planner.flag_unlogged([block], [(at(3, 9), at(3, 11))], now=at(3, 14))
    assert flagged[0].hours == 2


def test_flag_unlogged_skips_blocks_not_due_yet():
    block = FakeBlock("b1", at(3, 9), at(3, 11))
    flagged = planner.flag_unlogged([block], [], now=at(3, 8))
    assert flagged == []


def test_flag_unlogged_tolerates_a_small_gap():
    block = FakeBlock("b1", at(3, 9), at(3, 11))
    # Clocked out two minutes early — well inside the default 5-minute tolerance.
    flagged = planner.flag_unlogged(
        [block], [(at(3, 9), at(3, 10, 58))], now=at(3, 12)
    )
    assert flagged == []


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


def test_autofill_rounds_the_closing_block_up_to_a_quarter():
    # 0.13h floors to zero quarters in every window it is ever offered, so
    # rounding down alone leaves the target permanently a sliver short.
    windows = planner.free_windows(
        days=[date(2026, 8, 8)], day_start=time(9, 0), day_end=time(22, 0),
        blocked=[], tz=TZ,
    )
    placed = planner.autofill(
        deficit_hours=0.13, windows=windows, clocked={}, planned=[],
        max_hours_per_day=10, min_block_minutes=0,
    )
    total = sum((end - start).total_seconds() / 3600 for start, end in placed)
    assert total == 0.25  # overshoots by under 15 minutes, rather than by never finishing


def test_autofill_rounding_up_still_respects_the_day_cap():
    windows = planner.free_windows(
        days=[date(2026, 8, 8)], day_start=time(9, 0), day_end=time(22, 0),
        blocked=[], tz=TZ,
    )
    placed = planner.autofill(
        deficit_hours=2.1, windows=windows, clocked={},
        planned=[], max_hours_per_day=2, min_block_minutes=0,
    )
    total = sum((end - start).total_seconds() / 3600 for start, end in placed)
    assert total == 2  # the cap wins over closing the gap



# ---- month summary ---------------------------------------------------------

AUG = datetime(2026, 8, 1, tzinfo=TZ)
SEP = datetime(2026, 9, 1, tzinfo=TZ)


def month(**kwargs):
    """summarise_month over August 2026, with sensible defaults."""
    return planner.summarise_month(
        target_hours=kwargs.get("target", 90.0),
        clocked=kwargs.get("clocked", {}),
        blocks=kwargs.get("blocks", []),
        sessions=kwargs.get("sessions", []),
        now=kwargs.get("now", at(20, 12)),
        month_start=AUG,
        month_end=SEP,
    )


def test_clip_trims_a_block_at_the_month_boundary():
    # 22:00 on the 31st to 02:00 on the 1st: three of those hours are August's
    # and one is September's, and the store hands back the whole thing.
    straddler = FakeBlock("b", at(31, 22), datetime(2026, 9, 1, 2, tzinfo=TZ))
    kept = planner.clip([straddler], (AUG, SEP))
    assert len(kept) == 1
    assert kept[0].hours == 2  # 22:00-24:00, not the full 4h
    assert kept[0].id == "b"   # still recognisably the same block


def test_clip_drops_blocks_outside_the_window():
    outside = FakeBlock("b", datetime(2026, 7, 4, 9, tzinfo=TZ),
                        datetime(2026, 7, 4, 12, tzinfo=TZ))
    assert planner.clip([outside], (AUG, SEP)) == []


def test_month_counts_only_days_inside_the_month():
    summary = month(clocked={
        date(2026, 7, 31): timedelta(hours=8),   # previous month
        date(2026, 8, 3): timedelta(hours=6),
        date(2026, 9, 1): timedelta(hours=8),    # next month
    })
    assert summary.clocked_hours == 6


def test_month_separates_hours_to_log_from_hours_to_place():
    # 60h logged, 20h still on the grid ahead of `now`: 30h to log, 10h to place.
    summary = month(
        clocked={date(2026, 8, 3): timedelta(hours=60)},
        blocks=[FakeBlock("b", at(25, 9), at(25, 19)),
                FakeBlock("c", at(26, 9), at(26, 19))],
    )
    assert summary.clocked_hours == 60
    assert summary.planned_future_hours == 20
    assert summary.to_log_hours == 30   # planned hours are not logged hours
    assert summary.remaining_hours == 10
    assert summary.on_track is False


def test_month_on_track_once_the_plan_covers_the_target():
    summary = month(
        clocked={date(2026, 8, 3): timedelta(hours=60)},
        blocks=[FakeBlock("b", at(25, 9), at(25, 19)),
                FakeBlock("c", at(26, 9), at(26, 19)),
                FakeBlock("d", at(27, 9), at(27, 19))],
    )
    assert summary.remaining_hours == 0     # nothing left to place
    assert summary.to_log_hours == 30       # but 30h still has to be clocked
    assert summary.on_track is True


def test_month_target_met_is_clamped_not_negative():
    summary = month(clocked={date(2026, 8, 3): timedelta(hours=95)})
    assert summary.to_log_hours == 0
    assert summary.remaining_hours == 0
    assert summary.projected_hours == 95


def test_month_days_left_counts_today():
    assert month(now=at(20, 12)).days_left == 12   # the 20th through the 31st
    assert month(now=at(31, 23)).days_left == 1    # last day still counts


def test_month_days_left_clamps_outside_the_month():
    past = month(now=datetime(2026, 10, 5, 9, tzinfo=TZ))
    assert past.days_left == 0                     # never negative
    future = month(now=datetime(2026, 6, 5, 9, tzinfo=TZ))
    assert future.days_left == 31                  # never more than August holds


def test_month_per_day_rate_survives_a_finished_month():
    over = month(clocked={date(2026, 8, 3): timedelta(hours=10)},
                 now=datetime(2026, 10, 5, 9, tzinfo=TZ))
    assert over.days_left == 0
    assert over.per_day_hours == 0   # not a division by zero


def test_month_reports_planned_time_that_never_got_logged():
    # A block on the 10th with no session under it, seen from the 20th.
    summary = month(
        blocks=[FakeBlock("b", at(10, 9), at(10, 13))],
        sessions=[(at(10, 9), at(10, 11))],  # only half of it actually happened
    )
    assert summary.unlogged_hours == 2
    assert summary.planned_past_hours == 4


def test_month_labels_come_from_the_month_not_from_today():
    summary = month(now=datetime(2026, 12, 25, 9, tzinfo=TZ))
    assert summary.as_dict()["label"] == "August"
    assert summary.as_dict()["longLabel"] == "August 2026"
    assert summary.as_dict()["start"] == "2026-08-01"


if __name__ == "__main__":
    passed = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"  ok  {name}")
            passed += 1
    print(f"\n{passed} passed")
