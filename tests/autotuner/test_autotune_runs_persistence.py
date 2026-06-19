"""
Cycle P1 — RED: per-run Optuna validation metrics must be persisted to
``autotune_runs`` table so Claude's context-assembly can retrieve them.

Background
----------
``run_autotuner()`` computes five per-symphony validation signals on every run:

    oos_alpha           — OOS guard alpha for the AI-tuned proposal
    best_alpha_train    — train-window guard alpha (aliased as train_alpha below)
    baseline_decision   — "Adopted AI" | "Reverted to Fallback" |
                          "Reset to Global Default"
    fallback_oos_alpha  — OOS guard alpha for the fallback (current) params
    default_oos_alpha   — OOS guard alpha for the global DEFAULT_STRATEGY

These values are computed inside the per-symphony loop and then silently
discarded — only the cascade-selected ``current_params`` survive via
``save_symphony_strategy()``.  The config-surface research doc (§4) flags
this as the single biggest gap blocking the Claude advisor feature:

    "What's computed but never persisted anywhere: best_alpha_train,
     oos_alpha, fallback_oos_alpha, default_oos_alpha, ... baseline_decision.
     This is the single biggest gap for the feature."

P1 fixes this by:
  1. Adding an ``autotune_runs`` table to the state DB (``init_db()``).
  2. Providing a migration file (``migrations/002_autotune_runs.sql``).
  3. Having ``run_autotuner()`` INSERT one row per (run_timestamp, symphony)
     after each symphony's cascade completes.
  4. Exposing a ``get_latest_autotune_run(symphony_id)`` accessor so
     the Claude prompt-assembly layer can read the most-recent row.

All five tests in this file are RED — they assert behavior against code
that does not yet exist.  They will turn GREEN in the P1 implementer cycle.

Mocking strategy (inherits from test_oos_baseline_selection.py)
---------------------------------------------------------------
* ``optuna.create_study``               -> fake study with controlled
                                           ``best_params`` and ``best_value``
* ``synthetic_history.generate_synthetic_history``
                                         -> minimal 5-day fixture
* ``database.load_chart_history``        -> empty
* ``database.save_chart_archive``        -> no-op
* ``database.get_symphony_strategy``     -> known fallback + locked vars
* ``math_engine.compute_vwap_breakdown_update``
                                         -> side_effect per marker
* ``database.save_symphony_strategy``    -> real call so the DB actually
                                           writes (we redirect DB_FILE to
                                           tmp_path so the full DB path is
                                           exercised end-to-end, including
                                           ``autotune_runs`` INSERT)

``database.save_autotune_run`` (the new writer) and
``database.get_latest_autotune_run`` (the new reader) are exercised REAL
(not mocked) — that is the point of these tests.

Notes
-----
* No live APIs. No real Optuna storage. SQLite DB is redirected to tmp_path.
* Floats compared with ``pytest.approx`` + explicit tolerance comment.
* No module-level mutable state — isolated_db fixture has function scope.
* Lazy import of ``autotuner`` and ``database`` to avoid collection-time
  side effects from optuna.logging.set_verbosity (see test_oos_baseline_selection.py
  module docstring for full explanation).
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture: isolated, fresh SQLite DB per test
# Mirrors the pattern in tests/database/test_chart_aggregates.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Redirect database.DB_FILE to a per-test temp path and initialise schema.

    Function scope (default) — every test gets an empty DB.
    Monkeypatch is restored automatically after each test, so DB_FILE
    is never permanently changed in the module-level singleton.
    """
    import database as db_module  # lazy — see module docstring

    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Re-use the same minimal history / bot_state helpers as the OOS tests.
# ---------------------------------------------------------------------------


def _build_bot_state(symphony_name: str = "Test Symphony A") -> dict:
    """Single-symphony bot_state — minimum needed for run_autotuner to iterate."""
    return {
        "sym-1": {
            "name": symphony_name,
            "account_uuid": "acc-1",
        }
    }


def _build_history(n_days: int = 5) -> dict:
    """
    Minimal 5-day synthetic history.  80/20 split gives 4 train + 1 test day —
    enough for a non-empty OOS evaluation without real Optuna work.
    """
    tick = {
        "return": 2.0,
        "mc_prob": 50.0,
        "vol": 1.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, n_days + 1)]
    return {"sym-1": {d: [tick] for d in dates}}


def _fallback_params() -> dict:
    """Fallback params derived from the real DEFAULT_STRATEGY — no hardcoded values."""
    import database  # lazy import

    return database.DEFAULT_STRATEGY.copy()


def _ai_best_params() -> dict:
    """
    Valid Optuna best_params — includes all 7 OPTUNA_SEARCH_SPACE_KEYS
    so schema validation in run_autotuner passes.
    """
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 0.51,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _no_trigger_vwap_side_effect(**kwargs):
    """VWAP helper side effect that never fires a break — all param sets get alpha 0."""
    return (0, 0, False, False)


@contextlib.contextmanager
def _autotuner_patches(best_params: dict, fallback: dict, vwap_side_effect=None):
    """
    Wire the mocks needed for one run_autotuner invocation.

    Unlike test_oos_baseline_selection.py, we do NOT mock
    ``database.save_symphony_strategy`` or ``database.save_autotune_run``
    here — both are called real, against the tmp_path SQLite redirected
    by the isolated_db fixture.  This exercises the full DB write path.
    """
    if vwap_side_effect is None:
        vwap_side_effect = _no_trigger_vwap_side_effect

    import database  # lazy import

    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)

    history = _build_history(n_days=5)

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": fallback.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.DEFAULT_STRATEGY", database.DEFAULT_STRATEGY.copy()),
        patch("autotuner.math_engine.compute_vwap_breakdown_update", side_effect=vwap_side_effect),
    ):
        yield {"fake_study": fake_study}


def _make_phase1_spec_bundle() -> int:
    """Insert a minimal all-THEORY Phase-1 spec bundle and return its integer id.

    Required since run_autotuner now demands an explicit spec_bundle_id (NN1
    spec-freeze discipline — Phase-1 strict, no implicit defaults). This helper
    is called by _run_autotuner_via_patches so existing persistence tests remain
    valid without weakening the None-refusal guard.
    """
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)
    _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    for name, value in canon_facets.items():
        _db.insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name=name,
            facet_value=value,
            freeze_discipline="THEORY",
            justification="Phase-1 test fixture — all-THEORY bundle",
        )
    return bundle_id


def _run_autotuner_via_patches(best_params, fallback, vwap_side_effect=None):
    """Run run_autotuner through the patch harness; suppress stdout noise.

    Creates a minimal all-THEORY spec bundle so the NN1 spec-freeze guard
    (spec_bundle_id required, BACKTEST_SELECTION → hard fail) is satisfied.
    """
    import inspect

    import autotuner  # lazy import

    bot_state = _build_bot_state()
    spec_bundle_id = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    with _autotuner_patches(best_params, fallback, vwap_side_effect=vwap_side_effect):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"], **extra)
    return result, buf.getvalue()


# ===========================================================================
# Test 1 — autotune_runs table exists after init_db
# ===========================================================================


def test_autotune_runs_table_exists_after_init_db(isolated_db):
    """
    ``init_db()`` must CREATE the ``autotune_runs`` table.

    Schema must include at minimum the seven columns named in the P1 brief:
      run_timestamp, symphony_id, oos_alpha, train_alpha,
      baseline_decision, fallback_oos_alpha, default_oos_alpha.

    This test will FAIL (RED) until the CREATE TABLE statement is added to
    ``init_db()`` in ``database.py``.

    Column existence is verified by querying ``pragma_table_info`` so that
    the test fails on a missing column, not just a missing table.
    """
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()

    # Verify the table exists at all — sqlite_master lookup.
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='autotune_runs'")
    row = cursor.fetchone()
    assert row is not None, (
        "autotune_runs table must exist after init_db(); "
        "it was not found in sqlite_master. "
        "Add a CREATE TABLE IF NOT EXISTS autotune_runs (...) block to init_db()."
    )

    # Verify every required column is present.
    cursor.execute("PRAGMA table_info(autotune_runs)")
    col_names = {r[1] for r in cursor.fetchall()}
    conn.close()

    required_columns = {
        "run_timestamp",
        "symphony_id",
        "oos_alpha",
        "train_alpha",
        "baseline_decision",
        "fallback_oos_alpha",
        "default_oos_alpha",
    }
    missing = required_columns - col_names
    assert not missing, (
        f"autotune_runs table is missing required columns: {sorted(missing)}. "
        "All seven P1 metric columns must be present and NULLable per the "
        "project schema rule (additive-first, NULLable + DEFAULT)."
    )


# ===========================================================================
# Test 2 — migration file 002_autotune_runs.sql is well-formed
# ===========================================================================


def test_migration_002_creates_autotune_runs_table_idempotently(tmp_path):
    """
    ``migrations/002_autotune_runs.sql`` must exist, be valid SQL, and be
    idempotent (safe to run twice on a fresh DB that already has the base
    schema from ``init_db()``).

    Tested by:
      1. Creating an in-memory SQLite and running the base ``init_db()``
         schema (without ``autotune_runs``) to simulate a pre-migration DB.
      2. Reading the migration file and executing it once -> table must exist.
      3. Executing it again -> must not raise (idempotent via IF NOT EXISTS).

    This test will FAIL (RED) until ``migrations/002_autotune_runs.sql``
    exists with a correct ``CREATE TABLE IF NOT EXISTS autotune_runs`` statement.
    """
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parents[2]  # repo root from tests/autotuner/
        / "migrations"
        / "002_autotune_runs.sql"
    )
    assert migration_path.exists(), (
        f"Migration file not found at {migration_path}. "
        "Create migrations/002_autotune_runs.sql with a "
        "CREATE TABLE IF NOT EXISTS autotune_runs (...) statement."
    )

    sql = migration_path.read_text(encoding="utf-8")
    assert sql.strip(), "Migration file must not be empty."

    # Apply to in-memory DB (not the isolated_db fixture — we want a
    # controlled baseline without autotune_runs to prove migration adds it).
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    # Simulate the pre-P1 base schema (everything except autotune_runs).
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS bot_state (id INTEGER PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS execution_lock
            (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL);
        CREATE TABLE IF NOT EXISTS chart_history (id INTEGER PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS chart_archive
            (date TEXT, symphony_id TEXT, data TEXT, UNIQUE(date, symphony_id));
        CREATE TABLE IF NOT EXISTS symphony_strategies (
            symphony_name TEXT PRIMARY KEY,
            parameters TEXT,
            locked_vars TEXT
        );
    """)

    # First application of the migration — must succeed and create the table.
    try:
        cursor.executescript(sql)
    except sqlite3.Error as exc:
        pytest.fail(
            f"migrations/002_autotune_runs.sql raised a SQLite error on first "
            f"application: {exc}. The SQL must be syntactically valid."
        )

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='autotune_runs'")
    assert cursor.fetchone() is not None, (
        "After applying 002_autotune_runs.sql, autotune_runs table must exist."
    )

    # Second application — must be idempotent (IF NOT EXISTS).
    try:
        cursor.executescript(sql)
    except sqlite3.Error as exc:
        pytest.fail(
            f"migrations/002_autotune_runs.sql is not idempotent — raised on "
            f"second application: {exc}. Use CREATE TABLE IF NOT EXISTS."
        )

    conn.close()


# ===========================================================================
# Test 3 — run_autotuner writes one row per (run, symphony) to autotune_runs
# ===========================================================================


def test_run_autotuner_inserts_one_row_per_symphony_per_run(isolated_db):
    """
    After a mocked ``run_autotuner`` over one symphony, ``autotune_runs``
    must contain exactly one row for that symphony.

    The row must contain non-NULL values for all seven metric columns —
    no silent omission of any signal the Claude context-assembly needs.

    This test will FAIL (RED) until ``run_autotuner()`` calls
    ``database.save_autotune_run(run_timestamp, symphony_id, ...)`` (or
    equivalent) inside the per-symphony loop.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()

    _result, _stdout = _run_autotuner_via_patches(ai, fallback)

    # Inspect the real DB written by run_autotuner.
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT run_timestamp, symphony_id, oos_alpha, train_alpha, "
        "baseline_decision, fallback_oos_alpha, default_oos_alpha "
        "FROM autotune_runs"
    )
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1, (
        f"Expected exactly 1 row in autotune_runs after a single-symphony "
        f"run_autotuner call; found {len(rows)}. "
        "run_autotuner must INSERT one row per symphony per invocation."
    )

    (
        run_timestamp,
        symphony_id,
        oos_alpha,
        train_alpha,
        baseline_decision,
        fallback_oos_alpha,
        default_oos_alpha,
    ) = rows[0]

    assert run_timestamp is not None, "run_timestamp must be non-NULL"
    assert symphony_id is not None, "symphony_id must be non-NULL"

    # Metric values must be present (non-NULL) — shape/presence assertion,
    # not hardcoded value assertion, per feedback_no_hardcoded_test_values.
    assert oos_alpha is not None, (
        "oos_alpha must be persisted; it is the primary Claude confidence signal"
    )
    assert train_alpha is not None, "train_alpha (best_alpha_train) must be persisted"
    assert baseline_decision is not None, (
        "baseline_decision string must be persisted; "
        "Claude needs it to determine whether to defer to or supplement Optuna"
    )
    assert fallback_oos_alpha is not None, (
        "fallback_oos_alpha must be persisted; it is one leg of the OOS margin signal"
    )
    assert default_oos_alpha is not None, (
        "default_oos_alpha must be persisted; it is the other leg of the OOS margin signal"
    )

    # baseline_decision must be one of the three canonical strings from the
    # decision tree at autotuner.py:384-407.
    valid_decisions = {"Adopted AI", "Reverted to Fallback", "Reset to Global Default"}
    assert baseline_decision in valid_decisions, (
        f"baseline_decision must be one of {valid_decisions}; "
        f"got {baseline_decision!r}. "
        "The persisted value must match the exact string printed by run_autotuner."
    )

    # symphony_id must be the normalized form of the bot_state name.
    import database  # lazy import

    expected_sym_id = database.normalize_name("Test Symphony A")
    assert symphony_id == expected_sym_id, (
        f"Persisted symphony_id must be the normalized symphony name; "
        f"expected {expected_sym_id!r}, got {symphony_id!r}."
    )


# ===========================================================================
# Test 4 — get_latest_autotune_run accessor returns the most-recent row
# ===========================================================================


def test_get_latest_autotune_run_returns_most_recent_row_for_symphony(isolated_db):
    """
    ``database.get_latest_autotune_run(symphony_id)`` must return a dict
    containing the seven P1 metric keys for the most-recent row matching
    ``symphony_id``.

    Tested by writing two rows directly (simulating two runs on different
    timestamps) and asserting that the accessor returns the LATER one.

    This test will FAIL (RED) until:
      1. ``database.save_autotune_run(...)`` exists and writes to autotune_runs.
      2. ``database.get_latest_autotune_run(symphony_id)`` exists and reads
         the most-recent row ordered by ``run_timestamp DESC``.
    """
    import database as db_module  # lazy import

    symphony_id = "test symphony a"

    # Write two rows with different timestamps — the second is "most recent."
    db_module.save_autotune_run(
        run_timestamp="2026-05-10T16:00:00",
        symphony_id=symphony_id,
        oos_alpha=-0.5,
        train_alpha=1.2,
        baseline_decision="Reverted to Fallback",
        fallback_oos_alpha=0.3,
        default_oos_alpha=-1.0,
    )
    db_module.save_autotune_run(
        run_timestamp="2026-05-11T16:00:00",
        symphony_id=symphony_id,
        oos_alpha=1.5,
        train_alpha=3.0,
        baseline_decision="Adopted AI",
        fallback_oos_alpha=0.8,
        default_oos_alpha=0.2,
    )

    result = db_module.get_latest_autotune_run(symphony_id)

    assert result is not None, (
        f"get_latest_autotune_run({symphony_id!r}) must return a dict; "
        "got None. The accessor must return the most-recent row, not None."
    )
    assert isinstance(result, dict), (
        f"get_latest_autotune_run must return a dict; got {type(result)}"
    )

    # Must have all seven metric keys.
    required_keys = {
        "run_timestamp",
        "symphony_id",
        "oos_alpha",
        "train_alpha",
        "baseline_decision",
        "fallback_oos_alpha",
        "default_oos_alpha",
    }
    missing = required_keys - set(result.keys())
    assert not missing, (
        f"get_latest_autotune_run result is missing keys: {sorted(missing)}. "
        "The accessor must return all seven metric fields."
    )

    # Must be the MOST RECENT row — run_timestamp "2026-05-11..." wins.
    assert result["run_timestamp"] == "2026-05-11T16:00:00", (
        "get_latest_autotune_run must return the row with the most-recent "
        "run_timestamp (ORDER BY run_timestamp DESC LIMIT 1); "
        f"got run_timestamp={result['run_timestamp']!r} instead of the expected later row."
    )

    # Verify the baseline_decision from the later row is present — proves
    # we got the right row, not just a later timestamp with stale metrics.
    assert result["baseline_decision"] == "Adopted AI", (
        "baseline_decision must match the most-recent row's value; "
        f"got {result['baseline_decision']!r}. "
        "The accessor must return metrics from the same row as the run_timestamp."
    )


# ===========================================================================
# Test 5 — round-trip fidelity: values written == values read, no float drift
# ===========================================================================


def test_autotune_run_round_trip_no_precision_loss(isolated_db):
    """
    Float metrics written via ``save_autotune_run`` must come back unchanged
    via ``get_latest_autotune_run`` — no implicit coercion, rounding, or
    truncation from SQLite REAL storage.

    SQLite stores REAL as an IEEE-754 double (8-byte float), so values
    representable as Python floats round-trip without loss.  We verify this
    with a tolerance of 1e-9 (relative to the magnitude of each test value),
    which is well within the precision of 64-bit doubles and far tighter than
    any quant-meaningful threshold (the engine's OOS alphas are in the range
    -100 to +100 percent, so 1e-9 relative tolerance is ~1e-7 absolute —
    effectively zero drift).
    """
    import database as db_module  # lazy import

    symphony_id = "round-trip-sym"

    # Deliberately asymmetric values to catch sign/magnitude bugs.
    written_oos_alpha = -3.141592653589793
    written_train_alpha = 7.777777777777778
    written_baseline_decision = "Reset to Global Default"
    written_fallback_oos_alpha = -0.00001
    written_default_oos_alpha = 100.0

    db_module.save_autotune_run(
        run_timestamp="2026-05-12T16:00:00",
        symphony_id=symphony_id,
        oos_alpha=written_oos_alpha,
        train_alpha=written_train_alpha,
        baseline_decision=written_baseline_decision,
        fallback_oos_alpha=written_fallback_oos_alpha,
        default_oos_alpha=written_default_oos_alpha,
    )

    result = db_module.get_latest_autotune_run(symphony_id)

    assert result is not None, (
        "get_latest_autotune_run returned None; round-trip cannot be verified."
    )

    # Float tolerance: 1e-9 relative to the written value.
    # SQLite REAL is IEEE-754 double; no precision loss is expected for
    # values representable as Python floats.  1e-9 tolerance is documented
    # here as an explicit guard rather than an exact equality assertion because
    # future serialization changes (e.g., JSON encoding) could introduce
    # sub-epsilon drift that still meets the quant precision requirement.
    assert result["oos_alpha"] == pytest.approx(written_oos_alpha, rel=1e-9), (
        f"oos_alpha round-trip mismatch: wrote {written_oos_alpha}, "
        f"read back {result['oos_alpha']}. SQLite REAL must not lose precision."
    )
    assert result["train_alpha"] == pytest.approx(written_train_alpha, rel=1e-9), (
        f"train_alpha round-trip mismatch: wrote {written_train_alpha}, "
        f"read back {result['train_alpha']}."
    )
    assert result["fallback_oos_alpha"] == pytest.approx(
        written_fallback_oos_alpha,
        rel=1e-3,  # rel tolerance is large because value is near zero;
        # abs tolerance comment: ~1e-8 at this magnitude,
        # well within any quant-meaningful threshold
    ), (
        f"fallback_oos_alpha round-trip mismatch: wrote {written_fallback_oos_alpha}, "
        f"read back {result['fallback_oos_alpha']}."
    )
    assert result["default_oos_alpha"] == pytest.approx(written_default_oos_alpha, rel=1e-9), (
        f"default_oos_alpha round-trip mismatch: wrote {written_default_oos_alpha}, "
        f"read back {result['default_oos_alpha']}."
    )

    # String field must be exact.
    assert result["baseline_decision"] == written_baseline_decision, (
        f"baseline_decision round-trip mismatch: wrote {written_baseline_decision!r}, "
        f"read back {result['baseline_decision']!r}. "
        "String storage must be lossless."
    )
    assert result["symphony_id"] == symphony_id, (
        f"symphony_id round-trip mismatch: wrote {symphony_id!r}, "
        f"read back {result['symphony_id']!r}."
    )


# ===========================================================================
# Test 6 — get_latest_autotune_run returns None for an unknown symphony
# ===========================================================================


def test_get_latest_autotune_run_returns_none_for_unknown_symphony(isolated_db):
    """
    ``get_latest_autotune_run`` must return ``None`` (not raise, not return an
    empty dict) when no rows exist for the requested ``symphony_id``.

    This is the contract the Claude prompt-assembly will depend on:
    "if None, Optuna has not yet run for this symphony; omit the validation
    block from the context blob."

    This test also validates that the accessor does not accidentally return
    a row from a different symphony.
    """
    import database as db_module  # lazy import

    # Write a row for a different symphony to ensure the accessor does not
    # return a cross-symphony match.
    db_module.save_autotune_run(
        run_timestamp="2026-05-10T16:00:00",
        symphony_id="other-symphony",
        oos_alpha=1.0,
        train_alpha=2.0,
        baseline_decision="Adopted AI",
        fallback_oos_alpha=0.5,
        default_oos_alpha=0.1,
    )

    result = db_module.get_latest_autotune_run("nonexistent-symphony")

    assert result is None, (
        f"get_latest_autotune_run must return None when no rows exist for the "
        f"requested symphony_id; got {result!r}. "
        "Claude prompt-assembly uses None as the 'no history' sentinel."
    )
