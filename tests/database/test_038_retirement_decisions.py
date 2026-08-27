"""
RED tests -- Migration 038: retirement_decisions + accessors (AC-3).

Feature: feature-plans/retirement-approval-lifecycle.md (contract frozen in
.claude/tdd-handoff.md -- implementers read the handoff, not the plan).

Cycle 2c addendum (feature-plans/retirement-approval-polish.md, PR#139's
2nd /code-review remediation -- .claude/tdd-handoff.md for this cycle pins
the exact contract):
  AC-5 (decided_at semantic, F3): an idempotent re-approve/re-reject
    PRESERVES the original decided_at (the first decision time) and only
    bumps updated_at -- decided_at records the ORIGINAL decision, not the
    last write. The prior behavior (still exercised by UPS1/UPS2/UPS2b
    below, which this cycle does NOT change -- a brand-new candidate's
    FIRST decision still stamps decided_at fresh) re-stamped decided_at to
    datetime('now') on EVERY write where the new status is approved/
    rejected, even a same-status idempotent re-write or a status
    transition -- see TestDecidedAtPreservedAcrossRewrites below.
  AC-8 (dead accessor removal, F5): the unused singular
    database.get_retirement_decision accessor is removed (verified via repo
    grep: its only callers were this file's own tests, pre-Cycle-2c) -- the
    render/API paths use the plural batch get_retirement_decisions(). Every
    UPS-series test below that used to call get_retirement_decision(id) is
    REPOINTED onto the local _find_decision(id) helper (built on the plural
    accessor), preserving IDENTICAL test intent/coverage -- see
    TestDeadAccessorRemoved below for the removal proof itself.

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
         -- [REMOVED, Cycle 2c AC-8] single-row lookup by candidate_id, was
            dead code (zero production callers) -- see TestDeadAccessorRemoved.

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

  UPS1-UPS9: upsert_retirement_decision / get_retirement_decisions contract
             (repointed off the removed get_retirement_decision, AC-8)
  DS1-DS4 (Cycle 2c AC-5): decided_at preserved across an idempotent
      re-write and a status transition; updated_at still bumps regardless;
      a brand-new candidate's first decision is unaffected.
  DA1-DA2 (Cycle 2c AC-8): get_retirement_decision no longer exists on the
      database module; no production caller references it repo-wide.

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
    __import__("pathlib").Path(__file__).parents[2] / "migrations" / "038_retirement_decisions.sql"
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


def _find_decision(candidate_id: str) -> dict | None:
    """Cycle 2c (AC-8) repoint: single-row lookup built on the PLURAL
    get_retirement_decisions() -- replaces every prior call site of the now-
    removed singular get_retirement_decision(candidate_id), preserving
    IDENTICAL test coverage/intent (None for an unknown id, the matching row
    dict otherwise)."""
    for row in db_module.get_retirement_decisions():
        if row["candidate_id"] == candidate_id:
            return row
    return None


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

    expected_columns = {
        "id",
        "candidate_id",
        "sibling_id",
        "approval_status",
        "decided_at",
        "updated_at",
    }
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
            "INSERT INTO retirement_decisions (candidate_id, approval_status) VALUES (?, ?)",
            ("dup-candidate", "pending"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO retirement_decisions (candidate_id, approval_status) VALUES (?, ?)",
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

        row = _find_decision("cand-new-1")
        assert row is not None
        assert row["approval_status"] == "pending"
        assert row["decided_at"] is None, "decided_at must stay NULL while pending."

    def test_approving_sets_decided_at_non_null(self, migrated_db):
        """UPS2: writing approval_status='approved' sets decided_at (non-null)."""
        db_module.upsert_retirement_decision("cand-approve-1", approval_status="approved")
        row = _find_decision("cand-approve-1")
        assert row is not None
        assert row["approval_status"] == "approved"
        assert row["decided_at"] is not None, "decided_at must be set on an approve write."

    def test_rejecting_sets_decided_at_non_null(self, migrated_db):
        """UPS2b: same for 'rejected'."""
        db_module.upsert_retirement_decision("cand-reject-1", approval_status="rejected")
        row = _find_decision("cand-reject-1")
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
            f"Expected exactly one row for cand-dup-1 after two upserts, got {len(matching)}."
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
            db_module.upsert_retirement_decision(
                "cand-bad-status-2", approval_status="not-a-status"
            )
        assert _find_decision("cand-bad-status-2") is None, (
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

        row = _find_decision("cand-sib-1")
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

        row = _find_decision("cand-sib-2")
        assert row is not None
        assert row["sibling_id"] == "sib-b"

    def test_unknown_candidate_id_returns_none_never_raises(self, migrated_db):
        """UPS8: get_retirement_decision on an id that was never written
        returns None, never raises."""
        assert _find_decision("cand-never-written") is None

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
        row = _find_decision(tricky_id)
        assert row is not None
        assert row["candidate_id"] == tricky_id


# ---------------------------------------------------------------------------
# AC-5 (Cycle 2c, feature-plans/retirement-approval-polish.md): decided_at
# semantic -- preserved across an idempotent re-write and a status
# transition, updated_at always bumps regardless.
#
# Deliberately verified via a RAW backdated INSERT (bypassing the accessor
# under test entirely for the SETUP step) rather than relying on wall-clock
# separation between two real upsert_retirement_decision() calls --
# datetime('now') has 1-second resolution, so two calls inside the same test
# process within the same second would produce an IDENTICAL decided_at even
# under the OLD buggy always-re-stamp behavior, making a same-second
# assertion pass vacuously for the wrong reason. Backdating to a fixed
# distant timestamp makes "preserved" vs "re-stamped" unambiguous and
# deterministic regardless of test execution speed.
# ---------------------------------------------------------------------------

_BACKDATED_TIMESTAMP = "2020-01-01T00:00:00Z"


def _raw_insert_backdated(
    db_path: str, candidate_id: str, *, approval_status: str, decided_at: str | None
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO retirement_decisions "
            "(candidate_id, approval_status, decided_at, updated_at) VALUES (?, ?, ?, ?)",
            (candidate_id, approval_status, decided_at, _BACKDATED_TIMESTAMP),
        )
        conn.commit()
    finally:
        conn.close()


class TestDecidedAtPreservedAcrossRewrites:
    def test_idempotent_reapprove_preserves_original_decided_at(self, migrated_db):
        """DS1: re-upserting the SAME approval_status ('approved' again)
        must NOT re-stamp decided_at -- it already recorded the original
        decision."""
        _raw_insert_backdated(
            migrated_db, "cand-ds-1", approval_status="approved", decided_at=_BACKDATED_TIMESTAMP
        )

        db_module.upsert_retirement_decision("cand-ds-1", approval_status="approved")

        row = _find_decision("cand-ds-1")
        assert row is not None
        assert row["decided_at"] == _BACKDATED_TIMESTAMP, (
            f"An idempotent re-approve must PRESERVE the original decided_at "
            f"({_BACKDATED_TIMESTAMP!r}), got {row['decided_at']!r} -- decided_at "
            "must record the FIRST decision, not the last write."
        )

    def test_idempotent_reapprove_still_bumps_updated_at(self, migrated_db):
        """DS2: decided_at is preserved, but updated_at must still reflect
        that a write happened."""
        _raw_insert_backdated(
            migrated_db, "cand-ds-2", approval_status="approved", decided_at=_BACKDATED_TIMESTAMP
        )

        db_module.upsert_retirement_decision("cand-ds-2", approval_status="approved")

        row = _find_decision("cand-ds-2")
        assert row is not None
        assert row["updated_at"] != _BACKDATED_TIMESTAMP, (
            "updated_at must be bumped on every write, even when decided_at is "
            "preserved (idempotent re-approve is still a real write)."
        )

    def test_reject_then_reapprove_preserves_the_original_decided_at_throughout(self, migrated_db):
        """DS3: the plan's own edge-case ruling -- 'preserve the original
        row's decided_at (first-ever decision on this candidate); updated_at
        tracks the latest change.' A genuine STATUS TRANSITION (approved ->
        rejected -> approved again) must never re-stamp decided_at at any
        step, even though the status itself legitimately changes each time."""
        _raw_insert_backdated(
            migrated_db, "cand-ds-3", approval_status="approved", decided_at=_BACKDATED_TIMESTAMP
        )

        db_module.upsert_retirement_decision("cand-ds-3", approval_status="rejected")
        row_after_reject = _find_decision("cand-ds-3")
        assert row_after_reject is not None
        assert row_after_reject["approval_status"] == "rejected"
        assert row_after_reject["decided_at"] == _BACKDATED_TIMESTAMP, (
            "A status TRANSITION (approved -> rejected) must still preserve the "
            "original decided_at -- it is not a 'first decision' event."
        )

        db_module.upsert_retirement_decision("cand-ds-3", approval_status="approved")
        row_after_reapprove = _find_decision("cand-ds-3")
        assert row_after_reapprove is not None
        assert row_after_reapprove["approval_status"] == "approved"
        assert row_after_reapprove["decided_at"] == _BACKDATED_TIMESTAMP, (
            "Re-approving after a reject must STILL preserve the ORIGINAL "
            "decided_at, not the reject's timestamp and not a fresh 'now'."
        )

    def test_pending_to_approved_transition_stamps_decided_at_on_first_real_decision(
        self, migrated_db
    ):
        """DS4 (adversarial, CASE-ordering guard): a candidate created as
        'pending' has decided_at=NULL. Its FIRST real decision (pending ->
        approved) must correctly stamp decided_at fresh -- the 'preserve if
        already set' rule must not be written in a way that also refuses to
        stamp a NULL decided_at (e.g. a naive COALESCE(decided_at, ...) vs a
        correctly-ordered CASE could get this backwards)."""
        db_module.upsert_retirement_decision("cand-ds-4", approval_status="pending")
        row_pending = _find_decision("cand-ds-4")
        assert row_pending is not None
        assert row_pending["decided_at"] is None

        db_module.upsert_retirement_decision("cand-ds-4", approval_status="approved")
        row_approved = _find_decision("cand-ds-4")
        assert row_approved is not None
        assert row_approved["decided_at"] is not None, (
            "A candidate's FIRST real decision (pending -> approved) must stamp "
            "decided_at -- it was never set before, so there is nothing to preserve."
        )


# ---------------------------------------------------------------------------
# AC-8 (Cycle 2c): the dead singular get_retirement_decision accessor is
# removed.
# ---------------------------------------------------------------------------


class TestDeadAccessorRemoved:
    def test_get_retirement_decision_no_longer_exists_on_database_module(self):
        """DA1: the singular accessor is gone -- the plural
        get_retirement_decisions() is the sole read path."""
        assert not hasattr(db_module, "get_retirement_decision"), (
            "database.get_retirement_decision (singular) still exists -- AC-8 "
            "requires removing it (zero production callers; the plural "
            "get_retirement_decisions() is the sole read path)."
        )
        assert hasattr(db_module, "get_retirement_decisions"), (
            "database.get_retirement_decisions (plural) must still exist -- this is "
            "NOT the accessor being removed."
        )

    def test_no_production_module_references_the_removed_accessor(self):
        """DA2: repo-wide proof (not just 'I checked app.py') -- no .py file
        outside this test file (and the migrations tree, which lists no
        accessor names at all) references get_retirement_decision as a bare
        call (i.e. NOT followed by the plural 's').

        repo_root here resolves to THIS WORKTREE's own root (parents[2] of
        this file, matching tests/security/test_retirement_action_no_trade_
        boundary.py's identical REPO_ROOT convention) -- rglob from there
        never descends into .claude/worktrees/<other-worktree> at all (this
        worktree has no nested worktrees inside it), so the usual "exclude
        .claude/worktrees" blast-radius-scanner guard (CLAUDE.md's Known
        Gotchas table) does not apply here and is deliberately NOT added --
        adding it would incorrectly exclude EVERY file in this scan, since
        this worktree's own absolute path already contains
        '.claude/worktrees/retire-polish/' as a path prefix."""
        import pathlib
        import re

        repo_root = pathlib.Path(__file__).resolve().parents[2]
        this_file = pathlib.Path(__file__).resolve()
        # Matches "get_retirement_decision(" but NOT "get_retirement_decisions("
        # -- the plural has an 's' between the name and the '(' that the
        # singular pattern's negative lookahead excludes.
        pattern = re.compile(r"get_retirement_decision(?!s)\s*\(")

        offenders: list[str] = []
        for py_file in repo_root.rglob("*.py"):
            resolved = py_file.resolve()
            if resolved == this_file:
                continue
            try:
                text = resolved.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern.search(text):
                offenders.append(str(resolved.relative_to(repo_root)))

        assert offenders == [], (
            f"Found reference(s) to the removed get_retirement_decision (singular) "
            f"outside this test file: {offenders}. AC-8 requires it fully removed "
            "with no remaining callers."
        )
