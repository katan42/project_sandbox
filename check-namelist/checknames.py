import re
import sys
import json
import argparse
from difflib import SequenceMatcher
from pathlib import Path


# =========================
# Cleaning / Normalization
# =========================

def _clean(name):
    name = name.lower().strip()
    name = name.replace(",", " ")

    honorifics = [
        r"\bmr\b", r"\bms\b", r"\bmiss\b", r"\bmrs\b",
        r"\bmdm\b", r"\bmadam\b", r"\bmadame\b",
        r"\bdr\b", r"\bdoctor\b",
        r"\bprof\b", r"\bprofessor\b",
        r"\bsir\b", r"\bencik\b", r"\bpuan\b",
    ]
    for h in honorifics:
        name = re.sub(h, "", name)

    name = re.sub(r"\s*/\s*", "/", name)

    mapping = {
        r"\bbte\b": "binte",
        r"\bbinte\b": "binte",
        r"\bbinting\b": "binte",
        r"\bbinti\b": "binti",
        r"\bbin\b": "bin",
        r"\bs/o\b": "son of",
        r"\bd/o\b": "daughter of",
        r"\ba/l\b": "a/l",
        r"\ba/p\b": "a/p",
    }
    for pat, rep in mapping.items():
        name = re.sub(pat, rep, name)

    # OPTION A: make bin/binti/binte optional
    name = re.sub(r"\b(bin|binti|binte)\b", "", name)

    name = re.sub(r"\s+", " ", name).strip()
    return name


def pretty(name):
    out = []
    for t in name.split():
        out.append(t.upper() if "/" in t else t.title())
    return " ".join(out)


def merged_tokens(tokens):
    merged = set(tokens)
    for i in range(len(tokens) - 1):
        merged.add(tokens[i] + tokens[i + 1])
    return merged


# =========================
# Extraction
# =========================

def extract_names_from_text(text):
    pattern = re.compile(r"\b\d+\.\s*(.+?)(?=\s*\[|\s+\d+\.|$)", re.DOTALL)
    raw = pattern.findall(text)

    results = set()
    for r in raw:
        c = _clean(r)
        if c:
            results.add(c)
    return results


# =========================
# Suggestion Engine
# =========================

def similar_names(name, expected_list, used_expected):
    name_tokens = name.split()
    name_set = set(name_tokens)
    name_merged = merged_tokens(name_tokens)
    name_first = name_tokens[0] if name_tokens else ""

    suggestions = []

    for exp in expected_list:
        if exp in used_expected:
            continue

        exp_tokens = exp.split()
        exp_set = set(exp_tokens)
        exp_merged = merged_tokens(exp_tokens)
        exp_first = exp_tokens[0] if exp_tokens else ""

        if name_first and exp_first and name_first == exp_first:
            suggestions.append((exp, 4, 1.0))
            continue

        if (name_set & exp_set) or (name_merged & exp_merged):
            overlap = len((name_set | name_merged) & (exp_set | exp_merged))
            suggestions.append((exp, 3, overlap))
            continue

        ratio = SequenceMatcher(None, name, exp).ratio()
        if ratio >= 0.70:
            suggestions.append((exp, 2, ratio))

    suggestions.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return [s[0] for s in suggestions[:3]]


# =========================
# Storage
# =========================

def load_lines(path):
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def save_lines(path, lines):
    seen = set()
    out = []
    for ln in lines:
        if ln not in seen:
            out.append(ln)
            seen.add(ln)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def load_alias_map(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_alias_map(path, d):
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_aliases(given_set, alias_map):
    out = set()
    for g in given_set:
        out.add(alias_map.get(g, g))
    return out


# =========================
# Interactive I/O
# =========================

def ask_multiline(prompt):
    print(prompt)
    print("(Finish by pressing Enter on an empty line.)")
    lines = []
    while True:
        try:
            ln = input()
        except EOFError:
            break
        if ln.strip() == "":
            break
        lines.append(ln)
    return "\n".join(lines)


def choose_menu(prompt, options):
    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}) {pretty(opt)}")
    print("  0) Skip")
    while True:
        ans = input("> ").strip()
        if ans == "0" or ans == "":
            return -1
        if ans.isdigit():
            k = int(ans)
            if 1 <= k <= len(options):
                return k - 1
        print("Please enter a valid number.")


# =========================
# Core matching
# =========================

def compute(expected_clean, given_clean):
    matched = expected_clean & given_clean
    missing = expected_clean - given_clean
    extras = given_clean - expected_clean
    return missing, extras, matched


# =========================
# Delete participants
# =========================

def delete_participants_flow(expected_raw, alias_map, delete_args):
    """
    Deletes participants from expected_raw and cleans alias_map:
    - remove expected names that match delete_args (cleaned)
    - remove any alias mappings whose target is deleted
    """
    expected_clean_map = { _clean(x): x for x in expected_raw if _clean(x) }
    expected_clean_list = sorted(expected_clean_map.keys())

    # If user passed names as args, use those; else interactive search
    to_delete_clean = set()

    if delete_args:
        for x in delete_args:
            cx = _clean(x)
            if cx:
                to_delete_clean.add(cx)
    else:
        # interactive: search substring in cleaned form
        while True:
            q = input("Search name to delete (or blank to stop): ").strip()
            if not q:
                break
            cq = _clean(q)
            hits = [c for c in expected_clean_list if cq in c]
            if not hits:
                print("No matches.")
                continue
            # show first 15
            hits = hits[:15]
            idx = choose_menu("Choose a participant to delete:", hits)
            if idx >= 0:
                to_delete_clean.add(hits[idx])
                print(f"Queued for deletion: {pretty(hits[idx])}")

    if not to_delete_clean:
        print("No participants selected for deletion.")
        return expected_raw, alias_map

    print("\nWill delete:")
    for c in sorted(to_delete_clean):
        print("-", pretty(c))

    confirm = input("Confirm delete? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled deletion.")
        return expected_raw, alias_map

    # Remove from expected_raw
    new_expected_raw = []
    deleted_targets = set(to_delete_clean)
    for orig in expected_raw:
        c = _clean(orig)
        if c and c in deleted_targets:
            continue
        new_expected_raw.append(orig)

    # Clean aliases: remove mappings pointing to deleted targets
    new_alias = {}
    removed_alias_count = 0
    for observed, target in alias_map.items():
        if target in deleted_targets:
            removed_alias_count += 1
            continue
        new_alias[observed] = target

    print(f"Deleted {len(deleted_targets)} participant(s). Removed {removed_alias_count} alias mapping(s).")
    return new_expected_raw, new_alias


# =========================
# CLI
# =========================

def build_argparser():
    p = argparse.ArgumentParser(
        prog="check_names.py",
        description="Check attendance names vs expected namelist, with interactive reconciliation + saved aliases."
    )
    p.add_argument("org", help="Org label used to create/read name_<org>.txt and name_<org>.aliases.json")

    p.add_argument("--dir", default=".", help="Directory to store/read files (default: current folder)")
    p.add_argument("--show-aliases", action="store_true", help="Print saved aliases and exit")
    p.add_argument("--reset-aliases", action="store_true", help="Clear aliases for this org and exit")
    p.add_argument("--add-expected", action="store_true", help="Interactively append expected names and exit")

    # NEW:
    p.add_argument("--delete-participants", nargs="*", metavar="NAME",
                   help="Delete participants from expected list. If NAME(s) provided, deletes those; "
                        "otherwise runs interactive search+delete.")

    p.add_argument("--names-file", default=None, help="Override names filename (default: name_<org>.txt)")
    p.add_argument("--aliases-file", default=None, help="Override aliases filename (default: name_<org>.aliases.json)")
    p.add_argument("--chunk-file", default=None, help="Read attendance chunk from a text file instead of pasting")
    return p

def print_help_cheatsheet(org):
    print("\n=== Quick commands ===")
    print(f"Run check (interactive):           python3 check_names.py {org}")
    print(f"Show aliases:                      python3 check_names.py {org} --show-aliases")
    print(f"Reset aliases:                     python3 check_names.py {org} --reset-aliases")
    print(f"Add expected names:                python3 check_names.py {org} --add-expected")
    print(f"Delete participants (interactive): python3 check_names.py {org} --delete-participants")
    print(f"Delete participants (by name):     python3 check_names.py {org} --delete-participants \"Name 1\" \"Name 2\"")
    print(f"Read chunk from file:              python3 check_names.py {org} --chunk-file attendance.txt")
    print(f"Show all flags/help:               python3 check_names.py --help")

def main():
    args = build_argparser().parse_args()

    base = Path(__file__).parent.resolve()
    base.mkdir(parents=True, exist_ok=True)

    org = args.org.strip()
    names_file = Path(args.names_file) if args.names_file else (base / f"name_{org}.txt")
    alias_file = Path(args.aliases_file) if args.aliases_file else (base / f"name_{org}.aliases.json")

    # First-time creation
    if not names_file.exists():
        print(f"First run detected. Creating {names_file.name}")
        seed = ask_multiline("Paste expected names (one per line):")
        seed_lines = [ln.strip() for ln in seed.splitlines() if ln.strip()]
        save_lines(names_file, seed_lines)
        save_alias_map(alias_file, {})
        print(f"Saved {len(seed_lines)} expected names.\n")

    expected_raw = load_lines(names_file)
    alias_map = load_alias_map(alias_file)

    # Modes
    if args.show_aliases:
        if not alias_map:
            print("No aliases saved.")
        else:
            print(f"=== Aliases for {org} ===")
            for observed in sorted(alias_map.keys()):
                print(f"- {pretty(observed)}  ->  {pretty(alias_map[observed])}")
        return

    if args.reset_aliases:
        save_alias_map(alias_file, {})
        print(f"Cleared aliases: {alias_file.name}")
        return

    if args.add_expected:
        more = ask_multiline("Paste expected names to ADD (one per line):")
        more_lines = [ln.strip() for ln in more.splitlines() if ln.strip()]
        expected_raw.extend(more_lines)
        save_lines(names_file, expected_raw)
        print(f"Added {len(more_lines)} names to {names_file.name}")
        return

    if args.delete_participants is not None:
        # NOTE: argparse gives [] when flag present without values, or list of names when provided.
        expected_raw, alias_map = delete_participants_flow(expected_raw, alias_map, args.delete_participants)
        save_lines(names_file, expected_raw)
        save_alias_map(alias_file, alias_map)
        return

    # Normal run
    expected_clean = {_clean(n) for n in expected_raw if _clean(n)}
    expected_list = sorted(list(expected_clean))

    if args.chunk_file:
        chunk = Path(args.chunk_file).read_text(encoding="utf-8")
    else:
        chunk = ask_multiline("Paste the attendance chunk (single line or multi-line):")

    given = extract_names_from_text(chunk)
    given_mapped = apply_aliases(given, alias_map)

    missing, extras, matched = compute(expected_clean, given_mapped)

    print("\n=== Expected names NOT mentioned ===")
    if missing:
        for m in sorted(missing):
            print("-", pretty(m))
        print(f"Total missing: {len(missing)}")
    else:
        print("None")

    print("\n=== Names that don't match the namelist ===")
    if not extras:
        print("None")
    else:
        for ex in sorted(extras):
            print(f"\n- {pretty(ex)}")
            suggestions = similar_names(ex, expected_list, used_expected=matched)

            if not suggestions:
                print("No suggestions found.")

                # Offer missing names as candidates
                missing_candidates = sorted(list(missing - matched))  # still-missing, not already used
                if missing_candidates:
                    idx2 = choose_menu("Pick from MISSING expected names to map this to:", missing_candidates)
                    if idx2 >= 0:
                        chosen = missing_candidates[idx2]
                        alias_map[ex] = chosen
                        matched.add(chosen)
                        print(f"✅ Mapped '{pretty(ex)}' -> '{pretty(chosen)}'")
                        
                        save_alias_map(alias_file, alias_map)
                        save_lines(names_file, expected_raw)
                    continue

            idx = choose_menu("Choose a match to map this name to (saved alias):", suggestions)
            if idx >= 0:
                chosen = suggestions[idx]
                alias_map[ex] = chosen
                matched.add(chosen)
                print(f"✅ Mapped '{pretty(ex)}' -> '{pretty(chosen)}'")
            else:
                add = input("Add this as a NEW expected name? (y/N): ").strip().lower()
                if add == "y":
                    expected_raw.append(pretty(ex))
                    expected_clean.add(ex)
                    expected_list = sorted(list(expected_clean))
                    save_lines(names_file, expected_raw)
                    print(f"✅ Added '{pretty(ex)}' to {names_file.name}")

            save_alias_map(alias_file, alias_map)
            save_lines(names_file, expected_raw)

    # Recompute and show missing list
    given_mapped2 = apply_aliases(given, alias_map)
    missing2, extras2, matched2 = compute(expected_clean, given_mapped2)

    print("\n=== After reconciliation ===")
    print(f"Missing: {len(missing2)} | Extras: {len(extras2)} | Matched: {len(matched2)}")
    if missing2:
        print("\nStill missing:")
        for m in sorted(missing2):
            print("-", pretty(m))

    print(f"\nSaved namelist: {names_file.name}")
    print(f"Saved aliases : {alias_file.name}")
    print_help_cheatsheet(org)


if __name__ == "__main__":
    main()
