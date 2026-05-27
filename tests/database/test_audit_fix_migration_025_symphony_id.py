"""
Sprint 3 audit-fix Cycle A — RED tests for S3-AUDIT-004 (HIGH) +
S3-AUDIT-010 (LOW, folded into 004):
  Migration 025 adds a NULLable symphony_id column to advisor_observations so
  the /api/advisor-observations?symphony_id=<id> filter can find producer rows
  in a SINGLE query (instead of issuing 3 separate subject-type SELECTs whose
  subject_id never matches the user-supplied symphony name).

Schema discipline (project CLAUDE.md):
  Additive-first. NULLable + DEFAULT NULL. Never destructive. Append the new
  filename to _MIGRATION_FILES — do not reorder existing entries (ARCH-002
  inline comment in database.py preserves the intentional 021/020 ordering).

These tests are RED until:
  - migrations/025_advisor_observations_symphony_id.sql exists with
    `ALTER TABLE advisor_observations ADD COLUMN symphony_id TEXT DEFAULT NULL`.
  - "025_advisor_observations_symphony_id.sql" is appended to
    database._MIGRATION_FILES (after 024_*.sql).
  - PRAGMA table_info confirms the new column shape (NULLable, no NOT NULL,
    no DEFAULT besides NULL).
  - insert_advisor_observation accepts a symphony_id kwarg and stores it.

Fixture provenance: schema-derived (PRAGMA table_info + read of _MIGRATION_FILES);
no producer-computed values.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

import database as db_module


_MIGRATION_FILENAME = "025_advisor_observations_symphony_id.sql"
_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / _MIGRATION_FILENAME


# ---------------------------------------------------------------------------
# 1. Migration file exists and is well-formed (additive, IF NOT EXISTS-safe).
# ---------------------------------------------------------------------------


def test_migration_025_file_exists():
    """migrations/025_advisor_observations_symphony_id.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/025_advisor_observations_symphony_id.sql with an "
        "ALTER TABLE advisor_observations ADD COLUMN symphony_id TEXT DEFAULT NULL."
    )


def test_migration_025_uses_alter_table_add_column():
    """The migration must ALTER TABLE ... ADD COLUMN — additive only.

    A CREATE TABLE on advisor_observations would attempt to recreate the
    existing table; only ADD COLUMN is permitted on a live schema.
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    assert "ALTER TABLE" in sql, (
        "Migration 025 must use ALTER TABLE ADD COLUMN — additive-first schema "
        "discipline (project CLAUDE.md §Coding Standards).  No CREATE TABLE or "
        "DROP/RENAME on the existing advisor_observations table."
    )
    assert "ADD COLUMN" in sql, (
        "Migration 025 must ADD COLUMN symphony_id — destructive operations or "
        "non-additive shapes (table-rewrite, CREATE+COPY+DROP) are forbidden."
    )
    assert "ADVISOR_OBSERVATIONS" in sql, (
        "Migration 025 must target the advisor_observations table by name."
    )
    assert "SYMPHONY_ID" in sql, (
        "Migration 025 must declare a `symphony_id` column."
    )


def test_migration_025_column_is_nullable_with_default_null():
    """The new column must be NULLable and DEFAULT NULL — no NOT NULL constraint.

    Pre-existing rows in advisor_observations are unaffected because the column
    is added with DEFAULT NULL.  A NOT NULL declaration would attempt to back-fill
    every existing row and fail on a non-empty table.
    """
    assert _MIGRATION_PATH.is_file(), "Migration file missing"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    # The ADD COLUMN clause must NOT carry NOT NULL.
    # Split on ADD COLUMN, take the clause that contains SYMPHONY_ID.
    add_idx = sql.find("ADD COLUMN")
    assert add_idx >= 0, "Migration must contain ADD COLUMN"
    clause = sql[add_idx:]
    # Strip everything after the next semicolon — focus on this column declaration.
    semicolon = clause.find(";")
    if semicolon >= 0:
        clause = clause[:semicolon]
    assert "NOT NULL" not in clause, (
        f"symphony_id column declaration must NOT include NOT NULL; clause was:\n{clause}\n"
        "Additive-first migrations on live tables require NULLable + DEFAULT NULL "
        "so existing rows survive (project CLAUDE.md §Coding Standards)."
    )
    # DEFAULT NULL is the canonical safe default; accept either explicit
    # "DEFAULT NULL" or no DEFAULT clause at all (SQLite stores NULL).
    # Either way, NOT NULL must be absent (asserted above).


# ---------------------------------------------------------------------------
# 2. _MIGRATION_FILES contains the new filename, appended AFTER 024.
# ---------------------------------------------------------------------------


def test_migration_025_listed_in_migration_files():
    """_MIGRATION_FILES must contain '025_advisor_observations_symphony_id.sql'."""
    assert _MIGRATION_FILENAME in db_module._MIGRATION_FILES, (
        f"'{_MIGRATION_FILENAME}' is not in database._MIGRATION_FILES. "
        "Append it to the list — never reorder or remove existing entries "
        "(ARCH-002 inline comment in database.py — 021 precedes 020 intentionally)."
    )


def test_migration_025_appears_after_024():
    """025 must appear after 024_spec_facets_unique_constraint.sql in the list.

    Append-only ordering.  The new entry is the LAST in the list — no reorder.
    """
    files = db_module._MIGRATION_FILES
    idx_024 = files.index("024_spec_facets_unique_constraint.sql")
    idx_025 = files.index(_MIGRATION_FILENAME)
    assert idx_025 > idx_024, (
        f"'{_MIGRATION_FILENAME}' (index {idx_025}) must appear AFTER "
        f"'024_spec_facets_unique_constraint.sql' (index {idx_024}). "
        "Append-only ordering (project CLAUDE.md)."
    )


def test_migration_025_does_not_touch_021_020_order():
    """The intentional 021 before 020 ordering must be preserved.

    ARCH-002 inline comment in database.py:916-921 documents that
    021_cvar_diagnostics.sql is listed BEFORE 020_autotune_runs_eut.sql by
    design (historical restoration hotfix).  Reordering would corrupt live DBs
    that already have 021 applied.
    """
    files = db_module._MIGRATION_FILES
    idx_021 = files.index("021_cvar_diagnostics.sql")
    idx_020 = files.index("020_autotune_runs_eut.sql")
    assert idx_021 < idx_020, (
        f"Migration 025 must not have touched the intentional 021/020 order. "
        f"021_cvar_diagnostics.sql (index {idx_021}) must precede "
        f"020_autotune_runs_eut.sql (index {idx_020}).  See ARCH-002 inline "
        "comment in database.py."
    )


# ---------------------------------------------------------------------------
# 3. PRAGMA table_info confirms the column shape post-migration.
# ---------------------------------------------------------------------------


def test_advisor_observations_has_symphony_id_column_after_init_db():
    """Live schema (via PRAGMA table_info) must show symphony_id present and NULLable.

    The global _isolate_db fixture has already called init_db() which runs
    migrations; this test asserts the column appears with the right shape.
    """
    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    cols = {
        row[1]: {"notnull": row[3], "dflt_value": row[4]}
        for row in conn.execute("PRAGMA table_info(advisor_observations)").fetchall()
    }
    conn.close()

    assert "symphony_id" in cols, (
        "advisor_observations.symphony_id column not present after init_db(). "
        "Migration 025 must run and add the column."
    )
    assert cols["symphony_id"]["notnull"] == 0, (
        f"advisor_observations.symphony_id must be NULLable (notnull=0); "
        f"got notnull={cols['symphony_id']['notnull']}.  Existing rows must "
        "survive the additive migration."
    )


# ---------------------------------------------------------------------------
# 4. insert_advisor_observation accepts a symphony_id kwarg and stores it.
# ---------------------------------------------------------------------------


def test_insert_advisor_observation_signature_accepts_symphony_id():
    """insert_advisor_observation must accept a symphony_id kwarg (default None).

    The producers (OC, SC, DE) all populate symphony_id when persisting; the
    accessor must accept it as a first-class kwarg, not silently swallow it
    via **kwargs.
    """
    sig = inspect.signature(db_module.insert_advisor_observation)
    assert "symphony_id" in sig.parameters, (
        f"insert_advisor_observation signature missing 'symphony_id' parameter. "
        f"Signature: {sig}.  The producers persist symphony_id; the accessor "
        "must accept it as a named kwarg."
    )
    sym_param = sig.parameters["symphony_id"]
    assert sym_param.default is None, (
        f"insert_advisor_observation symphony_id default must be None; "
        f"got {sym_param.default!r}.  Pre-existing callers (WALL_BREACH writer) "
        "do not supply symphony_id."
    )


def test_insert_advisor_observation_persists_symphony_id():
    """A symphony_id kwarg must round-trip via the get_advisor_observations_* accessors.

    Insert → SELECT — the value flows through the column added by migration 025.
    """
    row_id = db_module.insert_advisor_observation(
        advisor_role="OVERFITTING_CONSCIENCE",
        subject_type="autotune_run",
        subject_id="7",
        verdict="CLEAR",
        raw_response={},
        spec_bundle_id=None,
        symphony_id="DefensiveAlpha",
    )
    assert isinstance(row_id, int) and row_id > 0

    rows = db_module.get_advisor_observations_for_subject("autotune_run", "7")
    assert rows, "Expected one row by subject"
    assert rows[0].get("symphony_id") == "DefensiveAlpha", (
        f"symphony_id did not round-trip: stored={rows[0].get('symphony_id')!r}, "
        f"expected 'DefensiveAlpha'.  insert_advisor_observation must write the "
        "symphony_id column added by migration 025."
    )


def test_insert_advisor_observation_omitting_symphony_id_stores_null():
    """When symphony_id is omitted, the column must persist as NULL.

    Belt-and-braces: pre-existing callers (WALL_BREACH writer at database.py
    _write_wall_breach_observation) do not supply symphony_id; the additive
    migration must leave that path untouched.
    """
    row_id = db_module.insert_advisor_observation(
        advisor_role="SPEC_CRITIC",
        subject_type="spec_bundle",
        subject_id="bundle-hash-xyz",
        verdict="CLEAR",
        raw_response={},
    )
    assert isinstance(row_id, int) and row_id > 0

    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    raw = conn.execute(
        "SELECT symphony_id FROM advisor_observations WHERE id = ?", (row_id,)
    ).fetchone()
    conn.close()
    assert raw is not None, "Row not found by id"
    assert raw[0] is None, (
        f"symphony_id must be NULL when caller omits it; got {raw[0]!r}. "
        "Existing callers that do not supply symphony_id must remain unaffected."
    )


# ---------------------------------------------------------------------------
# 5. Single-query symphony filter accessor — S3-AUDIT-010 fix.
# ---------------------------------------------------------------------------


def test_get_advisor_observations_for_symphony_accessor_exists():
    """A single-query accessor must exist for filter-by-symphony lookups.

    Today /api/advisor-observations issues THREE separate SELECTs and unions in
    Python.  S3-AUDIT-010 (folded into 004) requires one SELECT against the
    symphony_id column added by migration 025.

    Acceptable accessor names (implementer's choice):
      - get_advisor_observations_for_symphony
      - get_advisor_observations_by_symphony
    """
    candidate_names = (
        "get_advisor_observations_for_symphony",
        "get_advisor_observations_by_symphony",
    )
    found = [n for n in candidate_names if hasattr(db_module, n) and callable(getattr(db_module, n))]
    assert found, (
        f"None of {candidate_names!r} are exported by database.py. "
        "Add a single-query symphony-filter accessor backed by the new symphony_id "
        "column (S3-AUDIT-004 + S3-AUDIT-010)."
    )


def test_get_advisor_observations_for_symphony_returns_only_matching_rows():
    """The symphony-filter accessor must return only rows whose symphony_id matches.

    Insert observations under two symphonies, query for one — only the matching
    subset comes back.
    """
    db_module.insert_advisor_observation(
        advisor_role="OVERFITTING_CONSCIENCE",
        subject_type="autotune_run",
        subject_id="1",
        verdict="CLEAR",
        raw_response={},
        symphony_id="DefensiveAlpha",
    )
    db_module.insert_advisor_observation(
        advisor_role="OVERFITTING_CONSCIENCE",
        subject_type="autotune_run",
        subject_id="2",
        verdict="WATCH",
        raw_response={},
        symphony_id="OffensiveBeta",
    )
    db_module.insert_advisor_observation(
        advisor_role="DIVERGENCE_EXPLAINER",
        subject_type="autotune_run",
        subject_id="3",
        verdict="NOT_APPLICABLE",
        raw_response={},
        symphony_id="DefensiveAlpha",
    )

    # Locate the accessor (implementer-chosen name).
    candidate_names = (
        "get_advisor_observations_for_symphony",
        "get_advisor_observations_by_symphony",
    )
    accessor = next(
        (getattr(db_module, n) for n in candidate_names if hasattr(db_module, n)),
        None,
    )
    assert accessor is not None, (
        "Symphony-filter accessor not exported by database.py — see "
        "test_get_advisor_observations_for_symphony_accessor_exists."
    )

    rows = accessor("DefensiveAlpha")
    assert isinstance(rows, list), f"accessor must return list; got {type(rows).__name__}"
    assert len(rows) == 2, (
        f"DefensiveAlpha filter must return exactly the 2 rows persisted under "
        f"that symphony; got {len(rows)}.  Rows: {rows!r}."
    )
    returned_symphonies = {r.get("symphony_id") for r in rows}
    assert returned_symphonies == {"DefensiveAlpha"}, (
        f"Symphony filter leaked non-matching rows: got symphonies "
        f"{returned_symphonies!r}.  Expected exactly {{'DefensiveAlpha'}}."
    )
