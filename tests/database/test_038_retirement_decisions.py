"""
RED tests -- Migration 038: retirement_decisions + accessors (AC-3).

Feature: feature-plans/retirement-approval-lifecycle.md (contract frozen in
.claude/tdd-handoff.md -- implementers read the handoff, not the plan).

THE IMPLEMENTATION DELIVERABLES (all must land for these tests to go GREEN):
  1. migrations/038_retirement_decisions.sql:
       retirement_decisions(
         id             INTEGER PRIMARY KEY AUTOINCREMENT,
         candidate_id   TEXT NOT NULL UNIQUE,
         sibling_id     TEXT,
         approval_status TEXT NOT NULL DEFAULT 'pending',
         decided_at     TEXT,                 -- NULL while pending; set to
                                               -- datetime('now') on every write
                                               -- where approval_status is
                                               -- 'approved' or 'rejected'
         updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
       )
  2. database._MIGRATION_FILES: append '038_retirement_decisions.sql' directly
     after '037_strategy_incubation.sql' (order-vs-neighbor, never an is-last
     pin -- feedback_no_is_last_migration_pins).
  3. database.py new accessors (see .claude/tdd-handoff.md
     "retirement_decisions accessors"):
       upsert_retirement_decision(candidate_id, *, approval_status,
           sibling_id=None) -> bool
         -- UPSERT keyed by candidate_id's UNIQUE constraint (INSERT ... ON
            CONFLICT(candidate_id) DO UPDATE). approval_status must be one of
            {pending, approved, rejected} -- raises ValueError otherwise (a
            caller bug, mirrors update_frontrunner_proposal_status's
            _VALID_PROPOSAL_APPROVAL_STATUSES pattern), and must NOT write
            anything on an invalid value. sibling_id=None on an UPDATE
            preserves the existing stored value (COALESCE, same pattern as
            update_frontrunner_proposal_status's created_symphony_id); a
            non-None sibling_id always overwrites. decided_at is set to
            datetime('now') whenever approval_status is written as 'approved'
            or 'rejected'; stays NULL while approval_status is 'pending'.
            updated_at is set to datetime('now') on every write. Returns True
            on a successful write (insert or update).
       get_retirement_decisions() -> list[dict]
         -- ALL rows, unfiltered, each a flat dict of the 6 columns. []  when
            the table is empty. Never raises.
       get_retirement_decision(candidate_id) -> dict | None
         -- single-row lookup by candidate_id. None for an unknown id. Never
            raises.

Coverage (all FAIL RED until GREEN implementation lands):
  M1: migration file exists on disk
  M2: a pre-038 DB gains retirement_decisions via run_migrations()
  M3: run_migrations() is idempotent for 038
  M4: 038 registered in _MIGRATION_FILES directly after 037 (order-vs-
      neighbor, no is-last pin)
  M5: migration does not reference the optimization DB (two-DB boundary)
  M6: schema pin -- column types/NOT NULL/DEFAULT/UNIQUE shape
  M7: candidate_id UNIQUE is enforced at the raw-SQL level (defense-in-depth,
      independent of the accessor's own upsert logic)

  UPS1-UPS9: upsert_retirement_decision / get_retirement_decisions /
             get_retirement_decision contract

Fixture: none -- rows are seeded directly via the production accessors under
test (feedback_no_hardcoded_test_values) -- every expected value is derived
from what the test itself wrote, never a bare literal compared against an
unrelated producer-computed number.
"""

from __future__ import annotations

import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

_MIGRATION_PATH = (
    __import__("pathlib").Path(__file__).parents[2]
    / "migrations"
    / "038_retirement_decisions.sql"
)

_VALID_STATUSES = {"pending", "approved", "rejected"}


# ---------------------------------------------------------------------------
# DB isolation fixture (mirrors test_037_strategy_incubation.py's pattern)
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied."""
    db_path = str(tmp_path / "test_038.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _table_exists(db_path: str, table_name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# M1-M5: migration file + registration
# ---------------------------------------------------------------------------


def test_migration_038_file_exists():
    """M1: migrations/038_retirement_decisions.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/038_retirement_decisions.sql per .claude/tdd-handoff.md."
    )


def test_upgraded_db_gains_retirement_decisions_via_migration(tmp_path, monkeypatch):
    """M2: a pre-038 DB (missing the table) must gain it via run_migrations()."""
    db_path = str(tmp_path / "upgrade_038.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    for name in db_module._MIGRATION_FILES:
        if name != "038_retirement_decisions.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    assert _table_exists(db_path, "retirement_decisions"), (
        "run_migrations() did not create retirement_decisions."
    )


def test_run_migrations_idempotent_for_038(migrated_db):
    """M3: calling run_migrations() again after 038 is applied must not raise."""
    try:
        run_migrations()
        run_migrations()
    except Exception as exc:
        pytest.fail(f"run_migrations() raised on repeat call after 038 already applied: {exc!r}")
    assert _table_exists(migrated_db, "retirement_decisions")


def test_038_in_migration_files_immediately_after_037():
    """M4: '038_retirement_decisions.sql' registered directly after
    '037_strategy_incubation.sql' -- order-vs-neighbor assertion, never an
    is-last pin (feedback_no_is_last_migration_pins) so a future 039 does
    not break this test."""
    assert "038_retirement_decisions.sql" in db_module._MIGRATION_FILES, (
        "'038_retirement_decisions.sql' not registered in database._MIGRATION_FILES."
    )
    assert "037_strategy_incubation.sql" in db_module._MIGRATION_FILES

    idx_037 = db_module._MIGRATION_FILES.index("037_strategy_incubation.sql")
    idx_038 = db_module._MIGRATION_FILES.index("038_retirement_decisions.sql")
    assert idx_038 == idx_037 + 1, (
        f"'038_retirement_decisions.sql' (index {idx_038}) must appear DIRECTLY "
        f"after '037_strategy_incubation.sql' (index {idx_037}) -- got a gap or "
        "wrong order."
    )


def test_038_migration_does_not_reference_opt_db():
    """M5: 038_retirement_decisions.sql must not reference alphabot_opt or
    .opt.db. Architecture constraint 3: retirement_decisions lives in the
    state DB only."""
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    import re

    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert not re.search(r"alphabot_opt|\.opt\.db", sql, re.IGNORECASE), (
        "038_retirement_decisions.sql must not reference the optimization DB "
        "(architecture constraint 3: two-DB boundary)."
    )


def test_retirement_decisions_schema_shape(migrated_db):
    """M6: column types / NOT NULL / DEFAULT / UNIQUE shape pin."""
    conn = sqlite3.connect(migrated_db)
    try:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(retirement_decisions)")}
        indexes = conn.execute("PRAGMA index_list(retirement_decisions)").fetchall()
    finally:
        conn.close()

    expected_columns = {"id", "candidate_id", "sibling_id", "approval_status", "decided_at", "updated_at"}
    assert expected_columns <= set(columns.keys()), (
        f"retirement_decisions is missing expected columns. "
        f"Expected at least {expected_columns}, got {sorted(columns.keys())}."
    )

    _cid, _name, col_type, not_null, default_value, _pk = columns["candidate_id"]
    assert col_type.upper() == "TEXT", f"candidate_id must be TEXT, got {col_type!r}."
    assert not_null == 1, "candidate_id must be NOT NULL."

    _cid, _name, col_type, not_null, default_value, _pk = columns["approval_status"]
    assert not_null == 1, "approval_status must be NOT NULL."
    assert default_value is not None and default_value.strip("'\"") == "pending", (
        f"approval_status must DEFAULT 'pending', got default={default_value!r}."
    )

    _cid, _name, col_type, not_null, default_value, _pk = columns["updated_at"]
    assert not_null == 1, "updated_at must be NOT NULL."

    # candidate_id must be covered by a UNIQUE index (either the column-level
    # UNIQUE constraint or an explicit CREATE UNIQUE INDEX).
    assert any(idx[2] == 1 for idx in indexes), (
        f"retirement_decisions has no UNIQUE index -- expected one covering "
        f"candidate_id. index_list: {indexes}."
    )


def test_candidate_id_unique_enforced_at_sql_level(migrated_db):
    """M7 (defense-in-depth, independent of the accessor's own upsert logic):
    a raw duplicate INSERT against candidate_id must raise
    sqlite3.IntegrityError -- the UNIQUE constraint itself must exist at the
    schema level, not merely be respected by well-behaved application code."""
    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "INSERT INTO retirement_decisions (candidate_id, approval_status) "
            "VALUES (?, ?)",
            ("dup-candidate", "pending"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO retirement_decisions (candidate_id, approval_status) "
                "VALUES (?, ?)",
                ("dup-candidate", "pending"),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# UPS1-UPS9: upsert_retirement_decision / get_retirement_decisions /
#            get_retirement_decision contract
# ---------------------------------------------------------------------------


class TestUpsertRetirementDecision:
    def test_new_candidate_is_inserted_pending(self, migrated_db):
        """UPS1: a brand-new candidate_id is inserted with the given status."""
        result = db_module.upsert_retirement_decision("cand-new-1", approval_status="pending")
        assert result is True

        row = db_module.get_retirement_decision("cand-new-1")
        assert row is not None
        assert row["approval_status"] == "pending"
        assert row["decided_at"] is None, "decided_at must stay NULL while pending."

    def test_approving_sets_decided_at_non_null(self, migrated_db):
        """UPS2: writing approval_status='approved' sets decided_at (non-null)."""
        db_module.upsert_retirement_decision("cand-approve-1", approval_status="approved")
        row = db_module.get_retirement_decision("cand-approve-1")
        assert row is not None
        assert row["approval_status"] == "approved"
        assert row["decided_at"] is not None, "decided_at must be set on an approve write."

    def test_rejecting_sets_decided_at_non_null(self, migrated_db):
        """UPS2b: same for 'rejected'."""
        db_module.upsert_retirement_decision("cand-reject-1", approval_status="rejected")
        row = db_module.get_retirement_decision("cand-reject-1")
        assert row is not None
        assert row["approval_status"] == "rejected"
        assert row["decided_at"] is not None

    def test_re_upserting_same_candidate_id_updates_in_place_not_duplicated(self, migrated_db):
        """UPS3 (idempotency, AC-3's 'decide once, persists' core guarantee):
        re-upserting the SAME candidate_id must UPDATE the existing row, never
        insert a second row."""
        db_module.upsert_retirement_decision("cand-dup-1", approval_status="pending")
        db_module.upsert_retirement_decision("cand-dup-1", approval_status="approved")

        all_rows = db_module.get_retirement_decisions()
        matching = [r for r in all_rows if r["candidate_id"] == "cand-dup-1"]
        assert len(matching) == 1, (
            f"Expected exactly one row for cand-dup-1 after two upserts, "
            f"got {len(matching)}."
        )
        assert matching[0]["approval_status"] == "approved"

    def test_invalid_approval_status_raises_value_error(self, migrated_db):
        """UPS4: an approval_status outside {pending, approved, rejected}
        raises ValueError -- a caller bug, mirrors
        update_frontrunner_proposal_status's own validation contract."""
        with pytest.raises(ValueError):
            db_module.upsert_retirement_decision("cand-bad-status", approval_status="uploaded")

    def test_invalid_approval_status_writes_nothing(self, migrated_db):
        """UPS5 (adversarial): the ValueError-raising call above must not have
        left a partial/garbage row behind."""
        with pytest.raises(ValueError):
            db_module.upsert_retirement_decision("cand-bad-status-2", approval_status="not-a-status")
        assert db_module.get_retirement_decision("cand-bad-status-2") is None, (
            "An invalid approval_status must not create a row at all."
        )

    def test_sibling_id_preserved_when_omitted_on_update(self, migrated_db):
        """UPS6: sibling_id=None on a subsequent upsert must PRESERVE the
        previously-stored sibling_id (COALESCE), not null it out -- mirrors
        update_frontrunner_proposal_status's created_symphony_id pattern."""
        db_module.upsert_retirement_decision(
            "cand-sib-1", approval_status="pending", sibling_id="sib-original"
        )
        db_module.upsert_retirement_decision("cand-sib-1", approval_status="approved")

        row = db_module.get_retirement_decision("cand-sib-1")
        assert row is not None
        assert row["sibling_id"] == "sib-original", (
            f"sibling_id must be preserved when omitted on update, got {row['sibling_id']!r}."
        )

    def test_sibling_id_overwritten_when_explicitly_provided(self, migrated_db):
        """UPS7: a non-None sibling_id on a subsequent upsert DOES overwrite."""
        db_module.upsert_retirement_decision(
            "cand-sib-2", approval_status="pending", sibling_id="sib-a"
        )
        db_module.upsert_retirement_decision(
            "cand-sib-2", approval_status="approved", sibling_id="sib-b"
        )

        row = db_module.get_retirement_decision("cand-sib-2")
        assert row is not None
        assert row["sibling_id"] == "sib-b"

    def test_unknown_candidate_id_returns_none_never_raises(self, migrated_db):
        """UPS8: get_retirement_decision on an id that was never written
        returns None, never raises."""
        assert db_module.get_retirement_decision("cand-never-written") is None

    def test_get_retirement_decisions_empty_table_returns_empty_list(self, migrated_db):
        """UPS9a: an empty table returns [], never raises."""
        assert db_module.get_retirement_decisions() == []

    def test_get_retirement_decisions_returns_all_rows_unfiltered(self, migrated_db):
        """UPS9b: get_retirement_decisions() is not status-filtered -- pending,
        approved, and rejected rows all come back."""
        db_module.upsert_retirement_decision("cand-all-1", approval_status="pending")
        db_module.upsert_retirement_decision("cand-all-2", approval_status="approved")
        db_module.upsert_retirement_decision("cand-all-3", approval_status="rejected")

        rows = db_module.get_retirement_decisions()
        candidate_ids = {r["candidate_id"] for r in rows}
        assert {"cand-all-1", "cand-all-2", "cand-all-3"} <= candidate_ids

    def test_candidate_id_with_sql_special_characters_round_trips_safely(self, migrated_db):
        """UPS10 (SQLi smoke, parameterized-query proof): a candidate_id
        containing a quote/semicolon must round-trip exactly, never break the
        query or get silently mangled."""
        tricky_id = "cand-o'brien; DROP TABLE retirement_decisions;--"
        result = db_module.upsert_retirement_decision(tricky_id, approval_status="pending")
        assert result is True

        # The table must still exist and the row must be retrievable exactly.
        row = db_module.get_retirement_decision(tricky_id)
        assert row is not None
        assert row["candidate_id"] == tricky_id
