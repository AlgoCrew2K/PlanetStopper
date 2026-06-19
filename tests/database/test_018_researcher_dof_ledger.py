"""
RED tests — Migration 018_researcher_dof_ledger (NN1 multiple-testing DoF ledger).

Coverage targets (all tests must FAIL RED until the GREEN implementation lands):

  Plan-mandated tests (plan.md §Golden-fixture tests required):
    1. Honest Phase-1 case yields S = 0 — three theory/calibration rows;
       count_dof_backtest_selections() returns 0.  Consumer-side: N_effective == N_optuna
       (council §2.2 property-1 byte-identity assertion).
    2. Single P&L-toured row contributes its n_configs_searched to S — one row with
       evidence_source='BACKTEST_SELECTION' and n_configs_searched=4 gives S=4.
       (Defect 2 sub-sweep contribution, v3-evaluation §A.0.)
    3. touched_frozen_eval=1 records the wall breach — a row with this flag set is
       queryable so the tripwire in plan 019 can read it.
    4. Append-only — no update or delete accessor exists in database module.
    5. Schema-validator — fixture DB carries the table with all expected columns and
       the three Phase-1 theory-frozen seed rows.
    6. Conservative-upper-bound property — SUM(n_configs_searched) >= COUNT(DISTINCT)
       when any bundle has n_configs_searched > 1; documents the binding accumulator
       choice so a future reviewer cannot quietly switch to bundle-distinct COUNT.

  Hostile edge tests:
    7. Idempotent re-run — run_migrations() twice on same DB must not raise.
    8. mode=ro connection SELECTs from researcher_dof_ledger without error.
    9. Migration file appended after 017 in _MIGRATION_FILES.
   10. Evidence-source enum guard — invalid value raises ValueError.
   11. Two-DB boundary — new accessors must not reference alphabot_opt or opt.db.

DB isolation: each test receives a fresh SQLite file under tmp_path via the
migrated_db fixture (identical pattern to test_spec_bundles.py).
run_migrations() is called within the fixture, so tests exercise the full
migration stack including 018.

Fixture provenance (D-2 ★): schema-derived with runtime validator.
See tests/fixtures/database/researcher_dof_ledger/researcher_dof_ledger_schema.json.

Accessor signatures expected from the GREEN implementer (plan §Deliverables #4):

    insert_dof_ledger_row(
        *,
        facet_name:          str,
        facet_category:      str,          # 'specification' | 'parameter'
        decision_type:       str,          # 'FIXED' | 'SEARCHED' | 'REVISED' | 'OOS_PEEK'
        evidence_source:     str,          # enum — see _VALID_EVIDENCE_SOURCES below
        n_configs_searched:  int = 1,      # sub-sweep count for the S accumulator
        touched_frozen_eval: int = 0,      # boolean: 1 = wall-breach tripwire fired
        spec_bundle_id:      str | None = None,
        justification:       str | None = None,
    ) -> int                               # returns new row id

    get_dof_ledger_for_bundle(spec_bundle_id: str) -> list[dict]
        # returns all rows tagged with this spec_bundle_id

    count_dof_backtest_selections(spec_bundle_id: str | None = None) -> int
        # returns SUM(n_configs_searched) WHERE evidence_source = 'BACKTEST_SELECTION'
        # if spec_bundle_id is given, filters to that bundle only
        # this is the S accumulator — sub-sweep sum, NOT COUNT(DISTINCT)
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# Canonical enum constants — sourced from plan.md + council synthesis
# Pin here so any deviation surfaces immediately.
# ---------------------------------------------------------------------------

_VALID_EVIDENCE_SOURCES: frozenset[str] = frozenset(
    {
        "THEORY",
        "MANDATE",
        "STYLIZED_FACT",
        "CALIBRATION",
        "BACKTEST_SELECTION",
        "OOS",
    }
)

# These are the evidence sources that do NOT contribute to S (honest Phase-1 case).
_ZERO_S_SOURCES: frozenset[str] = frozenset(
    {
        "THEORY",
        "MANDATE",
        "STYLIZED_FACT",
        "CALIBRATION",
        "OOS",
    }
)

_VALID_FACET_CATEGORIES: frozenset[str] = frozenset({"specification", "parameter"})
_VALID_DECISION_TYPES: frozenset[str] = frozenset({"FIXED", "SEARCHED", "REVISED", "OOS_PEEK"})

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "database"
    / "researcher_dof_ledger"
    / "researcher_dof_ledger_schema.json"
)

_MIGRATION_PATH = pathlib.Path(__file__).parents[2] / "migrations" / "018_researcher_dof_ledger.sql"

# ---------------------------------------------------------------------------
# DB isolation fixture — function-scoped, redirects DB_FILE, runs full
# migration stack so 018 is applied alongside 016, 019.
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied.

    Uses monkeypatch.setattr on DB_FILE, consistent with test_spec_bundles.py
    and test_019_fold_role_columns.py patterns.  Calls both init_db() (core
    tables / execution_lock) and run_migrations() (all numbered migrations
    including 018).

    Scope: function — every test starts clean.
    """
    db_path = str(tmp_path / "test_018.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _table_columns(db_path: str, table_name: str) -> dict[str, dict]:
    """Return {col_name: {notnull, dflt_value, pk, type}} via PRAGMA table_info."""
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


def _insert_dof_row_direct(
    db_path: str,
    *,
    facet_name: str,
    facet_category: str,
    decision_type: str,
    evidence_source: str,
    n_configs_searched: int = 1,
    touched_frozen_eval: int = 0,
    spec_bundle_id: str | None = None,
    justification: str | None = None,
) -> int:
    """Insert directly via sqlite3, bypassing application-layer guards.

    Used to set up preconditions for tests that check the read/count accessors.
    Does NOT validate evidence_source — that is intentional; tests that exercise
    the application-layer guard use insert_dof_ledger_row() instead.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO researcher_dof_ledger "
            "(facet_name, facet_category, decision_type, evidence_source, "
            "n_configs_searched, touched_frozen_eval, spec_bundle_id, justification) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                facet_name,
                facet_category,
                decision_type,
                evidence_source,
                n_configs_searched,
                touched_frozen_eval,
                spec_bundle_id,
                justification,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


# ===========================================================================
# Plan-mandated test 1: Honest Phase-1 case yields S = 0
# ===========================================================================


def test_honest_phase1_three_theory_rows_yield_s_equals_zero(migrated_db):
    """
    When the only DoF entries are the three Phase-1 theory-frozen facets
    (gamma/THEORY, utility_family/THEORY-or-MANDATE, wealth_argument/CALIBRATION)
    count_dof_backtest_selections() must return 0.

    This is the council §2.2 property-1 byte-identity assertion: S=0 means
    N_effective = N_optuna + 0 = N_optuna, which is byte-identical to today's
    BHY haircut.

    Tests both the bundle-scoped and global-scope variants of the accessor
    (the plan requires the global scope at minimum; bundle scope is the Phase-1
    consumer surface).
    """
    from database import count_dof_backtest_selections, insert_dof_ledger_row  # noqa: PLC0415

    bundle_id = "phase1-honest-bundle-abc123"

    # Insert the three Phase-1 theory-frozen rows.
    insert_dof_ledger_row(
        facet_name="gamma",
        facet_category="specification",
        decision_type="FIXED",
        evidence_source="THEORY",
        n_configs_searched=1,
        spec_bundle_id=bundle_id,
        justification="CRRA risk-aversion parameter; frozen by utility theory — not P&L-selected.",
    )
    insert_dof_ledger_row(
        facet_name="utility_family",
        facet_category="specification",
        decision_type="FIXED",
        evidence_source="THEORY",
        n_configs_searched=1,
        spec_bundle_id=bundle_id,
        justification="CRRA family frozen by theoretical commitment to expected-utility axioms.",
    )
    insert_dof_ledger_row(
        facet_name="wealth_argument",
        facet_category="specification",
        decision_type="FIXED",
        evidence_source="CALIBRATION",
        n_configs_searched=1,
        spec_bundle_id=bundle_id,
        justification="W-H2 derivation result; calibrated from terminal-wealth axiom.",
    )

    s_global = count_dof_backtest_selections()
    assert s_global == 0, (
        f"count_dof_backtest_selections() returned {s_global} but expected 0. "
        "All three Phase-1 seed rows have evidence_source != 'BACKTEST_SELECTION'; "
        "the S accumulator must be 0 (council §2.2 property 1: honest Phase-1 gives "
        "N_effective = N_optuna, byte-identical to today's haircut)."
    )

    s_bundle = count_dof_backtest_selections(spec_bundle_id=bundle_id)
    assert s_bundle == 0, (
        f"count_dof_backtest_selections(spec_bundle_id={bundle_id!r}) returned {s_bundle}; "
        "expected 0 — none of the three seed rows have evidence_source='BACKTEST_SELECTION'."
    )


def test_n_effective_equals_n_optuna_when_s_is_zero(migrated_db):
    """
    Consumer-side byte-identity check: when S=0, N_effective = N_optuna + S
    equals N_optuna.

    This is the arithmetic form of council §2.2 property 1.  The test does not
    hardcode any N_optuna value — it asserts the additive relationship holds
    for an arbitrary positive integer.
    """
    from database import count_dof_backtest_selections  # noqa: PLC0415

    # No rows inserted — table is empty.
    s = count_dof_backtest_selections()
    assert s == 0, (
        f"count_dof_backtest_selections() on empty table returned {s}; expected 0. "
        "No rows exist, so S must be 0."
    )

    # Arithmetic invariant: any N_optuna + S = N_optuna when S = 0.
    # We use a sentinel value to make the intent clear without hardcoding a
    # business-meaningful number.
    sentinel_n_optuna = 42  # arbitrary positive integer — the value is not load-bearing
    n_effective = sentinel_n_optuna + s
    assert n_effective == sentinel_n_optuna, (
        f"N_effective = N_optuna + S = {sentinel_n_optuna} + {s} = {n_effective}; "
        f"expected {sentinel_n_optuna}.  When S=0, N_effective must equal N_optuna exactly "
        "(council §2.2 property 1: additive correction with zero P&L-touring is byte-identical)."
    )


# ===========================================================================
# Plan-mandated test 2: Single P&L-toured row contributes n_configs_searched to S
# ===========================================================================


def test_single_backtest_selection_row_contributes_full_sub_sweep_count_to_s(migrated_db):
    """
    One row with evidence_source='BACKTEST_SELECTION' and n_configs_searched=4
    must yield S=4 from count_dof_backtest_selections().

    This is the binding Defect 2 sub-sweep contribution (v3-evaluation §A.0):
    the accumulator is SUM(n_configs_searched), NOT COUNT(DISTINCT spec_bundle_id).
    A bundle that received a 4-configuration sub-sweep contributes 4 to S, not 1.
    """
    from database import count_dof_backtest_selections, insert_dof_ledger_row  # noqa: PLC0415

    insert_dof_ledger_row(
        facet_name="exit_threshold",
        facet_category="parameter",
        decision_type="SEARCHED",
        evidence_source="BACKTEST_SELECTION",
        n_configs_searched=4,  # sub-sweep of 4 configurations
        justification="Searched 4 exit-threshold values on strategy P&L; selected the best-fitting.",
    )

    s = count_dof_backtest_selections()
    assert s == 4, (
        f"count_dof_backtest_selections() returned {s}; expected 4. "
        "One row with evidence_source='BACKTEST_SELECTION' and n_configs_searched=4 "
        "must contribute 4 to S (SUM accumulator, not COUNT). "
        "Defect 2 binding: a P&L-toured spec that received a sub-sweep of K "
        "configurations contributes K to S (v3-evaluation §A.0)."
    )


def test_multiple_backtest_selection_rows_accumulate_by_sum_not_count(migrated_db):
    """
    Two BACKTEST_SELECTION rows with n_configs_searched=4 and 2 must give S=6,
    not S=2 (COUNT(DISTINCT)) or S=1 (COUNT rows) or S=anything_else.

    This is the conservative-upper-bound implementation guard: SUM >= COUNT when
    any bundle has n_configs_searched > 1.  It pins the binding accumulator so a
    future reviewer cannot quietly switch to COUNT(DISTINCT spec_bundle_id).
    """
    from database import count_dof_backtest_selections, insert_dof_ledger_row  # noqa: PLC0415

    bundle_a = "bundle-toured-a"
    bundle_b = "bundle-toured-b"

    insert_dof_ledger_row(
        facet_name="entry_signal",
        facet_category="specification",
        decision_type="SEARCHED",
        evidence_source="BACKTEST_SELECTION",
        n_configs_searched=4,
        spec_bundle_id=bundle_a,
    )
    insert_dof_ledger_row(
        facet_name="rebalance_frequency",
        facet_category="parameter",
        decision_type="SEARCHED",
        evidence_source="BACKTEST_SELECTION",
        n_configs_searched=2,
        spec_bundle_id=bundle_b,
    )

    s = count_dof_backtest_selections()
    # SUM(n_configs_searched) = 4 + 2 = 6.
    # COUNT(DISTINCT spec_bundle_id) = 2 — this is NOT the correct accumulator.
    assert s == 6, (
        f"count_dof_backtest_selections() returned {s}; expected 6. "
        "Two rows with n_configs_searched=4 and 2 must sum to 6. "
        "An implementation returning 2 (COUNT(DISTINCT)) or 1 (COUNT rows) "
        "would undercount the P&L-touring penalty (Defect 2, v3-evaluation §A.0). "
        "The binding accumulator is SUM(n_configs_searched)."
    )


def test_non_backtest_sources_do_not_contribute_to_s(migrated_db):
    """
    Rows with evidence_source in the zero-S set (THEORY/MANDATE/STYLIZED_FACT/
    CALIBRATION/OOS) must be excluded from S even if n_configs_searched > 1.

    A misconfigured accumulator that sums ALL rows regardless of evidence_source
    would fail here.
    """
    from database import count_dof_backtest_selections, insert_dof_ledger_row  # noqa: PLC0415

    # Insert one row from each zero-S source with n_configs_searched > 1 to catch
    # an implementation that sums ALL n_configs_searched values.
    for source in sorted(_ZERO_S_SOURCES):
        insert_dof_ledger_row(
            facet_name=f"facet_{source.lower()}",
            facet_category="specification",
            decision_type="FIXED",
            evidence_source=source,
            n_configs_searched=3,  # > 1 to expose an incorrect SUM(all)
        )

    s = count_dof_backtest_selections()
    assert s == 0, (
        f"count_dof_backtest_selections() returned {s}; expected 0. "
        f"Inserted {len(_ZERO_S_SOURCES)} rows with zero-S evidence sources, "
        "each with n_configs_searched=3. "
        "S must be 0: only BACKTEST_SELECTION rows contribute (council §2.2). "
        "An implementation returning > 0 sums ALL n_configs_searched, which "
        "erroneously penalises theory-frozen facets."
    )


# ===========================================================================
# Plan-mandated test 3: touched_frozen_eval=1 records the wall breach
# ===========================================================================


def test_wall_breach_row_with_touched_frozen_eval_is_queryable(migrated_db):
    """
    A row inserted with touched_frozen_eval=1 must be retrievable via
    get_dof_ledger_for_bundle() or a direct SQL query.

    The wall-breach tripwire (plan 019) reads this column to fire its alert.
    This test verifies the column is present, stored, and visible to reads —
    not that the tripwire logic itself fires (that is plan 019's domain).
    """
    from database import get_dof_ledger_for_bundle, insert_dof_ledger_row  # noqa: PLC0415

    bundle_id = "breach-test-bundle-xyz"

    insert_dof_ledger_row(
        facet_name="oos_peek_facet",
        facet_category="specification",
        decision_type="OOS_PEEK",
        evidence_source="OOS",
        n_configs_searched=1,
        touched_frozen_eval=1,  # wall breach recorded
        spec_bundle_id=bundle_id,
        justification="OOS_PEEK event recorded; wall-breach tripwire must see this row.",
    )

    rows = get_dof_ledger_for_bundle(bundle_id)
    assert rows, (
        f"get_dof_ledger_for_bundle({bundle_id!r}) returned empty list. "
        "A row was inserted with spec_bundle_id matching the query; it must be returned."
    )

    breach_rows = [r for r in rows if r.get("touched_frozen_eval") == 1]
    assert breach_rows, (
        f"No rows in get_dof_ledger_for_bundle() result have touched_frozen_eval=1. "
        f"Returned rows: {rows!r}. "
        "The wall-breach column must be stored and readable — plan 019's tripwire "
        "depends on querying this column."
    )


def test_touched_frozen_eval_default_is_zero(migrated_db):
    """
    A row inserted without specifying touched_frozen_eval must have
    touched_frozen_eval=0 (the DEFAULT 0 from the migration schema).

    Verifies the schema default fires without explicit caller specification.
    """
    from database import get_dof_ledger_for_bundle, insert_dof_ledger_row  # noqa: PLC0415

    bundle_id = "default-breach-flag-bundle"

    insert_dof_ledger_row(
        facet_name="gamma",
        facet_category="specification",
        decision_type="FIXED",
        evidence_source="THEORY",
        # touched_frozen_eval intentionally omitted — must default to 0
        spec_bundle_id=bundle_id,
    )

    rows = get_dof_ledger_for_bundle(bundle_id)
    assert rows, "Precondition: at least one row must be returned for the bundle"

    row = rows[0]
    assert "touched_frozen_eval" in row, (
        f"Row dict missing 'touched_frozen_eval' key. Got keys: {sorted(row.keys())}. "
        "The column must be present in the accessor return shape."
    )
    assert row["touched_frozen_eval"] == 0, (
        f"touched_frozen_eval={row['touched_frozen_eval']!r}; expected 0. "
        "The DEFAULT 0 must fire when the caller omits the argument."
    )


# ===========================================================================
# Plan-mandated test 4: Append-only — no update or delete accessor
# ===========================================================================


def test_no_update_accessor_exported_for_researcher_dof_ledger():
    """
    database module must NOT export any mutation accessor for researcher_dof_ledger.

    Allowed names: insert_dof_ledger_row, get_dof_ledger_for_bundle,
    count_dof_backtest_selections.  Any function named update_*, edit_*, modify_*,
    set_*, delete_*, or remove_* targeting the ledger is a defect: the ledger is
    append-only (llm_suggestions + advisor_observations precedent).

    This is a static inspect test — no DB required.
    """
    mutation_names = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if "dof_ledger" in name.lower()
        and any(
            name.startswith(prefix)
            for prefix in ("update_", "edit_", "modify_", "set_", "delete_", "remove_")
        )
    ]
    assert not mutation_names, (
        f"database module must not export mutation accessors for researcher_dof_ledger; "
        f"found: {mutation_names}. "
        "The ledger is append-only — once a DoF event is recorded it is immutable. "
        "Consistent with llm_suggestions and advisor_observations precedents."
    )


def test_no_delete_accessor_exported_for_researcher_dof_ledger():
    """
    Complementary to test_no_update_accessor: explicitly enumerate for delete/
    remove prefixes separately so an accessor named 'delete_old_dof_rows' would
    surface even if the prefix list above is incomplete.
    """
    delete_names = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if "dof" in name.lower()
        and any(name.startswith(prefix) for prefix in ("delete_", "remove_", "purge_", "clear_"))
    ]
    assert not delete_names, (
        f"database module must not export delete/purge accessors for the DoF ledger; "
        f"found: {delete_names}. "
        "The ledger is an append-only audit trail — deletion is a methodology defect."
    )


# ===========================================================================
# Plan-mandated test 5: Schema-validator — table columns + Phase-1 seed rows
# ===========================================================================


def test_fixture_file_exists():
    """The golden-fixture JSON for migration 018 must exist on disk."""
    assert _FIXTURE_PATH.is_file(), (
        f"Fixture file not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/database/researcher_dof_ledger/researcher_dof_ledger_schema.json."
    )


def test_researcher_dof_ledger_table_has_all_expected_columns(migrated_db):
    """
    Load the golden-fixture schema and verify researcher_dof_ledger has every
    column listed there with correct NOT NULL and DEFAULT constraints.

    This is the runtime schema-validator: guards against silent schema/fixture drift.
    """
    assert _FIXTURE_PATH.is_file(), "Fixture missing — run test_fixture_file_exists first"
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_cols = fixture["researcher_dof_ledger"]["columns"]

    actual_cols = _table_columns(migrated_db, "researcher_dof_ledger")
    assert actual_cols, (
        "researcher_dof_ledger table does not exist after run_migrations(). "
        "Migration 018_researcher_dof_ledger.sql must create it."
    )

    for col_name, constraints in expected_cols.items():
        assert col_name in actual_cols, (
            f"Column {col_name!r} missing from researcher_dof_ledger. "
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


def test_phase1_seed_rows_present_after_migrations(migrated_db):
    """
    After run_migrations(), researcher_dof_ledger must contain the three Phase-1
    theory-frozen seed rows (gamma, utility_family, wealth_argument) as required
    by plan §Deliverables #3.

    These rows are the honest-Phase-1 baseline: S=0 after seeding only these rows.

    How the rows get there is the implementer's choice (migration seed SQL,
    conftest autouse fixture, or init_db supplement) — this test only asserts
    their presence.  The evidence_source for each row must NOT be
    'BACKTEST_SELECTION' (which would incorrectly contribute to S).
    """
    conn = sqlite3.connect(migrated_db)
    try:
        rows = conn.execute(
            "SELECT facet_name, evidence_source, decision_type, n_configs_searched "
            "FROM researcher_dof_ledger ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    facet_names_present = {row[0] for row in rows}
    required_facets = {"gamma", "utility_family", "wealth_argument"}

    missing = required_facets - facet_names_present
    assert not missing, (
        f"Phase-1 seed rows missing from researcher_dof_ledger after run_migrations(): "
        f"{missing}. "
        "Plan §Deliverables #3 requires gamma + utility_family + wealth_argument "
        "seed rows to be present on a freshly migrated DB."
    )

    # Each seed row must NOT be a BACKTEST_SELECTION (which would make S > 0).
    for facet_name, evidence_source, _, _ in rows:
        if facet_name in required_facets:
            assert evidence_source != "BACKTEST_SELECTION", (
                f"Seed row for facet_name={facet_name!r} has "
                f"evidence_source='BACKTEST_SELECTION' — this would make S > 0 in "
                "the honest Phase-1 case. Seed rows must use "
                "THEORY / MANDATE / CALIBRATION to preserve S=0 (council §2.2 property 1)."
            )


def test_phase1_seed_rows_yield_s_equals_zero_via_accessor(migrated_db):
    """
    After seeding only the Phase-1 rows (no further inserts),
    count_dof_backtest_selections() must return 0.

    Variant of plan test 1 using the migrated DB with seed rows already present
    (validates that the seed rows themselves do not corrupt the S=0 invariant).
    """
    from database import count_dof_backtest_selections  # noqa: PLC0415

    s = count_dof_backtest_selections()
    assert s == 0, (
        f"count_dof_backtest_selections() returned {s} on a DB containing only the "
        "Phase-1 seed rows. Expected 0: seed rows have evidence_source != "
        "'BACKTEST_SELECTION' (plan §Deliverables #3 + council §2.2 property 1)."
    )


# ===========================================================================
# Plan-mandated test 6: Conservative-upper-bound property
# ===========================================================================


def test_sum_n_configs_searched_is_conservative_upper_bound_over_bundle_distinct_count(
    migrated_db,
):
    """
    SUM(n_configs_searched) >= COUNT(DISTINCT spec_bundle_id) for any set of
    BACKTEST_SELECTION rows.

    Concretely: one bundle with n_configs_searched=3 gives SUM=3 vs COUNT=1.
    A future reviewer who switches the accumulator to COUNT(DISTINCT) would
    undercount the multiple-testing penalty by a factor of 3.

    This test documents the binding choice so the conservative accumulator
    cannot be silently replaced.  The assertion inequality (SUM > COUNT_DISTINCT)
    is load-bearing — an implementation returning SUM < COUNT_DISTINCT would
    expose a floor violation impossible in practice but would signal a deeper bug.
    """
    from database import count_dof_backtest_selections, insert_dof_ledger_row  # noqa: PLC0415

    bundle_id = "multi-sweep-bundle-001"

    # One bundle — one spec_bundle_id — but it received a sub-sweep of 3 configs.
    # SUM = 3;  COUNT(DISTINCT spec_bundle_id) = 1.
    insert_dof_ledger_row(
        facet_name="momentum_lookback",
        facet_category="parameter",
        decision_type="SEARCHED",
        evidence_source="BACKTEST_SELECTION",
        n_configs_searched=3,
        spec_bundle_id=bundle_id,
        justification="Searched 3 lookback windows on strategy P&L.",
    )

    s_sum = count_dof_backtest_selections(spec_bundle_id=bundle_id)

    # Direct SQL to obtain the bundle-distinct count for comparison.
    conn = sqlite3.connect(migrated_db)
    try:
        s_distinct = conn.execute(
            "SELECT COUNT(DISTINCT spec_bundle_id) FROM researcher_dof_ledger "
            "WHERE evidence_source = 'BACKTEST_SELECTION' AND spec_bundle_id = ?",
            (bundle_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    # The SUM accumulator must be >= the bundle-distinct count.
    assert s_sum >= s_distinct, (
        f"SUM(n_configs_searched)={s_sum} < COUNT(DISTINCT spec_bundle_id)={s_distinct}. "
        "This should be mathematically impossible when n_configs_searched >= 1. "
        "An implementation returning SUM < COUNT_DISTINCT has a serious accumulator bug."
    )

    # The key property: when any bundle has n_configs_searched > 1, SUM > COUNT.
    assert s_sum > s_distinct, (
        f"SUM(n_configs_searched)={s_sum} == COUNT(DISTINCT spec_bundle_id)={s_distinct}. "
        "For a bundle with n_configs_searched=3 and one distinct spec_bundle_id, "
        "SUM must be strictly greater than COUNT. "
        "An implementation returning SUM == COUNT has silently switched to "
        "COUNT(DISTINCT), which underestimates the multiple-testing penalty "
        "(v3-evaluation §A.0 Defect 2, council §2.2)."
    )


# ===========================================================================
# Hostile edge test 7: Idempotent re-run of run_migrations()
# ===========================================================================


def test_run_migrations_is_idempotent_on_already_migrated_db(migrated_db, monkeypatch):
    """
    Calling run_migrations() a second and third time on a DB that already has
    018 applied must not raise and must leave the table intact.

    The schema_migrations tracker prevents re-execution; the CREATE TABLE IF NOT
    EXISTS clause is the final guard if the tracker is bypassed.
    """
    # Second and third calls — must not raise.
    run_migrations()
    run_migrations()

    # Table must still exist.
    actual_cols = _table_columns(migrated_db, "researcher_dof_ledger")
    assert actual_cols, (
        "researcher_dof_ledger table must still exist after repeated run_migrations() calls. "
        "Idempotency contract: re-applying the migration must not drop the table."
    )


def test_018_migration_file_uses_create_table_if_not_exists():
    """
    018_researcher_dof_ledger.sql must use CREATE TABLE IF NOT EXISTS for
    idempotency — bare CREATE TABLE would fail on re-run.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql_upper = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    assert "CREATE TABLE IF NOT EXISTS" in sql_upper, (
        "018_researcher_dof_ledger.sql must use CREATE TABLE IF NOT EXISTS. "
        "Bare CREATE TABLE fails on a DB where the table already exists."
    )


# ===========================================================================
# Hostile edge test 8: mode=ro connection SELECTs from researcher_dof_ledger
# ===========================================================================


def test_read_only_connection_can_select_from_researcher_dof_ledger(migrated_db):
    """
    get_ro_connection() must be able to SELECT from researcher_dof_ledger after
    the migration. New tables must be visible on the read path used by the
    dashboard and the Advisor (MED-2 contract precedent from test_spec_bundles).

    Inserts a row via a direct write connection, then reads it back via
    get_ro_connection() to verify table visibility.
    """
    _insert_dof_row_direct(
        migrated_db,
        facet_name="ro_test_facet",
        facet_category="specification",
        decision_type="FIXED",
        evidence_source="THEORY",
    )

    conn_ro = db_module.get_ro_connection()
    try:
        row = conn_ro.execute("SELECT COUNT(*) FROM researcher_dof_ledger").fetchone()
        assert row is not None
        assert isinstance(row[0], int) and row[0] >= 1, (
            "get_ro_connection() must be able to COUNT researcher_dof_ledger rows. "
            "Table is invisible to the RO connection — verify the migration was applied "
            "to the same DB file that get_ro_connection() opens."
        )
    finally:
        conn_ro.close()


def test_read_only_connection_cannot_write_to_researcher_dof_ledger(migrated_db):
    """
    Write attempt via get_ro_connection() into researcher_dof_ledger must raise
    sqlite3.OperationalError — the RO-connection contract (MED-2) applies to
    all tables, not just the core tables from init_db().
    """
    conn_ro = db_module.get_ro_connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
            conn_ro.execute(
                "INSERT INTO researcher_dof_ledger "
                "(facet_name, facet_category, decision_type, evidence_source) "
                "VALUES (?, ?, ?, ?)",
                ("rw_attempt", "specification", "FIXED", "THEORY"),
            )
            conn_ro.commit()
    finally:
        conn_ro.close()


# ===========================================================================
# Hostile edge test 9: Migration file appended after 017 in _MIGRATION_FILES
# ===========================================================================


def test_018_migration_file_exists():
    """The migration file migrations/018_researcher_dof_ledger.sql must exist."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/018_researcher_dof_ledger.sql."
    )


def test_018_present_in_migration_files_list():
    """
    '018_researcher_dof_ledger.sql' must appear in database._MIGRATION_FILES.

    The list is append-only; the entry must appear after '016_spec_bundles.sql'
    (the nearest established predecessor on this branch).  When migration 017 is
    added in a future cycle, the append-order invariant requires 017 to be
    inserted between 016 and 018 — this test will catch that if 018 slips behind.
    """
    assert "018_researcher_dof_ledger.sql" in db_module._MIGRATION_FILES, (
        "'018_researcher_dof_ledger.sql' not found in database._MIGRATION_FILES. "
        "Append it in order after '017_advisor_observations.sql' "
        "(or after '016_spec_bundles.sql' if 017 is not yet present on this branch)."
    )


def test_018_appears_after_016_in_migration_files_list():
    """
    '018_researcher_dof_ledger.sql' must appear at a higher index than
    '016_spec_bundles.sql' in _MIGRATION_FILES.

    The plan states 018 is appended after 017_advisor_observations.sql; on this
    branch 017 may not yet exist so we verify the nearest confirmed predecessor (016).
    When 017 lands, a companion test in that cycle's suite will enforce 017 > 016 and
    this test continues to enforce 018 > 016 (transitively correct).
    """
    migrations = db_module._MIGRATION_FILES
    assert "016_spec_bundles.sql" in migrations, (
        "'016_spec_bundles.sql' must be present in _MIGRATION_FILES before 018 can appear."
    )
    idx_016 = migrations.index("016_spec_bundles.sql")
    idx_018 = migrations.index("018_researcher_dof_ledger.sql")
    assert idx_018 > idx_016, (
        f"'018_researcher_dof_ledger.sql' (index {idx_018}) must appear after "
        f"'016_spec_bundles.sql' (index {idx_016}) in _MIGRATION_FILES. "
        "Append-only contract violated."
    )


def test_018_migration_sql_contains_no_create_trigger():
    """
    018_researcher_dof_ledger.sql must NOT contain a CREATE TRIGGER.

    Append-only immutability is enforced at application level (no-UPDATE-accessor
    pattern), not via SQL triggers.  A trigger would be out-of-pattern and create
    hidden behavioural coupling invisible to readers of database.py.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql_upper = _MIGRATION_PATH.read_text(encoding="utf-8").upper()
    assert "CREATE TRIGGER" not in sql_upper, (
        "018_researcher_dof_ledger.sql must not contain CREATE TRIGGER. "
        "Immutability is enforced at application level — consistent with "
        "llm_suggestions and advisor_observations precedents."
    )


# ===========================================================================
# Hostile edge test 10: Evidence-source enum guard
# ===========================================================================


def test_insert_dof_ledger_row_rejects_invalid_evidence_source(migrated_db):
    """
    insert_dof_ledger_row() must raise ValueError (or a subclass) when
    evidence_source is not one of the six accepted values.

    Application-level enforcement — consistent with the spec_facets
    freeze_discipline guard (insert_spec_bundle_facet raises ValueError on
    unrecognised freeze_discipline values).
    """
    from database import insert_dof_ledger_row  # noqa: PLC0415

    with pytest.raises(
        (ValueError, TypeError),
        match=r"(?i)evidence_source|invalid|not.*valid|unrecognised|unrecognized",
    ):
        insert_dof_ledger_row(
            facet_name="some_facet",
            facet_category="specification",
            decision_type="FIXED",
            evidence_source="VIBES",  # not a valid evidence source
        )


def test_insert_dof_ledger_row_accepts_all_valid_evidence_sources(migrated_db):
    """
    All six valid evidence_source values must INSERT without error.

    Pins the complete accepted enum so a future addition or removal updates
    both this test and the fixture JSON.
    """
    from database import insert_dof_ledger_row  # noqa: PLC0415

    for source in sorted(_VALID_EVIDENCE_SOURCES):
        row_id = insert_dof_ledger_row(
            facet_name=f"facet_{source.lower()}",
            facet_category="specification",
            decision_type="FIXED",
            evidence_source=source,
        )
        assert isinstance(row_id, int) and row_id > 0, (
            f"insert_dof_ledger_row must return a positive int row_id for "
            f"evidence_source={source!r}; got {row_id!r}"
        )


# ===========================================================================
# Hostile edge test 11: Two-DB boundary
# ===========================================================================


def test_018_new_accessors_do_not_reference_opt_db():
    """
    No accessor introduced by cycle 018 may open alphabot_opt.db or any
    connection that is not DB_FILE / _db_file().

    Enforces architecture constraint 3 (Two-DB boundary) and council §3.7
    (state-DB-only placement for the spec registry and its dependents).
    """
    new_accessors = [
        "insert_dof_ledger_row",
        "get_dof_ledger_for_bundle",
        "count_dof_backtest_selections",
    ]

    for name in new_accessors:
        if not hasattr(db_module, name):
            continue  # not implemented yet — RED is expected; skip source inspection
        source = inspect.getsource(getattr(db_module, name))
        assert "alphabot_opt" not in source, (
            f"{name}() references 'alphabot_opt' — DoF ledger must live in "
            "the state DB only (Two-DB boundary, architecture constraint 3)."
        )
        assert "opt.db" not in source, (
            f"{name}() references 'opt.db' — state DB only per council §3.7."
        )


# ===========================================================================
# Accessor shape tests (shape / presence / format — no hardcoded values)
# ===========================================================================


def test_insert_dof_ledger_row_returns_positive_int_row_id(migrated_db):
    """
    insert_dof_ledger_row() must return a positive integer row id (the AUTOINCREMENT
    surrogate key). Shape assertion — the exact value is not asserted.
    """
    from database import insert_dof_ledger_row  # noqa: PLC0415

    row_id = insert_dof_ledger_row(
        facet_name="gamma",
        facet_category="specification",
        decision_type="FIXED",
        evidence_source="THEORY",
    )
    assert isinstance(row_id, int), (
        f"insert_dof_ledger_row() must return an int; got {type(row_id)}"
    )
    assert row_id > 0, f"insert_dof_ledger_row() must return a positive int row_id; got {row_id}"


def test_get_dof_ledger_for_bundle_returns_empty_list_for_unknown_bundle_id(migrated_db):
    """
    get_dof_ledger_for_bundle() for an unknown spec_bundle_id must return [],
    not raise.
    """
    from database import get_dof_ledger_for_bundle  # noqa: PLC0415

    result = get_dof_ledger_for_bundle("no-such-bundle-id-99999")
    assert isinstance(result, list) and result == [], (
        f"get_dof_ledger_for_bundle() must return [] for unknown bundle; got {result!r}"
    )


def test_get_dof_ledger_for_bundle_returns_dicts_with_all_schema_columns(migrated_db):
    """
    Each dict returned by get_dof_ledger_for_bundle() must carry all
    researcher_dof_ledger columns. Shape assertion — not value assertion.
    """
    from database import get_dof_ledger_for_bundle, insert_dof_ledger_row  # noqa: PLC0415

    bundle_id = "shape-check-bundle-111"
    insert_dof_ledger_row(
        facet_name="cvar_alpha",
        facet_category="parameter",
        decision_type="FIXED",
        evidence_source="CALIBRATION",
        spec_bundle_id=bundle_id,
    )

    rows = get_dof_ledger_for_bundle(bundle_id)
    assert rows, "Precondition: at least one row must be returned for the bundle"

    expected_keys = {
        "id",
        "created_at",
        "facet_name",
        "facet_category",
        "decision_type",
        "evidence_source",
        "n_configs_searched",
        "touched_frozen_eval",
        "spec_bundle_id",
        "justification",
    }
    for row in rows:
        missing = expected_keys - set(row.keys())
        assert not missing, f"Row dict missing columns: {missing}. Got: {sorted(row.keys())}"


def test_created_at_is_nonnull_when_omitted(migrated_db):
    """
    An INSERT that omits created_at must still produce a non-NULL created_at
    because the column carries DEFAULT (datetime('now')).

    Tests the schema-level default directly via sqlite3 INSERT (bypassing the
    accessor, which may or may not pass created_at explicitly).
    """
    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "INSERT INTO researcher_dof_ledger "
            "(facet_name, facet_category, decision_type, evidence_source) "
            "VALUES (?, ?, ?, ?)",
            ("direct_insert", "specification", "FIXED", "THEORY"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT created_at FROM researcher_dof_ledger WHERE facet_name = 'direct_insert'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Row must exist after INSERT"
    created_at = row[0]
    assert created_at is not None, (
        "created_at must not be NULL when omitted from INSERT. "
        "Migration 018 must specify DEFAULT (datetime('now')) on this column."
    )
    assert isinstance(created_at, str) and len(created_at) > 0, (
        f"created_at must be a non-empty string after DEFAULT fires; got {created_at!r}"
    )


def test_n_configs_searched_defaults_to_one_when_omitted(migrated_db):
    """
    An INSERT that omits n_configs_searched must still produce n_configs_searched=1
    because the column carries DEFAULT 1.

    This is the per-event contract: when the caller does not know a sub-sweep
    count, the conservative default is 1 (contributes 1 to S if BACKTEST_SELECTION).
    """
    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "INSERT INTO researcher_dof_ledger "
            "(facet_name, facet_category, decision_type, evidence_source) "
            "VALUES (?, ?, ?, ?)",
            ("default_n_configs", "specification", "SEARCHED", "BACKTEST_SELECTION"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT n_configs_searched FROM researcher_dof_ledger "
            "WHERE facet_name = 'default_n_configs'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Row must exist after INSERT"
    n = row[0]
    assert n == 1, (
        f"n_configs_searched must default to 1 when omitted from INSERT; got {n!r}. "
        "Migration 018 must specify DEFAULT 1 on n_configs_searched."
    )


def test_count_dof_backtest_selections_returns_int(migrated_db):
    """
    count_dof_backtest_selections() must return an int (not None, not a float,
    not a list). Shape assertion: the type contract is non-negotiable for
    arithmetic in N_effective = N_optuna + S.
    """
    from database import count_dof_backtest_selections  # noqa: PLC0415

    result = count_dof_backtest_selections()
    assert isinstance(result, int), (
        f"count_dof_backtest_selections() must return int; got {type(result)!r}. "
        "N_effective = N_optuna + S requires int arithmetic; a None or float would "
        "propagate a type error into the BHY haircut computation."
    )


def test_init_db_does_not_create_researcher_dof_ledger():
    """
    init_db() must NOT contain a CREATE TABLE statement for researcher_dof_ledger.

    New-table migrations use run_migrations() only.  A spurious CREATE TABLE in
    init_db() would shadow the migration runner and make future schema changes
    invisible to operator deployments that call init_db() but not run_migrations().
    """
    source = inspect.getsource(db_module.init_db)
    assert "researcher_dof_ledger" not in source, (
        "init_db() must not contain 'researcher_dof_ledger'. "
        "New-table migrations use run_migrations() only; no dual-write needed here."
    )
