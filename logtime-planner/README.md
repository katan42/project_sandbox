# 42 logtime planner

Plan your 20 hours a week — and your 90 hours a month — against the calendars
you actually live by, and re-plan them when the week doesn't go as intended.

- **Actual hours** come from the 42 intra API. They are never typed in by hand.
- **Busy time** comes from your Google calendars (secret `.ics` feeds) and your
  iCloud calendars (CalDAV).
- **Planned blocks** live in a local SQLite file, are dragged around in the
  browser, and sync to one dedicated iCloud calendar so they show up in
  Calendar.app on every device.

---

## Running it

```bash
./run.sh
```

One script, both platforms. It frees port 8042 if a previous run left it held,
installs dependencies, starts uvicorn with `--reload`, and opens your browser at
`http://127.0.0.1:8042`. Ctrl+C stops the server cleanly — the `trap` makes sure
uvicorn dies with the script instead of being orphaned on the port.

It detects the OS itself rather than making you pick a script: `open` vs
`xdg-open` for the browser, and `--link-mode=copy` for `uv sync` on Linux, which
matters on `/sgoinfre` where hardlinks may not work. If `uv` is installed it
uses that; otherwise it falls back to a `.venv` and `pip install .`.

If you'd rather run it by hand:

```bash
PYTHONPATH=. python3 -m uvicorn app.main:app --reload --port 8042
```

The `PYTHONPATH=.` matters — uvicorn's reloader spawns a subprocess that
doesn't reliably inherit the working directory on its import path, and without
it you get `ModuleNotFoundError: No module named 'app'`. And `python3 -m
uvicorn` rather than bare `uvicorn`, which only works if `~/.local/bin` is on
your PATH.

---

## First-time setup

```bash
cp .env.example .env && chmod 600 .env
./run.sh
```

Dependencies are declared in `pyproject.toml` and installed by `run.sh` — there
is nothing to install by hand. To do it yourself anyway:

```bash
uv sync                                      # or:
python3 -m venv .venv && .venv/bin/pip install .
```

### 1. Intra credentials

profile.intra.42.fr → *Settings* → *API* → *Register a new app*. No redirect URI
needed; the client-credentials flow doesn't use one. Copy UID → `FT_UID`,
secret → `FT_SECRET`, your login → `FT_LOGIN`.

**42 client secrets expire on roughly a 30-day cycle.** The token response
carries the deadline in `secret_valid_until`, so `/api/health` shows you how
many days are left and the server prints a warning below seven days. The UI
shows the same warning as a pill in the header, with a **+ remind** button that
drops an all-day reminder on the expiry date into your iCloud plan calendar.
When it expires, regenerate on the same page and update `.env` — nothing else
changes.

### 2. Google calendars

For each calendar: Settings → *Integrate calendar* → **Secret address in iCal
format**. Comma-separate them into `GOOGLE_ICS_URLS`. Those URLs are
credentials — anyone holding one can read that calendar without logging in.

### 3. iCloud

appleid.apple.com → *Sign-In and Security* → *App-Specific Passwords*. Apple ID
→ `ICLOUD_USERNAME`, generated password → `ICLOUD_APP_PASSWORD`.

**Create the plan calendar by hand** in Calendar.app, named `42 Plan` (or change
`ICLOUD_PLAN_CALENDAR`). The app refuses to create calendars, so it can never
write somewhere you didn't intend. Every *other* iCloud calendar is read as busy
time.

### 4. Check it

```bash
curl -s http://127.0.0.1:8042/api/health | python3 -m json.tool
```

Want `"intra": true`, `"icloud": true`, a non-zero `googleFeeds`, and a
`secret.expiresOn` roughly a month out.

---

## Using it

Three tabs: **Week**, **Month** and **Goals**.

### Week

| You do | It does |
| --- | --- |
| Drag on an empty column | Places a block — it can't land on top of another block |
| Drag a block | Moves it, recomputes the gap |
| Grab a block's edge | Resizes it |
| Click the **×** in a block's corner | Deletes it |
| Click the **✎** in a block's corner | Sets that session's goal and tags |
| Place or drag a block into the past | Asks you to confirm first — nothing forbids it, it's just a safety check |
| **Fill the gap** | Auto-places blocks in the earliest free time until the week is covered, keeping a `TRAVEL_BUFFER_MINUTES` gap around every calendar event |
| **Refresh from intra** | Pulls your real clocked hours |
| **Send to iCloud** | Makes the plan calendar match the grid |
| **Sync from iCloud** | Adopts blocks you moved in Calendar.app, removes ones you deleted there |

The rail across the top is the week: solid navy is hours intra says you've done,
hatched teal is planned-but-not-yet-done, and the gap to the finish line is
what's unaccounted for. A block you're currently in the middle of only counts
its *remaining* time toward that gap — the elapsed half is already inside
"done", so it isn't counted twice.

A fourth figure, **not logged**, shows up once a planned block's time has
passed without a matching clocked session covering it — the block itself gets
a hatched border on the grid too, so you can see exactly which slot slipped.
This only works once intra is configured; without it there's nothing to
compare the plan against.

Grey bands are your other calendars. A block turns orange when it overlaps one,
or starts so soon after one ends that you couldn't get there
(`TRAVEL_BUFFER_MINUTES`).

Below the rail, the month strip tracks the same two quantities against
`MONTHLY_TARGET_HOURS` (default 90), for **the month the week you're looking at
mostly falls in** — step the grid into October and the strip goes with it. A
week straddling two months belongs to whichever holds most of it.

### Month

| You do | It does |
| --- | --- |
| Click the **Month** tab | Switches the rail, the figures and the grid to the whole month |
| Click a day cell | Opens that week, where you can actually drag blocks |
| **Fill the month** | Plans the rest of the month up to `MONTHLY_TARGET_HOURS` |
| ← → | Step months instead of weeks |

Each cell is one day: a solid bar for clocked hours, hatched for planned, scaled
against the busiest day of the month so the cells are comparable to each other.
Days with planned time that never got logged say so.

*Send to iCloud* and *Sync from iCloud* are off in this tab — both are
week-shaped operations, so rather than doing something silently partial they
point you back to a week.

### Goals

Optional. A block can carry one short line of what that session is *for*, plus
any number of tags — `CPP00`, `exam`, whatever you sort your work by. Set it
from the **✎** in a block's corner on the week grid, or from **+ goal** on the
Goals tab.

The goal rides along into iCloud, appended to the event title the push already
wrote:

```
42 planned hours · 1.8h                       (no goal)
42 planned hours · 1.8h · Finish CPP00 ex02   (with one)
```

Tags go into the event's `CATEGORIES` alongside `42`, so they survive the round
trip and are searchable in Calendar.app even though it doesn't display them.

The **Goals** tab collates them: one line per planned session, grouped by day,
with a checkbox you tick once you've actually done the thing.

| You do | It does |
| --- | --- |
| Tick a box | Marks that goal done — this is the one edit that *doesn't* make the iCloud copy stale, so it never needs a re-push |
| **edit** / **+ goal** | Opens the editor; previously used tags are one click away, so a project name gets typed in full exactly once |
| Click a tag chip | Filters to that tag alone |
| Click a date | Opens that week, where you can drag the block itself |
| **This month** / **Everything** | Switches between the month on screen and every block ever planned |
| **show blank blocks** | Also lists blocks with no goal yet — the ones worth naming |
| ← → | Step months (dead in *Everything*, which isn't a month you can step) |

A goal whose session has already been and gone without being ticked is called
out in the list and counted in the header (*"n not ticked"*). That's a
different question from the *not logged* figure on the rail: **not logged** is
intra's verdict on whether you were there, **not ticked** is yours on whether
you got the thing done.

The banner above the list keeps reporting on the month, so planning progress
stays visible while you work through the list.

### Logged, planned, and the difference

The month readout deliberately separates three things that are easy to conflate:

- **clocked** — hours intra has actually recorded.
- **to place** — hours that aren't even on the grid yet.
- **to log** — hours that still have to appear on intra, planned or not.

So `covered by the plan · 22.0h still to log · 5 days left` means the grid adds
up to 90 but you haven't done it yet, while `target met · 90.4h logged` means
the hours are banked. **Fill the month** closes *to place*; only showing up
closes *to log*.

### The month-end flag

With seven days or less to go and hours still owed, a flag appears above
everything else in the header:

```
⚠ 5 days left · 22.0h still to log in September
  8.0h of that isn't on the grid yet · 4.4h a day to finish
```

It carries its own **Fill the month** button, and it shows on both tabs. It
hides once the target is met, and never appears for a month that's already over.

### The Friday scenario

Plan 4h Friday, 8h Saturday, 8h Sunday. Friday you manage only 3h. Saturday
morning, hit **Refresh from intra**: clocked reads 3.0, the gap reopens to 1.0.
Drag Saturday's block an hour longer, or hit **Fill the gap** and it finds the
hour for you, skipping anything already on your calendars.

Add a Sunday commitment that collides with a planned block and the block goes
orange. Delete it, hit **Fill the gap**, and those hours relocate to whatever
free time is left.

---

## Things worth knowing

**The monthly target is the stricter one, and auto-fill knows it.** Four weeks
at 20h is 80, so hitting 90 needs some weeks around 22.5. **Fill the gap**
targets the weekly 20; **Fill the month** targets the monthly 90 and
deliberately ignores the weekly figure, because treating 20h as a ceiling would
put 90 permanently out of reach. Run either, or both — the month fill counts
whatever the week fill already placed.

**A month fill paces itself; a week fill doesn't.** Auto-fill is
chronologically greedy, which is right for a week: the deficit is small and
hours banked early can't be lost to a cancelled Sunday. Spread over a month the
same rule would empty the whole target into the next day or two. So a month fill
goes round twice — once at the pace the month actually needs (deficit ÷ days
left), and again at the full daily ceiling only if that left a shortfall.

**There are two daily ceilings, on purpose.** `PLAN_MAX_HOURS_PER_DAY`
(default 14) is the most auto-fill will ever *suggest* for one day.
`MANUAL_MAX_HOURS_PER_DAY` (default 24) is the most a block you drag out
yourself may add to one day. What the planner is willing to propose and what
you're allowed to commit to are different questions, and only the second one
belongs in a validator. Both count hours already clocked that day. (These
replaced a single `MAX_HOURS_PER_DAY`; if that variable is still in your `.env`,
the server says so at startup rather than ignoring it silently.)

**Hours come from raw sessions, not the daily rollup.** The week reads
`/v2/users/<login>/locations`, where each session has a start and an end and a
`null` end means "still running". That sidesteps having to guess how
`locations_stats` treats an in-progress session — an earlier version guessed
wrong in both directions, first under-counting today and then double-counting
it. Sessions crossing midnight are split at the day boundary. Sanity check: the
big weekly number should always equal the sum of the day-header tallies.

**The month total sees a session you're in the middle of right now.** It reads
the month's sessions directly rather than inheriting them from whichever week
is on screen, so the figure is the same no matter which week you're looking at.

**The iCloud sync is manual in both directions.** Push rewrites the plan
calendar from the database. Pull reconciles the other way: it adopts a block's
new time if you moved it in Calendar.app, and deletes the database block if
you removed it there. Either way, pull only ever touches blocks that were
previously pushed, so a block you just made and haven't synced isn't mistaken
for a deletion. A moved block is left where it was, not adopted, if the new
time would land it on top of another block.

**Two planned blocks can never occupy the same time.** The grid won't let you
drop or drag one on top of another (busy calendar events and your own clocked
sessions are exempt — those are just shown underneath, not blocked against),
and the API rejects it too if you somehow get past the grid.

**A block you're mid-way through only counts its remaining half.** If a 9am–1pm
block is still running at 11am, the 9–11 slice is already reflected in
`clocked`, so counting the whole 4h as still-planned would double-count that
overlap. `planner.split_future_past()` is what splits it at `now`.

**A block straddling a month boundary is charged to both months.** The store
returns anything *overlapping* a window, so `planner.clip()` trims it before the
hours are counted — 22:00 on the 31st to 02:00 on the 1st is two hours of one
month and two of the next, not four of either.

**Past blocks that didn't happen get flagged, not silently dropped.** Once a
block's end time passes, `planner.flag_unlogged()` checks whether a clocked
session actually covered it. Anything left uncovered (with a five-minute
tolerance for intra's own rounding) shows up hatched on the grid and rolled
into the *not logged* figure, in both the week and the month view.

**The grid and auto-fill have separate hours.** `GRID_START`/`GRID_END` control
what the week grid draws — set them to `00:00` and `24:00` to see overnight
sessions. `DAY_WINDOW_START`/`DAY_WINDOW_END` control where auto-fill may place
blocks, so widening the grid doesn't get you scheduled at 4am. The grid scrolls
to the auto-fill start on open.

**Rate limits.** Intra allows roughly 2 requests/second, 1200/hour. Network
results are cached two minutes, and one `locations_stats` call feeds both the
week and the month, so dragging blocks is free — only *Refresh from intra* goes
back to the source.

**It won't run on iPhone** — no Python runtime, no way to host a server. It
doesn't need to: *Send to iCloud* puts the plan in Calendar.app on your phone
natively. If you really want the UI there, `--host 0.0.0.0` exposes it to your
LAN, but the app has **no authentication**, so never do that on campus wifi.

---

## Linux vs macOS

`run.sh` handles the differences that matter at runtime. These still bite when
you're editing by hand:

| Linux | macOS |
| --- | --- |
| `sed -i 's/a/b/' f` | `sed -i '' 's/a/b/' f` |
| Ctrl+Shift+R (hard reload) | Cmd+Shift+R |

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'app'`** — run from the project root
with `PYTHONPATH=.`, or just use `./run.sh`. Check the layout matches the tree
below; downloaded files sometimes land flat or in the wrong folder.

**`zsh: command not found: uvicorn`** — `~/.local/bin` isn't on your PATH. Use
`python3 -m uvicorn`.

**Page looks stale after an edit** — `--reload` only watches `*.py`, so HTML
changes never restart the server (they don't need to; the file is re-read per
request). A stale page is either the browser cache or the wrong file on disk.
Verify which:

```bash
grep -c monthstrip static/index.html                  # the file on disk
curl -s http://127.0.0.1:8042/ | grep -c monthstrip   # what the server sends
```

Both should print the same number. If the file is right but the browser isn't,
hard-reload, or visit `http://127.0.0.1:8042/?v=2` — which is what `run.sh` does
on launch, since the index route sets no cache headers of its own.

**`curl` returns nothing / `Expecting value: line 1 column 1`** — nothing is
listening on 8042. The server needs its own terminal; run curl in a second tab.

**Numbers don't match intra** — the weekly total should equal the sum of the day
tallies. If it doesn't, that's a bug, not a rounding artefact.

**The month says something different from the week** — check which month the
strip is reporting on. It follows the week you're viewing, not today's date, so
a week in late September and a week in early October will show different months.

---

## Architecture

Three read-only sources feed one read/write local store, and the browser only
ever sees the result of that merge — it never talks to intra, Google, or
iCloud directly:

```
intra /locations ──┐        ┌──► GET /api/week  ──┐
                    ├──► app/main.py              ├──► browser (FullCalendar +
Google .ics feeds ──┤        │  └──► GET /api/month┘      the month grid)
iCloud CalDAV ──────┘        │                                    │
                          SQLite ◄──── POST/PATCH/DELETE /api/blocks
                        (app/store.py)     │
                              └──► app/caldav_sync.py ──► iCloud "42 Plan" calendar
```

`/api/week` embeds the month summary for the strip, so the week view never
needs a second request. `/api/month` is the fuller payload the Month tab reads:
the same summary plus per-day clocked hours, the month's blocks, and its
unlogged ones.

What each module owns:

- **`app/planner.py`** is the only module with real logic in it, and it's
  deliberately free of I/O — no database, no network, no reading the clock
  itself (`now` is always passed in). That's what makes it unit-testable
  without a server running. It's where interval merging/subtraction lives,
  where free windows for auto-fill get computed, where calendar conflicts are
  detected, where a block straddling `now` gets split into its
  already-elapsed and still-ahead halves (so an in-progress block can't
  double-count itself against `clocked`), where a block straddling a month
  boundary gets clipped, where past blocks get checked against actual sessions
  to flag ones that never happened, and where the auto-fill placement
  algorithm itself runs. `WeekSummary` and `MonthSummary` are both here.
- **`app/main.py`** is the HTTP layer. It wires a request into
  `planner`/`store`/`caldav_sync`, wraps every slow call (intra, CalDAV,
  `.ics` fetches) in a short-TTL cache so dragging a block never re-hits the
  network, and is where rules that span multiple pieces live — "two blocks
  can't overlap," "a day has a manual hours ceiling," "auto-fill keeps a
  travel buffer around calendar events," and the two-pass pacing that keeps a
  month fill from stacking on the nearest day.
- **`app/store.py`** is the only thing that touches the SQLite file. Every
  edit clears a block's `pushed_at`, which is how `caldav_sync.py` later knows
  that block is now stale in iCloud — with one deliberate exception, ticking a
  goal done, which doesn't change anything iCloud holds. It also owns tag
  normalisation and the additive migration that brings a pre-goals database
  forward.
- **`app/calendars.py`** turns Google `.ics` feeds and iCloud CalDAV calendars
  into one flat list of busy intervals, expanding recurring events (`RRULE`)
  along the way. Read-only — it never writes anywhere. It also owns
  `week_bounds`/`month_bounds` and the rule for which month a given week
  belongs to.
- **`app/caldav_sync.py`** is the only thing that writes to iCloud, and only
  to the one calendar named in `ICLOUD_PLAN_CALENDAR` — it refuses to create
  that calendar itself, so it can never write somewhere unintended. It matches
  database blocks to calendar events by a UID derived from the block's own id,
  which is what makes push idempotent and pull able to tell "you deleted this"
  from "you never synced this" apart.
- **`app/config.py`** reads `.env` once into a frozen `Settings` object at
  import time. Nothing re-reads the environment after startup.
- **`static/index.html`** is the entire frontend — one file, FullCalendar for
  the week grid, a hand-rolled CSS grid for the month (what that view needs is
  one number per day, not event boxes), no build step or framework. It only
  ever talks to the backend through the `/api/*` JSON endpoints.

The browser never computes an hours total itself — the rail, the month strip,
the month-end flag, the *not logged* figure, every conflict/overlap flag, all
arrive pre-computed. That keeps the hours math in exactly one place, and that
place is what `tests/test_planner.py` covers.

---

## Layout

```
logtime-planner/
├── .env                 your secrets, gitignored, chmod 600
├── .env.example         committed template, no values
├── run.sh               launcher, Linux and macOS
├── pyproject.toml       dependencies
├── uv.lock
├── app/
│   ├── __init__.py
│   ├── config.py        every knob, read from .env
│   ├── ft_api.py        intra client — token caching, sessions, secret expiry
│   ├── calendars.py     .ics + CalDAV readers, recurrence expansion, week/month bounds
│   ├── store.py         SQLite for planned blocks
│   ├── planner.py       free windows, conflicts, week/month maths, auto-fill
│   ├── caldav_sync.py   push to and pull from the iCloud plan calendar
│   └── main.py          FastAPI routes + caching
├── static/
│   └── index.html       the whole UI, all three tabs
└── tests/
    ├── test_planner.py  covers planner.py, including the Friday shortfall
    └── test_goals.py    goals, tags, the legacy-column migration, ICS titles
```

`FUNCTIONALITY.md` documents the API surface, data flow, module
responsibilities, the full configuration reference, and known limits.

```bash
python3 tests/test_planner.py     # 31 passed
python3 tests/test_goals.py       # 9 passed
```

## Where to take it next

- Per-session detail on the grid: the session data is already fetched, so past
  days could show real bars instead of a header tally — planned versus actual,
  side by side.
- A month-scoped *Send to iCloud*, so a month fill doesn't have to be pushed one
  week at a time.
- A nightly job that pushes the week and warns you when remaining free time is
  less than the remaining deficit.
