"""
RED tests — M-1 Structural Advisor Frozen-Eval Wall + CI Tripwire
(cycles advisor-frozen-eval-wall + test-frozen-eval-wall-tripwire combined).

All tests are RED on fork 3e0b83a until the implementer:
  1. Creates migrations/021_fold_role.sql (additive ALTER for researcher_dof_ledger)
  2. Appends '021_fold_role.sql' to database._MIGRATION_FILES
  3. Creates the researcher_dof_ledger table (migration 020 or 021)
  4. Ensures advisor_ro_query is the ONLY Advisor DB entry point (ai_advisor/ tree)
  5. Ensures _BARE_FOLD_ROLE_PREDICATES covers both '!=' and '<>' operator strings
  6. Makes the wall-breach tripwire write survive caller-swallowed exceptions

Fixture provenance: schema-derived from plans §Deliverables (DDL runs through
run_migrations()). No producer-computed values. All assertions are structural
or behavioural.

H3 SQL-NULL trap (binding hazard, §3.7 of decision-science-council-synthesis.md):
  A bare fold_role != 'frozen_eval' silently hides train/validation rows with
  NULL fold_role via SQL three-valued logic. The safe predicate must handle NULL.

M-1 safe-default for NULL fold_role (tripwire plan §D3 Scenario 2):
  An untagged row (fold_role IS NULL) is operationally indistinguishable from
  'frozen_eval' for safety purposes — a forgotten tag must fail-safe to
  "frozen_eval until proven train/validation". Untagged rows are EXCLUDED.
  Implementation freedom: COALESCE(fold_role,'NULL_SENTINEL') NOT IN
  ('frozen_eval','NULL_SENTINEL') or equivalent; test asserts the EFFECT.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The fold-partitioned table used for wall filter tests.
# autotune_runs is the production table (holds per-run walk-forward data with
# fold_role column added by migration 019).
_FOLD_TABLE = "autotune_runs"
_FOLD_TABLE_ID_COL = "symphony_id"

# Known bundle hash from frozen_eval_wall_seed.sql.
_SEED_BUNDLE_HASH = "deadbeef0000000000000000000000000000000000000000000000000000abcd"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def walled_db(tmp_path):
    """Isolated DB with all migrations applied, including the fold_role column.

    Seeds four autotune_runs rows covering all canonical fold_role values:
    'train', 'validation', 'frozen_eval', and NULL (untagged legacy row).

    advisor_observations table is also created so the wall-breach tripwire
    can write its audit row without failing on a missing table.
    """
    db_path = str(tmp_path / "wall_test.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        conn = sqlite3.connect(db_path)
        # Seed four fold_role rows into autotune_runs.
        for symphony_id, fold_role in [
            ("wall-train-1", "train"),
            ("wall-val-1", "validation"),
            ("wall-frozen-1", "frozen_eval"),
            ("wall-null-1", None),  # untagged legacy row — the H3 / M-1 safe-default case
        ]:
            conn.execute(
                "INSERT INTO autotune_runs (run_timestamp, symphony_id, fold_role) "
                "VALUES (?, ?, ?)",
                ("2026-01-01T09:30:00", symphony_id, fold_role),
            )
        # advisor_observations: required by the wall-breach write path.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS advisor_observations (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                advisor_role     TEXT NOT NULL,
                subject_type     TEXT NOT NULL,
                subject_id       TEXT NOT NULL,
                verdict          TEXT,
                raw_response     TEXT NOT NULL DEFAULT '{}',
                is_advisory_only INTEGER NOT NULL DEFAULT 1,
                spec_bundle_id   TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    return db_path


@pytest.fixture
def tripwire_db(tmp_path):
    """Isolated DB with spec_bundles and researcher_dof_ledger tables seeded
    for the post-freeze breach tripwire scenario.

    Requires migration 021 to create researcher_dof_ledger.  If the table does
    not exist after run_migrations(), the INSERT statements raise OperationalError
    and the test that depends on this fixture fails RED with a clear message.
    """
    db_path = str(tmp_path / "tripwire_test.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        conn = sqlite3.connect(db_path)
        # spec_bundles seed: one bundle with known frozen_at T0.
        conn.execute(
            "INSERT OR IGNORE INTO spec_bundles "
            "(bundle_hash, frozen_at, facets_json, horizon_bars, cvar_alpha, generator_family) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _SEED_BUNDLE_HASH,
                "2026-01-15T12:00:00",  # T0 — the freeze point
                '{"generator_family":"crra-seed","horizon_bars":63}',
                63,
                0.05,
                "crra-seed",
            ),
        )
        conn.commit()
        conn.close()

    return db_path


# ---------------------------------------------------------------------------
# Scenario 1 — advisor_ro_query excludes frozen_eval rows
# ---------------------------------------------------------------------------


def test_advisor_ro_query_excludes_frozen_eval_rows(walled_db):
    """M-1 (★): advisor_ro_query must never return a row with fold_role='frozen_eval'.

    The frozen-eval fold is the held-out evaluation partition.  Any row reaching
    the Advisor with that label is a structural wall breach.

    Discriminating-power: a naive passthrough (no filter at all) fails this
    scenario because wall-frozen-1 would appear in the result set.
    """
    with patch.object(db, "DB_FILE", walled_db):
        # Use a query that the helper's COALESCE wrap will apply to.
        # The query must NOT contain a bare fold_role != predicate.
        try:
            rows = db.advisor_ro_query(
                f"SELECT {_FOLD_TABLE_ID_COL}, fold_role FROM {_FOLD_TABLE}"
            )
        except RuntimeError as exc:
            # If a WALL_BREACH RuntimeError is raised here, the helper is NOT
            # applying a COALESCE wrap before execution — it is passing the raw
            # query through and detecting frozen_eval rows in the result set.
            # GREEN requires the helper to WRAP the query with a
            # COALESCE(fold_role,...) NOT IN ('frozen_eval',...) predicate so
            # the frozen_eval row is filtered at the SQL level and never appears
            # in rows, preventing the tripwire from firing.
            pytest.fail(
                f"advisor_ro_query raised RuntimeError instead of returning a "
                f"filtered result set: {exc}. "
                "M-1 plan §Deliverables 2: the helper must wrap the caller's SQL "
                "with WHERE COALESCE(fold_role,...) NOT IN ('frozen_eval',...) so "
                "frozen_eval rows are excluded at the DB level, never reaching the "
                "result set (and therefore never triggering the WALL_BREACH tripwire). "
                "The current implementation passes the raw query through and relies on "
                "post-hoc detection — that is insufficient: the COALESCE wrap must be applied."
            )

    role_by_id = {r[_FOLD_TABLE_ID_COL]: r["fold_role"] for r in rows}
    assert "wall-frozen-1" not in role_by_id, (
        "advisor_ro_query returned the row with fold_role='frozen_eval'. "
        "M-1: the wall must structurally exclude frozen-eval rows from every "
        "Advisor result set.  The wall is not applied."
    )


# ---------------------------------------------------------------------------
# Scenario 2 — NULL fold_role is excluded (M-1 safe-default / fail-safe)
# ---------------------------------------------------------------------------


def test_advisor_ro_query_excludes_null_fold_role_via_coalesce(walled_db):
    """H3 / M-1 safe-default: a row with fold_role IS NULL is operationally
    indistinguishable from fold_role='frozen_eval' — a forgotten tag must
    fail-safe to 'frozen_eval until proven train/validation'.

    The test asserts the EFFECT: untagged rows must not appear in the Advisor
    result set.  Implementation freedom on the exact predicate form:
      COALESCE(fold_role,'NULL_SENTINEL') NOT IN ('frozen_eval','NULL_SENTINEL')
      or any equivalent that excludes both frozen_eval and NULL.

    Discriminating-power: the simpler COALESCE(fold_role,'') != 'frozen_eval'
    form passes NULL rows through ('' != 'frozen_eval' is TRUE) — that form
    fails this scenario.  The correct form must exclude NULLs.

    Reference: tripwire plan §D3 Scenario 2; decision-science-council-synthesis
    §3.7 H3 NULL trap.
    """
    with patch.object(db, "DB_FILE", walled_db):
        try:
            rows = db.advisor_ro_query(
                f"SELECT {_FOLD_TABLE_ID_COL}, fold_role FROM {_FOLD_TABLE}"
            )
        except RuntimeError as exc:
            pytest.fail(
                f"advisor_ro_query raised RuntimeError instead of returning a "
                f"filtered result set: {exc}. "
                "M-1 plan §Deliverables 2: the helper must wrap the caller's SQL "
                "with a COALESCE predicate that excludes both frozen_eval AND NULL. "
                "The current implementation does not wrap the query — frozen_eval rows "
                "reach the result set and trigger the WALL_BREACH tripwire."
            )

    returned_ids = {r[_FOLD_TABLE_ID_COL] for r in rows}
    assert "wall-null-1" not in returned_ids, (
        "advisor_ro_query returned the untagged (fold_role IS NULL) row. "
        "M-1 safe-default: a forgotten fold_role tag must fail-safe to 'frozen_eval' "
        "and be excluded from Advisor reads.  "
        "COALESCE(fold_role,'') != 'frozen_eval' passes NULLs through — "
        "use COALESCE(fold_role,'NULL_SENTINEL') NOT IN ('frozen_eval','NULL_SENTINEL') "
        "or equivalent."
    )


# ---------------------------------------------------------------------------
# Scenario 3 — train and validation rows are included (no over-filtering)
# ---------------------------------------------------------------------------


def test_advisor_ro_query_includes_train_and_validation_rows(walled_db):
    """Negative identity: advisor_ro_query must return train and validation rows.

    A buggy 'exclude everything if any frozen_eval row is present in the DB'
    predicate fails this scenario — as does a predicate with an off-by-one that
    over-excludes rows with non-NULL fold_role values.

    Discriminating-power: a predicate that excludes ALL rows (e.g. WHERE 1=0)
    would pass Scenario 1 and 2 but fail here.
    """
    with patch.object(db, "DB_FILE", walled_db):
        try:
            rows = db.advisor_ro_query(
                f"SELECT {_FOLD_TABLE_ID_COL}, fold_role FROM {_FOLD_TABLE}"
            )
        except RuntimeError as exc:
            pytest.fail(
                f"advisor_ro_query raised RuntimeError instead of returning a "
                f"filtered result set: {exc}. "
                "M-1 plan §Deliverables 2: the helper must wrap the caller's SQL "
                "with a COALESCE predicate so train/validation rows are returned and "
                "frozen_eval/NULL rows are excluded at the DB level."
            )

    returned_ids = {r[_FOLD_TABLE_ID_COL] for r in rows}

    assert "wall-train-1" in returned_ids, (
        "advisor_ro_query did not return the row with fold_role='train'. "
        "The wall must exclude frozen_eval (and NULL) without over-blocking "
        "legitimate train fold data."
    )
    assert "wall-val-1" in returned_ids, (
        "advisor_ro_query did not return the row with fold_role='validation'. "
        "The wall must exclude frozen_eval (and NULL) without over-blocking "
        "legitimate validation fold data."
    )


# ---------------------------------------------------------------------------
# Scenario 4 — all Advisor entry points route through advisor_ro_query
# ---------------------------------------------------------------------------


def test_only_advisor_ro_query_is_an_advisor_read_path():
    """Architectural invariant (I-2 ★): no code in the ai_advisor/ module tree
    may call get_connection() or get_ro_connection() directly.

    Direct calls bypass the COALESCE guard and the wall-breach tripwire —
    they are structural side doors.  Every DB read from Advisor code must
    go through advisor_ro_query.

    Scope: any file matched by ai_advisor*.py or ai_advisor/**/*.py in the
    project root.  The helper itself (database.py) is NOT in scope; its
    internal cursor use is the implementation of the helper, not a bypass.

    Whitelist: none initially.  Any addition to the whitelist requires a
    code-review note citing M-1 (plan §Risk callouts — load-bearing).

    Discriminating-power: catches a developer who adds a new Advisor routine
    that opens its own cursor, bypassing the wall.
    """
    project_root = pathlib.Path(__file__).parents[2]

    # Gather all Advisor-side source files.
    advisor_files = list(project_root.glob("ai_advisor*.py"))
    advisor_dir = project_root / "ai_advisor"
    if advisor_dir.is_dir():
        advisor_files.extend(advisor_dir.rglob("*.py"))

    # Calls to these functions from Advisor code are forbidden.
    _FORBIDDEN_DIRECT_CALLS = frozenset({"get_connection", "get_ro_connection"})

    # Whitelist: empty.  Every entry must cite M-1 in a code-review note.
    _WHITELIST: frozenset[tuple[str, int]] = frozenset()

    violations: list[str] = []

    for advisor_file in advisor_files:
        if not advisor_file.exists():
            continue
        source = advisor_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(advisor_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_name: str | None = None
            if isinstance(node.func, ast.Attribute):
                called_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                called_name = node.func.id
            if called_name in _FORBIDDEN_DIRECT_CALLS:
                location = (advisor_file.name, node.lineno)
                if location not in _WHITELIST:
                    violations.append(
                        f"{advisor_file.name}:{node.lineno} — direct call to {called_name}()"
                    )

    assert not violations, (
        "Advisor code paths must use advisor_ro_query() exclusively for DB reads. "
        "Direct calls to get_connection() or get_ro_connection() bypass the "
        "COALESCE guard and the wall-breach tripwire (structural side door, M-1 ★). "
        "Violations:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Scenario 5 — wall-breach tripwire detects post-freeze frozen_eval touch
# ---------------------------------------------------------------------------


def test_wall_breach_tripwire_query_detects_post_freeze_frozen_eval_touch(
    tripwire_db,
):
    """M-1 tripwire: a researcher_dof_ledger row with touched_frozen_eval=1
    AND created_at > spec_bundles.frozen_at must be returned by the tripwire query.

    Uses canonical schema (migration 018):
      - spec_bundle_id TEXT soft FK to spec_bundles.bundle_hash
      - touched_frozen_eval INTEGER boolean (1 = post-freeze wall-breach)

    Sub-scenario A: seed contains the breach row → query returns exactly 1 row.
    Sub-scenario B: seed does NOT contain the breach row → query returns 0 rows.

    The tripwire query is expected to exist as database.query_wall_breach_tripwire()
    or as a helper function by that name (plan §Deliverables 3).

    Discriminating-power: a tripwire whose JOIN is broken (always returns 0, false-
    clean) fails sub-scenario A.  A tripwire whose filter is inverted (always fires,
    noise) fails sub-scenario B.  Both sub-scenarios must be asserted.

    Prerequisite: researcher_dof_ledger table must exist (migration 018).
    If the table is missing, the INSERT raises OperationalError with a clear
    message identifying the missing table.
    """
    assert hasattr(db, "query_wall_breach_tripwire"), (
        "database.query_wall_breach_tripwire() does not exist. "
        "Plan §Deliverables 3 requires this function (or the tripwire view). "
        "Implement it before the tripwire test can go GREEN."
    )

    with patch.object(db, "DB_FILE", tripwire_db):
        # --- Sub-scenario A: post-freeze breach row present ---
        conn = sqlite3.connect(tripwire_db)
        try:
            # Row A: pre-freeze honest evaluation — touched_frozen_eval=0, must NOT fire.
            conn.execute(
                "INSERT INTO researcher_dof_ledger "
                "(spec_bundle_id, facet_name, facet_category, decision_type, "
                " evidence_source, n_configs_searched, touched_frozen_eval, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _SEED_BUNDLE_HASH,
                    "gamma",
                    "parameter",
                    "FIXED",
                    "THEORY",
                    1,
                    0,  # not a wall breach
                    "2026-01-10T08:00:00",  # before frozen_at 2026-01-15
                ),
            )
            # Row B: post-freeze wall breach — touched_frozen_eval=1, after frozen_at.
            conn.execute(
                "INSERT INTO researcher_dof_ledger "
                "(spec_bundle_id, facet_name, facet_category, decision_type, "
                " evidence_source, n_configs_searched, touched_frozen_eval, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _SEED_BUNDLE_HASH,
                    "exit_threshold",
                    "parameter",
                    "REVISED",
                    "BACKTEST_SELECTION",
                    5,
                    1,  # wall-breach tripwire fired
                    "2026-01-20T09:00:00",  # after frozen_at 2026-01-15
                ),
            )
            conn.commit()
        finally:
            conn.close()

        breaches_a = db.query_wall_breach_tripwire()

    assert len(breaches_a) == 1, (
        f"Tripwire query returned {len(breaches_a)} rows with one seeded breach; "
        "expected exactly 1. "
        "M-1: the tripwire must detect the post-freeze touched_frozen_eval=1 row and only it. "
        "If 0: the JOIN is broken (check spec_bundle_id TEXT = bundle_hash TEXT) or filter wrong. "
        "If >1: the pre-freeze row is also being returned (date filter or flag filter incorrect)."
    )

    # --- Sub-scenario B: no breach row → zero results ---
    with patch.object(db, "DB_FILE", tripwire_db):
        conn = sqlite3.connect(tripwire_db)
        try:
            # Remove the breach row; leave the honest pre-freeze row.
            conn.execute(
                "DELETE FROM researcher_dof_ledger "
                "WHERE touched_frozen_eval = 1"
            )
            conn.commit()
        finally:
            conn.close()

        breaches_b = db.query_wall_breach_tripwire()

    assert len(breaches_b) == 0, (
        f"Tripwire query returned {len(breaches_b)} rows after breach row was removed; "
        "expected 0. "
        "M-1: the tripwire must return empty when no post-freeze touched_frozen_eval=1 rows exist. "
        "The pre-freeze honest row must NOT match the tripwire criteria."
    )


# ---------------------------------------------------------------------------
# Additional hostile scenario — tripwire AST walker covers both != and <> forms
# ---------------------------------------------------------------------------


def test_bare_fold_role_predicate_tuple_covers_both_inequality_operators():
    """CI lint invariant: database._BARE_FOLD_ROLE_PREDICATES must include both
    SQL inequality operator forms that carry the H3 NULL trap:
      - 'fold_role !='  (the Python/MySQL form)
      - 'fold_role <>'  (the SQL standard form)

    Both forms silently exclude NULL fold_role rows via SQL three-valued logic.
    A guard that checks only '!=' misses any query using '<>', allowing a bare
    '<>' predicate to bypass the COALESCE requirement check.

    This test reads the live constant from the database module — it fails if
    either form is absent or if the constant is renamed/removed.
    """
    assert hasattr(db, "_BARE_FOLD_ROLE_PREDICATES"), (
        "database._BARE_FOLD_ROLE_PREDICATES does not exist. "
        "Plan §Risk callouts: the predicate guard tuple must be a named constant "
        "(plan deliverable 2, docstring 'H3 SQL-NULL trap')."
    )

    predicates = db._BARE_FOLD_ROLE_PREDICATES

    assert "fold_role !=" in predicates, (
        "'fold_role !=' is missing from _BARE_FOLD_ROLE_PREDICATES. "
        "The != form of the bare inequality must be detected and rejected. "
        "See database.py §Advisor Wall comment."
    )
    assert "fold_role <>" in predicates, (
        "'fold_role <>' is missing from _BARE_FOLD_ROLE_PREDICATES. "
        "The SQL standard <> form carries identical H3 NULL trap semantics. "
        "A guard that only checks '!=' misses queries using the standard form."
    )


# ---------------------------------------------------------------------------
# Additional scenario — _write_wall_breach_observation write-then-swallow contract
# ---------------------------------------------------------------------------


def test_write_wall_breach_observation_writes_audit_row_and_swallows_own_exceptions(
    walled_db, tmp_path
):
    """Plan §Risk callouts: _write_wall_breach_observation must:
      (a) Commit a WALL_BREACH row to advisor_observations when the table exists.
      (b) Swallow its own internal exceptions (log, never raise) when the write
          fails — so the caller's raise is never suppressed by a write-side error.

    This tests the helper directly, decoupled from advisor_ro_query's bypass-
    detection design (which may change). The write-then-swallow contract is
    independently load-bearing: if the helper raises instead of swallowing, a
    write failure would propagate to the caller and mask the original breach
    RuntimeError.

    Sub-scenario A: table exists → row is committed, function returns normally.
    Sub-scenario B: table absent → function swallows the OperationalError and
      returns normally (does NOT raise).

    Discriminating-power:
      - A helper that raises on write failure fails sub-scenario B.
      - A helper that never writes anything fails sub-scenario A.
    """
    assert hasattr(db, "_write_wall_breach_observation"), (
        "database._write_wall_breach_observation() does not exist. "
        "Plan §Risk callouts: this helper is the write side of the write-then-raise "
        "contract and must be independently testable."
    )

    sql_fragment = "SELECT * FROM autotune_runs -- test audit fragment"

    # --- Sub-scenario A: advisor_observations table exists → row is written ---
    with patch.object(db, "DB_FILE", walled_db):
        # Must not raise — best-effort write, always returns None.
        db._write_wall_breach_observation(sql_fragment)

        conn = sqlite3.connect(walled_db)
        try:
            breach_rows = conn.execute(
                "SELECT advisor_role, subject_type, verdict, raw_response "
                "FROM advisor_observations WHERE advisor_role = 'WALL_BREACH'"
            ).fetchall()
        finally:
            conn.close()

    assert len(breach_rows) >= 1, (
        "No WALL_BREACH row found in advisor_observations after calling "
        "_write_wall_breach_observation(). "
        "Plan §Risk callouts: the helper must commit the audit row to "
        "advisor_observations when the table exists."
    )
    assert breach_rows[0][1] == "fold_role_wall", (
        f"WALL_BREACH row subject_type={breach_rows[0][1]!r}; expected 'fold_role_wall'. "
        "The audit row schema is part of the breach-detection contract."
    )
    assert breach_rows[0][2] == "BREACH", (
        f"WALL_BREACH row verdict={breach_rows[0][2]!r}; expected 'BREACH'."
    )
    assert sql_fragment[:50] in breach_rows[0][3], (
        "WALL_BREACH row raw_response must contain the SQL fragment passed to the helper. "
        "The audit trail requires the offending SQL to be preserved."
    )

    # --- Sub-scenario B: advisor_observations absent → swallows, does NOT raise ---
    # Use tmp_path (pytest-managed) to avoid Windows file-lock issues on cleanup.
    empty_db_path = str(tmp_path / "no_obs.db")

    # Initialise schema without advisor_observations.
    conn = sqlite3.connect(empty_db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(migration_name TEXT PRIMARY KEY, applied_at TEXT)"
    )
    conn.commit()
    conn.close()

    with patch.object(db, "DB_FILE", empty_db_path):
        # Must NOT raise — the helper swallows its own write failures.
        try:
            db._write_wall_breach_observation(sql_fragment)
        except Exception as exc:
            pytest.fail(
                f"_write_wall_breach_observation raised {type(exc).__name__} "
                f"when advisor_observations was absent: {exc}. "
                "Plan §Risk callouts: the helper must swallow its own exceptions "
                "so a write failure never suppresses the caller's RuntimeError."
            )


# ---------------------------------------------------------------------------
# Additional hostile scenario — nested subquery with fold_role from different alias
# ---------------------------------------------------------------------------


def test_advisor_ro_query_bare_predicate_guard_fires_on_nested_subquery_alias(walled_db):
    """Hostile: a nested subquery that projects fold_role under a different table
    alias must still be caught by the predicate guard if the inner SQL contains
    a bare fold_role != predicate.

    The guard inspects the SQL string passed by the caller.  A caller that wraps
    a bare fold_role != in a subquery and passes the outer SELECT must be rejected
    when the inner SQL string contains the forbidden pattern.

    This tests the string-scan approach covers subquery strings, not just
    top-level WHERE clauses.
    """
    assert hasattr(db, "advisor_ro_query"), (
        "database.advisor_ro_query() does not exist."
    )

    # SQL where the bare != is inside a subquery — the forbidden pattern is present
    # in the string regardless of nesting depth.
    nested_bare_sql = (
        f"SELECT outer.{_FOLD_TABLE_ID_COL} FROM "
        f"(SELECT {_FOLD_TABLE_ID_COL}, fold_role FROM {_FOLD_TABLE} "
        f"WHERE fold_role != 'frozen_eval') AS outer"
    )

    with patch.object(db, "DB_FILE", walled_db):
        with pytest.raises((ValueError, Exception)) as exc_info:
            db.advisor_ro_query(nested_bare_sql)

    assert isinstance(exc_info.value, ValueError), (
        "advisor_ro_query must raise ValueError (predicate guard, not DB error) "
        "for a bare fold_role != inside a nested subquery. "
        "The string-scan approach catches the forbidden pattern at any nesting depth."
    )


# ---------------------------------------------------------------------------
# Additional hostile scenario — parameterized SQL with predicate in string concat
# ---------------------------------------------------------------------------


def test_advisor_ro_query_bare_predicate_guard_fires_on_string_concat_predicate(walled_db):
    """Hostile: a caller who constructs the bare predicate via string concatenation
    still passes a string containing the forbidden pattern to advisor_ro_query.

    The guard must fire on the final SQL string value — not on the form of the
    Python expression that produced it.

    Note: this test does NOT test that the guard catches runtime-generated SQL
    assembled AFTER the call (that is impossible to guard against in a pure
    string-check approach); it verifies the guard fires when the assembled string
    contains the forbidden pattern, regardless of how the caller built it.
    """
    assert hasattr(db, "advisor_ro_query"), (
        "database.advisor_ro_query() does not exist."
    )

    # Caller assembles the SQL via concatenation before passing it in.
    table_name = _FOLD_TABLE
    predicate_fragment = "fold_role != 'frozen_eval'"
    concat_sql = f"SELECT id FROM {table_name} WHERE {predicate_fragment}"

    with patch.object(db, "DB_FILE", walled_db):
        with pytest.raises((ValueError, Exception)) as exc_info:
            db.advisor_ro_query(concat_sql)

    assert isinstance(exc_info.value, ValueError), (
        "advisor_ro_query must raise ValueError for a bare fold_role != predicate "
        "even when the caller assembles the SQL via string concatenation. "
        "The guard inspects the final string value — the construction method is irrelevant."
    )


# ---------------------------------------------------------------------------
# BLOCK-S2: advisor_observations migration must exist and be registered
# (rev-wall finding — production wall-breach writes silently fail without it)
# ---------------------------------------------------------------------------


def test_advisor_observations_migration_is_registered_in_migration_files():
    """BLOCK-S2 (rev-wall): _write_wall_breach_observation INSERTs into
    advisor_observations, but that table has no registered migration file.
    On a production DB, the INSERT fails silently (exception swallowed at
    database.py _write_wall_breach_observation catch block) — the audit trail
    is a permanent no-op until the migration lands.

    This test asserts that a migration file for advisor_observations is listed
    in _MIGRATION_FILES AND appears after 016_spec_bundles.sql (which is the
    last migration before this feature area was added).

    The canonical name per team convention is '017_advisor_observations.sql'
    (referenced in test_019_fold_role_columns.py:86 as 'migration 017').

    Discriminating-power: a test suite that never runs _write_wall_breach_observation
    against a production-schema DB will not catch this gap.  This test fails
    until the migration file is created and registered.
    """
    migration_list = db._MIGRATION_FILES

    assert "017_advisor_observations.sql" in migration_list, (
        "'017_advisor_observations.sql' is missing from database._MIGRATION_FILES. "
        "BLOCK-S2: _write_wall_breach_observation inserts into advisor_observations "
        "which does not exist on production DBs until this migration runs. "
        "The wall-breach audit trail is silently a no-op on every production deployment. "
        "Add migrations/017_advisor_observations.sql with the table DDL and register it "
        "in _MIGRATION_FILES after '016_spec_bundles.sql'."
    )

    # Ordering: 017 must follow 016.
    assert "016_spec_bundles.sql" in migration_list, (
        "'016_spec_bundles.sql' unexpectedly absent — cannot verify ordering."
    )
    idx_016 = migration_list.index("016_spec_bundles.sql")
    idx_017 = migration_list.index("017_advisor_observations.sql")
    assert idx_017 > idx_016, (
        f"'017_advisor_observations.sql' (index {idx_017}) must appear AFTER "
        f"'016_spec_bundles.sql' (index {idx_016}). "
        "Migration list is append-only in dependency order."
    )


def test_advisor_observations_table_exists_after_run_migrations(tmp_path):
    """BLOCK-S2 (rev-wall): After run_migrations() on a fresh DB, the
    advisor_observations table must exist with the correct schema.

    Asserts PRAGMA table_info columns: id, created_at, advisor_role,
    subject_type, subject_id, verdict, raw_response, is_advisory_only,
    spec_bundle_id.

    Discriminating-power: a migration that creates a differently-named table
    or omits required columns fails this test.
    """
    assert "017_advisor_observations.sql" in db._MIGRATION_FILES, (
        "'017_advisor_observations.sql' missing from _MIGRATION_FILES — "
        "cannot test table existence after run_migrations()."
    )

    db_path = str(tmp_path / "test_017.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        conn = sqlite3.connect(db_path)
        try:
            columns = conn.execute(
                "PRAGMA table_info(advisor_observations)"
            ).fetchall()
        finally:
            conn.close()

    col_names = {row[1] for row in columns}

    assert col_names, (
        "advisor_observations table not found after run_migrations(). "
        "BLOCK-S2: the migration must create the table."
    )

    required_cols = {
        "id", "created_at", "advisor_role", "subject_type",
        "subject_id", "verdict", "raw_response", "is_advisory_only",
    }
    missing = required_cols - col_names
    assert not missing, (
        f"advisor_observations is missing columns: {sorted(missing)}. "
        "BLOCK-S2: the table schema must match the contract used by "
        "_write_wall_breach_observation (see database.py INSERT statement)."
    )


# ---------------------------------------------------------------------------
# Gap test — COALESCE wrap must not break queries on tables without fold_role
# ---------------------------------------------------------------------------


def test_advisor_ro_query_succeeds_on_table_without_fold_role_column(walled_db):
    """Structural correctness: advisor_ro_query must return results for a query
    against a table that has no fold_role column.

    The COALESCE outer-wrap appends:
      WHERE COALESCE(fold_role,'NULL_SENTINEL') NOT IN ('frozen_eval','NULL_SENTINEL')
    to every query.  If the inner subquery does not project fold_role, SQLite
    raises OperationalError: no such column: fold_role — the Advisor cannot read
    any non-partitioned table (spec_bundles, schema_migrations, fleet_alert_state,
    advisor_observations itself, etc.).

    This is a correctness defect: the COALESCE predicate must be conditional on
    whether fold_role is in the projected columns, or the outer wrap must only
    be applied when the queried table has a fold_role column.

    Discriminating-power:
      - The current COALESCE-wrap implementation raises OperationalError here.
      - A correct implementation returns the spec_bundles row without error.

    GREEN requires: advisor_ro_query('SELECT bundle_hash FROM spec_bundles')
    returns the seeded row and does NOT raise any exception.

    Reference: database.advisor_ro_query docstring comment (incorrect):
      'Queries that do not project fold_role are unaffected: the outer WHERE
       references a column not in the subquery output and is effectively a no-op.'
    SQLite does NOT silently ignore a missing column in an outer WHERE — it
    raises OperationalError.  The comment is wrong; the implementation must be fixed.
    """
    with patch.object(db, "DB_FILE", walled_db):
        # spec_bundles exists after init_db() — it has no fold_role column.
        # This query must succeed and return at least the schema_migrations row
        # (init_db() always creates schema_migrations).
        try:
            rows = db.advisor_ro_query(
                "SELECT migration_name FROM schema_migrations LIMIT 5"
            )
        except Exception as exc:
            pytest.fail(
                f"advisor_ro_query raised {type(exc).__name__} on a query against "
                f"schema_migrations (no fold_role column): {exc}. "
                "The COALESCE outer-wrap blindly appends "
                "WHERE COALESCE(fold_role,'NULL_SENTINEL') NOT IN (...) to every query. "
                "When fold_role is not in the subquery output, SQLite raises "
                "OperationalError: no such column: fold_role. "
                "Fix: only apply the COALESCE predicate when the inner query projects "
                "fold_role, or query the table's column list before wrapping."
            )

    # Result shape: list of sqlite3.Row; must not be empty (schema_migrations always
    # has at least one row after init_db()).
    assert isinstance(rows, list), (
        "advisor_ro_query must return a list of sqlite3.Row objects."
    )
    assert len(rows) >= 1, (
        "schema_migrations must have at least one row after init_db(). "
        "If 0 rows: init_db() did not apply any migrations — fixture setup failed."
    )
