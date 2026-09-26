"""Goals, tags and the iCloud event title they end up in.

Run with:  python -m pytest tests/  (or just: python tests/test_goals.py)

Unlike test_planner.py these touch the database, so each test gets its own
throwaway file — settings is frozen, hence the object.__setattr__.
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import caldav_sync, store  # noqa: E402
from app.config import settings  # noqa: E402

TZ = ZoneInfo(settings.timezone)


def fresh_db() -> None:
    """Point the store at an empty database for the test about to run."""
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    Path(handle.name).unlink()  # let sqlite create it, so init() runs on a blank file
    object.__setattr__(settings, "db_path", handle.name)
    store.init()


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=TZ)


def test_tags_are_deduped_and_stripped():
    assert store.normalise_tags("CPP00, exam ,#exam, , cpp00") == ["CPP00", "exam"]
    assert store.normalise_tags(["  #Rush  "]) == ["Rush"]
    assert store.normalise_tags(None) == []


def test_goal_is_one_trimmed_line():
    assert store.clean_goal("  finish\n  ex02  ") == "finish ex02"
    assert len(store.clean_goal("x" * 500)) == store.GOAL_MAX_CHARS


def test_block_round_trips_goal_and_tags():
    fresh_db()
    block = store.create(at(24, 9), at(24, 11), "Finish CPP00 ex02", "CPP00, exam")
    again = store.get(block.id)
    assert again.goal == "Finish CPP00 ex02"
    assert again.tags == ["CPP00", "exam"]
    assert again.done is False


def test_update_leaves_untouched_fields_alone():
    fresh_db()
    block = store.create(at(24, 9), at(24, 11), "Old goal", "CPP00")
    # A drag sends times only — the goal must survive it.
    moved = store.update(block.id, at(24, 14), at(24, 16))
    assert moved.goal == "Old goal" and moved.tags == ["CPP00"]
    # ...and editing the goal must not move the block.
    renamed = store.update(block.id, goal="New goal")
    assert (renamed.start, renamed.end) == (at(24, 14), at(24, 16))
    assert renamed.goal == "New goal" and renamed.tags == ["CPP00"]


def test_ticking_done_does_not_invalidate_the_icloud_copy():
    fresh_db()
    block = store.create(at(24, 9), at(24, 11), "Finish CPP00 ex02")
    store.mark_pushed(block.id)
    assert store.set_done(block.id, True).pushed_at is not None
    # Editing the goal *does* invalidate it: the goal is in the event title.
    assert store.update(block.id, goal="Something else").pushed_at is None
    assert store.set_done(block.id, False).done is False


def test_known_tags_are_ordered_by_use():
    fresh_db()
    store.create(at(24, 9), at(24, 11), "a", "CPP00, exam")
    store.create(at(25, 9), at(25, 11), "b", "cpp00")
    assert store.known_tags() == ["CPP00", "exam"]  # CPP00 twice, exam once


def test_legacy_note_column_becomes_a_goal():
    """A database from before goals existed keeps whatever was in `note`."""
    import sqlite3
    import uuid

    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    connection = sqlite3.connect(handle.name)
    connection.executescript(
        "CREATE TABLE blocks (id TEXT PRIMARY KEY, start_at TEXT NOT NULL, "
        "end_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', pushed_at TEXT);"
    )
    for note in ("read the subject", "auto"):
        connection.execute(
            "INSERT INTO blocks VALUES (?,?,?,?,NULL)",
            (uuid.uuid4().hex, at(24, 9).isoformat(), at(24, 11).isoformat(), note),
        )
    connection.commit()
    connection.close()

    object.__setattr__(settings, "db_path", handle.name)
    store.init()
    goals = sorted(block.goal for block in store.list_all())
    assert goals == ["", "read the subject"]  # "auto" was never a real note


def test_ics_title_keeps_the_hours_and_adds_the_goal():
    block = store.Block("id1", at(24, 9), at(24, 10), goal="", tags=[])
    assert "SUMMARY:42 planned hours · 1.0h\r\n" in caldav_sync._ics(block)

    titled = store.Block(
        "id2", at(24, 9), at(24, 11), goal="Finish CPP00 ex02", tags=["CPP00", "exam"]
    )
    ics = caldav_sync._ics(titled)
    assert "SUMMARY:42 planned hours · 2.0h · Finish CPP00 ex02" in ics
    assert "CATEGORIES:42,CPP00,exam" in ics


def test_ics_escapes_punctuation_in_a_goal():
    block = store.Block("id3", at(24, 9), at(24, 11), goal="ex02, then ex03; push")
    line = next(
        row for row in caldav_sync._ics(block).split("\r\n") if row.startswith("SUMMARY")
    )
    assert line.endswith("ex02\\, then ex03\; push")


if __name__ == "__main__":
    for name, test in sorted(dict(globals()).items()):
        if name.startswith("test_") and callable(test):
            test()
            print("ok", name)
