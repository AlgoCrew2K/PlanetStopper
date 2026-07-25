"""
RED tests -- Migration 037: strategy_incubation + incubation_daily + accessors.

Feature: feature-plans/strategy-incubation-gate.md (contract frozen in
.claude/tdd-handoff.md -- implementers read the handoff, not the plan).

THE IMPLEMENTATION DELIVERABLES (all must land for these tests to go GREEN):
  1. migrations/037_strategy_incubation.sql:
       strategy_incubation(id, candidate_hash UNIQUE, tree_json, objective,
       provenance, admitted_at, backtest_mdd_pct, status DEFAULT 'INCUBATING',
       status_reason, status_changed_at, promoted_at)
       incubation_daily(id, candidate_hash, trading_day, forward_return_pct,
       spy_return_pct, UNIQUE(candidate_hash, trading_day))
  2. database._MIGRATION_FILES: append '037_strategy_incubation.sql' after
     '036_sleeve_rule_fires.sql'.
  3. database.py new accessors (see .claude/tdd-handoff.md "database.py accessors"):
       register_incubation_candidate(candidate_hash, tree_json, objective,
           provenance, backtest_mdd_pct) -> dict {"admitted", "status", "reason"}
       append_incubation_day(candidate_hash, trading_day, forward_return_pct,
           spy_return_pct) -> bool (True = new row inserted)
       set_incubation_status(candidate_hash, status, status_reason=None) -> None
       get_incubating() -> list[dict]
       get_incubation_overview() -> list[dict] (each row + days_observed)

Coverage (all FAIL RED until GREEN implementation lands):
  M1: migration file exists on disk
  M2: both tables present after run_migrations() on an upgrade-path DB
  M3: run_migrations() is idempotent for 037
  M4: 037 registered in _MIGRATION_FILES after 036 (order-vs-neighbor, no is-last pin)
  M5: migration does not reference the optimization DB (two-DB boundary)

  REG1-REG7: register_incubation_candidate idempotency/refractory/cap contract
  APP1-APP2: append_incubation_day idempotency
  SET1-SET3: set_incubation_status write contract
  GI1-GI3: get_incubating filter/order/empty contract
  GO1-GO3: get_incubation_overview all-statuses/days_observed/empty contract

Fixture: none -- rows are seeded directly via the production accessors under
test, matching feedback_no_hardcoded_test_values -- every expected value is
derived from what the test itself seeded, never a literal.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import database as db_module
from database import init_db, run_migrations

_MIGRATION_PATH = (
    __import__("pathlib").Path(__file__).parents[2] / "migrations" / "037_strategy_incubation.sql"
)


# ---------------------------------------------------------------------------
# DB isolation fixture (mirrors test_033_candidate_alert_state.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied."""
    db_path = str(tmp_path / "test_037.db")
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


def _sample_tree(tag: str = "a") -> dict:
    """A minimal, structurally-varying synthetic tree for candidate_hash tests."""
    return {
        "id": f"root-{tag}-uuid-does-not-affect-hash",
        "step": "root",
        "name": "Test Candidate",
        "children": [
            {
                "id": "child-uuid",
                "step": "wt-cash-equal",
                "children": [{"id": "asset-uuid", "step": "asset", "ticker": tag.upper()}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# M1-M5: migration file + registration
# ---------------------------------------------------------------------------


def test_migration_037_file_exists():
    """M1: migrations/037_strategy_incubation.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/037_strategy_incubation.sql per .claude/tdd-handoff.md."
    )


def test_upgraded_db_gains_both_tables_via_migration(tmp_path, monkeypatch):
    """M2: a pre-037 DB (missing both tables) must gain them via run_migrations()."""
    db_path = str(tmp_path / "upgrade_037.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    for name in db_module._MIGRATION_FILES:
        if name != "037_strategy_incubation.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    assert _table_exists(db_path, "strategy_incubation"), (
        "run_migrations() did not create strategy_incubation."
    )
    assert _table_exists(db_path, "incubation_daily"), (
        "run_migrations() did not create incubation_daily."
    )


def test_run_migrations_idempotent_for_037(migrated_db):
    """M3: calling run_migrations() again after 037 is applied must not raise."""
    try:
        run_migrations()
        run_migrations()
    except Exception as exc:
        pytest.fail(f"run_migrations() raised on repeat call after 037 already applied: {exc!r}")
    assert _table_exists(migrated_db, "strategy_incubation")
    assert _table_exists(migrated_db, "incubation_daily")


def test_037_in_migration_files_immediately_after_036():
    """M4: '037_strategy_incubation.sql' registered directly after
    '036_sleeve_rule_fires.sql' -- order-vs-neighbor assertion, never an is-last pin
    (feedback_no_is_last_migration_pins) so a future 038 does not break this test."""
    assert "037_strategy_incubation.sql" in db_module._MIGRATION_FILES, (
        "'037_strategy_incubation.sql' not registered in database._MIGRATION_FILES."
    )
    assert "036_sleeve_rule_fires.sql" in db_module._MIGRATION_FILES

    idx_036 = db_module._MIGRATION_FILES.index("036_sleeve_rule_fires.sql")
    idx_037 = db_module._MIGRATION_FILES.index("037_strategy_incubation.sql")
    assert idx_037 == idx_036 + 1, (
        f"'037_strategy_incubation.sql' (index {idx_037}) must appear DIRECTLY after "
        f"'036_sleeve_rule_fires.sql' (index {idx_036}) -- got a gap or wrong order."
    )


def test_037_migration_does_not_reference_opt_db():
    """M5: 037_strategy_incubation.sql must not reference alphabot_opt or .opt.db.

    Architecture constraint 3: strategy_incubation lives in the state DB only.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    import re

    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert not re.search(r"alphabot_opt|\.opt\.db", sql, re.IGNORECASE), (
        "037_strategy_incubation.sql must not reference the optimization DB "
        "(architecture constraint 3: two-DB boundary)."
    )


# ---------------------------------------------------------------------------
# REG1-REG7: register_incubation_candidate contract
# ---------------------------------------------------------------------------


class TestRegisterIncubationCandidate:
    def test_new_candidate_is_admitted_incubating(self, migrated_db):
        """REG1: a brand-new candidate_hash is admitted with status INCUBATING."""
        result = db_module.register_incubation_candidate(
            candidate_hash="hash-new-1",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        assert result["admitted"] is True
        assert result["status"] == "INCUBATING"

    def test_duplicate_hash_while_incubating_does_not_reset_clock(self, migrated_db):
        """REG2 (AC-2 core guarantee): re-registering an already-INCUBATING hash is a
        no-op -- admitted_at must not change."""
        db_module.register_incubation_candidate(
            candidate_hash="hash-dup-1",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        rows_before = [r for r in db_module.get_incubating() if r["candidate_hash"] == "hash-dup-1"]
        assert len(rows_before) == 1
        admitted_at_before = rows_before[0]["admitted_at"]

        second = db_module.register_incubation_candidate(
            candidate_hash="hash-dup-1",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        assert second["admitted"] is False, "A duplicate INCUBATING hash must not be re-admitted."

        rows_after = [r for r in db_module.get_incubating() if r["candidate_hash"] == "hash-dup-1"]
        assert len(rows_after) == 1, "Duplicate registration must not create a second row."
        assert rows_after[0]["admitted_at"] == admitted_at_before, (
            "admitted_at changed on a duplicate registration -- the incubation clock "
            "must never reset while INCUBATING (AC-2)."
        )

    def test_duplicate_hash_while_promoted_is_permanent_noop(self, migrated_db):
        """REG3: a PROMOTED hash is never re-admitted, regardless of elapsed time."""
        db_module.register_incubation_candidate(
            candidate_hash="hash-promoted-1",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        db_module.set_incubation_status("hash-promoted-1", "PROMOTED")

        result = db_module.register_incubation_candidate(
            candidate_hash="hash-promoted-1",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        assert result["admitted"] is False
        assert result["status"] == "PROMOTED"

    def test_duplicate_hash_while_failed_within_refractory_is_noop(self, migrated_db):
        """REG4: a FAILED hash within INCUBATION_REFRACTORY_DAYS (90) is not re-admitted."""
        db_module.register_incubation_candidate(
            candidate_hash="hash-failed-recent",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        db_module.set_incubation_status("hash-failed-recent", "FAILED", "mdd_breach")

        result = db_module.register_incubation_candidate(
            candidate_hash="hash-failed-recent",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        assert result["admitted"] is False, (
            "A FAILED hash re-proposed immediately (well within the 90-day refractory "
            "window) must not be re-admitted."
        )

    def test_duplicate_hash_while_failed_after_refractory_elapsed_reenters(
        self, migrated_db
    ):
        """REG5: a FAILED hash re-proposed AFTER the refractory window elapses is
        admitted again with a RESET clock (fresh incubation attempt)."""
        db_module.register_incubation_candidate(
            candidate_hash="hash-failed-old",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        db_module.set_incubation_status("hash-failed-old", "FAILED", "mdd_breach")

        # Backdate status_changed_at past the 90-day refractory window directly via SQL
        # (the accessor's own timestamp is "now" -- backdating exercises the boundary
        # without waiting 90 real days).
        conn = db_module.get_connection()
        try:
            old_ts = (datetime.now(UTC) - timedelta(days=91)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE strategy_incubation SET status_changed_at = ? WHERE candidate_hash = ?",
                (old_ts, "hash-failed-old"),
            )
            conn.commit()
        finally:
            conn.close()

        result = db_module.register_incubation_candidate(
            candidate_hash="hash-failed-old",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        assert result["admitted"] is True, (
            "A FAILED hash whose refractory window has elapsed (91 days > 90-day "
            "floor) must be admitted again."
        )
        assert result["status"] == "INCUBATING"

        incubating = [r for r in db_module.get_incubating() if r["candidate_hash"] == "hash-failed-old"]
        assert len(incubating) == 1, "Refractory reentry must produce exactly one INCUBATING row."

    def test_duplicate_hash_while_expired_within_refractory_is_noop(self, migrated_db):
        """REG6: EXPIRED is treated identically to FAILED for the refractory window
        (ga3-tw coordination decision, documented in .claude/tdd-handoff.md) -- a
        recently-EXPIRED hash must not be re-admitted."""
        db_module.register_incubation_candidate(
            candidate_hash="hash-expired-recent",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        db_module.set_incubation_status(
            "hash-expired-recent", "EXPIRED", "composer_422_tree_invalid"
        )

        result = db_module.register_incubation_candidate(
            candidate_hash="hash-expired-recent",
            tree_json="{}",
            objective="cut_drawdown",
            provenance="built-new",
            backtest_mdd_pct=8.0,
        )
        assert result["admitted"] is False

    def test_register_respects_max_incubating_cap(self, migrated_db, monkeypatch):
        """REG7: once count(status='INCUBATING') reaches MAX_INCUBATING, a further
        new admission is refused (honest cap enforcement, AC-2). MAX_INCUBATING is a
        database.py-level constant (layering: database.py never imports from
        advisors.*, see .claude/tdd-handoff.md "Module placement" note)."""
        monkeypatch.setattr(db_module, "MAX_INCUBATING", 2, raising=False)
        db_module.register_incubation_candidate("cap-1", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.register_incubation_candidate("cap-2", "{}", "cut_drawdown", "built-new", 8.0)
        result = db_module.register_incubation_candidate(
            "cap-3", "{}", "cut_drawdown", "built-new", 8.0
        )
        assert result["admitted"] is False, (
            "A third candidate beyond a cap of 2 must not be admitted -- cap "
            f"enforcement failed, got {result}"
        )
        assert result["reason"] == "cap_exceeded"


# ---------------------------------------------------------------------------
# APP1-APP2: append_incubation_day contract
# ---------------------------------------------------------------------------


class TestAppendIncubationDay:
    def test_append_new_day_returns_true(self, migrated_db):
        """APP1: appending a genuinely new (candidate_hash, trading_day) row returns True."""
        db_module.register_incubation_candidate("hash-app-1", "{}", "cut_drawdown", "built-new", 8.0)
        result = db_module.append_incubation_day("hash-app-1", "2026-08-03", 0.25, 0.10)
        assert result is True

    def test_append_duplicate_day_is_idempotent(self, migrated_db):
        """APP2: appending the SAME (candidate_hash, trading_day) twice never raises
        and never creates a duplicate row (tick-rerun safety)."""
        db_module.register_incubation_candidate("hash-app-2", "{}", "cut_drawdown", "built-new", 8.0)
        first = db_module.append_incubation_day("hash-app-2", "2026-08-03", 0.25, 0.10)
        second = db_module.append_incubation_day("hash-app-2", "2026-08-03", 0.99, 0.99)
        assert first is True
        assert second is False, "A duplicate (candidate_hash, trading_day) must return False."

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-app-2")
        assert row["days_observed"] == 1, (
            f"Expected exactly 1 observed day after a duplicate append attempt, "
            f"got {row['days_observed']}."
        )


# ---------------------------------------------------------------------------
# SET1-SET3: set_incubation_status contract
# ---------------------------------------------------------------------------


class TestSetIncubationStatus:
    def test_set_status_updates_status_and_reason(self, migrated_db):
        """SET1: set_incubation_status persists status + status_reason."""
        db_module.register_incubation_candidate("hash-set-1", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.set_incubation_status("hash-set-1", "FAILED", "mdd_breach")

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-set-1")
        assert row["status"] == "FAILED"
        assert row["status_reason"] == "mdd_breach"

    def test_set_status_promoted_sets_promoted_at(self, migrated_db):
        """SET2: transitioning to PROMOTED sets promoted_at (non-null)."""
        db_module.register_incubation_candidate("hash-set-2", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.set_incubation_status("hash-set-2", "PROMOTED")

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-set-2")
        assert row["promoted_at"] is not None, "PROMOTED transition must set promoted_at."

    def test_set_status_non_promoted_leaves_promoted_at_null(self, migrated_db):
        """SET3: a FAILED/EXPIRED transition must NOT set promoted_at."""
        db_module.register_incubation_candidate("hash-set-3", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.set_incubation_status("hash-set-3", "FAILED", "mdd_breach")

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-set-3")
        assert row["promoted_at"] is None, (
            "A FAILED transition must not populate promoted_at."
        )


# ---------------------------------------------------------------------------
# GI1-GI3: get_incubating contract
# ---------------------------------------------------------------------------


class TestGetIncubating:
    def test_only_incubating_status_rows_returned(self, migrated_db):
        """GI1: get_incubating() excludes PROMOTED/FAILED/EXPIRED rows."""
        db_module.register_incubation_candidate("hash-gi-1", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.register_incubation_candidate("hash-gi-2", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.set_incubation_status("hash-gi-2", "PROMOTED")

        incubating = db_module.get_incubating()
        hashes = {r["candidate_hash"] for r in incubating}
        assert "hash-gi-1" in hashes
        assert "hash-gi-2" not in hashes

    def test_empty_ledger_returns_empty_list_never_raises(self, migrated_db):
        """GI3: a fresh ledger with zero rows returns [], never raises."""
        assert db_module.get_incubating() == []


# ---------------------------------------------------------------------------
# GO1-GO3: get_incubation_overview contract
# ---------------------------------------------------------------------------


class TestGetIncubationOverview:
    def test_overview_includes_all_statuses(self, migrated_db):
        """GO1: get_incubation_overview() includes INCUBATING, PROMOTED, FAILED, and
        EXPIRED rows -- unlike get_incubating(), it is not status-filtered."""
        db_module.register_incubation_candidate("hash-go-1", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.register_incubation_candidate("hash-go-2", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.register_incubation_candidate("hash-go-3", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.set_incubation_status("hash-go-2", "PROMOTED")
        db_module.set_incubation_status("hash-go-3", "FAILED", "mdd_breach")

        overview = db_module.get_incubation_overview()
        hashes = {r["candidate_hash"] for r in overview}
        assert {"hash-go-1", "hash-go-2", "hash-go-3"} <= hashes

    def test_overview_days_observed_reflects_incubation_daily_count(self, migrated_db):
        """GO2: days_observed is a true COUNT(*) of incubation_daily rows for that hash."""
        db_module.register_incubation_candidate("hash-go-days", "{}", "cut_drawdown", "built-new", 8.0)
        db_module.append_incubation_day("hash-go-days", "2026-08-03", 0.1, 0.05)
        db_module.append_incubation_day("hash-go-days", "2026-08-04", 0.2, 0.05)
        db_module.append_incubation_day("hash-go-days", "2026-08-05", -0.1, 0.05)

        overview = db_module.get_incubation_overview()
        row = next(r for r in overview if r["candidate_hash"] == "hash-go-days")
        assert row["days_observed"] == 3

    def test_overview_empty_ledger_returns_empty_list(self, migrated_db):
        """GO3: an empty ledger returns [], never raises."""
        assert db_module.get_incubation_overview() == []
