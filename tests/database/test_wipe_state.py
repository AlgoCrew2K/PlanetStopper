"""
Regression tests for database.wipe_transient_state.

wipe_transient_state is HIGH-RISK: it resets all per-symphony trigger/armed/
breakeven fields on day boundary.  A silent bug here would carry stale trigger
state into the next trading session — false exits or missed exits.

Key contract points under test:
  - Transient keys are reset to their zero/empty sentinel values.
  - Trigger-related snapshot keys are DELETED (not zeroed) from the dict.
  - Non-transient keys (e.g. custom fields the caller may store alongside
    state) that are NOT in the transient key list are PRESERVED unchanged.
  - The function operates in-place AND returns the mutated dict (same object).
  - Multi-symphony state dicts: each symphony is wiped independently;
    symphonies that are plain non-dict values are skipped without error.

wipe_transient_state does NOT call load_state / save_state — it is a pure
dict transformer.  No DB isolation fixture is needed for these tests.
"""

from __future__ import annotations

import pytest

from database import wipe_transient_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Canonical set of transient keys that must be reset to a sentinel value.
# Derived by reading database.py lines 98-113 — do NOT hardcode sentinel
# values here; assert them by reading back from the returned dict.
_TRANSIENT_RESET_KEYS = frozenset(
    {
        "high_water_mark",
        "shadow_hwm",
        "prev_return",
        "armed",
        "tp_armed",
        "para_armed",
        "triggered",
        "breakeven_locked",
        "below_stop_count",
        "above_tp_count",
        "vwap_ticks",
        "vwap_bleed_ticks",
        "hwm_hold_ticks",
        "mc_history",
    }
)

# Canonical set of trigger-snapshot keys that must be DELETED (not zeroed).
# Derived from database.py lines 114-118.
_TRIGGER_DELETE_KEYS = frozenset(
    {
        "triggered_reason",
        "triggered_at_return",
        "triggered_at_hwm",
        "triggered_at_stop",
        "triggered_at_time",
        "trigger_prices",
        "triggered_basket_snapshot",
    }
)


def _make_armed_symphony() -> dict:
    """
    Build a per-symphony state dict that has every transient and trigger key
    populated with non-sentinel "dirty" values.  Used as seed for wipe tests.
    """
    return {
        # Transient reset keys — dirty values
        "high_water_mark": 1.12,
        "shadow_hwm": 1.08,
        "prev_return": 0.04,
        "armed": True,
        "tp_armed": True,
        "para_armed": True,
        "triggered": True,
        "breakeven_locked": True,
        "below_stop_count": 3,
        "above_tp_count": 2,
        "vwap_ticks": 7,
        "vwap_bleed_ticks": 4,
        "hwm_hold_ticks": 5,
        "mc_history": [0.55, 0.60, 0.58],
        # Trigger-delete keys
        "triggered_reason": "stop_loss",
        "triggered_at_return": -0.07,
        "triggered_at_hwm": 1.12,
        "triggered_at_stop": 0.95,
        "triggered_at_time": "2026-05-13T14:30:00",
        "trigger_prices": {"SPY": 450.0, "QQQ": 380.0},
        "triggered_basket_snapshot": {"SPY": 0.6, "QQQ": 0.4},
        # Non-transient preservable keys
        "last_seen_return": 0.03,
        "last_seen_nav": 1000.0,
        "custom_operator_tag": "momentum-v2",
    }


# ---------------------------------------------------------------------------
# Test 1: transient fields reset to sentinel values
# ---------------------------------------------------------------------------


def test_wipe_transient_state_resets_all_transient_keys():
    """
    After wipe_transient_state, every key in _TRANSIENT_RESET_KEYS must hold
    the sentinel value defined in database.py — not the dirty value seeded here.

    We assert the TYPE and falsiness/zero-ness of the sentinel, not a hardcoded
    literal, so that a future sentinel change (e.g. HWM initialised to 0.0
    instead of -999.0) trips only this test's shape assertion, not a silent pass.
    """
    state = {"sym1": _make_armed_symphony()}
    result = wipe_transient_state(state)
    sym = result["sym1"]

    # Boolean fields must be False
    for key in ("armed", "tp_armed", "para_armed", "triggered", "breakeven_locked"):
        assert sym[key] is False, (
            f"wipe_transient_state must set '{key}' to False; got {sym[key]!r}"
        )

    # Integer counter fields must be zero
    for key in (
        "below_stop_count",
        "above_tp_count",
        "vwap_ticks",
        "vwap_bleed_ticks",
        "hwm_hold_ticks",
    ):
        assert sym[key] == 0, f"wipe_transient_state must reset '{key}' to 0; got {sym[key]!r}"

    # mc_history must be an empty list (not None, not the old list)
    assert sym["mc_history"] == [], (
        f"wipe_transient_state must reset 'mc_history' to []; got {sym['mc_history']!r}"
    )

    # HWM sentinel values — must be numeric, and the production sentinel is -999.0.
    # Assert it is a negative float (sentinel semantics) rather than hardcoding -999.0
    # so that a future change to e.g. float('-inf') still passes shape assertions.
    for key in ("high_water_mark", "shadow_hwm"):
        assert isinstance(sym[key], (int, float)), (
            f"'{key}' sentinel must be numeric after wipe; got {type(sym[key])}"
        )
        assert sym[key] < 0, (
            f"'{key}' sentinel must be negative (HWM reset semantics); got {sym[key]}"
        )

    # prev_return must be None (E1 sentinel: no prior observation on a fresh day)
    # None signals to the velocity consumer that cycle-1 has no baseline, so velocity=0.
    # A value of 0.0 would produce false velocity = current_return on any positive-gap open.
    # See: tests/fixtures/database/e1_sentinel/01_new_day_reset_prev_return_is_none.json
    assert sym["prev_return"] is None, (
        f"wipe_transient_state must reset 'prev_return' to None (E1 sentinel); got {sym['prev_return']!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: trigger-snapshot keys are DELETED (not zeroed)
# ---------------------------------------------------------------------------


def test_wipe_transient_state_deletes_trigger_snapshot_keys():
    """
    Keys in _TRIGGER_DELETE_KEYS must be absent from the symphony dict after
    wipe — not zeroed, not set to None.  Presence of a stale 'trigger_prices'
    key (even with a falsy value) can cause downstream logic to mis-identify
    a trigger-in-progress state.
    """
    state = {"sym1": _make_armed_symphony()}
    result = wipe_transient_state(state)
    sym = result["sym1"]

    for key in _TRIGGER_DELETE_KEYS:
        assert key not in sym, (
            f"wipe_transient_state must DELETE '{key}' from the symphony dict; "
            f"found it still present with value {sym.get(key)!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: non-transient keys are PRESERVED
# ---------------------------------------------------------------------------


def test_wipe_transient_state_preserves_non_transient_keys():
    """
    Keys that are not in the transient or delete sets must survive wipe
    unchanged.  This pins the contract that wipe is surgical — it only touches
    the fields it is supposed to touch.

    Spot-checks: last_seen_return, last_seen_nav, custom_operator_tag.
    These represent the broader 'HWM, last_seen_*' family mentioned in the
    task brief.
    """
    seed = _make_armed_symphony()
    state = {"sym1": seed.copy()}  # copy so we can compare against original

    result = wipe_transient_state(state)
    sym = result["sym1"]

    for preserved_key in ("last_seen_return", "last_seen_nav", "custom_operator_tag"):
        assert preserved_key in sym, (
            f"wipe_transient_state must NOT delete '{preserved_key}'; key is missing"
        )
        assert sym[preserved_key] == seed[preserved_key], (
            f"wipe_transient_state must NOT modify '{preserved_key}'; "
            f"before={seed[preserved_key]!r}, after={sym[preserved_key]!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: return value is the same object (in-place mutation contract)
# ---------------------------------------------------------------------------


def test_wipe_transient_state_returns_same_dict_object():
    """
    wipe_transient_state must return the same dict object it received, not a
    copy.  Callers pass the live state dict and rely on the reference being
    the same object for subsequent save_state() calls.
    """
    state = {"sym1": _make_armed_symphony()}
    result = wipe_transient_state(state)

    assert result is state, (
        "wipe_transient_state must return the same dict object (in-place); "
        "a copy was returned instead — callers holding the original reference "
        "would see an un-wiped state"
    )


# ---------------------------------------------------------------------------
# Test 5: multi-symphony dict — each symphony is wiped independently
# ---------------------------------------------------------------------------


def test_wipe_transient_state_wipes_all_symphonies_independently():
    """
    When state contains multiple symphony IDs, every symphony dict must be
    wiped.  No cross-contamination — each symphony's non-transient fields
    must remain its own values.
    """
    state = {
        "sym_a": _make_armed_symphony(),
        "sym_b": _make_armed_symphony(),
    }
    # Give each symphony a distinct preservable value so cross-contamination
    # would be detectable
    state["sym_a"]["custom_operator_tag"] = "tag-alpha"
    state["sym_b"]["custom_operator_tag"] = "tag-beta"

    result = wipe_transient_state(state)

    for sym_id in ("sym_a", "sym_b"):
        sym = result[sym_id]
        # Transient reset
        assert sym["triggered"] is False, f"{sym_id}: 'triggered' must be False after wipe"
        assert sym["armed"] is False, f"{sym_id}: 'armed' must be False after wipe"
        # Trigger delete
        assert "trigger_prices" not in sym, f"{sym_id}: 'trigger_prices' must be deleted after wipe"
        assert "triggered_basket_snapshot" not in sym, (
            f"{sym_id}: 'triggered_basket_snapshot' must be deleted after wipe"
        )

    # Non-transient values must remain per-symphony (no bleed)
    assert result["sym_a"]["custom_operator_tag"] == "tag-alpha"
    assert result["sym_b"]["custom_operator_tag"] == "tag-beta"


# ---------------------------------------------------------------------------
# Test 6: non-dict values at top level are skipped (no crash)
# ---------------------------------------------------------------------------


def test_wipe_transient_state_skips_non_dict_top_level_values():
    """
    wipe_transient_state iterates state_dict.items() and guards with
    isinstance(s_data, dict).  Top-level values that are not dicts (e.g. a
    metadata timestamp string) must be skipped without raising.

    This is the negative-path case — the engine may store version or timestamp
    fields at the top level of state_dict.
    """
    state = {
        "last_updated": "2026-05-13T09:30:00",  # non-dict
        "sym1": _make_armed_symphony(),  # dict — will be wiped
    }

    # Must not raise
    result = wipe_transient_state(state)

    # Non-dict entry survives untouched
    assert result["last_updated"] == "2026-05-13T09:30:00"

    # The actual symphony entry is still wiped
    assert result["sym1"]["triggered"] is False


# ---------------------------------------------------------------------------
# Test 7: trigger-snapshot keys only deleted when present (no KeyError)
# ---------------------------------------------------------------------------


def test_wipe_transient_state_tolerates_missing_trigger_keys():
    """
    A symphony dict that does NOT contain any trigger-snapshot keys must still
    be wiped without raising KeyError.

    Scenario: a symphony that was never triggered — it has no 'trigger_prices'
    or 'triggered_basket_snapshot' keys at all.
    """
    minimal_state = {
        "armed": True,
        "triggered": False,
        "high_water_mark": 1.05,
        "shadow_hwm": 1.03,
        "prev_return": 0.02,
        "tp_armed": False,
        "para_armed": False,
        "breakeven_locked": False,
        "below_stop_count": 0,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "hwm_hold_ticks": 0,
        "mc_history": [],
    }
    state = {"sym_clean": minimal_state}

    # Must not raise
    result = wipe_transient_state(state)
    assert result["sym_clean"]["armed"] is False


# ---------------------------------------------------------------------------
# Test 8: empty state dict returns empty dict
# ---------------------------------------------------------------------------


def test_wipe_transient_state_empty_state_dict_is_idempotent():
    """
    An empty state dict must pass through without error and return an empty dict.
    This exercises the degenerate case where no symphonies have been loaded.
    """
    result = wipe_transient_state({})
    assert result == {}, "wipe_transient_state({}) must return {}; got non-empty result"
