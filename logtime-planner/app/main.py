"""HTTP layer. Everything slow (intra, CalDAV, .ics fetches) is cached per week
for a short TTL so dragging a block around doesn't re-hit the network."""

from __future__ import annotations

import math
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import caldav_sync, planner, store
from .calendars import busy_between, month_bounds, month_of_week, week_bounds
from .config import settings
from .ft_api import SECRET_WARN_DAYS, FtApiError, client

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CACHE_TTL_SECONDS = 120

app = FastAPI(title="42 logtime planner")
_cache: dict[str, tuple[float, object]] = {}


@app.on_event("startup")
def _startup() -> None:
    store.init()


def _cached(key: str, producer, force: bool = False):
    """Simple in-memory TTL cache, one entry per key. `force` (the "refresh"
    query param / button) bypasses it to guarantee fresh data."""
    now = _time.monotonic()
    if not force and key in _cache:
        stamped, value = _cache[key]
        if now - stamped < CACHE_TTL_SECONDS:
            return value
    value = producer()
    _cache[key] = (now, value)
    return value


def _parse(raw: str) -> datetime:
    """Turn a request's ISO string into a tz-aware datetime in the app's
    timezone. A bare (tz-less) string is assumed to already be local time."""
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=settings.tz)
    return moment.astimezone(settings.tz)


def _anchor(day: str | None) -> date:
    """Any date the caller wants to view; defaults to today when omitted."""
    return date.fromisoformat(day) if day else datetime.now(settings.tz).date()


def _load_month(anchor: date, force: bool = False) -> dict:
    """Calendar-month progress against MONTHLY_TARGET_HOURS, for the month
    containing `anchor` — not for whatever month it happens to be today."""
    start, end = month_bounds(anchor)
    now = datetime.now(settings.tz)

    clocked: dict[date, timedelta] = {}
    sessions: list[tuple[datetime, datetime]] = []
    intra_error = None
    if settings.ft_enabled:
        try:
            all_logtime = _cached("logtime", client.all_logtime, force)
            clocked = {
                day: duration
                for day, duration in all_logtime.items()
                if start.date() <= day < end.date()
            }
            # Sessions on top of the daily rollup, for the same reason the
            # week view prefers them: locations_stats leaves out a session
            # that is still running, so this is the only route by which time
            # you are clocking *right now* reaches the month total.
            sessions[:] = _cached(
                f"sessions:month:{start.date()}",
                lambda: client.sessions_between(start, end),
                force,
            )
            clocked.update(planner.hours_by_day(sessions, settings.tz))
        except FtApiError as exc:
            intra_error = str(exc)  # keep the coarser rollup we already have

    blocks = store.list_between(start, end)
    summary = planner.summarise_month(
        settings.monthly_target_hours, clocked, blocks, sessions, now, start, end
    )
    unlogged = (
        planner.flag_unlogged(planner.clip(blocks, (start, end)), sessions, now)
        if settings.ft_enabled
        else []
    )

    return {
        "monthStart": start.isoformat(),
        "monthEnd": end.isoformat(),
        "now": now.isoformat(),
        "timezone": settings.timezone,
        "summary": summary.as_dict(),
        "clockedByDay": {
            day.isoformat(): round(duration.total_seconds() / 3600, 2)
            for day, duration in sorted(clocked.items())
        },
        "blocks": [block.as_dict() for block in blocks],
        "unlogged": [{"blockId": u.block_id, "hours": u.hours} for u in unlogged],
        "intraError": intra_error,
    }


def _load_week(anchor: date, force: bool = False) -> dict:
    start, end = week_bounds(anchor)
    now = datetime.now(settings.tz)

    busy = _cached(f"busy:{start.date()}", lambda: busy_between(start, end), force)

    all_logtime: dict[date, timedelta] = {}
    open_since = None
    intra_error = None
    clocked: dict[date, timedelta] = {}
    sessions: list[tuple[datetime, datetime]] = []
    if settings.ft_enabled:
        try:
            all_logtime = _cached("logtime", client.all_logtime, force)
            open_since = _cached("open", client.open_session_started_at, force)
            # Raw sessions, not the daily rollup: this is the only way to be
            # sure about a session that is still running or crosses midnight.
            sessions[:] = _cached(
                f"sessions:{start.date()}",
                lambda: client.sessions_between(start, end),
                force,
            )
            clocked = planner.hours_by_day(sessions, settings.tz)
        except FtApiError as exc:
            intra_error = str(exc)
            # intra is down or the token expired — fall back to the coarser
            # daily rollup so the week still shows *something*.
            clocked = {
                day: duration
                for day, duration in all_logtime.items()
                if start.date() <= day < end.date()
            }

    # Session data already includes anything still running, so there is nothing
    # to add on top. Kept only so the UI can say a session is in progress.
    live_hours = 0.0

    blocks = store.list_between(start, end)
    summary = planner.summarise(settings.weekly_target_hours, clocked, blocks, now)
    summary.clocked_hours += live_hours

    clocked_by_day = {
        day.isoformat(): round(duration.total_seconds() / 3600, 2)
        for day, duration in sorted(clocked.items())
    }

    # The strip under the week rail reports on the month this week mostly
    # falls in, so stepping the grid into a new month moves the strip with it.
    month = _load_month(month_of_week(start.date()), force)["summary"]

    conflicts = planner.find_conflicts(
        blocks, busy, timedelta(minutes=settings.travel_buffer_minutes)
    )

    # Only meaningful once intra has weighed in — without it there is no
    # source of truth to compare the plan against, and everything past would
    # look unlogged.
    unlogged = planner.flag_unlogged(blocks, sessions, now) if settings.ft_enabled else []

    summary_dict = summary.as_dict()
    # Not part of WeekSummary itself — it's a separate reconciliation figure,
    # tacked on to the same dict the UI already reads its stats from.
    summary_dict["plannedPastUnlogged"] = round(sum(u.hours for u in unlogged), 2)

    return {
        "weekStart": start.isoformat(),
        "weekEnd": end.isoformat(),
        "now": now.isoformat(),
        "timezone": settings.timezone,
        "summary": summary_dict,
        "liveHours": round(live_hours, 2),
        "openSince": open_since.isoformat() if open_since else None,
        "clockedByDay": clocked_by_day,
        "sessions": [
            {
                "start": begin.isoformat(),
                "end": finish.isoformat(),
                "hours": round((finish - begin).total_seconds() / 3600, 2),
                # A session counts as "open" if intra reports one started and
                # this session's end is right at "now" — i.e. it's the one
                # still running, not a session that merely finished recently.
                "open": open_since is not None and finish >= now - timedelta(minutes=1),
            }
            for begin, finish in sorted(sessions)
        ],
        "month": month,
        "blocks": [block.as_dict() for block in blocks],
        "busy": [event.as_dict() for event in busy],
        "conflicts": [
            {"blockId": c.block_id, "reason": c.reason, "against": c.against}
            for c in conflicts
        ],
        "unlogged": [{"blockId": u.block_id, "hours": u.hours} for u in unlogged],
        # Every tag in use, so the goal editor can offer them without a
        # second round trip — a project name gets typed in full once.
        "tags": store.known_tags(),
        "intraError": intra_error,
        "dayWindow": {  # where auto-fill may place blocks
            "start": settings.day_window_start.strftime("%H:%M"),
            "end": settings.day_window_end.strftime("%H:%M"),
        },
        "gridWindow": {  # what the grid draws
            "start": settings.grid_start,
            "end": settings.grid_end,
        },
        "icloudEnabled": settings.icloud_enabled,
    }


class BlockIn(BaseModel):
    start: str
    end: str
    # One short line of what this session is for, plus any number of tags
    # ("CPP00", "exam"). Both optional — a block with neither is still a
    # perfectly ordinary block of planned hours.
    goal: str = ""
    tags: list[str] | str = ""


class BlockPatch(BaseModel):
    """Every field optional: dragging a block sends times only, and editing
    its goal from the goals page sends no times at all."""

    start: str | None = None
    end: str | None = None
    goal: str | None = None
    tags: list[str] | str | None = None


class DoneIn(BaseModel):
    done: bool = True


def _reject_day_overload(
    start: datetime, end: datetime, exclude_id: str | None = None
) -> None:
    """A block you place yourself may fill a day right up to
    MANUAL_MAX_HOURS_PER_DAY. Auto-fill stops far earlier, at
    PLAN_MAX_HOURS_PER_DAY — what the planner is willing to suggest and what
    you are allowed to commit to are not the same question, and only the
    second one belongs in a validator."""
    cap = settings.manual_max_hours_per_day

    # Widened to whole local days: a day's total includes blocks that start
    # before this one or run past it, not just the slice inside [start, end).
    first = datetime.combine(start.date(), datetime.min.time(), tzinfo=settings.tz)
    last = datetime.combine(
        end.date() + timedelta(days=1), datetime.min.time(), tzinfo=settings.tz
    )
    others = [b for b in store.list_between(first, last) if b.id != exclude_id]
    intervals = [(b.start, b.end) for b in others] + [(start, end)]

    # hours_by_day splits at midnight, so an overnight block is charged to the
    # two days it actually covers rather than counted twice over.
    for day, total in planner.hours_by_day(intervals, settings.tz).items():
        if day < start.date() or day > end.date():
            continue  # only the days this block touches are this block's problem
        hours = total.total_seconds() / 3600
        if hours > cap + 0.01:
            raise HTTPException(
                400,
                f"That would put {hours:.1f}h of planned time on "
                f"{day.strftime('%a %-d %b')} — the limit is {cap:g}h a day.",
            )


def _reject_overlap(start: datetime, end: datetime, exclude_id: str | None = None) -> None:
    """Two planned blocks covering the same minute would double-count that
    minute in every hours total, so the grid must stay a partition, not a
    multiset."""
    clash = next(
        (b for b in store.list_between(start, end) if b.id != exclude_id), None
    )
    if clash is not None:
        raise HTTPException(
            400,
            "That overlaps an existing block "
            f"({clash.start.strftime('%a %H:%M')}–{clash.end.strftime('%H:%M')}).",
        )


@app.get("/api/week")
def get_week(date_: str | None = None, refresh: bool = False):
    return _load_week(_anchor(date_), force=refresh)


@app.get("/api/month")
def get_month(date_: str | None = None, refresh: bool = False):
    return _load_month(_anchor(date_), force=refresh)


@app.post("/api/blocks")
def add_block(payload: BlockIn):
    start, end = _parse(payload.start), _parse(payload.end)
    if end <= start:
        raise HTTPException(400, "A block has to end after it starts.")
    _reject_overlap(start, end)
    _reject_day_overload(start, end)
    return store.create(start, end, payload.goal, payload.tags).as_dict()


@app.patch("/api/blocks/{block_id}")
def edit_block(block_id: str, payload: BlockPatch):
    block = store.get(block_id)
    if block is None:
        raise HTTPException(404, "That block no longer exists.")

    # A goal-only edit carries no times, so fall back to where the block
    # already sits — the overlap and day-cap checks still have to run against
    # a real interval, and nothing about them changes when only text moved.
    start = _parse(payload.start) if payload.start else block.start
    end = _parse(payload.end) if payload.end else block.end
    if end <= start:
        raise HTTPException(400, "A block has to end after it starts.")
    if (start, end) != (block.start, block.end):
        _reject_overlap(start, end, exclude_id=block_id)
        _reject_day_overload(start, end, exclude_id=block_id)

    return store.update(
        block_id, start, end, goal=payload.goal, tags=payload.tags
    ).as_dict()


@app.post("/api/blocks/{block_id}/done")
def set_block_done(block_id: str, payload: DoneIn):
    """Tick a goal off, or untick it. Separate from the generic patch because
    it is the one edit that doesn't invalidate the iCloud copy."""
    if store.get(block_id) is None:
        raise HTTPException(404, "That block no longer exists.")
    return store.set_done(block_id, payload.done).as_dict()


@app.delete("/api/blocks/{block_id}")
def remove_block(block_id: str):
    store.delete(block_id)
    return {"ok": True}


def _goal_bounds(scope: str, anchor: date) -> tuple[datetime | None, datetime | None, str]:
    """The span the goals page is reporting on, and what to call it."""
    if scope == "week":
        start, end = week_bounds(anchor)
        label = (
            f"{start.strftime('%-d %b')} – "
            f"{(end - timedelta(days=1)).strftime('%-d %b')}"
        )
        return start, end, label
    if scope == "all":
        return None, None, "Everything"
    start, end = month_bounds(anchor)
    return start, end, start.strftime("%B %Y")


@app.get("/api/goals")
def get_goals(date_: str | None = None, scope: str = "month", tag: str | None = None):
    """Every planned block in the span, grouped by the day it starts on.

    Blocks without a goal are included rather than filtered out server-side:
    they are exactly the ones worth prompting you to name, and the page can
    hide them with a toggle if you'd rather not see them.
    """
    anchor = _anchor(date_)
    now = datetime.now(settings.tz)
    start, end, label = _goal_bounds(scope, anchor)
    blocks = store.list_all() if start is None else store.list_between(start, end)

    wanted = tag.strip().lower() if tag and tag.strip() else None
    if wanted:
        blocks = [b for b in blocks if any(t.lower() == wanted for t in b.tags)]

    days: dict[str, list[dict]] = {}
    for block in blocks:
        item = block.as_dict()
        item["past"] = block.end <= now
        days.setdefault(block.start.date().isoformat(), []).append(item)

    with_goal = [b for b in blocks if b.goal]
    done = [b for b in with_goal if b.done]
    # "Missed" is the number the page exists to surface: a goal whose session
    # has already been and gone, still sitting unticked.
    missed = [b for b in with_goal if not b.done and b.end <= now]

    return {
        "scope": scope,
        "label": label,
        "from": start.isoformat() if start else None,
        "to": end.isoformat() if end else None,
        "now": now.isoformat(),
        "tag": tag or None,
        "tags": store.known_tags(),
        "days": [
            {"date": day, "items": items} for day, items in sorted(days.items())
        ],
        "summary": {
            "blocks": len(blocks),
            "goals": len(with_goal),
            "done": len(done),
            "missed": len(missed),
            "untitled": len(blocks) - len(with_goal),
            "hours": round(sum(b.hours for b in with_goal), 2),
            "doneHours": round(sum(b.hours for b in done), 2),
        },
    }


@app.post("/api/rebalance")
def rebalance(date_: str | None = None, scope: str = "week"):
    """Place blocks in the earliest free time until the gap is closed.

    `scope=week` fills this week up to WEEKLY_TARGET_HOURS. `scope=month`
    fills the rest of the month up to MONTHLY_TARGET_HOURS and deliberately
    ignores the weekly target: the weekly figure is a pacing device, and
    treating it as a ceiling can make the monthly target unreachable outright
    (four 20h weeks is 80h, which never gets you to 90).
    PLAN_MAX_HOURS_PER_DAY still applies — that is the most this will ever
    suggest for one day, and it is deliberately lower than what you are
    allowed to commit to by hand.
    """
    anchor = _anchor(date_)
    now = datetime.now(settings.tz)

    if scope == "month":
        view = _load_month(anchor)
        start, end = month_bounds(anchor)
        deficit = view["summary"]["remaining"]
        subject = view["summary"]["longLabel"]
    else:
        view = _load_week(anchor)
        start, end = week_bounds(anchor)
        deficit = view["summary"]["deficit"]
        subject = "the week"

    if deficit <= 0:
        return {
            "placed": 0,
            "hours": 0.0,
            "shortfall": 0.0,
            "detail": f"Nothing to place — {subject} is covered.",
        }

    if scope == "month" and view["summary"]["daysLeft"] < 1:
        # Every window in a finished month is behind `not_before`, so the fill
        # would place nothing and then blame your day window for it.
        return {
            "placed": 0,
            "hours": 0.0,
            "shortfall": round(deficit, 2),
            "detail": f"{subject} is over — those hours can't be planned now.",
        }

    # Padded by the travel buffer so autofill never lands a block so tight
    # against a calendar event that it's an instant conflict — that gap is
    # yours to close by hand if you want to, just not something we'll suggest.
    buffer = timedelta(minutes=settings.travel_buffer_minutes)
    busy = busy_between(start, end)
    blocks = store.list_between(start, end)
    fixed = [
        (event.start - buffer, event.end + buffer)
        for event in busy
        if event.source != "error"  # a dead feed isn't a reason to block out time
    ]
    fixed += [(block.start, block.end) for block in blocks]  # your own plans block, too

    span = (end - start).days  # 7, or however long the month is
    days = [(start + timedelta(days=offset)).date() for offset in range(span)]
    clocked = {
        date.fromisoformat(day): timedelta(hours=hours)
        for day, hours in view["clockedByDay"].items()
    }

    # Autofill is chronologically greedy, which is right for a week: the
    # deficit is small and hours banked early can't be lost to a cancelled
    # Sunday. Spread over a month it would empty the entire target into the
    # next day or two — technically inside PLAN_MAX_HOURS_PER_DAY, but not a plan
    # anyone can keep. So a month fill goes round twice: once at the pace the
    # month actually needs, and again at the real daily ceiling only if that
    # left a shortfall.
    caps = [settings.plan_max_hours_per_day]
    if scope == "month":
        days_left = max(1, view["summary"]["daysLeft"])
        pace = math.ceil((deficit / days_left) * 4) / 4  # to the quarter hour
        pace = max(pace, settings.min_block_minutes / 60)  # still placeable
        ceiling = settings.plan_max_hours_per_day
        caps = [min(pace, ceiling), ceiling]

    placements: list[tuple[datetime, datetime]] = []
    for cap in caps:
        shortfall = deficit - sum((f - b).total_seconds() / 3600 for b, f in placements)
        if shortfall <= 0.01:
            break
        windows = planner.free_windows(
            days=days,
            day_start=settings.day_window_start,
            day_end=settings.day_window_end,
            blocked=fixed + placements,  # earlier passes hold their ground
            tz=settings.tz,
            not_before=now,  # days already gone are skipped outright
            min_minutes=15,
        )
        placements += planner.autofill(
            deficit_hours=shortfall,
            windows=windows,
            clocked=clocked,
            # Per-day headroom has to see this pass's own placements too, or
            # the second pass would hand the same day its full cap again.
            planned=blocks + [store.Block("", b, f) for b, f in placements],
            max_hours_per_day=cap,
            min_block_minutes=settings.min_block_minutes,
        )

    for begins, finishes in placements:
        store.create(begins, finishes, "")  # persist each suggested slot as a real block

    placed_hours = sum((f - b).total_seconds() / 3600 for b, f in placements)
    # Clamped: rounding the last block up to a quarter hour can overshoot the
    # deficit slightly, and "-0.1h short" is not a thing.
    shortfall = round(max(0.0, deficit - placed_hours), 2)
    covered = subject[0].upper() + subject[1:]  # not .capitalize(), which would
    return {                                    # flatten "September" to "september"
        "placed": len(placements),
        "hours": round(placed_hours, 2),
        "shortfall": shortfall,
        "detail": (
            f"Placed {placed_hours:.1f}h. Still {shortfall:.1f}h short — "
            "widen your day window or free up time."
            if shortfall > 0.01
            else f"Placed {placed_hours:.1f}h. {covered} is covered."
        ),
    }


@app.post("/api/pull")
def pull(date_: str | None = None):
    """Reconcile deletions made in Calendar.app back into the planner."""
    start, end = week_bounds(_anchor(date_))
    try:
        return caldav_sync.pull_week(start, end)
    except Exception as exc:
        raise HTTPException(502, f"iCloud pull failed: {exc}")


@app.post("/api/push")
def push(date_: str | None = None):
    start, end = week_bounds(_anchor(date_))
    try:
        return caldav_sync.push_week(start, end)
    except Exception as exc:
        raise HTTPException(502, f"iCloud push failed: {exc}")


@app.get("/api/health")
def health():
    secret = {"daysLeft": None, "expiresOn": None, "warn": False, "error": None}
    if settings.ft_enabled:
        try:
            client._access_token()  # populates the expiry fields
            secret["daysLeft"] = client.secret_days_left
            secret["expiresOn"] = (
                client.secret_expires_on.date().isoformat()
                if client.secret_expires_on
                else None
            )
            secret["warn"] = (
                client.secret_days_left is not None
                and client.secret_days_left < SECRET_WARN_DAYS
            )
        except FtApiError as exc:
            secret["error"] = str(exc)

    return {
        "intra": settings.ft_enabled,
        "icloud": settings.icloud_enabled,
        "googleFeeds": len(settings.google_ics_urls),
        "target": settings.weekly_target_hours,
        "timezone": settings.timezone,
        "secret": secret,
    }


@app.post("/api/secret-reminder")
def secret_reminder():
    """Push an all-day iCloud reminder on the 42 client secret's expiry date."""
    if client.secret_expires_on is None:
        raise HTTPException(400, "Secret expiry isn't known yet — load /api/health first.")
    try:
        result = caldav_sync.push_secret_reminder(client.secret_expires_on.date())
    except Exception as exc:
        raise HTTPException(502, f"Could not add the iCloud reminder: {exc}")
    if not result["ok"]:
        raise HTTPException(400, result["detail"])
    return result


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
