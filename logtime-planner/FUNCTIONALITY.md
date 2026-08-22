# Functionality and structure

Reference for what the app currently does and where each piece lives. For setup
and day-to-day use, see `README.md`.

---

## What it does

Three sources of truth, kept deliberately separate:

| Source | Owns | Direction |
| --- | --- | --- |
| 42 intra | hours you have **actually** clocked | read only |
| Google + iCloud calendars | when you are **busy** | read only |
| Local SQLite | hours you **plan** to clock | read/write |

The app never invents an actual hour and never lets you type one in. Anything
labelled "clocked" came from intra; anything labelled "planned" came from you.
That separation is what makes the deficit figure trustworthy.

### The core calculation

```
deficit = weekly_target − clocked − planned_future
```

`planned_future` counts only blocks that haven't happened yet. A block whose
time has passed stops counting as a plan — whatever you actually did in that
slot is already in `clocked`, so counting both would inflate the total. This is
what makes a missed Friday reopen the gap instead of silently absorbing it.

### How hours are counted

The current week's hours come from `/v2/users/<login>/locations` — the raw
session list — not the `locations_stats` daily rollup. Each session has a
`begin_at` and an `end_at`; a `null` end means the session is still running, so
*now* is substituted.

`planner.hours_by_day()` then splits every session at local midnight. A session
from 21:00 Saturday to 03:00 Sunday is three Saturday hours and three Sunday
hours, which a per-day rollup cannot express.

The month view uses `locations_stats` for older days, since precision matters
less there and it's one cheap call, but session data overrides it for the days
it covers — so the week and month can never disagree about the current week.

### Conflict detection

A planned block is flagged when it overlaps a calendar event, or when it starts
within `TRAVEL_BUFFER_MINUTES` of one ending (or ends that close to one
starting). Flagging is advisory — you can leave a conflicted block in place.

### Auto-fill

**Fill the gap** places blocks in the earliest free time until the deficit
closes. Earliest-first rather than largest-window-first, because hours banked
early can't be lost to a cancelled Sunday. It respects `MAX_HOURS_PER_DAY`
(counting hours already clocked that day), won't place a block shorter than
`MIN_BLOCK_MINUTES` unless doing so finishes the job, rounds down to the
nearest 15 minutes, and never overshoots the deficit.

It targets the **weekly** figure only. The monthly target is a readout.

### Calendar sync

Both directions are manual, and asymmetric:

- **Push** rewrites the plan calendar for the week from the database. Event
  UIDs derive from block ids, so re-pushing updates in place rather than
  duplicating. Titles are the block's note, or `42 planned hours`.
- **Pull** deletes database blocks whose calendar events are gone. It only ever
  touches blocks with `pushed_at` set — a block you just created and haven't
  synced yet is absent from iCloud for an innocent reason.

Time edits made in Calendar.app are overwritten on the next push. The plan
calendar is a rendering of the database, not an input.

---

## Data flow

```
  intra /locations ──┐
                     ├──► FastAPI ──► /api/week ──► browser (FullCalendar)
  Google .ics feeds ─┤       │                            │
  iCloud CalDAV ─────┘       │                            │ drag / resize
                             │                            ▼
                          SQLite ◄──── POST/PATCH/DELETE /api/blocks
                             │
                             └──► push_week / pull_week ──► iCloud "42 Plan"
```

Every network result is cached for 120 seconds per week, keyed separately for
sessions, the logtime rollup, and busy events. Dragging blocks never re-hits the
network; only **Refresh from intra** (`?refresh=true`) bypasses the cache.

---

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | The single-page UI. Sends `Cache-Control: no-store`. |
| `GET` | `/api/week?date_=&refresh=` | Everything the UI needs for one week |
| `POST` | `/api/blocks` | Create a block `{start, end, note}` |
| `PATCH` | `/api/blocks/{id}` | Move or resize `{start, end, note?}` |
| `DELETE` | `/api/blocks/{id}` | Delete a block |
| `POST` | `/api/rebalance?date_=` | Auto-fill the deficit |
| `POST` | `/api/push?date_=` | Write the week to iCloud |
| `POST` | `/api/pull?date_=` | Reconcile deletions from iCloud |
| `GET` | `/api/health` | Config status and secret expiry |

`date_` is any date inside the week you want; it's resolved to week bounds
server-side. Omit it for the current week.

### `/api/week` response

```jsonc
{
  "weekStart": "2026-08-17T00:00:00+08:00",
  "weekEnd":   "2026-08-24T00:00:00+08:00",
  "now":       "2026-08-23T00:55:18+08:00",
  "timezone":  "Asia/Singapore",
  "summary":   { "target": 20, "clocked": 11.3, "plannedFuture": 0,
                 "plannedPast": 0, "deficit": 8.7, "covered": 11.3 },
  "month":     { "target": 90, "clocked": 70.7, "planned": 0,
                 "remaining": 19.3, "daysLeft": 9, "label": "August" },
  "clockedByDay": { "2026-08-18": 5.1, "2026-08-19": 0.3 },
  "blocks":    [ { "id": "...", "start": "...", "end": "...",
                   "note": "", "hours": 4.0, "pushed": false } ],
  "busy":      [ { "start": "...", "end": "...", "title": "...",
                   "source": "Work" } ],
  "conflicts": [ { "blockId": "...", "reason": "overlaps",
                   "against": "Class (Work)" } ],
  "dayWindow": { "start": "08:00", "end": "23:00" },
  "openSince": null,
  "liveHours": 0.0,
  "intraError": null,
  "icloudEnabled": true
}
```

`liveHours` and `openSince` are informational only — they are not added to any
total, since session data already includes running sessions.

### `/api/health` response

```jsonc
{
  "intra": true, "icloud": true, "googleFeeds": 3,
  "target": 20.0, "timezone": "Asia/Singapore",
  "secret": { "daysLeft": 28, "expiresOn": "2026-09-20",
              "warn": false, "error": null }
}
```

---

## Repository structure

```
logtime-planner/
├── README.md              setup, usage, troubleshooting
├── FUNCTIONALITY.md       this file
├── requirements.txt
├── run.sh                 Linux launcher
├── run_mac.sh             macOS launcher
├── .env                   your secrets — gitignored, chmod 600
├── .env.example           committed template, blank values
├── .gitignore             excludes .env, *.db, .venv, __pycache__
├── logtime.db             SQLite, created on first run, gitignored
│
├── app/
│   ├── __init__.py
│   ├── config.py          every knob, read from .env at import time
│   ├── ft_api.py          intra client
│   ├── calendars.py       calendar readers
│   ├── store.py           SQLite persistence
│   ├── planner.py         scheduling logic — pure, no I/O
│   ├── caldav_sync.py     iCloud push and pull
│   └── main.py            FastAPI routes, caching, week assembly
│
├── static/
│   └── index.html         entire UI: markup, CSS, JS in one file
│
└── tests/
    └── test_planner.py    12 tests against planner.py
```

### Module responsibilities

**`config.py`** — a frozen dataclass read once at import. Because of that,
changing `.env` requires a server restart; `--reload` won't catch it.

**`ft_api.py`** — OAuth client-credentials token with caching (refreshed 120s
before expiry), `sessions_between()` for raw sessions, `all_logtime()` for the
daily rollup, and secret-expiry tracking that warns under seven days.

**`calendars.py`** — Google `.ics` over HTTP with `recurring_ical_events` to
expand RRULEs client-side; iCloud over CalDAV with `expand=True` so the server
expands them. Both normalise to `BusyEvent`. A dead feed yields an error entry
rather than killing the week. Also owns `week_bounds()`.

**`store.py`** — one `blocks` table: `id`, `start_at`, `end_at`, `note`,
`pushed_at`. Any edit clears `pushed_at`, marking it out of sync with iCloud.

**`planner.py`** — the only module with no I/O, which is why it's the only one
with tests. Interval maths (`merge`, `subtract`, `free_windows`), session
splitting (`hours_by_day`), `find_conflicts`, `summarise`, `autofill`.

**`caldav_sync.py`** — `push_week` and `pull_week`, both scoped to a single
week. UID scheme: `{block_id}@logtime-planner.local`.

**`main.py`** — routes, the 120-second cache, and `_load_week()` which
assembles the payload from all sources.

**`static/index.html`** — one file by design; no build step. FullCalendar from
CDN. The calendar owns navigation via `prev()`/`next()`, and a `datesSet`
handler fetches whatever range it lands on — the app never calls `gotoDate`,
because two systems both trying to own the current date is what made the arrows
unresponsive in an earlier version.

---

## Configuration reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `FT_UID` | — | Intra app UID (public) |
| `FT_SECRET` | — | Intra app secret (expires ~monthly) |
| `FT_LOGIN` | — | Your intra login |
| `TIMEZONE` | `Asia/Singapore` | All times are local to this |
| `WEEKLY_TARGET_HOURS` | `20` | Drives the rail and auto-fill |
| `MONTHLY_TARGET_HOURS` | `90` | Drives the month strip only |
| `WEEK_START_DAY` | `0` | 0=Mon … 6=Sun |
| `DAY_WINDOW_START` | `08:00` | Earliest auto-fill will place a block |
| `DAY_WINDOW_END` | `23:00` | Latest |
| `MIN_BLOCK_MINUTES` | `60` | Shortest auto-placed block |
| `MAX_HOURS_PER_DAY` | `10` | Ceiling per day, counting clocked hours |
| `TRAVEL_BUFFER_MINUTES` | `30` | Gap below which a block is flagged |
| `GOOGLE_ICS_URLS` | — | Comma-separated secret iCal URLs |
| `ICLOUD_USERNAME` | — | Apple ID |
| `ICLOUD_APP_PASSWORD` | — | App-specific password |
| `ICLOUD_PLAN_CALENDAR` | `42 Plan` | The only calendar written to |
| `ICLOUD_BUSY_CALENDARS` | all | Optional allow-list of names |
| `DB_PATH` | `logtime.db` | SQLite location |

---

## Known limits

- **Weekly and monthly targets aren't reconciled.** 4 × 20 = 80, so a 90h month
  needs ~22.5h in some weeks. Auto-fill only targets the weekly figure.
- **No authentication.** Safe on `127.0.0.1`; never bind `0.0.0.0` on a shared
  network.
- **Calendar sync is manual and one-way for edits.** Push overwrites; pull only
  detects deletions, not moves.
- **Week definition is a plain calendar-week sum.** If your campus uses a
  rolling window, `summarise()` is the only function to change.
- **Google `.ics` feeds are cached by Google** and can lag by hours. Swapping
  `_google_busy()` for the Calendar API would fix that at the cost of OAuth
  setup.
- **Single user.** Login and targets are global config, not per-user.
