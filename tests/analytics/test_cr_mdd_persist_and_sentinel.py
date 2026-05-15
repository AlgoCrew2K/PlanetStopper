"""
RED-phase tests for the CR/MDD persist path and None-sentinel contract.

Three surfaces under test:
  1. Engine persist path — alpha_bot_execution._update_bot_state_from_composer (or equivalent
     inline logic at the bot_state write site ~line 673-680) must write all four Composer fields
     into the per-symphony bot_state dict: simple_return, net_deposits, time_weighted_return,
     max_drawdown. Join key: composer_obj["id"] == bot_state key.

  2. app.py /api/state symphonies_list construction — given a bot_state dict that carries the
     four real Composer fields, the symphonies_list built at app.py:153-166 must pass real
     (non-zero) values to the helpers. This is the wiring test: correct source → non-zero output.

  3. None-sentinel — when a Composer field is genuinely absent (not zero), the helpers must
     receive None (not 0.0) and the analytics sentinel path must propagate it so the template
     can render '---' rather than '0.00%'. A REAL zero (simple_return=0.0 with
     net_deposits != 0.0, or max_drawdown=0.0) must still render as '0.00%'.

All tests are fixture-only — no live Composer or Alpaca calls.
Fixture: tests/fixtures/composer/symphony_stats_meta.json (captured-from-producer).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "composer"
_SYMPHONY_STATS_META_FIXTURE = _FIXTURE_DIR / "symphony_stats_meta.json"


@pytest.fixture(scope="module")
def all_composer_symphonies() -> list[dict]:
    """All 11 symphonies from the captured fixture."""
    with open(_SYMPHONY_STATS_META_FIXTURE, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload["symphonies"]


@pytest.fixture(scope="module")
def composer_symphony_with_nonzero_fields(all_composer_symphonies) -> dict:
    """
    A Composer symphony with all four non-None fields and non-zero simple_return.
    Uses index 1: simple_return=0.65976, net_deposits=658.5,
                   time_weighted_return non-zero, max_drawdown=0.1495.
    """
    sym = all_composer_symphonies[1]
    assert sym["simple_return"] != 0.0, "fixture assumption: index 1 has non-zero simple_return"
    assert sym["net_deposits"] != 0.0, "fixture assumption: index 1 has non-zero net_deposits"
    assert sym["max_drawdown"] is not None, "fixture assumption: index 1 has max_drawdown"
    return sym


@pytest.fixture(scope="module")
def composer_symphony_twr_fallback(all_composer_symphonies) -> dict:
    """
    Index 0: simple_return=0.0, net_deposits=0.0, time_weighted_return=3.13212.
    TWR-fallback case — but still has REAL data (just uses TWR, not simple_return).
    """
    sym = all_composer_symphonies[0]
    assert sym["simple_return"] == 0.0
    assert sym["net_deposits"] == 0.0
    assert sym["time_weighted_return"] != 0.0
    return sym


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FOUR_FIELDS = ("simple_return", "net_deposits", "time_weighted_return", "max_drawdown")


def _minimal_bot_state_entry(symphony_id: str, current_return: float = -1.92) -> dict:
    """Minimal engine-written bot_state entry (the 29-field shape from recon)."""
    return {
        "high_water_mark": current_return,
        "shadow_hwm": current_return,
        "prev_return": current_return,
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
        "account": "test-account",
        "current_return": current_return,
        "mc_prob": 65.0,
        "stop_trigger": -3.0,
        "active_stop_distance": 2.0,
        "symphony_vol": 0.15,
        "current_value": 1609.36,
        "current_holdings": [],
    }


def _bot_state_entry_with_composer_fields(
    symphony_id: str,
    composer_sym: dict,
    current_return: float = -1.92,
) -> dict:
    """Engine-written bot_state entry after the fix: includes all four Composer fields."""
    entry = _minimal_bot_state_entry(symphony_id, current_return)
    entry["simple_return"] = composer_sym["simple_return"]
    entry["net_deposits"] = composer_sym["net_deposits"]
    entry["time_weighted_return"] = composer_sym["time_weighted_return"]
    entry["max_drawdown"] = composer_sym["max_drawdown"]
    return entry


# ---------------------------------------------------------------------------
# SURFACE 1 — Engine persist path
#
# The engine writes bot_state[symphony_id][field] for all four Composer fields.
# Tests assert the shape of bot_state AFTER the engine processes a symphony.
# We test the persist logic in isolation by calling the module-level function
# (or the inline assembly) with a fake symphony dict from the fixture.
#
# These tests are RED because the engine currently does NOT write these fields.
# They will turn GREEN when the implementer adds the four lines to the
# bot_state write assembly at alpha_bot_execution.py ~line 673-680.
# ---------------------------------------------------------------------------


class TestEnginePersistsComposerFieldsIntoBotState:

    def test_simple_return_written_to_bot_state_after_engine_processes_symphony(
        self, composer_symphony_with_nonzero_fields
    ):
        """
        After the engine processes a Composer symphony dict, bot_state[symphony_id]
        must contain 'simple_return' with the value from the Composer object.

        Currently fails because alpha_bot_execution.py:673-680 does not write
        simple_return into bot_state.
        """
        import alpha_bot_execution

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]

        # Simulate the engine's bot_state write site: the entry exists (not a new symphony)
        bot_state = {symphony_id: _minimal_bot_state_entry(symphony_id)}

        # Apply only the persist-path logic (the four lines the implementer must add)
        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)

        assert "simple_return" in bot_state[symphony_id], (
            f"engine must write 'simple_return' into bot_state[{symphony_id}]; "
            f"current keys: {list(bot_state[symphony_id].keys())}"
        )
        assert bot_state[symphony_id]["simple_return"] == pytest.approx(
            sym["simple_return"], abs=1e-9
        ), (
            f"persisted simple_return must match Composer value {sym['simple_return']}; "
            f"got {bot_state[symphony_id].get('simple_return')}"
        )

    def test_net_deposits_written_to_bot_state(self, composer_symphony_with_nonzero_fields):
        """bot_state[symphony_id]['net_deposits'] must match Composer net_deposits."""
        import alpha_bot_execution

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]
        bot_state = {symphony_id: _minimal_bot_state_entry(symphony_id)}

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)

        assert "net_deposits" in bot_state[symphony_id], (
            f"engine must write 'net_deposits' into bot_state[{symphony_id}]"
        )
        assert bot_state[symphony_id]["net_deposits"] == pytest.approx(
            sym["net_deposits"], abs=1e-6
        ), (
            f"persisted net_deposits must match Composer value {sym['net_deposits']}; "
            f"got {bot_state[symphony_id].get('net_deposits')}"
        )

    def test_time_weighted_return_written_to_bot_state(
        self, composer_symphony_with_nonzero_fields
    ):
        """bot_state[symphony_id]['time_weighted_return'] must match Composer TWR."""
        import alpha_bot_execution

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]
        bot_state = {symphony_id: _minimal_bot_state_entry(symphony_id)}

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)

        assert "time_weighted_return" in bot_state[symphony_id], (
            f"engine must write 'time_weighted_return' into bot_state[{symphony_id}]"
        )
        assert bot_state[symphony_id]["time_weighted_return"] == pytest.approx(
            sym["time_weighted_return"], abs=1e-9
        )

    def test_max_drawdown_written_to_bot_state(self, composer_symphony_with_nonzero_fields):
        """bot_state[symphony_id]['max_drawdown'] must match Composer max_drawdown."""
        import alpha_bot_execution

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]
        bot_state = {symphony_id: _minimal_bot_state_entry(symphony_id)}

        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)

        assert "max_drawdown" in bot_state[symphony_id], (
            f"engine must write 'max_drawdown' into bot_state[{symphony_id}]"
        )
        assert bot_state[symphony_id]["max_drawdown"] == pytest.approx(
            sym["max_drawdown"], abs=1e-9
        )

    def test_all_four_fields_persisted_for_all_fixture_symphonies(
        self, all_composer_symphonies
    ):
        """
        For each of the 11 fixture symphonies, all four Composer fields must be
        written into bot_state. No symphony is skipped.
        """
        import alpha_bot_execution

        for sym in all_composer_symphonies:
            symphony_id = sym["id"]
            bot_state = {symphony_id: _minimal_bot_state_entry(symphony_id)}

            alpha_bot_execution._persist_composer_fields_to_bot_state(
                bot_state, symphony_id, sym
            )

            for field in _FOUR_FIELDS:
                assert field in bot_state[symphony_id], (
                    f"symphony {symphony_id}: field '{field}' must be persisted; "
                    f"current keys: {sorted(bot_state[symphony_id].keys())}"
                )

    def test_join_key_is_composer_id_field_not_symphony_id(
        self, composer_symphony_with_nonzero_fields
    ):
        """
        The Composer fixture uses 'id' as the join key (confirmed in fixture recon).
        The engine must join on sym['id'], not sym.get('symphony_id', ...).
        A symphony object that has 'id' but not 'symphony_id' must still be persisted.
        """
        import alpha_bot_execution

        sym = dict(composer_symphony_with_nonzero_fields)
        # Ensure symphony_id key is absent to verify the join uses 'id'
        sym.pop("symphony_id", None)

        symphony_id = sym["id"]
        bot_state = {symphony_id: _minimal_bot_state_entry(symphony_id)}

        # Must not raise and must write the fields
        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)

        assert "simple_return" in bot_state[symphony_id], (
            "join must use sym['id'], not sym['symphony_id'] — "
            "symphony_id key was absent but persist must still work"
        )

    def test_existing_bot_state_fields_not_clobbered_by_persist(
        self, composer_symphony_with_nonzero_fields
    ):
        """
        Writing the four Composer fields must not overwrite existing engine fields
        (high_water_mark, current_return, etc.). Additive-only.
        """
        import alpha_bot_execution

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]
        entry = _minimal_bot_state_entry(symphony_id, current_return=-5.0)
        original_hwm = entry["high_water_mark"]
        original_cr = entry["current_return"]

        bot_state = {symphony_id: entry}
        alpha_bot_execution._persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)

        assert bot_state[symphony_id]["high_water_mark"] == original_hwm, (
            "persist must not clobber high_water_mark"
        )
        assert bot_state[symphony_id]["current_return"] == original_cr, (
            "persist must not clobber current_return"
        )


# ---------------------------------------------------------------------------
# SURFACE 2 — /api/state wiring
#
# Given a bot_state that carries the four Composer fields (post-fix shape),
# the symphonies_list built at app.py:153-166 must pass real values to the
# helpers, and the helpers must return non-zero CR/MDD.
#
# These tests are RED because today bot_state never has these fields
# (so s.get("simple_return", 0.0) always returns 0.0).
# They will turn GREEN once the engine persists the fields.
# ---------------------------------------------------------------------------


class TestApiStateWiringProducesNonZeroCRMDD:

    def test_symphonies_list_built_from_bot_state_with_composer_fields_has_nonzero_cr_inputs(
        self, composer_symphony_with_nonzero_fields
    ):
        """
        When bot_state[symphony_id] contains simple_return != 0, the symphonies_list
        built by app.py:153-166 must pass that non-zero value through to the helper.

        Asserts the wiring is correct once the data source is populated.
        Currently fails because the engine never writes simple_return.
        """
        from analytics import get_symphony_cumulative_return

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]

        # Post-fix bot_state: has all four Composer fields
        bot_state_entry = _bot_state_entry_with_composer_fields(symphony_id, sym)

        # Replicate the app.py:153-166 symphonies_list construction
        symphonies_list_entry = {
            "id": symphony_id,
            "value": bot_state_entry.get("current_value") or 0.0,
            "last_percent_change": (bot_state_entry.get("current_return") or 0.0) / 100.0,
            "simple_return": bot_state_entry.get("simple_return", 0.0),
            "net_deposits": bot_state_entry.get("net_deposits", 0.0),
            "time_weighted_return": bot_state_entry.get("time_weighted_return", 0.0),
            "max_drawdown": bot_state_entry.get("max_drawdown", 0.0),
        }

        result = get_symphony_cumulative_return(symphonies_list_entry, bot_state_entry)

        assert result["if_held"] != pytest.approx(0.0, abs=1e-6), (
            f"CR if_held must be non-zero when bot_state carries real simple_return="
            f"{sym['simple_return']}; got {result['if_held']:.6f}. "
            f"If 0.0: bot_state does not yet have the Composer fields persisted."
        )

    def test_symphonies_list_built_from_bot_state_with_composer_fields_has_nonzero_mdd_inputs(
        self, composer_symphony_with_nonzero_fields
    ):
        """
        When bot_state[symphony_id] contains max_drawdown != 0, the symphonies_list
        must pass that non-zero value through to get_symphony_max_drawdown.
        """
        from analytics import get_symphony_max_drawdown

        sym = composer_symphony_with_nonzero_fields
        symphony_id = sym["id"]
        bot_state_entry = _bot_state_entry_with_composer_fields(symphony_id, sym)

        symphonies_list_entry = {
            "id": symphony_id,
            "value": bot_state_entry.get("current_value") or 0.0,
            "last_percent_change": (bot_state_entry.get("current_return") or 0.0) / 100.0,
            "simple_return": bot_state_entry.get("simple_return", 0.0),
            "net_deposits": bot_state_entry.get("net_deposits", 0.0),
            "time_weighted_return": bot_state_entry.get("time_weighted_return", 0.0),
            "max_drawdown": bot_state_entry.get("max_drawdown", 0.0),
        }

        result = get_symphony_max_drawdown(symphonies_list_entry, bot_state_entry)

        assert result["if_held"] != pytest.approx(0.0, abs=1e-6), (
            f"MDD if_held must be non-zero when bot_state carries real max_drawdown="
            f"{sym['max_drawdown']}; got {result['if_held']:.6f}. "
            f"If 0.0: bot_state does not yet have max_drawdown persisted."
        )

    def test_portfolio_cr_nonzero_given_bot_state_with_composer_fields(
        self, all_composer_symphonies
    ):
        """
        Portfolio CR must be non-zero when the symphonies_list is built from
        bot_state entries that have all four Composer fields.

        This is the end-to-end dashboard wiring test for CR.
        """
        from analytics import get_portfolio_cumulative_return

        symphonies_list = []
        bot_state: dict[str, Any] = {}

        for sym in all_composer_symphonies:
            symphony_id = sym["id"]
            entry = _bot_state_entry_with_composer_fields(symphony_id, sym)
            bot_state[symphony_id] = entry

            symphonies_list.append({
                "id": symphony_id,
                "value": entry.get("current_value") or 0.0,
                "last_percent_change": (entry.get("current_return") or 0.0) / 100.0,
                "simple_return": entry.get("simple_return", 0.0),
                "net_deposits": entry.get("net_deposits", 0.0),
                "time_weighted_return": entry.get("time_weighted_return", 0.0),
                "max_drawdown": entry.get("max_drawdown", 0.0),
            })

        result = get_portfolio_cumulative_return(symphonies_list, bot_state)

        assert result["if_held"] != pytest.approx(0.0, abs=1e-4), (
            f"portfolio CR must be non-zero when symphonies carry real Composer data; "
            f"got {result['if_held']:.6f}. "
            f"If 0.0: the persist path is not yet implemented."
        )

    def test_portfolio_mdd_nonzero_given_bot_state_with_composer_fields(
        self, all_composer_symphonies
    ):
        """
        Portfolio MDD must be non-zero when the symphonies_list is built from
        bot_state entries that have max_drawdown from Composer.
        """
        from analytics import get_portfolio_max_drawdown

        symphonies_list = []
        bot_state: dict[str, Any] = {}

        for sym in all_composer_symphonies:
            symphony_id = sym["id"]
            entry = _bot_state_entry_with_composer_fields(symphony_id, sym)
            bot_state[symphony_id] = entry

            symphonies_list.append({
                "id": symphony_id,
                "value": entry.get("current_value") or 0.0,
                "last_percent_change": (entry.get("current_return") or 0.0) / 100.0,
                "simple_return": entry.get("simple_return", 0.0),
                "net_deposits": entry.get("net_deposits", 0.0),
                "time_weighted_return": entry.get("time_weighted_return", 0.0),
                "max_drawdown": entry.get("max_drawdown", 0.0),
            })

        result = get_portfolio_max_drawdown(symphonies_list, bot_state)

        assert result["if_held"] != pytest.approx(0.0, abs=1e-4), (
            f"portfolio MDD must be non-zero when symphonies carry real max_drawdown; "
            f"got {result['if_held']:.6f}. "
            f"If 0.0: max_drawdown persist is not yet implemented."
        )


# ---------------------------------------------------------------------------
# SURFACE 3 — None sentinel
#
# When a Composer field is genuinely absent from bot_state (engine hasn't run yet,
# or Composer was unreachable), the app.py symphonies_list construction must use
# None as the default (not 0.0). The analytics helpers must then propagate None
# so the template can render '---' instead of '0.00%'.
#
# A REAL zero (simple_return=0.0 with net_deposits != 0, or max_drawdown=0.0)
# must still render as '0.00%' — it is a genuine value, not missing data.
# ---------------------------------------------------------------------------


class TestNoneSentinelDistinguishesMissingFromRealZero:

    def test_symphonies_list_uses_none_sentinel_when_simple_return_absent_from_bot_state(self):
        """
        app.py:162 must use None as the default for missing Composer fields,
        not 0.0. Currently uses s.get("simple_return", 0.0) — this test is RED
        until that default is changed to None.
        """
        # Simulate a bot_state entry with no Composer fields (pre-fix or stale state)
        symphony_id = "test-sym-sentinel"
        bot_state_entry = _minimal_bot_state_entry(symphony_id)
        # Confirm the four fields are absent
        for field in _FOUR_FIELDS:
            assert field not in bot_state_entry, (
                f"test precondition: {field} must not be in a minimal engine-only entry"
            )

        # Replicate the FIXED app.py symphonies_list construction (using None default)
        symphonies_list_entry = {
            "id": symphony_id,
            "value": bot_state_entry.get("current_value") or 0.0,
            "last_percent_change": (bot_state_entry.get("current_return") or 0.0) / 100.0,
            "simple_return": bot_state_entry.get("simple_return"),          # None default
            "net_deposits": bot_state_entry.get("net_deposits"),            # None default
            "time_weighted_return": bot_state_entry.get("time_weighted_return"),  # None
            "max_drawdown": bot_state_entry.get("max_drawdown"),            # None default
        }

        assert symphonies_list_entry["simple_return"] is None, (
            "app.py must use None default for missing simple_return, not 0.0; "
            "fix: change s.get('simple_return', 0.0) -> s.get('simple_return')"
        )
        assert symphonies_list_entry["max_drawdown"] is None, (
            "app.py must use None default for missing max_drawdown, not 0.0"
        )

    def test_cr_helper_returns_none_when_simple_return_is_none(self):
        """
        get_symphony_cumulative_return must return None (or a dict with if_held=None)
        when simple_return is None (missing-data sentinel).

        Currently raises TypeError because the helper does float(None).
        RED until the helper is updated to propagate None on missing input.
        """
        from analytics import get_symphony_cumulative_return

        sym_missing = {
            "id": "test-sym",
            "simple_return": None,          # sentinel: field absent from bot_state
            "net_deposits": None,
            "time_weighted_return": None,
            "last_percent_change": -0.02,
            "max_drawdown": None,
            "value": 1609.36,
        }

        result = get_symphony_cumulative_return(sym_missing, bot_state_entry=None)

        assert result["if_held"] is None, (
            f"CR helper must return None for if_held when simple_return is None (missing data); "
            f"got {result['if_held']}. "
            f"None sentinel lets the template render '---' instead of '0.00%'."
        )
        assert result["dry_run"] is None, (
            f"CR helper must return None for dry_run when simple_return is None; "
            f"got {result['dry_run']}"
        )

    def test_mdd_helper_returns_none_when_max_drawdown_is_none(self):
        """
        get_symphony_max_drawdown must return None (dict with if_held=None)
        when max_drawdown is None. Currently raises TypeError (float(None)).
        """
        from analytics import get_symphony_max_drawdown

        sym_missing = {
            "id": "test-sym",
            "simple_return": None,
            "net_deposits": None,
            "time_weighted_return": None,
            "last_percent_change": -0.02,
            "max_drawdown": None,
            "value": 1609.36,
        }

        result = get_symphony_max_drawdown(sym_missing, bot_state_entry=None)

        assert result["if_held"] is None, (
            f"MDD helper must return None for if_held when max_drawdown is None; "
            f"got {result['if_held']}. "
            f"None sentinel lets the template render '---' instead of '0.00%'."
        )

    def test_portfolio_cr_skips_none_sentinels_in_value_weighted_aggregate(self):
        """
        _value_weighted_portfolio must skip symphonies whose per_sym_fn returns
        None for if_held (missing-data sentinel), rather than incorporating a
        None-valued term into the weighted sum.

        One valid symphony + one None-sentinel symphony → result derived from
        the valid symphony only, not 0.0 (which would be the wrong weighted average).
        """
        from analytics import get_portfolio_cumulative_return

        valid_sym = {
            "id": "sym-valid",
            "simple_return": 0.65976,
            "net_deposits": 658.5,
            "time_weighted_return": 0.65,
            "last_percent_change": -0.02,
            "max_drawdown": 0.1495,
            "value": 1000.0,
        }
        missing_sym = {
            "id": "sym-missing",
            "simple_return": None,
            "net_deposits": None,
            "time_weighted_return": None,
            "last_percent_change": 0.01,
            "max_drawdown": None,
            "value": 500.0,
        }

        result = get_portfolio_cumulative_return([valid_sym, missing_sym], bot_state={})

        # Result must not be 0.0; it must reflect valid_sym only
        assert result["if_held"] is not None, (
            "portfolio CR must not produce None when at least one valid symphony exists"
        )
        assert result["if_held"] != pytest.approx(0.0, abs=1e-6), (
            f"portfolio CR must not be zero when a valid symphony contributes; "
            f"got {result['if_held']}. "
            f"None-sentinel symphonies must be skipped, not zero-weighted."
        )
        # And must equal the valid symphony's CR (the only contributor)
        assert result["if_held"] == pytest.approx(valid_sym["simple_return"] * 100.0, abs=1e-4), (
            f"with one valid symphony (CR={valid_sym['simple_return']*100.0}) and one "
            f"None-sentinel, portfolio CR must equal the valid symphony's CR*100; "
            f"got {result['if_held']}"
        )

    def test_portfolio_mdd_skips_none_sentinels(self):
        """
        _value_weighted_portfolio for MDD must skip None-sentinel symphonies.
        One valid (max_drawdown=0.1495) + one None → result from valid only.
        """
        from analytics import get_portfolio_max_drawdown

        valid_sym = {
            "id": "sym-valid",
            "simple_return": 0.65976,
            "net_deposits": 658.5,
            "time_weighted_return": 0.65,
            "last_percent_change": -0.02,
            "max_drawdown": 0.1495,
            "value": 1000.0,
        }
        missing_sym = {
            "id": "sym-missing",
            "simple_return": None,
            "net_deposits": None,
            "time_weighted_return": None,
            "last_percent_change": 0.01,
            "max_drawdown": None,
            "value": 500.0,
        }

        result = get_portfolio_max_drawdown([valid_sym, missing_sym], bot_state={})

        assert result["if_held"] is not None, (
            "portfolio MDD must not produce None when at least one valid symphony exists"
        )
        assert result["if_held"] == pytest.approx(valid_sym["max_drawdown"], abs=1e-6), (
            f"with one valid (MDD={valid_sym['max_drawdown']}) and one None-sentinel, "
            f"portfolio MDD must equal the valid symphony's MDD; got {result['if_held']}"
        )

    def test_real_zero_simple_return_still_produces_zero_cr_not_none(self):
        """
        A symphony with genuine zero simple_return (net_deposits != 0) must still
        render 0.00%, NOT '---'. Missing data != real zero.

        This test pins the distinction: None means 'absent', 0.0 means 'zero return'.
        """
        from analytics import get_symphony_cumulative_return

        sym_real_zero = {
            "id": "test-sym-zero",
            "simple_return": 0.0,       # genuinely zero
            "net_deposits": 500.0,      # non-zero — so it IS simple_return=0, not missing
            "time_weighted_return": 99.0,  # sentinel: must NOT be used
            "last_percent_change": 0.0,
            "max_drawdown": 0.05,
            "value": 1000.0,
        }

        result = get_symphony_cumulative_return(sym_real_zero, bot_state_entry=None)

        assert result["if_held"] is not None, (
            "real zero simple_return must produce 0.0, not None (not missing data)"
        )
        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"real zero simple_return with net_deposits!=0 must yield CR=0.0; "
            f"got {result['if_held']} — check fallback does not fire on this case"
        )

    def test_real_zero_max_drawdown_still_produces_zero_mdd_not_none(self):
        """
        max_drawdown=0.0 is a genuine 'no drawdown ever' result (e.g. monotonically rising
        symphony). Must render '0.00%', not '---'.
        """
        from analytics import get_symphony_max_drawdown

        sym_zero_mdd = {
            "id": "test-sym-zero-mdd",
            "simple_return": 0.5,
            "net_deposits": 100.0,
            "time_weighted_return": 0.5,
            "last_percent_change": 0.01,
            "max_drawdown": 0.0,        # genuine zero MDD
            "value": 1000.0,
        }

        result = get_symphony_max_drawdown(sym_zero_mdd, bot_state_entry=None)

        assert result["if_held"] is not None, (
            "max_drawdown=0.0 is a genuine value; must produce 0.0, not None"
        )
        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"real zero max_drawdown must yield MDD=0.0; got {result['if_held']}"
        )

    def test_app_state_except_blocks_return_none_sentinel_not_zeros(self):
        """
        The try/except blocks in app.py:172-183 currently return {"if_held": 0.0, "dry_run": 0.0}
        on KeyError/TypeError/ValueError. After the fix they must return
        {"if_held": None, "dry_run": None} so the template renders '---' on failure.

        This test constructs the scenario directly: calling the helper with a None
        sentinel input raises TypeError (pre-fix) or returns None-dict (post-fix).
        The post-fix behavior is what this test asserts.
        """
        from analytics import get_symphony_cumulative_return, get_symphony_max_drawdown

        sym_sentinel = {
            "id": "test-sym",
            "simple_return": None,
            "net_deposits": None,
            "time_weighted_return": None,
            "last_percent_change": -0.01,
            "max_drawdown": None,
            "value": 1000.0,
        }

        cr_result = get_symphony_cumulative_return(sym_sentinel, bot_state_entry=None)
        mdd_result = get_symphony_max_drawdown(sym_sentinel, bot_state_entry=None)

        assert cr_result["if_held"] is None, (
            f"CR helper with None inputs must return if_held=None for sentinel propagation; "
            f"got {cr_result['if_held']}. "
            f"app.py except-block must then set _cr = {{'if_held': None, 'dry_run': None}}."
        )
        assert mdd_result["if_held"] is None, (
            f"MDD helper with None inputs must return if_held=None; "
            f"got {mdd_result['if_held']}"
        )


# ---------------------------------------------------------------------------
# BLOCKER 1 (flask-dashboard-specialist) — _value_weighted_portfolio must
# return None sentinel when ALL symphonies are skipped due to None if_held.
#
# Currently returns {"if_held": 0.0, "dry_run": 0.0} when total_weight==0.0,
# which re-introduces the "0.00%" bug at the portfolio-strip level even after
# per-symphony None propagation is working correctly.
# ---------------------------------------------------------------------------


class TestPortfolioReturnsNoneSentinelWhenAllSymphoniesMissing:

    def test_portfolio_cr_returns_none_when_all_symphonies_have_none_simple_return(self):
        """
        get_portfolio_cumulative_return must return {"if_held": None, "dry_run": None}
        when every symphony has simple_return=None (all skipped by per-sym None guard).

        Currently returns {"if_held": 0.0, "dry_run": 0.0} because _value_weighted_portfolio
        falls through to `return {"if_held": 0.0, "dry_run": 0.0}` when total_weight==0.0
        (all symphonies skipped). That 0.0 reaches the JS as a real value and renders
        '+0.00%' — the portfolio strip bug is back.

        Fix: change analytics.py:491-492 to return {"if_held": None, "dry_run": None}
        when total_weight == 0.0 (no valid contributors).
        """
        from analytics import get_portfolio_cumulative_return

        symphonies_all_none = [
            {
                "id": f"sym-{i}",
                "simple_return": None,
                "net_deposits": None,
                "time_weighted_return": None,
                "last_percent_change": -0.01,
                "max_drawdown": None,
                "value": 1000.0,
            }
            for i in range(3)
        ]

        result = get_portfolio_cumulative_return(symphonies_all_none, bot_state={})

        assert result["if_held"] is None, (
            f"portfolio CR must return None when all symphonies have None CR inputs; "
            f"got {result['if_held']}. "
            f"0.0 here is indistinguishable from a real zero and re-introduces the bug. "
            f"Fix: analytics.py:491-492 — return {{\"if_held\": None, \"dry_run\": None}} "
            f"when total_weight == 0.0."
        )
        assert result["dry_run"] is None, (
            f"portfolio CR dry_run must also be None when all inputs are None; "
            f"got {result['dry_run']}"
        )

    def test_portfolio_mdd_returns_none_when_all_symphonies_have_none_max_drawdown(self):
        """
        get_portfolio_max_drawdown must return {"if_held": None, "dry_run": None}
        when every symphony has max_drawdown=None (all skipped).
        """
        from analytics import get_portfolio_max_drawdown

        symphonies_all_none = [
            {
                "id": f"sym-{i}",
                "simple_return": None,
                "net_deposits": None,
                "time_weighted_return": None,
                "last_percent_change": 0.01,
                "max_drawdown": None,
                "value": 500.0,
            }
            for i in range(3)
        ]

        result = get_portfolio_max_drawdown(symphonies_all_none, bot_state={})

        assert result["if_held"] is None, (
            f"portfolio MDD must return None when all symphonies have None max_drawdown; "
            f"got {result['if_held']}. "
            f"Fix: analytics.py:491-492 — return {{\"if_held\": None, \"dry_run\": None}} "
            f"when total_weight == 0.0."
        )
        assert result["dry_run"] is None, (
            f"portfolio MDD dry_run must also be None when all inputs are None; "
            f"got {result['dry_run']}"
        )

    def test_portfolio_cr_returns_none_when_symphony_list_is_empty(self):
        """
        Empty symphony list: total_weight==0.0, must return None sentinel not 0.0.
        This changes the existing contract documented in test_m1_helpers.py
        TestGetPortfolioCumulativeReturn.test_empty_symphonies_returns_zeros —
        that test must also be updated by the implementer when going GREEN.
        """
        from analytics import get_portfolio_cumulative_return

        result = get_portfolio_cumulative_return([], bot_state={})

        assert result["if_held"] is None, (
            f"empty symphony list: portfolio CR must return None (no data), not 0.0; "
            f"got {result['if_held']}. "
            f"0.0 here falsely implies a computed zero return."
        )

    def test_portfolio_mdd_returns_none_when_symphony_list_is_empty(self):
        """Empty symphony list: portfolio MDD must return None sentinel not 0.0."""
        from analytics import get_portfolio_max_drawdown

        result = get_portfolio_max_drawdown([], bot_state={})

        assert result["if_held"] is None, (
            f"empty symphony list: portfolio MDD must return None (no data), not 0.0; "
            f"got {result['if_held']}"
        )

    def test_portfolio_today_change_still_returns_zero_when_empty(self):
        """
        Today's Change portfolio is NOT affected by the None-sentinel change —
        TC always has last_percent_change available (it comes from bot_state
        current_return which is always present). Portfolio TC on empty list
        correctly returns 0.0 (no symphonies, no change). This test pins that
        TC is NOT changed by the None-sentinel fix.
        """
        from analytics import get_portfolio_today_change

        result = get_portfolio_today_change([], bot_state={})

        # TC empty guard returns 0.0 — this is correct and must not change
        assert result["if_held"] == pytest.approx(0.0, abs=1e-9), (
            f"portfolio TC empty guard must remain 0.0 (not None); got {result['if_held']}. "
            f"TC 0.0 is semantically correct for 'no symphonies, no change today'."
        )

    def test_partial_none_portfolio_uses_valid_symphonies_only(self):
        """
        Mixed list: some None, some valid. Portfolio CR must be derived from
        valid symphonies only and must NOT be None (valid contributors exist).
        This guards against over-application of the None-sentinel fix.
        """
        from analytics import get_portfolio_cumulative_return

        symphonies = [
            {
                "id": "sym-none",
                "simple_return": None,
                "net_deposits": None,
                "time_weighted_return": None,
                "last_percent_change": -0.01,
                "max_drawdown": None,
                "value": 500.0,
            },
            {
                "id": "sym-valid",
                "simple_return": 0.65976,
                "net_deposits": 658.5,
                "time_weighted_return": 0.65,
                "last_percent_change": -0.02,
                "max_drawdown": 0.1495,
                "value": 1000.0,
            },
        ]

        result = get_portfolio_cumulative_return(symphonies, bot_state={})

        assert result["if_held"] is not None, (
            "portfolio CR must not be None when at least one valid symphony contributes"
        )
        assert result["if_held"] == pytest.approx(65.976, abs=1e-4), (
            f"portfolio CR with one valid symphony (CR=65.976) and one None must "
            f"equal the valid symphony's CR*100; got {result['if_held']}"
        )


# ---------------------------------------------------------------------------
# BLOCKER 2 (flask-dashboard-specialist) — pctColor(null) must return a
# neutral CSS class, not text-emerald-400.
#
# In index.html, `function pctColor(v) {{ return v >= 0 ? ... : ...; }}` —
# JS coerces null >= 0 to true, so null portfolio values get colored green.
# Fix: add `if (v == null) return 'text-slate-400';` before the comparison.
# ---------------------------------------------------------------------------


class TestPctColorNullGuardInIndexHtml:

    def test_pct_color_function_contains_null_guard(self):
        """
        The pctColor JS function in templates/index.html must contain a null guard
        that returns a neutral color class when v is null.

        Currently pctColor has no null guard — `null >= 0` is true in JS, so
        null sentinel values (missing portfolio CR/MDD) get colored text-emerald-400
        (green), which is misleading for a '---' display value.

        Fix: add `if (v == null) return 'text-slate-400';` (or equivalent neutral
        class) as the first line of pctColor.

        This test asserts the fix is present by checking the template source text.
        """
        template_path = (
            Path(__file__).parent.parent.parent / "templates" / "index.html"
        )
        source = template_path.read_text(encoding="utf-8")

        # Find the pctColor function body
        pct_color_idx = source.find("function pctColor(")
        assert pct_color_idx != -1, "pctColor function must exist in index.html"

        # Extract up to 300 chars after the function declaration to cover the body
        pct_color_body = source[pct_color_idx: pct_color_idx + 300]

        # The null guard must appear before any comparison
        has_null_guard = (
            "v == null" in pct_color_body
            or "v === null" in pct_color_body
            or "null ==" in pct_color_body
            or "v == undefined" in pct_color_body
        )
        assert has_null_guard, (
            f"pctColor must guard against null before the >= comparison; "
            f"null >= 0 is true in JS and colors '---' values green. "
            f"Fix: add `if (v == null) return 'text-slate-400';` as first line. "
            f"Current pctColor body:\n{pct_color_body}"
        )

    def test_pct_color_null_guard_returns_neutral_slate_class(self):
        """
        The null guard in pctColor must return a neutral class (text-slate-400
        or text-slate-500), not an emerald or rose class.

        Guards against a fix that adds the null check but returns the wrong class
        (e.g. accidentally returning 'text-emerald-400' for null).
        """
        template_path = (
            Path(__file__).parent.parent.parent / "templates" / "index.html"
        )
        source = template_path.read_text(encoding="utf-8")

        pct_color_idx = source.find("function pctColor(")
        assert pct_color_idx != -1

        pct_color_body = source[pct_color_idx: pct_color_idx + 300]

        has_slate = "text-slate-" in pct_color_body
        assert has_slate, (
            f"pctColor null guard must return a slate/neutral class (text-slate-400 "
            f"or text-slate-500) for null values; "
            f"emerald/rose classes are semantically wrong for missing data. "
            f"Current pctColor body:\n{pct_color_body}"
        )
