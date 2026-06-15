"""
RED tests for the llm_suggestions audit table — task P3 (Claude AI Config Advisor).

Coverage targets (all tests must FAIL until the GREEN implementation lands):
  1.  Table existence after init_db — schema captures the full decision chain.
  2.  Migration file 003_llm_suggestions.sql exists, is idempotent, and is
      well-formed SQL.
  3a. record_llm_suggestion() inserts an ACCEPTED row (with before/after +
      OOS result fields populated).
  3b. record_llm_suggestion() inserts a REJECTED row (decision logged; before/
      after/OOS fields remain NULL).
  4a. get_suggestions_for_symphony() retrieves all rows for a given symphony.
  4b. get_suggestions_for_session() retrieves all rows for a given session_id.
  5.  Append-only integrity — no UPDATE or DELETE path exposed by the accessor
      layer.  Directly modifying an existing audit row via the public API must
      be impossible.
  6.  Round-trip fidelity — large JSON blobs in prompt_inputs and raw_response
      survive a write/read cycle byte-for-byte.
  7.  Schema field-count canary — pin the exact column set so future schema
      changes are conscious.

DB isolation strategy: identical to test_symphony_strategy.py — each test gets
a fresh SQLite file under tmp_path, with database.DB_FILE monkeypatched before
init_db() is called.

Accessor signatures proposed (binding contract for the GREEN phase):

    record_llm_suggestion(
        *,                          # keyword-only to prevent positional confusion
        session_id: str,
        created_at: str,            # UTC ISO-8601 string
        symphony_name: str,
        operator_identity: str,
        prompt_inputs: dict,        # JSON-serialisable
        model_id: str,
        generation_settings: dict,  # JSON-serialisable
        raw_response: dict,         # JSON-serialisable
        validation_results: dict,   # JSON-serialisable
        param_name: str,
        operator_decision: str,     # 'accepted' | 'rejected' | 'pending'
        decision_at: str | None = None,
        operator_note: str | None = None,
        before_value=None,          # JSON-serialisable scalar or None
        after_value=None,           # JSON-serialisable scalar or None
        oos_revalidation: dict | None = None,
    ) -> int                        # returns the new row id

    get_suggestions_for_symphony(
        symphony_name: str,
    ) -> list[dict]                 # list of row dicts, oldest-first

    get_suggestions_for_session(
        session_id: str,
    ) -> list[dict]                 # list of row dicts for one Claude call batch
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import database as db_module
from database import init_db

# ---------------------------------------------------------------------------
# Lazy imports for the accessors under test — they don't exist yet (RED phase).
# Each test imports them individually so that NameError surfaces as a test
# failure (not a collection error that would abort the whole module).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fixture: isolated, fresh SQLite DB per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Redirect database.DB_FILE to a per-test temp file and initialise schema.

    Scope is function (default) — every test starts with an empty DB.
    Monkeypatch is restored automatically after each test.
    """
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Shared test data helpers
# ---------------------------------------------------------------------------

_LARGE_PROMPT_INPUTS: dict = {
    "quant_snapshot": {
        "vol_20d": 0.18,
        "atr_14d_pct": 0.023,
        "mc_prob_benchmark": 0.67,
        "vwap_deviation": -0.005,
        "current_stop_distance": 0.12,
    },
    "optuna_evidence": {
        "train_alpha": 0.042,
        "oos_alpha": 0.019,
        "fallback_oos_alpha": 0.011,
        "default_oos_alpha": 0.008,
        "_baseline_chosen": "Adopted AI",
        "best_params": {
            "TAKE_PROFIT_MC_PCT": 4.5,
            "VWAP_CROSS_HWM_PCT": 1.2,
            "VWAP_BLEED_MULTIPLIER": 1.8,
            "VWAP_BLEED_TICKS": 12,
            "PARABOLIC_VELOCITY_THRESHOLD": 2.5,
            "MAX_PARABOLIC_SQUEEZE": 0.45,
        },
    },
    "param_table": [
        {
            "param": "TAKE_PROFIT_MC_PCT",
            "current": 5.0,
            "valid_range": [2.0, 10.0],
            "locked": False,
            "risk_direction": "loosens if raised",
        },
        {
            "param": "TRIGGER_THRESHOLD_PCT",
            "current": 15.0,
            "valid_range": [5.0, 25.0],
            "locked": True,
            "risk_direction": "tightens if raised",
        },
    ],
    "regime_context": {
        "vol_20d_window_range": [0.10, 0.31],
        "atr_14d_window_range": [0.012, 0.041],
        "regimes_present": ["low-vol trending", "mild drawdown"],
        "regimes_not_covered": ["flash crash", "gap-open > 3%"],
    },
}

_LARGE_RAW_RESPONSE: dict = {
    "analysis": (
        "Given the OOS-vs-train gap of 2.3pp, Optuna may have moderately overfit. "
        "The current TAKE_PROFIT_MC_PCT at 5.0 is above the walk-forward best of 4.5 "
        "but within a noisy range. Regime context is covered by the tuning window."
    ),
    "suggestions": [
        {
            "param": "TAKE_PROFIT_MC_PCT",
            "current": 5.0,
            "suggested": 4.5,
            "rationale": (
                "OOS alpha of 1.9% is positive but below train alpha of 4.2%, "
                "suggesting mild overfit. The walk-forward best of 4.5 is within "
                "range and has OOS validation support. Tightening slightly reduces "
                "exposure to false positives."
            ),
            "risk_direction": "tightens",
            "confidence": "medium",
            "data_sufficiency": "sufficient",
        }
    ],
}

_VALIDATION_RESULTS: dict = {
    "schema_valid": True,
    "suggestions_processed": 1,
    "rejected": [],
    "flagged": [],
    "passed": ["TAKE_PROFIT_MC_PCT"],
}

_GENERATION_SETTINGS: dict = {"temperature": 0.3, "max_tokens": 1024}


def _make_accepted_kwargs(session_id: str = "sess-001", symphony: str = "test-sym") -> dict:
    """Return a complete set of keyword args for an ACCEPTED suggestion record."""
    return {
        "session_id": session_id,
        "created_at": "2026-05-14T10:30:00Z",
        "symphony_name": symphony,
        "operator_identity": "pgreaney@example.com",
        "prompt_inputs": _LARGE_PROMPT_INPUTS,
        "model_id": "claude-sonnet-4-6",
        "generation_settings": _GENERATION_SETTINGS,
        "raw_response": _LARGE_RAW_RESPONSE,
        "validation_results": _VALIDATION_RESULTS,
        "param_name": "TAKE_PROFIT_MC_PCT",
        "operator_decision": "accepted",
        "decision_at": "2026-05-14T10:35:00Z",
        "operator_note": "Looks reasonable given OOS evidence.",
        "before_value": 5.0,
        "after_value": 4.5,
        "oos_revalidation": {"oos_alpha": 0.021, "passed": True},
    }


def _make_rejected_kwargs(session_id: str = "sess-001", symphony: str = "test-sym") -> dict:
    """Return keyword args for a REJECTED suggestion record (no before/after/OOS)."""
    return {
        "session_id": session_id,
        "created_at": "2026-05-14T10:30:00Z",
        "symphony_name": symphony,
        "operator_identity": "pgreaney@example.com",
        "prompt_inputs": _LARGE_PROMPT_INPUTS,
        "model_id": "claude-sonnet-4-6",
        "generation_settings": _GENERATION_SETTINGS,
        "raw_response": _LARGE_RAW_RESPONSE,
        "validation_results": _VALIDATION_RESULTS,
        "param_name": "VWAP_BLEED_TICKS",
        "operator_decision": "rejected",
        "decision_at": "2026-05-14T10:36:00Z",
        "operator_note": None,
        # before_value, after_value, oos_revalidation intentionally omitted (defaults to None)
    }


# ---------------------------------------------------------------------------
# Helper: raw SQL row fetch
# ---------------------------------------------------------------------------


def _fetch_all_rows(db_path: str) -> list[tuple]:
    """Return all rows from llm_suggestions as raw tuples."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM llm_suggestions ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows


def _count_rows(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM llm_suggestions")
    count = cursor.fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Test 1: table exists after init_db and has expected columns
# ---------------------------------------------------------------------------

# CANARY: This is the authoritative column set as of task P3 (2026-05-14).
# Any addition, removal, or rename to llm_suggestions MUST update this set —
# that is the purpose of this test.
_EXPECTED_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "session_id",
        "created_at",
        "symphony_name",
        "operator_identity",
        "prompt_inputs",
        "model_id",
        "generation_settings",
        "raw_response",
        "validation_results",
        "param_name",
        "operator_decision",
        "decision_at",
        "operator_note",
        "before_value",
        "after_value",
        "oos_revalidation",
    }
)


def test_llm_suggestions_table_exists_after_init_db(isolated_db):
    """
    init_db() must create the llm_suggestions table.

    Verifies via PRAGMA table_info — if the table does not exist, the PRAGMA
    returns zero rows, which fails the column-set assertion.
    """
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(llm_suggestions)")
    columns_info = cursor.fetchall()
    conn.close()

    assert columns_info, (
        "llm_suggestions table does not exist after init_db(); PRAGMA table_info returned no rows"
    )

    actual_columns = frozenset(row[1] for row in columns_info)

    missing = _EXPECTED_COLUMNS - actual_columns
    extra = actual_columns - _EXPECTED_COLUMNS

    assert not missing, (
        f"Columns missing from llm_suggestions: {missing}. "
        "Add them in the CREATE TABLE and update _EXPECTED_COLUMNS if intentional."
    )
    assert not extra, (
        f"Unexpected columns in llm_suggestions: {extra}. "
        "Update _EXPECTED_COLUMNS if these are intentional additions."
    )


def test_llm_suggestions_schema_canary_column_count(isolated_db):
    """
    Secondary canary: column COUNT must match the expected set size.

    Catches the edge case where a column is added and removed in the same
    migration leaving the set difference empty but the count wrong.
    """
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(llm_suggestions)")
    columns_info = cursor.fetchall()
    conn.close()

    assert len(columns_info) == len(_EXPECTED_COLUMNS), (
        f"llm_suggestions has {len(columns_info)} columns; "
        f"canary expects {len(_EXPECTED_COLUMNS)}. "
        "If you added or removed a column, update _EXPECTED_COLUMNS."
    )


def test_llm_suggestions_nullable_and_default_columns(isolated_db):
    """
    Audit-trail columns that may not be populated at insert time (decision_at,
    operator_note, before_value, after_value, oos_revalidation) must allow NULL.

    Additive-migration rule: NULLable + DEFAULT — never destructive.
    Verify via PRAGMA: notnull == 0 means nullable.
    """
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(llm_suggestions)")
    cols = {row[1]: {"notnull": row[3], "dflt_value": row[4]} for row in cursor.fetchall()}
    conn.close()

    nullable_columns = {
        "decision_at",
        "operator_note",
        "before_value",
        "after_value",
        "oos_revalidation",
    }
    for col in nullable_columns:
        assert col in cols, f"Expected column {col!r} not found in llm_suggestions"
        assert cols[col]["notnull"] == 0, (
            f"Column {col!r} must be NULLable (notnull=0) per the additive-migration rule; "
            f"got notnull={cols[col]['notnull']}"
        )


# ---------------------------------------------------------------------------
# Test 2: migration file exists, is idempotent, and is well-formed SQL
# ---------------------------------------------------------------------------

_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "003_llm_suggestions.sql"


def test_migration_003_file_exists():
    """
    The migration file migrations/003_llm_suggestions.sql must exist on disk.
    If it is missing, the GREEN implementer hasn't created it yet.
    """
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. Create migrations/003_llm_suggestions.sql."
    )


def test_migration_003_contains_create_table_if_not_exists():
    """
    The migration must use CREATE TABLE IF NOT EXISTS so it is safe to re-run
    (idempotency requirement from migrations/README.md).
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing — cannot check content"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    assert "CREATE TABLE IF NOT EXISTS" in sql, (
        "Migration must use CREATE TABLE IF NOT EXISTS for idempotency; "
        "bare CREATE TABLE would fail on re-run."
    )


def test_migration_003_targets_llm_suggestions_table():
    """
    The migration must create (or target) the llm_suggestions table by name.
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8").lower()
    assert "llm_suggestions" in sql, (
        "Migration file does not mention 'llm_suggestions'; "
        "check the table name in the CREATE TABLE statement."
    )


def test_migration_003_is_valid_sql_against_fresh_db(tmp_path):
    """
    Execute the migration SQL against a fresh in-memory SQLite DB.
    If the SQL is malformed, sqlite3 will raise an OperationalError, failing
    this test.  Run twice to prove idempotency (IF NOT EXISTS).
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")

    db_path = str(tmp_path / "migration_test.db")
    conn = sqlite3.connect(db_path)
    try:
        # First execution — table must be created
        conn.executescript(sql)
        conn.commit()

        # Second execution — must not raise (idempotency)
        conn.executescript(sql)
        conn.commit()
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f"Migration SQL is not valid or not idempotent: {exc}\n"
            f"Migration path: {_MIGRATION_PATH}"
        )
    finally:
        conn.close()

    # Confirm the table exists after migration
    conn2 = sqlite3.connect(db_path)
    cursor = conn2.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='llm_suggestions'")
    row = cursor.fetchone()
    conn2.close()

    assert row is not None, (
        "After running the migration, llm_suggestions table must exist in sqlite_master"
    )


# ---------------------------------------------------------------------------
# Test 3a: record_llm_suggestion inserts an ACCEPTED row
# ---------------------------------------------------------------------------


def test_record_accepted_suggestion_inserts_row(isolated_db):
    """
    Calling record_llm_suggestion() with operator_decision='accepted' must
    insert exactly one row into llm_suggestions.

    Verifies:
    - Return value is a positive integer (the new row id).
    - Row count increases by 1.
    - Stored operator_decision equals 'accepted'.
    - before_value and after_value are NOT NULL (accepted rows must carry them).
    """
    from database import record_llm_suggestion  # noqa: PLC0415

    row_id = record_llm_suggestion(**_make_accepted_kwargs())

    assert isinstance(row_id, int) and row_id > 0, (
        f"record_llm_suggestion must return a positive integer row id; got {row_id!r}"
    )
    assert _count_rows(isolated_db) == 1, (
        f"Expected 1 row after insert; found {_count_rows(isolated_db)}"
    )

    # Verify stored decision directly via SQL
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT operator_decision, before_value, after_value, oos_revalidation "
        "FROM llm_suggestions WHERE id = ?",
        (row_id,),
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None, f"Row with id={row_id} not found after insert"
    decision, before, after, oos = row

    assert decision == "accepted", f"Stored operator_decision must be 'accepted'; got {decision!r}"
    assert before is not None, "before_value must not be NULL for an accepted record"
    assert after is not None, "after_value must not be NULL for an accepted record"
    assert oos is not None, (
        "oos_revalidation must not be NULL for an accepted record that passed OOS gate"
    )


def test_record_accepted_suggestion_stores_correct_param_name(isolated_db):
    """
    The param_name field must be stored verbatim — it is the key used by
    downstream queries to group suggestions by parameter.
    """
    from database import record_llm_suggestion  # noqa: PLC0415

    kwargs = _make_accepted_kwargs()
    row_id = record_llm_suggestion(**kwargs)

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT param_name FROM llm_suggestions WHERE id = ?", (row_id,))
    stored_param = cursor.fetchone()[0]
    conn.close()

    assert stored_param == kwargs["param_name"], (
        f"param_name stored as {stored_param!r}; expected {kwargs['param_name']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3b: record_llm_suggestion inserts a REJECTED row
# ---------------------------------------------------------------------------


def test_record_rejected_suggestion_inserts_row(isolated_db):
    """
    Calling record_llm_suggestion() with operator_decision='rejected' must
    insert exactly one row.

    Rejected records must have before_value, after_value, and oos_revalidation
    all NULL — the operator did not apply the change, so there is no before/after.
    """
    from database import record_llm_suggestion  # noqa: PLC0415

    row_id = record_llm_suggestion(**_make_rejected_kwargs())

    assert isinstance(row_id, int) and row_id > 0, (
        f"record_llm_suggestion must return a positive integer row id; got {row_id!r}"
    )
    assert _count_rows(isolated_db) == 1, (
        f"Expected 1 row after insert; found {_count_rows(isolated_db)}"
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT operator_decision, before_value, after_value, oos_revalidation "
        "FROM llm_suggestions WHERE id = ?",
        (row_id,),
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None, f"Row with id={row_id} not found after insert"
    decision, before, after, oos = row

    assert decision == "rejected", f"Stored operator_decision must be 'rejected'; got {decision!r}"
    assert before is None, (
        "before_value must be NULL for a rejected record (no config change applied)"
    )
    assert after is None, (
        "after_value must be NULL for a rejected record (no config change applied)"
    )
    assert oos is None, "oos_revalidation must be NULL for a rejected record"


def test_record_multiple_suggestions_same_session(isolated_db):
    """
    Multiple suggestions from a single Claude call share a session_id.
    Both rows must be inserted; neither overwrites the other.
    """
    from database import record_llm_suggestion  # noqa: PLC0415

    session_id = "sess-multi"
    accepted_kwargs = _make_accepted_kwargs(session_id=session_id)
    rejected_kwargs = _make_rejected_kwargs(session_id=session_id)
    # Vary param_name so they represent different suggestions from the same call
    rejected_kwargs["param_name"] = "VWAP_BLEED_TICKS"

    id1 = record_llm_suggestion(**accepted_kwargs)
    id2 = record_llm_suggestion(**rejected_kwargs)

    assert id1 != id2, "Two inserts must produce distinct row ids"
    assert _count_rows(isolated_db) == 2, (
        f"Expected 2 rows for 2 suggestions; found {_count_rows(isolated_db)}"
    )

    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT session_id FROM llm_suggestions")
    sessions = [r[0] for r in cursor.fetchall()]
    conn.close()

    assert all(s == session_id for s in sessions), "Both rows must carry the shared session_id"


# ---------------------------------------------------------------------------
# Test 4a: get_suggestions_for_symphony
# ---------------------------------------------------------------------------


def test_get_suggestions_for_symphony_returns_matching_rows(isolated_db):
    """
    get_suggestions_for_symphony(symphony_name) must return all rows for that
    symphony and no rows for a different symphony.
    """
    from database import get_suggestions_for_symphony, record_llm_suggestion  # noqa: PLC0415

    sym_a = "symphony-alpha"
    sym_b = "symphony-beta"

    record_llm_suggestion(**_make_accepted_kwargs(symphony=sym_a))
    record_llm_suggestion(**_make_accepted_kwargs(session_id="sess-002", symphony=sym_a))
    record_llm_suggestion(**_make_rejected_kwargs(session_id="sess-003", symphony=sym_b))

    results_a = get_suggestions_for_symphony(sym_a)
    results_b = get_suggestions_for_symphony(sym_b)

    assert isinstance(results_a, list), (
        f"get_suggestions_for_symphony must return a list; got {type(results_a)}"
    )
    assert len(results_a) == 2, f"Expected 2 rows for {sym_a!r}; got {len(results_a)}"
    assert len(results_b) == 1, f"Expected 1 row for {sym_b!r}; got {len(results_b)}"

    for row in results_a:
        assert isinstance(row, dict), (
            "Each element returned by get_suggestions_for_symphony must be a dict"
        )
        assert row.get("symphony_name") == sym_a, (
            f"Returned row has symphony_name={row.get('symphony_name')!r}; expected {sym_a!r}"
        )


def test_get_suggestions_for_symphony_returns_empty_list_for_unknown(isolated_db):
    """
    Querying a symphony with no recorded suggestions must return an empty list,
    not None, not raise.
    """
    from database import get_suggestions_for_symphony  # noqa: PLC0415

    result = get_suggestions_for_symphony("no-such-symphony")

    assert isinstance(result, list), (
        f"get_suggestions_for_symphony must return a list for unknown symphony; got {type(result)}"
    )
    assert result == [], f"Expected empty list for unknown symphony; got {result!r}"


def test_get_suggestions_for_symphony_contains_required_keys(isolated_db):
    """
    Each dict returned by get_suggestions_for_symphony must contain at minimum
    the full audit-trail fields — shape assertion, not value assertion.
    """
    from database import get_suggestions_for_symphony, record_llm_suggestion  # noqa: PLC0415

    record_llm_suggestion(**_make_accepted_kwargs())
    results = get_suggestions_for_symphony("test-sym")

    assert results, "Precondition: at least one row must be returned"
    row = results[0]

    for col in _EXPECTED_COLUMNS:
        assert col in row, (
            f"Column {col!r} missing from dict returned by "
            f"get_suggestions_for_symphony; got keys: {sorted(row.keys())}"
        )


# ---------------------------------------------------------------------------
# Test 4b: get_suggestions_for_session
# ---------------------------------------------------------------------------


def test_get_suggestions_for_session_returns_rows_for_session(isolated_db):
    """
    get_suggestions_for_session(session_id) must return all rows sharing that
    session_id and exclude rows from a different session.
    """
    from database import get_suggestions_for_session, record_llm_suggestion  # noqa: PLC0415

    sess_x = "sess-x"
    sess_y = "sess-y"

    record_llm_suggestion(**_make_accepted_kwargs(session_id=sess_x))
    record_llm_suggestion(**_make_rejected_kwargs(session_id=sess_x))
    record_llm_suggestion(**_make_accepted_kwargs(session_id=sess_y))

    results_x = get_suggestions_for_session(sess_x)
    results_y = get_suggestions_for_session(sess_y)

    assert len(results_x) == 2, f"Expected 2 rows for session {sess_x!r}; got {len(results_x)}"
    assert len(results_y) == 1, f"Expected 1 row for session {sess_y!r}; got {len(results_y)}"

    for row in results_x:
        assert row.get("session_id") == sess_x, (
            f"Row session_id={row.get('session_id')!r} does not match query {sess_x!r}"
        )


def test_get_suggestions_for_session_returns_empty_list_for_unknown(isolated_db):
    """
    Querying an unknown session_id must return an empty list, not raise.
    """
    from database import get_suggestions_for_session  # noqa: PLC0415

    result = get_suggestions_for_session("no-such-session")
    assert isinstance(result, list) and result == [], (
        f"Expected [] for unknown session; got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: append-only integrity — no public update/delete path
# ---------------------------------------------------------------------------


def test_no_update_accessor_exists_in_database_module():
    """
    The public API must not expose a function that can mutate an existing
    llm_suggestions row.

    Approach: enumerate the names exported by database.py that contain
    'llm_suggestion' and assert none are prefixed with 'update_', 'edit_',
    'modify_', 'delete_', or 'remove_'.

    This catches both current and future accidental exposure of a mutation path.
    An audit trail that can be silently edited is not an audit trail.
    """
    import inspect  # noqa: PLC0415

    mutation_prefixes = ("update_llm", "edit_llm", "modify_llm", "delete_llm", "remove_llm")
    exposed_names = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if any(name.startswith(prefix) for prefix in mutation_prefixes)
    ]

    assert not exposed_names, (
        "database.py must not export mutation accessors for llm_suggestions; "
        f"found: {exposed_names}. "
        "The audit trail is append-only — existing rows are immutable."
    )


def test_append_only_row_count_never_decreases(isolated_db):
    """
    After inserting N rows, the row count must be exactly N.
    There is no delete path, so count must not decrease.

    This test inserts 3 rows and verifies count == 3 — it would catch a
    bug where an accessor silently replaces existing rows (e.g., INSERT OR
    REPLACE semantics that would reset rows with the same natural key).
    """
    from database import record_llm_suggestion  # noqa: PLC0415

    for i in range(3):
        kwargs = _make_accepted_kwargs(
            session_id=f"sess-append-{i}",
            symphony="monotonic-test-sym",
        )
        # Give each a distinct param_name so they differ meaningfully
        kwargs["param_name"] = f"PARAM_{i}"
        record_llm_suggestion(**kwargs)

    assert _count_rows(isolated_db) == 3, (
        f"Expected 3 rows after 3 inserts; found {_count_rows(isolated_db)}. "
        "An INSERT OR REPLACE on a non-surrogate key would silently drop rows."
    )


def test_record_llm_suggestion_ids_are_monotonically_increasing(isolated_db):
    """
    Row ids returned by record_llm_suggestion must strictly increase.
    This confirms AUTOINCREMENT semantics and that inserts are not silently
    updating / replacing rows.
    """
    from database import record_llm_suggestion  # noqa: PLC0415

    ids = []
    for i in range(4):
        kwargs = _make_rejected_kwargs(session_id=f"sess-mono-{i}")
        kwargs["param_name"] = f"PARAM_{i}"
        ids.append(record_llm_suggestion(**kwargs))

    for j in range(1, len(ids)):
        assert ids[j] > ids[j - 1], (
            f"Row ids must strictly increase; got ids={ids}. "
            "Non-monotonic ids indicate INSERT OR REPLACE semantics, which "
            "would allow rows to be silently overwritten."
        )


# ---------------------------------------------------------------------------
# Test 6: round-trip fidelity for large JSON blobs
# ---------------------------------------------------------------------------


def test_large_prompt_inputs_round_trip_exactly(isolated_db):
    """
    prompt_inputs is potentially the largest field — it carries the full quant
    snapshot, Optuna evidence, param table, and regime context.

    The JSON blob must survive write → read exactly (byte-for-byte after
    re-serialisation to a canonical form).  A lossy round-trip (e.g. float
    precision loss, key reordering) would make audit records unreliable.
    """
    from database import get_suggestions_for_session, record_llm_suggestion  # noqa: PLC0415

    kwargs = _make_accepted_kwargs(session_id="sess-roundtrip")
    row_id = record_llm_suggestion(**kwargs)

    results = get_suggestions_for_session("sess-roundtrip")
    assert results, f"Expected row with id={row_id} to be returned"

    row = results[0]

    # prompt_inputs may be returned as a dict (deserialized) or str (raw JSON).
    # Either way we normalise to dict for comparison.
    stored_prompt = row.get("prompt_inputs")
    if isinstance(stored_prompt, str):
        stored_prompt = json.loads(stored_prompt)

    assert stored_prompt == _LARGE_PROMPT_INPUTS, (
        "prompt_inputs did not round-trip exactly. "
        "Check that the accessor stores and retrieves the full JSON blob without truncation."
    )


def test_large_raw_response_round_trip_exactly(isolated_db):
    """
    raw_response carries the full Claude API response — analysis text + all
    suggestions.  It must round-trip without truncation or modification.
    """
    from database import get_suggestions_for_session, record_llm_suggestion  # noqa: PLC0415

    kwargs = _make_accepted_kwargs(session_id="sess-response-rt")
    record_llm_suggestion(**kwargs)

    results = get_suggestions_for_session("sess-response-rt")
    assert results, "Expected at least one row"

    stored_response = results[0].get("raw_response")
    if isinstance(stored_response, str):
        stored_response = json.loads(stored_response)

    assert stored_response == _LARGE_RAW_RESPONSE, (
        "raw_response did not round-trip exactly. "
        "Ensure the accessor stores the full JSON blob and the retrieval accessor "
        "returns it without truncation."
    )


def test_generation_settings_round_trip_exactly(isolated_db):
    """
    generation_settings (model temperature, max_tokens, etc.) must also
    survive the round-trip — these are part of the audit record of how the
    model was invoked.
    """
    from database import get_suggestions_for_session, record_llm_suggestion  # noqa: PLC0415

    kwargs = _make_accepted_kwargs(session_id="sess-gensettings")
    record_llm_suggestion(**kwargs)

    results = get_suggestions_for_session("sess-gensettings")
    stored = results[0].get("generation_settings")
    if isinstance(stored, str):
        stored = json.loads(stored)

    assert stored == _GENERATION_SETTINGS, (
        f"generation_settings did not round-trip; expected {_GENERATION_SETTINGS!r}, got {stored!r}"
    )


def test_before_and_after_value_round_trip_for_accepted(isolated_db):
    """
    before_value and after_value for accepted suggestions must round-trip
    without type coercion.  These are stored as JSON-encoded scalars; reading
    them back must yield the original Python values (not strings).
    """
    from database import get_suggestions_for_session, record_llm_suggestion  # noqa: PLC0415

    kwargs = _make_accepted_kwargs(session_id="sess-before-after")
    expected_before = kwargs["before_value"]  # 5.0
    expected_after = kwargs["after_value"]  # 4.5
    record_llm_suggestion(**kwargs)

    results = get_suggestions_for_session("sess-before-after")
    row = results[0]

    stored_before = row.get("before_value")
    stored_after = row.get("after_value")

    # Accept either raw scalar or JSON-encoded string — normalise both
    if isinstance(stored_before, str):
        stored_before = json.loads(stored_before)
    if isinstance(stored_after, str):
        stored_after = json.loads(stored_after)

    # Use pytest.approx for float comparison — float serialisation via JSON
    # is lossless for small values but approx guards against platform drift.
    # Tolerance 1e-9 is tight enough to catch unit confusion (e.g., 5.0 vs 0.05)
    # while ignoring sub-nanosecond float representation noise.
    assert stored_before == pytest.approx(expected_before, abs=1e-9), (
        f"before_value round-trip failed; expected {expected_before}, got {stored_before}"
    )
    assert stored_after == pytest.approx(expected_after, abs=1e-9), (
        f"after_value round-trip failed; expected {expected_after}, got {stored_after}"
    )


# ---------------------------------------------------------------------------
# Test 7: schema field-count canary (also covers Test 1's canary set)
# ---------------------------------------------------------------------------
# NOTE: The primary canary is _EXPECTED_COLUMNS at the top of this module and
# the two tests (test_llm_suggestions_table_exists_after_init_db,
# test_llm_suggestions_schema_canary_column_count) that pin it.
# This test adds a higher-level assertion: the accessor return dicts must also
# carry all expected keys, not just the raw DB schema.


def test_get_suggestions_for_symphony_row_dict_has_all_expected_keys(isolated_db):
    """
    The dicts returned by get_suggestions_for_symphony must carry ALL columns
    from _EXPECTED_COLUMNS — not a subset.

    This prevents the accessor from silently dropping audit fields (e.g. a
    SELECT of only a few columns instead of SELECT *).
    """
    from database import get_suggestions_for_symphony, record_llm_suggestion  # noqa: PLC0415

    record_llm_suggestion(**_make_accepted_kwargs())
    rows = get_suggestions_for_symphony("test-sym")

    assert rows, "Precondition: query must return at least one row"
    row = rows[0]

    missing_keys = _EXPECTED_COLUMNS - set(row.keys())
    assert not missing_keys, (
        f"get_suggestions_for_symphony returned a row dict missing columns: {missing_keys}. "
        "The accessor must return all audit columns, not a subset."
    )


def test_get_suggestions_for_session_row_dict_has_all_expected_keys(isolated_db):
    """
    Same canary for get_suggestions_for_session — the return dicts must carry
    all _EXPECTED_COLUMNS.
    """
    from database import get_suggestions_for_session, record_llm_suggestion  # noqa: PLC0415

    record_llm_suggestion(**_make_accepted_kwargs(session_id="sess-key-check"))
    rows = get_suggestions_for_session("sess-key-check")

    assert rows, "Precondition: query must return at least one row"
    row = rows[0]

    missing_keys = _EXPECTED_COLUMNS - set(row.keys())
    assert not missing_keys, (
        f"get_suggestions_for_session returned a row dict missing columns: {missing_keys}. "
        "The accessor must return all audit columns."
    )
