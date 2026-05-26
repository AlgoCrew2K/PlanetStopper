"""
NN1 Spec-Freeze Discipline — RED tests.

These tests are written BEFORE the implementation. All tests will FAIL until:
  1. autotuner.py adds FREEZE_DISCIPLINE_* and EVIDENCE_SOURCE_* named constants
     (D1) with NN1_HONEST_DISCIPLINES as a frozenset.
  2. autotuner.py adds validate_nn1_compliance(spec_bundle_id) -> tuple[bool, list[str]]
     (D2) — reads spec_facets for the bundle; NN1-honest iff every facet's
     freeze_discipline is in NN1_HONEST_DISCIPLINES; default-deny for unknown values.
  3. autotuner.py adds validate_search_space_nn1() (D5) — raises RuntimeError if any
     known-frozen facet name appears in OPTUNA_SEARCH_SPACE_KEYS.
  4. validate_search_space_nn1() is called at the top of run_autotuner BEFORE
     optuna.create_study (D5 wiring).
  5. When validate_nn1_compliance detects BACKTEST_SELECTION facets, it writes them
     into researcher_dof_ledger via database.insert_dof_ledger_row with
     evidence_source='BACKTEST_SELECTION' and n_configs_searched populated.
  6. A BACKTEST_SELECTION facet on any load-bearing Phase-1 facet is a HARD FAIL —
     run_autotuner refuses to start (team-lead binding, NN1 load-bearing hazard).
  7. A missing (None) spec_bundle_id causes run_autotuner to refuse to start.
  8. A bundle_hash mismatch (claimed hash != computed hash from facets_json) causes
     refusal to start.

These tests do NOT test the Optuna objective, the BHY haircut pipeline, or the
N_effective accounting. Those are separate plans.

Fixture provenance:
  tests/fixtures/math/nn1_spec_freeze_discipline.json — schema-derived from the plan
  D1-D5 deliverables and council synthesis §2.5 §3.3 §3.7. No producer-computed values
  (no rates, Sharpe ratios, dollar amounts, percents). All expected values are structural
  shape assertions or named enum constants.

Reference: council synthesis §2.5 ("NN1 is the precondition that keeps the BHY haircut
  TRUE"), §3.3 (three Phase-1 THEORY facets), §3.7 (spec_bundles / spec_facets schema).
  feature-plans/decision-science/phase-1/nn1-spec-freeze-discipline/plan.md.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths and fixture loading
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "nn1_spec_freeze_discipline.json"
)
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _import_autotuner():
    import autotuner
    return autotuner


def _import_database():
    import database
    return database


# ---------------------------------------------------------------------------
# Helpers — DB scaffolding
# ---------------------------------------------------------------------------

def _make_all_theory_bundle(db, facets: "list[dict] | None" = None) -> tuple[str, int]:
    """Insert a spec_bundle + three all-THEORY Phase-1 facets.

    Returns (bundle_hash, bundle_id) where bundle_id is the rowid from
    spec_bundles. If custom facets are provided, they replace the defaults.
    """
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)

    _db.insert_spec_bundle(
        bundle_hash=bundle_hash,
        facets_json=canonical_json,
    )
    row = _db.get_spec_bundle(bundle_hash)
    # The rowid of the inserted spec_bundles row — used as spec_bundle_id.
    # Fetched by re-querying since insert_spec_bundle returns None.
    import sqlite3
    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    default_facets = [
        {"facet_name": "gamma", "facet_value": "2.0", "freeze_discipline": "THEORY"},
        {"facet_name": "utility_family", "facet_value": "CRRA", "freeze_discipline": "THEORY"},
        {"facet_name": "wealth_argument", "facet_value": "compounded_return", "freeze_discipline": "THEORY"},
    ]
    for f in (facets or default_facets):
        _db.insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name=f["facet_name"],
            facet_value=f["facet_value"],
            freeze_discipline=f["freeze_discipline"],
        )

    return bundle_hash, bundle_id


def _make_single_backtest_selection_bundle(db) -> tuple[str, int]:
    """Bundle where gamma has freeze_discipline=BACKTEST_SELECTION."""
    import database as _db

    canon_facets = {
        "gamma": "1.5",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)

    _db.insert_spec_bundle(
        bundle_hash=bundle_hash,
        facets_json=canonical_json,
    )
    import sqlite3
    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    # gamma is intentionally marked BACKTEST_SELECTION — NN1 violation
    _db.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="gamma",
        facet_value="1.5",
        freeze_discipline="BACKTEST_SELECTION",
    )
    _db.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="utility_family",
        facet_value="CRRA",
        freeze_discipline="THEORY",
    )
    _db.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="wealth_argument",
        facet_value="compounded_return",
        freeze_discipline="THEORY",
    )
    return bundle_hash, bundle_id


def _make_mixed_violation_bundle(db) -> tuple[str, int]:
    """Bundle with THEORY, MANDATE, and one BACKTEST_SELECTION facet."""
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "calibrated_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)

    _db.insert_spec_bundle(
        bundle_hash=bundle_hash,
        facets_json=canonical_json,
    )
    import sqlite3
    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    _db.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="gamma",
        facet_value="2.0",
        freeze_discipline="THEORY",
    )
    _db.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="utility_family",
        facet_value="CRRA",
        freeze_discipline="MANDATE",
    )
    # wealth_argument is the BACKTEST_SELECTION violation
    _db.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="wealth_argument",
        facet_value="calibrated_return",
        freeze_discipline="BACKTEST_SELECTION",
    )
    return bundle_hash, bundle_id


# ---------------------------------------------------------------------------
# T1 — Fixture coherence: fixture file is well-formed and structurally complete
# ---------------------------------------------------------------------------

def test_nn1_fixture_is_structurally_complete():
    """The fixture must be loadable and contain all required top-level keys.

    This test fails if the fixture file is missing or malformed — it does not
    assert producer-computed values, only shape.
    """
    fixture = _load_fixture()
    for key in ("freeze_disciplines", "phase_1_load_bearing_facets",
                "validate_nn1_compliance_contract", "validate_search_space_nn1_contract",
                "nn1_violation_scenarios", "dof_ledger_population"):
        assert key in fixture, f"fixture missing required key: {key!r}"

    honest_set = fixture["freeze_disciplines"]["honest_set"]
    assert isinstance(honest_set, list)
    assert len(honest_set) >= 1  # at minimum one honest discipline must exist

    assert fixture["freeze_disciplines"]["violation_backtest_selection"] == "BACKTEST_SELECTION"


# ---------------------------------------------------------------------------
# T2 — D1: Named constant exports exist in autotuner
# ---------------------------------------------------------------------------

def test_nn1_honest_disciplines_constant_exists_and_is_frozenset():
    """autotuner must expose NN1_HONEST_DISCIPLINES as a frozenset.

    The frozenset contract is load-bearing: it prevents accidental mutation
    and allows O(1) membership tests in validate_nn1_compliance.
    """
    at = _import_autotuner()
    assert hasattr(at, "NN1_HONEST_DISCIPLINES"), (
        "autotuner.NN1_HONEST_DISCIPLINES not found — D1 not implemented"
    )
    assert isinstance(at.NN1_HONEST_DISCIPLINES, frozenset), (
        "NN1_HONEST_DISCIPLINES must be a frozenset, not mutable"
    )


def test_freeze_discipline_named_constants_all_present():
    """autotuner must export each FREEZE_DISCIPLINE_* named constant (D1).

    These are the single source of truth for the autotuner-side consumer.
    Values must exactly match the fixture's canonical strings.
    """
    at = _import_autotuner()
    fixture = _load_fixture()
    honest_set = set(fixture["freeze_disciplines"]["honest_set"])

    required_constants = {
        "FREEZE_DISCIPLINE_THEORY": "THEORY",
        "FREEZE_DISCIPLINE_MANDATE": "MANDATE",
        "FREEZE_DISCIPLINE_STYLIZED_FACT": "STYLIZED_FACT",
        "FREEZE_DISCIPLINE_CALIBRATION": "CALIBRATION",
        "FREEZE_DISCIPLINE_BACKTEST_SELECTION": "BACKTEST_SELECTION",
    }
    for attr, expected_value in required_constants.items():
        assert hasattr(at, attr), f"autotuner.{attr} not found — D1 not implemented"
        assert getattr(at, attr) == expected_value, (
            f"autotuner.{attr} = {getattr(at, attr)!r}, expected {expected_value!r}"
        )


def test_nn1_honest_disciplines_contains_all_fixture_honest_values():
    """NN1_HONEST_DISCIPLINES must include every discipline the fixture marks as honest.

    A discipline in the fixture's honest_set that is absent from NN1_HONEST_DISCIPLINES
    would cause honest bundles to be falsely rejected.
    """
    at = _import_autotuner()
    fixture = _load_fixture()
    for discipline in fixture["freeze_disciplines"]["honest_set"]:
        assert discipline in at.NN1_HONEST_DISCIPLINES, (
            f"NN1_HONEST_DISCIPLINES is missing {discipline!r} — "
            f"honest bundles using this discipline would be incorrectly rejected"
        )


def test_backtest_selection_not_in_nn1_honest_disciplines():
    """BACKTEST_SELECTION must NOT appear in NN1_HONEST_DISCIPLINES.

    If it were present, the violation-detection logic would allow P&L-toured
    facets to pass as honest — the BHY haircut would silently undercount N.
    """
    at = _import_autotuner()
    assert "BACKTEST_SELECTION" not in at.NN1_HONEST_DISCIPLINES, (
        "BACKTEST_SELECTION is in NN1_HONEST_DISCIPLINES — "
        "this would allow NN1-violation facets to pass as honest"
    )


# ---------------------------------------------------------------------------
# T3 — D2 / T1 (plan): all-THEORY bundle passes validation
# ---------------------------------------------------------------------------

def test_validate_nn1_compliance_all_theory_bundle_returns_honest_and_empty_violations():
    """An all-THEORY bundle must return (True, []) — zero violations, autotuner proceeds.

    Fixture scenario: nn1_violation_scenarios.all_theory_bundle.
    """
    import database as _db
    at = _import_autotuner()

    assert hasattr(at, "validate_nn1_compliance"), (
        "autotuner.validate_nn1_compliance not found — D2 not implemented"
    )

    _, bundle_id = _make_all_theory_bundle(_db)
    is_honest, violations = at.validate_nn1_compliance(bundle_id)

    assert is_honest is True, (
        f"All-THEORY bundle returned is_honest={is_honest!r}; expected True"
    )
    assert violations == [], (
        f"All-THEORY bundle returned violations={violations!r}; expected []"
    )


# ---------------------------------------------------------------------------
# T4 — D2 / T2 (plan): single BACKTEST_SELECTION facet trips violation
# ---------------------------------------------------------------------------

def test_validate_nn1_compliance_single_backtest_selection_facet_is_not_honest():
    """A bundle with even one BACKTEST_SELECTION facet must return (False, [...]).

    Fixture scenario: nn1_violation_scenarios.single_backtest_selection_facet.
    The violation message must name the offending facet.
    """
    import database as _db
    at = _import_autotuner()

    _, bundle_id = _make_single_backtest_selection_bundle(_db)
    is_honest, violations = at.validate_nn1_compliance(bundle_id)

    assert is_honest is False, (
        "Bundle with gamma=BACKTEST_SELECTION returned is_honest=True; "
        "BACKTEST_SELECTION facets must trip the violation flag"
    )
    assert len(violations) >= 1, (
        "violations list is empty for a BACKTEST_SELECTION bundle — "
        "the violation message is required so the operator sees what failed"
    )

    # The message must name 'gamma' and 'BACKTEST_SELECTION'.
    combined = " ".join(violations)
    assert "gamma" in combined, (
        f"violations {violations!r} does not name the offending facet 'gamma'"
    )
    assert "BACKTEST_SELECTION" in combined, (
        f"violations {violations!r} does not name the violation discipline 'BACKTEST_SELECTION'. "
        "Renaming the violation string requires re-justifying council §2.5 disclosure."
    )


# ---------------------------------------------------------------------------
# T5 — D2 / mixed bundle: partial honesty is a fail (one bad facet = whole bundle bad)
# ---------------------------------------------------------------------------

def test_validate_nn1_compliance_mixed_bundle_is_not_honest():
    """A bundle with mixed THEORY + BACKTEST_SELECTION facets must return is_honest=False.

    Even one BACKTEST_SELECTION among otherwise-honest facets is an NN1 violation —
    partial honesty is not honesty.
    """
    import database as _db
    at = _import_autotuner()

    _, bundle_id = _make_mixed_violation_bundle(_db)
    is_honest, violations = at.validate_nn1_compliance(bundle_id)

    assert is_honest is False, (
        "Mixed bundle (THEORY + BACKTEST_SELECTION) returned is_honest=True; "
        "partial honesty must not pass as full honesty"
    )
    assert len(violations) >= 1, (
        "violations list is empty for a mixed bundle with BACKTEST_SELECTION facets"
    )


# ---------------------------------------------------------------------------
# T6 — D2 default-deny: unknown freeze_discipline is treated as a violation
# ---------------------------------------------------------------------------

def test_validate_nn1_compliance_unknown_discipline_is_treated_as_violation():
    """A facet with an unrecognised freeze_discipline must be treated as a violation.

    The risk callout in the plan: 'any value NOT in NN1_HONEST_DISCIPLINES is treated
    as a violation'. Silent fall-through is forbidden — a typo or forward-compat shape
    must never silently allow a facet through as honest.
    """
    import database as _db
    at = _import_autotuner()

    # Build a bundle where one facet has a discipline value not in either honest or
    # known-violation enums — simulates a typo or a future forward-compat value.
    # database.insert_spec_bundle_facet enforces _VALID_FREEZE_DISCIPLINES at the
    # application layer. We bypass it by writing directly to the DB to simulate the
    # scenario the default-deny rule must handle.
    canon_facets = {"gamma": "2.0", "utility_family": "CRRA", "wealth_argument": "derived"}
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)
    _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    import sqlite3
    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    # Directly insert a row with an unknown discipline to bypass app-layer validation.
    conn.execute(
        "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
        "VALUES (?, ?, ?, ?)",
        (bundle_hash, "gamma", "2.0", "UNKNOWN_FUTURE_VALUE"),
    )
    conn.commit()
    conn.close()

    is_honest, violations = at.validate_nn1_compliance(bundle_id)

    assert is_honest is False, (
        "Unknown freeze_discipline value returned is_honest=True; "
        "default-deny requires any unrecognised value to be treated as a violation"
    )
    assert len(violations) >= 1, (
        "violations list is empty for an unknown-discipline facet — "
        "the operator must see what value was rejected"
    )


# ---------------------------------------------------------------------------
# T7 — D2 / T3 (plan): OOS evidence_source trips stricter violation label
# ---------------------------------------------------------------------------

def test_validate_nn1_compliance_oos_evidence_source_is_stricter_violation():
    """A facet with evidence_source=OOS must be flagged as a stricter violation.

    OOS-peek (a facet chosen by looking at frozen-eval returns) is worse than
    BACKTEST_SELECTION (chosen by looking at backtest P&L). The plan requires the
    violation message to distinguish these — operators must see the severity gradient.

    Note: database.spec_facets does not currently have an evidence_source column
    (that lives in researcher_dof_ledger). This test pins that the violation detection
    path checks evidence_source where available and labels OOS distinctly — the
    implementer must decide the surface (could be a separate facet_value convention,
    or a joined check against dof_ledger). The test asserts the output shape:
    the violation message for an OOS row must contain 'OOS' and must NOT produce the
    same message as a plain BACKTEST_SELECTION row.
    """
    import database as _db
    at = _import_autotuner()

    # Insert a DOF ledger row with evidence_source=OOS for a bundle that
    # otherwise looks clean (THEORY freeze_discipline). The validate_nn1_compliance
    # function must detect this OOS ledger entry and treat it as a stricter violation.
    _, bundle_id = _make_all_theory_bundle(_db)
    _db.insert_dof_ledger_row(
        facet_name="gamma",
        facet_category="specification",
        decision_type="OOS_PEEK",
        evidence_source="OOS",
        n_configs_searched=1,
        touched_frozen_eval=1,
        spec_bundle_id=str(bundle_id),
        justification="test: OOS-peek violation scenario",
    )

    is_honest, violations = at.validate_nn1_compliance(bundle_id)

    assert is_honest is False, (
        "Bundle with an OOS-peek ledger entry returned is_honest=True; "
        "OOS evidence_source is a hard violation even if freeze_discipline=THEORY"
    )
    assert len(violations) >= 1
    combined = " ".join(violations)
    assert "OOS" in combined, (
        f"violations {violations!r} does not mention 'OOS'; "
        "OOS-peek must be labelled distinctly from BACKTEST_SELECTION (severity gradient)"
    )


# ---------------------------------------------------------------------------
# T8 — D5 / T4 (plan): validate_search_space_nn1 raises on forbidden key in search space
# ---------------------------------------------------------------------------

def test_validate_search_space_nn1_raises_when_gamma_added_to_search_space():
    """validate_search_space_nn1 must raise RuntimeError if 'gamma' is in OPTUNA_SEARCH_SPACE_KEYS.

    Fixture scenario: validate_search_space_nn1_contract.raises_on_forbidden_key.
    Monkeypatching OPTUNA_SEARCH_SPACE_KEYS models the 'let me just A/B gamma' PR risk.
    """
    at = _import_autotuner()

    assert hasattr(at, "validate_search_space_nn1"), (
        "autotuner.validate_search_space_nn1 not found — D5 not implemented"
    )

    contaminated_search_space = at.OPTUNA_SEARCH_SPACE_KEYS | frozenset({"gamma"})
    with patch.object(at, "OPTUNA_SEARCH_SPACE_KEYS", contaminated_search_space):
        with pytest.raises(RuntimeError) as exc_info:
            at.validate_search_space_nn1()

    msg = str(exc_info.value)
    assert "NN1 VIOLATION" in msg, (
        f"RuntimeError message {msg!r} does not contain 'NN1 VIOLATION'; "
        "the error must be clearly labelled so the developer sees the category of failure"
    )
    assert "gamma" in msg, (
        f"RuntimeError message {msg!r} does not name the offending key 'gamma'"
    )


def test_validate_search_space_nn1_raises_for_each_known_forbidden_facet():
    """validate_search_space_nn1 must raise for every facet in the forbidden set.

    The fixture lists all forbidden facets. Each one individually must trigger
    the RuntimeError — not just 'gamma'. Covers Phase-2 pre-named facets.
    """
    at = _import_autotuner()
    fixture = _load_fixture()
    forbidden = fixture["search_space_forbidden_facets"]

    for facet in forbidden:
        contaminated = at.OPTUNA_SEARCH_SPACE_KEYS | frozenset({facet})
        with patch.object(at, "OPTUNA_SEARCH_SPACE_KEYS", contaminated):
            with pytest.raises(RuntimeError, match="NN1 VIOLATION"), (
                # message is the test failure detail if it does NOT raise
                __import__("contextlib").suppress()
            ):
                at.validate_search_space_nn1()
                pytest.fail(
                    f"validate_search_space_nn1 did not raise for forbidden facet {facet!r}"
                )


# ---------------------------------------------------------------------------
# T9 — D5 / T6 (plan): frozen facets are NOT in OPTUNA_SEARCH_SPACE_KEYS (static tripwire)
# ---------------------------------------------------------------------------

def test_gamma_and_phase1_theory_facets_are_not_in_optuna_search_space():
    """gamma, utility_family, wealth_argument must never appear in OPTUNA_SEARCH_SPACE_KEYS.

    This is the static-analysis tripwire (plan T6). It will catch a future PR that
    adds 'gamma' to the search space, breaking NN1 at definition time.
    """
    at = _import_autotuner()
    for facet in ("gamma", "utility_family", "wealth_argument"):
        assert facet not in at.OPTUNA_SEARCH_SPACE_KEYS, (
            f"'{facet}' found in OPTUNA_SEARCH_SPACE_KEYS — "
            f"this is an NN1 violation: {facet!r} is a theory-frozen spec facet, "
            f"not an Optuna trial parameter (council synthesis §2.5)"
        )


# ---------------------------------------------------------------------------
# T10 — D2 wiring: BACKTEST_SELECTION violations are written to researcher_dof_ledger
# ---------------------------------------------------------------------------

def test_validate_nn1_compliance_writes_backtest_selection_to_dof_ledger():
    """BACKTEST_SELECTION facets detected by validate_nn1_compliance must be persisted.

    The detected violations must be written via database.insert_dof_ledger_row with
    evidence_source='BACKTEST_SELECTION' and n_configs_searched populated.
    After the call, count_dof_backtest_selections(spec_bundle_id) must return > 0.
    """
    import database as _db
    at = _import_autotuner()

    _, bundle_id = _make_single_backtest_selection_bundle(_db)

    # Baseline: no DOF rows yet
    s_before = _db.count_dof_backtest_selections(str(bundle_id))

    at.validate_nn1_compliance(bundle_id)

    s_after = _db.count_dof_backtest_selections(str(bundle_id))
    assert s_after > s_before, (
        f"count_dof_backtest_selections({bundle_id}) did not increase after "
        f"validate_nn1_compliance detected a BACKTEST_SELECTION facet; "
        f"before={s_before}, after={s_after}. "
        f"The +S contribution is required for BHY haircut integrity."
    )


def test_validate_nn1_compliance_all_theory_bundle_writes_zero_dof_rows():
    """An all-THEORY bundle must not write any BACKTEST_SELECTION DOF ledger rows.

    S must stay zero — no contribution to N_effective from an honest bundle.
    """
    import database as _db
    at = _import_autotuner()

    _, bundle_id = _make_all_theory_bundle(_db)
    s_before = _db.count_dof_backtest_selections(str(bundle_id))

    at.validate_nn1_compliance(bundle_id)

    s_after = _db.count_dof_backtest_selections(str(bundle_id))
    assert s_after == s_before, (
        f"count_dof_backtest_selections increased for an all-THEORY bundle; "
        f"before={s_before}, after={s_after}. "
        f"An honest bundle must contribute S=0 to the haircut."
    )


# ---------------------------------------------------------------------------
# T11 — T5 (plan): immutability of frozen facets — no UPDATE path exists
# ---------------------------------------------------------------------------

def test_spec_facets_has_no_update_accessor_in_database():
    """database.py must not expose an update_spec_bundle_facet or equivalent function.

    The immutability convention is: new bundle = new row, never modify in place.
    A future maintainer must not be able to silently modify a frozen facet.
    This test is a static inspection of database's public surface.
    """
    import database as _db
    import inspect

    # Any function whose name contains 'update' AND 'facet' would be a
    # write-guard violation. We check for the most likely naming patterns.
    suspicious = [
        name for name in dir(_db)
        if "facet" in name.lower() and
        any(verb in name.lower() for verb in ("update", "modify", "set", "replace", "edit"))
    ]
    assert len(suspicious) == 0, (
        f"database.py exposes potential facet-update accessor(s): {suspicious}. "
        f"The spec-freeze invariant requires immutability — "
        f"facet rows may only be INSERTed (new bundle), never UPDATEd."
    )


# ---------------------------------------------------------------------------
# T12 — run_autotuner signature must accept spec_bundle_id
# ---------------------------------------------------------------------------

def test_run_autotuner_accepts_spec_bundle_id_parameter():
    """run_autotuner must accept a spec_bundle_id keyword parameter.

    Phase-1 strict: every autotuner run requires an explicit pinned spec bundle.
    This test asserts the parameter exists in the function signature. A TypeError
    for an unexpected keyword argument means the parameter hasn't been added yet.
    """
    import inspect
    at = _import_autotuner()

    sig = inspect.signature(at.run_autotuner)
    assert "spec_bundle_id" in sig.parameters, (
        f"run_autotuner signature {sig} does not include 'spec_bundle_id'. "
        f"Phase-1 strict: every autotuner run requires an explicit pinned spec bundle — "
        f"the parameter must be added to enforce this at the call site."
    )


# ---------------------------------------------------------------------------
# T13 — No pinned spec bundle causes run_autotuner to refuse to start
# ---------------------------------------------------------------------------

def test_run_autotuner_refuses_to_start_without_pinned_spec_bundle():
    """run_autotuner must raise when spec_bundle_id is None (Phase-1 strict).

    Phase-1 admits no implicit defaults: every autotuner run requires an explicit
    pinned spec bundle. A None bundle_id is a configuration error, not a silent fallback.
    """
    import inspect
    at = _import_autotuner()

    sig = inspect.signature(at.run_autotuner)
    if "spec_bundle_id" not in sig.parameters:
        pytest.fail(
            "run_autotuner has no 'spec_bundle_id' parameter — "
            "cannot test None-refusal until the parameter exists (see T12)"
        )

    # Pass a minimal plausible call; the implementation must refuse on None bundle.
    with pytest.raises((RuntimeError, ValueError)) as exc_info:
        at.run_autotuner(
            bot_state={},
            current_date_str="2026-01-01",
            account_uuids=[],
            spec_bundle_id=None,
        )

    msg = str(exc_info.value).lower()
    assert any(term in msg for term in ("spec_bundle", "nn1", "bundle")), (
        f"run_autotuner raised {exc_info.type.__name__} but the message {msg!r} "
        f"does not mention spec_bundle or nn1 — the error must be informative"
    )


# ---------------------------------------------------------------------------
# T14 — Hash mismatch causes run_autotuner to refuse to start
# ---------------------------------------------------------------------------

def test_run_autotuner_refuses_to_start_on_bundle_hash_mismatch():
    """run_autotuner must refuse to start when the bundle's facets_json hash doesn't
    match the stored bundle_hash.

    A tampered bundle (facets_json edited after frozen_at) is a corrupt provenance
    record. The system must refuse, not silently proceed.
    """
    import inspect
    import database as _db
    at = _import_autotuner()

    sig = inspect.signature(at.run_autotuner)
    if "spec_bundle_id" not in sig.parameters:
        pytest.fail(
            "run_autotuner has no 'spec_bundle_id' parameter — "
            "cannot test hash-mismatch refusal until the parameter exists (see T12)"
        )

    bundle_hash, bundle_id = _make_all_theory_bundle(_db)

    # Tamper: overwrite facets_json with content whose hash would differ.
    # Direct DB write bypasses application-layer protections to simulate an
    # out-of-band corruption.
    conn = _db.get_connection()
    conn.execute(
        "UPDATE spec_bundles SET facets_json = ? WHERE id = ?",
        ('{"gamma":"99.0","utility_family":"OTHER","wealth_argument":"tampered"}', bundle_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises((RuntimeError, ValueError)) as exc_info:
        at.run_autotuner(
            bot_state={},
            current_date_str="2026-01-01",
            account_uuids=[],
            spec_bundle_id=bundle_id,
        )

    msg = str(exc_info.value).lower()
    assert any(term in msg for term in ("hash", "mismatch", "tamper", "integrity", "corrupt")), (
        f"run_autotuner raised {exc_info.type.__name__} but the message {msg!r} "
        f"does not indicate a hash/integrity issue — "
        f"the error must distinguish a hash mismatch from other failures"
    )


# ---------------------------------------------------------------------------
# T15 — BACKTEST_SELECTION bundle causes run_autotuner hard-fail (load-bearing NN1)
# ---------------------------------------------------------------------------

def test_run_autotuner_refuses_to_start_when_bundle_contains_backtest_selection_facet():
    """run_autotuner must refuse to start when the pinned bundle has a BACKTEST_SELECTION facet.

    This is the PRIMARY load-bearing NN1 enforcement: a P&L-toured spec facet on
    any Phase-1 load-bearing facet (gamma, utility_family, wealth_argument) must
    prevent the autotuner from running. The BHY haircut would be a lie by omission
    if it ran with an NN1-violation bundle.

    This is NOT a soft warning — it is a hard fail (team-lead binding directive).
    """
    import inspect
    import database as _db
    at = _import_autotuner()

    sig = inspect.signature(at.run_autotuner)
    if "spec_bundle_id" not in sig.parameters:
        pytest.fail(
            "run_autotuner has no 'spec_bundle_id' parameter — "
            "cannot test BACKTEST_SELECTION refusal until the parameter exists (see T12)"
        )

    _, bundle_id = _make_single_backtest_selection_bundle(_db)

    with pytest.raises((RuntimeError, ValueError)) as exc_info:
        at.run_autotuner(
            bot_state={},
            current_date_str="2026-01-01",
            account_uuids=[],
            spec_bundle_id=bundle_id,
        )

    msg = str(exc_info.value)
    # Must mention NN1 or BACKTEST_SELECTION so the operator knows why it refused.
    assert any(term in msg for term in ("NN1", "BACKTEST_SELECTION", "spec_freeze", "nn1")), (
        f"run_autotuner raised {exc_info.type.__name__} but the message {msg!r} "
        f"does not mention NN1 or BACKTEST_SELECTION — "
        f"the operator must know the refusal is a spec-freeze enforcement, not a crash"
    )


# ---------------------------------------------------------------------------
# T16 — D5 wiring: validate_search_space_nn1 is called inside run_autotuner
# ---------------------------------------------------------------------------

def test_run_autotuner_calls_validate_search_space_nn1_before_create_study():
    """validate_search_space_nn1 must be invoked at the top of run_autotuner,
    before optuna.create_study, so any leaked frozen facet is caught at runtime.

    Two stubs are required to reach optuna.create_study:
    1. generate_synthetic_history returns a multi-date stub so run_autotuner
       does not short-circuit on total_days < 2.
    2. bot_state includes a symphony whose key matches the stub history key
       so the per-symphony for-loop body executes and create_study is reached.

    The test intercepts create_study to abort early (StopIteration), then
    asserts validate_search_space_nn1 appeared in the call_log before create_study.
    """
    at = _import_autotuner()

    assert hasattr(at, "validate_search_space_nn1"), (
        "autotuner.validate_search_space_nn1 not found — D5 not implemented; "
        "this wiring test cannot run until the function exists"
    )

    import database as _db
    _, bundle_id = _make_all_theory_bundle(_db)

    call_log: list[str] = []

    original_validate = at.validate_search_space_nn1

    def _recording_validate():
        call_log.append("validate_search_space_nn1")
        original_validate()

    # Abort the run early once create_study is reached, before real Optuna work.
    def _abort_create_study(*args, **kwargs):
        call_log.append("create_study")
        raise StopIteration("abort after guard check")

    # Stub synthetic history: key "stub_sym" matches bot_state below.
    # Two dates so total_days >= 2 (avoids the "Need at least 2 days" early-return).
    _stub_history = {
        "stub_sym": {
            "2026-01-02": {"open": 100.0, "close": 101.0},
            "2026-01-03": {"open": 101.0, "close": 102.0},
        }
    }

    # bot_state must contain the same symphony key as the stub history so the
    # per-symphony for-loop body executes and reaches optuna.create_study.
    _bot_state = {"stub_sym": {"name": "stub_sym", "account_uuid": "acc-1"}}

    import inspect
    sig = inspect.signature(at.run_autotuner)
    call_kwargs = (
        {"bot_state": _bot_state, "current_date_str": "2026-01-03",
         "account_uuids": ["acc-1"], "spec_bundle_id": bundle_id}
        if "spec_bundle_id" in sig.parameters
        else {"bot_state": _bot_state, "current_date_str": "2026-01-03",
              "account_uuids": ["acc-1"]}
    )

    with patch.object(at.synthetic_history, "generate_synthetic_history",
                      return_value=_stub_history):
        with patch.object(at, "validate_search_space_nn1", _recording_validate):
            import optuna
            with patch.object(optuna, "create_study", _abort_create_study):
                with pytest.raises((StopIteration, RuntimeError, ValueError, TypeError)):
                    at.run_autotuner(**call_kwargs)

    # validate_search_space_nn1 must have been called before create_study.
    if "validate_search_space_nn1" in call_log and "create_study" in call_log:
        nn1_idx = call_log.index("validate_search_space_nn1")
        study_idx = call_log.index("create_study")
        assert nn1_idx < study_idx, (
            "validate_search_space_nn1 was called AFTER optuna.create_study; "
            "it must run BEFORE create_study to enforce the NN1 search-space guard (D5)"
        )
    else:
        assert "validate_search_space_nn1" in call_log, (
            "validate_search_space_nn1 was never called during run_autotuner — "
            "D5 wiring is missing"
        )


# ---------------------------------------------------------------------------
# T16 — D1: NN1 disclosure block exists alongside OPTUNA_SEARCH_SPACE_KEYS (static)
# ---------------------------------------------------------------------------

def test_nn1_disclosure_block_exists_in_autotuner_source():
    """The NN1 disclosure block (D4) must be present in autotuner.py near OPTUNA_SEARCH_SPACE_KEYS.

    This is a static-source inspection. The block must contain the literal text
    'NN1' and reference 'council §2.5' so a reader of OPTUNA_SEARCH_SPACE_KEYS
    cannot miss the hazard declaration.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")

    assert "NN1" in src, (
        "autotuner.py source does not contain 'NN1' — "
        "the NN1 disclosure block (D4) is missing"
    )
    assert "OPTUNA_SEARCH_SPACE_KEYS" in src, (
        "autotuner.py source does not contain 'OPTUNA_SEARCH_SPACE_KEYS' — "
        "this constant is required by D4 and the existing search-space contract"
    )

    # The disclosure must appear near OPTUNA_SEARCH_SPACE_KEYS.
    # We find the line range and assert the disclosure is within 50 lines.
    lines = src.splitlines()
    ss_line = next(
        (i for i, l in enumerate(lines) if "OPTUNA_SEARCH_SPACE_KEYS" in l), None
    )
    assert ss_line is not None
    window = "\n".join(lines[max(0, ss_line - 10): ss_line + 50])
    assert "NN1" in window, (
        "NN1 disclosure block is not found within 50 lines of OPTUNA_SEARCH_SPACE_KEYS — "
        "the disclosure must appear immediately adjacent to the search space definition (D4)"
    )


# ---------------------------------------------------------------------------
# T18 — POLITIS_WHITE and CADENCE accepted end-to-end by database layer
#        (spec-nn1 gap: these two values are in NN1_HONEST_DISCIPLINES per plan D1
#        but are NOT yet in database._VALID_FREEZE_DISCIPLINES; insert_spec_bundle_facet
#        raises ValueError for any unrecognised value — the implementer must extend
#        database._VALID_FREEZE_DISCIPLINES to match the full D1 enum set)
# ---------------------------------------------------------------------------

def test_database_valid_freeze_disciplines_includes_politis_white_and_cadence():
    """database._VALID_FREEZE_DISCIPLINES must include POLITIS_WHITE and CADENCE.

    Plan D1 names both as honest disciplines in NN1_HONEST_DISCIPLINES. If the
    database enum is not extended, insert_spec_bundle_facet will raise ValueError
    whenever a Phase-2 bundle uses these disciplines — breaking the end-to-end
    insert path silently after the autotuner layer is green.

    spec-nn1 confirmed the current set is {THEORY, MANDATE, STYLIZED_FACT,
    CALIBRATION, BACKTEST_SELECTION} — POLITIS_WHITE and CADENCE are absent.
    """
    import database as _db

    for discipline in ("POLITIS_WHITE", "CADENCE"):
        assert discipline in _db._VALID_FREEZE_DISCIPLINES, (
            f"database._VALID_FREEZE_DISCIPLINES is missing {discipline!r}. "
            f"Plan D1 names it as an honest discipline in NN1_HONEST_DISCIPLINES; "
            f"insert_spec_bundle_facet will raise ValueError for any bundle that uses it. "
            f"The implementer must extend _VALID_FREEZE_DISCIPLINES to include all D1 values."
        )


def test_insert_spec_bundle_facet_accepts_politis_white_and_cadence_end_to_end():
    """insert_spec_bundle_facet must accept POLITIS_WHITE and CADENCE without raising.

    A facet with freeze_discipline='POLITIS_WHITE' or 'CADENCE' must persist
    successfully and be readable back from spec_facets. This pins the full
    round-trip, not just the enum membership check above.
    """
    import database as _db

    for discipline in ("POLITIS_WHITE", "CADENCE"):
        canon_facets = {"test_facet": "some_value", "discipline_under_test": discipline}
        canonical_json = _db.canonicalize_facets_json(canon_facets)
        bundle_hash = _db.hash_facets_json(canonical_json + discipline)  # unique per discipline
        _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

        # This must NOT raise ValueError — previously it would because the value
        # was absent from _VALID_FREEZE_DISCIPLINES.
        row_id = _db.insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name="test_facet",
            facet_value="some_value",
            freeze_discipline=discipline,
            justification=f"end-to-end test for {discipline}",
        )
        assert isinstance(row_id, int) and row_id > 0, (
            f"insert_spec_bundle_facet returned {row_id!r} for discipline={discipline!r}; "
            f"expected a positive integer rowid"
        )

        rows = _db.get_spec_facets_for_bundle(bundle_hash)
        assert len(rows) == 1
        assert rows[0]["freeze_discipline"] == discipline, (
            f"Persisted freeze_discipline {rows[0]['freeze_discipline']!r} != {discipline!r}; "
            f"the value was not stored correctly"
        )
