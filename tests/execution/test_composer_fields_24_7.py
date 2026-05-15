"""
RED-phase tests for CR/MDD 24/7 persistence — the gap left by b9ab6f9.

The b9ab6f9 fix placed _persist_composer_fields_to_bot_state ONLY inside the
intraday evaluation loop (alpha_bot_execution.py:689), which runs 09:30-16:00 ET.
After-hours, weekend, and market-closed cycles never execute that loop, so the 4
Composer fields (simple_return, net_deposits, time_weighted_return, max_drawdown)
are never written to bot_state on those paths.

These tests are RED because they assert that after any successful cycle — regardless
of market state — bot_state ends with the 4 fields populated. They will turn GREEN
when the implementer lifts the Composer fetch + persist to run before (or instead of)
the market-closed early-return at alpha_bot_execution.py:310-313.

Verified invariants:
  1. Market-closed cycle (weekday, before 09:30): bot_state has all 4 fields.
  2. After-hours / EOD window (weekday, 16:00-16:05): bot_state has all 4 fields.
  3. Weekend (Saturday after 16:05): bot_state has all 4 fields.
  4. Fetch failure preserves prior cycle's values — cycle 2 fetch fails, bot_state
     still has cycle 1 values (not None/absent).
  5. No double-persist: _persist_composer_fields_to_bot_state is called at most
     once per symphony per cycle, OR if called twice the second write is idempotent
     (values are identical). Asserts the call-count invariant.

Mocking philosophy mirrors test_main_pipeline.py:
  - No live Composer or Alpaca calls.
  - fetch_symphony_stats is patched to return fixture-derived data.
  - database functions are patched to inject/capture bot_state.
  - get_current_et is patched per scenario.
  - math_engine NOT mocked — not reached on market-closed paths.
  - time.sleep patched to no-op.

Fixture: tests/fixtures/composer/symphony_stats_meta.json (captured-from-producer).
The test uses only the first 2 symphonies from the fixture to keep setup concise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import alpha_bot_execution

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "composer" / "symphony_stats_meta.json"
)
_FOUR_FIELDS = ("simple_return", "net_deposits", "time_weighted_return", "max_drawdown")


def _load_composer_symphonies() -> list[dict]:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)["symphonies"]


# Use the first 2 fixture symphonies across all tests.
_FIXTURE_SYMPHONIES = _load_composer_symphonies()[:2]
_SYM_IDS = [s["id"] for s in _FIXTURE_SYMPHONIES]
_ACCOUNT_ID = "acct-crmdd24-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
        return datetime(year, month, day, hour, minute, 0, tzinfo=_ET)
except Exception:  # pragma: no cover -- tzdata missing
    def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
        return datetime(year, month, day, hour, minute, 0,
                        tzinfo=timezone(timedelta(hours=-4)))


# Fixed ET times for each scenario
_WEEKDAY_PRE_OPEN = _et(2025, 5, 14, 8, 0)     # Wed 08:00 — before market open
_WEEKDAY_EOD_WINDOW = _et(2025, 5, 16, 16, 2)  # Fri 16:02 — within post-mortem window
_WEEKEND_AFTER_CLOSE = _et(2025, 5, 17, 10, 0)  # Sat 10:00 — weekend, market closed


def _minimal_bot_state_entry(symphony_id: str) -> dict:
    """Minimal 30-field bot_state entry as written by the engine (no Composer fields)."""
    return {
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
        "name": "Test Symphony",
        "account": _ACCOUNT_ID,
        "current_return": -1.92,
        "mc_prob": 65.0,
        "stop_trigger": -3.0,
        "active_stop_distance": 2.0,
        "symphony_vol": 0.15,
        "current_value": 1609.36,
        "current_holdings": [],
    }


def _seed_state_with_symphonies(extra_top_level: dict | None = None) -> dict:
    """Build a bot_state dict with entries for both fixture symphonies, no Composer fields."""
    state = {
        "date": "2025-05-14",
        "last_execution_mode": False,
    }
    for sym_id in _SYM_IDS:
        state[sym_id] = _minimal_bot_state_entry(sym_id)
    if extra_top_level:
        state.update(extra_top_level)
    return state


def _seed_state_with_composer_fields() -> dict:
    """bot_state where cycle 1 already persisted Composer fields into each symphony."""
    state = _seed_state_with_symphonies()
    for sym in _FIXTURE_SYMPHONIES:
        sym_id = sym["id"]
        state[sym_id]["simple_return"] = sym["simple_return"]
        state[sym_id]["net_deposits"] = sym["net_deposits"]
        state[sym_id]["time_weighted_return"] = sym["time_weighted_return"]
        state[sym_id]["max_drawdown"] = sym["max_drawdown"]
    return state


def _minimal_history(date_str: str) -> dict:
    return {
        date_str: {
            "SPY": {
                "c": 500.0, "daily_ret": 0.001, "high": 501.0, "low": 499.0, "close": 500.0
            }
        }
    }


# ---------------------------------------------------------------------------
# Shared patch context
#
# All scenarios need the same base patches. Each test overrides get_current_et
# and any scenario-specific values.
# ---------------------------------------------------------------------------

def _base_patches(
    current_et: datetime,
    bot_state: dict,
    fetch_sym_return_value: list | None = None,
) -> dict:
    """
    Build a dict of (patch_target, patch_kwargs) for contextlib.ExitStack.
    Returns a context manager that yields the mock_db and other mocks.

    This is a helper — tests use _run_cycle_with_patches() instead.
    """
    raise NotImplementedError  # not used directly; see _run_cycle_with_patches


def _run_cycle_with_patches(
    current_et: datetime,
    initial_bot_state: dict,
    fetch_sym_return_value: list | None = None,
) -> dict:
    """
    Run alpha_bot_execution.main() with patched dependencies for the given
    market time and initial bot_state.

    Returns the final bot_state dict captured from the LAST save_state call.
    If save_state is never called, returns the initial_bot_state unchanged.

    fetch_sym_return_value: what fetch_symphony_stats returns. Defaults to
    _FIXTURE_SYMPHONIES (the 2-symphony fixture subset). Pass [] to simulate
    a fetch failure (empty result from the function, which returns [] on error).
    """
    if fetch_sym_return_value is None:
        fetch_sym_return_value = _FIXTURE_SYMPHONIES

    date_str = current_et.strftime("%Y-%m-%d")
    captured_states: list[dict] = []

    def capture_save_state(state):
        captured_states.append({k: (v.copy() if isinstance(v, dict) else v)
                                 for k, v in state.items()})

    # Deep-copy the bot_state so load_state returns a fresh mutable copy per call
    import copy

    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting") as _mock_reporting, \
         patch.object(alpha_bot_execution, "fetch_symphony_stats",
                      return_value=fetch_sym_return_value) as _mock_fetch_sym, \
         patch.object(alpha_bot_execution, "fetch_alpaca_history",
                      return_value=_minimal_history(date_str)) as _mock_fetch_hist, \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                      return_value={}) as _mock_fetch_vwap, \
         patch.object(alpha_bot_execution, "get_current_et",
                      return_value=current_et), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
         patch.object(alpha_bot_execution, "autotuner") as _mock_autotuner:

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = copy.deepcopy(initial_bot_state)
        mock_db.load_chart_history.return_value = {
            "date": date_str, "symphonies": {}
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s
        mock_db.save_state.side_effect = capture_save_state
        _mock_autotuner.run_autotuner.return_value = None

        alpha_bot_execution.main()

    if not captured_states:
        return copy.deepcopy(initial_bot_state)

    # Return the last saved state (the definitive post-cycle state)
    return captured_states[-1]


# ---------------------------------------------------------------------------
# Scenario 1 — Market-closed (weekday, pre-open)
#
# Simulates a cycle at Wed 08:00 ET — market not yet open. Under b9ab6f9, the
# early-return at alpha_bot_execution.py:310-313 fires before any Composer fetch,
# so the 4 fields are never written. After the fix, the 4 fields must be present.
# ---------------------------------------------------------------------------

class TestMarketClosedCyclePopulatesComposerFields:
    """The test that would have caught the b9ab6f9 deployment-timing gap."""

    def test_market_closed_weekday_pre_open_populates_simple_return(self):
        """
        After a market-closed cycle (weekday before open), bot_state must contain
        'simple_return' for each symphony that had data in the Composer fetch.

        RED: currently the early-return at :310-313 fires before the Composer fetch,
        so save_state is never called and the 4 fields are never written.
        """
        initial = _seed_state_with_symphonies()
        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_PRE_OPEN,
            initial_bot_state=initial,
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            assert sym_id in final, (
                f"bot_state must contain symphony {sym_id} after market-closed cycle"
            )
            assert "simple_return" in final[sym_id], (
                f"bot_state[{sym_id}] must contain 'simple_return' after market-closed "
                f"cycle; fix: lift Composer fetch + persist before early-return at :310-313"
            )

    def test_market_closed_weekday_pre_open_populates_all_four_fields(self):
        """
        All four Composer fields must be present in bot_state after any market-closed cycle.
        This is the canonical regression test for the b9ab6f9 placement gap.
        """
        initial = _seed_state_with_symphonies()
        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_PRE_OPEN,
            initial_bot_state=initial,
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            assert sym_id in final, (
                f"symphony {sym_id} must be in bot_state after market-closed cycle"
            )
            for field in _FOUR_FIELDS:
                assert field in final[sym_id], (
                    f"bot_state[{sym_id}] missing '{field}' after market-closed cycle. "
                    f"The b9ab6f9 persist only runs in the intraday loop (09:30-16:00); "
                    f"fix: lift the Composer fetch + persist before the early-return "
                    f"at alpha_bot_execution.py:310-313."
                )

    def test_market_closed_weekday_pre_open_field_values_match_fixture(self):
        """
        The persisted values must match what fetch_symphony_stats returned (the fixture).
        Shape-and-value test: value is not None and matches the fixture object.
        """
        initial = _seed_state_with_symphonies()
        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_PRE_OPEN,
            initial_bot_state=initial,
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            if sym_id not in final:
                pytest.fail(
                    f"symphony {sym_id} absent from final bot_state after market-closed cycle"
                )
            for field in _FOUR_FIELDS:
                if field not in final[sym_id]:
                    pytest.fail(
                        f"field '{field}' absent from bot_state[{sym_id}] — "
                        f"Composer persist not running on market-closed path"
                    )
                # Value must equal what the fixture provides (or None if fixture value is None)
                expected = sym.get(field)
                actual = final[sym_id][field]
                if expected is None:
                    assert actual is None, (
                        f"bot_state[{sym_id}]['{field}']: expected None from fixture, "
                        f"got {actual!r}"
                    )
                else:
                    assert actual == pytest.approx(expected, abs=1e-9), (
                        f"bot_state[{sym_id}]['{field}']: expected {expected!r} from "
                        f"fixture, got {actual!r}"
                    )


# ---------------------------------------------------------------------------
# Scenario 2 — After-hours / EOD window (weekday, 16:00-16:05)
#
# The EOD post-mortem branch iterates symphony_data_cache but does NOT call
# _persist_composer_fields_to_bot_state. After the fix it must.
# ---------------------------------------------------------------------------

class TestEodWindowPopulatesComposerFields:

    def test_eod_window_friday_populates_all_four_fields(self):
        """
        After an EOD post-mortem cycle (Fri 16:02 ET), the 4 Composer fields
        must be present in bot_state. Currently absent because the EOD branch
        (~:404-412) writes current_return/holdings but not the Composer fields.
        """
        initial = _seed_state_with_symphonies(
            extra_top_level={"date": "2025-05-16"}  # Friday
        )
        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_EOD_WINDOW,
            initial_bot_state=initial,
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            assert sym_id in final, (
                f"symphony {sym_id} must be in bot_state after EOD post-mortem cycle"
            )
            for field in _FOUR_FIELDS:
                assert field in final[sym_id], (
                    f"bot_state[{sym_id}] missing '{field}' after EOD window cycle. "
                    f"EOD branch (~:404-412) does not call _persist_composer_fields_to_bot_state; "
                    f"fix must add the persist call or lift it above all branches."
                )


# ---------------------------------------------------------------------------
# Scenario 3 — Weekend cycle
#
# Saturday is is_weekday=False, so the early-return at :310 fires. After the fix,
# a Composer fetch + persist must still run (or the early-return becomes conditional
# on whether the persist has already run for today).
# ---------------------------------------------------------------------------

class TestWeekendCyclePopulatesComposerFields:

    def test_weekend_cycle_populates_all_four_fields(self):
        """
        After a weekend cycle (Saturday), the 4 Composer fields must be present
        in bot_state. Weekend implies is_weekday=False, so the early-return at
        :310-313 fires (unless the fix restructures that check).
        """
        initial = _seed_state_with_symphonies(
            extra_top_level={"date": "2025-05-17"}
        )
        final = _run_cycle_with_patches(
            current_et=_WEEKEND_AFTER_CLOSE,
            initial_bot_state=initial,
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            assert sym_id in final, (
                f"symphony {sym_id} must be in bot_state after weekend cycle; "
                f"the Composer fetch + persist must run before the early-return guard."
            )
            for field in _FOUR_FIELDS:
                assert field in final[sym_id], (
                    f"bot_state[{sym_id}] missing '{field}' after weekend cycle. "
                    f"Weekend path hits the early-return at :310-313 before any Composer fetch."
                )


# ---------------------------------------------------------------------------
# Scenario 4 — Fetch failure preserves prior cycle's values
#
# Cycle 1: fetch succeeds, fields are written to bot_state.
# Cycle 2: fetch returns [] (failure path of fetch_symphony_stats).
# Assertion: bot_state still has the cycle-1 values, not None/absent.
#
# This pins A/C criterion 3: "On a Composer fetch failure, bot_state retains
# the LAST successful values — do NOT overwrite with absent/None on failure."
# ---------------------------------------------------------------------------

class TestFetchFailurePreservesPriorValues:

    def test_fetch_failure_cycle_preserves_prior_simple_return(self):
        """
        When the Composer fetch returns [] on cycle 2, bot_state must still
        contain 'simple_return' with the value written by cycle 1.

        The fix must NOT overwrite existing Composer fields with None/absent when
        fetch_symphony_stats returns an empty list.
        """
        # Simulate a bot_state that already has Composer fields from a prior cycle
        prior_state = _seed_state_with_composer_fields()

        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_PRE_OPEN,
            initial_bot_state=prior_state,
            fetch_sym_return_value=[],  # fetch failure
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            assert sym_id in final, (
                f"bot_state must retain symphony {sym_id} after a fetch-failure cycle"
            )
            assert "simple_return" in final[sym_id], (
                f"bot_state[{sym_id}] must retain 'simple_return' after fetch failure; "
                f"the fix must not overwrite existing fields when the fetch returns []"
            )

    def test_fetch_failure_cycle_preserves_all_four_fields_with_prior_values(self):
        """
        After a fetch-failure cycle, all four Composer fields in bot_state must
        equal the prior cycle's values (from the fixture). Not None, not 0.0,
        not absent — the exact prior values.
        """
        prior_state = _seed_state_with_composer_fields()

        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_PRE_OPEN,
            initial_bot_state=prior_state,
            fetch_sym_return_value=[],  # fetch failure
        )

        for sym in _FIXTURE_SYMPHONIES:
            sym_id = sym["id"]
            if sym_id not in final:
                pytest.fail(
                    f"symphony {sym_id} absent from bot_state after fetch-failure cycle; "
                    f"state must be preserved even when Composer is unreachable."
                )
            for field in _FOUR_FIELDS:
                if field not in final[sym_id]:
                    pytest.fail(
                        f"bot_state[{sym_id}]['{field}'] absent after fetch-failure cycle; "
                        f"the fix must not erase existing Composer fields on fetch failure."
                    )
                expected = sym.get(field)
                actual = final[sym_id][field]
                if expected is None:
                    assert actual is None, (
                        f"prior value for {sym_id}['{field}'] was None; must remain None "
                        f"after fetch failure, got {actual!r}"
                    )
                else:
                    assert actual == pytest.approx(expected, abs=1e-9), (
                        f"bot_state[{sym_id}]['{field}'] must equal prior cycle value "
                        f"{expected!r} after fetch failure; got {actual!r}. "
                        f"Fix must not overwrite with None on []."
                    )

    def test_fetch_failure_does_not_introduce_none_over_prior_real_value(self):
        """
        Specifically pins the None-overwrite case: if prior cycle wrote
        simple_return=0.65976, a fetch-failure cycle must NOT overwrite it with None.

        This guards against an implementation that calls _persist_composer_fields_to_bot_state
        with an empty sym={} or sym.get() defaults — which would write None over the
        real prior value.
        """
        prior_state = _seed_state_with_composer_fields()
        # Identify a symphony with a non-None, non-zero simple_return in the fixture
        target_sym = next(
            (s for s in _FIXTURE_SYMPHONIES if s.get("simple_return") not in (None, 0.0)),
            None,
        )
        if target_sym is None:
            pytest.skip("no non-zero simple_return in first 2 fixture symphonies")

        sym_id = target_sym["id"]
        prior_value = prior_state[sym_id]["simple_return"]
        assert prior_value not in (None, 0.0), "test precondition: prior value is real"

        final = _run_cycle_with_patches(
            current_et=_WEEKDAY_PRE_OPEN,
            initial_bot_state=prior_state,
            fetch_sym_return_value=[],  # fetch failure
        )

        assert final.get(sym_id, {}).get("simple_return") == pytest.approx(
            prior_value, abs=1e-9
        ), (
            f"bot_state[{sym_id}]['simple_return'] must remain {prior_value!r} after "
            f"a fetch-failure cycle; the fix must guard against overwriting prior values "
            f"with None when fetch returns []."
        )


# ---------------------------------------------------------------------------
# Scenario 5 — No double-persist within a single intraday cycle
#
# After the fix lifts the persist call above the market-hours branch, an intraday
# cycle (09:30-16:00) could call _persist_composer_fields_to_bot_state twice:
# once in the lifted path and once at the original line 689. The fix must ensure
# this is either idempotent (values are identical both times) OR the original line
# 689 call is removed. This test asserts at most one call per symphony per cycle.
# ---------------------------------------------------------------------------

class TestNoPersistDoubleCallWithinIntraday:

    def test_persist_function_called_at_most_once_per_symphony_per_intraday_cycle(self):
        """
        During an intraday cycle (09:30-16:00 ET), _persist_composer_fields_to_bot_state
        must be called at most once per symphony.

        This guards against the lifted-path + original-line-689 double-call, which
        while idempotent on identical data, is wasteful and signals a half-finished
        refactor (the line-689 call was not removed).

        If the implementer instead removes line 689 and calls the persist only from
        the lifted path, this test passes by asserting call_count <= len(symphonies).
        If the implementer chooses to keep line 689 and adds a guard making the lifted
        call a no-op when market is open, this test also passes.
        """
        try:
            from zoneinfo import ZoneInfo
            et = ZoneInfo("America/New_York")
            intraday_et = datetime(2025, 5, 14, 11, 30, 0, tzinfo=et)
        except Exception:
            intraday_et = datetime(2025, 5, 14, 11, 30, 0,
                                   tzinfo=timezone(timedelta(hours=-4)))

        initial = _seed_state_with_symphonies()
        date_str = intraday_et.strftime("%Y-%m-%d")

        import copy
        call_log: list[tuple[str, str]] = []  # (symphony_id, field_snapshot_key)

        original_persist = alpha_bot_execution._persist_composer_fields_to_bot_state

        def spy_persist(bot_state, symphony_id, sym):
            call_log.append(symphony_id)
            original_persist(bot_state, symphony_id, sym)

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=_FIXTURE_SYMPHONIES), \
             patch.object(alpha_bot_execution, "fetch_alpaca_history",
                          return_value=_minimal_history(date_str)), \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                          return_value={}), \
             patch.object(alpha_bot_execution, "get_current_et",
                          return_value=intraday_et), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution.time, "sleep"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
             patch.object(alpha_bot_execution, "_persist_composer_fields_to_bot_state",
                          side_effect=spy_persist):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial)
            mock_db.load_chart_history.return_value = {
                "date": date_str, "symphonies": {}
            }
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s

            with patch.object(alpha_bot_execution, "math_engine") as mock_math:
                mock_math.run_monte_carlo.return_value = ([], 0.65)
                mock_math.calculate_20d_vol.return_value = 0.15
                mock_math.compute_volatility_scaling.return_value = (0.15, 2.0)
                mock_math.compute_log_time_squeeze.return_value = (2.0, 0.5)
                mock_math.compute_parabolic_ratchet.return_value = False
                mock_math.compute_para_arm_decision.return_value = (False, False)
                mock_math.compute_exit_confirmation.return_value = (0, False)
                mock_math.compute_breakeven_update.return_value = (0.0, False)
                mock_math.compute_vwap_breakdown_update.return_value = (0, 0, False, False)

            alpha_bot_execution.main()

        # Each of the 2 fixture symphonies must be persisted AT MOST once.
        for sym_id in _SYM_IDS:
            sym_call_count = call_log.count(sym_id)
            assert sym_call_count <= 1, (
                f"_persist_composer_fields_to_bot_state called {sym_call_count} times "
                f"for symphony {sym_id} in a single intraday cycle; must be <= 1. "
                f"If the lifted path and line-689 both call it, remove one."
            )
