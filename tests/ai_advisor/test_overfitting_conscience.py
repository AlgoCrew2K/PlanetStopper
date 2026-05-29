"""
Sprint 3 — RED: Overfitting Conscience producer for ``advisors/overfitting_conscience.py``.

Tier 1 — fixture-based and structural unit tests. No network. No live DB. Runs every CI pass.

The module under test (``advisors/overfitting_conscience.py``) does NOT exist yet —
every test here is RED until the implementer cycle creates it. The binding
contract these tests pin:

    def compute_overfitting_conscience_observation(
        autotune_run: dict,
        ledger_rows: list[dict],
    ) -> dict:
        # Returns an AdvisorObservation dict with keys:
        #   advisor_role      str   'OVERFITTING_CONSCIENCE'
        #   subject_type      str   'autotune_run'
        #   subject_id        str   str(autotune_run['id'])
        #   verdict           str   in {'CLEAR', 'WATCH', 'BREACH'}
        #   raw_response      dict  structured indicator summary
        #   is_advisory_only  int   1 (always; the Advisor never moves money)
        #   spec_bundle_id    str   autotune_run['spec_bundle_id']

Phase-1 indicators (all computed in the pure function):
  I-1  S > 0: any BACKTEST_SELECTION row for the bundle in ledger_rows → WATCH or BREACH
  I-2  S/N_optuna ratio > 0.1: escalate to BREACH
  I-3  Operator drift (monotonic S growth across consecutive runs): WATCH

Wall integrity:
  The call-site in autotuner reads ledger via advisor_ro_query (never direct
  get_connection). A lint-style test asserts the advisors module does NOT import
  or call get_connection / get_ro_connection directly.

Mocking strategy
----------------
* ``database.insert_advisor_observation``  → patched at the call-site test.
  The pure function itself has no DB side-effects — it returns a dict.
* ``database.advisor_ro_query``            → patched at the call-site test.
* The math engine is NOT touched — this module does no risk math.
* No module-level mutable state; every fixture is function-scoped.

Fixture provenance
------------------
tests/fixtures/math/overfitting_conscience_indicators.json
  Hand-derived from the Sprint 3 dispatch brief. No producer-computed
  rates/dollars/percents. Verdict is an enum; ratio is derived from the
  fixture inputs at test time, never hardcoded.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import time
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture() -> dict:
    """Return the parsed overfitting_conscience_indicators fixture."""
    fixture_path = (
        pathlib.Path(__file__).parents[1]
        / "fixtures"
        / "math"
        / "overfitting_conscience_indicators.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _import_conscience():
    """Lazy import of the module under test.

    Deferred so collection-time ImportError surfaces as a test failure on the
    specific test that needs the module, not a conftest-level crash.
    """
    return importlib.import_module("advisors.overfitting_conscience")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conscience_fixture() -> dict:
    """Parsed fixture file (function-scoped, immutable)."""
    return _load_fixture()


@pytest.fixture
def honest_run(conscience_fixture) -> tuple[dict, list]:
    """NN1-honest scenario: no BACKTEST_SELECTION rows."""
    s = conscience_fixture["scenarios"]["honest_s0_clear"]
    return s["autotune_run"], s["ledger_rows"]


@pytest.fixture
def watch_run(conscience_fixture) -> tuple[dict, list]:
    """S > 0 but ratio <= 0.1 scenario (WATCH)."""
    s = conscience_fixture["scenarios"]["s_gt_0_low_ratio_watch"]
    return s["autotune_run"], s["ledger_rows"]


@pytest.fixture
def breach_run(conscience_fixture) -> tuple[dict, list]:
    """High-ratio scenario (BREACH)."""
    s = conscience_fixture["scenarios"]["high_ratio_breach"]
    return s["autotune_run"], s["ledger_rows"]


@pytest.fixture
def different_bundle_run(conscience_fixture) -> tuple[dict, list]:
    """Ledger row for a different bundle must be excluded."""
    s = conscience_fixture["scenarios"]["different_bundle_excluded"]
    return s["autotune_run"], s["ledger_rows"]


@pytest.fixture
def boundary_run(conscience_fixture) -> tuple[dict, list]:
    """S/N_optuna = 0.1 exactly — WATCH not BREACH (strict > threshold)."""
    s = conscience_fixture["scenarios"]["ratio_boundary_exactly_0_1_is_watch"]
    return s["autotune_run"], s["ledger_rows"]


# ===========================================================================
# Test 1 — Pure function contract: return dict structure and required keys.
# ===========================================================================

def test_pure_function_returns_a_dict(honest_run):
    """compute_overfitting_conscience_observation returns a dict on every path."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert isinstance(result, dict), (
        "compute_overfitting_conscience_observation must return a dict"
    )


def test_return_dict_has_all_required_keys(honest_run):
    """The returned dict must carry every required AdvisorObservation key."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    required = {
        "advisor_role",
        "subject_type",
        "subject_id",
        "verdict",
        "raw_response",
        "is_advisory_only",
        "spec_bundle_id",
    }
    missing = required - set(result.keys())
    assert not missing, (
        f"compute_overfitting_conscience_observation result is missing keys: {missing}"
    )


def test_advisor_role_is_overfitting_conscience(honest_run):
    """advisor_role must be exactly 'OVERFITTING_CONSCIENCE'."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["advisor_role"] == "OVERFITTING_CONSCIENCE", (
        f"advisor_role must be 'OVERFITTING_CONSCIENCE', got {result['advisor_role']!r}"
    )


def test_subject_type_is_autotune_run(honest_run):
    """subject_type must be 'autotune_run'."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["subject_type"] == "autotune_run", (
        f"subject_type must be 'autotune_run', got {result['subject_type']!r}"
    )


def test_subject_id_matches_run_id(honest_run):
    """subject_id must be str(autotune_run['id']) — identifies the specific run."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["subject_id"] == str(autotune_run["id"]), (
        f"subject_id must equal str(autotune_run['id'])={str(autotune_run['id'])!r}, "
        f"got {result['subject_id']!r}"
    )


def test_spec_bundle_id_comes_from_run(honest_run):
    """spec_bundle_id in the returned dict must equal autotune_run['spec_bundle_id']."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["spec_bundle_id"] == autotune_run["spec_bundle_id"], (
        f"spec_bundle_id must propagate from autotune_run; "
        f"expected {autotune_run['spec_bundle_id']!r}, got {result['spec_bundle_id']!r}"
    )


def test_is_advisory_only_is_always_1(honest_run, breach_run, watch_run):
    """is_advisory_only must be 1 on every path — the Advisor never moves money.

    The architectural invariant is unconditional: no verdict, no indicator state,
    no edge case may ever set is_advisory_only to anything other than 1.
    """
    mod = _import_conscience()
    for run_data, label in [
        (honest_run, "CLEAR path"),
        (watch_run, "WATCH path"),
        (breach_run, "BREACH path"),
    ]:
        autotune_run, ledger_rows = run_data
        result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
        assert result["is_advisory_only"] == 1, (
            f"is_advisory_only must be 1 on the {label} — the Advisor never moves money; "
            f"got {result['is_advisory_only']!r}"
        )


def test_verdict_is_in_allowed_enum(honest_run, watch_run, breach_run):
    """verdict must be one of {'CLEAR', 'WATCH', 'BREACH'} on every path."""
    mod = _import_conscience()
    valid_verdicts = {"CLEAR", "WATCH", "BREACH"}
    for run_data, label in [
        (honest_run, "honest/CLEAR"),
        (watch_run, "watch"),
        (breach_run, "breach"),
    ]:
        autotune_run, ledger_rows = run_data
        result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
        assert result["verdict"] in valid_verdicts, (
            f"{label}: verdict must be in {valid_verdicts!r}, got {result['verdict']!r}"
        )


def test_raw_response_is_a_dict(honest_run, watch_run, breach_run):
    """raw_response must be a dict (not a string, not None) on every path."""
    mod = _import_conscience()
    for run_data, label in [
        (honest_run, "honest/CLEAR"),
        (watch_run, "watch"),
        (breach_run, "breach"),
    ]:
        autotune_run, ledger_rows = run_data
        result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
        assert isinstance(result["raw_response"], dict), (
            f"{label}: raw_response must be a dict, got {type(result['raw_response']).__name__!r}"
        )


# ===========================================================================
# Test 2 — Indicator 1: S > 0 → WATCH or BREACH.
# ===========================================================================

def test_indicator_1_s_gt_0_produces_watch_or_breach(watch_run, breach_run):
    """When ledger_rows has BACKTEST_SELECTION rows for the bundle, verdict is
    WATCH or BREACH — never CLEAR.

    Indicator 1 fires on any S > 0. Indicator 2 (ratio > 0.1) may escalate to
    BREACH on top. Either way S > 0 must never result in CLEAR.
    """
    mod = _import_conscience()
    for run_data, label in [(watch_run, "low-ratio watch"), (breach_run, "high-ratio breach")]:
        autotune_run, ledger_rows = run_data
        result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
        assert result["verdict"] in {"WATCH", "BREACH"}, (
            f"{label}: S > 0 must produce WATCH or BREACH, never CLEAR; "
            f"got {result['verdict']!r}"
        )


def test_indicator_1_raw_response_includes_s_count(watch_run):
    """raw_response must include the BACKTEST_SELECTION count (S) when S > 0."""
    mod = _import_conscience()
    autotune_run, ledger_rows = watch_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    raw = result["raw_response"]
    # S count must appear under some key — exact key is implementer's choice,
    # but the count must be derivable from raw_response.
    has_s = (
        "s_count" in raw
        or "backtest_selection_count" in raw
        or "s" in raw
    )
    assert has_s, (
        "raw_response must include the BACKTEST_SELECTION S count when S > 0; "
        f"got raw_response keys: {sorted(raw.keys())}"
    )


def test_indicator_1_raw_response_includes_facet_names(watch_run):
    """raw_response must include BACKTEST_SELECTION facet names when S > 0."""
    mod = _import_conscience()
    autotune_run, ledger_rows = watch_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    raw = result["raw_response"]
    # Facet names must be present in some form.
    has_facets = (
        "facet_names" in raw
        or "facets" in raw
        or "backtest_selection_facets" in raw
    )
    assert has_facets, (
        "raw_response must include BACKTEST_SELECTION facet names when S > 0; "
        f"got raw_response keys: {sorted(raw.keys())}"
    )


# ===========================================================================
# Test 3 — Indicator 2: S/N_optuna ratio > 0.1 → BREACH.
# ===========================================================================

def test_indicator_2_high_ratio_escalates_to_breach(breach_run, conscience_fixture):
    """When S/N_optuna ratio > 0.1, verdict must be BREACH.

    The ratio is derived from the fixture inputs (s_count and n_effective) so
    no producer-computed literal is hardcoded here.
    """
    mod = _import_conscience()
    autotune_run, ledger_rows = breach_run
    # Verify our fixture does indeed have a ratio > 0.1 (guard the fixture).
    s = autotune_run["s_count"]
    n_effective = autotune_run["n_effective"]
    n_optuna = n_effective - s  # n_optuna = n_effective - S
    assert n_optuna > 0, "fixture must have n_optuna > 0"
    ratio = s / n_optuna
    assert ratio > 0.1, (
        f"breach fixture must have S/N_optuna > 0.1; fixture has ratio={ratio:.4f}"
    )

    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["verdict"] == "BREACH", (
        f"S/N_optuna ratio {ratio:.4f} > 0.1 must produce BREACH; "
        f"got {result['verdict']!r}"
    )


def test_indicator_2_raw_response_includes_ratio_and_threshold(breach_run):
    """raw_response must carry the computed ratio and the threshold when BREACH fires."""
    mod = _import_conscience()
    autotune_run, ledger_rows = breach_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    raw = result["raw_response"]
    has_ratio = "ratio" in raw or "s_n_optuna_ratio" in raw
    has_threshold = "threshold" in raw or "breach_threshold" in raw
    assert has_ratio, (
        "raw_response must include the S/N_optuna ratio when BREACH fires; "
        f"got raw_response keys: {sorted(raw.keys())}"
    )
    assert has_threshold, (
        "raw_response must include the BREACH threshold when BREACH fires; "
        f"got raw_response keys: {sorted(raw.keys())}"
    )


def test_indicator_2_boundary_0_1_exact_is_not_breach(boundary_run):
    """S/N_optuna = 0.1 exactly must NOT trigger BREACH — the condition is strict (>).

    A S > 0 scenario with ratio exactly at threshold should be WATCH (I-1 fires)
    but the BREACH threshold is exclusive: ratio > 0.1, not >=.
    """
    mod = _import_conscience()
    autotune_run, ledger_rows = boundary_run
    s = autotune_run["s_count"]
    n_effective = autotune_run["n_effective"]
    n_optuna = n_effective - s
    assert n_optuna > 0
    ratio = s / n_optuna
    # Verify fixture is at exactly 0.1.
    assert abs(ratio - 0.1) < 1e-9, f"boundary fixture must have ratio=0.1, got {ratio}"

    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["verdict"] != "BREACH", (
        f"ratio=0.1 exactly must NOT produce BREACH (threshold is strict >); "
        f"got {result['verdict']!r}"
    )
    # S > 0, so I-1 fires → must be WATCH.
    assert result["verdict"] == "WATCH", (
        f"ratio=0.1 with S={s} must produce WATCH (I-1: S>0), got {result['verdict']!r}"
    )


# ===========================================================================
# Test 4 — Honest case S=0: verdict is CLEAR.
# ===========================================================================

def test_honest_s0_verdict_is_clear(honest_run):
    """S=0 (no BACKTEST_SELECTION rows) must produce CLEAR."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["verdict"] == "CLEAR", (
        f"empty ledger (S=0) must produce CLEAR; got {result['verdict']!r}"
    )


def test_honest_s0_raw_response_notes_n_effective_equals_n_optuna(honest_run):
    """raw_response must note S=0 and that N_effective equals N_optuna."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    raw = result["raw_response"]
    raw_str = json.dumps(raw).lower()
    # Must indicate absence of BACKTEST_SELECTION facets in some form.
    has_clear_note = (
        "no backtest_selection" in raw_str
        or "s=0" in raw_str
        or "s_count" in raw and raw.get("s_count") == 0
        or "backtest_selection_count" in raw and raw.get("backtest_selection_count") == 0
    )
    assert has_clear_note, (
        "raw_response must note that S=0 and no BACKTEST_SELECTION facets are present; "
        f"got raw_response: {raw!r}"
    )


def test_empty_ledger_produces_clear():
    """Hostile edge: entirely empty ledger_rows (no rows at all) → CLEAR."""
    mod = _import_conscience()
    autotune_run = {
        "id": 99,
        "symphony_id": "sym-no-ledger",
        "run_timestamp": "2026-05-23T20:00:00Z",
        "spec_bundle_id": "bundle-empty-ledger",
        "n_effective": 500,
        "s_count": 0,
    }
    result = mod.compute_overfitting_conscience_observation(autotune_run, [])
    assert result["verdict"] == "CLEAR", (
        f"empty ledger must produce CLEAR; got {result['verdict']!r}"
    )
    assert result["is_advisory_only"] == 1


# ===========================================================================
# Test 5 — Cross-bundle isolation: ledger rows for a different spec_bundle_id
#           must be excluded from S.
# ===========================================================================

def test_different_bundle_ledger_rows_excluded(different_bundle_run):
    """A BACKTEST_SELECTION ledger row pointing at a different spec_bundle_id
    must not contribute to S for this run. Result is CLEAR.

    This is the single most critical isolation invariant: one autotune run must
    not pick up another run's BACKTEST_SELECTION debt.
    """
    mod = _import_conscience()
    autotune_run, ledger_rows = different_bundle_run
    # Verify our fixture: ledger_rows points at a different bundle.
    for row in ledger_rows:
        assert row["spec_bundle_id"] != autotune_run["spec_bundle_id"], (
            "fixture sanity: ledger row must point at a different bundle"
        )

    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    assert result["verdict"] == "CLEAR", (
        "ledger rows for a different spec_bundle_id must not affect this run's verdict; "
        f"got {result['verdict']!r}"
    )


def test_only_rows_matching_spec_bundle_id_are_counted():
    """Adversarial: mix of matching and non-matching rows. Only the matching row
    contributes to S. The non-matching row (which would alone breach) is excluded.
    """
    mod = _import_conscience()
    autotune_run = {
        "id": 7,
        "symphony_id": "sym-mixed-bundles",
        "run_timestamp": "2026-05-23T20:01:00Z",
        "spec_bundle_id": "bundle-target-007",
        "n_effective": 502,
        "s_count": 2,
    }
    ledger_rows = [
        # This row MATCHES — contributes S=2.
        {
            "evidence_source": "BACKTEST_SELECTION",
            "n_configs_searched": 2,
            "touched_frozen_eval": 0,
            "spec_bundle_id": "bundle-target-007",
            "facet_name": "matching-facet",
        },
        # This row does NOT match — must be excluded (would push ratio over 0.1 alone).
        {
            "evidence_source": "BACKTEST_SELECTION",
            "n_configs_searched": 9999,
            "touched_frozen_eval": 0,
            "spec_bundle_id": "bundle-OTHER-should-be-excluded",
            "facet_name": "other-facet",
        },
    ]
    result = mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    # S=2, n_optuna=500, ratio=0.004 → WATCH (S > 0), not BREACH.
    assert result["verdict"] == "WATCH", (
        "only the matching-bundle row contributes to S; the non-matching row (n_configs=9999) "
        "must be excluded. Expected WATCH (S=2, ratio=0.004), "
        f"got {result['verdict']!r}"
    )


# ===========================================================================
# Test 6 — Wall integrity: producer uses advisor_ro_query, never direct
#           get_connection / get_ro_connection.
# ===========================================================================

def test_advisors_module_does_not_import_get_connection_directly():
    """The advisors/overfitting_conscience.py module must NOT call
    get_connection() or get_ro_connection() directly.

    Direct connection calls bypass the advisor_ro_query fold_role guard and
    the wall-breach tripwire — a structural side door prohibited by architecture
    constraint (database.py:1510-1513).

    This test inspects the module source as text (no import side effects)
    to enforce the rule at CI time.
    """
    source_path = (
        pathlib.Path(__file__).parents[2]
        / "advisors"
        / "overfitting_conscience.py"
    )
    assert source_path.exists(), (
        f"advisors/overfitting_conscience.py not found at {source_path} — "
        "the implementer must create it before this test can pass"
    )
    source = source_path.read_text(encoding="utf-8")
    # Neither direct connection function may appear outside a comment/docstring.
    # We strip comments and strings to be precise, but a simple substring check
    # on the non-comment source is sufficient here — the functions have unique names.
    non_comment_lines = [
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    ]
    non_comment_source = "\n".join(non_comment_lines)
    assert "get_connection()" not in non_comment_source, (
        "advisors/overfitting_conscience.py must NOT call get_connection() directly — "
        "use database.advisor_ro_query() for all DB reads (wall integrity requirement)"
    )
    assert "get_ro_connection()" not in non_comment_source, (
        "advisors/overfitting_conscience.py must NOT call get_ro_connection() directly — "
        "use database.advisor_ro_query() for all DB reads (wall integrity requirement)"
    )


def test_advisors_module_uses_advisor_ro_query():
    """The advisors/overfitting_conscience.py source must reference advisor_ro_query.

    Pairing: the previous test ensures direct connections are absent;
    this test ensures the approved read path IS present.
    """
    source_path = (
        pathlib.Path(__file__).parents[2]
        / "advisors"
        / "overfitting_conscience.py"
    )
    assert source_path.exists(), (
        "advisors/overfitting_conscience.py not found — implementer must create it"
    )
    source = source_path.read_text(encoding="utf-8")
    assert "advisor_ro_query" in source, (
        "advisors/overfitting_conscience.py must use database.advisor_ro_query() "
        "as the sole DB read path — it was not found in the module source"
    )


# ===========================================================================
# Test 7 — Read-only contract: producer writes ONLY via insert_advisor_observation.
#           Must not modify spec_bundles, researcher_dof_ledger, or autotune_runs.
# ===========================================================================

def test_producer_writes_only_via_insert_advisor_observation(honest_run):
    """The call-site integration: after computing the observation dict, the
    call site must persist it via database.insert_advisor_observation.

    This test patches insert_advisor_observation and asserts it is called with
    the exact keys produced by compute_overfitting_conscience_observation.

    We also assert that insert_dof_ledger_row, save_autotune_run, and
    insert_spec_bundle are NOT called — the producer is read+observe only.
    """
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run

    # The integration entry point that calls compute + insert.
    # If the module exposes a `produce_and_persist` / `run_conscience` function,
    # call that. Otherwise the test verifies the contract via the call-site
    # in autotuner that is documented to call both in sequence.
    assert hasattr(mod, "run_overfitting_conscience"), (
        "advisors/overfitting_conscience.py must expose run_overfitting_conscience() "
        "as the integration entry point that computes + persists the observation"
    )

    with (
        patch("database.insert_advisor_observation", return_value=42) as mock_insert,
        patch("database.insert_dof_ledger_row") as mock_dof,
        patch("database.save_autotune_run") as mock_save,
        patch("database.insert_spec_bundle") as mock_bundle,
    ):
        obs_id = mod.run_overfitting_conscience(autotune_run, ledger_rows)

    mock_insert.assert_called_once()
    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs.get("advisor_role") == "OVERFITTING_CONSCIENCE", (
        "insert_advisor_observation must be called with advisor_role='OVERFITTING_CONSCIENCE'"
    )
    assert call_kwargs.get("subject_type") == "autotune_run"
    assert call_kwargs.get("is_advisory_only", None) in (None, 1), (
        "insert_advisor_observation must not override is_advisory_only to anything "
        "other than 1 (the DB enforces 1 unconditionally, but the call must not pass 0)"
    )

    mock_dof.assert_not_called(), "producer must NOT write to researcher_dof_ledger"
    mock_save.assert_not_called(), "producer must NOT call save_autotune_run"
    mock_bundle.assert_not_called(), "producer must NOT write to spec_bundles"


def test_run_overfitting_conscience_returns_row_id(honest_run):
    """run_overfitting_conscience returns the row id from insert_advisor_observation."""
    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run
    sentinel_id = 77

    with patch("database.insert_advisor_observation", return_value=sentinel_id):
        result = mod.run_overfitting_conscience(autotune_run, ledger_rows)

    assert result == sentinel_id, (
        f"run_overfitting_conscience must return the row id from "
        f"insert_advisor_observation; expected {sentinel_id}, got {result!r}"
    )


# ===========================================================================
# Test 8 — Phase-2 deferral guard: only OVERFITTING_CONSCIENCE is written.
# ===========================================================================

def test_only_overfitting_conscience_advisor_role_written(honest_run, watch_run, breach_run):
    """The producer must write ONLY the OVERFITTING_CONSCIENCE advisor_role.

    Phase-2 roles (REGIME_NARRATOR etc.) are deferred — no other advisor_role
    may be written by this module, on any verdict path.
    """
    mod = _import_conscience()
    for run_data, label in [
        (honest_run, "CLEAR"),
        (watch_run, "WATCH"),
        (breach_run, "BREACH"),
    ]:
        autotune_run, ledger_rows = run_data
        written_roles = []

        def capture_role(**kwargs):
            written_roles.append(kwargs.get("advisor_role"))
            return 1

        with patch("database.insert_advisor_observation", side_effect=capture_role):
            mod.run_overfitting_conscience(autotune_run, ledger_rows)

        assert written_roles == ["OVERFITTING_CONSCIENCE"], (
            f"{label} path: expected exactly one 'OVERFITTING_CONSCIENCE' write; "
            f"got {written_roles!r}. Phase-2 roles must not be written by this module."
        )


# ===========================================================================
# Test 9 — Performance: producer execution under 100ms.
# ===========================================================================

def test_producer_executes_under_100ms(honest_run):
    """compute_overfitting_conscience_observation must complete in under 100ms.

    Post-autotune-run, this is a non-blocking call on the execution path.
    The 100ms budget is generous for an in-process pure function over a small
    ledger — a slow implementation indicates an unexpected blocking call.
    """
    # 100ms tolerance includes two orders of magnitude of safety margin for
    # pure Python iteration over a list of ledger rows without DB I/O.
    BUDGET_SECONDS = 0.1

    mod = _import_conscience()
    autotune_run, ledger_rows = honest_run

    start = time.monotonic()
    mod.compute_overfitting_conscience_observation(autotune_run, ledger_rows)
    elapsed = time.monotonic() - start

    assert elapsed < BUDGET_SECONDS, (
        f"compute_overfitting_conscience_observation took {elapsed:.4f}s — "
        f"exceeds the {BUDGET_SECONDS}s budget. The function is a pure in-process "
        "computation; no blocking I/O is permitted."
    )


# ===========================================================================
# Test 10 — Indicator 3: operator drift — monotonically growing S across
#            consecutive runs on same symphony → WATCH.
# ===========================================================================

def test_indicator_3_monotonic_s_growth_produces_watch():
    """Indicator 3: if S grows monotonically across the last N consecutive runs
    on the same symphony, the verdict is WATCH (or escalated to BREACH if ratio
    also fires).

    We pass a `prior_runs` list to the function to enable cross-run trend detection.
    The function signature extension: compute_overfitting_conscience_observation(
        autotune_run, ledger_rows, prior_runs=None).
    """
    mod = _import_conscience()

    # Three consecutive runs with strictly increasing s_count.
    prior_runs = [
        {"id": 10, "symphony_id": "sym-drift", "s_count": 1, "n_effective": 501,
         "spec_bundle_id": "bundle-drift", "run_timestamp": "2026-05-21T16:00:00Z"},
        {"id": 11, "symphony_id": "sym-drift", "s_count": 3, "n_effective": 503,
         "spec_bundle_id": "bundle-drift", "run_timestamp": "2026-05-22T16:00:00Z"},
    ]
    current_run = {
        "id": 12,
        "symphony_id": "sym-drift",
        "run_timestamp": "2026-05-23T16:00:00Z",
        "spec_bundle_id": "bundle-drift",
        "n_effective": 505,
        "s_count": 5,  # 1 → 3 → 5: strictly monotonically increasing
    }
    ledger_rows = [
        {
            "evidence_source": "BACKTEST_SELECTION",
            "n_configs_searched": 5,
            "touched_frozen_eval": 0,
            "spec_bundle_id": "bundle-drift",
            "facet_name": "drift-facet",
        }
    ]

    result = mod.compute_overfitting_conscience_observation(
        current_run, ledger_rows, prior_runs=prior_runs
    )
    # S > 0 fires I-1 → at minimum WATCH. Monotonic growth fires I-3 → WATCH.
    assert result["verdict"] in {"WATCH", "BREACH"}, (
        "monotonically growing S across consecutive runs must produce WATCH or BREACH; "
        f"got {result['verdict']!r}"
    )
    # The raw_response must mention the drift trend.
    raw_str = json.dumps(result["raw_response"]).lower()
    has_drift = (
        "drift" in raw_str
        or "monoton" in raw_str
        or "trend" in raw_str
        or "growing" in raw_str
    )
    assert has_drift, (
        "raw_response must mention operator drift when I-3 fires; "
        f"got raw_response: {result['raw_response']!r}"
    )


def test_indicator_3_non_monotonic_s_does_not_trigger_drift():
    """Non-monotonic S (e.g. 5 → 3 → 4) must NOT trigger the drift indicator."""
    mod = _import_conscience()

    prior_runs = [
        {"id": 20, "symphony_id": "sym-no-drift", "s_count": 5, "n_effective": 505,
         "spec_bundle_id": "bundle-nodrift", "run_timestamp": "2026-05-21T16:00:00Z"},
        {"id": 21, "symphony_id": "sym-no-drift", "s_count": 3, "n_effective": 503,
         "spec_bundle_id": "bundle-nodrift", "run_timestamp": "2026-05-22T16:00:00Z"},
    ]
    current_run = {
        "id": 22,
        "symphony_id": "sym-no-drift",
        "run_timestamp": "2026-05-23T16:00:00Z",
        "spec_bundle_id": "bundle-nodrift",
        "n_effective": 504,
        "s_count": 4,  # 5 → 3 → 4: NOT monotonically increasing
    }
    ledger_rows = [
        {
            "evidence_source": "BACKTEST_SELECTION",
            "n_configs_searched": 4,
            "touched_frozen_eval": 0,
            "spec_bundle_id": "bundle-nodrift",
            "facet_name": "some-facet",
        }
    ]

    result = mod.compute_overfitting_conscience_observation(
        current_run, ledger_rows, prior_runs=prior_runs
    )
    raw_str = json.dumps(result["raw_response"]).lower()
    # Drift indicator should NOT have fired (even though S > 0 fires I-1 → WATCH).
    has_drift = (
        "drift" in raw_str and "monoton" in raw_str
    )
    assert not has_drift, (
        "non-monotonic S must not trigger I-3 drift; raw_response incorrectly "
        f"mentions drift for non-monotonic sequence 5→3→4: {result['raw_response']!r}"
    )


def test_indicator_3_prior_runs_from_different_symphony_ignored():
    """prior_runs entries for a different symphony must be ignored for drift detection."""
    mod = _import_conscience()

    prior_runs = [
        # These belong to a DIFFERENT symphony — must be excluded from trend.
        {"id": 30, "symphony_id": "OTHER-symphony", "s_count": 1, "n_effective": 501,
         "spec_bundle_id": "bundle-other", "run_timestamp": "2026-05-21T16:00:00Z"},
        {"id": 31, "symphony_id": "OTHER-symphony", "s_count": 3, "n_effective": 503,
         "spec_bundle_id": "bundle-other", "run_timestamp": "2026-05-22T16:00:00Z"},
    ]
    current_run = {
        "id": 32,
        "symphony_id": "target-symphony",
        "run_timestamp": "2026-05-23T16:00:00Z",
        "spec_bundle_id": "bundle-target-032",
        "n_effective": 502,
        "s_count": 2,
    }
    ledger_rows = [
        {
            "evidence_source": "BACKTEST_SELECTION",
            "n_configs_searched": 2,
            "touched_frozen_eval": 0,
            "spec_bundle_id": "bundle-target-032",
            "facet_name": "facet-target",
        }
    ]

    result = mod.compute_overfitting_conscience_observation(
        current_run, ledger_rows, prior_runs=prior_runs
    )
    # S > 0 → WATCH from I-1. But no same-symphony trend data → I-3 does not fire.
    # raw_response must not claim a drift trend from cross-symphony data.
    raw_str = json.dumps(result["raw_response"]).lower()
    # The presence of "drift" alone is fine only if it's not attributed to a trend;
    # the simplest invariant: I-3 should not have fired, so no "trend" claim.
    has_cross_symphony_trend = "trend" in raw_str and "drift" in raw_str and "monoton" in raw_str
    assert not has_cross_symphony_trend, (
        "drift trend must not be computed from prior_runs of a different symphony; "
        f"raw_response: {result['raw_response']!r}"
    )


# ===========================================================================
# Test 11 — Hostile edge: missing autotune_runs columns (pre-020 DB).
# ===========================================================================

def test_missing_s_count_column_fails_clearly():
    """When autotune_run dict is missing 's_count' (pre-020 DB schema), the
    function must raise a clear error — never silently no-op.

    A silent no-op would produce a false CLEAR verdict, masking a real
    overfitting signal. The function must blow up loudly so the operator
    knows the DB migration is missing.
    """
    mod = _import_conscience()
    # Omit s_count to simulate a pre-020 row.
    autotune_run_without_s_count = {
        "id": 50,
        "symphony_id": "sym-pre020",
        "run_timestamp": "2026-05-23T16:00:00Z",
        "spec_bundle_id": "bundle-pre020",
        "n_effective": 500,
        # s_count intentionally omitted
    }
    ledger_rows: list = []

    with pytest.raises((KeyError, ValueError, TypeError)):
        mod.compute_overfitting_conscience_observation(
            autotune_run_without_s_count, ledger_rows
        )


def test_missing_n_effective_column_fails_clearly():
    """When autotune_run dict is missing 'n_effective' (pre-020 DB), the function
    must raise — not silently return a verdict computed from incomplete data.
    """
    mod = _import_conscience()
    autotune_run_without_n_effective = {
        "id": 51,
        "symphony_id": "sym-pre020-neff",
        "run_timestamp": "2026-05-23T16:00:00Z",
        "spec_bundle_id": "bundle-pre020-neff",
        # n_effective intentionally omitted
        "s_count": 0,
    }
    ledger_rows: list = []

    with pytest.raises((KeyError, ValueError, TypeError)):
        mod.compute_overfitting_conscience_observation(
            autotune_run_without_n_effective, ledger_rows
        )


# ===========================================================================
# Test 12 — Call-site integration: after save_autotune_run, run_conscience fires.
#
# The autotuner must call run_overfitting_conscience after save_autotune_run.
# This test patches autotuner's call-site without running a full Optuna study.
# ===========================================================================

def test_autotuner_calls_run_overfitting_conscience_after_save_autotune_run():
    """run_overfitting_conscience must be called from the autotuner's
    post-save_autotune_run path.

    We inspect the autotuner source as text (no import) to assert the call
    exists in sequence after save_autotune_run — following the same source-
    inspection precedent as test_optuna_search_space_keys_match_autotuner_source
    (tests/ai_advisor/test_ai_advisor_safety.py:996).

    No live Optuna run is needed — this is a structural/sourcing assertion.
    """
    autotuner_path = pathlib.Path(__file__).parents[2] / "autotuner.py"
    assert autotuner_path.exists(), "autotuner.py not found"
    source = autotuner_path.read_text(encoding="utf-8")

    assert "run_overfitting_conscience" in source, (
        "autotuner.py must call run_overfitting_conscience after save_autotune_run — "
        "the function name was not found in the autotuner source. "
        "The implementer must wire the call-site."
    )

    # Assert call-site ordering: run_overfitting_conscience appears AFTER
    # save_autotune_run in the source text. This is a proxy for correct
    # sequencing; a precise structural check.
    save_idx = source.rfind("save_autotune_run")
    conscience_idx = source.rfind("run_overfitting_conscience")
    assert conscience_idx > save_idx, (
        "run_overfitting_conscience must appear AFTER the last save_autotune_run call "
        "in autotuner.py — the producer fires post-save, not before"
    )
