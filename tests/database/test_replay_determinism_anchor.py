"""
RED tests for replay-determinism-anchor (Phase 1, plan §Golden-fixture tests).

Coverage maps to plan §Golden-fixture tests 1-5 plus two hostile extras:

  1. Bit-identical CVaR across two replays of the same cycle_id (§8 test 4 parity).
  2. read_cvar_diagnostic_for_cycle uses get_ro_connection(), not get_connection().
  3. _PARITY_DECISION_COLUMNS and _PARITY_EXCLUDE_COLUMNS are complementary
     subsets of cvar_diagnostics's column list (anti-omission / anti-overlap guard).
  4. Column-add reminder: PRAGMA table_info detects any unclassified new column and
     fails with a named-column message (anti-drift fence).
  5. No mc_seed column on cvar_diagnostics (anti-redundant-copy guard).
  6. (Hostile) Parallel calls with different seeds do not mutate shared state —
     two seeded MC runs interleaved must each reproduce their own mc_prob.
  7. (Hostile) Seed is never persisted — re-derived from cycle_id on every call;
     re-derivation across a simulated restart produces the same seed.

Fixture: tests/fixtures/database/replay_determinism_anchor/cvar_parity_seed.json

PA-18 compliance: no cvar_5pct or mc_prob literals asserted. Reproducibility is
always asserted as run_a == run_b. The fixture carries no producer-computed floats.

D-3 compliance: all RNG isolation assertions verify default_rng is used, never
global np.random state.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sqlite3
from unittest.mock import patch

import numpy as np
import pytest

import database as db
import math_engine

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "database"
    / "replay_determinism_anchor"
    / "cvar_parity_seed.json"
)


def _load_fixture() -> dict:
    with _FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# DB helpers: isolated cvar_diagnostics table
# ---------------------------------------------------------------------------

_CVAR_DIAGNOSTICS_DDL = """
CREATE TABLE IF NOT EXISTS cvar_diagnostics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id         TEXT    NOT NULL,
    ts_utc           TEXT    NOT NULL DEFAULT (datetime('now')),
    symphony_id      TEXT    NOT NULL,
    cvar_5pct        REAL    DEFAULT NULL,
    cvar_5pct_stderr REAL    DEFAULT NULL,
    cvar_n_tail      INTEGER DEFAULT NULL,
    cvar_5pct_long   REAL    DEFAULT NULL,
    cvar_n_tail_long INTEGER DEFAULT NULL
)
"""


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Isolated SQLite with cvar_diagnostics. Patches DB_PATH env so all
    database.py helpers target the temp file, not alphabot_state.db."""
    db_path = str(tmp_path / "rda_test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()
        conn = sqlite3.connect(db_path)
        conn.execute(_CVAR_DIAGNOSTICS_DDL)
        conn.commit()
        conn.close()
        yield db_path


# ---------------------------------------------------------------------------
# MC helpers
# ---------------------------------------------------------------------------


def _build_history(spec: dict) -> dict:
    """Expand fixture mc_inputs.historical_data_spec into the dict shape
    that run_monte_carlo consumes."""
    kind = spec["kind"]
    num_days = spec["num_days"]
    spy_amp = spec["spy_amplitude"]
    aaa_amp = spec["aaa_amplitude"]
    history: dict = {}

    if kind == "alternating":
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            month = 1 + i // 28
            day = 1 + i % 28
            key = f"2024-{month:02d}-{day:02d}"
            history[key] = {
                "SPY": {"daily_ret": sign * spy_amp},
                "AAA": {"daily_ret": sign * aaa_amp},
            }
    else:
        raise ValueError(f"Unknown historical_data_spec kind: {kind!r}")

    return history


def _run_mc_for_cycle(cycle_id: str, inputs: dict) -> float:
    """Derive seed from cycle_id and call run_monte_carlo, mirroring the
    production replay path: seed = derive_cycle_mc_seed(cycle_id)."""
    seed = math_engine.derive_cycle_mc_seed(cycle_id)
    history = _build_history(inputs["historical_data_spec"])
    result = math_engine.run_monte_carlo(
        inputs["holdings"],
        history,
        inputs["spy_today_return"],
        simulation_paths=inputs["simulation_paths"],
        neighbor_k=inputs["neighbor_k"],
        seed=seed,
    )
    return float(result)


# ---------------------------------------------------------------------------
# Test 1 — Bit-identical CVaR across two replays of the same cycle_id
# (plan §Golden-fixture tests #1 / synthesis §8 test 4 parity precondition)
# ---------------------------------------------------------------------------


def test_same_cycle_id_produces_bit_identical_mc_prob_across_two_replays() -> None:
    """Gate-1 parity precondition: replaying cycle_id_a twice must yield
    identical mc_prob values.

    The M2 CVaR estimate is bit-identically reproducible for the same cycle_id
    because:
      - derive_cycle_mc_seed is a pure function (SHA-256 → int, no I/O).
      - run_monte_carlo with a seed uses numpy.random.default_rng(seed) — isolated.

    PA-18: no expected float literal. We assert replay_a == replay_b.
    Tolerance: strict equality. The same RNG seed and same history must
    produce the exact same floating-point draws (numpy does not introduce
    non-determinism between identical default_rng sequences).
    """
    fixture = _load_fixture()
    cycle_id = fixture["cycle_id_a"]
    inputs = fixture["mc_inputs"]

    # Simulate two independent replay runs (different process starts → different
    # call stack, but same cycle_id → same seed → same RNG sequence).
    replay_a = _run_mc_for_cycle(cycle_id, inputs)
    replay_b = _run_mc_for_cycle(cycle_id, inputs)

    assert replay_a == replay_b, (
        f"Two replays of cycle_id='{cycle_id}' produced different mc_prob: "
        f"{replay_a!r} vs {replay_b!r}. "
        "Gate-1 parity precondition violated: same cycle_id must yield "
        "bit-identical mc_prob via derive_cycle_mc_seed + run_monte_carlo."
    )


# ---------------------------------------------------------------------------
# Test 2 — read_cvar_diagnostic_for_cycle uses get_ro_connection()
# (plan §Golden-fixture tests #2 / architecture constraint 3)
# ---------------------------------------------------------------------------


def test_read_cvar_diagnostic_for_cycle_exists_in_database() -> None:
    """database.read_cvar_diagnostic_for_cycle must be importable.
    Fails RED until the accessor is implemented."""
    assert hasattr(db, "read_cvar_diagnostic_for_cycle"), (
        "database.read_cvar_diagnostic_for_cycle is missing. "
        "Plan deliverable 2: implement this accessor using get_ro_connection()."
    )
    assert callable(db.read_cvar_diagnostic_for_cycle), (
        "database.read_cvar_diagnostic_for_cycle is not callable."
    )


def test_read_cvar_diagnostic_for_cycle_signature() -> None:
    """Accessor signature must accept (cycle_id, symphony_id) — both positional."""
    assert hasattr(db, "read_cvar_diagnostic_for_cycle"), (
        "read_cvar_diagnostic_for_cycle missing — see previous test."
    )
    sig = inspect.signature(db.read_cvar_diagnostic_for_cycle)
    params = list(sig.parameters.keys())
    assert "cycle_id" in params, (
        f"read_cvar_diagnostic_for_cycle missing 'cycle_id' param. Got: {params!r}."
    )
    assert "symphony_id" in params, (
        f"read_cvar_diagnostic_for_cycle missing 'symphony_id' param. Got: {params!r}."
    )


def test_read_cvar_diagnostic_for_cycle_uses_ro_connection_in_source() -> None:
    """Structural guard: the accessor's source must reference get_ro_connection.

    An implementation that uses get_connection() bypasses the read-only
    enforcement and could accidentally participate in a write transaction
    during a dashboard read. We verify the source reference — the WAL tests
    already verify the runtime behaviour.
    """
    assert hasattr(db, "read_cvar_diagnostic_for_cycle"), (
        "read_cvar_diagnostic_for_cycle missing — see previous test."
    )
    source = inspect.getsource(db.read_cvar_diagnostic_for_cycle)
    assert "get_ro_connection" in source, (
        "read_cvar_diagnostic_for_cycle does not call get_ro_connection(). "
        "Plan deliverable 2 requires the accessor uses the RO connection; "
        "using get_connection() would bypass read-only enforcement."
    )
    assert "get_connection()" not in source.replace("get_ro_connection()", ""), (
        "read_cvar_diagnostic_for_cycle calls get_connection() (read-write). "
        "Must use get_ro_connection() exclusively."
    )


def test_read_cvar_diagnostic_for_cycle_returns_row_for_existing_entry(isolated_db) -> None:
    """Accessor returns a row dict for a (cycle_id, symphony_id) pair that exists."""
    fixture = _load_fixture()
    cycle_id = fixture["cycle_id_a"]
    symphony_id = fixture["symphony_id"]

    # Write a row via the production writer so accessor and writer share the same schema.
    db.record_cvar_diagnostic(
        cycle_id=cycle_id,
        symphony_id=symphony_id,
        cvar_5pct=-0.03,
        cvar_5pct_stderr=0.005,
        cvar_n_tail=12,
        cvar_5pct_long=-0.04,
        cvar_n_tail_long=10,
        mode="replay",
    )

    row = db.read_cvar_diagnostic_for_cycle(cycle_id, symphony_id)

    assert row is not None, (
        f"read_cvar_diagnostic_for_cycle returned None for "
        f"('{cycle_id}', '{symphony_id}') after a successful write. "
        "Accessor must return the stored row."
    )
    # Assert the returned object has the parity columns — do not assert specific float values.
    for col in db._PARITY_DECISION_COLUMNS:
        assert col in row, (
            f"Row returned by read_cvar_diagnostic_for_cycle is missing "
            f"parity column '{col}'. All _PARITY_DECISION_COLUMNS must be present."
        )


def test_read_cvar_diagnostic_for_cycle_returns_none_for_missing_entry(isolated_db) -> None:
    """Accessor returns None when no row exists for the given (cycle_id, symphony_id).

    A missing row is not an error — the replay harness handles None as
    'no prior run to compare against'."""
    row = db.read_cvar_diagnostic_for_cycle("NONEXISTENT_CYCLE", "NONEXISTENT_SYM")
    assert row is None, (
        f"read_cvar_diagnostic_for_cycle returned {row!r} for a (cycle_id, symphony_id) "
        "pair that was never inserted. Expected None."
    )


# ---------------------------------------------------------------------------
# Test 3 — _PARITY_DECISION_COLUMNS and _PARITY_EXCLUDE_COLUMNS are
#           complementary subsets (plan §Golden-fixture tests #3)
# ---------------------------------------------------------------------------


def test_parity_decision_columns_constant_exists_in_database() -> None:
    """database._PARITY_DECISION_COLUMNS must be defined (plan deliverable 3)."""
    assert hasattr(db, "_PARITY_DECISION_COLUMNS"), (
        "database._PARITY_DECISION_COLUMNS is missing. Plan deliverable 3 requires this constant."
    )


def test_parity_exclude_columns_constant_exists_in_database() -> None:
    """database._PARITY_EXCLUDE_COLUMNS must be defined (plan deliverable 3)."""
    assert hasattr(db, "_PARITY_EXCLUDE_COLUMNS"), (
        "database._PARITY_EXCLUDE_COLUMNS is missing. Plan deliverable 3 requires this constant."
    )


def test_parity_decision_columns_contains_all_required_fields() -> None:
    """_PARITY_DECISION_COLUMNS must include every field listed in §A.8 A3 binding:
    cvar_5pct, cvar_5pct_stderr, cvar_n_tail, cvar_5pct_long, cvar_n_tail_long.

    Omitting cvar_5pct_long or cvar_n_tail_long would let a long-window
    regression silently pass Gate-1 parity (council §B.6 risk)."""
    required = {
        "cvar_5pct",
        "cvar_5pct_stderr",
        "cvar_n_tail",
        "cvar_5pct_long",
        "cvar_n_tail_long",
    }
    actual = set(db._PARITY_DECISION_COLUMNS)
    missing = required - actual
    assert not missing, (
        f"_PARITY_DECISION_COLUMNS is missing required fields: {sorted(missing)}. "
        "Per synthesis §A.8 A3, all five fields must be parity-asserted. "
        "Omitting cvar_5pct_long or cvar_n_tail_long creates a silent regression surface."
    )


def test_parity_exclude_columns_contains_id_and_ts_utc() -> None:
    """_PARITY_EXCLUDE_COLUMNS must contain 'id' and 'ts_utc'.

    'id' is autoincrement — a replay legitimately inserts a different row id.
    'ts_utc' is wall-clock — a replay produces a different timestamp.
    Including either in _PARITY_DECISION_COLUMNS would false-fail every replay."""
    exclude = set(db._PARITY_EXCLUDE_COLUMNS)
    assert "id" in exclude, (
        "'id' is missing from _PARITY_EXCLUDE_COLUMNS. "
        "autoincrement ids differ across replays and must be excluded from parity."
    )
    assert "ts_utc" in exclude, (
        "'ts_utc' is missing from _PARITY_EXCLUDE_COLUMNS. "
        "Wall-clock timestamps differ across replays and must be excluded from parity."
    )


def test_parity_decision_and_exclude_columns_are_disjoint() -> None:
    """_PARITY_DECISION_COLUMNS and _PARITY_EXCLUDE_COLUMNS must not overlap.

    A column in both sets would be simultaneously asserted and excluded — a
    contract contradiction. The plan (§risk callouts) names this explicitly."""
    decision_set = set(db._PARITY_DECISION_COLUMNS)
    exclude_set = set(db._PARITY_EXCLUDE_COLUMNS)
    overlap = decision_set & exclude_set
    assert not overlap, (
        f"Columns appear in both _PARITY_DECISION_COLUMNS and "
        f"_PARITY_EXCLUDE_COLUMNS: {sorted(overlap)}. "
        "Sets must be disjoint — a column cannot be both asserted and excluded."
    )


def test_parity_sets_are_subsets_of_known_cvar_diagnostics_columns() -> None:
    """Both parity sets must be subsets of the known cvar_diagnostics column list.

    Uses the fixture's expected_column_set to define the known schema. A column
    in _PARITY_DECISION_COLUMNS that is not in the actual table would silently
    KeyError at parity-assertion time — fail here instead."""
    fixture = _load_fixture()
    known_columns = set(fixture["expected_column_set"]["columns"])
    decision_set = set(db._PARITY_DECISION_COLUMNS)
    exclude_set = set(db._PARITY_EXCLUDE_COLUMNS)

    decision_unknown = decision_set - known_columns
    assert not decision_unknown, (
        f"_PARITY_DECISION_COLUMNS references columns not in the known "
        f"cvar_diagnostics schema: {sorted(decision_unknown)}. "
        "Update the fixture or remove the unknown column from the constant."
    )

    exclude_unknown = exclude_set - known_columns
    assert not exclude_unknown, (
        f"_PARITY_EXCLUDE_COLUMNS references columns not in the known "
        f"cvar_diagnostics schema: {sorted(exclude_unknown)}. "
        "Update the fixture or remove the unknown column from the constant."
    )


# ---------------------------------------------------------------------------
# Test 4 — Column-add reminder: unclassified columns detected at test time
# (plan §Golden-fixture tests #4 / anti-drift fence)
# ---------------------------------------------------------------------------


def test_cvar_diagnostics_has_no_unclassified_columns(isolated_db) -> None:
    """Every column on cvar_diagnostics must appear in either
    _PARITY_DECISION_COLUMNS or _PARITY_EXCLUDE_COLUMNS.

    This is the anti-drift fence: when a future migration adds a column, this
    test fails immediately with the unclassified column name. The developer
    must consciously decide whether it is a decision-content column
    (_PARITY_DECISION_COLUMNS) or a metadata column (_PARITY_EXCLUDE_COLUMNS).

    A column silently absent from both sets would slip through Gate-1 parity
    unchanged — a silent regression surface.

    Implementation uses PRAGMA table_info which is SQLite-native and requires
    no schema fixtures (schema-derived provenance, PA-18 compliant).
    """
    conn = sqlite3.connect(isolated_db)
    try:
        rows = conn.execute("PRAGMA table_info(cvar_diagnostics)").fetchall()
    finally:
        conn.close()

    assert rows, (
        "PRAGMA table_info(cvar_diagnostics) returned no rows. "
        "The table may not exist in the isolated_db fixture."
    )

    actual_columns = {row[1] for row in rows}  # column index 1 = name
    classified = set(db._PARITY_DECISION_COLUMNS) | set(db._PARITY_EXCLUDE_COLUMNS)
    unclassified = actual_columns - classified

    assert not unclassified, (
        f"cvar_diagnostics has unclassified column(s): {sorted(unclassified)}. "
        "Every column must appear in either _PARITY_DECISION_COLUMNS or "
        "_PARITY_EXCLUDE_COLUMNS. "
        "Classify the column before the next GREEN merge — a column absent from "
        "both sets creates a silent Gate-1 parity regression surface."
    )


# ---------------------------------------------------------------------------
# Test 5 — No mc_seed column on cvar_diagnostics
# (plan §Golden-fixture tests #5 / anti-redundant-copy guard)
# ---------------------------------------------------------------------------


def test_cvar_diagnostics_has_no_mc_seed_column(isolated_db) -> None:
    """cvar_diagnostics must NOT have an mc_seed column.

    The seed is a pure function of cycle_id (derive_cycle_mc_seed). Persisting
    it would create a drift surface: a stored seed that diverges from the
    function output would silently break replay reproducibility without any
    observable error. The plan §risk callouts identifies this as the single
    biggest risk and mandates this structural enforcement.

    Tests both the live table (via PRAGMA) and the DDL source in
    database.py — belt-and-suspenders: a migration that adds the column would
    trip PRAGMA; a future _CVAR_DIAGNOSTICS_DDL-like constant in database.py
    that includes mc_seed would trip the source check.
    """
    conn = sqlite3.connect(isolated_db)
    try:
        rows = conn.execute("PRAGMA table_info(cvar_diagnostics)").fetchall()
    finally:
        conn.close()

    column_names = {row[1] for row in rows}
    assert "mc_seed" not in column_names, (
        "cvar_diagnostics has an 'mc_seed' column. "
        "The seed is a pure function of cycle_id — persisting it creates a "
        "drift surface (stored seed can diverge from derive_cycle_mc_seed output). "
        "Plan §risk callouts forbids this column. Drop it and re-derive on replay."
    )


def test_database_source_contains_no_mc_seed_column_definition() -> None:
    """Grep-style guard: database.py source must not define mc_seed as a column.

    Covers the case where a future developer adds mc_seed to the in-module
    DDL string without running migrations, which would not be caught by the
    PRAGMA-based test above until init_db() is called.
    """
    db_source_path = pathlib.Path(inspect.getfile(db))
    source_text = db_source_path.read_text(encoding="utf-8")

    # Match any DDL-style mc_seed column definition or INSERT key.
    # Pattern: 'mc_seed' appearing as a column name in SQL context.
    import re

    mc_seed_defs = re.findall(
        r"\bmc_seed\b",
        source_text,
    )
    assert not mc_seed_defs, (
        f"database.py contains {len(mc_seed_defs)} reference(s) to 'mc_seed'. "
        "This column must never be added to cvar_diagnostics — the seed is "
        "re-derived from cycle_id via derive_cycle_mc_seed on every replay. "
        "Remove all mc_seed references."
    )


# ---------------------------------------------------------------------------
# Test 6 (Hostile) — Parallel seeds do not interfere with each other
# (D-3: no global RNG mutation; plan §Binding Hazards D-3)
# ---------------------------------------------------------------------------


def test_parallel_seeded_mc_runs_do_not_share_rng_state() -> None:
    """D-3 hazard: two cycle_ids seeded independently must each reproduce their
    own mc_prob when called in interleaved order (A→B→A→B vs A→A→B→B).

    A global-RNG implementation would produce cycle_id_a's second call with
    the RNG state left by cycle_id_b, yielding a different result. Only
    numpy.random.default_rng(seed) (isolated generator) preserves independence.

    PA-18: no expected literals. We assert that each cycle_id's mc_prob is
    identical across both orderings.
    """
    fixture = _load_fixture()
    cycle_id_a = fixture["cycle_id_a"]
    cycle_id_b = fixture["cycle_id_b"]
    inputs = fixture["mc_inputs"]

    # Interleaved: a, b, a, b
    result_a1 = _run_mc_for_cycle(cycle_id_a, inputs)
    result_b1 = _run_mc_for_cycle(cycle_id_b, inputs)
    result_a2 = _run_mc_for_cycle(cycle_id_a, inputs)
    result_b2 = _run_mc_for_cycle(cycle_id_b, inputs)

    assert result_a1 == result_a2, (
        f"cycle_id_a ('{cycle_id_a}') produced different mc_prob when interleaved "
        f"with cycle_id_b: first={result_a1!r}, second={result_a2!r}. "
        "D-3: per-call seeding via default_rng must isolate each run's RNG state."
    )
    assert result_b1 == result_b2, (
        f"cycle_id_b ('{cycle_id_b}') produced different mc_prob when interleaved "
        f"with cycle_id_a: first={result_b1!r}, second={result_b2!r}. "
        "D-3: per-call seeding via default_rng must isolate each run's RNG state."
    )


# ---------------------------------------------------------------------------
# Test 7 (Hostile) — Seed re-derivation across simulated daemon restart
# (plan §Golden-fixture tests #1 / BINDING HAZARDS D-3)
# ---------------------------------------------------------------------------


def test_seed_rederivation_after_daemon_restart_produces_same_mc_prob() -> None:
    """Simulates two separate daemon starts for the same cycle.

    Daemon start A: imports math_engine cold, derives seed, runs MC.
    Daemon start B: derives seed again (no shared process state), runs MC.
    Both must produce the same mc_prob — derive_cycle_mc_seed has no I/O or
    global state to diverge across restarts.

    This test verifies that seed non-persistence (plan §Why point 2) is safe:
    the re-derivation path is reliable enough that storing the seed would be
    redundant rather than necessary.

    PA-18: no expected float. We assert restart_a == restart_b.
    """
    fixture = _load_fixture()
    cycle_id = fixture["cycle_id_a"]
    inputs = fixture["mc_inputs"]

    # Daemon start A: derive seed fresh
    seed_a = math_engine.derive_cycle_mc_seed(cycle_id)

    # Daemon start B: simulate by re-importing or re-calling (same result — pure fn)
    # We explicitly re-call to document the intent.
    seed_b = math_engine.derive_cycle_mc_seed(cycle_id)

    assert seed_a == seed_b, (
        f"derive_cycle_mc_seed('{cycle_id}') returned different seeds on two "
        f"independent calls: {seed_a!r} vs {seed_b!r}. "
        "The function must be pure — no I/O, no global state."
    )

    history = _build_history(inputs["historical_data_spec"])

    result_restart_a = math_engine.run_monte_carlo(
        inputs["holdings"],
        history,
        inputs["spy_today_return"],
        simulation_paths=inputs["simulation_paths"],
        neighbor_k=inputs["neighbor_k"],
        seed=seed_a,
    )
    result_restart_b = math_engine.run_monte_carlo(
        inputs["holdings"],
        history,
        inputs["spy_today_return"],
        simulation_paths=inputs["simulation_paths"],
        neighbor_k=inputs["neighbor_k"],
        seed=seed_b,
    )

    assert float(result_restart_a) == float(result_restart_b), (
        f"MC replay for cycle_id='{cycle_id}' is not restart-stable: "
        f"restart_a={result_restart_a!r}, restart_b={result_restart_b!r}. "
        "The seed derivation or run_monte_carlo depends on process-global state."
    )


# ---------------------------------------------------------------------------
# Gate-1 parity accessor test — round-trip through write + read
# (verifies the accessor returns exactly the parity columns needed for replay)
# ---------------------------------------------------------------------------


def test_gate1_parity_round_trip_via_accessor(isolated_db) -> None:
    """Write a cvar_diagnostics row then read it back via read_cvar_diagnostic_for_cycle.

    Assert that the values of _PARITY_DECISION_COLUMNS in the round-tripped row
    match the values written. This is the contractual shape test: we do NOT assert
    specific float values — we assert that what was written is what is read back,
    and that all parity columns survive the round-trip.

    A row that stores cvar_5pct = -0.03 must return cvar_5pct = -0.03 (or
    None → None), etc. No hardcoded producer floats — the written values are
    the test-controlled inputs, not producer outputs (PA-18).
    """
    fixture = _load_fixture()
    cycle_id = fixture["cycle_id_b"]  # Use cycle_id_b to keep test isolation
    symphony_id = fixture["symphony_id"]

    # Test-controlled inputs — not producer-computed values.
    # Includes the migration-026 MC parity columns (mc_regime_match_mean_dist2
    # and mc_regime_match_suppressed) which the mc-parity-postinit cycle promoted
    # into _PARITY_DECISION_COLUMNS. Every column in _PARITY_DECISION_COLUMNS
    # must have an entry here or the iteration below KeyErrors.
    written = {
        "cvar_5pct": -0.05,
        "cvar_5pct_stderr": 0.008,
        "cvar_n_tail": 8,
        "cvar_5pct_long": -0.06,
        "cvar_n_tail_long": 7,
        "mc_regime_match_mean_dist2": 11.25,
        "mc_regime_match_suppressed": 1,
    }

    db.record_cvar_diagnostic(
        cycle_id=cycle_id,
        symphony_id=symphony_id,
        cvar_5pct=written["cvar_5pct"],
        cvar_5pct_stderr=written["cvar_5pct_stderr"],
        cvar_n_tail=written["cvar_n_tail"],
        cvar_5pct_long=written["cvar_5pct_long"],
        cvar_n_tail_long=written["cvar_n_tail_long"],
        mode="replay",
        mc_regime_match_mean_dist2=written["mc_regime_match_mean_dist2"],
        mc_regime_match_suppressed=written["mc_regime_match_suppressed"],
    )

    row = db.read_cvar_diagnostic_for_cycle(cycle_id, symphony_id)

    assert row is not None, (
        f"read_cvar_diagnostic_for_cycle returned None after writing a row for "
        f"('{cycle_id}', '{symphony_id}'). Accessor must return the stored row."
    )

    for col in db._PARITY_DECISION_COLUMNS:
        written_val = written[col]
        read_val = row[col]
        # Use pytest.approx for float columns (storage may introduce epsilon-level
        # float representation differences for REAL SQLite columns).
        # Tolerance 1e-9 relative: sufficient for financial diagnostic values stored
        # as IEEE 754 doubles; tighter than any meaningful CVaR precision requirement.
        if isinstance(written_val, float):
            assert read_val == pytest.approx(written_val, rel=1e-9), (
                f"Parity column '{col}' round-trip mismatch: "
                f"wrote {written_val!r}, read back {read_val!r}. "
                f"Tolerance: rel=1e-9 (IEEE 754 double storage should be exact; "
                "if this fails, check SQLite REAL precision)."
            )
        else:
            assert read_val == written_val, (
                f"Parity column '{col}' round-trip mismatch: "
                f"wrote {written_val!r}, read back {read_val!r}."
            )
