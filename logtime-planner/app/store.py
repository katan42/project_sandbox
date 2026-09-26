"""Planned blocks live here. Actual hours never do — those always come from
intra, so there is only ever one source of truth for what you really clocked.

A block may also carry a *goal*: one short line of what that session is for,
plus any number of tags (a project, an exam). The goal is optional everywhere —
a block without one is still a perfectly ordinary block of planned hours."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    id         TEXT PRIMARY KEY,
    start_at   TEXT NOT NULL,
    end_at     TEXT NOT NULL,
    goal       TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    done_at    TEXT,
    pushed_at  TEXT
);
CREATE INDEX IF NOT EXISTS blocks_start ON blocks (start_at);
"""

# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT
# EXISTS", so init() checks the live table and applies only what's missing —
# which also makes this a no-op on a database created from SCHEMA above.
ADDED_COLUMNS = {
    "goal": "ALTER TABLE blocks ADD COLUMN goal TEXT NOT NULL DEFAULT ''",
    "tags": "ALTER TABLE blocks ADD COLUMN tags TEXT NOT NULL DEFAULT ''",
    "done_at": "ALTER TABLE blocks ADD COLUMN done_at TEXT",
}

GOAL_MAX_CHARS = 120  # one short line — it has to fit in a calendar event title
TAG_MAX_CHARS = 24

_TAG_STRIP = re.compile(r"^[#\s]+|\s+$")


def normalise_tags(raw: str | list[str] | None) -> list[str]:
    """Accept either a comma-separated string or a list, and return clean tags.

    Deduped case-insensitively but stored with the casing you typed, so
    "CPP00" stays shouty and "exam" stays quiet even if you later type
    "cpp00" somewhere else.
    """
    if raw is None:
        return []
    parts = raw.split(",") if isinstance(raw, str) else list(raw)
    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = _TAG_STRIP.sub("", str(part)).strip()[:TAG_MAX_CHARS].strip()
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)
    return tags


def clean_goal(raw: str | None) -> str:
    """One line, trimmed. Newlines are collapsed rather than rejected — a goal
    pasted from somewhere else shouldn't fail, it should just get tidied."""
    if raw is None:
        return ""
    return " ".join(str(raw).split())[:GOAL_MAX_CHARS].strip()


@dataclass
class Block:
    id: str
    start: datetime
    end: datetime
    goal: str = ""
    tags: list[str] = field(default_factory=list)
    done_at: str | None = None
    pushed_at: str | None = None

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def done(self) -> bool:
        return self.done_at is not None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "goal": self.goal,
            "tags": list(self.tags),
            "done": self.done,
            "doneAt": self.done_at,
            "hours": round(self.hours, 2),
            "pushed": self.pushed_at is not None,
        }


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row  # rows behave like dicts, so row["start_at"] works
    return connection


def init() -> None:
    with _connect() as connection:
        connection.executescript(SCHEMA)
        existing = {
            row["name"] for row in connection.execute("PRAGMA table_info(blocks)")
        }
        for column, statement in ADDED_COLUMNS.items():
            if column not in existing:
                connection.execute(statement)
        # `note` was the pre-goal free-text field. It was never editable from
        # the UI, so in practice it holds either nothing or the legacy "auto"
        # marker — but if anything real is in there it becomes the goal rather
        # than being stranded in a column nothing reads any more.
        if "note" in existing and "goal" not in existing:
            connection.execute(
                "UPDATE blocks SET goal = note "
                "WHERE goal = '' AND note NOT IN ('', 'auto')"
            )


def _row_to_block(row: sqlite3.Row) -> Block:
    return Block(
        id=row["id"],
        start=datetime.fromisoformat(row["start_at"]).astimezone(settings.tz),
        end=datetime.fromisoformat(row["end_at"]).astimezone(settings.tz),
        goal=row["goal"],
        tags=normalise_tags(row["tags"]),
        done_at=row["done_at"],
        pushed_at=row["pushed_at"],
    )


def list_between(start: datetime, end: datetime) -> list[Block]:
    # Standard interval-overlap query: any block that touches [start, end),
    # not just ones fully contained in it — a block straddling the boundary
    # still needs to show up.
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM blocks WHERE start_at < ? AND end_at > ? ORDER BY start_at",
            (end.isoformat(), start.isoformat()),
        ).fetchall()
    return [_row_to_block(row) for row in rows]


def list_all() -> list[Block]:
    """Every block ever planned, oldest first — what the goals page reads when
    you ask it for everything rather than one month."""
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM blocks ORDER BY start_at").fetchall()
    return [_row_to_block(row) for row in rows]


def get(block_id: str) -> Block | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
    return _row_to_block(row) if row else None


def create(
    start: datetime,
    end: datetime,
    goal: str = "",
    tags: str | list[str] | None = None,
) -> Block:
    block = Block(
        id=uuid.uuid4().hex,
        start=start,
        end=end,
        goal=clean_goal(goal),
        tags=normalise_tags(tags),
    )
    with _connect() as connection:
        connection.execute(
            "INSERT INTO blocks (id, start_at, end_at, goal, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (block.id, start.isoformat(), end.isoformat(), block.goal,
             ",".join(block.tags)),
        )
    return block


def update(
    block_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    goal: str | None = None,
    tags: str | list[str] | None = None,
) -> Block | None:
    """Change any subset of a block. Anything left as None is left alone.

    pushed_at is always cleared here: any edit — times *or* goal, since the
    goal is part of the event title — means the iCloud copy (if there is one)
    is now stale until the next push.
    """
    fields: list[str] = []
    values: list = []
    if start is not None:
        fields.append("start_at=?")
        values.append(start.isoformat())
    if end is not None:
        fields.append("end_at=?")
        values.append(end.isoformat())
    if goal is not None:
        fields.append("goal=?")
        values.append(clean_goal(goal))
    if tags is not None:
        fields.append("tags=?")
        values.append(",".join(normalise_tags(tags)))
    if not fields:
        return get(block_id)  # nothing asked for, nothing to invalidate

    with _connect() as connection:
        connection.execute(
            f"UPDATE blocks SET {', '.join(fields)}, pushed_at=NULL WHERE id=?",
            (*values, block_id),
        )
    return get(block_id)


def set_done(block_id: str, done: bool) -> Block | None:
    """Tick or untick a goal.

    Deliberately does not clear pushed_at: whether you achieved the goal isn't
    part of the calendar event, so the iCloud copy is still accurate.
    """
    with _connect() as connection:
        connection.execute(
            "UPDATE blocks SET done_at = ? WHERE id = ?",
            (datetime.now(settings.tz).isoformat() if done else None, block_id),
        )
    return get(block_id)


def delete(block_id: str) -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM blocks WHERE id = ?", (block_id,))


def known_tags() -> list[str]:
    """Every tag in use, most-used first — the suggestion list behind the tag
    field, so a project gets typed out in full exactly once."""
    counts: dict[str, int] = {}
    casing: dict[str, str] = {}
    with _connect() as connection:
        rows = connection.execute("SELECT tags FROM blocks WHERE tags != ''")
        for row in rows:
            for tag in normalise_tags(row["tags"]):
                key = tag.lower()
                counts[key] = counts.get(key, 0) + 1
                casing.setdefault(key, tag)
    return [
        casing[key]
        for key, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def mark_pushed(block_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE blocks SET pushed_at = ? WHERE id = ?",
            (datetime.now(settings.tz).isoformat(), block_id),
        )
