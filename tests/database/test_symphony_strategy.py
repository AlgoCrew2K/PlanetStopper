"""
Tests for get_symphony_strategy / save_symphony_strategy in database.py.

Coverage targets:
  1. First-seen auto-init returns DEFAULT_STRATEGY byte-for-byte.
  2. Auto-init persists the row in the DB.
  3. Round-trip preservation — save custom dict, fetch, equal.
  4. Name normalization contract (PRODUCTION GAP DOCUMENTED — see test body).
  5. Multiple symphonies are isolated (no cross-contamination).
  6. Schema field-count snapshot — canary for DEFAULT_STRATEGY drift.

DB isolation strategy:
  Each test gets a fresh SQLite file under tmp_path.  We monkey-patch
  database.DB_FILE before every test and restore it after, so the global
  `init_db()` at import time only pollutes the real file once (at first
  import); subsequent per-test init_db() calls hit the temp file.
"""

import json
import sqlite3

import pytest

import database as db_module
from database import (
    DEFAULT_LOCKED_VARS,
    DEFAULT_STRATEGY,
    get_symphony_strategy,
    init_db,
    normalize_name,
    save_symphony_strategy,
)

# ---------------------------------------------------------------------------
# Fixture: isolated, fresh SQLite database per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Redirect database.DB_FILE to a per-test temp file and initialise schema.

    Scope is function (default) so every test starts with an empty DB.
    The monkeypatch is restored automatically after each test.
    """
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    # Re-run init_db so the schema exists in the temp file.
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Helper: read the raw row from DB for inspection
# ---------------------------------------------------------------------------

def _fetch_raw_row(db_path: str, symphony_name: str):
    """Return (parameters_json, locked_vars_json) or None."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT parameters, locked_vars FROM symphony_strategies WHERE symphony_name = ?",
        (symphony_name,),
    )
    row = cursor.fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Test 1: first-seen auto-init returns DEFAULT_STRATEGY byte-for-byte
# ---------------------------------------------------------------------------

def test_first_seen_autoinit_returns_default_strategy():
    """
    Fetching an unseen symphony name must return DEFAULT_STRATEGY values
    without any modification — not a subset, not a superset.

    The assertion derives from DEFAULT_STRATEGY itself, never from a
    hardcoded literal, so future schema changes trip test 6 (canary) first.
    """
    result = get_symphony_strategy("brand-new-symphony")

    assert isinstance(result, dict), "return value must be a dict"
    assert "params" in result, "return dict must have 'params' key"
    assert "locked_vars" in result, "return dict must have 'locked_vars' key"

    # params must match DEFAULT_STRATEGY key-for-key and value-for-value
    assert result["params"] == DEFAULT_STRATEGY, (
        "auto-init params must equal DEFAULT_STRATEGY exactly; "
        "got divergence on keys: "
        f"{set(result['params'].keys()) ^ set(DEFAULT_STRATEGY.keys())}"
    )

    # locked_vars must match DEFAULT_LOCKED_VARS
    assert result["locked_vars"] == DEFAULT_LOCKED_VARS, (
        "auto-init locked_vars must equal DEFAULT_LOCKED_VARS exactly"
    )


# ---------------------------------------------------------------------------
# Test 2: auto-init persists the row in the DB
# ---------------------------------------------------------------------------

def test_first_seen_autoinit_persists_row(isolated_db):
    """
    After the auto-init path fires, the row must exist in symphony_strategies
    so the *next* call reads from the DB rather than re-seeding.
    """
    symphony_name = "persist-check-symphony"
    get_symphony_strategy(symphony_name)

    row = _fetch_raw_row(isolated_db, symphony_name)
    assert row is not None, (
        "auto-init must write a row to symphony_strategies; found None"
    )

    persisted_params = json.loads(row[0])
    persisted_locked = json.loads(row[1])

    assert persisted_params == DEFAULT_STRATEGY, (
        "persisted parameters must equal DEFAULT_STRATEGY"
    )
    assert persisted_locked == DEFAULT_LOCKED_VARS, (
        "persisted locked_vars must equal DEFAULT_LOCKED_VARS"
    )


# ---------------------------------------------------------------------------
# Test 3: round-trip preservation — save custom dict, fetch, equal
# ---------------------------------------------------------------------------

def test_round_trip_custom_strategy_preserved():
    """
    A strategy saved with non-default values must come back byte-for-byte.
    Values are chosen to differ from DEFAULT_STRATEGY on every key so a
    partial-merge bug is immediately visible.
    """
    custom_params = {k: v + 1.0 for k, v in DEFAULT_STRATEGY.items()}
    custom_locked = ["VWAP_BLEED_MULTIPLIER", "MAX_SQUEEZE_FLOOR"]
    symphony_name = "round-trip-symphony"

    save_symphony_strategy(symphony_name, custom_params, custom_locked)
    result = get_symphony_strategy(symphony_name)

    assert result["params"] == custom_params, (
        "fetched params must equal saved custom params exactly"
    )
    assert result["locked_vars"] == custom_locked, (
        "fetched locked_vars must equal saved custom locked_vars exactly"
    )


# ---------------------------------------------------------------------------
# Test 4: name normalization — PRODUCTION GAP documented
# ---------------------------------------------------------------------------

def test_name_normalization_gap_documented(isolated_db):
    """
    PRODUCTION GAP: get_symphony_strategy and save_symphony_strategy do NOT
    call normalize_name() internally.  The normalize_name() utility exists
    in database.py but is not applied on the DB key.

    This test ASSERTS THE CURRENT (BROKEN) BEHAVIOR so that any future fix
    (calling normalize_name internally) will cause this test to fail and
    prompt a reviewer to update the test to the correct contract.

    When the gap is fixed, this test should be replaced with one that
    asserts "My Symphony!" and its normalized form share the same row.

    Current observed behavior: the two name variants produce TWO independent
    rows with independent data.
    """
    raw_name = "My Symphony!"
    normalized = normalize_name(raw_name)  # produces "my symphony!"

    assert raw_name != normalized, (
        "precondition: raw_name and normalized must differ for this test to be meaningful"
    )

    # Save a custom strategy under the raw name
    custom_params = {k: v * 2.0 for k, v in DEFAULT_STRATEGY.items()}
    save_symphony_strategy(raw_name, custom_params, DEFAULT_LOCKED_VARS)

    # Fetch under the normalized name — with the gap, this auto-inits a
    # SECOND row and returns DEFAULT_STRATEGY, not the custom params.
    result_via_normalized = get_symphony_strategy(normalized)

    # CURRENT (BROKEN) BEHAVIOR: normalized lookup returns defaults, not custom
    assert result_via_normalized["params"] == DEFAULT_STRATEGY, (
        "PRODUCTION GAP CONFIRMED: fetching via normalized name returns DEFAULT_STRATEGY "
        "instead of the custom params saved under the raw name. "
        "Fix: normalize the key inside get_symphony_strategy and save_symphony_strategy."
    )

    # Verify both rows exist independently (two DB rows = the gap)
    raw_row = _fetch_raw_row(isolated_db, raw_name)
    norm_row = _fetch_raw_row(isolated_db, normalized)
    assert raw_row is not None, "row saved under raw name must exist"
    assert norm_row is not None, (
        "auto-init under normalized name created a second row — gap confirmed"
    )


# ---------------------------------------------------------------------------
# Test 5: multiple symphonies are isolated — no cross-contamination
# ---------------------------------------------------------------------------

def test_multiple_symphonies_are_isolated():
    """
    Saving a strategy for symphony-A must not affect what symphony-B returns,
    and vice versa.  Tests both directions of potential bleed.
    """
    params_a = {k: 1.0 for k in DEFAULT_STRATEGY}
    params_b = {k: 99.0 for k in DEFAULT_STRATEGY}

    save_symphony_strategy("symphony-alpha", params_a, [])
    save_symphony_strategy("symphony-beta", params_b, ["TRIGGER_THRESHOLD_PCT"])

    result_a = get_symphony_strategy("symphony-alpha")
    result_b = get_symphony_strategy("symphony-beta")

    assert result_a["params"] == params_a, (
        "symphony-alpha params contaminated by symphony-beta write"
    )
    assert result_b["params"] == params_b, (
        "symphony-beta params contaminated by symphony-alpha write"
    )
    assert result_a["locked_vars"] == [], (
        "symphony-alpha locked_vars contaminated"
    )
    assert result_b["locked_vars"] == ["TRIGGER_THRESHOLD_PCT"], (
        "symphony-beta locked_vars contaminated"
    )

    # Overwrite alpha and confirm beta is unaffected
    updated_params_a = {k: 42.0 for k in DEFAULT_STRATEGY}
    save_symphony_strategy("symphony-alpha", updated_params_a, [])

    result_b_after = get_symphony_strategy("symphony-beta")
    assert result_b_after["params"] == params_b, (
        "symphony-beta params changed after re-save of symphony-alpha — cross-contamination"
    )


# ---------------------------------------------------------------------------
# Test 6: schema field-count snapshot — canary for DEFAULT_STRATEGY drift
# ---------------------------------------------------------------------------

# CANARY: This set is the authoritative field list as of the time this test
# was written (2026-05-13).  Any addition or removal to DEFAULT_STRATEGY
# must be accompanied by an update here — that is the point of this test.
_EXPECTED_DEFAULT_STRATEGY_KEYS = frozenset({
    "TRIGGER_THRESHOLD_PCT",
    "TAKE_PROFIT_MC_PCT",
    "MAX_SQUEEZE_FLOOR",
    "VWAP_CROSS_HWM_PCT",
    "PARABOLIC_VELOCITY_THRESHOLD",
    "MAX_PARABOLIC_SQUEEZE",
    "VWAP_BLEED_MULTIPLIER",
    "VWAP_BLEED_TICKS",
})


def test_default_strategy_field_set_canary():
    """
    Pin the exact field set of DEFAULT_STRATEGY.

    If a field is added, removed, or renamed, this test fails and forces the
    author to consciously update the canary set above.  This prevents silent
    drift where a new parameter is introduced but its downstream consumers
    (autotuner, math_engine) are not updated to handle it.

    Does NOT assert values — only key presence and count.  Value assertions
    would violate the no-hardcoded-producer-values rule.
    """
    actual_keys = frozenset(DEFAULT_STRATEGY.keys())

    missing = _EXPECTED_DEFAULT_STRATEGY_KEYS - actual_keys
    extra = actual_keys - _EXPECTED_DEFAULT_STRATEGY_KEYS

    assert not missing, (
        f"Fields removed from DEFAULT_STRATEGY (update canary if intentional): {missing}"
    )
    assert not extra, (
        f"Fields added to DEFAULT_STRATEGY (update canary if intentional): {extra}"
    )

    # Count check as a secondary signal
    assert len(DEFAULT_STRATEGY) == len(_EXPECTED_DEFAULT_STRATEGY_KEYS), (
        f"DEFAULT_STRATEGY has {len(DEFAULT_STRATEGY)} fields; "
        f"canary expects {len(_EXPECTED_DEFAULT_STRATEGY_KEYS)}"
    )


# ---------------------------------------------------------------------------
# Test 7: all DEFAULT_STRATEGY values are positive floats (shape/property)
# ---------------------------------------------------------------------------

def test_default_strategy_all_values_are_positive():
    """
    Every DEFAULT_STRATEGY value must be numeric and strictly positive.
    A zero or negative default would silently disable a risk parameter.

    Asserts shape/property, not specific magnitudes — no hardcoded values.
    """
    for key, value in DEFAULT_STRATEGY.items():
        assert isinstance(value, (int, float)), (
            f"DEFAULT_STRATEGY['{key}'] must be numeric, got {type(value)}"
        )
        assert value > 0, (
            f"DEFAULT_STRATEGY['{key}'] = {value} is not strictly positive; "
            "a zero/negative default silently disables a risk parameter"
        )


# ---------------------------------------------------------------------------
# Test 8: second call after auto-init reads from DB, not re-seeds
# ---------------------------------------------------------------------------

def test_second_call_after_autoinit_reads_from_db(isolated_db):
    """
    After auto-init, overwriting the row directly in the DB and re-fetching
    must return the overwritten values — proving the second call goes to the
    DB rather than falling through to the seed path again.
    """
    symphony_name = "idempotent-init-check"

    # Trigger auto-init
    get_symphony_strategy(symphony_name)

    # Manually overwrite the row in the DB
    modified_params = {k: v + 100.0 for k, v in DEFAULT_STRATEGY.items()}
    conn = sqlite3.connect(isolated_db)
    conn.execute(
        "UPDATE symphony_strategies SET parameters = ? WHERE symphony_name = ?",
        (json.dumps(modified_params), symphony_name),
    )
    conn.commit()
    conn.close()

    # Second fetch must return the modified values, not DEFAULT_STRATEGY
    result = get_symphony_strategy(symphony_name)
    assert result["params"] == modified_params, (
        "second fetch after auto-init returned DEFAULT_STRATEGY instead of "
        "the DB row — the auto-init path is firing more than once"
    )
