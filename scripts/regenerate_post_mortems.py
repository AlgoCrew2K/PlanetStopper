"""
Historical post-mortem money-math regeneration from shadow_history ground truth.

PURPOSE: repair post_mortem_*.json files whose if-held basis was booked from
the clobbered bot_state.current_return (2026-07-09 droplet audit, Finding 2:
7 of 11 audited days sign-flipped). shadow_history.current_return is the
authoritative if-held series — written in the data phase from Composer's
last_percent_change (alpha_bot_execution.py record_shadow_observation call)
and never touched by the action-phase "TRUE SHADOW RETURN OVERRIDE" that
clobbers bot_state.

WHAT IT RECOMPUTES per trigger entry, and NOTHING else:
    shadow_return          <- true if-held at the snapshot cutoff (percent)
    saved_pct_guard_alpha  <- exit_return - true_if_held (percent points)
    saved_dollars          <- symphony_value * saved_pct / 100
    summary.positive_guard_alpha_count  <- count(saved_pct > 0)
exit_return, attempted_trigger_level, hwm fields, strategy_params, holdings
and every other field are preserved byte-for-byte.

SAFETY MODEL (operator-gated deploy tool — NEVER run against the droplet
without an approved deploy + confirmed droplet backup):
    * DRY RUN is the default: prints a full per-entry old->new report and
      writes nothing.
    * --apply first copies the whole post-mortems directory to a timestamped
      backup sibling, then rewrites only files with changed entries.
    * --apply REFUSES to write if ANY entry in the window cannot be resolved
      to a shadow_history row (all-or-nothing: a partial money repair is
      worse than a loud failure).

BASIS: the last shadow_history row per (symphony_id, trading_day) with
ET time-of-day <= 15:54:59 — the same "snapshot-time basis" the Stage-1
post-mortem freeze declares (Stage 1 fires in the 15:54 ET minute; see the
rebalance-blackout call in alpha_bot_execution.py). Rows after 15:54 exist
(engine ticks to ~16:04), so the cutoff is load-bearing: dropping it would
silently switch the repair to an EOD basis.

USAGE:
    python scripts/regenerate_post_mortems.py                       # dry run
    python scripts/regenerate_post_mortems.py --db alphabot_state.db \
        --post-mortems-dir post_mortems --start 2026-06-23 --end 2026-07-08
    python scripts/regenerate_post_mortems.py --apply               # gated

Default window 2026-06-22..2026-07-09 (widened 2026-07-20, F-008): both
boundary days are contaminated per the F-008 data-integrity audit — the
prior default (2026-06-23..2026-07-08) rested on a refuted assumption that
06-22 was already manually regenerated to truth and that 07-09 was written
correctly by the fixed Stage-1 (see tests/app/test_post_mortem_validity_
guard.py for the real-06-22-capture evidence). The 07-09 exit_triggers rows
80-83 4x-duplicate is a SEPARATE gated data repair — deliberately out of scope.

PRECISION NOTE: the file's exit_return is stored rounded to 2dp, so the
recomputed saved_dollars inherits ~±0.005pp of that rounding (≈±$0.06 on a
$1,200 position). The audit's ground-truth recompute carries the same limit.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime

# Stage-1 post-mortem freeze fires in the 15:54 ET minute; every row through
# the end of that minute is on the declared snapshot basis. Verified against
# the 2026-07-09 audit ground truth: this cutoff reproduces -0.67 (06-24 LQD),
# 7.22 (06-23 Golden Age), 1.89 (06-23 Reasonabilists) exactly.
SNAPSHOT_CUTOFF_ET = "15:54:59"

# Default repair window widened 2026-07-20 (F-008 data-integrity fix): both
# 06-22 and 07-09 are contaminated per the F-008 audit — the prior "06-22
# already correct" / "07-09+ written correctly" assumption is refuted, see
# the module docstring above.
DEFAULT_START = "2026-06-22"
DEFAULT_END = "2026-07-09"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_name_account_map(db_path: str) -> dict[tuple[str, str], str]:
    """Build (symphony_name, account_id) -> symphony_id from bot_state.

    shadow_history.trigger_id was never populated (audit Finding 10), so the
    name+account pair is the only join key the historical files carry. The
    map hard-fails on ambiguity: a silent wrong-symphony join would write
    another symphony's if-held into a money field.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT data FROM bot_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"FATAL: no bot_state row in {db_path}")

    state = json.loads(row[0])
    mapping: dict[tuple[str, str], str] = {}
    for sym_id, sym in state.items():
        if not isinstance(sym, dict) or "name" not in sym:
            continue
        key = (sym.get("name"), sym.get("account"))
        if key in mapping and mapping[key] != sym_id:
            raise SystemExit(
                f"FATAL: ambiguous (name, account) {key!r} maps to both "
                f"{mapping[key]} and {sym_id} — refusing to guess on a money repair."
            )
        mapping[key] = sym_id
    return mapping


def true_if_held(db_path: str, symphony_id: str, trading_day: str) -> float | None:
    """Last shadow_history.current_return for the day at the snapshot cutoff."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT current_return FROM shadow_history "
            "WHERE symphony_id = ? AND trading_day = ? AND substr(ts_et, 12, 8) <= ? "
            "ORDER BY ts_utc DESC LIMIT 1",
            (symphony_id, trading_day, SNAPSHOT_CUTOFF_ET),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        return None
    value = float(row[0])
    # NO NaN-fail-open: a non-finite if-held must surface as unresolved,
    # never flow into saved_dollars arithmetic.
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def regenerate_file(
    report_path: str, db_path: str, name_map: dict[tuple[str, str], str]
) -> tuple[dict, list[dict], list[str]]:
    """Compute the corrected report for one post-mortem file.

    Returns (new_report, per-entry change records, unresolved descriptions).
    Does not write anything.
    """
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    trading_day = report.get("date") or os.path.basename(report_path)[len("post_mortem_") : -5]
    changes: list[dict] = []
    unresolved: list[str] = []
    positive_count = 0

    for entry in report.get("triggers", []):
        key = (entry.get("symphony_name"), entry.get("account_id"))
        sym_id = name_map.get(key)
        if sym_id is None:
            unresolved.append(f"{trading_day}: no symphony_id for (name, account) {key!r}")
            positive_count += 1 if entry.get("saved_pct_guard_alpha", 0) > 0 else 0
            continue

        if_held = true_if_held(db_path, sym_id, trading_day)
        if if_held is None:
            unresolved.append(
                f"{trading_day}: {sym_id} ({entry.get('symphony_name')}) has no finite "
                f"shadow_history row at cutoff {SNAPSHOT_CUTOFF_ET}"
            )
            positive_count += 1 if entry.get("saved_pct_guard_alpha", 0) > 0 else 0
            continue

        exit_return = entry.get("exit_return", 0.0)
        sym_val = entry.get("symphony_value", 0.0)
        # Mirror reporting.py Stage-1 math exactly: saved_pct in percent
        # points, dollars from the unrounded pct, positive-only value guard.
        saved_pct = exit_return - if_held
        saved_dollars = sym_val * (saved_pct / 100.0) if sym_val > 0 else 0.0
        # Classify win/loss from the UNROUNDED saved_pct, matching Stage-1
        # (reporting.py counts saved_pct > 0 before rounding) — counting the
        # stored rounded field would flip entries with 0 < saved_pct < 0.005.
        positive_count += 1 if saved_pct > 0 else 0

        old = {
            "shadow_return": entry.get("shadow_return"),
            "saved_pct_guard_alpha": entry.get("saved_pct_guard_alpha"),
            "saved_dollars": entry.get("saved_dollars"),
        }
        new = {
            "shadow_return": round(if_held, 2),
            "saved_pct_guard_alpha": round(saved_pct, 2),
            "saved_dollars": round(saved_dollars, 2),
        }
        if old != new:
            entry.update(new)
            entry["if_held_source"] = "shadow_history"
            changes.append(
                {
                    "day": trading_day,
                    "symphony": entry.get("symphony_name"),
                    "old": old,
                    "new": new,
                }
            )

    # Producer semantic (reporting.py Stage 1): count of entries whose UNROUNDED
    # recomputed saved_pct is positive (pf-review finding 3 — the rounded stored
    # field misclassifies 0 < saved_pct < 0.005). Unresolved/unmapped entries
    # contribute their stored classification (best available; --apply refuses on
    # any unresolved anyway).
    report["summary"]["positive_guard_alpha_count"] = positive_count
    if changes:
        report["regenerated"] = {
            "at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "shadow_history",
            "cutoff_et": SNAPSHOT_CUTOFF_ET,
            "script": "scripts/regenerate_post_mortems.py",
            "fields": ["shadow_return", "saved_pct_guard_alpha", "saved_dollars"],
        }
    return report, changes, unresolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--db", default=os.path.join(_REPO_ROOT, "alphabot_state.db"))
    parser.add_argument("--post-mortems-dir", default=os.path.join(_REPO_ROOT, "post_mortems"))
    parser.add_argument("--start", default=DEFAULT_START, help="first trading day (inclusive)")
    parser.add_argument("--end", default=DEFAULT_END, help="last trading day (inclusive)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write corrected files (creates a timestamped backup first); default is dry run",
    )
    args = parser.parse_args(argv)

    name_map = load_name_account_map(args.db)

    paths = sorted(glob.glob(os.path.join(args.post_mortems_dir, "post_mortem_*.json")))
    in_window = [
        p for p in paths if args.start <= os.path.basename(p)[len("post_mortem_") : -5] <= args.end
    ]
    if not in_window:
        print(f"No post-mortem files in [{args.start}, {args.end}] under {args.post_mortems_dir}")
        return 0

    all_results: list[tuple[str, dict, list[dict]]] = []
    all_unresolved: list[str] = []
    old_grand = new_grand = 0.0

    for path in in_window:
        new_report, changes, unresolved = regenerate_file(path, args.db, name_map)
        all_unresolved.extend(unresolved)
        with open(path, encoding="utf-8") as f:
            old_report = json.load(f)
        old_day = sum(t.get("saved_dollars", 0.0) for t in old_report.get("triggers", []))
        new_day = sum(t.get("saved_dollars", 0.0) for t in new_report.get("triggers", []))
        old_grand += old_day
        new_grand += new_day
        all_results.append((path, new_report, changes))
        marker = "" if abs(new_day - old_day) < 0.005 else "  <-- CHANGED"
        print(
            f"{os.path.basename(path)}: booked ${old_day:+.2f} -> corrected ${new_day:+.2f} "
            f"({len(changes)} of {len(new_report.get('triggers', []))} entries){marker}"
        )
        for c in changes:
            print(
                f"    {c['symphony'][:45]}: if-held {c['old']['shadow_return']} -> "
                f"{c['new']['shadow_return']}, saved ${c['old']['saved_dollars']:+.2f} -> "
                f"${c['new']['saved_dollars']:+.2f}"
            )

    print(
        f"\nTOTAL: booked ${old_grand:+.2f} -> corrected ${new_grand:+.2f} "
        f"(delta ${new_grand - old_grand:+.2f})"
    )

    if all_unresolved:
        print("\nUNRESOLVED ENTRIES (left untouched):", file=sys.stderr)
        for u in all_unresolved:
            print(f"  !! {u}", file=sys.stderr)

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write (gated).")
        return 2 if all_unresolved else 0

    if all_unresolved:
        print(
            "\nREFUSING --apply: unresolved entries above must be investigated first "
            "(all-or-nothing money repair).",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_dir = f"{args.post_mortems_dir.rstrip(os.sep)}.bak-regen-{stamp}"
    shutil.copytree(args.post_mortems_dir, backup_dir)
    print(f"\nBackup written: {backup_dir}")

    written = 0
    for path, new_report, changes in all_results:
        if not changes:
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_report, f, indent=4)
        written += 1
    print(f"Applied: {written} file(s) rewritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
