"""Planned blocks live here. Actual hours never do — those always come from
intra, so there is only ever one source of truth for what you really clocked."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    id         TEXT PRIMARY KEY,
    start_at   TEXT NOT NULL,
    end_at     TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    pushed_at  TEXT
);
CREATE INDEX IF NOT EXISTS blocks_start ON blocks (start_at);
"""


@dataclass
class Block:
    id: str
    start: datetime
    end: datetime
    note: str = ""
    pushed_at: str | None = None

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "note": self.note,
            "hours": round(self.hours, 2),
            "pushed": self.pushed_at is not None,
        }


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init() -> None:
    with _connect() as connection:
        connection.executescript(SCHEMA)


def _row_to_block(row: sqlite3.Row) -> Block:
    return Block(
        id=row["id"],
        start=datetime.fromisoformat(row["start_at"]).astimezone(settings.tz),
        end=datetime.fromisoformat(row["end_at"]).astimezone(settings.tz),
        note=row["note"],
        pushed_at=row["pushed_at"],
    )


def list_between(start: datetime, end: datetime) -> list[Block]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM blocks WHERE start_at < ? AND end_at > ? ORDER BY start_at",
            (end.isoformat(), start.isoformat()),
        ).fetchall()
    return [_row_to_block(row) for row in rows]


def get(block_id: str) -> Block | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
    return _row_to_block(row) if row else None


def create(start: datetime, end: datetime, note: str = "") -> Block:
    block = Block(id=uuid.uuid4().hex, start=start, end=end, note=note)
    with _connect() as connection:
        connection.execute(
            "INSERT INTO blocks (id, start_at, end_at, note) VALUES (?, ?, ?, ?)",
            (block.id, start.isoformat(), end.isoformat(), note),
        )
    return block


def update(block_id: str, start: datetime, end: datetime, note: str | None = None):
    with _connect() as connection:
        if note is None:
            connection.execute(
                "UPDATE blocks SET start_at=?, end_at=?, pushed_at=NULL WHERE id=?",
                (start.isoformat(), end.isoformat(), block_id),
            )
        else:
            connection.execute(
                "UPDATE blocks SET start_at=?, end_at=?, note=?, pushed_at=NULL "
                "WHERE id=?",
                (start.isoformat(), end.isoformat(), note, block_id),
            )
    return get(block_id)


def delete(block_id: str) -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM blocks WHERE id = ?", (block_id,))


def mark_pushed(block_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE blocks SET pushed_at = ? WHERE id = ?",
            (datetime.now(settings.tz).isoformat(), block_id),
        )
