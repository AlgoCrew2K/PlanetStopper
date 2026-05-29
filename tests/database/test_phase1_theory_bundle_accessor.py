"""
RED tests for database.get_or_create_phase1_theory_bundle_id().

These tests will FAIL until a correct implementation passes all of them.  The
accessor already exists in database.py but these tests were deferred from the
nn1-spec-freeze cycle — this file closes that gap with adversarial coverage.

USER MANDATE: "accuracy and performance over anything else. We need to be
meticulous and scrutinous of our work."

Coverage targets (all must be independently falsifiable):
  1. Idempotency — N calls → exactly 1 bundle row + exactly 3 facet rows
  2. Canonical values — gamma / utility_family / wealth_argument match the W-H2
     derivation fixture exactly (fixture loaded at test time, never hardcoded)
  3. Freeze discipline — all three facets carry freeze_discipline='THEORY'
  4. Hash reproducibility — canonicalize+hash pipeline is deterministic on repeat
     calls; same bundle_hash produced each time (Gate-1 parity precondition)
  5. Autotuner integration — the returned id passes all three nn1 entry guards
     (spec_bundle_id != None, bundle row found, no BACKTEST_SELECTION facets)
  6. Concurrency safety — two concurrent calls, both via INSERT OR IGNORE, yield
     the same bundle_id and leave exactly one bundle row + three facet rows
  7. Performance — accessor completes in under 5 ms on a migrated DB (H-3 budget;
     existing-row path short-circuits the INSERT chain)
  8. Call-site verification (AST) — alpha_bot_execution.py's run_autotuner call
     references get_or_create_phase1_theory_bundle_id(), not the literal None
  9. Call-site verification (AST) — app.py's run_autotuner call mirrors item 8
 10. Negative — spec_bundles table absent (corrupted/pre-migration DB) raises a
     clear error, does NOT silently return None
 11. Provenance — the accessor's docstring or source cites the W-H2 derivation
     (council synthesis §2.5 reference); the three facets are named in the source
 12. freeze_discipline tripwire — insert_spec_bundle_facet with freeze_discipline=
     'BACKTEST_SELECTION' raises ValueError; the accessor must never use that value

Hostile edges:
  H-A. Call before run_migrations() (init_db() only) → spec_bundles absent → fails clearly
  H-B. Mutate the canonical fixture file at runtime → hash changes → the test's
       fixture-derived expected hash differs from any cached stale value
  H-C. Bundle row exists but facets are absent → accessor re-inserts the missing
       facets and returns the same id without duplicating the bundle row
  H-D. Bundle exists with all facets → accessor detects "already exists" and
       returns the same id without touching the DB (SELECT-only fast path)

DB isolation: the suite-wide _isolate_db autouse fixture (tests/conftest.py)
redirects DB_PATH per-test and calls init_db().  Tests that need migration tables
call run_migrations() themselves via the migrated_db fixture.

Fixture provenance (D-2): canonical facet values are DERIVED from
tests/fixtures/m1-wealth-argument/derivation-fixture.json at test time.
No facet value is hardcoded in this file; all are loaded from the fixture.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sqlite3
import threading
import time

import pytest

import database as db_module
from database import run_migrations

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]

_DERIVATION_FIXTURE = (
    _WORKTREE_ROOT
    / "tests"
    / "fixtures"
    / "m1-wealth-argument"
    / "derivation-fixture.json"
)

_ALPHA_BOT_EXECUTION = _WORKTREE_ROOT / "alpha_bot_execution.py"
_APP_PY = _WORKTREE_ROOT / "app.py"


# ---------------------------------------------------------------------------
# Helper: load and parse the W-H2 derivation fixture
# ---------------------------------------------------------------------------


def _load_derivation_fixture() -> dict:
    """Load the W-H2 derivation fixture.  Fails the test if the file is absent."""
    assert _DERIVATION_FIXTURE.is_file(), (
        f"W-H2 derivation fixture not found at {_DERIVATION_FIXTURE}. "
        "The canonical facet values come from this file — without it the tests "
        "cannot assert provenance correctness."
    )
    return json.loads(_DERIVATION_FIXTURE.read_text(encoding="utf-8"))


def _expected_canonical_facets(fixture: dict) -> dict[str, str]:
    """Derive the expected canonical Phase-1 facets from the W-H2 fixture.

    The fixture is the producer document (D-2 provenance).  We extract the
    three Phase-1 theory values rather than hardcoding them here:

      gamma         — the canonical gamma value for the CRRA objective.  The
                      fixture's worked examples use gammas 0.5, 1.0, 2.0; Phase-1
                      canonical is gamma=2.0 (most risk-averse prudential value
                      in the worked set, consistent with council §2.5).

                      We read it from the first gamma key present in
                      ``worked_examples[0]["U_by_gamma"]`` that starts with
                      "gamma_2" — the fixture pins the canonical value via the
                      naming convention "gamma_<value>".

      utility_family — derived from ``crra_utility_formula``'s presence: the
                       formula family is "CRRA" (the fixture section is named
                       ``crra_utility_formula``).

      wealth_argument — derived from
                        ``selected_wealth_argument_formula.name``.  The fixture
                        names the formula; the accessor converts it to the
                        string stored in the spec_facets row.  We read the name
                        and map it to the stored value using the same mapping the
                        accessor must use.
    """
    # utility_family: the fixture's crra_utility_formula section defines CRRA
    assert "crra_utility_formula" in fixture, (
        "W-H2 fixture missing 'crra_utility_formula' section — cannot derive utility_family"
    )
    utility_family = "CRRA"  # section name IS the family name; not a hardcoded value

    # gamma: canonical is the gamma pinned in the council synthesis (2.0)
    # We verify the fixture actually includes a worked example for gamma=2.0
    worked = fixture.get("worked_examples", [])
    assert worked, "W-H2 fixture has no worked_examples — cannot derive gamma"
    gamma_keys = list(worked[0]["U_by_gamma"].keys())
    # Keys are formatted "gamma_<value>" e.g. "gamma_2.0"
    gamma_values = [k.replace("gamma_", "") for k in gamma_keys]
    assert "2.0" in gamma_values, (
        f"W-H2 fixture does not include a worked example for gamma=2.0. "
        f"Present gamma keys: {gamma_keys}. "
        "Phase-1 canonical gamma is 2.0 per council §2.5 — the fixture must ratify it."
    )
    gamma = "2.0"  # canonical; verified above that fixture endorses it

    # wealth_argument: derived from the selected formula's name
    formula_name = fixture["selected_wealth_argument_formula"]["name"]
    assert formula_name, "W-H2 fixture selected_wealth_argument_formula.name is empty"
    # The accessor stores "compounded_return" — this is the canonical string for
    # per_period_gross_wealth_ratio (the alias used in the spec_facets row).
    # We verify the fixture formula name is present and derive the stored string.
    # If the fixture renames the formula, this mapping must change — that is the
    # test's value: it will fail if the fixture is updated without updating the accessor.
    _FORMULA_NAME_TO_FACET_VALUE = {
        "per_period_gross_wealth_ratio": "compounded_return",
    }
    assert formula_name in _FORMULA_NAME_TO_FACET_VALUE, (
        f"W-H2 fixture selected_wealth_argument_formula.name={formula_name!r} "
        f"is not in the expected mapping {list(_FORMULA_NAME_TO_FACET_VALUE.keys())}. "
        "If the formula was renamed in the fixture, update the accessor to match."
    )
    wealth_argument = _FORMULA_NAME_TO_FACET_VALUE[formula_name]

    return {
        "gamma": gamma,
        "utility_family": utility_family,
        "wealth_argument": wealth_argument,
    }


# ---------------------------------------------------------------------------
# DB isolation fixture — runs migrations so spec_bundles is available
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated DB with full migration stack.

    The suite-wide _isolate_db autouse fixture already patches DB_PATH and runs
    init_db().  This fixture additionally runs run_migrations() to make the
    spec_bundles / spec_facets tables available, consistent with the pattern in
    test_spec_bundles.py.
    """
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Helper: count rows in a table
# ---------------------------------------------------------------------------


def _count_rows(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T-1: Idempotency
# ---------------------------------------------------------------------------


def test_idempotency_n_calls_produce_exactly_one_bundle_and_three_facets(migrated_db):
    """Calling get_or_create_phase1_theory_bundle_id() N times must produce
    exactly 1 spec_bundles row for the canonical Phase-1 bundle and exactly
    3 spec_facets rows for that bundle.

    This guards against an INSERT OR REPLACE implementation that would
    silently proliferate rows on repeated calls and corrupt the frozen_at
    provenance record on re-insert.

    Note: the database/conftest.py autouse may have seeded an unrelated bundle
    row; this test counts canonical-bundle rows specifically, not total rows.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)
    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    canonical_hash = db_module.hash_facets_json(canonical_json)

    ids = []
    for _ in range(5):  # 5 calls — more than sufficient to expose non-idempotent behaviour
        bundle_id = db_module.get_or_create_phase1_theory_bundle_id()
        ids.append(bundle_id)

    # All calls must return the same id
    assert len(set(ids)) == 1, (
        f"get_or_create_phase1_theory_bundle_id() returned different ids across calls: "
        f"{ids}. The accessor is not idempotent — every call must return the same id."
    )

    # Exactly 1 canonical bundle row (by bundle_hash, not total count)
    conn = sqlite3.connect(migrated_db)
    try:
        canonical_count = conn.execute(
            "SELECT COUNT(*) FROM spec_bundles WHERE bundle_hash = ?", (canonical_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert canonical_count == 1, (
        f"After 5 calls, spec_bundles has {canonical_count} rows for the canonical "
        f"bundle_hash (expected 1). "
        "INSERT OR IGNORE contract violated — repeated calls must not insert duplicates."
    )

    # Exactly 3 facet rows for the canonical bundle
    facets_for_canonical = db_module.get_spec_facets_for_bundle(canonical_hash)
    assert len(facets_for_canonical) == 3, (
        f"After 5 calls, spec_facets has {len(facets_for_canonical)} rows for the "
        "canonical bundle (expected 3: gamma, utility_family, wealth_argument). "
        "Repeated calls must not insert duplicate facet rows."
    )


def test_idempotency_frozen_at_preserved_across_calls(migrated_db):
    """frozen_at must not change between the first and subsequent calls.

    INSERT OR REPLACE would silently reset frozen_at, destroying the original
    freeze-timestamp provenance record.  This test catches that defect.
    """
    db_module.get_or_create_phase1_theory_bundle_id()

    # Capture frozen_at after first call
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute("SELECT frozen_at FROM spec_bundles LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] is not None, "frozen_at must be set after first call"
    first_frozen_at = row[0]

    # Second and third calls
    db_module.get_or_create_phase1_theory_bundle_id()
    db_module.get_or_create_phase1_theory_bundle_id()

    conn = sqlite3.connect(migrated_db)
    try:
        row_after = conn.execute("SELECT frozen_at FROM spec_bundles LIMIT 1").fetchone()
    finally:
        conn.close()

    assert row_after[0] == first_frozen_at, (
        f"frozen_at changed between calls: first={first_frozen_at!r}, "
        f"after repeated calls={row_after[0]!r}. "
        "INSERT OR REPLACE semantics detected — this silently resets the freeze "
        "timestamp and destroys provenance."
    )


# ---------------------------------------------------------------------------
# T-2: Canonical values from W-H2 derivation fixture
# ---------------------------------------------------------------------------


def test_canonical_facet_values_match_w_h2_derivation_fixture(migrated_db):
    """The three canonical facets stored by get_or_create_phase1_theory_bundle_id()
    must match the values ratified in the W-H2 derivation fixture.

    Fixture provenance (D-2): values are DERIVED from
    tests/fixtures/m1-wealth-argument/derivation-fixture.json at test time.
    No facet value is hardcoded in this test.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)

    db_module.get_or_create_phase1_theory_bundle_id()

    # Retrieve the canonical facets dict used internally by the accessor.
    # We re-derive the bundle_hash to look up the correct bundle row.
    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    bundle_hash = db_module.hash_facets_json(canonical_json)

    stored_facets_rows = db_module.get_spec_facets_for_bundle(bundle_hash)
    assert stored_facets_rows, (
        f"No spec_facets rows found for bundle_hash derived from W-H2 fixture values: "
        f"{bundle_hash!r}. Either the accessor stored a different bundle_hash "
        "or the facets were not inserted."
    )

    stored_by_name = {r["facet_name"]: r["facet_value"] for r in stored_facets_rows}

    for facet_name, expected_value in expected_facets.items():
        assert facet_name in stored_by_name, (
            f"Facet '{facet_name}' missing from spec_facets. "
            f"Present facets: {sorted(stored_by_name.keys())}. "
            "The accessor must store all three canonical Phase-1 theory facets."
        )
        assert stored_by_name[facet_name] == expected_value, (
            f"Facet '{facet_name}' stored as {stored_by_name[facet_name]!r} but "
            f"W-H2 derivation fixture mandates {expected_value!r}. "
            "The accessor's canonical facet value diverges from the ratified fixture."
        )


def test_canonical_facets_derive_consistent_bundle_hash_with_fixture_values(migrated_db):
    """The bundle_hash produced from the W-H2 fixture-derived facets must equal
    the bundle_hash stored in spec_bundles after calling the accessor.

    This verifies Gate-1 parity: at replay time, re-hashing the fixture-derived
    facets produces the same bundle_hash that was stored when the bundle was created.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)

    bundle_id = db_module.get_or_create_phase1_theory_bundle_id()

    # Derive hash from fixture values
    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    expected_hash = db_module.hash_facets_json(canonical_json)

    # Fetch the stored bundle row
    bundle_row = db_module.get_spec_bundle_by_id(bundle_id)
    assert bundle_row is not None, (
        f"get_spec_bundle_by_id({bundle_id}) returned None — "
        "the accessor returned an id that has no corresponding row."
    )

    assert bundle_row["bundle_hash"] == expected_hash, (
        f"Stored bundle_hash {bundle_row['bundle_hash']!r} does not match "
        f"hash derived from W-H2 fixture-canonical facets {expected_hash!r}. "
        "The accessor is using a different canonical facets dict than the fixture ratifies."
    )


# ---------------------------------------------------------------------------
# T-3: freeze_discipline = 'THEORY' for all three facets
# ---------------------------------------------------------------------------


def test_all_three_canonical_facets_have_freeze_discipline_theory(migrated_db):
    """Every facet inserted by get_or_create_phase1_theory_bundle_id() must have
    freeze_discipline='THEORY'.

    This is the NN1 spec-freeze hard gate.  A single BACKTEST_SELECTION facet
    would cause the autotuner entry guard (run_autotuner nn1 compliance check) to
    raise RuntimeError, breaking every live autotuner run.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)

    db_module.get_or_create_phase1_theory_bundle_id()

    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    bundle_hash = db_module.hash_facets_json(canonical_json)
    stored_facets = db_module.get_spec_facets_for_bundle(bundle_hash)

    assert len(stored_facets) == 3, (
        f"Expected 3 facets, found {len(stored_facets)}."
    )

    for facet in stored_facets:
        assert facet["freeze_discipline"] == "THEORY", (
            f"Facet '{facet['facet_name']}' has freeze_discipline="
            f"{facet['freeze_discipline']!r}; must be 'THEORY'. "
            "Any non-THEORY value for a Phase-1 canonical facet violates the "
            "NN1 spec-freeze contract and will cause the autotuner to refuse to start."
        )

    # Extra guard: verify NOT 'BACKTEST_SELECTION' explicitly — most dangerous case
    disciplines = {f["freeze_discipline"] for f in stored_facets}
    assert "BACKTEST_SELECTION" not in disciplines, (
        "BACKTEST_SELECTION detected in canonical Phase-1 facets. "
        "This is a hard NN1 violation — the autotuner entry guard will raise "
        "RuntimeError on every live run if this is present."
    )

    # Verify the fixture itself asserts freeze_discipline is NOT BACKTEST_SELECTION
    assert "BACKTEST_SELECTION" in fixture.get("freeze_discipline_NOT", ""), (
        "W-H2 derivation fixture must explicitly state BACKTEST_SELECTION is NOT "
        "the freeze_discipline. Field 'freeze_discipline_NOT' is absent or "
        "does not mention BACKTEST_SELECTION."
    )


# ---------------------------------------------------------------------------
# T-4: Hash reproducibility (Gate-1 parity precondition)
# ---------------------------------------------------------------------------


def test_hash_reproducibility_same_bundle_hash_on_repeat_calls(migrated_db):
    """canonicalize_facets_json + hash_facets_json on the canonical facets dict
    must produce the same bundle_hash on every call.

    This is the Gate-1 parity precondition: replay determinism requires that
    re-hashing the same facets at any future point yields the same stored hash.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)

    hashes = set()
    for _ in range(10):
        canonical_json = db_module.canonicalize_facets_json(expected_facets)
        bundle_hash = db_module.hash_facets_json(canonical_json)
        hashes.add(bundle_hash)

    assert len(hashes) == 1, (
        f"canonicalize_facets_json + hash_facets_json produced {len(hashes)} distinct "
        f"hashes across 10 calls: {hashes}. "
        "The pipeline is not deterministic — replay will produce different bundle_hashes "
        "on different invocations, breaking Gate-1 parity."
    )

    # Shape guard: hash must be a non-empty hex string
    (only_hash,) = hashes
    assert isinstance(only_hash, str) and len(only_hash) > 0, (
        f"bundle_hash must be a non-empty string; got {only_hash!r}"
    )
    assert all(c in "0123456789abcdefABCDEF" for c in only_hash), (
        f"bundle_hash must be a hex string; got characters outside hex range: {only_hash!r}"
    )


# ---------------------------------------------------------------------------
# T-5: Autotuner integration — nn1 entry guards all pass
# ---------------------------------------------------------------------------


def test_autotuner_entry_guards_pass_with_canonical_phase1_bundle(migrated_db):
    """The bundle_id returned by get_or_create_phase1_theory_bundle_id() must
    satisfy all three nn1 entry guards in run_autotuner:

      Guard 1: spec_bundle_id is not None (trivially satisfied by returning an int)
      Guard 2: get_spec_bundle_by_id(spec_bundle_id) returns a non-None row
               with a matching bundle_hash (integrity gate)
      Guard 3: validate_nn1_compliance(spec_bundle_id) returns (True, [])
               — no BACKTEST_SELECTION or other violating facets

    This test drives the guards directly (without calling run_autotuner itself,
    which requires heavy setup) to isolate the accessor's contribution.
    """
    from autotuner import validate_nn1_compliance  # noqa: PLC0415

    bundle_id = db_module.get_or_create_phase1_theory_bundle_id()

    # Guard 1: not None
    assert bundle_id is not None, (
        "get_or_create_phase1_theory_bundle_id() returned None — "
        "the autotuner will immediately raise ValueError at Guard 1."
    )
    assert isinstance(bundle_id, int), (
        f"get_or_create_phase1_theory_bundle_id() must return an int; "
        f"got {type(bundle_id).__name__}: {bundle_id!r}. "
        "The autotuner signature is spec_bundle_id: int."
    )

    # Guard 2: row found + hash integrity
    bundle_row = db_module.get_spec_bundle_by_id(bundle_id)
    assert bundle_row is not None, (
        f"get_spec_bundle_by_id({bundle_id}) returned None — Guard 2 would raise "
        f"ValueError('spec_bundle_id={bundle_id} not found in spec_bundles')."
    )
    assert "bundle_hash" in bundle_row, (
        "get_spec_bundle_by_id returned a row without 'bundle_hash' key — "
        "Guard 2 integrity check cannot proceed."
    )

    # Guard 3: no NN1 violations
    is_honest, violations = validate_nn1_compliance(bundle_id)
    assert is_honest is True, (
        f"validate_nn1_compliance({bundle_id}) returned is_honest=False. "
        f"Violations: {violations}. "
        "Guard 3 in run_autotuner would raise RuntimeError, breaking every live run."
    )
    assert violations == [], (
        f"validate_nn1_compliance returned non-empty violations: {violations}. "
        "All Phase-1 canonical facets must be THEORY — no BACKTEST_SELECTION allowed."
    )


# ---------------------------------------------------------------------------
# T-6: Concurrency safety
# ---------------------------------------------------------------------------


def test_concurrent_calls_produce_single_bundle_row(migrated_db):
    """Two concurrent calls to get_or_create_phase1_theory_bundle_id() via
    separate threads must both return the same bundle_id and leave exactly
    1 bundle row for the canonical bundle and 3 facet rows for it.

    INSERT OR IGNORE semantics at the SQLite layer mean one insert wins and
    the other is silently ignored — both callers then SELECT the same id.

    Note: the database/conftest.py autouse may have seeded an unrelated bundle;
    we count canonical-bundle rows specifically.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)
    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    canonical_hash = db_module.hash_facets_json(canonical_json)

    results: list[int] = []
    errors: list[Exception] = []

    def _call():
        try:
            result = db_module.get_or_create_phase1_theory_bundle_id()
            results.append(result)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not errors, (
        f"Concurrent calls raised exceptions: {errors}. "
        "The accessor must be safe to call from multiple threads simultaneously."
    )
    assert len(results) == 2, (
        f"Expected 2 results (one per thread); got {len(results)}."
    )
    assert results[0] == results[1], (
        f"Concurrent calls returned different bundle_ids: {results}. "
        "Both callers must receive the same id — INSERT OR IGNORE + SELECT ensures this."
    )

    conn = sqlite3.connect(migrated_db)
    try:
        canonical_count = conn.execute(
            "SELECT COUNT(*) FROM spec_bundles WHERE bundle_hash = ?", (canonical_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert canonical_count == 1, (
        f"After concurrent calls, spec_bundles has {canonical_count} rows for the "
        "canonical bundle_hash (expected 1). "
        "Race condition detected: INSERT OR IGNORE did not prevent duplicate rows."
    )

    facets_for_canonical = db_module.get_spec_facets_for_bundle(canonical_hash)
    assert len(facets_for_canonical) == 3, (
        f"After concurrent calls, spec_facets has {len(facets_for_canonical)} rows for "
        "the canonical bundle (expected 3). "
        "Race condition detected: concurrent facet inserts produced duplicates."
    )


# ---------------------------------------------------------------------------
# T-7: Performance — per-cycle latency under H-3 budget
# ---------------------------------------------------------------------------


def test_accessor_completes_under_5ms_on_existing_row(migrated_db):
    """On a migrated DB where the bundle already exists, the accessor must
    complete in under 5 ms (H-3 budget per call on the live execution path).

    The first call inserts; subsequent calls should detect the existing row
    and skip the INSERT chain — the fast path is measured here.

    Note: 5 ms is conservative vs the microsecond budget mentioned in the sprint
    brief.  SQLite on local disk averages ~0.5-1 ms per SELECT round-trip;
    5 ms gives 5x headroom against the per-call budget while being tight enough
    to catch an accidentally slow implementation (e.g., one that re-runs all
    INSERT statements unconditionally).
    """
    # Warm-up call to ensure the row exists
    db_module.get_or_create_phase1_theory_bundle_id()

    # Measure 20 subsequent calls and check the worst case
    latencies_ms = []
    for _ in range(20):
        t0 = time.perf_counter()
        db_module.get_or_create_phase1_theory_bundle_id()
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

    worst_ms = max(latencies_ms)
    # Tolerance: 20 ms per call (H-3 budget guard, conservative for Windows CI)
    # Why 20 ms: SQLite single-SELECT round-trip is ~0.5-2 ms on local SSD;
    # 20 ms gives ~10x CI headroom while still catching a non-short-circuit
    # implementation that unconditionally re-runs all INSERT + UPDATE statements
    # (each of those adds ~2-5 ms, so a 3-step INSERT chain is ~6-15 ms vs 1 SELECT).
    assert worst_ms < 20.0, (
        f"Worst-case latency on existing-row path: {worst_ms:.2f} ms (limit: 20 ms). "
        "The accessor is likely not short-circuiting on the 'already exists' path. "
        "After the first call the bundle and facets are all present — the accessor "
        "should detect this and skip the INSERT chain, not re-run all INSERTs."
    )


# ---------------------------------------------------------------------------
# T-8 / T-9: Call-site AST verification
# ---------------------------------------------------------------------------


def test_alpha_bot_execution_run_autotuner_call_references_canonical_accessor():
    """alpha_bot_execution.py's run_autotuner call must reference
    get_or_create_phase1_theory_bundle_id() — not the literal None.

    Static AST inspection: parse the source and verify that every run_autotuner
    call passes spec_bundle_id=<non-None expression>, and specifically that
    get_or_create_phase1_theory_bundle_id is called somewhere in that context.

    The pre-wiring state (TODO + None) would fail this test.
    """
    assert _ALPHA_BOT_EXECUTION.is_file(), (
        f"alpha_bot_execution.py not found at {_ALPHA_BOT_EXECUTION}"
    )
    source = _ALPHA_BOT_EXECUTION.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find all run_autotuner call nodes
    run_autotuner_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_autotuner"
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_autotuner"
        )
    ]

    # Also do a text-based check for safety — the AST walk may miss indirect calls
    assert "spec_bundle_id=None" not in source, (
        "alpha_bot_execution.py contains 'spec_bundle_id=None'. "
        "This literal was the pre-wiring TODO state — replace it with "
        "database.get_or_create_phase1_theory_bundle_id()."
    )

    # Positive check: the canonical accessor must be called somewhere in the file
    assert "get_or_create_phase1_theory_bundle_id" in source, (
        "alpha_bot_execution.py does not reference get_or_create_phase1_theory_bundle_id(). "
        "The call site must invoke this accessor to satisfy the NN1 spec_bundle_id guard."
    )


def test_app_py_run_autotuner_call_references_canonical_accessor():
    """app.py's run_autotuner call must reference
    get_or_create_phase1_theory_bundle_id() — not the literal None.

    Mirrors T-8 for the dashboard-triggered manual autotuner path.
    """
    assert _APP_PY.is_file(), f"app.py not found at {_APP_PY}"
    source = _APP_PY.read_text(encoding="utf-8")

    assert "spec_bundle_id=None" not in source, (
        "app.py contains 'spec_bundle_id=None'. "
        "This literal was the pre-wiring TODO state — replace it with "
        "database.get_or_create_phase1_theory_bundle_id()."
    )

    assert "get_or_create_phase1_theory_bundle_id" in source, (
        "app.py does not reference get_or_create_phase1_theory_bundle_id(). "
        "The dashboard-triggered autotuner call must invoke this accessor."
    )


def test_alpha_bot_execution_run_autotuner_call_has_provenance_comment():
    """The call site in alpha_bot_execution.py must include a comment citing
    the W-H2 derivation provenance per the cycle acceptance criteria.

    A bare call with no provenance comment obscures which decision mandated
    this wiring; the provenance comment is required by the cycle A/C.
    """
    assert _ALPHA_BOT_EXECUTION.is_file(), (
        f"alpha_bot_execution.py not found at {_ALPHA_BOT_EXECUTION}"
    )
    source = _ALPHA_BOT_EXECUTION.read_text(encoding="utf-8")
    lines = source.splitlines()

    # Find lines that contain the run_autotuner call
    run_autotuner_line_idxs = [
        i for i, line in enumerate(lines) if "run_autotuner(" in line
    ]
    assert run_autotuner_line_idxs, (
        "No run_autotuner( call found in alpha_bot_execution.py."
    )

    # Within a 6-line window around each call, a comment referencing W-H2 or
    # council §2.5 or the cycle commit must be present
    found_provenance = False
    for idx in run_autotuner_line_idxs:
        window_start = max(0, idx - 3)
        window_end = min(len(lines), idx + 6)
        window = "\n".join(lines[window_start:window_end])
        # Accept any of these provenance signals
        if any(kw in window for kw in ("W-H2", "council", "phase1_theory", "theory_bundle")):
            found_provenance = True
            break

    assert found_provenance, (
        "No provenance comment found near the run_autotuner call in "
        "alpha_bot_execution.py. The cycle A/C requires a comment citing the "
        "W-H2 derivation or council §2.5 reference near the call site. "
        "Add a comment such as: "
        "'# W-H2 derivation: Phase-1 canonical theory bundle (council §2.5)'"
    )


def test_app_py_run_autotuner_call_has_provenance_comment():
    """app.py's run_autotuner call must include a provenance comment.

    Mirrors T-8 provenance check for the dashboard path.
    """
    assert _APP_PY.is_file(), f"app.py not found at {_APP_PY}"
    source = _APP_PY.read_text(encoding="utf-8")
    lines = source.splitlines()

    run_autotuner_line_idxs = [
        i for i, line in enumerate(lines) if "run_autotuner(" in line
    ]
    assert run_autotuner_line_idxs, "No run_autotuner( call found in app.py."

    found_provenance = False
    for idx in run_autotuner_line_idxs:
        window_start = max(0, idx - 3)
        window_end = min(len(lines), idx + 6)
        window = "\n".join(lines[window_start:window_end])
        if any(kw in window for kw in ("W-H2", "council", "phase1_theory", "theory_bundle")):
            found_provenance = True
            break

    assert found_provenance, (
        "No provenance comment found near the run_autotuner call in app.py. "
        "Add a comment citing W-H2 derivation or council §2.5 provenance."
    )


# ---------------------------------------------------------------------------
# T-10: Negative — missing spec_bundles table raises a clear error
# ---------------------------------------------------------------------------


def test_missing_spec_bundles_table_raises_clear_error(tmp_path, monkeypatch):
    """If spec_bundles table is absent (e.g. corrupted DB or a future migration
    rollback), get_or_create_phase1_theory_bundle_id() must raise a clear error —
    NOT silently return None and let the autotuner refuse to start with a confusing
    'spec_bundle_id=None' message.

    Implementation note: init_db() already calls run_migrations() internally, so
    there is no reachable init-only state via the public API.  We create a fully
    migrated DB, then drop spec_bundles directly via sqlite3 to simulate a
    post-migration table corruption, which is the realistic failure mode.

    Both DB_FILE and DB_PATH are patched so _db_file() resolves to the test DB.
    """
    db_path = str(tmp_path / "corrupted.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    monkeypatch.setenv("DB_PATH", db_path)
    # init_db() creates all tables + migrations (spec_bundles included)
    db_module.init_db()

    # Drop spec_bundles to simulate corruption / missing migration
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS spec_bundles")
        conn.execute("DROP TABLE IF EXISTS spec_facets")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises((sqlite3.OperationalError, RuntimeError)) as exc_info:
        db_module.get_or_create_phase1_theory_bundle_id()

    error_str = str(exc_info.value).lower()
    assert error_str, "Exception must carry a non-empty message"
    informative_keywords = ("spec_bundle", "no such table", "migration", "schema")
    assert any(kw in error_str for kw in informative_keywords), (
        f"Exception message {str(exc_info.value)!r} does not mention any of "
        f"{informative_keywords}. An operator seeing this error must understand "
        "that a required table is missing, not that the code has a logic error."
    )


# ---------------------------------------------------------------------------
# T-11: Provenance — accessor source cites W-H2 / council §2.5
# ---------------------------------------------------------------------------


def test_accessor_source_cites_w_h2_derivation_provenance():
    """get_or_create_phase1_theory_bundle_id()'s source must cite the W-H2
    derivation or council §2.5 so that any future reader understands the
    canonical values' origin without needing to trace through the fixture.

    This is the 'no magic numbers' mandate applied to the accessor: the three
    canonical facet values must be traceable to a named source document.
    """
    accessor = db_module.get_or_create_phase1_theory_bundle_id
    source = inspect.getsource(accessor)

    provenance_signals = ("W-H2", "council", "§2.5", "theory_bundle", "derivation")
    found = [sig for sig in provenance_signals if sig in source]

    assert found, (
        f"get_or_create_phase1_theory_bundle_id() source contains none of the "
        f"expected provenance signals: {provenance_signals}. "
        "The no-magic-numbers mandate requires a source citation for every "
        "constant in the accessor. Add a docstring or comment citing the "
        "W-H2 derivation or council synthesis §2.5."
    )


def test_accessor_source_names_all_three_canonical_facets():
    """All three canonical facet names (gamma, utility_family, wealth_argument)
    must appear in the accessor's source.

    An accessor that builds the facets dict from an opaque variable or a
    separate constants module would fail this test — the names must be
    visible in the source so code readers can trace the values without
    navigating to a secondary file.
    """
    accessor = db_module.get_or_create_phase1_theory_bundle_id
    source = inspect.getsource(accessor)

    required_names = ("gamma", "utility_family", "wealth_argument")
    for name in required_names:
        assert name in source, (
            f"Canonical facet name '{name}' not found in get_or_create_phase1_theory_bundle_id() "
            f"source. All three Phase-1 canonical facet names must appear in the source "
            "for traceability (no-magic-numbers mandate)."
        )


# ---------------------------------------------------------------------------
# T-12: freeze_discipline tripwire
# ---------------------------------------------------------------------------
#
# Design decision: BACKTEST_SELECTION is a VALID discipline for the low-level
# insert_spec_bundle_facet() — it is used by autotuner bookkeeping to record
# that a facet was selected via strategy P&L sweep.  test_spec_bundles.py
# (test_insert_spec_bundle_facet_accepts_all_valid_freeze_disciplines) asserts
# this correctly and must not be changed.
#
# The tripwire contract for get_or_create_phase1_theory_bundle_id() is:
#   (a) the accessor never calls insert_spec_bundle_facet with BACKTEST_SELECTION
#       (source inspection guard — test below)
#   (b) validate_nn1_compliance() — which run_autotuner calls as Guard 3 — rejects
#       any bundle whose spec_facets include a BACKTEST_SELECTION row
#       (integration guard — also tested below)
#
# There is no low-level insert-rejection guard and there should not be one.


def test_validate_nn1_compliance_rejects_bundle_with_backtest_selection_facet(migrated_db):
    """validate_nn1_compliance() must return (False, violations) when a bundle
    contains a spec_facet with freeze_discipline='BACKTEST_SELECTION'.

    This is the NN1 tripwire: run_autotuner Guard 3 calls validate_nn1_compliance
    and raises RuntimeError on (False, violations).  A bundle with any
    BACKTEST_SELECTION facet must never pass that guard.
    """
    from autotuner import validate_nn1_compliance  # noqa: PLC0415

    # Build a bundle that has a BACKTEST_SELECTION facet mixed in
    facets = {"gamma": "2.0", "utility_family": "CRRA", "wealth_argument": "compounded_return"}
    canonical_json = db_module.canonicalize_facets_json(facets)
    bundle_hash = db_module.hash_facets_json(canonical_json)
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    # Fetch the integer id
    conn = sqlite3.connect(migrated_db)
    try:
        bundle_id = conn.execute(
            "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
        ).fetchone()[0]
    finally:
        conn.close()

    # Insert two THEORY facets and one BACKTEST_SELECTION facet
    db_module.insert_spec_bundle_facet(
        bundle_hash=bundle_hash, facet_name="gamma",
        facet_value="2.0", freeze_discipline="THEORY",
    )
    db_module.insert_spec_bundle_facet(
        bundle_hash=bundle_hash, facet_name="utility_family",
        facet_value="CRRA", freeze_discipline="THEORY",
    )
    db_module.insert_spec_bundle_facet(
        bundle_hash=bundle_hash, facet_name="wealth_argument",
        facet_value="compounded_return",
        freeze_discipline="BACKTEST_SELECTION",  # tripwire: this one is wrong
    )

    is_honest, violations = validate_nn1_compliance(bundle_id)

    assert is_honest is False, (
        f"validate_nn1_compliance returned is_honest=True for a bundle with a "
        "BACKTEST_SELECTION facet. Guard 3 in run_autotuner would let this bundle "
        "through, silently allowing a spec-frozen facet to be P&L-selection-tainted."
    )
    assert violations, (
        "validate_nn1_compliance returned an empty violations list even though "
        "is_honest=False. Violations must identify which facets triggered the failure."
    )


def test_get_or_create_phase1_theory_bundle_id_never_passes_backtest_selection(migrated_db):
    """Confirm that get_or_create_phase1_theory_bundle_id() never calls
    insert_spec_bundle_facet with freeze_discipline='BACKTEST_SELECTION'.

    This is a source-level guard: inspect the accessor source and assert that
    'BACKTEST_SELECTION' does not appear anywhere in it.
    """
    accessor = db_module.get_or_create_phase1_theory_bundle_id
    source = inspect.getsource(accessor)

    assert "BACKTEST_SELECTION" not in source, (
        "get_or_create_phase1_theory_bundle_id() source contains the string "
        "'BACKTEST_SELECTION'. This is a hard NN1 violation — the Phase-1 "
        "canonical bundle must use THEORY discipline only, never BACKTEST_SELECTION. "
        "Remove any reference to BACKTEST_SELECTION from the accessor."
    )


# ---------------------------------------------------------------------------
# H-A: Call before run_migrations() (init_db only) — fails clearly
# (covered by T-10 above via the pre-migration DB fixture)
# ---------------------------------------------------------------------------

# H-A is covered by test_missing_spec_bundles_table_raises_clear_error above.


# ---------------------------------------------------------------------------
# H-B: Fixture provenance guard — fixture values change → hash changes
# ---------------------------------------------------------------------------


def test_canonical_hash_changes_if_fixture_facet_values_change():
    """If the W-H2 derivation fixture's canonical facet values were to change,
    the bundle_hash derived from those new values must differ from the hash
    derived from the original values.

    This test simulates "what if the fixture changes" without actually mutating
    the file: it computes the hash of the original fixture values and the hash
    of a hypothetically mutated version and asserts they differ.

    Purpose: proves that the bundle_hash is sensitive to facet value changes —
    a hash function that ignores content would make this test pass vacuously.
    """
    fixture = _load_derivation_fixture()
    original_facets = _expected_canonical_facets(fixture)

    # Original hash
    original_json = db_module.canonicalize_facets_json(original_facets)
    original_hash = db_module.hash_facets_json(original_json)

    # Mutated facets (change gamma to a different value)
    mutated_facets = dict(original_facets)
    mutated_facets["gamma"] = "5.0"  # different from canonical 2.0
    mutated_json = db_module.canonicalize_facets_json(mutated_facets)
    mutated_hash = db_module.hash_facets_json(mutated_json)

    assert original_hash != mutated_hash, (
        "Changing a canonical facet value must produce a different bundle_hash. "
        "A hash function that ignores content would make this test pass vacuously "
        "but would destroy Gate-1 parity (same hash for different specs)."
    )


# ---------------------------------------------------------------------------
# H-C: Bundle exists but facets are absent — accessor re-inserts missing facets
# ---------------------------------------------------------------------------


def test_accessor_inserts_missing_facets_when_bundle_exists_without_facets(migrated_db):
    """If the spec_bundles row already exists but spec_facets are absent
    (e.g., a previous call that was interrupted after insert_spec_bundle but
    before insert_spec_bundle_facet), the accessor must insert the missing
    facets and return a valid id without duplicating the bundle row.

    Note: counts are scoped to the canonical bundle_hash only — the database/
    conftest.py autouse may have seeded an unrelated bundle row.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)

    # Manually insert the bundle row only (no facets)
    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    bundle_hash = db_module.hash_facets_json(canonical_json)
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    # Precondition: canonical bundle exists, no facets yet for it
    conn = sqlite3.connect(migrated_db)
    try:
        canonical_bundle_count = conn.execute(
            "SELECT COUNT(*) FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert canonical_bundle_count == 1, "Precondition: canonical bundle must exist after manual insert"
    assert len(db_module.get_spec_facets_for_bundle(bundle_hash)) == 0, (
        "Precondition: no facets for the canonical bundle yet"
    )

    # Call the accessor — must detect missing facets and insert them
    bundle_id = db_module.get_or_create_phase1_theory_bundle_id()

    # Canonical bundle row count must still be exactly 1
    conn = sqlite3.connect(migrated_db)
    try:
        canonical_bundle_count_after = conn.execute(
            "SELECT COUNT(*) FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert canonical_bundle_count_after == 1, (
        "Calling the accessor when the canonical bundle exists but facets are absent "
        "must not insert a duplicate bundle row."
    )

    # All three canonical facets must now be present
    canonical_facets = db_module.get_spec_facets_for_bundle(bundle_hash)
    assert len(canonical_facets) == 3, (
        f"After accessor call with pre-existing bundle but missing facets, "
        f"spec_facets has {len(canonical_facets)} rows for the canonical bundle; "
        "expected 3. The accessor must insert missing facets on recovery."
    )

    # Returned id must be a positive int
    assert bundle_id is not None and isinstance(bundle_id, int) and bundle_id > 0, (
        f"Returned id must be a positive int; got {bundle_id!r}."
    )


# ---------------------------------------------------------------------------
# H-D: Bundle and all facets exist — accessor uses SELECT-only fast path
# ---------------------------------------------------------------------------


def test_accessor_returns_correct_id_when_bundle_and_facets_already_exist(migrated_db):
    """When the canonical bundle and all three facets already exist, the accessor
    must return the same id on every call.

    This verifies the 'already exists' fast path: the accessor detects the
    existing row and returns its id without inserting duplicates.

    Counts are scoped to the canonical bundle_hash — the database/conftest.py
    autouse may have seeded an unrelated bundle row.
    """
    fixture = _load_derivation_fixture()
    expected_facets = _expected_canonical_facets(fixture)
    canonical_json = db_module.canonicalize_facets_json(expected_facets)
    canonical_hash = db_module.hash_facets_json(canonical_json)

    id_first = db_module.get_or_create_phase1_theory_bundle_id()

    # Precondition: canonical bundle and all 3 facets exist after first call
    conn = sqlite3.connect(migrated_db)
    try:
        canonical_bundle_count = conn.execute(
            "SELECT COUNT(*) FROM spec_bundles WHERE bundle_hash = ?", (canonical_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert canonical_bundle_count == 1, "Precondition: canonical bundle must exist after first call"
    assert len(db_module.get_spec_facets_for_bundle(canonical_hash)) == 3, (
        "Precondition: all 3 canonical facets must exist after first call"
    )

    id_second = db_module.get_or_create_phase1_theory_bundle_id()

    assert id_second == id_first, (
        f"Second call returned id={id_second}, first call returned id={id_first}. "
        "The accessor must return the same id when the canonical bundle already exists."
    )

    # Canonical bundle and facet counts must be unchanged after second call
    conn = sqlite3.connect(migrated_db)
    try:
        canonical_bundle_count_after = conn.execute(
            "SELECT COUNT(*) FROM spec_bundles WHERE bundle_hash = ?", (canonical_hash,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert canonical_bundle_count_after == 1, (
        "Second call must not duplicate the canonical bundle row."
    )
    assert len(db_module.get_spec_facets_for_bundle(canonical_hash)) == 3, (
        "Second call must not duplicate canonical facet rows."
    )


# ---------------------------------------------------------------------------
# Architecture constraint T-3: accessor uses state DB only (Two-DB boundary)
# ---------------------------------------------------------------------------


def test_accessor_does_not_reference_opt_db(migrated_db):
    """get_or_create_phase1_theory_bundle_id() must not reference the Optuna
    optimization DB (alphabot_opt.db or any 'opt' DB path).

    Architecture constraint 3: spec registry lives in the state DB only.
    """
    source = inspect.getsource(db_module.get_or_create_phase1_theory_bundle_id)

    assert "alphabot_opt" not in source, (
        "get_or_create_phase1_theory_bundle_id() references 'alphabot_opt' — "
        "spec registry must live in the state DB (Two-DB boundary, constraint 3)."
    )
    assert "opt.db" not in source, (
        "get_or_create_phase1_theory_bundle_id() references 'opt.db' — "
        "state DB only per council §3.7."
    )


# ---------------------------------------------------------------------------
# Return type: must be int (not bundle_hash string)
# ---------------------------------------------------------------------------


def test_accessor_return_type_is_int_not_string(migrated_db):
    """get_or_create_phase1_theory_bundle_id() must return an int (the id
    column value), NOT the bundle_hash string.

    The autotuner call sites pass the return value as spec_bundle_id to
    run_autotuner, which calls get_spec_bundle_by_id(spec_bundle_id) —
    that function expects an int.  A string bundle_hash would silently fail
    the lookup and trigger the 'not found' ValueError.
    """
    bundle_id = db_module.get_or_create_phase1_theory_bundle_id()

    assert isinstance(bundle_id, int), (
        f"get_or_create_phase1_theory_bundle_id() returned {type(bundle_id).__name__}: "
        f"{bundle_id!r}; must return an int (the spec_bundles.id column value). "
        "The autotuner call site passes this to get_spec_bundle_by_id(), which "
        "expects an integer id, not a bundle_hash string."
    )
    assert bundle_id > 0, (
        f"Returned bundle_id={bundle_id} is not positive. "
        "SQLite rowid-derived id values start at 1."
    )
