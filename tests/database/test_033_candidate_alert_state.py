"""
RED tests — Migration 033: candidate_alert_state + candidate-alert accessors.

Feature: feature-plans/candidate-alert.md (header candidate-alert indicator).

Context: the AI Advisor's weekly suggestion job (advisors/weekly_suggestions_scheduler)
persists ASSET_SWAP / LOGIC_CHANGE / STRATEGY_BUILDER rows into advisor_observations,
gated by a strict FDR/PBO/SPY-OOS overfitting discipline.  Most candidates are
correctly rejected; survivors are rare.  Today those rows only surface if the
operator happens to open the AI Advisor tab.  This migration adds the single-row
"viewed marker" state candidate-alert needs to know which survivors are NEW.

IMPORTANT — verdict-classification correction vs the feature-plan's literal AC-2
text (traced against the real producers before writing these tests):
  - acceptance_gate.py:80-82 defines exactly THREE decision strings:
    DECISION_ADOPT_CANDIDATE = "ADOPT_CANDIDATE"
    DECISION_KEEP_INCUMBENT  = "KEEP_INCUMBENT"
    DECISION_REJECT_VETO_FAILED = "REJECT_VETO_FAILED"
  - asset_swap_engine.py:848 and logic_change_engine.py:847 persist
    verdict=gate_result.verdict.decision VERBATIM — KEEP_INCUMBENT is a real,
    common persisted value (RC-4: "persistence is verdict-agnostic ... an
    ADOPT-only write left advisor_observations empty on the common KEEP path").
  - strategy_builder_engine.py:612 confirms the intent directly: "survivor
    always ADOPT_CANDIDATE".
  - There is NO rejection_reason column on advisor_observations (columns:
    id/created_at/advisor_role/subject_type/subject_id/verdict/raw_response/
    is_advisory_only/spec_bundle_id/symphony_id — database.py:1081-1092).
  The plan's literal `verdict != 'REJECT_VETO_FAILED'` would badge KEEP_INCUMBENT
  as a "new valid candidate" — exactly the noise AC-2 says must not alert.  The
  CORRECT, pinned rule is: verdict == 'ADOPT_CANDIDATE' is the ONLY survivor
  condition, uniformly across ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER.

THE IMPLEMENTATION DELIVERABLES (all must land for these tests to go GREEN):
  1. migrations/033_candidate_alert_state.sql:
       CREATE TABLE IF NOT EXISTS candidate_alert_state (
           id                          INTEGER PRIMARY KEY CHECK (id = 1),
           last_viewed_observation_id  INTEGER NOT NULL DEFAULT 0,
           updated_at                  TEXT
       );
  2. database._MIGRATION_FILES: append '033_candidate_alert_state.sql' after
     '032_prism_audit_log.sql'.
  3. init_db() must ALSO create candidate_alert_state inline (CREATE TABLE IF
     NOT EXISTS — same H1 dual-write mirror used for config_audit_log/030 and
     prism_audit_log/032) so a fresh DB has the table without run_migrations().
  4. database.py new accessors:
       get_candidate_alert_viewed_marker() -> int
           — read-only (get_ro_connection); 0 when table/row absent; never raises.
       set_candidate_alert_viewed_marker(observation_id: int) -> int
           — UPSERT id=1; MONOTONIC (new value = max(existing, observation_id));
             returns the resulting marker.
       mark_candidate_alert_viewed() -> int
           — computes MAX(id) across ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER
             advisor_observations rows (0 if none exist) and calls
             set_candidate_alert_viewed_marker with it.  Server-computed only —
             no caller-supplied observation_id parameter (a client must never be
             able to set the marker to an arbitrary value).
       get_candidate_alert_new_valid_count() -> int
           — COUNT(*) FROM advisor_observations WHERE advisor_role IN
             ('ASSET_SWAP','LOGIC_CHANGE','STRATEGY_BUILDER') AND
             verdict='ADOPT_CANDIDATE' AND id > <viewed_marker>.  Fail-closed:
             NULL/odd/other verdicts never count.
       get_candidate_alert_last_run() -> dict | None
           — None when no weekly-suggestion-role row has EVER been written
             (honest "no run yet").  Otherwise groups by the calendar date
             (UTC, substr(created_at,1,10)) of the MOST RECENT such row and
             returns {"ran_at": <max created_at that date>, "evaluated":
             <row count that date>, "survivors": <subset verdict=='ADOPT_CANDIDATE'>}.
             The 0-survivor case must NOT return None (AC-3 "know it's working").

Coverage (all FAIL RED until GREEN implementation lands):
  M1: migration file exists on disk
  M2: candidate_alert_state present after init_db() only (fresh DB, H1 dual-write)
  M3: candidate_alert_state present after migration on a pre-033 DB (upgrade path)
  M4: run_migrations() is idempotent for 033
  M5: 033 registered in _MIGRATION_FILES after 032
  M6: migration does not reference the optimization DB (two-DB boundary)

  V1: get_candidate_alert_viewed_marker returns 0 on a fresh DB (unset marker)
  V2: set_candidate_alert_viewed_marker persists a value; read-back confirms
  V3: set_candidate_alert_viewed_marker is MONOTONIC — a lower value never
      regresses the stored marker
  V4: set_candidate_alert_viewed_marker UPSERTs — calling it twice never raises
      (single-row id=1 constraint must not collide)

  MV1: mark_candidate_alert_viewed() with zero relevant rows returns 0 and
       persists 0 (no crash on an empty table)
  MV2: mark_candidate_alert_viewed() advances the marker to MAX(id) across
       ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER rows only — a higher-id
       MARKET_PRISM row must NOT be picked up (role-scoping, adversarial)
  MV3: mark_candidate_alert_viewed() takes no observation_id argument (a
       caller cannot inject an arbitrary marker value)

  NC1: get_candidate_alert_new_valid_count counts ADOPT_CANDIDATE rows above
       the viewed marker
  NC2: get_candidate_alert_new_valid_count does NOT count KEEP_INCUMBENT rows
       (the verdict-classification correction — the crux adversarial case)
  NC3: get_candidate_alert_new_valid_count does NOT count REJECT_VETO_FAILED rows
  NC4: get_candidate_alert_new_valid_count does NOT count rows with verdict=None
       or an unrecognised verdict string (fail-closed, AC-6)
  NC5: get_candidate_alert_new_valid_count does NOT count ADOPT_CANDIDATE rows
       from a non-weekly-suggestion role (e.g. MARKET_PRISM) even though the
       verdict string coincidentally matches (role-scoping, adversarial)
  NC6: get_candidate_alert_new_valid_count excludes rows at-or-below the viewed
       marker (id > marker, not id >= marker)
  NC7: get_candidate_alert_new_valid_count returns 0 on a table with zero rows

  LR1: get_candidate_alert_last_run returns None when no weekly-suggestion row
       has ever been written (honest "no run yet")
  LR2: get_candidate_alert_last_run returns survivors=0 (not None) when the
       latest batch was all-rejected — the "know it's working" case
  LR3: get_candidate_alert_last_run.evaluated counts ALL weekly-suggestion rows
       in the latest batch (survivors + rejected), not survivors only
  LR4: get_candidate_alert_last_run only reflects the MOST RECENT batch — an
       older batch (a different calendar date) must not inflate evaluated/survivors

Fixture: none — rows are seeded directly via database.insert_advisor_observation
(the real production write path), matching feedback_no_hardcoded_test_values —
every expected count is DERIVED by the test from what it seeded, never a literal.

Provenance: schema-derived from database.py:1081-1092 (advisor_observations),
acceptance_gate.py:80-82 (decision strings), and the mission brief above.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

import database as db_module
from database import init_db, insert_advisor_observation, run_migrations

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    __import__("pathlib").Path(__file__).parents[2] / "migrations" / "033_candidate_alert_state.sql"
)

_WEEKLY_ROLES = ("ASSET_SWAP", "LOGIC_CHANGE", "STRATEGY_BUILDER")


# ---------------------------------------------------------------------------
# DB isolation fixture (mirrors test_030_per_symphony_live_mode.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied."""
    db_path = str(tmp_path / "test_033.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


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


def _seed_observation(advisor_role: str, verdict: str | None, symphony_id: str = "sym-a") -> int:
    """Insert a real advisor_observations row via the production write path.

    Returns the new row id so tests can derive expectations from what they seeded
    rather than hardcoding ids.
    """
    return insert_advisor_observation(
        advisor_role=advisor_role,
        subject_type="test_subject",
        subject_id=f"cand-{time.time_ns()}",
        verdict=verdict,
        raw_response={},
        symphony_id=symphony_id,
    )


# ---------------------------------------------------------------------------
# M1: migration file exists
# ---------------------------------------------------------------------------


def test_migration_033_file_exists():
    """M1: migrations/033_candidate_alert_state.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/033_candidate_alert_state.sql with: "
        "CREATE TABLE IF NOT EXISTS candidate_alert_state (...);"
    )


# ---------------------------------------------------------------------------
# M2: candidate_alert_state present after init_db() only (fresh DB, H1 dual-write)
# ---------------------------------------------------------------------------


def test_fresh_db_has_candidate_alert_state_table(tmp_path, monkeypatch):
    """M2: A DB created via init_db() ONLY must already have candidate_alert_state.

    H1 dual-write mirror (same pattern as config_audit_log/030 and
    prism_audit_log/032) — fresh deployments must not silently lack the table.
    """
    db_path = str(tmp_path / "fresh_033.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()

    assert _table_exists(db_path, "candidate_alert_state"), (
        "init_db() does not create candidate_alert_state. Fix: add "
        "CREATE TABLE IF NOT EXISTS candidate_alert_state (...) to init_db() "
        "(database.py), mirroring the config_audit_log/prism_audit_log pattern."
    )


# ---------------------------------------------------------------------------
# M3: candidate_alert_state present after migration on a pre-033 DB
# ---------------------------------------------------------------------------


def test_upgraded_db_gains_candidate_alert_state_via_migration(tmp_path, monkeypatch):
    """M3: A pre-033 DB (missing candidate_alert_state) must gain it via run_migrations()."""
    db_path = str(tmp_path / "upgrade_033.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    for name in db_module._MIGRATION_FILES:
        if name != "033_candidate_alert_state.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    assert _table_exists(db_path, "candidate_alert_state"), (
        "run_migrations() did not create candidate_alert_state. "
        "033_candidate_alert_state.sql must include: "
        "CREATE TABLE IF NOT EXISTS candidate_alert_state (...);"
    )


# ---------------------------------------------------------------------------
# M4: run_migrations() is idempotent for 033
# ---------------------------------------------------------------------------


def test_run_migrations_idempotent_for_033(migrated_db):
    """M4: Calling run_migrations() again after 033 is applied must not raise."""
    try:
        run_migrations()
        run_migrations()
    except Exception as exc:
        pytest.fail(
            f"run_migrations() raised on second/third call after 033 already applied: {exc!r}."
        )
    assert _table_exists(migrated_db, "candidate_alert_state")


# ---------------------------------------------------------------------------
# M5: 033 registered in _MIGRATION_FILES after 032
# ---------------------------------------------------------------------------


def test_033_in_migration_files_after_032():
    """M5: '033_candidate_alert_state.sql' must be registered after '032_prism_audit_log.sql'."""
    assert "033_candidate_alert_state.sql" in db_module._MIGRATION_FILES, (
        "'033_candidate_alert_state.sql' not found in database._MIGRATION_FILES. "
        "Append it after '032_prism_audit_log.sql'."
    )
    assert "032_prism_audit_log.sql" in db_module._MIGRATION_FILES

    idx_032 = db_module._MIGRATION_FILES.index("032_prism_audit_log.sql")
    idx_033 = db_module._MIGRATION_FILES.index("033_candidate_alert_state.sql")
    assert idx_033 > idx_032, (
        f"'033_candidate_alert_state.sql' (index {idx_033}) must appear AFTER "
        f"'032_prism_audit_log.sql' (index {idx_032}) in _MIGRATION_FILES."
    )


# ---------------------------------------------------------------------------
# M6: migration does not reference the optimization DB
# ---------------------------------------------------------------------------


def test_033_migration_does_not_reference_opt_db():
    """M6: 033_candidate_alert_state.sql must not reference alphabot_opt or .opt.db.

    Architecture constraint 3: candidate_alert_state lives in the state DB only —
    zero optimization-DB placement.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert not re.search(r"alphabot_opt|\.opt\.db", sql, re.IGNORECASE), (
        "033_candidate_alert_state.sql must not reference the optimization DB "
        "(architecture constraint 3: two-DB boundary)."
    )


# ---------------------------------------------------------------------------
# V1-V4: viewed-marker accessor contract
# ---------------------------------------------------------------------------


class TestViewedMarkerAccessors:
    def test_marker_defaults_to_zero_on_fresh_db(self, migrated_db):
        """V1: get_candidate_alert_viewed_marker() returns 0 when unset."""
        assert db_module.get_candidate_alert_viewed_marker() == 0, (
            "Unset viewed-marker must default to 0 (AC-5 edge case: unset marker "
            "means nothing has been viewed yet)."
        )

    def test_set_marker_persists_and_reads_back(self, migrated_db):
        """V2: set_candidate_alert_viewed_marker() persists; read-back confirms."""
        db_module.set_candidate_alert_viewed_marker(7)
        assert db_module.get_candidate_alert_viewed_marker() == 7, (
            "set_candidate_alert_viewed_marker(7) must be readable via "
            "get_candidate_alert_viewed_marker()."
        )

    def test_set_marker_is_monotonic_never_regresses(self, migrated_db):
        """V3: a lower value passed to set_candidate_alert_viewed_marker must not regress it."""
        db_module.set_candidate_alert_viewed_marker(10)
        db_module.set_candidate_alert_viewed_marker(3)
        assert db_module.get_candidate_alert_viewed_marker() == 10, (
            "set_candidate_alert_viewed_marker(3) after marker=10 must NOT regress the "
            "marker to 3 — the stored value must be max(existing, new)."
        )

    def test_set_marker_upserts_without_raising_on_repeat_calls(self, migrated_db):
        """V4: repeated set_candidate_alert_viewed_marker calls never raise (single-row UPSERT)."""
        try:
            db_module.set_candidate_alert_viewed_marker(1)
            db_module.set_candidate_alert_viewed_marker(2)
            db_module.set_candidate_alert_viewed_marker(5)
        except Exception as exc:
            pytest.fail(
                f"Repeated set_candidate_alert_viewed_marker calls raised: {exc!r}. "
                "The single-row (id=1) table must be UPSERTed, not INSERTed."
            )


# ---------------------------------------------------------------------------
# MV1-MV3: mark_candidate_alert_viewed contract
# ---------------------------------------------------------------------------


class TestMarkCandidateAlertViewed:
    def test_mark_viewed_on_empty_table_returns_zero(self, migrated_db):
        """MV1: mark_candidate_alert_viewed() with no relevant rows returns 0, does not crash."""
        result = db_module.mark_candidate_alert_viewed()
        assert result == 0, f"Expected 0 on an empty table, got {result!r}."
        assert db_module.get_candidate_alert_viewed_marker() == 0

    def test_mark_viewed_advances_to_max_weekly_role_id_only(self, migrated_db):
        """MV2: mark_candidate_alert_viewed() must scope to ASSET_SWAP/LOGIC_CHANGE/
        STRATEGY_BUILDER only — a higher-id MARKET_PRISM row must be ignored
        (adversarial role-scoping — a naive MAX(id) over ALL advisor_observations
        would incorrectly pick up the MARKET_PRISM row's higher id)."""
        asset_swap_id = _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        # A later-inserted, higher-id row from an UNRELATED role — must not be picked up.
        _seed_observation("MARKET_PRISM", "ADOPT_CANDIDATE")

        result = db_module.mark_candidate_alert_viewed()
        assert result == asset_swap_id, (
            f"mark_candidate_alert_viewed() returned {result}, expected {asset_swap_id} "
            "(the max id among ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER rows only). "
            "A MARKET_PRISM row with a higher id must not influence the marker "
            "(role-scoping violation)."
        )

    def test_mark_viewed_takes_no_observation_id_argument(self, migrated_db):
        """MV3: mark_candidate_alert_viewed() must be callable with zero arguments —
        a caller must not be able to inject an arbitrary marker value."""
        import inspect

        sig = inspect.signature(db_module.mark_candidate_alert_viewed)
        required_params = [
            p
            for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        assert not required_params, (
            f"mark_candidate_alert_viewed() has required parameters {required_params} — "
            "it must be server-computed (zero required args) so a client can never set "
            "the marker to an arbitrary observation id."
        )


# ---------------------------------------------------------------------------
# NC1-NC7: get_candidate_alert_new_valid_count contract
# ---------------------------------------------------------------------------


class TestNewValidCount:
    def test_counts_adopt_candidate_rows_above_marker(self, migrated_db):
        """NC1: a fresh ADOPT_CANDIDATE row above the viewed marker counts."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        assert db_module.get_candidate_alert_new_valid_count() == 1

    def test_keep_incumbent_does_not_count(self, migrated_db):
        """NC2 (the crux adversarial case — verdict-classification correction):
        a KEEP_INCUMBENT row must NOT count as a valid new candidate, even though
        it is technically != 'REJECT_VETO_FAILED' (the feature-plan's literal,
        INCORRECT text). KEEP_INCUMBENT is the common "nothing changed" outcome —
        alerting on it is exactly the noise AC-2 prohibits."""
        _seed_observation("ASSET_SWAP", "KEEP_INCUMBENT")
        assert db_module.get_candidate_alert_new_valid_count() == 0, (
            "KEEP_INCUMBENT rows must NOT increment the badge count. Only "
            "verdict == 'ADOPT_CANDIDATE' is a survivor — see the module "
            "docstring's verdict-classification correction."
        )

    def test_reject_veto_failed_does_not_count(self, migrated_db):
        """NC3: a REJECT_VETO_FAILED row must not count."""
        _seed_observation("LOGIC_CHANGE", "REJECT_VETO_FAILED")
        assert db_module.get_candidate_alert_new_valid_count() == 0

    def test_null_or_unrecognised_verdict_does_not_count(self, migrated_db):
        """NC4: fail-closed — verdict=None or a typo'd/unrecognised verdict string
        must never be counted as a survivor (AC-6 edge case)."""
        _seed_observation("STRATEGY_BUILDER", None)
        _seed_observation("STRATEGY_BUILDER", "ADOPT_CANDIATE")  # typo, not the real string
        assert db_module.get_candidate_alert_new_valid_count() == 0, (
            "A missing or malformed verdict must be treated conservatively — "
            "NOT counted as a valid survivor (fail-closed)."
        )

    def test_non_weekly_role_does_not_count_even_with_matching_verdict(self, migrated_db):
        """NC5: role-scoping — a MARKET_PRISM (or OVERFITTING_CONSCIENCE/SPEC_CRITIC/
        ADD_CANDIDATE/NARRATOR) row must never count, even if its verdict string
        happens to equal 'ADOPT_CANDIDATE' (adversarial: proves the count query
        filters on advisor_role, not verdict alone)."""
        _seed_observation("MARKET_PRISM", "ADOPT_CANDIDATE")
        _seed_observation("OVERFITTING_CONSCIENCE", "ADOPT_CANDIDATE")
        assert db_module.get_candidate_alert_new_valid_count() == 0, (
            "Non-weekly-suggestion roles must never contribute to the candidate-alert "
            "count, regardless of their verdict string — role scope is exactly "
            "{ASSET_SWAP, LOGIC_CHANGE, STRATEGY_BUILDER}."
        )

    def test_excludes_rows_at_or_below_viewed_marker(self, migrated_db):
        """NC6: id > marker (strict), not id >= marker — the row AT the marker was
        already viewed and must not re-count."""
        seen_id = _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        db_module.set_candidate_alert_viewed_marker(seen_id)
        new_id = _seed_observation("LOGIC_CHANGE", "ADOPT_CANDIDATE")
        assert new_id > seen_id  # sanity: autoincrement is monotonic

        count = db_module.get_candidate_alert_new_valid_count()
        assert count == 1, (
            f"Expected exactly 1 new survivor (the row above the marker), got {count}. "
            "The row at-or-below the marker must be excluded (id > marker, not id >= marker)."
        )

    def test_zero_rows_returns_zero(self, migrated_db):
        """NC7: an empty advisor_observations table returns count 0, never raises."""
        assert db_module.get_candidate_alert_new_valid_count() == 0


# ---------------------------------------------------------------------------
# LR1-LR4: get_candidate_alert_last_run contract
# ---------------------------------------------------------------------------


class TestLastRunAggregate:
    def test_no_run_ever_returns_none(self, migrated_db):
        """LR1: zero weekly-suggestion rows ever written -> honest None (no run yet)."""
        assert db_module.get_candidate_alert_last_run() is None, (
            "get_candidate_alert_last_run() must return None when no ASSET_SWAP/"
            "LOGIC_CHANGE/STRATEGY_BUILDER row has ever been written."
        )

    def test_all_rejected_batch_shows_zero_survivors_not_none(self, migrated_db):
        """LR2 (the 'know it's working' case, AC-3): a batch that produced zero
        survivors must still surface — evaluated>0, survivors==0, NOT None."""
        _seed_observation("ASSET_SWAP", "KEEP_INCUMBENT")
        _seed_observation("LOGIC_CHANGE", "REJECT_VETO_FAILED")

        last_run = db_module.get_candidate_alert_last_run()
        assert last_run is not None, (
            "An all-rejected run must still return a last_run dict (not None) — "
            "this is the honest 'the weekly job ran and found nothing' signal."
        )
        assert last_run["survivors"] == 0
        assert last_run["evaluated"] == 2

    def test_evaluated_counts_survivors_and_rejected_together(self, migrated_db):
        """LR3: evaluated == total rows in the batch (survivors + rejected), not
        survivors-only."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        _seed_observation("ASSET_SWAP", "KEEP_INCUMBENT")
        _seed_observation("LOGIC_CHANGE", "REJECT_VETO_FAILED")

        last_run = db_module.get_candidate_alert_last_run()
        assert last_run["evaluated"] == 3, (
            f"Expected evaluated=3 (1 survivor + 2 rejected), got {last_run['evaluated']}."
        )
        assert last_run["survivors"] == 1

    def test_older_batch_does_not_inflate_latest_run(self, migrated_db, monkeypatch):
        """LR4: an older batch (a prior calendar date) must not be folded into the
        latest run's evaluated/survivors counts."""
        conn = db_module.get_connection()
        try:
            old_date = (datetime.now(UTC) - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO advisor_observations "
                "(advisor_role, subject_type, subject_id, verdict, raw_response, "
                " is_advisory_only, symphony_id, created_at) "
                "VALUES ('ASSET_SWAP', 'test_subject', 'old-cand', 'ADOPT_CANDIDATE', "
                " '{}', 1, 'sym-old', ?)",
                (old_date,),
            )
            conn.commit()
        finally:
            conn.close()

        # A fresh (today) batch — the only one that should be reflected.
        _seed_observation("ASSET_SWAP", "KEEP_INCUMBENT")
        _seed_observation("LOGIC_CHANGE", "REJECT_VETO_FAILED")

        last_run = db_module.get_candidate_alert_last_run()
        assert last_run["evaluated"] == 2, (
            f"Expected evaluated=2 (today's batch only), got {last_run['evaluated']}. "
            "A 14-day-old row must not be folded into the latest run's aggregate."
        )
        assert last_run["survivors"] == 0, (
            "The old ADOPT_CANDIDATE row is from a prior batch and must not count "
            "toward the latest run's survivors."
        )
