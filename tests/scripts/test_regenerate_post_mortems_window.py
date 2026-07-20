"""
RED tests — F-008 Post-Mortem Data Integrity, AC-1: regen script date window.

THE BUG: scripts/regenerate_post_mortems.py's DEFAULT_START/DEFAULT_END
constants (lines ~70-74) encode a wrong assumption from the docstring
(lines ~43-46): "06-22 was manually regenerated post-close and already
matches truth; 07-09 onward is written correctly by the fixed Stage-1."
The F-008 audit refutes this — 06-22 and 07-09-style days ARE contaminated
(see test_post_mortem_validity_guard.py). The current default window
(2026-06-23..2026-07-08) silently EXCLUDES both boundary days from a
default-window repair run.

WHAT AC-1 REQUIRES: "the window is exactly what the caller requests" — no
hard-coded exclusion of any date. Two independent things to prove:
  1. The `in_window` filtering logic itself (a simple `start <= date <= end`
     comprehension, scripts/regenerate_post_mortems.py:231-233) has NO
     special-casing that excludes 06-22/07-09 when explicitly requested —
     this should ALREADY be true today (f8-impl's reading, confirmed here
     as a regression anchor, not expected to be RED).
  2. The DEFAULT_START/DEFAULT_END constants must be widened to COVER both
     boundary days, so a caller who does NOT pass --start/--end (the common
     case) still gets them repaired — THIS is the actual RED-today bug.

Per the 2026-07-20 handoff exchange between f8-tw and f8-impl: this is a
constants-only fix (DEFAULT_START -> include 2026-06-22, DEFAULT_END ->
include 2026-07-09), no `in_window` logic change, and the stale docstring
claim gets corrected as GREEN fallout (no dedicated RED needed for the
prose itself — the constants assertion below is what forces the false
premise out of the code).

No live DB, no droplet. The functional test below builds a throwaway
temp SQLite DB with the minimal bot_state + shadow_history schema the
script's dry-run path touches (regenerate_file / load_name_account_map /
true_if_held) — never the real alphabot_state.db.
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

import scripts.regenerate_post_mortems as regen_mod


def _make_db(db_path: Path, symphonies: dict[str, dict[str, str]]) -> None:
    """Minimal bot_state + empty shadow_history schema — enough for
    load_name_account_map + true_if_held to run without raising. No shadow
    rows means every entry resolves if_held=None (marked unresolved by
    regenerate_file, not a crash) — irrelevant to this file's assertions,
    which only check WHICH files get processed, not the repaired dollar math
    (that's covered by test_postmortem_saved_dollars_source.py).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE bot_state (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute(
            "INSERT INTO bot_state (id, data) VALUES (1, ?)",
            (json.dumps(symphonies),),
        )
        conn.execute(
            "CREATE TABLE shadow_history ("
            "symphony_id TEXT, account_id TEXT, trading_day TEXT, "
            "ts_utc TEXT, ts_et TEXT, current_return REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def _make_pm_dir(tmp_path: Path, dates: list[str]) -> Path:
    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    for d in dates:
        payload = {
            "date": d,
            "summary": {
                "total_monitored": 1,
                "total_triggered": 1,
                "positive_guard_alpha_count": 0,
            },
            "tomorrow_target_holdings": {},
            "triggers": [
                {
                    "symphony_name": "Window Test Symphony",
                    "symphony_value": 1000.0,
                    "account_id": "acct-window-test",
                    "exit_reason": "Trailing Stop",
                    "exit_return": 1.0,
                    "attempted_trigger_level": 0.5,
                    "shadow_return": 0.2,
                    "shadow_hwm": 1.0,
                    "saved_pct_guard_alpha": 0.8,
                    "saved_dollars": 8.0,
                    "hwm_at_trigger": 1.0,
                    "time_triggered": "15:54",
                    "symphony_vol": 0.5,
                    "strategy_params": {},
                    "next_day_holdings": [],
                }
            ],
        }
        (pm_dir / f"post_mortem_{d}.json").write_text(json.dumps(payload), encoding="utf-8")
    return pm_dir


_SYMPHONIES = {
    "sym-window-test": {"name": "Window Test Symphony", "account": "acct-window-test"},
}


# ===========================================================================
# 1. DEFAULT_START/DEFAULT_END constants must cover both boundary days
# ===========================================================================


class TestDefaultWindowCoversBothBoundaryDays:
    def test_default_start_covers_2026_06_22(self):
        """DEFAULT_START must be on or before 2026-06-22 so the default window
        includes it. RED today: DEFAULT_START == "2026-06-23" excludes it.
        """
        assert regen_mod.DEFAULT_START <= "2026-06-22", (
            f"DEFAULT_START={regen_mod.DEFAULT_START!r} excludes 2026-06-22 from the "
            "default repair window — the F-008 audit found this day IS contaminated, "
            "contradicting the docstring's 'already matches truth' assumption."
        )

    def test_default_end_covers_2026_07_09(self):
        """DEFAULT_END must be on or after 2026-07-09. RED today:
        DEFAULT_END == "2026-07-08" excludes it.
        """
        assert regen_mod.DEFAULT_END >= "2026-07-09", (
            f"DEFAULT_END={regen_mod.DEFAULT_END!r} excludes 2026-07-09 from the default "
            "repair window — the F-008 audit found this day IS contaminated (pre-fix "
            "sign-flip; DE-GUARD-ALPHA-SAVED-001 did not take effect until 07-10)."
        )


# ===========================================================================
# 2. Functional: explicit caller-requested window is honored, inclusive
#    (regression anchor — the in_window comprehension has no hardcoded skip)
# ===========================================================================


class TestExplicitCallerWindowIsInclusive:
    def test_explicit_start_end_boundary_files_are_processed(self, tmp_path):
        """Passing --start 2026-06-22 --end 2026-07-09 explicitly must process
        BOTH boundary files (proves no hard-coded exclusion inside the window
        filter itself, independent of what the defaults are)."""
        db_path = tmp_path / "test_regen.db"
        _make_db(db_path, _SYMPHONIES)
        pm_dir = _make_pm_dir(tmp_path, ["2026-06-21", "2026-06-22", "2026-07-09", "2026-07-10"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = regen_mod.main(
                [
                    "--db",
                    str(db_path),
                    "--post-mortems-dir",
                    str(pm_dir),
                    "--start",
                    "2026-06-22",
                    "--end",
                    "2026-07-09",
                ]
            )
        output = buf.getvalue()

        assert "post_mortem_2026-06-22.json" in output, (
            f"explicit --start=2026-06-22 must include that boundary file in the dry-run "
            f"report; got output:\n{output}"
        )
        assert "post_mortem_2026-07-09.json" in output, (
            f"explicit --end=2026-07-09 must include that boundary file in the dry-run "
            f"report; got output:\n{output}"
        )
        assert "post_mortem_2026-06-21.json" not in output, (
            "a file BEFORE the requested window must not be processed"
        )
        assert "post_mortem_2026-07-10.json" not in output, (
            "a file AFTER the requested window must not be processed"
        )
        # Dry run (no --apply): return code is 0 (no unresolved) or 2
        # (unresolved present, expected here since shadow_history is empty) —
        # either is a valid non-crash dry-run outcome for this test's purpose.
        assert rc in (0, 2), f"dry run must not crash; got exit code {rc}"

    def test_default_window_processes_both_boundary_files_once_widened(self, tmp_path):
        """End-to-end: with NO --start/--end (using whatever DEFAULT_START/END
        the implementer ships), both boundary files must be processed. This is
        the functional counterpart to TestDefaultWindowCoversBothBoundaryDays —
        RED today because the current defaults (06-23..07-08) exclude both.
        """
        db_path = tmp_path / "test_regen.db"
        _make_db(db_path, _SYMPHONIES)
        pm_dir = _make_pm_dir(
            tmp_path,
            ["2026-06-21", "2026-06-22", "2026-06-25", "2026-07-09", "2026-07-10"],
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            regen_mod.main(["--db", str(db_path), "--post-mortems-dir", str(pm_dir)])
        output = buf.getvalue()

        assert "post_mortem_2026-06-22.json" in output, (
            f"default window (DEFAULT_START={regen_mod.DEFAULT_START!r}) must include "
            f"2026-06-22; got output:\n{output}"
        )
        assert "post_mortem_2026-07-09.json" in output, (
            f"default window (DEFAULT_END={regen_mod.DEFAULT_END!r}) must include "
            f"2026-07-09; got output:\n{output}"
        )
