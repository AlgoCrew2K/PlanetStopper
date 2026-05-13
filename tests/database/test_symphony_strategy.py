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
# Test 4: name normalization — variants must merge to a single row
# ---------------------------------------------------------------------------

def _count_all_rows(db_path: str) -> int:
    """Return total row count in symphony_strategies."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM symphony_strategies")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def test_name_normalization_merges_variants_to_same_row(isolated_db):
    """
    get_symphony_strategy and save_symphony_strategy must apply normalize_name()
    internally so that casing variants of the same symphony name map to a single
    DB row.

    Scenario A — save raw, fetch normalized:
      Save custom params under "My Symphony!" (raw mixed-case with punctuation).
      Fetch under normalize_name("My Symphony!") == "my symphony!".
      The fetched params must equal the saved custom params, NOT DEFAULT_STRATEGY.
      Only one row must exist in symphony_strategies after both operations.

    Scenario B — fetch normalized first, then save raw (inverse order):
      A fresh symphony is auto-inited via a normalized lookup.
      Then custom params are saved under the raw name variant.
      The re-fetch under the normalized form must return the custom params.
      Still only one row in the table.

    normalize_name() contract: name.strip().lower() — punctuation is preserved,
    so normalize_name("My Symphony!") == "my symphony!" (not "my symphony").
    """
    raw_name = "My Symphony!"
    normalized = normalize_name(raw_name)  # "my symphony!"

    # Precondition: the two forms must actually differ, otherwise the test is vacuous
    assert raw_name != normalized, (
        "precondition: raw_name and normalized must differ — "
        "if normalize_name is a no-op for this input, choose a different fixture"
    )

    # ------------------------------------------------------------------
    # Scenario A: save under raw name, fetch under normalized name
    # ------------------------------------------------------------------
    custom_params_a = {k: v * 2.0 for k, v in DEFAULT_STRATEGY.items()}

    save_symphony_strategy(raw_name, custom_params_a, DEFAULT_LOCKED_VARS)
    result_a = get_symphony_strategy(normalized)

    assert result_a["params"] == custom_params_a, (
        "Scenario A: fetching via normalized name must return the params saved "
        "under the raw name; got DEFAULT_STRATEGY — normalization is not applied "
        "inside get_symphony_strategy / save_symphony_strategy"
    )
    assert result_a["locked_vars"] == DEFAULT_LOCKED_VARS, (
        "Scenario A: fetching via normalized name must return the locked_vars "
        "saved under the raw name"
    )

    # One row only — no phantom duplicate created by the normalized lookup
    assert _count_all_rows(isolated_db) == 1, (
        "Scenario A: only one row must exist after save(raw) + get(normalized); "
        f"found {_count_all_rows(isolated_db)} rows — a duplicate was created"
    )

    # ------------------------------------------------------------------
    # Scenario B: fetch normalized first, then save under raw name
    # ------------------------------------------------------------------
    # Use a separate symphony name for a clean slate within the same isolated_db
    raw_name_b = "Tech Momentum  "   # trailing spaces — normalize strips them
    normalized_b = normalize_name(raw_name_b)  # "tech momentum"

    assert raw_name_b != normalized_b, (
        "precondition: raw_name_b and normalized_b must differ for Scenario B"
    )

    # Auto-init via normalized form
    result_b_init = get_symphony_strategy(normalized_b)
    assert result_b_init["params"] == DEFAULT_STRATEGY, (
        "Scenario B precondition: normalized auto-init must return DEFAULT_STRATEGY"
    )

    row_count_after_init = _count_all_rows(isolated_db)  # should be 2 total (A + B)

    # Now save custom params under the raw (un-normalized) form
    custom_params_b = {k: v * 3.0 for k, v in DEFAULT_STRATEGY.items()}
    save_symphony_strategy(raw_name_b, custom_params_b, [])

    # Fetch again under the normalized form — must see the custom params
    result_b_after_save = get_symphony_strategy(normalized_b)

    assert result_b_after_save["params"] == custom_params_b, (
        "Scenario B: after saving under raw name, fetching via normalized name "
        "must return the custom params — not DEFAULT_STRATEGY"
    )
    assert result_b_after_save["locked_vars"] == [], (
        "Scenario B: locked_vars must reflect the value saved under raw name"
    )

    # Row count must not have grown — save(raw) should UPDATE the existing row
    assert _count_all_rows(isolated_db) == row_count_after_init, (
        "Scenario B: save under raw name must UPDATE the existing normalized row, "
        f"not INSERT a new one; row count changed from {row_count_after_init} "
        f"to {_count_all_rows(isolated_db)}"
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
