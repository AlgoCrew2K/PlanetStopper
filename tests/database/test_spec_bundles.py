"""
RED tests for migration 016_spec_bundles.sql — spec_bundles + spec_facets.

Coverage targets (all tests must FAIL until the GREEN implementation lands):

  Plan-mandated tests (plan.md §Golden-fixture tests):
    1. Bundle immutability — application-code-level; UPDATE on any mutable field
       for an existing bundle_hash must raise (no SQL trigger — consistent with
       llm_suggestions app-level immutability precedent, database.py:670-715).
    2. Hash uniqueness — second INSERT of the same bundle_hash raises
       sqlite3.IntegrityError (PRIMARY KEY collision).
    3. frozen_at default fires on insert — non-null after omitting it at call site.
    4. spec_facets.bundle_hash references a real bundle — app-level soft-FK:
       walking from a facet row back to its parent bundle returns a non-empty row.
    5. Fixture refresh test — fixture DB carries spec_bundles + spec_facets tables
       with all expected columns; at least one bundle + one facet present.

  Hostile edge tests:
    6. run_migrations() is idempotent — CREATE TABLE IF NOT EXISTS contract:
       calling it twice on the same DB must not raise.
    7. facets_json canonical-serialised roundtrip — re-hashing the same dict via
       the same canonical serialisation yields the same hash bytes (Gate-1 parity
       precondition for deterministic replay).
    8. freeze_discipline enum contract — only accepted values are
       THEORY / MANDATE / STYLIZED_FACT / CALIBRATION / BACKTEST_SELECTION;
       test pins the accepted set and verifies that insert_spec_bundle_facet
       rejects an unrecognised value with ValueError.
       (Decision: enforced at application level — consistent with the codebase
       pattern; NOT a SQL CHECK constraint.)
    9. Read-only mode=ro connection SELECTs from the new tables without error
       (get_ro_connection — MED-2 contract; new tables must be visible to the
       dashboard read path).

DB isolation strategy: identical to test_llm_suggestions.py — each test receives
a fresh SQLite file under tmp_path with database.DB_FILE monkeypatched, then
run_migrations() called (not init_db() alone, since init_db() does not include
spec_bundles — these are migration-only tables per plan §Deliverables #2).

Fixture provenance (D-2 ★): schema-derived with runtime validator.
See tests/fixtures/database/spec_bundles/spec_bundles_schema.json.

Accessor signatures expected from the GREEN implementer:

    insert_spec_bundle(
        *,
        bundle_hash: str,           # content-hash of canonical facets_json
        facets_json: str,           # canonical-serialised JSON blob
        horizon_bars: int | None = None,
        cvar_alpha: float | None = None,
        generator_family: str | None = None,
        # frozen_at is DEFAULT (datetime('now')) — caller must NOT pass it
    ) -> None                       # idempotent: re-insert same hash is a no-op
                                    # OR raises; GREEN implementer decides;
                                    # immutability test covers the update-path separately

    get_spec_bundle(bundle_hash: str) -> dict | None
        # returns full row dict or None if not found

    insert_spec_bundle_facet(
        *,
        bundle_hash: str,
        facet_name: str,
        facet_value: str,           # JSON-serialised value
        freeze_discipline: str,     # one of the five valid enum values
        justification: str | None = None,
        calibration_evidence: str | None = None,
    ) -> int                        # returns new row id

    get_spec_facets_for_bundle(bundle_hash: str) -> list[dict]
        # returns list of facet row dicts for the given bundle_hash

    canonicalize_facets_json(facets: dict) -> str
        # deterministic JSON serialisation — same dict always yields same bytes
        # (used for hashing; must be stable across interpreter restarts)

    hash_facets_json(canonical_json: str) -> str
        # returns hex-encoded content hash of the canonical bytes
        # (algorithm choice belongs to the implementer; must be reproducible)
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# Shared fixture path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "database"
    / "spec_bundles"
    / "spec_bundles_schema.json"
)

_MIGRATION_PATH = (
    pathlib.Path(__file__).parents[2] / "migrations" / "016_spec_bundles.sql"
)

# ---------------------------------------------------------------------------
# Freeze-discipline canonical enum — sourced from plan.md and README.md §1 NN1.
# Pin it here so any deviation in the implementation surfaces immediately.
# ---------------------------------------------------------------------------

_VALID_FREEZE_DISCIPLINES: frozenset[str] = frozenset({
    "THEORY",
    "MANDATE",
    "STYLIZED_FACT",
    "CALIBRATION",
    "BACKTEST_SELECTION",
})

# ---------------------------------------------------------------------------
# DB isolation fixture — function-scoped, redirects DB_FILE, runs migrations
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """
    Per-test isolated SQLite DB with full migration stack applied.

    Uses monkeypatch on DB_FILE (consistent with test_llm_suggestions pattern).
    Calls both init_db() (to create core tables / execution_lock) and
    run_migrations() (to apply all numbered migrations including 016).

    Scope: function (default) — every test starts clean.
    """
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _table_columns(db_path: str, table_name: str) -> dict[str, dict]:
    """Return {col_name: {notnull, dflt_value, pk}} via PRAGMA table_info."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        conn.close()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {
        row[1]: {"notnull": row[3], "dflt_value": row[4], "pk": row[5], "type": row[2]}
        for row in rows
    }


def _count_rows(db_path: str, table_name: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    finally:
        conn.close()


def _make_minimal_bundle_kwargs(suffix: str = "a") -> dict:
    """Return keyword args for a minimal insert_spec_bundle call.

    bundle_hash and facets_json are deterministic test values; they are NOT
    asserted for exact content — only shape/presence properties are checked.
    """
    facets = {"generator_family": f"crra-{suffix}", "horizon_bars": 63}
    canonical_json = json.dumps(facets, sort_keys=True, separators=(",", ":"))
    bundle_hash = hashlib.sha256(canonical_json.encode()).hexdigest()
    return {
        "bundle_hash": bundle_hash,
        "facets_json": canonical_json,
        "horizon_bars": 63,
        "cvar_alpha": 0.05,
        "generator_family": f"crra-{suffix}",
    }


def _make_minimal_facet_kwargs(bundle_hash: str) -> dict:
    """Return keyword args for a minimal insert_spec_bundle_facet call."""
    return {
        "bundle_hash": bundle_hash,
        "facet_name": "generator_family",
        "facet_value": json.dumps("crra"),
        "freeze_discipline": "THEORY",
        "justification": "CRRA is theoretically motivated; not strategy-P&L-selected.",
    }


# ===========================================================================
# Plan-mandated test 1: Bundle immutability (application-code level)
# ===========================================================================


def test_bundle_immutability_update_raises_on_existing_bundle(migrated_db):
    """
    INSERT a bundle then attempt to UPDATE any mutable field via the public
    accessor.  The application-code contract (analogous to llm_suggestions
    append-only guard) must prevent this.

    The test calls a hypothetical update_spec_bundle() accessor — if no such
    function exists, the test asserts that no mutation accessor is exported,
    which is the preferred form of immutability (no path exists at all).

    If the implementer exposes insert_spec_bundle() with INSERT OR IGNORE /
    INSERT OR REPLACE semantics, this test also verifies that a second insert
    of the same hash does NOT change the original frozen_at or facets_json.
    """
    from database import get_spec_bundle, insert_spec_bundle  # noqa: PLC0415

    kwargs = _make_minimal_bundle_kwargs("immutable")
    insert_spec_bundle(**kwargs)
    original_row = get_spec_bundle(kwargs["bundle_hash"])
    assert original_row is not None, "Precondition: bundle must be retrievable after insert"
    original_frozen_at = original_row["frozen_at"]

    # Attempt 1: if an update accessor exists, it must raise
    import inspect  # noqa: PLC0415
    mutation_names = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if "spec_bundle" in name and any(
            name.startswith(prefix) for prefix in ("update_", "edit_", "modify_", "set_")
        )
    ]
    assert not mutation_names, (
        f"database.py must not export mutation accessors for spec_bundles; "
        f"found: {mutation_names}. "
        "spec_bundles rows are immutable once frozen — no update path."
    )

    # Attempt 2: re-inserting the same hash must not silently overwrite frozen_at
    # (INSERT OR REPLACE would do this — that would be a defect).
    # We insert again and confirm frozen_at is unchanged (same or later, never empty).
    insert_spec_bundle(**kwargs)  # must not raise; must not alter existing row
    row_after_second_insert = get_spec_bundle(kwargs["bundle_hash"])
    assert row_after_second_insert is not None
    assert row_after_second_insert["frozen_at"] == original_frozen_at, (
        "Second insert of the same bundle_hash must not overwrite frozen_at. "
        "INSERT OR REPLACE semantics would silently reset the freeze timestamp — "
        "that is a defect: the original freeze timestamp is the provenance record."
    )
    assert row_after_second_insert["facets_json"] == kwargs["facets_json"], (
        "Second insert of the same bundle_hash must not overwrite facets_json."
    )


# ===========================================================================
# Plan-mandated test 2: Hash uniqueness — IntegrityError on duplicate
# ===========================================================================


def test_hash_uniqueness_raises_integrity_error_on_duplicate_primary_key(migrated_db):
    """
    Direct sqlite3 INSERT of two rows sharing bundle_hash must raise
    sqlite3.IntegrityError (PRIMARY KEY violation).

    This tests the schema constraint directly (bypassing any INSERT OR IGNORE
    defensive code the application layer might add). The PRIMARY KEY on
    bundle_hash is the hard guard.
    """
    conn = sqlite3.connect(migrated_db)
    try:
        hash_val = "deadbeef" * 8  # 64-char hex string — shape only, not meaningful
        conn.execute(
            "INSERT INTO spec_bundles (bundle_hash, facets_json) VALUES (?, ?)",
            (hash_val, '{"x":1}'),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO spec_bundles (bundle_hash, facets_json) VALUES (?, ?)",
                (hash_val, '{"x":2}'),  # different content, same hash — must collide
            )
            conn.commit()
    finally:
        conn.close()


# ===========================================================================
# Plan-mandated test 3: frozen_at DEFAULT fires on insert
# ===========================================================================


def test_frozen_at_is_nonnull_when_omitted_from_insert(migrated_db):
    """
    An INSERT that omits frozen_at must still produce a non-NULL frozen_at
    because the column carries DEFAULT (datetime('now')).

    Tests the schema-level default — not the accessor wrapper.
    """
    conn = sqlite3.connect(migrated_db)
    try:
        hash_val = "cafebabe" * 8
        conn.execute(
            "INSERT INTO spec_bundles (bundle_hash, facets_json) VALUES (?, ?)",
            (hash_val, '{"y":1}'),
        )
        conn.commit()
        row = conn.execute(
            "SELECT frozen_at FROM spec_bundles WHERE bundle_hash = ?",
            (hash_val,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Row must exist after INSERT"
    frozen_at = row[0]
    assert frozen_at is not None, (
        "frozen_at must not be NULL when omitted from INSERT. "
        "The column requires DEFAULT (datetime('now')) — missing in migration."
    )
    assert isinstance(frozen_at, str) and len(frozen_at) > 0, (
        f"frozen_at must be a non-empty string after DEFAULT fires; got {frozen_at!r}"
    )


# ===========================================================================
# Plan-mandated test 4: spec_facets.bundle_hash references a real bundle
# ===========================================================================


def test_facet_bundle_hash_soft_fk_resolves_to_nonempty_row(migrated_db):
    """
    Application-level soft-FK test: walking from a facet row back to its parent
    bundle via get_spec_bundle(facet['bundle_hash']) returns a non-empty dict.

    SQLite FK enforcement is off by default; this is the app-level invariant.
    """
    from database import (  # noqa: PLC0415
        get_spec_bundle,
        get_spec_facets_for_bundle,
        insert_spec_bundle,
        insert_spec_bundle_facet,
    )

    kwargs = _make_minimal_bundle_kwargs("softfk")
    insert_spec_bundle(**kwargs)
    insert_spec_bundle_facet(**_make_minimal_facet_kwargs(kwargs["bundle_hash"]))

    facets = get_spec_facets_for_bundle(kwargs["bundle_hash"])
    assert facets, "Precondition: at least one facet must be returned for the bundle"

    for facet in facets:
        parent_bundle = get_spec_bundle(facet["bundle_hash"])
        assert parent_bundle is not None, (
            f"Facet row has bundle_hash={facet['bundle_hash']!r} but "
            "get_spec_bundle() returned None. "
            "The app-level soft-FK invariant is broken: every facet must reference "
            "an existing bundle row."
        )
        assert isinstance(parent_bundle, dict) and parent_bundle, (
            "get_spec_bundle() must return a non-empty dict for a known bundle_hash."
        )


# ===========================================================================
# Plan-mandated test 5: Fixture refresh — schema validator
# ===========================================================================


def test_fixture_schema_file_exists():
    """
    The golden-fixture JSON for this migration must exist on disk.
    If missing, the implementer hasn't created it yet.
    """
    assert _FIXTURE_PATH.is_file(), (
        f"Fixture file not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/database/spec_bundles/spec_bundles_schema.json."
    )


def test_spec_bundles_table_has_all_expected_columns_from_fixture(migrated_db):
    """
    Load the golden-fixture schema and verify spec_bundles has every column
    listed there, with correct nullable/notnull constraints.

    This is the runtime schema-validator test: it guards against silent
    schema/fixture drift.
    """
    assert _FIXTURE_PATH.is_file(), "Fixture missing — run test_fixture_schema_file_exists first"
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_cols = fixture["spec_bundles"]["columns"]

    actual_cols = _table_columns(migrated_db, "spec_bundles")
    assert actual_cols, (
        "spec_bundles table does not exist after run_migrations(). "
        "Migration 016_spec_bundles.sql must create it."
    )

    for col_name, constraints in expected_cols.items():
        assert col_name in actual_cols, (
            f"Column {col_name!r} missing from spec_bundles. "
            f"Present columns: {sorted(actual_cols.keys())}"
        )
        if constraints.get("notnull"):
            assert actual_cols[col_name]["notnull"] == 1, (
                f"Column {col_name!r} must be NOT NULL per fixture spec."
            )
        else:
            assert actual_cols[col_name]["notnull"] == 0, (
                f"Column {col_name!r} must be nullable per fixture spec."
            )
        if constraints.get("has_default"):
            assert actual_cols[col_name]["dflt_value"] is not None, (
                f"Column {col_name!r} must have a DEFAULT value per fixture spec."
            )


def test_spec_facets_table_has_all_expected_columns_from_fixture(migrated_db):
    """
    Same schema-validator test for spec_facets.
    """
    assert _FIXTURE_PATH.is_file(), "Fixture missing"
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_cols = fixture["spec_facets"]["columns"]

    actual_cols = _table_columns(migrated_db, "spec_facets")
    assert actual_cols, (
        "spec_facets table does not exist after run_migrations(). "
        "Migration 016_spec_bundles.sql must create it."
    )

    for col_name, constraints in expected_cols.items():
        assert col_name in actual_cols, (
            f"Column {col_name!r} missing from spec_facets. "
            f"Present columns: {sorted(actual_cols.keys())}"
        )
        if constraints.get("notnull"):
            assert actual_cols[col_name]["notnull"] == 1, (
                f"Column {col_name!r} must be NOT NULL per fixture spec."
            )
        else:
            assert actual_cols[col_name]["notnull"] == 0, (
                f"Column {col_name!r} must be nullable per fixture spec."
            )


def test_fixture_db_has_seed_bundle_and_facet_after_migrations(migrated_db):
    """
    After run_migrations(), the fixture DB must carry at least one bundle row
    and at least one facet row (plan §Deliverables #3: seed rows required).

    This test will FAIL RED until the implementer seeds the fixture DB as
    required by the plan. It is NOT testing the app DB — it tests the fixture
    bootstrap contract.

    Note: The implementer must either:
      (a) add seed SQL to the migration itself (discouraged — migrations are
          schema-only), or
      (b) provide a conftest fixture or a separate seed helper that inserts
          the seed rows after run_migrations().
    The test expects the rows to be present on a freshly migrated DB; how
    they get there is the implementer's choice.
    """
    from database import get_spec_bundle, insert_spec_bundle, insert_spec_bundle_facet  # noqa: PLC0415

    # If the implementer chose to seed via accessors, this is a no-op.
    # If they chose to seed via the migration SQL, these are already present.
    # We don't double-insert — just assert presence.
    bundle_count = _count_rows(migrated_db, "spec_bundles")
    facet_count = _count_rows(migrated_db, "spec_facets")

    assert bundle_count >= 1, (
        f"Fixture DB has {bundle_count} rows in spec_bundles; "
        "plan §Deliverables #3 requires at least one seed bundle row. "
        "The implementer must seed the fixture DB so consumers have something to read."
    )
    assert facet_count >= 1, (
        f"Fixture DB has {facet_count} rows in spec_facets; "
        "plan §Deliverables #3 requires at least one seed facet row."
    )


# ===========================================================================
# Hostile edge test 6: run_migrations() idempotency
# ===========================================================================


def test_run_migrations_idempotent_create_table_if_not_exists(migrated_db, monkeypatch):
    """
    Calling run_migrations() a second time on an already-migrated DB must not
    raise. The CREATE TABLE IF NOT EXISTS contract guarantees this at the SQL
    level; the schema_migrations tracker prevents re-execution, but the
    IF NOT EXISTS clause is the final guard if the tracker is somehow bypassed.

    This test also verifies that 016_spec_bundles.sql uses IF NOT EXISTS
    (a direct textual check on the migration file).
    """
    # Second call on same DB — must not raise
    run_migrations()  # already applied by the migrated_db fixture; second call here
    run_migrations()  # third application — belt-and-suspenders idempotency check

    # Verify the spec_bundles table still exists (not silently dropped)
    actual_cols = _table_columns(migrated_db, "spec_bundles")
    assert actual_cols, "spec_bundles table must still exist after repeated run_migrations() calls"

    # Verify the SQL file itself uses CREATE TABLE IF NOT EXISTS
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql_upper = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    assert "CREATE TABLE IF NOT EXISTS" in sql_upper, (
        "016_spec_bundles.sql must use CREATE TABLE IF NOT EXISTS for idempotency. "
        "Bare CREATE TABLE would fail on re-run or double-apply."
    )


def test_016_migration_file_exists():
    """
    The migration file migrations/016_spec_bundles.sql must exist on disk.
    """
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/016_spec_bundles.sql."
    )


def test_016_migration_is_in_migration_files_list():
    """
    '016_spec_bundles.sql' must appear in database._MIGRATION_FILES.

    The list is append-only; the entry must be at the tail (after 015).
    """
    assert "016_spec_bundles.sql" in db_module._MIGRATION_FILES, (
        "'016_spec_bundles.sql' not found in database._MIGRATION_FILES. "
        "Append it in order after '015_shadow_history_position_epoch.sql'."
    )
    idx_015 = db_module._MIGRATION_FILES.index("015_shadow_history_position_epoch.sql")
    idx_016 = db_module._MIGRATION_FILES.index("016_spec_bundles.sql")
    assert idx_016 > idx_015, (
        "'016_spec_bundles.sql' must appear AFTER '015_shadow_history_position_epoch.sql' "
        "in _MIGRATION_FILES. The list is append-only — never reorder."
    )


# ===========================================================================
# Hostile edge test 7: facets_json canonical roundtrip preserves hash bytes
# ===========================================================================


def test_canonicalize_facets_json_is_deterministic_across_calls():
    """
    canonicalize_facets_json(facets) must return the same bytes for the same
    dict regardless of invocation order. This is the Gate-1 parity precondition:
    a re-hash of the same facets at replay time must yield the same bundle_hash.

    Asserts: same input dict → same output string (twice). Does NOT assert the
    exact string value (that is producer-computed).
    """
    from database import canonicalize_facets_json  # noqa: PLC0415

    facets = {
        "generator_family": "crra",
        "horizon_bars": 63,
        "cvar_alpha": 0.05,
    }

    result_a = canonicalize_facets_json(facets)
    result_b = canonicalize_facets_json(facets)

    assert isinstance(result_a, str) and len(result_a) > 0, (
        "canonicalize_facets_json must return a non-empty string"
    )
    assert result_a == result_b, (
        "canonicalize_facets_json must be deterministic: same input must yield "
        "same output on every call. Got different results on two consecutive calls."
    )


def test_hash_facets_json_is_deterministic_across_calls():
    """
    hash_facets_json(canonical_json) must return the same hex string for the
    same input. Asserts shape (non-empty hex string) and determinism.
    Does NOT assert the exact hash value.
    """
    from database import canonicalize_facets_json, hash_facets_json  # noqa: PLC0415

    facets = {"generator_family": "crra", "horizon_bars": 63}
    canonical = canonicalize_facets_json(facets)

    hash_a = hash_facets_json(canonical)
    hash_b = hash_facets_json(canonical)

    assert isinstance(hash_a, str) and len(hash_a) > 0, (
        "hash_facets_json must return a non-empty string"
    )
    # Validate it looks like a hex digest (all hex chars)
    assert all(c in "0123456789abcdefABCDEF" for c in hash_a), (
        f"hash_facets_json must return a hex string; got {hash_a!r}"
    )
    assert hash_a == hash_b, (
        "hash_facets_json must be deterministic: same canonical_json must always "
        "yield the same hash. Replay-determinism requires this (plan §Risk callouts)."
    )


def test_different_facets_dicts_yield_different_hashes():
    """
    Two semantically distinct facets dicts must produce different bundle_hashes.
    A hash function that returns a constant passes the determinism tests but
    would collapse all bundles — this guards against that degenerate implementation.
    """
    from database import canonicalize_facets_json, hash_facets_json  # noqa: PLC0415

    facets_a = {"generator_family": "crra", "horizon_bars": 63}
    facets_b = {"generator_family": "crra", "horizon_bars": 126}  # different horizon

    hash_a = hash_facets_json(canonicalize_facets_json(facets_a))
    hash_b = hash_facets_json(canonicalize_facets_json(facets_b))

    assert hash_a != hash_b, (
        "Distinct facets dicts must produce distinct hashes. "
        "A constant-returning hash function passes determinism tests but is wrong."
    )


# ===========================================================================
# Hostile edge test 8: freeze_discipline enum contract
# ===========================================================================


def test_insert_spec_bundle_facet_rejects_invalid_freeze_discipline(migrated_db):
    """
    insert_spec_bundle_facet() must raise ValueError (or a subclass) when
    freeze_discipline is not one of the five accepted values.

    Decision: application-level enforcement (NOT a SQL CHECK constraint) — this
    is consistent with the codebase pattern where constraints are enforced by
    accessor code, not triggers or CHECK clauses.

    Binding enum (from plan.md §Deliverables and README.md §1 NN1):
      THEORY | MANDATE | STYLIZED_FACT | CALIBRATION | BACKTEST_SELECTION

    An invalid value like 'INTUITION' or 'VIBES' must not silently persist.
    """
    from database import insert_spec_bundle, insert_spec_bundle_facet  # noqa: PLC0415

    kwargs = _make_minimal_bundle_kwargs("enum-guard")
    insert_spec_bundle(**kwargs)

    with pytest.raises((ValueError, TypeError), match=r"(?i)freeze_discipline|invalid|not.*valid|unrecognised|unrecognized"):
        insert_spec_bundle_facet(
            bundle_hash=kwargs["bundle_hash"],
            facet_name="generator_family",
            facet_value=json.dumps("crra"),
            freeze_discipline="VIBES",  # not a valid enum value
        )


def test_insert_spec_bundle_facet_accepts_all_valid_freeze_disciplines(migrated_db):
    """
    All five accepted freeze_discipline values must INSERT without error.

    Pins the complete contract: a future addition or removal of a valid value
    must update both this test and the fixture JSON.
    """
    from database import insert_spec_bundle, insert_spec_bundle_facet  # noqa: PLC0415

    kwargs = _make_minimal_bundle_kwargs("enum-accept")
    insert_spec_bundle(**kwargs)

    for discipline in sorted(_VALID_FREEZE_DISCIPLINES):
        row_id = insert_spec_bundle_facet(
            bundle_hash=kwargs["bundle_hash"],
            facet_name=f"test_facet_{discipline.lower()}",
            facet_value=json.dumps(True),
            freeze_discipline=discipline,
        )
        assert isinstance(row_id, int) and row_id > 0, (
            f"insert_spec_bundle_facet must return a positive int row_id for "
            f"freeze_discipline={discipline!r}; got {row_id!r}"
        )


def test_freeze_discipline_enum_fixture_matches_implementation():
    """
    The fixture JSON's freeze_discipline_enum list must match _VALID_FREEZE_DISCIPLINES.

    This is the fixture-vs-implementation sync guard: if a new discipline value
    is added to the plan, both the fixture and this test must be updated together.
    """
    assert _FIXTURE_PATH.is_file(), "Fixture missing"
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture_enum = frozenset(fixture["spec_facets"]["freeze_discipline_enum"])

    assert fixture_enum == _VALID_FREEZE_DISCIPLINES, (
        f"Fixture freeze_discipline_enum {sorted(fixture_enum)} does not match "
        f"test-pinned _VALID_FREEZE_DISCIPLINES {sorted(_VALID_FREEZE_DISCIPLINES)}. "
        "Update the fixture JSON or the test constant — they must stay in sync."
    )


# ===========================================================================
# Hostile edge test 9: mode=ro connection SELECTs from new tables
# ===========================================================================


def test_read_only_connection_can_select_from_spec_bundles(migrated_db):
    """
    get_ro_connection() (MED-2 contract) must be able to SELECT from
    spec_bundles after the migration. New tables must be visible on the read
    path used by the dashboard.

    Inserts a row via direct sqlite3 (write connection), then reads it back
    through get_ro_connection() to verify table visibility.
    """
    conn_write = sqlite3.connect(migrated_db)
    try:
        conn_write.execute(
            "INSERT INTO spec_bundles (bundle_hash, facets_json) VALUES (?, ?)",
            ("abcd1234" * 8, '{"ro_test": true}'),
        )
        conn_write.commit()
    finally:
        conn_write.close()

    conn_ro = db_module.get_ro_connection()
    try:
        row = conn_ro.execute("SELECT COUNT(*) FROM spec_bundles").fetchone()
        assert row is not None
        assert isinstance(row[0], int) and row[0] >= 1, (
            "get_ro_connection() must be able to COUNT spec_bundles rows after migration. "
            "Table is invisible to the RO connection — migration may not have been applied "
            "to the file the RO connection opens."
        )
    finally:
        conn_ro.close()


def test_read_only_connection_can_select_from_spec_facets(migrated_db):
    """
    get_ro_connection() must be able to SELECT from spec_facets after migration.
    """
    conn_ro = db_module.get_ro_connection()
    try:
        row = conn_ro.execute("SELECT COUNT(*) FROM spec_facets").fetchone()
        assert row is not None
        assert isinstance(row[0], int), (
            "get_ro_connection() must be able to COUNT spec_facets rows. "
            "Table may not exist — verify migration 016 applied to the DB file "
            "that get_ro_connection() opens."
        )
    finally:
        conn_ro.close()


def test_read_only_connection_cannot_insert_into_spec_bundles(migrated_db):
    """
    Write attempt on get_ro_connection() into spec_bundles must raise
    sqlite3.OperationalError — same as the MED-2 contract verified in
    test_wal_ro_connection.py. New tables must obey the same RO constraint.
    """
    conn_ro = db_module.get_ro_connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
            conn_ro.execute(
                "INSERT INTO spec_bundles (bundle_hash, facets_json) VALUES (?, ?)",
                ("0000" * 16, '{"guard": true}'),
            )
            conn_ro.commit()
    finally:
        conn_ro.close()


# ===========================================================================
# Accessor shape tests (shape / presence — no hardcoded values)
# ===========================================================================


def test_insert_spec_bundle_returns_none_or_raises_on_duplicate(migrated_db):
    """
    insert_spec_bundle() must handle duplicate hash gracefully.
    Acceptable behaviours: return None silently (INSERT OR IGNORE) or raise
    sqlite3.IntegrityError. Either is correct; the test pins that no other
    exception type leaks.

    The direct-SQL uniqueness test (test 2) already proves the schema constraint.
    """
    from database import insert_spec_bundle  # noqa: PLC0415

    kwargs = _make_minimal_bundle_kwargs("dup-test")
    insert_spec_bundle(**kwargs)

    # Second insert: must not raise an unexpected exception
    try:
        insert_spec_bundle(**kwargs)
    except sqlite3.IntegrityError:
        pass  # acceptable — the PK collision surfaces


def test_get_spec_bundle_returns_none_for_unknown_hash(migrated_db):
    """
    get_spec_bundle() for an unknown hash must return None, not raise.
    """
    from database import get_spec_bundle  # noqa: PLC0415

    result = get_spec_bundle("no-such-hash-000000000000000000000000000000000000")
    assert result is None, (
        f"get_spec_bundle() must return None for an unknown hash; got {result!r}"
    )


def test_get_spec_bundle_returns_dict_with_all_schema_columns(migrated_db):
    """
    get_spec_bundle() for a known hash must return a dict carrying all
    spec_bundles columns. Shape assertion — not value assertion.
    """
    from database import get_spec_bundle, insert_spec_bundle  # noqa: PLC0415

    kwargs = _make_minimal_bundle_kwargs("shape-check")
    insert_spec_bundle(**kwargs)

    row = get_spec_bundle(kwargs["bundle_hash"])
    assert isinstance(row, dict) and row, (
        "get_spec_bundle() must return a non-empty dict for a known hash."
    )

    expected_keys = {"bundle_hash", "frozen_at", "facets_json", "horizon_bars",
                     "cvar_alpha", "generator_family"}
    missing = expected_keys - set(row.keys())
    assert not missing, (
        f"get_spec_bundle() returned a dict missing columns: {missing}. "
        f"Got keys: {sorted(row.keys())}"
    )


def test_get_spec_facets_for_bundle_returns_empty_list_for_unknown_hash(migrated_db):
    """
    get_spec_facets_for_bundle() for an unknown hash must return [], not raise.
    """
    from database import get_spec_facets_for_bundle  # noqa: PLC0415

    result = get_spec_facets_for_bundle("no-such-hash-000000000000000000000000000000000000")
    assert isinstance(result, list) and result == [], (
        f"get_spec_facets_for_bundle() must return [] for unknown hash; got {result!r}"
    )


def test_get_spec_facets_for_bundle_returns_dicts_with_all_schema_columns(migrated_db):
    """
    Each dict in the list returned by get_spec_facets_for_bundle() must carry
    all spec_facets columns. Shape assertion — not value assertion.
    """
    from database import (  # noqa: PLC0415
        get_spec_facets_for_bundle,
        insert_spec_bundle,
        insert_spec_bundle_facet,
    )

    kwargs = _make_minimal_bundle_kwargs("facets-shape")
    insert_spec_bundle(**kwargs)
    insert_spec_bundle_facet(**_make_minimal_facet_kwargs(kwargs["bundle_hash"]))

    facets = get_spec_facets_for_bundle(kwargs["bundle_hash"])
    assert facets, "Precondition: at least one facet must be returned"

    expected_keys = {"id", "bundle_hash", "facet_name", "facet_value",
                     "freeze_discipline", "justification", "calibration_evidence"}
    for facet in facets:
        missing = expected_keys - set(facet.keys())
        assert not missing, (
            f"Facet dict missing columns: {missing}. Got: {sorted(facet.keys())}"
        )
