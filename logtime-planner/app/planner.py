"""The scheduling brain. Deliberately free of I/O so it can be tested directly.

Vocabulary used throughout:
  clocked   hours intra says you have already done
  planned   hours you have placed on the grid but not yet done
  deficit   target - clocked - planned, i.e. hours still unaccounted for
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

Interval = tuple[datetime, datetime]


def merge(intervals: list[Interval]) -> list[Interval]:
    """Collapse overlapping or touching intervals into a minimal set."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda pair: pair[0])  # sweep left to right
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:  # touches or overlaps the interval being built
            merged[-1] = (last_start, max(last_end, end))  # stretch it to cover both
        else:
            merged.append((start, end))  # disjoint — starts a new interval
    return merged


def subtract(window: Interval, blocked: list[Interval]) -> list[Interval]:
    """What is left of `window` once every blocked interval is removed."""
    window_start, window_end = window
    free: list[Interval] = []
    cursor = window_start  # how far into the window we've accounted for so far
    for start, end in merge(blocked):
        if end <= cursor or start >= window_end:
            continue  # this blocked interval doesn't touch the window at all
        if start > cursor:
            free.append((cursor, min(start, window_end)))  # gap before this block
        cursor = max(cursor, end)  # jump the cursor past the block
        if cursor >= window_end:
            break  # nothing left of the window to check
    if cursor < window_end:
        free.append((cursor, window_end))  # whatever's left after the last block
    return [pair for pair in free if pair[1] > pair[0]]  # drop any zero-length gaps


def free_windows(
    days: list[date],
    day_start: time,
    day_end: time,
    blocked: list[Interval],
    tz,
    not_before: datetime | None = None,
    min_minutes: int = 0,
) -> list[Interval]:
    """Every stretch of a day you could realistically be on campus and aren't
    already committed elsewhere."""
    windows: list[Interval] = []
    for day in days:
        opens = datetime.combine(day, day_start, tzinfo=tz)
        closes = datetime.combine(day, day_end, tzinfo=tz)
        if not_before and not_before > opens:
            opens = not_before  # never suggest a slot that's already passed
        if opens >= closes:
            continue  # this day's window is already over (or not_before pushed past it)
        for gap in subtract((opens, closes), blocked):
            if (gap[1] - gap[0]).total_seconds() / 60 >= min_minutes:
                windows.append(gap)  # long enough to be worth suggesting
    return windows


def hours_by_day(
    sessions: list[tuple[datetime, datetime]], tz
) -> dict[date, timedelta]:
    """Split sessions at local midnight and total each day.

    A session running from 21:00 Saturday to 03:00 Sunday is six hours, but it
    is not six Saturday hours — it's three and three.
    """
    totals: dict[date, timedelta] = {}
    for begin, finish in sessions:
        cursor = begin
        while cursor < finish:
            midnight = datetime.combine(
                cursor.date() + timedelta(days=1), time.min, tzinfo=tz
            )
            segment_end = min(finish, midnight)  # stop at midnight, or the session's own end
            day = cursor.date()
            totals[day] = totals.get(day, timedelta()) + (segment_end - cursor)
            cursor = segment_end  # if we stopped at midnight, the loop continues into the next day
    return totals


@dataclass
class Conflict:
    block_id: str
    reason: str
    against: str


def find_conflicts(
    blocks: list,
    busy: list,
    travel_buffer: timedelta = timedelta(0),
) -> list[Conflict]:
    """A planned block conflicts if it overlaps a calendar event, or starts so
    soon after one ends that you could not physically get there."""
    conflicts: list[Conflict] = []
    for block in blocks:
        for event in busy:
            if event.source == "error":
                continue  # a dead calendar feed reporting an error isn't a real conflict
            if block.start < event.end and event.start < block.end:  # classic overlap test
                conflicts.append(
                    Conflict(block.id, "overlaps", f"{event.title} ({event.source})")
                )
            elif timedelta(0) <= block.start - event.end < travel_buffer:
                # block starts after the event ends, but too soon to get there
                conflicts.append(
                    Conflict(block.id, "tight turnaround", f"after {event.title}")
                )
            elif timedelta(0) <= event.start - block.end < travel_buffer:
                # symmetric case: the event follows too closely after the block
                conflicts.append(
                    Conflict(block.id, "tight turnaround", f"before {event.title}")
                )
    return conflicts


@dataclass
class WeekSummary:
    target_hours: float
    clocked_hours: float
    planned_future_hours: float
    planned_past_hours: float

    @property
    def deficit_hours(self) -> float:
        # Clamped at zero — being ahead of target isn't a negative deficit.
        return max(0.0, self.target_hours - self.clocked_hours - self.planned_future_hours)

    @property
    def covered_hours(self) -> float:
        return self.clocked_hours + self.planned_future_hours

    def as_dict(self) -> dict:
        return {
            "target": round(self.target_hours, 2),
            "clocked": round(self.clocked_hours, 2),
            "plannedFuture": round(self.planned_future_hours, 2),
            "plannedPast": round(self.planned_past_hours, 2),
            "deficit": round(self.deficit_hours, 2),
            "covered": round(self.covered_hours, 2),
        }


def split_future_past(blocks: list, now: datetime) -> tuple[float, float]:
    """How many planned hours are still ahead of `now` versus already behind
    it. A block straddling `now` (started, not yet finished) splits at the
    boundary instead of being counted whole on one side — the elapsed slice
    is already showing up in `clocked`, so counting it as still-planned too
    would double-count exactly that overlap."""
    future = 0.0
    past = 0.0
    for block in blocks:
        if block.end <= now:
            past += block.hours  # entirely elapsed
        elif block.start >= now:
            future += block.hours  # hasn't started yet
        else:
            # `now` falls inside the block — split it right there.
            elapsed = (now - block.start).total_seconds() / 3600
            past += elapsed
            future += block.hours - elapsed
    return future, past


def summarise(
    target_hours: float,
    clocked: dict[date, timedelta],
    blocks: list,
    now: datetime,
) -> WeekSummary:
    clocked_hours = sum(d.total_seconds() for d in clocked.values()) / 3600
    future, past = split_future_past(blocks, now)
    return WeekSummary(target_hours, clocked_hours, future, past)


@dataclass
class UnloggedBlock:
    block_id: str
    hours: float


def flag_unlogged(
    blocks: list,
    sessions: list[Interval],
    now: datetime,
    tolerance_minutes: int = 5,
) -> list[UnloggedBlock]:
    """Past blocks whose planned time was not actually clocked, in whole or in
    part. A block within `tolerance_minutes` of full coverage doesn't count —
    intra's own rounding shouldn't trip a flag every single week."""
    tolerance = tolerance_minutes / 60
    flagged: list[UnloggedBlock] = []
    for block in blocks:
        if block.end > now:
            continue  # not due yet, nothing to compare against
        # Whatever part of the block isn't covered by a clocked session is
        # the part that got planned but never actually happened.
        gaps = subtract((block.start, block.end), sessions)
        uncovered = sum((g[1] - g[0]).total_seconds() for g in gaps) / 3600
        if uncovered > tolerance:
            flagged.append(UnloggedBlock(block.id, round(uncovered, 2)))
    return flagged


def autofill(
    deficit_hours: float,
    windows: list[Interval],
    clocked: dict[date, timedelta],
    planned: list,
    max_hours_per_day: float,
    min_block_minutes: int,
) -> list[Interval]:
    """Chronologically greedy: fill the earliest free time first, respecting a
    per-day ceiling. Earliest-first beats largest-first here because hours you
    bank early are hours that can't be lost to a cancelled Sunday."""
    remaining = deficit_hours
    if remaining <= 0:
        return []

    # used_today tracks clocked + already-planned hours per day — the basis
    # for how much headroom is left before max_hours_per_day is hit.
    used_today: dict[date, float] = {}
    for day, duration in clocked.items():
        used_today[day] = used_today.get(day, 0.0) + duration.total_seconds() / 3600
    for block in planned:
        day = block.start.date()
        used_today[day] = used_today.get(day, 0.0) + block.hours

    minimum = min_block_minutes / 60
    placed: list[Interval] = []

    for window_start, window_end in sorted(windows, key=lambda pair: pair[0]):  # earliest first
        if remaining <= 0.01:
            break  # deficit closed
        day = window_start.date()
        headroom = max_hours_per_day - used_today.get(day, 0.0)
        if headroom <= 0:
            continue  # this day is already at its cap

        available = (window_end - window_start).total_seconds() / 3600
        take = min(remaining, available, headroom)  # bounded by whichever runs out first

        # A stub is only worth placing if it finishes the job.
        if take < minimum and take < remaining - 0.01:
            continue
        if take <= 0.01:
            continue

        # Round down to the nearest 15 minutes so the grid stays tidy.
        quarters = int(take * 4)
        if quarters == 0:
            continue
        take = quarters / 4

        placed.append((window_start, window_start + timedelta(hours=take)))
        used_today[day] = used_today.get(day, 0.0) + take
        remaining -= take

    return placed
