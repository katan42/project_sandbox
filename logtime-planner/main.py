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
from .ft_api import FtApiError, client

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CACHE_TTL_SECONDS = 120

app = FastAPI(title="42 logtime planner")
_cache: dict[str, tuple[float, object]] = {}


@app.on_event("startup")
def _startup() -> None:
    store.init()


def _cached(key: str, producer, force: bool = False):
    now = _time.monotonic()
    if not force and key in _cache:
        stamped, value = _cache[key]
        if now - stamped < CACHE_TTL_SECONDS:
            return value
    value = producer()
    _cache[key] = (now, value)
    return value


def _parse(raw: str) -> datetime:
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=settings.tz)
    return moment.astimezone(settings.tz)


def _anchor(day: str | None) -> date:
    return date.fromisoformat(day) if day else datetime.now(settings.tz).date()


def _load_week(anchor: date, force: bool = False) -> dict:
    start, end = week_bounds(anchor)
    now = datetime.now(settings.tz)

    busy = _cached(f"busy:{start.date()}", lambda: busy_between(start, end), force)

    clocked: dict[date, timedelta] = {}
    open_since = None
    intra_error = None
    if settings.ft_enabled:
        try:
            clocked = _cached(
                f"clocked:{start.date()}",
                lambda: client.daily_logtime(start.date(), end.date()),
                force,
            )
            open_since = _cached("open", client.open_session_started_at, force)
        except FtApiError as exc:
            intra_error = str(exc)

    # A session still running isn't in locations_stats yet — count it live.
    live_hours = 0.0
    if open_since and start <= open_since < end:
        live_hours = (now - open_since).total_seconds() / 3600

    blocks = store.list_between(start, end)
    summary = planner.summarise(settings.weekly_target_hours, clocked, blocks, now)
    summary.clocked_hours += live_hours

    conflicts = planner.find_conflicts(
        blocks, busy, timedelta(minutes=settings.travel_buffer_minutes)
    )

    return {
        "weekStart": start.isoformat(),
        "weekEnd": end.isoformat(),
        "now": now.isoformat(),
        "timezone": settings.timezone,
        "summary": summary.as_dict(),
        "liveHours": round(live_hours, 2),
        "openSince": open_since.isoformat() if open_since else None,
        "clockedByDay": {
            day.isoformat(): round(duration.total_seconds() / 3600, 2)
            for day, duration in sorted(clocked.items())
        },
        "blocks": [block.as_dict() for block in blocks],
        "busy": [event.as_dict() for event in busy],
        "conflicts": [
            {"blockId": c.block_id, "reason": c.reason, "against": c.against}
            for c in conflicts
        ],
        "intraError": intra_error,
        "dayWindow": {
            "start": settings.day_window_start.strftime("%H:%M"),
            "end": settings.day_window_end.strftime("%H:%M"),
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


@app.get("/api/week")
def get_week(date_: str | None = None, refresh: bool = False):
    return _load_week(_anchor(date_), force=refresh)


@app.post("/api/blocks")
def add_block(payload: BlockIn):
    start, end = _parse(payload.start), _parse(payload.end)
    if end <= start:
        raise HTTPException(400, "A block has to end after it starts.")
    return store.create(start, end, payload.note).as_dict()


@app.patch("/api/blocks/{block_id}")
def edit_block(block_id: str, payload: BlockPatch):
    if store.get(block_id) is None:
        raise HTTPException(404, "That block no longer exists.")
    start, end = _parse(payload.start), _parse(payload.end)
    if end <= start:
        raise HTTPException(400, "A block has to end after it starts.")
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

    busy = busy_between(start, end)
    blocks = store.list_between(start, end)
    blocked = [(_parse(e.start.isoformat()), _parse(e.end.isoformat())) for e in busy]
    blocked += [(block.start, block.end) for block in blocks]

    days = [(start + timedelta(days=offset)).date() for offset in range(7)]
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
        store.create(begins, finishes, "auto")

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


@app.post("/api/push")
def push(date_: str | None = None):
    start, end = week_bounds(_anchor(date_))
    try:
        return caldav_sync.push_week(start, end)
    except Exception as exc:
        raise HTTPException(502, f"iCloud push failed: {exc}")


@app.get("/api/health")
def health():
    return {
        "intra": settings.ft_enabled,
        "icloud": settings.icloud_enabled,
        "googleFeeds": len(settings.google_ics_urls),
        "target": settings.weekly_target_hours,
        "timezone": settings.timezone,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
