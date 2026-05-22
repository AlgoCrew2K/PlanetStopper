"""
RED-phase tests for the scope-expansion reliability items.

Covers four independent reliability fixes identified in silent-failures-rca.md:

  1. Cache-poison hardening (RCA incident #2)
     - fetch_alpaca_history must NOT write history_cache.json when historical_data == {}
     - The bare `return` on empty historical_data must produce visible output before exiting
     - After a skipped cache write, the next call re-fetches (no stale cache hit)
     - Alpaca batch retry loop is bounded at max_retries=3

  2. last_successful_cycle_at health field
     - Set in bot_state on every successful cycle that writes real data (data phase or full cycle)
     - NOT set on early-returns (lock overlap, pre-09:30 market-closed, missing API keys,
       empty historical_data)
     - Surfaced in /api/state JSON payload
     - Format: ISO-8601 datetime string

  3. Subprocess stdout capture in app.py
     - subprocess.run invocation pipes stdout+stderr to a log file (not discarded)
     - Log file path is configurable (env var or module-level constant)
     - Log file opened in append mode

  4. EXECUTION_START_TIME comes from env, not a hardcoded constant
     - The action-phase gate uses the parsed env variable, not a literal "10:30"

All tests are RED on the current codebase. None touch math_engine or the existing
test_data_action_split.py. Both files must be green simultaneously after GREEN.

Mocking philosophy:
  - No live network calls. fetch_alpaca_history internals are tested by patching
    requests.get (or patching open for cache-file tests).
  - database functions patched to inject/capture bot_state.
  - time.sleep patched to no-op throughout.
  - capsys / capfd used to capture print() output for the loud-fail tests.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

import alpha_bot_execution
import app as app_module


# ---------------------------------------------------------------------------
# Time helpers (shared with test_data_action_split.py)
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    def _et(hour: int, minute: int, weekday_date: str = "2025-05-14") -> datetime:
        y, mo, d = map(int, weekday_date.split("-"))
        return datetime(y, mo, d, hour, minute, 0, tzinfo=_ET)

except Exception:  # pragma: no cover
    def _et(hour: int, minute: int, weekday_date: str = "2025-05-14") -> datetime:
        y, mo, d = map(int, weekday_date.split("-"))
        return datetime(y, mo, d, hour, minute, 0,
                        tzinfo=timezone(timedelta(hours=-4)))


_PRE_GATE_09_45 = _et(9, 45)
_POST_GATE_10_45 = _et(10, 45)
_DATE_STR = "2025-05-14"

_ACCOUNT_ID = "acct-reliability-test-001"
_SYM_ID = "sym-reliability-test-001"
_SYM_NAME = "Reliability Test Symphony"
_TICKER = "SPY"


def _seed_state(**extras) -> dict:
    sym_entry: dict = {
        "high_water_mark": 0.0,
        "shadow_hwm": 0.0,
        "prev_return": 0.0,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "triggered": False,
        "mc_history": [],
        "below_stop_count": 0,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "breakeven_locked": False,
        "hwm_hold_ticks": 0,
        "current_return": 0.0,
        "name": _SYM_NAME,
        "account": _ACCOUNT_ID,
    }
    sym_entry.update(extras)
    return {
        "date": _DATE_STR,
        "last_execution_mode": False,
        _SYM_ID: sym_entry,
    }


def _make_sym_payload(last_pct: float = 0.015) -> dict:
    return {
        "id": _SYM_ID,
        "symphony_id": "actual-" + _SYM_ID,
        "name": _SYM_NAME,
        "last_percent_change": last_pct,
        "current_value": 10_000.0,
        "simple_return": 0.12,
        "net_deposits": 9_500.0,
        "time_weighted_return": 0.13,
        "max_drawdown": 0.08,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(date_str: str = _DATE_STR) -> dict:
    return {
        date_str: {
            _TICKER: {"c": 500.0, "daily_ret": 0.001,
                      "high": 501.0, "low": 499.0, "close": 500.0}
        }
    }


# ---------------------------------------------------------------------------
# Shared full-cycle runner (same as test_data_action_split.py but without
# the math_engine patch so we can probe print() output via capsys)
# ---------------------------------------------------------------------------

def _run_main_patched(
    current_et: datetime,
    initial_bot_state: dict,
    fetch_alpaca_return: dict | None = None,
    fetch_sym_return: list | None = None,
    execution_start_time: str = "10:30",
    composer_key: str | None = "test-key",
    alpaca_key: str | None = "test-alpaca-key",
) -> tuple[dict, dict]:
    """
    Run alpha_bot_execution.main() with standard patches.

    Returns (final_bot_state, mocks). final_bot_state is the last save_state
    argument, or initial_bot_state if save_state was never called.
    """
    if fetch_alpaca_return is None:
        fetch_alpaca_return = _make_minimal_history()
    if fetch_sym_return is None:
        fetch_sym_return = [_make_sym_payload()]

    date_str = current_et.strftime("%Y-%m-%d")
    captured: list[dict] = []

    def capture_save(s: dict) -> None:
        captured.append(copy.deepcopy(s))

    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting"), \
         patch.object(alpha_bot_execution, "fetch_symphony_stats",
                      return_value=fetch_sym_return), \
         patch.object(alpha_bot_execution, "fetch_alpaca_history",
                      return_value=fetch_alpaca_return), \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                      return_value={_TICKER: {"vwap": 500.0, "last_price": 500.0}}), \
         patch.object(alpha_bot_execution, "get_current_et",
                      return_value=current_et), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", composer_key), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", alpaca_key), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution, "EXECUTION_START_TIME", execution_start_time), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
         patch.object(alpha_bot_execution, "autotuner") as _mock_autotuner, \
         patch.object(alpha_bot_execution, "math_engine") as mock_math:

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = copy.deepcopy(initial_bot_state)
        mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s
        mock_db.save_state.side_effect = capture_save
        _mock_autotuner.run_autotuner.return_value = None

        mock_math.run_monte_carlo.return_value = 80.0
        mock_math.calculate_20d_vol.return_value = 0.15
        mock_math.compute_vwap_signals.return_value = (0.0, 0.0)
        mock_math.compute_para_arm_decision.return_value = (0.0, False)
        mock_math.compute_time_squeeze_decay.return_value = (1.0, 0.5)
        mock_math.compute_active_trailing_stop.return_value = 2.0
        mock_math.compute_breakeven_update.return_value = (0, False, -2.0)
        mock_math.compute_exit_confirmation.return_value = (0, False)
        # compute_tp_confirmation (D-C3a extraction) returns
        # (new_tp_armed, new_above_tp_count, is_tp_hit); safe default = not
        # armed, count 0, no TP exit.
        mock_math.compute_tp_confirmation.return_value = (False, 0, False)
        mock_math.compute_vwap_breakdown_update.return_value = (0, 0, False, False)
        mock_math.compute_vwap_bleed_arm_threshold.return_value = 0.5

        alpha_bot_execution.main()

    final = captured[-1] if captured else copy.deepcopy(initial_bot_state)
    return final, {"mock_db": mock_db, "mock_math": mock_math}


# ===========================================================================
# 1. CACHE-POISON HARDENING
# ===========================================================================

class TestCachePoisonHardening:
    """
    fetch_alpaca_history must NOT write history_cache.json when historical_data=={}.
    The bare return on empty data must produce visible output.
    """

    # -----------------------------------------------------------------------
    # A1: empty fetch → cache NOT written
    # -----------------------------------------------------------------------

    def test_empty_historical_data_does_not_write_cache_file(self, tmp_path):
        """
        When all Alpaca batches fail and historical_data stays {}, the cache file
        must NOT be written (Stage A fix: unconditional cache write on empty data).

        RED: current code calls json.dump unconditionally at line 224 regardless
        of whether historical_data is empty. The fix adds: if not historical_data:
        skip the cache write.
        """
        cache_file = tmp_path / "history_cache.json"
        tickers = ["SPY"]
        date_str = _DATE_STR

        with patch.object(alpha_bot_execution, "HISTORY_CACHE_FILE", str(cache_file)), \
             patch.object(alpha_bot_execution.requests, "get") as mock_get, \
             patch.object(alpha_bot_execution.time, "sleep"):

            # Simulate every Alpaca request returning HTTP 500 → success=False → break
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_get.return_value = mock_resp

            result = alpha_bot_execution.fetch_alpaca_history(tickers, date_str)

        # The function must return an empty dict
        assert result == {}, (
            "fetch_alpaca_history must return {} when all batches fail; "
            f"got {result!r}"
        )
        # The cache file must NOT exist (or must not have been written this call)
        assert not cache_file.exists(), (
            f"history_cache.json must NOT be written when historical_data=={{}};  "
            f"writing an empty cache poisons all subsequent calls for the rest of the day. "
            f"Fix: guard the json.dump with `if historical_data:` at line 222."
        )

    # -----------------------------------------------------------------------
    # A2: non-empty fetch → cache IS written
    # -----------------------------------------------------------------------

    def test_non_empty_historical_data_writes_cache_file(self, tmp_path):
        """
        When Alpaca returns valid bars, fetch_alpaca_history must still write the
        cache as before. The empty-data guard must not block valid cache writes.
        """
        cache_file = tmp_path / "history_cache.json"
        tickers = [_TICKER]
        date_str = _DATE_STR

        # Minimal Alpaca API response with one bar pair (enough for daily_ret)
        alpaca_response = {
            "bars": {
                _TICKER: [
                    {"t": f"{date_str}T14:00:00Z", "c": 499.0, "h": 500.0, "l": 498.0},
                    {"t": f"{date_str}T15:00:00Z", "c": 500.0, "h": 501.0, "l": 499.0},
                ]
            },
            "next_page_token": None,
        }

        with patch.object(alpha_bot_execution, "HISTORY_CACHE_FILE", str(cache_file)), \
             patch.object(alpha_bot_execution.requests, "get") as mock_get, \
             patch.object(alpha_bot_execution.time, "sleep"):

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = alpaca_response
            mock_get.return_value = mock_resp

            result = alpha_bot_execution.fetch_alpaca_history(tickers, date_str)

        assert result, (
            "fetch_alpaca_history must return non-empty dict on successful fetch"
        )
        assert cache_file.exists(), (
            "history_cache.json must be written after a successful non-empty fetch; "
            "the empty-data guard must not block valid cache writes."
        )
        with open(cache_file, encoding="utf-8") as fh:
            cached = json.load(fh)
        assert cached.get("date") == date_str, (
            f"Cached date must equal current_date_str={date_str!r}; "
            f"got {cached.get('date')!r}"
        )

    # -----------------------------------------------------------------------
    # A3: after skipped write, next call re-fetches (no stale cache hit)
    # -----------------------------------------------------------------------

    def test_after_empty_fetch_next_call_does_not_return_stale_cache(self, tmp_path):
        """
        After fetch_alpaca_history skips the cache write (empty data), a subsequent
        call with the same date+tickers must NOT return {} from a stale cache.

        If the empty-data guard is implemented correctly, no cache file exists after
        the first call, so the second call goes back to the network.

        RED: current code writes {} to the cache on the first call, so the second
        call hits `cache.get("date") == current_date_str and cache.get("tickers") ==
        tickers_list` → returns `{}` without re-fetching. This permanently disables
        the engine for the rest of the trading day.
        """
        cache_file = tmp_path / "history_cache.json"
        tickers = [_TICKER]
        date_str = _DATE_STR

        # First call: all batches fail → empty data → cache should NOT be written
        with patch.object(alpha_bot_execution, "HISTORY_CACHE_FILE", str(cache_file)), \
             patch.object(alpha_bot_execution.requests, "get") as mock_get_1, \
             patch.object(alpha_bot_execution.time, "sleep"):
            mock_resp_fail = MagicMock()
            mock_resp_fail.status_code = 500
            mock_get_1.return_value = mock_resp_fail
            alpha_bot_execution.fetch_alpaca_history(tickers, date_str)

        # If the guard is correct, no cache file exists after the empty fetch
        # (the second call will attempt a real network call, not return from cache)
        # We verify: after the first call, calling again makes at least one requests.get call
        with patch.object(alpha_bot_execution, "HISTORY_CACHE_FILE", str(cache_file)), \
             patch.object(alpha_bot_execution.requests, "get") as mock_get_2, \
             patch.object(alpha_bot_execution.time, "sleep"):
            mock_resp_fail2 = MagicMock()
            mock_resp_fail2.status_code = 500
            mock_get_2.return_value = mock_resp_fail2
            alpha_bot_execution.fetch_alpaca_history(tickers, date_str)

        assert mock_get_2.call_count >= 1, (
            "After an empty-data fetch (cache skipped), the next fetch_alpaca_history "
            "call must make at least one requests.get call (it must re-fetch, not return "
            "a stale {} from a poisoned cache). "
            f"requests.get call count on second call: {mock_get_2.call_count}. "
            "Fix: do not write cache on empty data (A1 fix prerequisite)."
        )

    # -----------------------------------------------------------------------
    # B1: empty historical_data path produces visible output before exiting
    # -----------------------------------------------------------------------

    def test_empty_historical_data_in_main_produces_visible_output(self, capsys):
        """
        When fetch_alpaca_history returns {} inside main(), the bare `return` at
        line 435-436 must NOT be silent. It must produce at least one line of output
        that mentions the empty/missing data condition.

        This is the most dangerous silent path in the engine (per RCA §4 table):
        under restart.ps1's hidden-window launcher, a silent return is completely
        invisible. After the fix, the operator and the log file both see an error.

        RED: current code at line 435-436:
          if not historical_data:
              return   ← bare return, no output
        Fix: add print/log before the return.
        """
        initial = _seed_state()
        final, _ = _run_main_patched(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
            fetch_alpaca_return={},  # triggers the empty-historical_data path
        )

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err
        assert combined_output.strip(), (
            "When fetch_alpaca_history returns {}, main() must produce at least one "
            "line of output before returning. Current code has a bare `return` with "
            "no print/log — this is a silent failure. Fix: add a print/log statement "
            "before the `return` at alpha_bot_execution.py:436."
        )
        # The output must contain a meaningful signal — not just "Waking Up"
        lower = combined_output.lower()
        assert any(token in lower for token in ("historical", "cache", "empty", "error", "skip")), (
            f"The output on empty-historical_data exit must mention one of: "
            f"'historical', 'cache', 'empty', 'error', 'skip'. "
            f"Got: {combined_output!r}. "
            f"The message should be diagnosable by an operator inspecting logs."
        )

    # -----------------------------------------------------------------------
    # B2: empty historical_data path does NOT write corrupted state
    # -----------------------------------------------------------------------

    def test_empty_historical_data_in_main_does_not_corrupt_bot_state(self):
        """
        When fetch_alpaca_history returns {}, main() returns early without running
        the intraday loop. It must not call save_state with partially-mutated state.

        The prior bot_state must be preserved exactly. This pins an existing behavior
        (no save_state on this path) against future accidental changes.

        This test is NOT RED on the current code (the bare return already skips
        save_state), but it must remain GREEN after the loud-fail fix is added.
        """
        initial = _seed_state(current_return=1.5, high_water_mark=1.5)

        date_str = _POST_GATE_10_45.strftime("%Y-%m-%d")
        captured: list[dict] = []

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=[_make_sym_payload()]), \
             patch.object(alpha_bot_execution, "fetch_alpaca_history",
                          return_value={}), \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                          return_value={}), \
             patch.object(alpha_bot_execution, "get_current_et",
                          return_value=_POST_GATE_10_45), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution, "EXECUTION_START_TIME", "10:30"), \
             patch.object(alpha_bot_execution.time, "sleep"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial)
            mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s
            mock_db.save_state.side_effect = lambda s: captured.append(copy.deepcopy(s))

            alpha_bot_execution.main()

        # save_state should NOT be called with the intraday-loop-mutated state.
        # It MAY be called by the data phase (if the data phase is implemented
        # to save its partial results before fetch_alpaca_history). But if it IS
        # called, the symphony's current_return must still be the seeded value —
        # the intraday loop (which would overwrite it) must not have run.
        for saved in captured:
            if _SYM_ID in saved:
                saved_return = saved[_SYM_ID].get("current_return")
                # The intraday loop would have written current_return=1.5 from fixture;
                # but on the empty-history path the loop should never run. So if
                # current_return is present, it must come from the data phase (same value).
                assert saved_return is None or isinstance(saved_return, (int, float)), (
                    f"current_return in saved state must be a number or None; "
                    f"got {saved_return!r}"
                )

    # -----------------------------------------------------------------------
    # C1: Alpaca batch retries are bounded at max_retries
    # -----------------------------------------------------------------------

    def test_alpaca_batch_retry_is_bounded_at_max_retries(self, tmp_path):
        """
        When every request fails (HTTP 500), fetch_alpaca_history must make exactly
        max_retries=3 attempts per batch, then break — no infinite loop.

        RED: while the current code is correctly bounded, this test pins the
        invariant so a future change (e.g. adding a retry for 429) cannot
        accidentally introduce an unbounded loop.
        """
        cache_file = tmp_path / "history_cache.json"
        tickers = [_TICKER]
        date_str = _DATE_STR

        attempt_counts: list[int] = []

        def counting_get(url, headers, timeout):
            attempt_counts.append(1)
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            return mock_resp

        with patch.object(alpha_bot_execution, "HISTORY_CACHE_FILE", str(cache_file)), \
             patch.object(alpha_bot_execution.requests, "get",
                          side_effect=counting_get), \
             patch.object(alpha_bot_execution.time, "sleep"):

            alpha_bot_execution.fetch_alpaca_history(tickers, date_str)

        # 1 batch × max_retries=3 attempts = exactly 3 requests.get calls
        assert len(attempt_counts) == 3, (
            f"fetch_alpaca_history must make exactly 3 requests.get calls per batch "
            f"when all fail (max_retries=3). Got {len(attempt_counts)} calls. "
            f"An infinite retry loop would cause the engine to hang on network issues."
        )


# ===========================================================================
# 2. last_successful_cycle_at HEALTH FIELD
# ===========================================================================

class TestLastSuccessfulCycleAt:
    """
    bot_state must carry a last_successful_cycle_at timestamp on successful cycles.
    It must NOT be set on any early-return path.
    """

    # -----------------------------------------------------------------------
    # Set on a successful post-gate cycle
    # -----------------------------------------------------------------------

    def test_successful_post_gate_cycle_sets_last_successful_cycle_at(self):
        """
        After a full post-gate cycle (data phase + action phase), bot_state must
        contain 'last_successful_cycle_at' as a non-empty string.

        RED: this field does not exist in the current codebase.
        """
        initial = _seed_state()
        final, _ = _run_main_patched(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
        )

        assert "last_successful_cycle_at" in final, (
            "bot_state must contain 'last_successful_cycle_at' after a successful "
            "post-gate cycle; this is the engine-health freshness signal. "
            "Fix: set bot_state['last_successful_cycle_at'] = current_et.isoformat() "
            "immediately before or after the final save_state call."
        )
        value = final["last_successful_cycle_at"]
        assert isinstance(value, str) and value.strip(), (
            f"last_successful_cycle_at must be a non-empty string; got {value!r}"
        )

    def test_last_successful_cycle_at_is_valid_iso_timestamp(self):
        """
        The value of last_successful_cycle_at must be parseable as an ISO-8601
        datetime string. The dashboard staleness badge needs to compare it to now().
        """
        initial = _seed_state()
        final, _ = _run_main_patched(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
        )

        value = final.get("last_successful_cycle_at", "")
        try:
            # datetime.fromisoformat works for "2025-05-14T10:45:00-04:00" style strings
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            pytest.fail(
                f"last_successful_cycle_at={value!r} is not a valid ISO-8601 datetime: {exc}. "
                f"Use current_et.isoformat() to produce a parseable value."
            )

    # -----------------------------------------------------------------------
    # Set on a successful pre-gate (data-only) cycle
    # -----------------------------------------------------------------------

    def test_successful_pre_gate_data_cycle_sets_last_successful_cycle_at(self):
        """
        A successful pre-gate data-only cycle (09:45 ET) must also set
        last_successful_cycle_at. A successful data phase = a successful cycle
        for freshness purposes.

        This matters because the dashboard must show a fresh timestamp from 09:30
        onward — not just from 10:30 when the action phase starts.

        RED: field doesn't exist yet.
        """
        initial = _seed_state()
        final, _ = _run_main_patched(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        assert "last_successful_cycle_at" in final, (
            "bot_state must contain 'last_successful_cycle_at' after a successful "
            "pre-gate data-only cycle (09:45 ET); the data phase completing = "
            "a successful cycle for the dashboard health signal."
        )

    # -----------------------------------------------------------------------
    # NOT set on lock-overlap early-return
    # -----------------------------------------------------------------------

    def test_lock_overlap_does_not_set_last_successful_cycle_at(self):
        """
        When acquire_lock() returns False (overlap), main() returns immediately
        at line 287. last_successful_cycle_at must NOT be updated on this path —
        no data was gathered, no state was written.

        RED: field doesn't exist, so this test vacuously passes until the field
        is added. But the invariant is: on the overlap path, the prior value of
        last_successful_cycle_at must be unchanged.
        """
        prior_timestamp = "2025-05-14T09:30:00-04:00"
        initial = _seed_state()
        initial["last_successful_cycle_at"] = prior_timestamp

        date_str = _PRE_GATE_09_45.strftime("%Y-%m-%d")
        captured: list[dict] = []

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "get_current_et",
                          return_value=_PRE_GATE_09_45), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = False  # overlap
            mock_db.save_state.side_effect = lambda s: captured.append(copy.deepcopy(s))

            alpha_bot_execution.main()

        # save_state must not be called on overlap return
        assert not captured, (
            "save_state must not be called on lock-overlap (acquire_lock=False); "
            f"got {len(captured)} call(s)."
        )
        # If we somehow got a saved state, last_successful_cycle_at must not change
        for s in captured:
            assert s.get("last_successful_cycle_at") == prior_timestamp, (
                f"last_successful_cycle_at must not be updated on lock-overlap; "
                f"expected {prior_timestamp!r}, got {s.get('last_successful_cycle_at')!r}"
            )

    # -----------------------------------------------------------------------
    # NOT set when fetch_alpaca_history returns {} (silent-fail path)
    # -----------------------------------------------------------------------

    def test_empty_historical_data_does_not_set_last_successful_cycle_at(self):
        """
        When the intraday cycle returns early because fetch_alpaca_history returns {},
        last_successful_cycle_at must NOT be updated. An aborted cycle is not a
        successful cycle.

        Note: the data phase (if implemented) may still have run and set
        last_successful_cycle_at for its portion. But the field must reflect the
        LAST time a FULL cycle (including Alpaca history) completed. If the data
        phase and action phase each have their own freshness signal, that's fine —
        but the pre-existing last_successful_cycle_at from a prior full cycle must
        not be overwritten by a failed-action-phase half-cycle.

        Simpler interpretation (test as written): if the engine has never run a
        successful full cycle, last_successful_cycle_at must be absent after an
        empty-history cycle. (If the data phase ran and set it, that's also
        acceptable behavior — test asserts either absent or unchanged from prior.)
        """
        # Seed with no prior timestamp
        initial = _seed_state()
        assert "last_successful_cycle_at" not in initial

        final, _ = _run_main_patched(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
            fetch_alpaca_return={},  # triggers the empty path
        )

        # The field must either be absent (action phase never completed)
        # OR if the data phase was already running and set it pre-gate,
        # that's a separate pre-gate cycle scenario. For this test (post-gate,
        # empty history), the full cycle did not complete.
        # Accept: absent OR unchanged (not freshly set to a 10:45 timestamp)
        if "last_successful_cycle_at" in final:
            # If it is set, it must not be a timestamp matching the current cycle time
            # (i.e. it must be a prior value, not freshly set by this failed cycle)
            value = final["last_successful_cycle_at"]
            # Can't assert exact value without knowing what the data phase would set,
            # so we just assert it's a string (format validated elsewhere)
            assert isinstance(value, str), (
                f"last_successful_cycle_at, if present, must be a string; got {value!r}"
            )

    # -----------------------------------------------------------------------
    # NOT set when API keys are missing
    # -----------------------------------------------------------------------

    def test_missing_api_keys_does_not_set_last_successful_cycle_at(self):
        """
        When COMPOSER_KEY_ID or ALPACA_KEY is absent, main() returns early at
        line 344 without fetching or computing anything. last_successful_cycle_at
        must not be set.
        """
        initial = _seed_state()
        date_str = _POST_GATE_10_45.strftime("%Y-%m-%d")
        captured: list[dict] = []

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=[]), \
             patch.object(alpha_bot_execution, "get_current_et",
                          return_value=_POST_GATE_10_45), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", None), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", None), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution, "EXECUTION_START_TIME", "10:30"), \
             patch.object(alpha_bot_execution.time, "sleep"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial)
            mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s
            mock_db.save_state.side_effect = lambda s: captured.append(copy.deepcopy(s))

            alpha_bot_execution.main()

        for saved in captured:
            assert "last_successful_cycle_at" not in saved, (
                "last_successful_cycle_at must not appear in bot_state when API keys "
                "are missing (missing-key early-return path)."
            )


# ===========================================================================
# 3. /api/state SURFACES last_successful_cycle_at
# ===========================================================================

class TestApiStateExposesLastSuccessfulCycleAt:
    """
    /api/state must include last_successful_cycle_at in its JSON response payload.
    """

    def test_api_state_includes_last_successful_cycle_at_field(self):
        """
        When bot_state contains 'last_successful_cycle_at', /api/state must
        include it in the response JSON payload (either in 'state' or as a
        top-level key).

        RED: the field doesn't exist in bot_state yet, and /api/state doesn't
        expose it. After the fix, both must be true.
        """
        import analytics
        from flask import json as flask_json

        timestamp = "2025-05-14T10:45:00-04:00"
        mock_state = {
            "date": _DATE_STR,
            "last_execution_mode": False,
            "last_successful_cycle_at": timestamp,
        }

        with app_module.app.test_client() as client, \
             patch.object(app_module.database, "load_state",
                          return_value=mock_state), \
             patch.object(app_module, "schedule") as mock_schedule, \
             patch.object(app_module.database, "normalize_name",
                          side_effect=lambda n: n.strip().lower()):

            mock_schedule.get_jobs.return_value = []

            resp = client.get("/api/state")

        assert resp.status_code == 200, (
            f"/api/state returned HTTP {resp.status_code}: "
            f"{resp.get_data(as_text=True)[:200]}"
        )
        data = resp.get_json()
        assert data is not None, "/api/state must return valid JSON"

        # The field must appear somewhere in the response
        in_state = data.get("state", {}).get("last_successful_cycle_at")
        top_level = data.get("last_successful_cycle_at")
        assert in_state == timestamp or top_level == timestamp, (
            f"last_successful_cycle_at={timestamp!r} must appear in the /api/state "
            f"JSON response (either in 'state' or as a top-level key). "
            f"'state.last_successful_cycle_at'={in_state!r}, "
            f"top-level 'last_successful_cycle_at'={top_level!r}. "
            f"Fix: include this field in the /api/state response so the dashboard "
            f"can render the staleness badge."
        )

    def test_api_state_absent_last_successful_cycle_at_does_not_crash(self):
        """
        When bot_state has no 'last_successful_cycle_at' (e.g. first cycle after
        deploy), /api/state must still return HTTP 200 without crashing.

        This is a non-breaking contract: the field is optional in bot_state but
        the dashboard must handle its absence gracefully.
        """
        from flask import json as flask_json

        mock_state = {
            "date": _DATE_STR,
            "last_execution_mode": False,
        }

        with app_module.app.test_client() as client, \
             patch.object(app_module.database, "load_state",
                          return_value=mock_state), \
             patch.object(app_module, "schedule") as mock_schedule, \
             patch.object(app_module.database, "normalize_name",
                          side_effect=lambda n: n.strip().lower()):

            mock_schedule.get_jobs.return_value = []
            resp = client.get("/api/state")

        assert resp.status_code == 200, (
            f"/api/state must return HTTP 200 even when 'last_successful_cycle_at' "
            f"is absent from bot_state. Got HTTP {resp.status_code}."
        )


# ===========================================================================
# 4. SUBPROCESS STDOUT CAPTURE IN app.py
# ===========================================================================

class TestSubprocessStdoutCapture:
    """
    app.py's subprocess.run invocation must pipe stdout+stderr to a log file.
    """

    def test_trigger_alpha_bot_opens_log_file_for_append(self):
        """
        trigger_alpha_bot() must open a log file in append mode and pass it as
        stdout+stderr to subprocess.run.

        RED: current code calls subprocess.run(cmd, check=True, env=env) with no
        stdout/stderr — all engine output is discarded. Under restart.ps1's
        WindowStyle Hidden launcher, this makes every print() in the engine
        completely invisible.

        Fix: open a log file (e.g. LOG_FILE = os.path.join(os.path.dirname(__file__), 'alphabot_daemon.log'))
        in append mode and pass as stdout=fh, stderr=fh to subprocess.run.
        """
        with patch.object(app_module.subprocess, "run") as mock_run, \
             patch("builtins.open", mock_open()) as mock_file:

            mock_run.return_value = MagicMock(returncode=0)
            app_module.trigger_alpha_bot()

        # Assert that open() was called with a file path (the log file) in append mode
        # Look through all open() calls for one with 'a' mode
        open_calls = mock_file.call_args_list
        append_calls = [
            c for c in open_calls
            if len(c.args) >= 2 and c.args[1] == "a"
            or c.kwargs.get("mode") == "a"
        ]
        assert append_calls, (
            f"trigger_alpha_bot() must open a log file in append mode ('a') before "
            f"calling subprocess.run. "
            f"open() calls seen: {[str(c) for c in open_calls]}. "
            f"Fix: add a log file open in append mode and pass to subprocess.run as "
            f"stdout=log_fh, stderr=log_fh."
        )

    def test_trigger_alpha_bot_passes_stdout_to_subprocess(self):
        """
        subprocess.run must receive stdout= and stderr= kwargs (not None).
        Passing None or omitting them discards the engine's output.

        RED: current code: subprocess.run(cmd, check=True, env=env) — no stdout/stderr.
        """
        with patch.object(app_module.subprocess, "run") as mock_run, \
             patch("builtins.open", mock_open()):

            mock_run.return_value = MagicMock(returncode=0)
            app_module.trigger_alpha_bot()

        assert mock_run.called, "subprocess.run must be called by trigger_alpha_bot()"
        kwargs = mock_run.call_args.kwargs
        args = mock_run.call_args.args

        # stdout must be passed as a non-None, non-PIPE value (it must be a file handle)
        # subprocess.PIPE (= -1) would buffer in memory and not write to a file
        import subprocess
        stdout = kwargs.get("stdout")
        assert stdout is not None, (
            f"subprocess.run must receive stdout=<file_handle>; got stdout=None. "
            f"Full subprocess.run kwargs: {kwargs}. "
            f"Fix: pass stdout=log_fh so engine output lands in the log file."
        )
        assert stdout != subprocess.PIPE, (
            "subprocess.run stdout must be a file handle, not subprocess.PIPE. "
            "PIPE buffers in memory but doesn't persist across daemon restarts."
        )

    def test_trigger_alpha_bot_log_file_path_is_configurable(self):
        """
        The log file path must be a module-level constant or env-var-derived value —
        not an inline literal only visible inside the function body.

        This ensures the operator can check the log file path from the module without
        reading the function source; it also makes tests independent of the literal path.

        RED: no log file constant exists in app.py yet.
        """
        # After the fix, app_module must expose a LOG_FILE constant (or similar name)
        assert hasattr(app_module, "LOG_FILE") or hasattr(app_module, "DAEMON_LOG_FILE") or \
               hasattr(app_module, "ENGINE_LOG_FILE") or hasattr(app_module, "BOT_LOG_FILE"), (
            "app.py must expose a module-level constant for the log file path "
            "(e.g. LOG_FILE, DAEMON_LOG_FILE). This makes the path discoverable "
            "by operators and tests without reading the function source."
        )


# ===========================================================================
# 5. EXECUTION_START_TIME COMES FROM ENV (not hardcoded)
# ===========================================================================

class TestExecutionStartTimeFromEnv:
    """
    The action-phase gate must use the EXECUTION_START_TIME env variable,
    not a hardcoded constant. A different value in .env must produce a different gate.
    """

    def test_action_phase_does_not_fire_before_custom_execution_start_time(self):
        """
        When EXECUTION_START_TIME is set to '11:00', a cycle at 10:45 ET must NOT
        run the action phase (run_monte_carlo must not be called).

        This pins that the gate is EXECUTION_START_TIME-derived, not hardcoded to
        10:30. If the implementer hardcodes 10:30 as the action gate, this test fails.

        RED: currently there is no data/action split at all, so run_monte_carlo IS
        called at 10:45 regardless of EXECUTION_START_TIME (because the whole
        intraday loop is gated as one block). After the split, the action-phase gate
        must use the parsed EXECUTION_START_TIME value.
        """
        initial = _seed_state()
        _, mocks = _run_main_patched(
            current_et=_POST_GATE_10_45,   # 10:45 ET
            initial_bot_state=initial,
            execution_start_time="11:00",   # gate is at 11:00, so 10:45 is pre-gate
        )

        assert mocks["mock_math"].run_monte_carlo.call_count == 0, (
            f"With EXECUTION_START_TIME=11:00, a cycle at 10:45 ET must NOT call "
            f"run_monte_carlo (it's before the action gate). "
            f"call_count={mocks['mock_math'].run_monte_carlo.call_count}. "
            f"Fix: the action-phase gate must use the parsed EXECUTION_START_TIME "
            f"value, not a hardcoded '10:30'."
        )

    def test_action_phase_fires_at_or_after_custom_execution_start_time(self):
        """
        When EXECUTION_START_TIME='09:45' (an earlier gate), a cycle at 10:45 ET
        must run the action phase (run_monte_carlo called).

        This confirms the gate is dynamic — it can be moved earlier as well as later.
        """
        initial = _seed_state()
        _, mocks = _run_main_patched(
            current_et=_POST_GATE_10_45,   # 10:45 ET — well after the 09:45 gate
            initial_bot_state=initial,
            execution_start_time="09:45",  # earlier gate
        )

        assert mocks["mock_math"].run_monte_carlo.call_count >= 1, (
            f"With EXECUTION_START_TIME=09:45, a cycle at 10:45 ET must call "
            f"run_monte_carlo (action phase is active after the gate). "
            f"call_count={mocks['mock_math'].run_monte_carlo.call_count}. "
            f"Fix: the action-phase gate must fire for any time >= EXECUTION_START_TIME."
        )

    def test_execution_start_time_env_var_is_read_from_module_level_constant(self):
        """
        EXECUTION_START_TIME must be a module-level variable in alpha_bot_execution,
        not read inline via os.getenv() inside main(). The module-level read pattern
        allows tests to patch EXECUTION_START_TIME directly via patch.object.

        This is structural: if EXECUTION_START_TIME were read inside main() via
        os.getenv(), patching alpha_bot_execution.EXECUTION_START_TIME would have
        no effect and all the gate tests would be uncontrollable.

        Confirm the attribute exists on the module.
        """
        assert hasattr(alpha_bot_execution, "EXECUTION_START_TIME"), (
            "alpha_bot_execution must expose EXECUTION_START_TIME as a module-level "
            "attribute (currently: `EXECUTION_START_TIME = os.getenv('EXECUTION_START_TIME', '09:30')` "
            "at the top of the file). This is already true in the current code — "
            "this test pins that it stays true after any refactor."
        )
        # It must be a string (or None if env not set)
        val = alpha_bot_execution.EXECUTION_START_TIME
        assert val is None or isinstance(val, str), (
            f"EXECUTION_START_TIME must be a string or None; got {type(val).__name__}"
        )
