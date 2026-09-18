"""HTTP layer. Everything slow (intra, CalDAV, .ics fetches) is cached per week
for a short TTL so dragging a block around doesn't re-hit the network."""

from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import caldav_sync, planner, store
from .calendars import busy_between, week_bounds
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


def _month_summary(
    all_logtime: dict[date, timedelta], live_hours: float, now: datetime
) -> dict:
    """Calendar-month progress against MONTHLY_TARGET_HOURS."""
    first = now.date().replace(day=1)  # first day of the current month
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)  # wrap into January
    else:
        next_first = first.replace(month=first.month + 1)

    clocked = sum(
        duration.total_seconds()
        for day, duration in all_logtime.items()
        if first <= day < next_first
    ) / 3600
    clocked += live_hours

    month_start = datetime.combine(first, datetime.min.time(), tzinfo=settings.tz)
    month_end = datetime.combine(next_first, datetime.min.time(), tzinfo=settings.tz)
    planned, _ = planner.split_future_past(
        store.list_between(month_start, month_end), now
    )

    target = settings.monthly_target_hours
    days_left = (next_first - now.date()).days
    return {
        "target": round(target, 2),
        "clocked": round(clocked, 2),
        "planned": round(planned, 2),
        "remaining": round(max(0.0, target - clocked - planned), 2),
        "daysLeft": days_left,
        "label": first.strftime("%B"),
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

    merged = dict(all_logtime)
    merged.update(clocked)  # session data wins for the days it covers
    month = _month_summary(merged, live_hours, now)

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
    note: str = ""


class BlockPatch(BaseModel):
    start: str
    end: str
    note: str | None = None


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


@app.post("/api/blocks")
def add_block(payload: BlockIn):
    start, end = _parse(payload.start), _parse(payload.end)
    if end <= start:
        raise HTTPException(400, "A block has to end after it starts.")
    _reject_overlap(start, end)
    return store.create(start, end, payload.note).as_dict()


@app.patch("/api/blocks/{block_id}")
def edit_block(block_id: str, payload: BlockPatch):
    if store.get(block_id) is None:
        raise HTTPException(404, "That block no longer exists.")
    start, end = _parse(payload.start), _parse(payload.end)
    if end <= start:
        raise HTTPException(400, "A block has to end after it starts.")
    _reject_overlap(start, end, exclude_id=block_id)
    return store.update(block_id, start, end, payload.note).as_dict()


@app.delete("/api/blocks/{block_id}")
def remove_block(block_id: str):
    store.delete(block_id)
    return {"ok": True}


@app.post("/api/rebalance")
def rebalance(date_: str | None = None):
    """Place blocks in the earliest free time until the deficit is closed."""
    anchor = _anchor(date_)
    week = _load_week(anchor)
    start, end = week_bounds(anchor)
    now = datetime.now(settings.tz)

    deficit = week["summary"]["deficit"]
    if deficit <= 0:
        return {"placed": 0, "detail": "Nothing to place — the week is covered."}

    # Padded by the travel buffer so autofill never lands a block so tight
    # against a calendar event that it's an instant conflict — that gap is
    # yours to close by hand if you want to, just not something we'll suggest.
    buffer = timedelta(minutes=settings.travel_buffer_minutes)
    busy = busy_between(start, end)
    blocks = store.list_between(start, end)
    blocked = [
        (_parse(e.start.isoformat()) - buffer, _parse(e.end.isoformat()) + buffer)
        for e in busy
    ]
    blocked += [(block.start, block.end) for block in blocks]  # your own plans block, too

    days = [(start + timedelta(days=offset)).date() for offset in range(7)]  # every day of the week
    windows = planner.free_windows(
        days=days,
        day_start=settings.day_window_start,
        day_end=settings.day_window_end,
        blocked=blocked,
        tz=settings.tz,
        not_before=now,
        min_minutes=15,
    )

    clocked = {
        date.fromisoformat(day): timedelta(hours=hours)
        for day, hours in week["clockedByDay"].items()
    }
    placements = planner.autofill(
        deficit_hours=deficit,
        windows=windows,
        clocked=clocked,
        planned=blocks,
        max_hours_per_day=settings.max_hours_per_day,
        min_block_minutes=settings.min_block_minutes,
    )

    for begins, finishes in placements:
        store.create(begins, finishes, "")  # persist each suggested slot as a real block

    placed_hours = sum((f - b).total_seconds() / 3600 for b, f in placements)
    shortfall = round(deficit - placed_hours, 2)
    return {
        "placed": len(placements),
        "hours": round(placed_hours, 2),
        "shortfall": shortfall,
        "detail": (
            f"Placed {placed_hours:.1f}h. Still {shortfall:.1f}h short — "
            "widen your day window or free up time."
            if shortfall > 0.01
            else f"Placed {placed_hours:.1f}h. Week is covered."
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
