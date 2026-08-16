#!/usr/bin/env python3
import re
import argparse
import hashlib
from pathlib import Path
import pandas as pd
from datetime import datetime, time, timedelta
from ics import Calendar, Event
from ics.alarm import DisplayAlarm
import pytz

# ======================
# DEFAULTS
# ======================
DEFAULT_INPUT = "schedule.csv"
DEFAULT_OUTPUT = "my_calendar_sutdacad.ics"
DEFAULT_INITIALS = "KS"
DEFAULT_PREFIX = "42 ACAD "
DEFAULT_TZ = "Asia/Singapore"
DEFAULT_UID_DOMAIN = "local.schedule"   # can be anything, e.g. "ks-tan.local"

SHIFT_TIMES = {
    1: (time(8, 0),  time(9, 30)),
    2: (time(12, 30), time(14, 0)),
    3: (time(16, 30), time(18, 0)),
}

# ======================
# IO
# ======================
def read_csv_robust(path: str) -> pd.DataFrame:
    encodings_to_try = ["utf-8-sig", "utf-8", "cp1252", "latin1", "utf-16"]
    for enc in encodings_to_try:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            pass
    return pd.read_csv(path, encoding="latin1", encoding_errors="replace")

# ======================
# HELPERS
# ======================
def has_initial(cell, target: str) -> bool:
    if pd.isna(cell):
        return False
    s = str(cell).upper()
    tokens = re.findall(r"[A-Z]{1,4}", s)
    return target.upper() in tokens

def find_event_blocks(columns):
    """
    Finds columns named like 'EVENT 1', 'EVENT 2', ... (including duplicates like EVENT 9.1).
    Each block assumed: EVENT -> CLASSROOM -> Shift 1 -> Shift 2 -> Shift 3
    Returns list of (start_index, event_col_name)
    """
    cols = list(columns)
    blocks = []
    for i, c in enumerate(cols):
        if isinstance(c, str) and re.match(r"^EVENT\s*\d+(\.\d+)?$", c.strip(), flags=re.IGNORECASE):
            blocks.append((i, c))
    return blocks

def normalize_for_uid(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", "", t)
    return t

def stable_uid(initials: str, date_obj, subject: str, shift_num: int, uid_domain: str) -> str:
    """
    Make a stable UID using a hash so it's compact and safe.
    Same inputs => same UID every run.
    """
    base = f"{initials.upper()}|{date_obj.isoformat()}|{normalize_for_uid(subject)}|shift{shift_num}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    return f"{digest}@{uid_domain}"

def parse_ymd(s: str):
    # Enforces YYYY-MM-DD
    return datetime.strptime(s, "%Y-%m-%d").date()

# ======================
# MAIN
# ======================
def main():
    ap = argparse.ArgumentParser(description="Export initials from schedule.csv into an .ics (stable UID + date filter).")
    ap.add_argument("--input", default=DEFAULT_INPUT, help="Input schedule CSV path (default: schedule.csv)")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="Output ICS path (default: my_calendar_sutdacad.ics)")
    ap.add_argument("--initials", default=DEFAULT_INITIALS, help="Initials to export (default: KS)")
    ap.add_argument("--prefix", default=DEFAULT_PREFIX, help="Subject prefix (default: '42 ACAD ')")
    ap.add_argument("--from-date", dest="from_date", default=None,
                    help="Only export events on/after this date (YYYY-MM-DD). Example: 2026-03-01")
    ap.add_argument("--tz", default=DEFAULT_TZ, help="Timezone (default: Asia/Singapore)")
    ap.add_argument("--uid-domain", default=DEFAULT_UID_DOMAIN,
                    help="UID domain suffix (default: local.schedule). Any string is fine.")
    args = ap.parse_args()

    tz = pytz.timezone(args.tz)
    from_date = parse_ymd(args.from_date) if args.from_date else None

    df = read_csv_robust(args.input)

    if "DATE" not in df.columns:
        raise ValueError("Couldn't find a DATE column. Please rename your date column header to 'DATE'.")

    dates = pd.to_datetime(df["DATE"], errors="coerce", dayfirst=True)

    event_blocks = find_event_blocks(df.columns)
    if not event_blocks:
        raise ValueError("Couldn't find any 'EVENT x' columns (e.g., EVENT 1, EVENT 2...).")

    added_tag = datetime.now(tz).strftime("[ADDED:%Y-%m-%d %H:%M SGT]")

    calendar = Calendar()
    count = 0

    for idx, row in df.iterrows():
        d = dates.iloc[idx]
        if pd.isna(d):
            continue
        event_date = d.date()

        if from_date and event_date < from_date:
            continue

        for start_idx, event_col in event_blocks:
            if start_idx + 4 >= len(df.columns):
                continue

            event_name = row.iloc[start_idx]
            location = row.iloc[start_idx + 1]
            s1 = row.iloc[start_idx + 2]
            s2 = row.iloc[start_idx + 3]
            s3 = row.iloc[start_idx + 4]

            if pd.isna(event_name) or str(event_name).strip() == "":
                continue

            base_subject = str(event_name).strip()
            full_subject = f"{args.prefix}{base_subject}"

            for shift_num, cell in [(1, s1), (2, s2), (3, s3)]:
                if not has_initial(cell, args.initials):
                    continue

                start_t, end_t = SHIFT_TIMES[shift_num]
                start_dt = tz.localize(datetime.combine(event_date, start_t))
                end_dt = tz.localize(datetime.combine(event_date, end_t))

                e = Event()
                e.name = full_subject
                e.begin = start_dt
                e.end = end_dt

                # Stable UID (prevents duplicate imports)
                e.uid = stable_uid(args.initials, event_date, full_subject, shift_num, args.uid_domain)

                if pd.notna(location) and str(location).strip():
                    e.location = str(location).strip()

                details = f"{event_col} | Shift {shift_num} | Scheduled: {str(cell).strip()}"
                e.description = f"{added_tag}\n{details}"

                e.alarms = [
                    DisplayAlarm(trigger=timedelta(days=-1)),
                    DisplayAlarm(trigger=timedelta(minutes=-10)),
                ]

                calendar.events.add(e)
                count += 1

    out_path = Path(args.output)
    out_path.write_text(calendar.serialize())
    print(f"✅ Done! Exported {count} events for '{args.initials}' to '{out_path}'"
          + (f" (from {from_date})" if from_date else ""))

if __name__ == "__main__":
    main()