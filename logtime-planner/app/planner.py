"""The scheduling brain. Deliberately free of I/O so it can be tested directly.

Vocabulary used throughout:
  clocked   hours intra says you have already done
  planned   hours you have placed on the grid but not yet done
  deficit   target - clocked - planned, i.e. hours still unaccounted for
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
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
    # The uncovered stretches themselves, so the UI can hatch just those
    # rather than the whole block.
    gaps: list[Interval] = field(default_factory=list)


def flag_unlogged(
    blocks: list,
    sessions: list[Interval],
    now: datetime,
    tolerance_minutes: int = 5,
) -> list[UnloggedBlock]:
    """Blocks whose planned time — so far — was not actually clocked, in whole
    or in part. A block still in progress is judged on the part that has
    already elapsed: planning 06:00 and clocking in at noon is a miss now, not
    once the block ends. A block within `tolerance_minutes` of full coverage
    doesn't count — intra's own rounding shouldn't trip a flag every week."""
    tolerance = tolerance_minutes / 60
    flagged: list[UnloggedBlock] = []
    for block in blocks:
        if block.start >= now:
            continue  # not started yet, nothing to compare against
        # Whatever elapsed part of the block isn't covered by a clocked
        # session is the part that got planned but never actually happened.
        gaps = subtract((block.start, min(block.end, now)), sessions)
        uncovered = sum((g[1] - g[0]).total_seconds() for g in gaps) / 3600
        if uncovered > tolerance:
            flagged.append(UnloggedBlock(block.id, round(uncovered, 2), gaps))
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

        # Snap to 15 minutes so the grid stays tidy. Rounding down alone would
        # strand a sub-quarter sliver of deficit that no later window can place
        # either — 0.13h floors to zero in every window it is offered — so the
        # piece that closes the gap rounds up instead, never past what the
        # window or the day can actually hold.
        if take >= remaining - 0.01:
            quarters = min(math.ceil(take * 4), int(available * 4), int(headroom * 4))
        else:
            quarters = int(take * 4)
        if quarters == 0:
            continue
        take = quarters / 4

        placed.append((window_start, window_start + timedelta(hours=take)))
        used_today[day] = used_today.get(day, 0.0) + take
        remaining -= take

    return placed


def clip(blocks: list, window: Interval) -> list:
    """Blocks trimmed to `window`, dropping the ones outside it entirely.

    The store returns anything *overlapping* a window, so a block straddling a
    month boundary arrives whole. Counting its full length against one month
    would credit that month with hours actually spent in the other.
    """
    start, end = window
    kept: list = []
    for block in blocks:
        if block.end <= start or block.start >= end:
            continue  # no part of this block falls inside the window
        if block.start >= start and block.end <= end:
            kept.append(block)  # already inside — no copy needed
        else:
            kept.append(
                replace(block, start=max(block.start, start), end=min(block.end, end))
            )
    return kept


@dataclass
class MonthSummary:
    """The month equivalent of WeekSummary, with one extra distinction the
    week view doesn't need: hours you still have to *log* versus hours you
    haven't even put on the grid yet. Late in the month those are the two
    numbers that matter, and they are not the same number."""

    target_hours: float
    clocked_hours: float
    planned_future_hours: float
    planned_past_hours: float
    unlogged_hours: float
    days_left: int
    first_day: date

    @property
    def projected_hours(self) -> float:
        """Where you land if you show up to everything still on the grid."""
        return self.clocked_hours + self.planned_future_hours

    # The monthly target has to be *exceeded*: exactly 90h logged is not 90h
    # met. `met` and `on_track` are the only verdicts; the hour figures below
    # can read 0 at exact equality while neither is true yet.

    @property
    def met(self) -> bool:
        return self.clocked_hours > self.target_hours

    @property
    def to_log_hours(self) -> float:
        """Hours that still have to appear on intra, planned or not. This is
        the honest month-end number — a planned block is not a logged hour."""
        return 0.0 if self.met else max(0.0, self.target_hours - self.clocked_hours)

    @property
    def remaining_hours(self) -> float:
        """Hours that aren't even on the grid yet."""
        return 0.0 if self.on_track else max(0.0, self.target_hours - self.projected_hours)

    @property
    def on_track(self) -> bool:
        return self.projected_hours > self.target_hours

    @property
    def per_day_hours(self) -> float:
        """What `to_log_hours` works out to per remaining day. Zero days left
        means the question is moot, not that the rate is infinite."""
        if self.days_left <= 0:
            return 0.0
        return self.to_log_hours / self.days_left

    def as_dict(self) -> dict:
        return {
            "target": round(self.target_hours, 2),
            "clocked": round(self.clocked_hours, 2),
            "plannedFuture": round(self.planned_future_hours, 2),
            "plannedPast": round(self.planned_past_hours, 2),
            "unlogged": round(self.unlogged_hours, 2),
            "projected": round(self.projected_hours, 2),
            "toLog": round(self.to_log_hours, 2),
            "remaining": round(self.remaining_hours, 2),
            "perDay": round(self.per_day_hours, 2),
            "onTrack": self.on_track,
            "met": self.met,
            "daysLeft": self.days_left,
            # `start` lets any consumer of this dict re-request the same month
            # without having to parse the label back into a date.
            "start": self.first_day.isoformat(),
            "label": self.first_day.strftime("%B"),
            "longLabel": self.first_day.strftime("%B %Y"),
        }


def summarise_month(
    target_hours: float,
    clocked: dict[date, timedelta],
    blocks: list,
    sessions: list[Interval],
    now: datetime,
    month_start: datetime,
    month_end: datetime,
) -> MonthSummary:
    first, last = month_start.date(), month_end.date()
    clocked_hours = sum(
        duration.total_seconds()
        for day, duration in clocked.items()
        if first <= day < last
    ) / 3600

    in_month = clip(blocks, (month_start, month_end))
    future, past = split_future_past(in_month, now)
    unlogged = sum(item.hours for item in flag_unlogged(in_month, sessions, now))

    # Counts today as a day you can still use. Clamped at both ends so a month
    # you've navigated away from reads 0 (past) or its full length (future)
    # rather than a nonsense negative or a number larger than the month.
    days_left = max(0, min((last - now.date()).days, (last - first).days))

    return MonthSummary(
        target_hours=target_hours,
        clocked_hours=clocked_hours,
        planned_future_hours=future,
        planned_past_hours=past,
        unlogged_hours=unlogged,
        days_left=days_left,
        first_day=first,
    )


@dataclass
class PeriodTotal:
    """Clocked hours for one month or one week of the summary history."""

    start: date
    end: date  # exclusive
    hours: float
    days: int  # days with any time logged at all

    def as_dict(self, today: date) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "hours": round(self.hours, 2),
            "days": self.days,
            # Still running, so its total is "so far", not final.
            "current": self.start <= today < self.end,
        }


def _next_month(first: date) -> date:
    return date(first.year + first.month // 12, first.month % 12 + 1, 1)


def _total(clocked: dict[date, timedelta], start: date, end: date, since: date) -> PeriodTotal:
    inside = [
        duration
        for day, duration in clocked.items()
        if max(start, since) <= day < end and duration > timedelta()
    ]
    return PeriodTotal(
        start, end, sum(d.total_seconds() for d in inside) / 3600, len(inside)
    )


def totals_by_month(
    clocked: dict[date, timedelta], since: date, today: date
) -> list[PeriodTotal]:
    """Every calendar month from `since`'s month through today's, oldest
    first. Months with nothing logged are kept at zero — a gap in the history
    is part of the history."""
    out: list[PeriodTotal] = []
    first = since.replace(day=1)
    while first <= today:
        following = _next_month(first)
        out.append(_total(clocked, first, following, since))
        first = following
    return out


def totals_by_week(
    clocked: dict[date, timedelta], since: date, today: date, week_start_day: int
) -> list[PeriodTotal]:
    """Every logtime week from the first one starting on or after `since`
    through the current one, oldest first — no part-week at the start.
    `week_start_day` is 0=Monday..6=Sunday, as in config."""
    out: list[PeriodTotal] = []
    first = since + timedelta(days=(week_start_day - since.weekday()) % 7)
    while first <= today:
        following = first + timedelta(days=7)
        out.append(_total(clocked, first, following, since))
        first = following
    return out
