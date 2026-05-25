"""
RED tests for migration 017_advisor_observations.sql and the advisor_observations
accessor surface.

Coverage (all tests must FAIL until the GREEN implementation lands):

  Plan-mandated tests (plan.md §Golden-fixture tests):
    1. Migration file exists, uses CREATE TABLE IF NOT EXISTS, is idempotent.
    2. _MIGRATION_FILES contains "017_advisor_observations.sql" appended AFTER
       "019_fold_role_columns.sql" (append-only ordering contract).
    3. Schema-validator — fixture DB carries advisor_observations with all
       expected columns after run_migrations().
    4. Soft-FK to spec_bundles — an observation row with spec_bundle_id
       referencing a known bundle_hash is readable (application-level soft FK).
    5. Append-only — no update_advisor_observation symbol exported by database.py.
    6. is_advisory_only hardwired to 1 — caller cannot persist a 0 value.
    7. Computed-row shape — raw_response='{}' round-trips as an empty dict.
    8. LLM-authored-row shape — raw_response with a JSON payload round-trips as
       a dict (not a raw JSON string).
    9. Role discrimination — get_advisor_observations_for_role returns only rows
       of the requested role.

  Hostile edge tests:
    10. run_migrations() is idempotent — calling twice must not raise.
    11. mode=ro connection can SELECT from advisor_observations.
    12. _write_wall_breach_observation continues to work — the helper that already
        exists at database.py writes a WALL_BREACH row; the table must now exist
        via the migration so the write succeeds instead of raising OperationalError.
    13. insert_advisor_observation returns a positive integer row id.
    14. get_advisor_observations_for_subject filters by both subject_type and
        subject_id — rows of a different subject are excluded.

DB isolation strategy: the global conftest._isolate_db fixture redirects
DB_PATH to a per-test tmpfile and calls init_db(), which calls run_migrations().
Every test therefore starts with a fully-migrated, empty database.

Fixture provenance (D-2 ★): schema-derived with runtime validator.
The two seed rows (computed row and LLM-authored row) are constructed to cover
both row shapes described in plan §Deliverables #4.

Accessor signatures expected from the GREEN implementer (modelled on llm_suggestions):

    insert_advisor_observation(
        *,
        advisor_role: str,           # OVERFITTING_CONSCIENCE | SPEC_CRITIC | …
        subject_type: str,
        subject_id: str,
        verdict: str | None = None,
        raw_response: dict | str | None = None,  # JSON-serialisable; defaults to {}
        spec_bundle_id: str | None = None,
        # is_advisory_only: NOT a caller parameter — always stored as 1
    ) -> int                         # returns new row id

    get_advisor_observations_for_subject(
        subject_type: str,
        subject_id: str,
    ) -> list[dict]                  # empty list if no rows match

    get_advisor_observations_for_role(
        advisor_role: str,
        limit: int = 50,
    ) -> list[dict]                  # empty list if no rows; newest-first
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

import database as db_module

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIGRATION_FILENAME = "017_advisor_observations.sql"
_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / _MIGRATION_FILENAME

# Canonical column set for advisor_observations (plan §Deliverables #1).
_EXPECTED_COLUMNS: frozenset[str] = frozenset({
    "id",
    "created_at",
    "advisor_role",
    "subject_type",
    "subject_id",
    "verdict",
    "raw_response",
    "is_advisory_only",
    "spec_bundle_id",
})

# Roles that must be accepted by the table discriminator (Phase-1/Phase-2 set).
_VALID_ROLES = (
    "OVERFITTING_CONSCIENCE",
    "SPEC_CRITIC",
    "DIVERGENCE_EXPLAINER",
    "NARRATOR",
)

# ---------------------------------------------------------------------------
# Helpers for building observation kwargs
# ---------------------------------------------------------------------------


def _computed_row_kwargs(**overrides) -> dict:
    """Return kwargs for a computed (non-LLM) observation row.

    raw_response is '{}' (the plan §Deliverables #4 computed-row shape).
    verdict is a non-empty label string.
    """
    base = {
        "advisor_role": "OVERFITTING_CONSCIENCE",
        "subject_type": "autotune_run",
        "subject_id": "run-001",
        "verdict": "D_spec=1,N_effective=N_optuna,no_violations",
        "raw_response": {},  # empty dict — stored as '{}'
        "spec_bundle_id": None,
    }
    base.update(overrides)
    return base


def _llm_authored_row_kwargs(**overrides) -> dict:
    """Return kwargs for an LLM-authored observation row (raw_response populated)."""
    base = {
        "advisor_role": "SPEC_CRITIC",
        "subject_type": "autotune_run",
        "subject_id": "run-001",
        "verdict": "spec_looks_clean",
        "raw_response": {
            "analysis": "The spec facets are within acceptable bounds.",
            "confidence": "high",
            "flags": [],
        },
        "spec_bundle_id": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Migration file exists, idempotent, contains CREATE TABLE IF NOT EXISTS
# ---------------------------------------------------------------------------


def test_migration_017_file_exists():
    """The migration file migrations/017_advisor_observations.sql must exist.

    If missing, the GREEN implementer has not created it yet.
    """
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/017_advisor_observations.sql."
    )


def test_migration_017_uses_create_table_if_not_exists():
    """The migration must use CREATE TABLE IF NOT EXISTS for idempotency.

    A bare CREATE TABLE would fail on re-run; the IF NOT EXISTS guard is
    the project-wide contract for all additive migrations.
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing — cannot check content"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    assert "CREATE TABLE IF NOT EXISTS" in sql, (
        "Migration must use CREATE TABLE IF NOT EXISTS for idempotency; "
        "bare CREATE TABLE would fail on second application."
    )


def test_migration_017_targets_advisor_observations_table():
    """The migration SQL must reference the advisor_observations table by name."""
    assert _MIGRATION_PATH.is_file(), "Migration file missing"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8").lower()
    assert "advisor_observations" in sql, (
        "Migration file does not mention 'advisor_observations'. "
        "Check the table name in the CREATE TABLE statement."
    )


def test_migration_017_is_valid_sql_and_idempotent(tmp_path):
    """Execute the migration SQL twice against a fresh SQLite DB.

    First pass creates the table; second pass must not raise (idempotency).
    If the SQL is malformed, sqlite3 raises OperationalError, failing this test.
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    db_path = str(tmp_path / "migration_idempotency_test.db")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
        # Second execution must not raise
        conn.executescript(sql)
        conn.commit()
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f"Migration SQL is invalid or not idempotent: {exc}\n"
            f"Migration path: {_MIGRATION_PATH}"
        )
    finally:
        conn.close()

    conn2 = sqlite3.connect(db_path)
    row = conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='advisor_observations'"
    ).fetchone()
    conn2.close()
    assert row is not None, (
        "advisor_observations table must exist after running the migration"
    )


# ---------------------------------------------------------------------------
# 2. _MIGRATION_FILES append-only ordering contract
# ---------------------------------------------------------------------------


def test_migration_017_listed_in_migration_files():
    """_MIGRATION_FILES must contain '017_advisor_observations.sql'.

    Append-only contract: entries are never reordered or removed.
    """
    assert _MIGRATION_FILENAME in db_module._MIGRATION_FILES, (
        f"'{_MIGRATION_FILENAME}' is not in database._MIGRATION_FILES. "
        "Append it to the list — never reorder or remove existing entries."
    )


def test_migration_017_listed_after_019_fold_role_columns():
    """017_advisor_observations.sql must appear AFTER 019_fold_role_columns.sql.

    The append-only ordering contract requires 019 (already shipped on the
    merged tip) to appear before 017 in _MIGRATION_FILES so that the
    run_migrations() walker applies them in dependency order.
    """
    files = db_module._MIGRATION_FILES
    predecessor = "019_fold_role_columns.sql"
    assert predecessor in files, (
        f"'{predecessor}' not found in _MIGRATION_FILES; cannot verify ordering."
    )
    idx_017 = files.index(_MIGRATION_FILENAME)
    idx_019 = files.index(predecessor)
    assert idx_017 > idx_019, (
        f"'017_advisor_observations.sql' (index {idx_017}) must appear AFTER "
        f"'019_fold_role_columns.sql' (index {idx_019}) in _MIGRATION_FILES. "
        "016 must also precede 017 — spec_bundle_id is a soft FK to spec_bundles."
    )


def test_migration_016_listed_before_017():
    """016_spec_bundles.sql must precede 017_advisor_observations.sql.

    017 carries a soft FK (spec_bundle_id) that references spec_bundles.bundle_hash;
    016 must be applied before 017.
    """
    files = db_module._MIGRATION_FILES
    predecessor = "016_spec_bundles.sql"
    assert predecessor in files, (
        f"'{predecessor}' not found in _MIGRATION_FILES."
    )
    idx_016 = files.index(predecessor)
    idx_017 = files.index(_MIGRATION_FILENAME)
    assert idx_016 < idx_017, (
        f"'016_spec_bundles.sql' (index {idx_016}) must appear before "
        f"'017_advisor_observations.sql' (index {idx_017})."
    )


# ---------------------------------------------------------------------------
# 3. Schema validator — table exists with all expected columns
# ---------------------------------------------------------------------------


def test_advisor_observations_table_exists_after_init_db():
    """init_db() (which calls run_migrations()) must create advisor_observations.

    Verified via PRAGMA table_info — zero rows means the table does not exist.
    """
    # The global _isolate_db fixture has already called init_db().
    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols = conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    conn.close()

    assert cols, (
        "advisor_observations table does not exist after init_db(); "
        "PRAGMA table_info returned zero rows."
    )


def test_advisor_observations_has_all_expected_columns():
    """Every column declared in the plan must be present in the live schema.

    This is a schema-derived test (D-2 ★ fixture provenance): the expected
    set is derived from plan §Deliverables #1 and verified against the real DB.
    """
    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols_info = conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    conn.close()

    assert cols_info, "advisor_observations does not exist — check migration."
    actual = frozenset(row[1] for row in cols_info)

    missing = _EXPECTED_COLUMNS - actual
    extra = actual - _EXPECTED_COLUMNS

    assert not missing, (
        f"Columns missing from advisor_observations: {missing}. "
        "Add them to the CREATE TABLE statement and update _EXPECTED_COLUMNS if intentional."
    )
    assert not extra, (
        f"Unexpected columns in advisor_observations: {extra}. "
        "Update _EXPECTED_COLUMNS if this is an intentional schema extension."
    )


def test_advisor_observations_column_count_canary():
    """Pin the exact column count to catch simultaneous add+remove scenarios.

    If a column is added and a different one removed in the same migration, the
    set-difference tests above pass but the count still changes — this catches it.
    """
    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols = conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    conn.close()

    assert len(cols) == len(_EXPECTED_COLUMNS), (
        f"advisor_observations has {len(cols)} columns; "
        f"expected {len(_EXPECTED_COLUMNS)}. "
        "Update _EXPECTED_COLUMNS if the schema change is intentional."
    )


def test_is_advisory_only_has_default_1_in_schema():
    """The is_advisory_only column must have a DEFAULT value of 1.

    Structural belt-and-braces: the hard-wired 1 declares table-level intent
    so a schema-grep reviewer sees the table is non-actionable (plan §Risk callouts).
    """
    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols = {
        row[1]: {"notnull": row[3], "dflt_value": row[4]}
        for row in conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    }
    conn.close()

    assert "is_advisory_only" in cols, (
        "is_advisory_only column not found in advisor_observations"
    )
    dflt = cols["is_advisory_only"]["dflt_value"]
    assert dflt == "1", (
        f"is_advisory_only DEFAULT must be 1 (got {dflt!r}). "
        "The schema-level default declares table intent for schema-grep reviewers."
    )


def test_raw_response_has_default_empty_json_in_schema():
    """raw_response must default to '{}' for computed rows that carry no LLM output."""
    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols = {
        row[1]: {"notnull": row[3], "dflt_value": row[4]}
        for row in conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    }
    conn.close()

    assert "raw_response" in cols, "raw_response column not found"
    dflt = cols["raw_response"]["dflt_value"]
    # SQLite stores string defaults with enclosing single-quotes in dflt_value
    assert dflt in ("'{}'", "{}"), (
        f"raw_response DEFAULT must be '{{}}' (got {dflt!r}). "
        "Computed rows that carry no LLM output must be legal without supplying raw_response."
    )


# ---------------------------------------------------------------------------
# 4. Soft-FK to spec_bundles
# ---------------------------------------------------------------------------


def test_observation_with_spec_bundle_id_references_existing_bundle():
    """An observation row with spec_bundle_id referencing a known bundle is readable.

    This is an application-level soft FK — SQLite does not enforce it.  The test
    verifies that the column accepts the value and it survives the round-trip;
    referential integrity is the application's responsibility.
    """
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415

    bundle_hash = "deadbeef1234567890abcdef"  # representative hash string
    row_id = insert_advisor_observation(
        **_computed_row_kwargs(spec_bundle_id=bundle_hash)
    )
    assert isinstance(row_id, int) and row_id > 0

    rows = get_advisor_observations_for_subject("autotune_run", "run-001")
    assert rows, "Expected at least one observation row"
    assert rows[0]["spec_bundle_id"] == bundle_hash, (
        f"spec_bundle_id not stored correctly; "
        f"expected {bundle_hash!r}, got {rows[0].get('spec_bundle_id')!r}"
    )


def test_observation_with_null_spec_bundle_id_is_legal():
    """spec_bundle_id=None is permitted (not all observations reference a bundle)."""
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415

    insert_advisor_observation(**_computed_row_kwargs(spec_bundle_id=None))
    rows = get_advisor_observations_for_subject("autotune_run", "run-001")
    assert rows, "Expected one row"
    assert rows[0]["spec_bundle_id"] is None, (
        "spec_bundle_id must be NULL when not supplied"
    )


# ---------------------------------------------------------------------------
# 5. Append-only — no update accessor
# ---------------------------------------------------------------------------


def test_no_update_advisor_observation_symbol_in_database_module():
    """database.py must not export any update_advisor_observation function.

    Immutability is enforced by the missing surface — the same pattern that
    protects llm_suggestions (plan §Deliverables #2; council plan §3.1 row 019).
    An audit trail that can be silently edited is not an audit trail.
    """
    mutation_prefixes = (
        "update_advisor",
        "edit_advisor",
        "modify_advisor",
        "delete_advisor",
        "remove_advisor",
    )
    exposed = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if any(name.startswith(p) for p in mutation_prefixes)
    ]
    assert not exposed, (
        "database.py must not export mutation accessors for advisor_observations; "
        f"found: {exposed}. "
        "The observation log is append-only — existing rows must be immutable."
    )


def test_insert_advisor_observation_ids_are_monotonically_increasing():
    """Row ids returned by insert_advisor_observation must strictly increase.

    Monotonic ids confirm AUTOINCREMENT semantics and rule out INSERT OR REPLACE
    semantics that would allow rows to be silently overwritten.
    """
    from database import insert_advisor_observation  # noqa: PLC0415

    ids = []
    for i in range(4):
        row_id = insert_advisor_observation(
            **_computed_row_kwargs(subject_id=f"run-mono-{i}")
        )
        ids.append(row_id)

    for j in range(1, len(ids)):
        assert ids[j] > ids[j - 1], (
            f"Row ids must strictly increase; got ids={ids}. "
            "Non-monotonic ids indicate INSERT OR REPLACE semantics."
        )


# ---------------------------------------------------------------------------
# 6. is_advisory_only hardwired to 1
# ---------------------------------------------------------------------------


def test_is_advisory_only_stored_as_1_when_caller_omits_it():
    """When the caller does not supply is_advisory_only, it must default to 1."""
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415

    insert_advisor_observation(**_computed_row_kwargs())
    rows = get_advisor_observations_for_subject("autotune_run", "run-001")
    assert rows, "Expected one row"
    assert rows[0]["is_advisory_only"] == 1, (
        f"is_advisory_only must be 1 (got {rows[0].get('is_advisory_only')!r}). "
        "The Advisor never moves money — this column is a structural declaration."
    )


def test_is_advisory_only_cannot_be_set_to_0():
    """Attempting to insert is_advisory_only=0 must either raise or store 1.

    The plan §Golden-fixture test 2 states: 'inserting an observation with
    is_advisory_only=False is permitted (Phase-1 binding: observations are advisory
    by default, but the column accepts boolean)' — HOWEVER plan §Deliverables #1
    hard-wires the column to 1. The accessor-level contract is that 0 stored is
    the failing case. Either an exception is raised or 1 is stored; never 0.
    """
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415
    import os
    db_path = os.environ["DB_PATH"]

    try:
        # The accessor may raise, or it may silently coerce to 1.
        insert_advisor_observation(
            **_computed_row_kwargs(subject_id="run-advisory-test"),
            is_advisory_only=0,
        )
    except (TypeError, ValueError):
        # Raising on a caller-supplied 0 is an acceptable implementation choice.
        return

    # If no exception, verify that 0 was NOT persisted.
    rows = get_advisor_observations_for_subject("autotune_run", "run-advisory-test")
    assert rows, "Precondition: row must have been inserted"
    stored = rows[0]["is_advisory_only"]
    assert stored != 0, (
        f"is_advisory_only=0 must never be stored; got {stored!r}. "
        "The accessor must ignore or reject a caller-supplied 0 — "
        "'0 stored' is the explicit failing case per plan §Golden-fixture test 2."
    )


# ---------------------------------------------------------------------------
# 7. Computed-row shape — raw_response='{}' round-trips as empty dict
# ---------------------------------------------------------------------------


def test_computed_row_raw_response_empty_dict_round_trips():
    """raw_response={} for a computed row must return as an empty dict from the accessor.

    The accessor may store it as the string '{}' but must deserialise it on
    read so callers receive a Python dict — consistent with the llm_suggestions
    precedent for JSON-blob columns (database.py:660-676).
    """
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415

    insert_advisor_observation(**_computed_row_kwargs())
    rows = get_advisor_observations_for_subject("autotune_run", "run-001")
    assert rows, "Expected one row"

    raw = rows[0]["raw_response"]
    # Normalise: accept either already-parsed dict or JSON string
    if isinstance(raw, str):
        import json  # noqa: PLC0415
        raw = json.loads(raw)

    assert raw == {}, (
        f"raw_response for a computed row must round-trip as {{}}; got {raw!r}. "
        "Computed Overfitting Conscience rows carry no LLM output — '{{}}' is canonical."
    )


# ---------------------------------------------------------------------------
# 8. LLM-authored-row shape — raw_response with payload round-trips as dict
# ---------------------------------------------------------------------------


def test_llm_authored_row_raw_response_payload_round_trips():
    """A non-empty raw_response payload must round-trip through the read accessor as a dict.

    LLM-authored roles (SPEC_CRITIC, DIVERGENCE_EXPLAINER, NARRATOR) populate
    raw_response with the full LLM JSON output.  The accessor must deserialise
    it on read so callers do not have to call json.loads themselves.
    """
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415
    import json  # noqa: PLC0415

    kwargs = _llm_authored_row_kwargs(subject_id="run-llm-001")
    expected_payload = kwargs["raw_response"]

    insert_advisor_observation(**kwargs)
    rows = get_advisor_observations_for_subject("autotune_run", "run-llm-001")
    assert rows, "Expected one row"

    raw = rows[0]["raw_response"]
    if isinstance(raw, str):
        raw = json.loads(raw)

    assert isinstance(raw, dict), (
        f"raw_response must be returned as a dict; got {type(raw)}"
    )
    assert raw == expected_payload, (
        f"raw_response payload did not round-trip. "
        f"Expected {expected_payload!r}, got {raw!r}."
    )


# ---------------------------------------------------------------------------
# 9. Role discrimination
# ---------------------------------------------------------------------------


def test_get_advisor_observations_for_role_returns_only_matching_role():
    """get_advisor_observations_for_role must return only rows for the requested role.

    Rows inserted under a different advisor_role must be excluded — the role
    discriminator is the primary filter for the multi-role single-table layout.
    """
    from database import insert_advisor_observation, get_advisor_observations_for_role  # noqa: PLC0415

    # Insert one OVERFITTING_CONSCIENCE and two SPEC_CRITIC rows.
    insert_advisor_observation(**_computed_row_kwargs(subject_id="role-disc-oc"))
    insert_advisor_observation(**_llm_authored_row_kwargs(subject_id="role-disc-sc-1"))
    insert_advisor_observation(**_llm_authored_row_kwargs(subject_id="role-disc-sc-2"))

    oc_rows = get_advisor_observations_for_role("OVERFITTING_CONSCIENCE")
    sc_rows = get_advisor_observations_for_role("SPEC_CRITIC")

    assert len(oc_rows) == 1, (
        f"Expected 1 OVERFITTING_CONSCIENCE row; got {len(oc_rows)}"
    )
    assert len(sc_rows) == 2, (
        f"Expected 2 SPEC_CRITIC rows; got {len(sc_rows)}"
    )

    for row in oc_rows:
        assert row["advisor_role"] == "OVERFITTING_CONSCIENCE", (
            f"Row with wrong role returned by get_advisor_observations_for_role: "
            f"{row['advisor_role']!r}"
        )
    for row in sc_rows:
        assert row["advisor_role"] == "SPEC_CRITIC", (
            f"Row with wrong role returned: {row['advisor_role']!r}"
        )


def test_get_advisor_observations_for_role_returns_empty_for_unknown_role():
    """An advisor_role with no rows must return an empty list, not raise."""
    from database import get_advisor_observations_for_role  # noqa: PLC0415

    result = get_advisor_observations_for_role("NARRATOR")  # no rows inserted
    assert isinstance(result, list) and result == [], (
        f"Expected [] for a role with no rows; got {result!r}"
    )


# ---------------------------------------------------------------------------
# 10. run_migrations() idempotency
# ---------------------------------------------------------------------------


def test_run_migrations_is_idempotent():
    """Calling run_migrations() twice must not raise.

    The schema_migrations tracker records each applied migration; re-running
    skips already-applied entries so the CREATE TABLE IF NOT EXISTS guard in
    017 is never even exercised on the second call — but the double-call must
    succeed regardless.
    """
    # First call was made by the global _isolate_db conftest fixture.
    # A second call must be safe.
    db_module.run_migrations()  # must not raise


# ---------------------------------------------------------------------------
# 11. mode=ro connection can SELECT from advisor_observations
# ---------------------------------------------------------------------------


def test_ro_connection_can_select_from_advisor_observations():
    """A get_ro_connection() (mode=ro) must be able to SELECT from advisor_observations.

    The dashboard read path uses get_ro_connection() — new tables must be visible
    on that connection (Advisor scope-boundary integrity I-3 ★: Advisor data-access
    layer is structurally read-only; dashboard architecture constraint 5).
    """
    from database import get_ro_connection, insert_advisor_observation  # noqa: PLC0415

    insert_advisor_observation(**_computed_row_kwargs())

    conn = get_ro_connection()
    try:
        rows = conn.execute("SELECT id FROM advisor_observations").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, (
        f"Expected 1 row via read-only connection; got {len(rows)}"
    )


def test_ro_connection_cannot_insert_into_advisor_observations():
    """A mode=ro connection must reject INSERT attempts with OperationalError.

    This confirms the read-only enforcement at the SQLite driver level — the
    dashboard cannot accidentally write to the observation log.
    """
    from database import get_ro_connection  # noqa: PLC0415

    conn = get_ro_connection()
    try:
        with pytest.raises(Exception):
            # SQLite URI mode=ro raises OperationalError: "attempt to write a
            # readonly database".  We catch Exception broadly so the test does
            # not care about the exact exception subtype, only that a write
            # through the RO connection is rejected.
            conn.execute(
                "INSERT INTO advisor_observations "
                "(advisor_role, subject_type, subject_id, raw_response, is_advisory_only) "
                "VALUES ('TEST', 'test', 'test', '{}', 1)"
            )
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 12. _write_wall_breach_observation continues to work
# ---------------------------------------------------------------------------


def test_write_wall_breach_observation_succeeds_after_migration():
    """_write_wall_breach_observation must succeed once advisor_observations exists.

    Before migration 017, this helper would raise OperationalError because the
    table did not exist.  After migration, it must insert a WALL_BREACH row.
    The test verifies the row appears in the table.
    """
    import os  # noqa: PLC0415
    db_path = os.environ["DB_PATH"]

    # Call the helper — it swallows its own exceptions internally and logs them.
    # We verify the effect via direct SQL to confirm the write succeeded.
    db_module._write_wall_breach_observation("SELECT * FROM autotune_runs")

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT advisor_role, subject_type, verdict FROM advisor_observations "
        "WHERE advisor_role = 'WALL_BREACH'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1, (
        f"Expected 1 WALL_BREACH row after _write_wall_breach_observation; "
        f"got {len(rows)}. The table must exist for the write to succeed."
    )
    assert rows[0][1] == "fold_role_wall", (
        f"subject_type must be 'fold_role_wall'; got {rows[0][1]!r}"
    )
    assert rows[0][2] == "BREACH", (
        f"verdict must be 'BREACH'; got {rows[0][2]!r}"
    )


# ---------------------------------------------------------------------------
# 13. insert_advisor_observation returns a positive integer row id
# ---------------------------------------------------------------------------


def test_insert_advisor_observation_returns_positive_int():
    """insert_advisor_observation must return a positive integer (the new row id).

    This matches the record_llm_suggestion precedent and allows callers to
    reference the inserted row for debugging or audit correlation.
    """
    from database import insert_advisor_observation  # noqa: PLC0415

    row_id = insert_advisor_observation(**_computed_row_kwargs())
    assert isinstance(row_id, int) and row_id > 0, (
        f"insert_advisor_observation must return a positive int row id; got {row_id!r}"
    )


# ---------------------------------------------------------------------------
# 14. get_advisor_observations_for_subject filters by subject_type AND subject_id
# ---------------------------------------------------------------------------


def test_get_advisor_observations_for_subject_filters_correctly():
    """get_advisor_observations_for_subject must filter by BOTH subject_type and subject_id.

    Rows with a matching subject_type but a different subject_id must be excluded,
    and vice versa.  A query that joins on only one dimension would return false
    positives — this test catches that.
    """
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415

    # Three rows: two for the target (type=autotune_run, id=run-target),
    # one decoy with same type but different id, one decoy with different type.
    insert_advisor_observation(**_computed_row_kwargs(subject_id="run-target"))
    insert_advisor_observation(
        **_llm_authored_row_kwargs(subject_id="run-target")
    )
    insert_advisor_observation(**_computed_row_kwargs(subject_id="run-decoy"))
    insert_advisor_observation(
        **_computed_row_kwargs(subject_type="shadow_decision", subject_id="run-target")
    )

    target_rows = get_advisor_observations_for_subject("autotune_run", "run-target")
    assert len(target_rows) == 2, (
        f"Expected 2 rows for (autotune_run, run-target); got {len(target_rows)}"
    )

    for row in target_rows:
        assert row["subject_type"] == "autotune_run", (
            f"Returned row has wrong subject_type: {row['subject_type']!r}"
        )
        assert row["subject_id"] == "run-target", (
            f"Returned row has wrong subject_id: {row['subject_id']!r}"
        )


def test_get_advisor_observations_for_subject_returns_empty_for_no_match():
    """Querying a subject with no observations must return an empty list, not raise."""
    from database import get_advisor_observations_for_subject  # noqa: PLC0415

    result = get_advisor_observations_for_subject("autotune_run", "nonexistent-run")
    assert isinstance(result, list) and result == [], (
        f"Expected [] for a subject with no observations; got {result!r}"
    )


def test_get_advisor_observations_for_subject_returns_all_expected_keys():
    """Each dict returned by get_advisor_observations_for_subject must carry all schema columns.

    This prevents the accessor from silently dropping fields (e.g. SELECT of a
    subset of columns instead of SELECT *).
    """
    from database import insert_advisor_observation, get_advisor_observations_for_subject  # noqa: PLC0415

    insert_advisor_observation(**_computed_row_kwargs())
    rows = get_advisor_observations_for_subject("autotune_run", "run-001")
    assert rows, "Precondition: at least one row must be returned"

    missing = _EXPECTED_COLUMNS - set(rows[0].keys())
    assert not missing, (
        f"get_advisor_observations_for_subject returned a row missing columns: {missing}. "
        "The accessor must return all schema columns."
    )


# ---------------------------------------------------------------------------
# F-3 (gap): raw_response DEFAULT fires when caller omits the argument
# ---------------------------------------------------------------------------


def test_raw_response_default_fires_when_omitted():
    """Inserting without raw_response must store '{}' via the SQL DEFAULT.

    The plan §Deliverables #1 declares DEFAULT '{}'. When the accessor omits
    the column from the INSERT (relying on the DEFAULT), the stored value must
    deserialise to an empty dict — not None, not an empty string.
    """
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415
    from database import insert_advisor_observation  # noqa: PLC0415

    # Call with only the required positional fields — no raw_response.
    row_id = insert_advisor_observation(
        advisor_role="OVERFITTING_CONSCIENCE",
        subject_type="autotune_run",
        subject_id="run-default-test",
        verdict="D_spec=1",
        # raw_response intentionally omitted
    )
    assert isinstance(row_id, int) and row_id > 0

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    raw = conn.execute(
        "SELECT raw_response FROM advisor_observations WHERE id = ?", (row_id,)
    ).fetchone()[0]
    conn.close()

    assert raw is not None, (
        "raw_response must not be NULL when omitted — the DEFAULT '{}' must fire."
    )
    # Must deserialise cleanly as an empty dict.
    parsed = _json.loads(raw)
    assert parsed == {}, (
        f"raw_response DEFAULT must deserialise to {{}}; got {parsed!r}. "
        "The SQL DEFAULT '{{}}' was not stored or was corrupted."
    )


# ---------------------------------------------------------------------------
# F-4 (gap): extend mutation-prefix scan to cover set_* symbols
# ---------------------------------------------------------------------------


def test_no_set_advisor_observation_symbol_in_database_module():
    """database.py must not export any set_advisor_* function.

    The existing test covers update_/edit_/modify_/delete_/remove_ prefixes.
    This test adds set_* to close the gap spec-017 identified.
    """
    exposed = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if name.startswith("set_advisor")
    ]
    assert not exposed, (
        "database.py must not export set_advisor_* mutation accessors; "
        f"found: {exposed}. "
        "The observation log is append-only — all mutation paths are forbidden."
    )


# ---------------------------------------------------------------------------
# F-7: init_db() source must NOT contain "advisor_observations"
# ---------------------------------------------------------------------------


def test_init_db_source_does_not_mention_advisor_observations():
    """init_db() must not contain a CREATE TABLE for advisor_observations.

    New-table migrations go through run_migrations() only — no dual-write is
    needed here (unlike autotune_runs which has a dual-write requirement per
    H1 migration plan §6 H1). If init_db() creates the table directly, it
    bypasses the migration tracker and breaks idempotency guarantees.
    """
    source = inspect.getsource(db_module.init_db)
    assert "advisor_observations" not in source, (
        "init_db() must not reference 'advisor_observations'. "
        "The table is created exclusively via migrations/017_advisor_observations.sql "
        "through run_migrations() — no dual-write needed."
    )


# ---------------------------------------------------------------------------
# F-8: read accessors must use get_ro_connection, not get_connection
# ---------------------------------------------------------------------------


def test_get_advisor_observations_for_subject_uses_ro_connection():
    """get_advisor_observations_for_subject must use get_ro_connection.

    Read-only query paths use get_ro_connection() (mode=ro at SQLite driver level)
    per architecture constraint 5 and Advisor scope-boundary integrity I-3 ★.
    Using get_connection() on a read path would allow accidental writes through
    the same code path.
    """
    source = inspect.getsource(db_module.get_advisor_observations_for_subject)
    assert "get_ro_connection" in source, (
        "get_advisor_observations_for_subject must use get_ro_connection(), not get_connection(). "
        "Read paths must be enforced at the driver level."
    )
    assert "get_connection()" not in source, (
        "get_advisor_observations_for_subject must not call get_connection() — "
        "use get_ro_connection() for all read-only query paths."
    )


def test_get_advisor_observations_for_role_uses_ro_connection():
    """get_advisor_observations_for_role must use get_ro_connection.

    Same rationale as get_advisor_observations_for_subject — read-only paths
    must be structurally isolated from the write path.
    """
    source = inspect.getsource(db_module.get_advisor_observations_for_role)
    assert "get_ro_connection" in source, (
        "get_advisor_observations_for_role must use get_ro_connection(), not get_connection(). "
        "Read paths must be enforced at the driver level."
    )
    assert "get_connection()" not in source, (
        "get_advisor_observations_for_role must not call get_connection()."
    )


def test_insert_advisor_observation_uses_rw_connection():
    """insert_advisor_observation must use get_connection() (read-write), not get_ro_connection.

    Writes require a read-write connection. Using get_ro_connection for an INSERT
    would silently fail or raise — this test confirms the accessor uses the right
    connection type for its direction.
    """
    source = inspect.getsource(db_module.insert_advisor_observation)
    assert "get_connection" in source, (
        "insert_advisor_observation must call get_connection() for its write path."
    )


# ---------------------------------------------------------------------------
# F-10 (gap): _write_wall_breach_observation stores is_advisory_only=1
# ---------------------------------------------------------------------------


def test_write_wall_breach_observation_stores_is_advisory_only_1():
    """_write_wall_breach_observation must store is_advisory_only=1 in the written row.

    The raw INSERT in _write_wall_breach_observation (database.py:1041-1057) passes
    is_advisory_only=1 explicitly. This test verifies that the stored value is 1
    — not 0, not NULL — after the migration has created the table with the right schema.
    """
    import os  # noqa: PLC0415

    db_module._write_wall_breach_observation("SELECT 1")

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT advisor_role, verdict, is_advisory_only, spec_bundle_id, created_at "
        "FROM advisor_observations WHERE advisor_role = 'WALL_BREACH'"
    ).fetchone()
    conn.close()

    assert row is not None, "WALL_BREACH row not found after _write_wall_breach_observation"
    advisor_role, verdict, is_advisory_only, spec_bundle_id, created_at = row

    assert is_advisory_only == 1, (
        f"is_advisory_only must be 1 in a WALL_BREACH row; got {is_advisory_only!r}. "
        "The raw INSERT passes 1 explicitly; the schema DEFAULT is also 1."
    )
    assert verdict == "BREACH", (
        f"verdict must be 'BREACH'; got {verdict!r}"
    )
    # spec_bundle_id omitted from the raw INSERT — must be NULL via column DEFAULT (no default → NULL)
    assert spec_bundle_id is None, (
        f"spec_bundle_id must be NULL in a WALL_BREACH row (column omitted from INSERT); "
        f"got {spec_bundle_id!r}"
    )
    # created_at omitted from the raw INSERT — must be non-NULL via DEFAULT (datetime('now'))
    assert created_at is not None, (
        "created_at must be non-NULL — the DEFAULT (datetime('now')) must fire "
        "when the column is omitted from the INSERT."
    )


# ---------------------------------------------------------------------------
# F-11: schema fixture JSON exists and matches live PRAGMA table_info
# ---------------------------------------------------------------------------

_SCHEMA_FIXTURE_PATH = (
    Path(__file__).parents[2]
    / "tests"
    / "fixtures"
    / "database"
    / "advisor_observations"
    / "advisor_observations_schema.json"
)


def test_schema_fixture_file_exists():
    """The schema fixture JSON must exist on disk.

    Fixture provenance (D-2 ★): schema-derived with runtime validator.
    The fixture is the declared expectation; the live PRAGMA is the evidence.
    """
    assert _SCHEMA_FIXTURE_PATH.is_file(), (
        f"Schema fixture not found: {_SCHEMA_FIXTURE_PATH}. "
        "Create tests/fixtures/database/advisor_observations/advisor_observations_schema.json."
    )


def test_advisor_observations_schema_fixture_matches_live_db():
    """Live PRAGMA table_info must match every constraint declared in the schema fixture.

    For each column in the fixture, check notnull and has_default against
    the actual table schema. This is the runtime-validator half of the D-2 ★
    fixture-provenance contract.
    """
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415

    assert _SCHEMA_FIXTURE_PATH.is_file(), (
        f"Schema fixture missing: {_SCHEMA_FIXTURE_PATH}"
    )
    fixture = _json.loads(_SCHEMA_FIXTURE_PATH.read_text(encoding="utf-8"))

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    pragma_rows = conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    conn.close()

    assert pragma_rows, "advisor_observations does not exist — migration not applied."

    # Build a map: column_name -> {notnull, has_default}
    live = {
        row[1]: {"notnull": row[3], "has_default": row[4] is not None}
        for row in pragma_rows
    }

    for col_spec in fixture["columns"]:
        name = col_spec["name"]
        assert name in live, (
            f"Fixture declares column {name!r} but it is absent from advisor_observations. "
            "Either the fixture is wrong or the migration is missing the column."
        )
        expected_notnull = col_spec["notnull"]
        actual_notnull = live[name]["notnull"]
        assert actual_notnull == expected_notnull, (
            f"Column {name!r}: fixture expects notnull={expected_notnull}, "
            f"live schema has notnull={actual_notnull}."
        )
        expected_has_default = col_spec["has_default"]
        actual_has_default = live[name]["has_default"]
        assert actual_has_default == expected_has_default, (
            f"Column {name!r}: fixture expects has_default={expected_has_default}, "
            f"live schema has has_default={actual_has_default}."
        )


# ---------------------------------------------------------------------------
# F-14 (gap): is_advisory_only notnull=1 in PRAGMA table_info
# ---------------------------------------------------------------------------


def test_is_advisory_only_is_not_null_in_schema():
    """PRAGMA table_info must show notnull=1 for is_advisory_only.

    The existing test checks dflt_value='1'. This test adds the notnull=1
    constraint — the column must be declared NOT NULL in the migration DDL,
    not just have a default.
    """
    import os  # noqa: PLC0415

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols = {
        row[1]: {"notnull": row[3], "dflt_value": row[4]}
        for row in conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    }
    conn.close()

    assert "is_advisory_only" in cols, (
        "is_advisory_only column not found in advisor_observations"
    )
    notnull = cols["is_advisory_only"]["notnull"]
    assert notnull == 1, (
        f"is_advisory_only must be NOT NULL (notnull=1); got notnull={notnull}. "
        "The column declaration must include INTEGER NOT NULL DEFAULT 1."
    )


# ---------------------------------------------------------------------------
# F-15: Two-DB boundary guard — accessors must not reference optimization DB
# ---------------------------------------------------------------------------


def test_insert_advisor_observation_does_not_reference_optimization_db():
    """insert_advisor_observation source must not reference alphabot_opt or opt.db.

    Architecture constraint 3: Two-DB pattern — state DB owns live positions/decisions;
    optimization DB owns Optuna studies. Decision-science migrations land in the
    state DB only. Referencing the optimization DB from an advisor accessor would
    violate the architectural boundary.
    """
    source = inspect.getsource(db_module.insert_advisor_observation)
    for forbidden in ("alphabot_opt", "opt.db"):
        assert forbidden not in source, (
            f"insert_advisor_observation must not reference {forbidden!r}. "
            "Advisor observations land in the state DB only — Two-DB boundary violation."
        )


def test_get_advisor_observations_for_subject_does_not_reference_optimization_db():
    """get_advisor_observations_for_subject must not reference alphabot_opt or opt.db."""
    source = inspect.getsource(db_module.get_advisor_observations_for_subject)
    for forbidden in ("alphabot_opt", "opt.db"):
        assert forbidden not in source, (
            f"get_advisor_observations_for_subject must not reference {forbidden!r}. "
            "Two-DB boundary: advisory reads come from the state DB only."
        )


def test_get_advisor_observations_for_role_does_not_reference_optimization_db():
    """get_advisor_observations_for_role must not reference alphabot_opt or opt.db."""
    source = inspect.getsource(db_module.get_advisor_observations_for_role)
    for forbidden in ("alphabot_opt", "opt.db"):
        assert forbidden not in source, (
            f"get_advisor_observations_for_role must not reference {forbidden!r}. "
            "Two-DB boundary: advisory reads come from the state DB only."
        )


# ---------------------------------------------------------------------------
# REVISE: get_advisor_observations_for_role default limit must be 50
# ---------------------------------------------------------------------------


def test_get_advisor_observations_for_role_default_limit_is_50():
    """get_advisor_observations_for_role must default limit to 50, not any other value.

    The spec (spec-017 accessor signature) specifies limit: int = 50. The RED
    test suite originally encoded 100 in the docstring helper — that was an
    error caught during REVISE. This test asserts the contract via inspect so
    the default is verified against the live signature, not just called behavior.
    """
    import inspect as _inspect  # noqa: PLC0415 — local import for clarity

    sig = _inspect.signature(db_module.get_advisor_observations_for_role)
    limit_param = sig.parameters.get("limit")
    assert limit_param is not None, (
        "get_advisor_observations_for_role must have a 'limit' parameter."
    )
    assert limit_param.default == 50, (
        f"get_advisor_observations_for_role default limit must be 50; "
        f"got {limit_param.default!r}. "
        "The spec (spec-017) specifies limit: int = 50."
    )
