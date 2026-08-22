# 42 logtime planner

Plan your 20 hours a week against the calendars you actually live by, and
re-plan them when the week doesn't go as intended.

- **Actual hours** come from the 42 intra API. They are never typed in by hand.
- **Busy time** comes from your Google calendars (secret `.ics` feeds) and your
  iCloud calendars (CalDAV).
- **Planned blocks** live in a local SQLite file, are dragged around in the
  browser, and sync to one dedicated iCloud calendar so they show up in
  Calendar.app on every device.

---

## Running it

```bash
./run.sh        # Linux
./run_mac.sh    # macOS
```

Both do the same three things: free port 8042 if a previous run left it held,
start uvicorn with `--reload`, and open your browser at
`http://127.0.0.1:8042`. Ctrl+C stops the server cleanly — the `trap` makes
sure uvicorn dies with the script instead of being orphaned on the port.

They differ in exactly two places, which is the whole reason there are two
files: `xdg-open` vs `open` for launching the browser, and `xargs -r` vs
`xargs` (BSD xargs has no `-r` flag and doesn't need one).

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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
```

The venv is optional — installing to `~/.local` works too — but it keeps these
packages from mixing with your other Python projects.

### 1. Intra credentials

profile.intra.42.fr → *Settings* → *API* → *Register a new app*. No redirect URI
needed; the client-credentials flow doesn't use one. Copy UID → `FT_UID`,
secret → `FT_SECRET`, your login → `FT_LOGIN`.

**42 client secrets expire on roughly a 30-day cycle.** The token response
carries the deadline in `secret_valid_until`, so `/api/health` shows you how
many days are left and the server prints a warning below seven days. When it
expires, regenerate on the same page and update `.env` — nothing else changes.

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

| You do | It does |
| --- | --- |
| Drag on an empty column | Places a block |
| Drag a block | Moves it, recomputes the gap |
| Grab a block's edge | Resizes it |
| Click a block | Deletes it |
| **Fill the gap** | Auto-places blocks in the earliest free time until the week is covered |
| **Refresh from intra** | Pulls your real clocked hours |
| **Send to iCloud** | Makes the plan calendar match the grid |
| **Sync from iCloud** | Removes blocks you deleted in Calendar.app |

The rail across the top is the week: solid navy is hours intra says you've done,
hatched teal is planned-but-not-yet-done, and the gap to the finish line is
what's unaccounted for. Below it, the month strip tracks the same thing against
`MONTHLY_TARGET_HOURS` (default 90) with days remaining in the month.

Grey bands are your other calendars. A block turns orange when it overlaps one,
or starts so soon after one ends that you couldn't get there
(`TRAVEL_BUFFER_MINUTES`).

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

**Weekly and monthly targets aren't reconciled.** Four weeks at 20h is 80, so a
90h month is the stricter constraint — some weeks need ~22.5h. **Fill the gap**
only targets the weekly 20. The month strip is a readout, not something autofill
optimises toward.

**Hours come from raw sessions, not the daily rollup.** The week reads
`/v2/users/<login>/locations`, where each session has a start and an end and a
`null` end means "still running". That sidesteps having to guess how
`locations_stats` treats an in-progress session — an earlier version guessed
wrong in both directions, first under-counting today and then double-counting
it. Sessions crossing midnight are split at the day boundary. Sanity check: the
big weekly number should always equal the sum of the day-header tallies.

**The iCloud sync is manual in both directions.** Push rewrites the plan
calendar from the database; pull deletes database blocks whose calendar events
are gone. Pull only ever touches blocks that were previously pushed, so a block
you just made and haven't synced isn't mistaken for a deletion. Edits you make
to event *times* in Calendar.app are overwritten on the next push — move blocks
in the planner, not in Calendar.

**Confirm your campus's week.** `WEEK_START_DAY` defaults to Monday and the
target is a plain calendar-week sum. Some campuses use a rolling window; if
yours does, `summarise()` in `app/planner.py` is the only thing to change.

**Rate limits.** Intra allows roughly 2 requests/second, 1200/hour. Network
results are cached two minutes per week, and one `locations_stats` call feeds
both the week and month views, so dragging blocks is free — only *Refresh from
intra* goes back to the source.

**It won't run on iPhone** — no Python runtime, no way to host a server. It
doesn't need to: *Send to iCloud* puts the plan in Calendar.app on your phone
natively. If you really want the UI there, `--host 0.0.0.0` exposes it to your
LAN, but the app has **no authentication**, so never do that on campus wifi.

---

## Linux vs macOS

Most commands are identical. These aren't:

| Linux | macOS |
| --- | --- |
| `xdg-open URL` | `open URL` |
| `xargs -r` | `xargs` |
| `sed -i 's/a/b/' f` | `sed -i '' 's/a/b/' f` |
| Ctrl+Shift+R (hard reload) | Cmd+Shift+R |

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'app'`** — run from the project root
with `PYTHONPATH=.`, or just use the launch scripts. Check the layout matches
the tree below; downloaded files sometimes land flat or in the wrong folder.

**`zsh: command not found: uvicorn`** — `~/.local/bin` isn't on your PATH. Use
`python3 -m uvicorn`.

**Page looks stale after an edit** — `--reload` only watches `*.py`, so HTML
changes never restart the server (they don't need to; the file is re-read per
request). A stale page is either the browser cache or the wrong file on disk.
Verify which:

```bash
grep -c monthstrip static/index.html          # the file on disk
curl -s http://127.0.0.1:8042/ | grep -c monthstrip   # what the server sends
```

Both should print 5. If the file is right but the browser isn't, hard-reload, or
visit `http://127.0.0.1:8042/?v=2`. The index route sends `Cache-Control:
no-store`, so this should be rare.

**`curl` returns nothing / `Expecting value: line 1 column 1`** — nothing is
listening on 8042. The server needs its own terminal; run curl in a second tab.

**Numbers don't match intra** — the weekly total should equal the sum of the day
tallies. If it doesn't, that's a bug, not a rounding artefact.

---

## Layout

```
logtime-planner/
├── .env                 your secrets, gitignored, chmod 600
├── .env.example         committed template, no values
├── run.sh               Linux launcher
├── run_mac.sh           macOS launcher
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── config.py        every knob, read from .env
│   ├── ft_api.py        intra client — token caching, sessions, secret expiry
│   ├── calendars.py     .ics + CalDAV readers, recurrence expansion
│   ├── store.py         SQLite for planned blocks
│   ├── planner.py       free windows, conflicts, deficit maths, auto-fill
│   ├── caldav_sync.py   push to and pull from the iCloud plan calendar
│   └── main.py          FastAPI routes + caching
├── static/
│   └── index.html       the whole UI
└── tests/
    └── test_planner.py  covers planner.py, including the Friday shortfall
```

`FUNCTIONALITY.md` documents the API surface, data flow, module
responsibilities, the full configuration reference, and known limits.

```bash
python3 tests/test_planner.py     # 12 passed
```

## Where to take it next

- Per-session detail on the grid: the session data is already fetched, so past
  days could show real bars instead of a header tally — planned versus actual,
  side by side.
- Make **Fill the gap** aware of the monthly target, not just the weekly one.
- A nightly job that pushes the week and warns you when remaining free time is
  less than the remaining deficit.
