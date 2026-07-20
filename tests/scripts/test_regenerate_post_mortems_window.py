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
import os
import sqlite3
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

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


# ===========================================================================
# F-008 completion (stamp regen): verified-unchanged entries get stamped
# ===========================================================================
#
# NOTE: the classes below cover feature-plans/fix-f008-regen-stamp.md
# (AC-1..AC-7) — a SEPARATE, later F-008 sub-feature from the date-window
# fix above (PR #104). Both share the "F-008" label; class names below are
# prefixed distinctly from TestDefaultWindowCoversBothBoundaryDays /
# TestExplicitCallerWindowIsInclusive above to avoid confusion.
#
# THE BUG (this sub-feature): regenerate_file()'s `if old != new:` block
# (scripts/regenerate_post_mortems.py:190) gates BOTH the value update AND
# the if_held_source stamp AND the changes.append — so an entry the script
# RESOLVES to a shadow_history row and CONFIRMS byte-equal is left with its
# original (missing/untrusted) stamp. The read-time guard
# (analytics.is_valid_post_mortem_entry / _TRUSTED_IF_HELD_SOURCES) then
# excludes that verified-correct entry from every live consumer. Production
# (2026-07-20 repair run): post_mortem_2026-06-22.json, 1 of 11 entries
# verified-correct but unstamped = $+28.72 missing from the live headline.
#
# IMPLEMENTER NOTE (handoff): the trusted-set membership check inside the
# fix must mirror analytics._TRUSTED_IF_HELD_SOURCES exactly
# ({"shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"}).
# Prefer importing it directly; if you mirror it as a local constant instead,
# the tests below (esp. TestTrustedStampEqualValuesUntouched) only exercise
# 2 of the 3 trusted values (shadow_history, bot_state_fallback) — add your
# own drift guard (e.g. assert the two sets are equal) if you copy rather
# than import, since a silently-diverged copy would NOT be caught by these
# fixtures alone.


def _make_db_with_shadow(
    db_path: Path,
    symphonies: dict[str, dict[str, str]],
    shadow_rows: list[tuple[str, str, str, str, str, float]],
) -> None:
    """bot_state + shadow_history seeded with rows true_if_held() can resolve.

    shadow_rows: (symphony_id, account_id, trading_day, ts_utc, ts_et,
    current_return). account_id is unused by the true_if_held query (it
    keys on symphony_id + trading_day only) but included for fixture
    readability.
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
        conn.executemany(
            "INSERT INTO shadow_history "
            "(symphony_id, account_id, trading_day, ts_utc, ts_et, current_return) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            shadow_rows,
        )
        conn.commit()
    finally:
        conn.close()


def _write_pm_file(
    pm_dir: Path,
    date: str,
    *,
    symphony_name: str,
    account_id: str,
    symphony_value: float = 1000.0,
    exit_return: float = 1.0,
    shadow_return: float = 0.2,
    saved_pct_guard_alpha: float = 0.8,
    saved_dollars: float = 8.0,
    if_held_source: str | None = None,
) -> Path:
    """One post-mortem file with a single controllable trigger entry.

    Default values (exit_return=1.0, shadow_return=0.2, saved_pct=0.8,
    saved_dollars=8.0, symphony_value=1000.0) are internally consistent with
    a shadow_history current_return=0.2 row: saved_pct = 1.0 - 0.2 = 0.8,
    saved_dollars = 1000 * 0.8 / 100 = 8.0 — i.e. the "already correct,
    stamp-only" case out of the box when if_held_source is None/untrusted.
    Pass different values to construct a genuine value-change case instead.
    """
    entry = {
        "symphony_name": symphony_name,
        "symphony_value": symphony_value,
        "account_id": account_id,
        "exit_reason": "Trailing Stop",
        "exit_return": exit_return,
        "attempted_trigger_level": 0.5,
        "shadow_return": shadow_return,
        "shadow_hwm": 1.0,
        "saved_pct_guard_alpha": saved_pct_guard_alpha,
        "saved_dollars": saved_dollars,
        "hwm_at_trigger": 1.0,
        "time_triggered": "15:54",
        "symphony_vol": 0.5,
        "strategy_params": {},
        "next_day_holdings": [],
    }
    if if_held_source is not None:
        entry["if_held_source"] = if_held_source
    payload = {
        "date": date,
        "summary": {
            "total_monitored": 1,
            "total_triggered": 1,
            "positive_guard_alpha_count": 0,
        },
        "tomorrow_target_holdings": {},
        "triggers": [entry],
    }
    path = pm_dir / f"post_mortem_{date}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestStampOnlyEntryGetsStamped:
    """AC-1: a resolved+confirmed-equal entry with a missing/untrusted stamp
    gets stamped `shadow_history`, and the stamp addition counts as a change."""

    def test_unstamped_but_correct_entry_gets_stamped_and_counted_as_change(self, tmp_path):
        sym_id, account, day = "sym-ac1-1", "acct-ac1-1", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC1 Test Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC1 Test Symphony",
            account_id=account,
            if_held_source=None,
        )

        name_map = regen_mod.load_name_account_map(str(db_path))
        new_report, changes, unresolved = regen_mod.regenerate_file(
            str(path), str(db_path), name_map
        )

        assert unresolved == [], f"fixture must resolve cleanly; got {unresolved}"
        assert new_report["triggers"][0].get("if_held_source") == "shadow_history", (
            "a resolved entry confirmed byte-equal to shadow_history truth must be "
            "stamped, even though its VALUES did not change"
        )
        assert len(changes) == 1, (
            "the stamp addition must count as a change so the file gets rewritten "
            "on --apply — the exact production gap: an unstamped-but-correct entry "
            f"silently stays unstamped forever; got changes={changes}"
        )
        assert "regenerated" in new_report, (
            "a stamp-only change must still trip the 'regenerated' marker "
            "(gates the --apply rewrite)"
        )

    def test_unrecognized_stamp_string_on_equal_values_gets_restamped(self, tmp_path):
        sym_id, account, day = "sym-ac1-2", "acct-ac1-2", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC1 Bogus Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC1 Bogus Symphony",
            account_id=account,
            if_held_source="bogus",
        )

        name_map = regen_mod.load_name_account_map(str(db_path))
        new_report, changes, unresolved = regen_mod.regenerate_file(
            str(path), str(db_path), name_map
        )

        assert unresolved == []
        assert new_report["triggers"][0]["if_held_source"] == "shadow_history", (
            "an unrecognized stamp string on a value just verified against "
            "shadow_history truth must be corrected to the real provenance"
        )
        assert len(changes) == 1

    def test_main_report_does_not_reuse_the_degenerate_identical_value_line(self, tmp_path):
        """A stamp-only change must produce SOME per-entry report line (not
        silence — RED today, since nothing prints while changes stays empty)
        and must NOT reuse the existing generic old->new line verbatim, since
        old==new there — printing 'if-held 0.2 -> 0.2, saved $+8.00 -> $+8.00'
        reads as a no-op and hides that a repair happened (AC-1's "distinct
        stamp-only line"). The exact replacement wording is the implementer's
        call — this only forbids literally reusing the identical-value arrow
        line while also requiring that a line be printed at all.
        """
        sym_id, account, day = "sym-ac1-3", "acct-ac1-3", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC1 Report Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC1 Report Symphony",
            account_id=account,
            if_held_source=None,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            regen_mod.main(
                [
                    "--db",
                    str(db_path),
                    "--post-mortems-dir",
                    str(pm_dir),
                    "--start",
                    day,
                    "--end",
                    day,
                ]
            )
        output = buf.getvalue()

        assert "AC1 Report Symphony" in output, (
            "a stamp-only change must produce a per-entry report line so operators "
            f"can see what was verified/stamped, not silence; got output:\n{output}"
        )
        degenerate_line = "AC1 Report Symphony: if-held 0.2 -> 0.2, saved $+8.00 -> $+8.00"
        assert degenerate_line not in output, (
            "a stamp-only change must not print as an identical-value 'if-held "
            f"X -> X' line (reads as a no-op); got output:\n{output}"
        )


class TestTrustedStampEqualValuesUntouched:
    """AC-2: a trusted stamp with equal values is not modified — no re-stamp,
    no rewrite churn from such entries alone. Regression anchor — this
    behavior already holds today (old==new skips the block unconditionally
    on stamp value) and must continue to hold once the elif is added."""

    def test_trusted_non_shadow_history_stamp_with_equal_values_not_modified(self, tmp_path):
        sym_id, account, day = "sym-ac2-1", "acct-ac2-1", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC2 Trusted Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC2 Trusted Symphony",
            account_id=account,
            if_held_source="bot_state_fallback",
        )
        before = json.loads(path.read_text(encoding="utf-8"))

        name_map = regen_mod.load_name_account_map(str(db_path))
        new_report, changes, unresolved = regen_mod.regenerate_file(
            str(path), str(db_path), name_map
        )

        assert unresolved == []
        assert changes == [], (
            "a value-verified entry already carrying a trusted non-shadow_history "
            f"stamp must not be churned; got changes={changes}"
        )
        assert "regenerated" not in new_report
        assert new_report["triggers"][0] == before["triggers"][0], (
            "the entry must be byte-identical — no re-stamp of already-trusted provenance"
        )

    def test_already_shadow_history_stamped_equal_values_no_phantom_change(self, tmp_path):
        sym_id, account, day = "sym-ac2-2", "acct-ac2-2", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC2 Already Stamped Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC2 Already Stamped Symphony",
            account_id=account,
            if_held_source="shadow_history",
        )

        name_map = regen_mod.load_name_account_map(str(db_path))
        _, changes, unresolved = regen_mod.regenerate_file(str(path), str(db_path), name_map)

        assert unresolved == []
        assert changes == [], (
            "an entry already correctly stamped shadow_history with equal values "
            f"must not generate a phantom change record; got {changes}"
        )


class TestValueChangedRegression:
    """AC-3: value-changed entries behave exactly as today. Regression
    anchor guarding that the elif refactor doesn't touch the existing
    if-branch — already green today, must stay green."""

    def test_value_changed_entry_still_updates_stamps_and_reports_old_new(self, tmp_path):
        sym_id, account, day = "sym-ac3-1", "acct-ac3-1", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC3 Changed Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.5)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        # Stored values (shadow_return=0.2, saved_pct=0.8, saved_dollars=8.0,
        # the fixture defaults) disagree with shadow_history truth
        # (current_return=0.5) — a genuine value-change case, independent of
        # if_held_source (set to the trusted value here to isolate the
        # value-change path from AC-1's stamp-only path).
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC3 Changed Symphony",
            account_id=account,
            if_held_source="shadow_history",
        )

        name_map = regen_mod.load_name_account_map(str(db_path))
        new_report, changes, unresolved = regen_mod.regenerate_file(
            str(path), str(db_path), name_map
        )

        assert unresolved == []
        entry = new_report["triggers"][0]
        # abs=1e-9: these are round(x, 2) outputs of deterministic literal
        # arithmetic (1.0 - 0.5, 1000 * 0.5 / 100) — the tolerance guards
        # only binary-float representation noise, not real precision loss.
        assert entry["shadow_return"] == pytest.approx(0.5, abs=1e-9)
        assert entry["saved_pct_guard_alpha"] == pytest.approx(0.5, abs=1e-9)  # 1.0 - 0.5
        assert entry["saved_dollars"] == pytest.approx(5.0, abs=1e-9)  # 1000 * 0.5 / 100
        assert entry["if_held_source"] == "shadow_history"
        assert len(changes) == 1
        assert changes[0]["old"] == pytest.approx(
            {"shadow_return": 0.2, "saved_pct_guard_alpha": 0.8, "saved_dollars": 8.0},
            abs=1e-9,
        )
        assert changes[0]["new"] == pytest.approx(
            {"shadow_return": 0.5, "saved_pct_guard_alpha": 0.5, "saved_dollars": 5.0},
            abs=1e-9,
        )


class TestStampOnlyIdempotency:
    """AC-4: a second run immediately after a stamp-only --apply reports
    zero changes and rewrites nothing."""

    def test_second_run_after_stamp_only_apply_reports_zero_changes(self, tmp_path):
        sym_id, account, day = "sym-ac4-1", "acct-ac4-1", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC4 Idempotency Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC4 Idempotency Symphony",
            account_id=account,
            if_held_source=None,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = regen_mod.main(
                [
                    "--db",
                    str(db_path),
                    "--post-mortems-dir",
                    str(pm_dir),
                    "--start",
                    day,
                    "--end",
                    day,
                    "--apply",
                ]
            )
        assert rc == 0, (
            f"apply must succeed on a fully-resolvable window; output:\n{buf.getvalue()}"
        )

        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["triggers"][0].get("if_held_source") == "shadow_history", (
            "first --apply must stamp the verified-equal entry to disk"
        )

        name_map = regen_mod.load_name_account_map(str(db_path))
        _, changes_2, unresolved_2 = regen_mod.regenerate_file(str(path), str(db_path), name_map)

        assert unresolved_2 == []
        assert changes_2 == [], (
            "a second run over an already-stamped, value-correct entry must report "
            f"zero changes (idempotent); got {changes_2}"
        )


class TestAllOrNothingRefusalUnchanged:
    """AC-5: --apply still refuses if ANY in-window entry is unresolved.
    Regression anchor — byte-unchanged behavior through the refactor,
    already green today."""

    def test_apply_refuses_when_any_entry_unresolved(self, tmp_path):
        day = "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        # Empty symphonies map -> name_map has no entry for the file's
        # (symphony_name, account_id) -> unresolved.
        _make_db_with_shadow(db_path, {}, [])
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC5 Unresolved Symphony",
            account_id="acct-ac5-1",
        )
        before_bytes = path.read_bytes()

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = regen_mod.main(
                [
                    "--db",
                    str(db_path),
                    "--post-mortems-dir",
                    str(pm_dir),
                    "--start",
                    day,
                    "--end",
                    day,
                    "--apply",
                ]
            )

        assert rc == 2, "an unresolved entry must refuse --apply (exit code 2)"
        assert path.read_bytes() == before_bytes, (
            "REFUSING --apply must write nothing — all-or-nothing money repair"
        )


class TestDryRunStampOnly:
    """AC-6: dry-run prints pending stamp-only changes without writing
    anything — dry-run-default is preserved."""

    def test_dry_run_prints_stamp_only_pending_without_writing(self, tmp_path):
        sym_id, account, day = "sym-ac6-1", "acct-ac6-1", "2026-06-24"
        db_path = tmp_path / "test_regen.db"
        _make_db_with_shadow(
            db_path,
            {sym_id: {"name": "AC6 Dry Run Symphony", "account": account}},
            [(sym_id, account, day, f"{day}T15:50:00", f"{day}T15:50:00", 0.2)],
        )
        pm_dir = tmp_path / "post_mortems"
        pm_dir.mkdir()
        path = _write_pm_file(
            pm_dir,
            day,
            symphony_name="AC6 Dry Run Symphony",
            account_id=account,
            if_held_source=None,
        )
        before_bytes = path.read_bytes()

        buf = io.StringIO()
        with redirect_stdout(buf):
            regen_mod.main(
                [
                    "--db",
                    str(db_path),
                    "--post-mortems-dir",
                    str(pm_dir),
                    "--start",
                    day,
                    "--end",
                    day,
                ]
            )
        output = buf.getvalue()

        # "(1 of 1 entries)" is the EXISTING generic per-file summary format
        # (pre-existing code, not new implementer wording) — legitimate to
        # assert literally.
        assert "(1 of 1 entries)" in output, (
            "dry run must report the pending stamp-only entry as a change in the "
            f"per-file summary count; got output:\n{output}"
        )
        assert "DRY RUN" in output
        assert path.read_bytes() == before_bytes, "dry run must write nothing"


# ===========================================================================
# F-008 completion follow-up: documented invocation must resolve imports
# path-independently (DE-F008-REGEN-STAMP-001 addendum)
# ===========================================================================
#
# THE BUG: scripts/regenerate_post_mortems.py:67 does a bare `import
# analytics` at module scope. Python's script-mode sys.path[0] is the
# DIRECTORY CONTAINING THE SCRIPT (scripts/), not the repo root — so the
# documented invocation (`python scripts/regenerate_post_mortems.py`, see
# the module docstring's USAGE block) raises ModuleNotFoundError: No module
# named 'analytics' the moment it's run outside a context that happens to
# already have the repo root on sys.path.
#
# WHY EVERY EXISTING TEST IN THIS FILE MISSED IT: pytest always puts the
# repo root on sys.path for collection, regardless of invocation form — so
# `import scripts.regenerate_post_mortems as regen_mod` (used by every other
# test above) can never reproduce this. Only a real subprocess invocation,
# the way an operator or a droplet cron/systemd unit would actually run it,
# exercises the real failure mode.
#
# PRODUCTION SYMPTOM (droplet, 2026-07-20): `python
# scripts/regenerate_post_mortems.py` -> ModuleNotFoundError. The PM's
# manual PYTHONPATH export masked it during the live data repair; the
# DOCUMENTED command in the module's own USAGE block does not work as
# written.


class TestScriptInvocationResolvesImportsPathIndependently:
    """The documented invocation must work from a NEUTRAL cwd with no
    PYTHONPATH pre-set — exactly how an operator or a cron/systemd unit
    would run it, and exactly how the droplet failure reproduced."""

    def test_direct_invocation_from_neutral_cwd_with_no_pythonpath(self, tmp_path):
        script_path = Path(regen_mod.__file__).resolve()

        # Strip PYTHONPATH explicitly so this test can't pass by accident
        # because the dev/CI shell happens to already have the repo root on
        # the path — the real failure mode is a BARE invocation with none of
        # that scaffolding, which is what an operator or systemd unit gets.
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        assert result.returncode == 0, (
            "the documented invocation (module docstring USAGE block) must work "
            "from a neutral cwd with no PYTHONPATH — this is the exact production "
            "failure shape (ModuleNotFoundError: No module named 'analytics'); "
            f"got rc={result.returncode}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        assert "usage" in result.stdout.lower(), (
            f"--help must print argparse usage on stdout once imports resolve; "
            f"got stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
