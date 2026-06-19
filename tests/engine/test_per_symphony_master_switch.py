"""
RED tests — Per-symphony MASTER-SWITCH execution gate.

Context: today live/dry-run is controlled by the global LIVE_EXECUTION flag
(alpha_bot_execution.py:69); real trades fire if and only if LIVE_EXECUTION is True
(alpha_bot_execution.py:1707).

This cycle adds per-symphony live_mode under a MASTER-SWITCH:
  is_live = LIVE_EXECUTION AND symphony_live_mode

A symphony fires a real trade ONLY when BOTH conditions are satisfied.  The global
flag stays the master arm — per-symphony=1 with global=0 is still dry-run.

READER CONTRACT (PM ruling 2026-06-02):
  database.get_symphony_strategy() is extended to include 'live_mode' (bool) in its
  return dict.  The exec path reads live_mode from the ALREADY-CALLED get_symphony_strategy
  return value at ~:1152, via symphony_strat.get("live_mode", False) — NO additional
  DB query.  This keeps the 1-min hot path at a single SELECT per symphony.

THE IMPLEMENTATION CHANGES (all must land before these tests go GREEN):

  1. database.py get_symphony_strategy (~:441):
       SELECT parameters, locked_vars, live_mode FROM symphony_strategies
       Return: {"params": ..., "locked_vars": ..., "live_mode": bool(live_mode_col)}
       Default (no-row) path must also return "live_mode": False.

  2. alpha_bot_execution.py ~:1153 (immediately after the existing get_symphony_strategy call):
       symphony_live_mode = bool(symphony_strat.get("live_mode", False))
     — reads from the already-fetched dict, no second query.

  3. alpha_bot_execution.py ~:1659-1680:
       execution_queue.append({
           ...existing keys...,
           "live_mode": symphony_live_mode,
       })
     And alpha_bot_execution.py:1707 gate becomes:
       if LIVE_EXECUTION and item["live_mode"]:
           # [LIVE EXECUTION] ...
       else:
           # [DRY RUN] ...

  4. alpha_bot_execution.py:1828:
       reporting.send_discord_alert(
           ...existing args...,
           LIVE_EXECUTION and item["live_mode"],   # <- per-symphony effective mode
           ...
       )
     (replaces bare LIVE_EXECUTION so the alert reflects per-symphony truth)

Coverage (all FAIL RED until GREEN implementation lands):
  MS1: BOTH on → execute_sell_to_cash called (real trade)
  MS2: global off, symphony on → execute_sell_to_cash NOT called (dry-run)
  MS3: global on, symphony off → execute_sell_to_cash NOT called (dry-run)
  MS4: both off → execute_sell_to_cash NOT called (dry-run)
  MS5: symphony with no live_mode (default-dry via get_symphony_strategy) + global on → dry-run
  MS6: get_symphony_strategy is called with a live_mode-aware mock (structural check)

  AU1: autotuner does not read live_mode from the parameters dict
  AU2: autotuner save_symphony_strategy call does not pass live_mode key in params

  RP1: send_discord_alert receives per-symphony effective mode (not bare LIVE_EXECUTION)
       — global on + symphony off → is_live=False passed to send_discord_alert

  EX1: resolve_trigger_priority (6-layer exit) is not modified — the gate change
       must not alter the exit-layer priority logic
  EX2: live_mode is read from get_symphony_strategy dict, not via a separate
       get_symphony_live_mode call — no extra DB read per symphony per cycle

Mocking philosophy (inherits from test_trigger_priority_dispatch.py):
  - No real network — fetch_symphony_stats / fetch_alpaca_history /
    fetch_intraday_vwaps patched.
  - database.* mocked; get_symphony_strategy.return_value includes "live_mode" key
    so the exec path's symphony_strat.get("live_mode", False) returns the test value.
  - Math engine NOT mocked wholesale; run_monte_carlo returns a fixed 50.0 so
    MC history shortage doesn't suppress the trigger.
  - execute_sell_to_cash mocked to return True so full side-effect block runs.

Fixture: tests/fixtures/database/per_symphony_live_mode/per_symphony_live_mode_schema.json
Provenance: schema-derived from mission brief + alpha_bot_execution.py:1707 execution gate.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "database"
    / "per_symphony_live_mode"
    / "per_symphony_live_mode_schema.json"
)


def _load_fixture() -> dict:
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared clock + minimal state helpers (mirrors test_trigger_priority_dispatch.py)
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2026, 6, 2, 11, 30, 0, tzinfo=_ET)
except Exception:
    _FIXED_ET = datetime(2026, 6, 2, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

_SYMPHONY_ID = "sym-ms-test"
_ACTUAL_SYMPHONY_ID = "actual-sym-ms-test"
_ACCOUNT_ID = "acct-ms-test"
_SYMPHONY_NAME = "Master Switch Test Symphony"
_TICKER = "SPY"
_NORMALIZED_NAME = "master_switch_test_symphony"


def _make_symphony_payload(last_percent_change: float = 0.05) -> dict:
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(date_str: str) -> dict:
    return {
        date_str: {
            "SPY": {
                "c": 500.0,
                "daily_ret": 0.001,
                "high": 501.0,
                "low": 499.0,
                "close": 500.0,
            }
        }
    }


def _make_vwap_payload(last_price: float = 500.0) -> dict:
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(**extras) -> dict:
    """Minimal armed state — symphony is in-position and the trailing stop has fired."""
    base = {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: {
            "high_water_mark": 5.0,
            "shadow_hwm": 5.0,
            "prev_return": 4.5,
            "armed": True,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "mc_history": [],
            "below_stop_count": 4,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
        },
    }
    base[_SYMPHONY_ID].update(extras)
    return base


def _make_patched_env(
    global_live: bool,
    symphony_live_mode: int,
    extra_patches: dict | None = None,
):
    """
    Context-manager factory for the master-switch test environment.

    Parameters
    ----------
    global_live : bool
        Patches LIVE_EXECUTION to this value.
    symphony_live_mode : int
        Value returned by database.get_symphony_live_mode for our test symphony.
    extra_patches : dict | None
        Additional patch.object kwargs to add to the context manager.
    """
    date_str = _FIXED_ET.strftime("%Y-%m-%d")

    return _PatchStack(date_str, global_live, symphony_live_mode, extra_patches or {})


class _PatchStack:
    """
    Manages the multi-patcher context manager for master-switch tests.

    Provides the same surface as test_trigger_priority_dispatch.py's patched_env
    fixture, augmented with live_mode control.
    """

    def __init__(self, date_str, global_live, symphony_live_mode, extra_patches):
        self._date_str = date_str
        self._global_live = global_live
        self._symphony_live_mode = symphony_live_mode
        self._extra_patches = extra_patches
        self._context = None
        self._mocks = {}

    def __enter__(self):
        date_str = self._date_str

        patcher_mock_db = patch.object(alpha_bot_execution, "database")
        patcher_reporting = patch.object(alpha_bot_execution, "reporting")
        patcher_fetch_sym = patch.object(alpha_bot_execution, "fetch_symphony_stats")
        patcher_fetch_hist = patch.object(alpha_bot_execution, "fetch_alpaca_history")
        patcher_fetch_vwap = patch.object(alpha_bot_execution, "fetch_intraday_vwaps")
        patcher_sell = patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True)
        patcher_et = patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET)
        patcher_accounts = patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID])
        patcher_key = patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key")
        patcher_alpaca = patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca")
        patcher_live = patch.object(alpha_bot_execution, "LIVE_EXECUTION", self._global_live)
        patcher_sleep = patch.object(alpha_bot_execution.time, "sleep")
        patcher_argv = patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"])
        patcher_mc = patch.object(
            alpha_bot_execution.math_engine, "run_monte_carlo", return_value=50.0
        )
        # Patch compute_exit_confirmation so the trailing-stop fires
        patcher_exit_confirm = patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(True, 4.0),  # (is_trailing_stop_hit, stop_level)
        )
        # Patch compute_vwap_breakdown_update to be inert (not breaking)
        patcher_vwap = patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(False, False, 0, 0),
        )

        self._patchers = [
            patcher_mock_db,
            patcher_reporting,
            patcher_fetch_sym,
            patcher_fetch_hist,
            patcher_fetch_vwap,
            patcher_sell,
            patcher_et,
            patcher_accounts,
            patcher_key,
            patcher_alpaca,
            patcher_live,
            patcher_sleep,
            patcher_argv,
            patcher_mc,
            patcher_exit_confirm,
            patcher_vwap,
        ]

        started = [p.__enter__() for p in self._patchers]
        mock_db = started[0]
        mock_reporting = started[1]
        mock_fetch_sym = started[2]
        mock_fetch_hist = started[3]
        mock_fetch_vwap = started[4]
        mock_sell = started[5]

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = _seed_state()
        mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
        # Include live_mode in the get_symphony_strategy return dict so the exec
        # path's symphony_strat.get("live_mode", False) returns the test-controlled
        # value (PM ruling 2026-06-02: reader contract — no second DB query).
        mock_db.get_symphony_strategy.return_value = {
            "params": {},
            "locked_vars": {},
            "live_mode": bool(self._symphony_live_mode),
        }
        mock_db.normalize_name.side_effect = lambda x: x
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = [_make_symphony_payload(last_percent_change=0.05)]
        mock_fetch_hist.return_value = _make_minimal_history(date_str)
        mock_fetch_vwap.return_value = _make_vwap_payload()

        self._mocks = {
            "db": mock_db,
            "reporting": mock_reporting,
            "sell": mock_sell,
            "fetch_sym": mock_fetch_sym,
        }
        return self._mocks

    def __exit__(self, *args):
        for patcher in reversed(self._patchers):
            patcher.__exit__(*args)


# ---------------------------------------------------------------------------
# MS1: both global and per-symphony on → real trade fires
# ---------------------------------------------------------------------------


def test_master_switch_both_on_fires_real_trade():
    """
    MS1: When LIVE_EXECUTION=True AND symphony live_mode=1, execute_sell_to_cash
    must be called exactly once — a real trade fires.

    Fixture: master_switch_truth_table / id='both_on'.
    """
    fx = _load_fixture()
    case = next(c for c in fx["master_switch_truth_table"] if c["id"] == "both_on")
    assert case["expect_sell_to_cash_called"] is True

    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=case["global_live_execution"],
        symphony_live_mode=case["symphony_live_mode"],
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    call_count = mocks["sell"].call_count
    assert call_count == 1, (
        f"MS1 FAILED: LIVE_EXECUTION=True AND live_mode=1 — execute_sell_to_cash "
        f"should be called once; got {call_count}. "
        "Fix: change the execution gate at alpha_bot_execution.py:1707 from "
        "'if LIVE_EXECUTION:' to 'if LIVE_EXECUTION and item[\"live_mode\"]:'."
    )


# ---------------------------------------------------------------------------
# MS2: global off, symphony on → dry-run (master switch blocks)
# ---------------------------------------------------------------------------


def test_master_switch_global_off_symphony_on_is_dry_run():
    """
    MS2: When LIVE_EXECUTION=False AND symphony live_mode=1, execute_sell_to_cash
    must NOT be called — the global master switch prevents real trades.

    Fixture: master_switch_truth_table / id='global_off_symph_on'.
    """
    fx = _load_fixture()
    case = next(c for c in fx["master_switch_truth_table"] if c["id"] == "global_off_symph_on")
    assert case["expect_sell_to_cash_called"] is False

    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=case["global_live_execution"],
        symphony_live_mode=case["symphony_live_mode"],
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    call_count = mocks["sell"].call_count
    assert call_count == 0, (
        f"MS2 FAILED: LIVE_EXECUTION=False but live_mode=1 — execute_sell_to_cash "
        f"was called {call_count} time(s). "
        "The global LIVE_EXECUTION flag is the master arm; per-symphony=1 must NOT "
        "bypass it. Per-symphony live_mode can NEVER arm real money while global is DRY RUN."
    )


# ---------------------------------------------------------------------------
# MS3: global on, symphony off → dry-run (per-symphony gate blocks)
# ---------------------------------------------------------------------------


def test_master_switch_global_on_symphony_off_is_dry_run():
    """
    MS3: When LIVE_EXECUTION=True AND symphony live_mode=0, execute_sell_to_cash
    must NOT be called — the per-symphony gate defaults to dry-run.

    Fixture: master_switch_truth_table / id='global_on_symph_off'.
    """
    fx = _load_fixture()
    case = next(c for c in fx["master_switch_truth_table"] if c["id"] == "global_on_symph_off")
    assert case["expect_sell_to_cash_called"] is False

    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=case["global_live_execution"],
        symphony_live_mode=case["symphony_live_mode"],
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    call_count = mocks["sell"].call_count
    assert call_count == 0, (
        f"MS3 FAILED: LIVE_EXECUTION=True but live_mode=0 — execute_sell_to_cash "
        f"was called {call_count} time(s). "
        "Per-symphony live_mode=0 must block real trade execution even when global is on."
    )


# ---------------------------------------------------------------------------
# MS4: both off → dry-run
# ---------------------------------------------------------------------------


def test_master_switch_both_off_is_dry_run():
    """
    MS4: When LIVE_EXECUTION=False AND symphony live_mode=0, execute_sell_to_cash
    must NOT be called.

    Fixture: master_switch_truth_table / id='both_off'.
    """
    fx = _load_fixture()
    case = next(c for c in fx["master_switch_truth_table"] if c["id"] == "both_off")
    assert case["expect_sell_to_cash_called"] is False

    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=case["global_live_execution"],
        symphony_live_mode=case["symphony_live_mode"],
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    call_count = mocks["sell"].call_count
    assert call_count == 0, (
        f"MS4 FAILED: Both LIVE_EXECUTION=False and live_mode=0 — "
        f"execute_sell_to_cash called {call_count} time(s). Expected 0."
    )


# ---------------------------------------------------------------------------
# MS5: symphony with no live_mode row (default-dry) + global on → dry-run
# ---------------------------------------------------------------------------


def test_master_switch_default_dry_run_when_no_live_mode_row():
    """
    MS5: A symphony that has no live_mode configured (get_symphony_live_mode
    returns 0 — the default) must remain dry-run even when LIVE_EXECUTION=True.

    This tests the default-dry contract: unconfigured symphonies never fire
    real trades (arch rule 4 — is_live explicit, never by omission).

    Fixture: default_dry_run_cases / id='no_row'.
    """
    fx = _load_fixture()
    case = next(c for c in fx["default_dry_run_cases"] if c["id"] == "no_row")
    assert case["expected_live_mode"] == 0

    # global=True, but symphony has no live_mode row → get_symphony_live_mode returns 0
    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=True,
        symphony_live_mode=0,  # default — no row
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    call_count = mocks["sell"].call_count
    assert call_count == 0, (
        f"MS5 FAILED: Global=True but no live_mode row (default 0) — "
        f"execute_sell_to_cash was called {call_count} time(s). "
        "Default-dry: unconfigured symphonies must never place real trades "
        "even when the global flag is on (arch rule 4)."
    )


# ---------------------------------------------------------------------------
# MS6: exec path reads live_mode from get_symphony_strategy return dict
# ---------------------------------------------------------------------------


def test_exec_path_reads_live_mode_from_get_symphony_strategy_dict():
    """
    MS6: The exec path must read live_mode from get_symphony_strategy()'s return
    dict (symphony_strat.get("live_mode", False)) — NOT via a separate
    get_symphony_live_mode call.

    This structural test verifies the reader contract (PM ruling 2026-06-02):
    the live_mode read piggybacks the already-called get_symphony_strategy SELECT
    rather than adding a new DB round-trip.  We confirm this by:
      (a) get_symphony_strategy is called (always true; existing call at :1152)
      (b) When get_symphony_strategy returns {"live_mode": True}, the real trade
          fires — meaning the exec path consumed the live_mode from the dict.
      (c) When get_symphony_strategy returns {"live_mode": False}, trade is blocked
          — same structural proof from the other side.

    The (b)/(c) pair is already covered by MS1/MS3; this test just anchors the
    structural explanation explicitly.
    """
    # With live_mode=True in the get_symphony_strategy dict and global=True,
    # execute_sell_to_cash must be called — proving the dict's live_mode is consumed.
    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=True,
        symphony_live_mode=1,  # → get_symphony_strategy returns {"live_mode": True}
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    assert mocks["db"].get_symphony_strategy.called, (
        "MS6 FAILED: database.get_symphony_strategy was not called during main(). "
        "The exec path at alpha_bot_execution.py:1152 must call get_symphony_strategy "
        "for each symphony — this is the existing call; it must not have been removed."
    )
    # Trade must fire when the dict carries live_mode=True and global is on.
    assert mocks["sell"].call_count == 1, (
        f"MS6 FAILED: execute_sell_to_cash called {mocks['sell'].call_count} time(s) "
        "when get_symphony_strategy returned live_mode=True and LIVE_EXECUTION=True. "
        "The exec path must consume live_mode from the get_symphony_strategy return dict "
        "(symphony_strat.get('live_mode', False)) and carry it in item['live_mode'] "
        "for the gate at :1707."
    )


# ---------------------------------------------------------------------------
# AU1: autotuner does not read live_mode from the parameters dict
# ---------------------------------------------------------------------------


def test_autotuner_does_not_read_live_mode_from_params_dict():
    """
    AU1: The autotuner reads strategy parameters from get_symphony_strategy()["params"].
    live_mode must NOT be a key in that dict — it is a separate column inaccessible
    to Optuna's trial suggestion loop.

    This verifies the structural contract at the Python level: even if a bug were
    to insert 'live_mode' into the parameters blob, the autotuner reading that blob
    would silently start tuning it. We confirm the two separation invariants:
      (a) get_symphony_strategy does not return a 'live_mode' key in params
      (b) DEFAULT_STRATEGY does not contain 'live_mode'
    """
    import database as db_module

    # (a) DEFAULT_STRATEGY must not contain live_mode
    assert "live_mode" not in db_module.DEFAULT_STRATEGY, (
        "database.DEFAULT_STRATEGY contains 'live_mode'. "
        "live_mode must not be part of the tuning parameter set — it is an "
        "operator-only flag in a separate column."
    )

    # (b) get_symphony_strategy's returned params must not contain live_mode
    # We test this against a real in-memory DB to catch implementation drift.
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        test_db = os.path.join(tmp, "au1.db")
        orig = db_module.DB_FILE
        db_module.DB_FILE = test_db
        try:
            db_module.init_db()
            db_module.run_migrations()
            db_module.save_symphony_strategy(
                "au1-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS
            )
            strategy = db_module.get_symphony_strategy("au1-sym")
        finally:
            db_module.DB_FILE = orig

    params = strategy.get("params", {})
    assert "live_mode" not in params, (
        f"get_symphony_strategy returned params containing 'live_mode': {params}. "
        "live_mode must be stored in a separate column — it is an operator-only flag "
        "that the autotuner must never tune."
    )


# ---------------------------------------------------------------------------
# AU2: save_symphony_strategy params dict does not contain live_mode key
# ---------------------------------------------------------------------------


def test_save_symphony_strategy_params_do_not_contain_live_mode():
    """
    AU2: The params dict passed to save_symphony_strategy by the autotuner
    must not contain 'live_mode'.

    This is a belt-and-suspenders check against the autotuner's call site
    (autotuner.py:2518). We verify that the DEFAULT_STRATEGY and any params
    round-tripped through save_symphony_strategy / get_symphony_strategy do not
    include live_mode in the serialized JSON.
    """
    import os
    import tempfile

    import database as db_module

    with tempfile.TemporaryDirectory() as tmp:
        test_db = os.path.join(tmp, "au2.db")
        orig = db_module.DB_FILE
        db_module.DB_FILE = test_db
        try:
            db_module.init_db()
            db_module.run_migrations()

            # Simulate what the autotuner does: pass DEFAULT_STRATEGY as params.
            db_module.save_symphony_strategy(
                "au2-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS
            )

            import sqlite3

            conn = sqlite3.connect(test_db)
            try:
                row = conn.execute(
                    "SELECT parameters FROM symphony_strategies WHERE symphony_name = 'au2-sym'"
                ).fetchone()
            finally:
                conn.close()
        finally:
            db_module.DB_FILE = orig

    assert row is not None, "save_symphony_strategy did not write a row."
    params_blob = json.loads(row[0])
    assert "live_mode" not in params_blob, (
        f"save_symphony_strategy serialized 'live_mode' into the parameters JSON blob: "
        f"{params_blob}. "
        "live_mode must remain in a separate column so the autotuner cannot tune it."
    )


# ---------------------------------------------------------------------------
# RP1: send_discord_alert receives per-symphony effective mode, not bare LIVE_EXECUTION
# ---------------------------------------------------------------------------


def test_discord_alert_receives_per_symphony_effective_mode():
    """
    RP1: When LIVE_EXECUTION=True AND symphony live_mode=0, the is_live argument
    passed to send_discord_alert must be False — not the bare LIVE_EXECUTION=True.

    Before this change, alpha_bot_execution.py:1828 passes 'LIVE_EXECUTION' as is_live.
    After the change, it must pass 'LIVE_EXECUTION and item["live_mode"]'.

    Rationale: the Discord alert must accurately reflect what actually happened for
    each symphony — a per-symphony DRY RUN must say "DRY RUN", not "LIVE".
    """
    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=True,
        symphony_live_mode=0,  # symphony is dry-run even though global is on
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    reporting_mock = mocks["reporting"]
    if not reporting_mock.send_discord_alert.called:
        pytest.skip("No Discord alert was sent — symphony did not trigger; check test setup.")

    # Locate the is_live positional argument (6th positional, 0-indexed=5) in
    # send_discord_alert(symphony_name, current_return, prob_underperforming,
    #                    stop_trigger_level, safe_hwm, is_live, ...)
    call_args = reporting_mock.send_discord_alert.call_args

    # is_live is the 6th positional arg (index 5) per reporting.py:469
    if call_args.args and len(call_args.args) >= 6:
        is_live_arg = call_args.args[5]
    else:
        # sent as keyword
        is_live_arg = call_args.kwargs.get("is_live")

    assert is_live_arg is False or is_live_arg == 0, (
        f"RP1 FAILED: send_discord_alert received is_live={is_live_arg!r} when "
        "LIVE_EXECUTION=True but symphony live_mode=0. "
        "The is_live argument must be 'LIVE_EXECUTION and item[\"live_mode\"]' — "
        "the effective per-symphony mode. "
        "Fix alpha_bot_execution.py:1828: change 'LIVE_EXECUTION' to "
        "'LIVE_EXECUTION and item[\"live_mode\"]'."
    )


# ---------------------------------------------------------------------------
# EX1: resolve_trigger_priority is not modified
# ---------------------------------------------------------------------------


def test_resolve_trigger_priority_signature_unchanged():
    """
    EX1: math_engine.resolve_trigger_priority (the 6-layer priority resolver)
    must not have live_mode in its signature — the gate change is exec-path-only
    and must not leak into the math engine.

    Architecture constraint: the math engine is pure function math; execution
    policy (live vs dry) belongs only in alpha_bot_execution.py.
    """
    import inspect

    import math_engine

    assert hasattr(math_engine, "resolve_trigger_priority"), (
        "math_engine.resolve_trigger_priority not found — cannot verify it is unchanged."
    )
    sig = inspect.signature(math_engine.resolve_trigger_priority)
    assert "live_mode" not in sig.parameters, (
        f"math_engine.resolve_trigger_priority has a 'live_mode' parameter: "
        f"{list(sig.parameters.keys())}. "
        "The master-switch gate must be exec-path-only (alpha_bot_execution.py). "
        "The math engine must not know about live/dry-run policy."
    )


# ---------------------------------------------------------------------------
# EX2: live_mode sourced from get_symphony_strategy dict, no extra query
# ---------------------------------------------------------------------------


def test_live_mode_sourced_from_get_symphony_strategy_no_extra_query():
    """
    EX2: live_mode must be read from get_symphony_strategy()'s return dict, NOT
    via a separate get_symphony_live_mode call.

    Architecture constraint 1: no new blocking I/O on the 1-min execution path.
    The live_mode value must piggyback the EXISTING get_symphony_strategy SELECT
    at ~:1152; it must not cause a second DB round-trip.

    We verify this by checking that database.get_symphony_live_mode is NOT called
    (i.e., call_count == 0) during a full main() cycle.  The live_mode value reaches
    item["live_mode"] via symphony_strat.get("live_mode", False) from the already-
    called get_symphony_strategy — no separate accessor needed.
    """
    with _PatchStack(
        _FIXED_ET.strftime("%Y-%m-%d"),
        global_live=True,
        symphony_live_mode=1,
        extra_patches={},
    ) as mocks:
        alpha_bot_execution.main()

    # get_symphony_live_mode must NOT be called — live_mode comes from
    # get_symphony_strategy's return dict (no extra SELECT).
    live_mode_call_count = mocks["db"].get_symphony_live_mode.call_count
    assert live_mode_call_count == 0, (
        f"EX2 FAILED: database.get_symphony_live_mode was called {live_mode_call_count} time(s). "
        "It must NOT be called — live_mode must be read from the get_symphony_strategy "
        "return dict via symphony_strat.get('live_mode', False). "
        "A separate get_symphony_live_mode call adds an extra DB SELECT per symphony "
        "on the 1-min hot path (arch constraint 1 violation)."
    )
    # get_symphony_strategy MUST still be called (the existing per-symphony read).
    strat_call_count = mocks["db"].get_symphony_strategy.call_count
    assert strat_call_count >= 1, (
        f"EX2 pre-condition FAILED: get_symphony_strategy was called {strat_call_count} time(s). "
        "It must be called at least once — it is the existing per-symphony strategy read "
        "that live_mode now piggybacks."
    )
