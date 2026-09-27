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
early can't be lost to a cancelled Sunday. It respects `PLAN_MAX_HOURS_PER_DAY`
(counting hours already clocked that day), won't place a block shorter than
`MIN_BLOCK_MINUTES` unless doing so finishes the job, and snaps to 15 minutes.
Every calendar event it treats as blocked is padded by `TRAVEL_BUFFER_MINUTES`
on both sides, so it never lands a suggestion flush against a meeting.

Snapping rounds *down*, with one exception: the block that closes the gap
rounds up, bounded by what the window and the day can hold. Rounding down
everywhere would strand a sub-quarter sliver of deficit that no later window
could place either — 0.13h floors to zero in every window it is offered.

`PLAN_MAX_HOURS_PER_DAY` is what auto-fill is willing to *suggest*;
`MANUAL_MAX_HOURS_PER_DAY` is what you're allowed to commit to by hand. They
are deliberately different numbers, and only the second one is a validator.

Planned blocks may never overlap each other — enforced both in the grid
(`eventOverlap`/`selectOverlap`, scoped to plan-blocks only so busy/actual
events underneath are unaffected) and server-side on every create/edit.

### Week fills and month fills

`/api/rebalance?scope=week` closes the weekly deficit. `scope=month` plans the
rest of the month up to `MONTHLY_TARGET_HOURS` and deliberately ignores the
weekly target: the weekly figure is a pacing device, and treating it as a
ceiling can make the monthly target unreachable outright (four 20h weeks is
80h, which never reaches 90).

A month fill goes round twice. Chronological greed is right for a week — the
deficit is small — but over a month it would empty the whole target into the
next day or two: inside `PLAN_MAX_HOURS_PER_DAY`, yet not a plan anyone can
keep. So the first pass caps each day at the pace the month actually needs
(`remaining ÷ days_left`, to the quarter hour), and only if that leaves a
shortfall does a second pass go up to the real daily ceiling.

A month that is already over returns "those hours can't be planned now" rather
than placing nothing and blaming your day window for it.

### Session goals

Optional, and orthogonal to the hours maths — no total changes because a block
has a goal or doesn't. A block may carry one short line of what that session is
for (120 characters, collapsed to a single line) plus any number of tags,
deduped case-insensitively but stored with the casing you typed, so `CPP00`
stays shouty.

`GET /api/goals` groups them by the day the block starts on, over a week, a
month, or everything, optionally filtered to one tag. Blocks *without* a goal
are returned rather than filtered out server-side: they are exactly the ones
worth prompting for, and the page hides them behind a toggle.

Two counts that sound alike and aren't:

| | Asks | Comes from |
| --- | --- | --- |
| `unlogged` | were you actually there? | intra sessions vs. the block |
| `missed` | did you do the thing? | the goal's own checkbox, unticked, session past |

Ticking a goal done is the one edit that does **not** clear `pushed_at` — it
changes nothing iCloud holds, so it can't make the calendar copy stale.

### Calendar sync

Both directions are manual, and asymmetric:

- **Push** rewrites the plan calendar for the week from the database. Event
  UIDs derive from block ids, so re-pushing updates in place rather than
  duplicating. Titles are `42 planned hours · <hours>h`, with the block's
  goal appended when it has one; its tags go into `CATEGORIES` beside `42`.
  Goal text is escaped per RFC 5545, so a comma or semicolon in it can't
  split the property.
- **Pull** reconciles the other way: for each previously-pushed block, if its
  iCloud event is gone the database block is deleted; if the event's time has
  changed, the database block adopts the new time — unless that would overlap
  another block, in which case it's left alone and reported as skipped. It
  only ever touches blocks with `pushed_at` set — a block you just created and
  haven't synced yet is absent from iCloud for an innocent reason, not a
  deletion.

Time edits made in Calendar.app are picked up by the *next pull*, not
automatically — until then, or if you push first, they're overwritten. The
plan calendar is a rendering of the database that pull can also read back
from, not a fully independent input.

### Past-vs-planned reconciliation

Two more checks run once intra is configured:

- A block straddling `now` only counts its remaining half toward the weekly
  and monthly "planned" totals — the elapsed half is already inside `clocked`,
  so counting both would double it (`planner.split_future_past`).
- A block that has started is checked against actual clocked sessions up to
  `now`; any elapsed part left uncovered (beyond a 5-minute tolerance) is
  flagged "unlogged" — that stretch is hatched on the grid and rolled into the
  *not logged* figure (`planner.flag_unlogged`). Skipped while intra is
  returning an error, since no sessions is not the same as none happening.

---

## Data flow

```
  intra /locations ──┐                  ┌─► /api/week   ─┐
  Google .ics feeds ─┼──► FastAPI ──────┼─► /api/month  ─┼─► browser (week grid,
  iCloud CalDAV ─────┘       │          └─► /api/goals  ─┘           month grid,
                             │                                 │     goals list)
                             ▼            drag / resize / tick │
                           SQLite ◄──── POST / PATCH / DELETE ─┘
                             │          /api/blocks
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
| `GET` | `/api/month?date_=&refresh=` | The same for one calendar month |
| `GET` | `/api/summary?refresh=` | Clocked hours per month and per week since `SUMMARY_SINCE` |
| `GET` | `/api/goals?date_=&scope=&tag=` | Planned blocks grouped by day, for the goals page |
| `POST` | `/api/blocks` | Create a block `{start, end, goal?, tags?}` |
| `PATCH` | `/api/blocks/{id}` | Change any subset of `{start, end, goal, tags}` |
| `POST` | `/api/blocks/{id}/done` | Tick or untick a goal `{done}` |
| `DELETE` | `/api/blocks/{id}` | Delete a block |
| `POST` | `/api/rebalance?date_=&scope=` | Auto-fill the deficit — `scope` is `week` or `month` |
| `POST` | `/api/push?date_=` | Write the week to iCloud |
| `POST` | `/api/pull?date_=` | Reconcile moves and deletions from iCloud |
| `GET` | `/api/health` | Config status and secret expiry |
| `POST` | `/api/secret-reminder` | All-day iCloud reminder on the secret's expiry date |

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
                 "plannedPast": 0, "plannedPastUnlogged": 0,
                 "deficit": 8.7, "covered": 11.3 },
  "month":     { "target": 90, "clocked": 70.7, "plannedFuture": 0,
                 "plannedPast": 0, "unlogged": 0, "projected": 70.7,
                 "toLog": 19.3, "remaining": 19.3, "perDay": 2.14,
                 "onTrack": false, "daysLeft": 9, "start": "2026-08-01",
                 "label": "August", "longLabel": "August 2026" },
  "months":    [ /* one `month` object per month the week touches, oldest first */ ],
  "clockedByDay": { "2026-08-18": 5.1, "2026-08-19": 0.3 },
  "blocks":    [ { "id": "...", "start": "...", "end": "...",
                   "goal": "Finish CPP00 ex02", "tags": ["CPP00"],
                   "done": false, "doneAt": null,
                   "hours": 4.0, "pushed": false } ],
  "busy":      [ { "start": "...", "end": "...", "title": "...",
                   "source": "Work" } ],
  "conflicts": [ { "blockId": "...", "reason": "overlaps",
                   "against": "Class (Work)" } ],
  "unlogged":  [ { "blockId": "...", "hours": 1.5,
                   "gaps": [ ["2026-08-18T06:00:00+08:00", "2026-08-18T07:30:00+08:00"] ] } ],
  "dayWindow": { "start": "08:00", "end": "23:00" },
  "gridWindow": { "start": "00:00", "end": "24:00" },
  "openSince": null,
  "liveHours": 0.0,
  "tags":      [ "CPP00", "exam" ],
  "intraError": null,
  "icloudEnabled": true
}
```

`liveHours` and `openSince` are informational only — they are not added to any
total, since session data already includes running sessions.

`/api/month` returns the same `summary` object as the `month` key above,
plus `clockedByDay`, the month's `blocks`, and its `unlogged` list — the fuller
payload the Month tab reads. `toLog` (hours that still have to appear on
intra) and `remaining` (hours not even on the grid yet) are different numbers,
and late in the month they are the two that matter.

### `/api/goals` response

`scope` is `month` (default), `week` or `all`; `tag` filters to one tag.

```jsonc
{
  "scope": "month", "label": "September 2026",
  "from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00",
  "now": "2026-09-21T18:09:33+08:00",
  "tag": null,
  "tags": [ "CPP00", "exam" ],          // every tag in use, most-used first
  "days": [ { "date": "2026-09-23",
              "items": [ { "id": "...", "goal": "Finish CPP00 ex02",
                           "tags": ["CPP00"], "done": true,
                           "past": false, "hours": 2.0, "...": "..." } ] } ],
  "summary": { "blocks": 3, "goals": 2, "done": 1, "missed": 0,
               "untitled": 1, "hours": 7.0, "doneHours": 2.0 }
}
```

Blocks without a goal are returned rather than filtered out — they are exactly
the ones worth prompting for, and the page hides them behind a toggle.
`missed` counts goals whose session has passed and that are still unticked,
which is a different question from `unlogged` (that one is intra's verdict on
whether you were there; this one is yours on whether you did the thing).

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
├── run.sh                 launcher — detects Linux vs macOS itself
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
│                          (week grid, month grid, goals list)
│
└── tests/
    ├── test_planner.py    31 tests against planner.py — pure, no database
    └── test_goals.py      9 tests: goals, tags, the migration, ICS titles
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
rather than killing the week. Also owns `week_bounds()`, `month_bounds()`, and
`month_of_week()` — the rule for which month a week straddling two belongs to.

**`store.py`** — one `blocks` table: `id`, `start_at`, `end_at`, `goal`,
`tags` (comma-joined), `done_at`, `pushed_at`. Any edit clears `pushed_at`,
marking it out of sync with iCloud; ticking `done_at` deliberately doesn't,
since it changes nothing iCloud holds. `init()` migrates a pre-goals
database by adding the missing columns and carrying any real `note` over
into `goal`.

**`planner.py`** — the only module with no I/O, which is what makes it
directly testable: no database, no network, and `now` always passed in rather
than read. Interval maths (`merge`, `subtract`, `free_windows`), session
splitting (`hours_by_day`), `find_conflicts`, `autofill`, `split_future_past`
(the in-progress block), `clip` (the month-straddling block), `flag_unlogged`,
and both `WeekSummary`/`summarise` and `MonthSummary`/`summarise_month`.

**`caldav_sync.py`** — `push_week` and `pull_week`, both scoped to a single
week, plus `push_secret_reminder` for the expiry date. UID scheme:
`{block_id}@logtime-planner.local`. Event text is escaped per RFC 5545, since
a goal you typed may contain a comma or semicolon.

**`main.py`** — routes, the 120-second cache, and `_load_week()`/`_load_month()`
which assemble each payload from all sources. Also where rules spanning
several pieces live: no two planned blocks may overlap, a day has a manual
hours ceiling, auto-fill keeps a travel buffer around calendar events, and a
month fill paces itself before falling back to the daily ceiling.

**`static/index.html`** — one file by design; no build step. FullCalendar from
CDN for the week; a hand-rolled CSS grid for the month (that view needs one
number per day, not event boxes) and a plain list for the goals. The calendar
owns navigation via `prev()`/`next()`, and a `datesSet` handler fetches
whatever range it lands on — the app never calls `gotoDate`, because two
systems both trying to own the current date is what made the arrows
unresponsive in an earlier version. The week view has its own anchor; the
month and goals views share one, which is why stepping a month in either moves
both. The browser never computes an hours total itself — every figure arrives
pre-computed, which keeps the maths in one place.

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
| `SUMMARY_SINCE` | `2025-05-01` | First day the Summary tab covers (weeks start at the first full week after it) |
| `DAY_WINDOW_START` | `08:00` | Earliest auto-fill will place a block |
| `DAY_WINDOW_END` | `23:00` | Latest |
| `MIN_BLOCK_MINUTES` | `60` | Shortest auto-placed block |
| `PLAN_MAX_HOURS_PER_DAY` | `14` | Most auto-fill will suggest for one day, counting clocked hours |
| `MANUAL_MAX_HOURS_PER_DAY` | `24` | Most a block you place yourself may put on one day |
| `TRAVEL_BUFFER_MINUTES` | `30` | Gap below which a block is flagged |
| `GOOGLE_ICS_URLS` | — | Comma-separated secret iCal URLs |
| `ICLOUD_USERNAME` | — | Apple ID |
| `ICLOUD_APP_PASSWORD` | — | App-specific password |
| `ICLOUD_PLAN_CALENDAR` | `42 Plan` | The only calendar written to |
| `ICLOUD_BUSY_CALENDARS` | all | Optional allow-list of names |
| `DB_PATH` | `logtime.db` | SQLite location |

---

## Known limits

- **Weekly and monthly targets aren't reconciled.** 4 × 20 = 80, so a 90h
  month needs ~22.5h in some weeks. A week fill and a month fill each target
  their own figure; neither reasons about the other.
- **No authentication.** Safe on `127.0.0.1`; never bind `0.0.0.0` on a shared
  network.
- **Calendar sync is manual, and push is destructive.** Push rewrites the week
  from the database, so an edit made in Calendar.app is lost unless you pull
  first. Pull adopts moves and deletions, but skips a move that would land on
  another block.
- **Goals are one line, and never reach intra.** They live only in the local
  database and the iCloud event title; nothing validates them against what you
  actually did, which is what the checkbox is for.
- **Week definition is a plain calendar-week sum.** If your campus uses a
  rolling window, `summarise()` is the only function to change.
- **Google `.ics` feeds are cached by Google** and can lag by hours. Swapping
  `_google_busy()` for the Calendar API would fix that at the cost of OAuth
  setup.
- **Single user.** Login and targets are global config, not per-user.
