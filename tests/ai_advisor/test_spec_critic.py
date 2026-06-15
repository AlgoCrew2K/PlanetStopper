"""
Sprint 3 — RED: Spec Critic producer for ``advisors/spec_critic.py``.

Tier 1 — fixture-based and structural unit tests. No network. No live DB. Runs every CI pass.

The module under test (``advisors/spec_critic.py``) does NOT exist yet —
every test here is RED until the implementer cycle creates it. The binding
contract these tests pin:

    def compute_spec_critic_observation(
        spec_bundle_id: str,
        spec_facets_rows: list[dict],
    ) -> dict:
        # Returns an AdvisorObservation dict with keys:
        #   advisor_role      str   'SPEC_CRITIC'
        #   subject_type      str   'spec_bundle'
        #   subject_id        str   spec_bundle_id (the bundle hash)
        #   verdict           str   in {'CLEAR', 'WATCH', 'BREACH'}
        #   raw_response      dict  structured indicator summary (JSON-serialisable)
        #   is_advisory_only  int   1 (always; the Advisor never moves money)

Phase-1 indicators (all computed in the pure function):
  I-1  Missing required facet: gamma / utility_family / wealth_argument absent → BREACH.
  I-2  Unrecognized freeze_discipline: any facet discipline not in
       NN1_HONEST_DISCIPLINES ∪ {BACKTEST_SELECTION} → BREACH (defense-in-depth).
  I-3  Stale frozen_at: any facet frozen_at > 90 days ago → WATCH.
       Inclusive boundary: exactly 90 days → WATCH (documented).
  I-4  Phase-2 facet seeded prematurely: 'lambda' or 'hysteresis-threshold'
       in spec_facets_rows → BREACH ('phase scope leak').

Hostile edges:
  - Empty spec_facets_rows → BREACH on I-1 (all three facets absent).
  - frozen_at exactly 90 days → WATCH (inclusive boundary documented).
  - bundle_hash not in spec_bundles → error path (handled, does not silently no-op).

Wall integrity:
  The call-site reads spec_facets via advisor_ro_query. A lint-style test asserts
  the advisors.spec_critic module does NOT import or call get_connection /
  get_ro_connection directly.

Read-only contract:
  compute_spec_critic_observation is a pure function — no DB side-effects.
  The integration entry point (run_spec_critic) writes ONLY to advisor_observations
  via insert_advisor_observation.

Call-site integration:
  Fires after validate_nn1_compliance returns + before the autotuner trial loop.
  Non-blocking; expected under 100ms. Phase-2 deferral: does not compute
  Regime Narrator observations.

Mocking strategy
----------------
* ``database.insert_advisor_observation``  → patched at the call-site test.
  The pure function itself has no DB side-effects — it returns a dict.
* ``database.advisor_ro_query``            → patched at the call-site test.
* The math engine is NOT touched — this module does no risk math.
* No module-level mutable state; every fixture is function-scoped.

Fixture provenance
------------------
tests/fixtures/math/spec_critic_indicators.json
  Hand-derived from the Sprint 3 dispatch brief. No producer-computed
  rates/dollars/percents. Verdict is an enum; frozen_at staleness is derived
  from the fixture-described delta relative to a known reference timestamp,
  never hardcoded.
"""

from __future__ import annotations

import importlib
import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1] / "fixtures" / "math" / "spec_critic_indicators.json"
)


def _load_fixture() -> dict:
    """Return the parsed spec_critic_indicators fixture."""
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _import_spec_critic():
    """Lazy import of the module under test.

    Deferred so collection-time ImportError surfaces as a test failure on the
    specific test that needs the module, not a conftest-level crash.
    """
    return importlib.import_module("advisors.spec_critic")


# Reference date used to compute fresh / stale frozen_at values in tests.
# Using a fixed past date keeps tests deterministic regardless of wall-clock.
_REFERENCE_NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _fresh_frozen_at() -> str:
    """Return an ISO-8601 frozen_at timestamp 10 days before reference — clearly fresh."""
    return (_REFERENCE_NOW - timedelta(days=10)).isoformat()


def _stale_frozen_at() -> str:
    """Return an ISO-8601 frozen_at timestamp 91 days before reference — clearly stale."""
    return (_REFERENCE_NOW - timedelta(days=91)).isoformat()


def _exactly_90d_frozen_at() -> str:
    """Return an ISO-8601 frozen_at timestamp exactly 90 days before reference."""
    return (_REFERENCE_NOW - timedelta(days=90)).isoformat()


def _hydrate_facet_rows(rows: list[dict]) -> list[dict]:
    """Replace frozen_at sentinel strings with real datetimes derived from _REFERENCE_NOW.

    The fixture uses sentinel values (__FRESH__ / __STALE__ / __EXACTLY_90D__)
    so the expected behaviour is human-readable without coupling to a wall-clock.
    """
    replacements = {
        "__FRESH__": _fresh_frozen_at(),
        "__STALE__": _stale_frozen_at(),
        "__EXACTLY_90D__": _exactly_90d_frozen_at(),
    }
    hydrated = []
    for row in rows:
        r = dict(row)
        if "frozen_at" in r and r["frozen_at"] in replacements:
            r["frozen_at"] = replacements[r["frozen_at"]]
        hydrated.append(r)
    return hydrated


def _scenario(name: str) -> tuple[str, list[dict]]:
    """Return (spec_bundle_id, hydrated_spec_facets_rows) for a named scenario."""
    fixture = _load_fixture()
    s = fixture["scenarios"][name]
    return s["spec_bundle_id"], _hydrate_facet_rows(s["spec_facets_rows"])


# ---------------------------------------------------------------------------
# Fixture-level coherence test
# ---------------------------------------------------------------------------


def test_spec_critic_fixture_is_structurally_complete():
    """Fixture must be loadable and contain all required top-level and scenario keys.

    Fails if the fixture file is missing or malformed. Asserts shape / presence
    only — no producer-computed values.
    """
    fixture = _load_fixture()

    required_top_level = {
        "phase1_required_facets",
        "canonical_phase2_facets",
        "stale_threshold_days",
        "valid_verdicts",
        "scenarios",
    }
    missing_keys = required_top_level - set(fixture.keys())
    assert not missing_keys, f"fixture missing top-level keys: {missing_keys}"

    assert fixture["stale_threshold_days"] == 90, (
        "stale_threshold_days must be 90 — dispatch brief contract"
    )
    assert set(fixture["valid_verdicts"]) == {"CLEAR", "WATCH", "BREACH"}, (
        "valid_verdicts must be exactly {CLEAR, WATCH, BREACH}"
    )
    assert set(fixture["phase1_required_facets"]) == {
        "gamma",
        "utility_family",
        "wealth_argument",
    }, "phase1_required_facets must be exactly the three canonical Phase-1 facets"

    required_scenarios = {
        "honest_complete_fresh_clear",
        "missing_gamma_breach",
        "empty_facets_breach",
        "unrecognized_discipline_breach",
        "stale_frozen_at_watch",
        "frozen_at_exactly_90_days_watch",
        "phase2_facet_lambda_breach",
        "phase2_facet_hysteresis_breach",
    }
    missing_scenarios = required_scenarios - set(fixture["scenarios"].keys())
    assert not missing_scenarios, f"fixture missing scenario keys: {missing_scenarios}"


# ---------------------------------------------------------------------------
# T1 — Pure-function contract: module importable and function exists
# ---------------------------------------------------------------------------


def test_spec_critic_module_is_importable():
    """advisors/spec_critic.py must be importable once the implementer creates it.

    This will fail until the file exists — that is its purpose as a RED test.
    """
    try:
        mod = _import_spec_critic()
    except ImportError as exc:
        pytest.fail(
            f"advisors.spec_critic is not importable: {exc}. "
            "The implementer must create advisors/spec_critic.py."
        )
    assert mod is not None


def test_compute_spec_critic_observation_exists():
    """advisors.spec_critic must expose compute_spec_critic_observation."""
    mod = _import_spec_critic()
    assert hasattr(mod, "compute_spec_critic_observation"), (
        "advisors.spec_critic.compute_spec_critic_observation not found. "
        "The function must exist per the dispatch brief contract."
    )
    import inspect

    assert callable(mod.compute_spec_critic_observation), (
        "compute_spec_critic_observation must be callable"
    )


def test_run_spec_critic_exists():
    """advisors.spec_critic must expose run_spec_critic (integration entry point)."""
    mod = _import_spec_critic()
    assert hasattr(mod, "run_spec_critic"), (
        "advisors.spec_critic.run_spec_critic not found. "
        "The integration entry point must exist per the dispatch brief contract."
    )


# ---------------------------------------------------------------------------
# T2 — Return dict structure on the honest path
# ---------------------------------------------------------------------------


def test_pure_function_returns_dict_on_honest_path():
    """compute_spec_critic_observation must return a dict on the CLEAR path."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert isinstance(result, dict), (
        f"compute_spec_critic_observation must return a dict; got {type(result).__name__!r}"
    )


def test_return_dict_has_all_required_keys():
    """The returned dict must carry every required AdvisorObservation key."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    required = {
        "advisor_role",
        "subject_type",
        "subject_id",
        "verdict",
        "raw_response",
        "is_advisory_only",
    }
    missing = required - set(result.keys())
    assert not missing, f"compute_spec_critic_observation result missing keys: {missing}"


def test_advisor_role_is_spec_critic():
    """advisor_role must be exactly 'SPEC_CRITIC'."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["advisor_role"] == "SPEC_CRITIC", (
        f"advisor_role must be 'SPEC_CRITIC', got {result['advisor_role']!r}"
    )


def test_subject_type_is_spec_bundle():
    """subject_type must be 'spec_bundle'."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["subject_type"] == "spec_bundle", (
        f"subject_type must be 'spec_bundle', got {result['subject_type']!r}"
    )


def test_subject_id_equals_bundle_hash():
    """subject_id must be the bundle hash (spec_bundle_id argument)."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["subject_id"] == bundle_id, (
        f"subject_id must equal the spec_bundle_id argument {bundle_id!r}; "
        f"got {result['subject_id']!r}"
    )


def test_is_advisory_only_is_1():
    """is_advisory_only must always be 1 — the Spec Critic never moves money."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["is_advisory_only"] == 1, (
        f"is_advisory_only must be 1, got {result['is_advisory_only']!r}"
    )


def test_verdict_is_one_of_valid_set():
    """verdict must be one of CLEAR / WATCH / BREACH on every path."""
    mod = _import_spec_critic()
    valid_verdicts = {"CLEAR", "WATCH", "BREACH"}
    for scenario_name in (
        "honest_complete_fresh_clear",
        "missing_gamma_breach",
        "stale_frozen_at_watch",
        "phase2_facet_lambda_breach",
    ):
        bundle_id, facets = _scenario(scenario_name)
        result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
        assert result["verdict"] in valid_verdicts, (
            f"scenario {scenario_name!r}: verdict {result['verdict']!r} not in {valid_verdicts}"
        )


def test_raw_response_is_dict():
    """raw_response must be a dict (JSON-serialisable) on every path."""
    mod = _import_spec_critic()
    for scenario_name in (
        "honest_complete_fresh_clear",
        "missing_gamma_breach",
        "empty_facets_breach",
        "stale_frozen_at_watch",
        "phase2_facet_lambda_breach",
    ):
        bundle_id, facets = _scenario(scenario_name)
        result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
        assert isinstance(result["raw_response"], dict), (
            f"scenario {scenario_name!r}: raw_response must be a dict; "
            f"got {type(result['raw_response']).__name__!r}"
        )
        # Verify JSON-serialisable — a non-serialisable raw_response would break
        # insert_advisor_observation's JSON dump.
        try:
            json.dumps(result["raw_response"])
        except (TypeError, ValueError) as exc:
            pytest.fail(f"scenario {scenario_name!r}: raw_response is not JSON-serialisable: {exc}")


# ---------------------------------------------------------------------------
# T3 — Indicator 1: honest complete bundle returns CLEAR
# ---------------------------------------------------------------------------


def test_honest_complete_fresh_bundle_returns_clear():
    """Complete Phase-1 bundle (all 3 THEORY facets, fresh) must return CLEAR.

    Fixture scenario: honest_complete_fresh_clear.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "CLEAR", (
        f"Complete all-THEORY fresh bundle returned verdict={result['verdict']!r}; expected CLEAR"
    )


def test_clear_raw_response_notes_all_facets_present():
    """On CLEAR path, raw_response must note all 3 facets present + THEORY + fresh.

    The dispatch brief specifies: raw_response notes
    'all 3 facets present, all THEORY, frozen<90d'.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    rr = result["raw_response"]
    # Assert shape / content keywords — not exact string matching.
    raw_str = json.dumps(rr).lower()
    assert any(
        kw in raw_str for kw in ("all 3", "3 facets", "all three", "facets present", "complete")
    ), f"CLEAR raw_response does not mention facet completeness; raw_response={rr!r}"


# ---------------------------------------------------------------------------
# T4 — Indicator 1: missing facets → BREACH
# ---------------------------------------------------------------------------


def test_missing_gamma_returns_breach():
    """Indicator 1: bundle without gamma must return BREACH.

    Fixture scenario: missing_gamma_breach.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("missing_gamma_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Bundle missing gamma returned verdict={result['verdict']!r}; expected BREACH"
    )


def test_missing_gamma_raw_response_names_the_gap():
    """Indicator 1: raw_response must name 'gamma' as a missing facet."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("missing_gamma_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    raw_str = json.dumps(result["raw_response"]).lower()
    assert "gamma" in raw_str, (
        f"BREACH raw_response does not name the missing facet 'gamma'; "
        f"raw_response={result['raw_response']!r}"
    )


def test_missing_utility_family_returns_breach():
    """Indicator 1: bundle without utility_family must return BREACH."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("missing_utility_family_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Bundle missing utility_family returned verdict={result['verdict']!r}; expected BREACH"
    )


def test_missing_wealth_argument_returns_breach():
    """Indicator 1: bundle without wealth_argument must return BREACH."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("missing_wealth_argument_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Bundle missing wealth_argument returned verdict={result['verdict']!r}; expected BREACH"
    )


def test_empty_facets_returns_breach_on_all_missing():
    """Hostile edge: empty spec_facets_rows → BREACH with all three facets listed missing.

    Dispatch brief hostile edge: 'Empty spec_facets for bundle → BREACH on indicator 1'.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("empty_facets_breach")
    assert facets == [], "fixture scenario must have empty facets list"
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Empty facets list returned verdict={result['verdict']!r}; expected BREACH"
    )
    raw_str = json.dumps(result["raw_response"]).lower()
    for required_facet in ("gamma", "utility_family", "wealth_argument"):
        assert required_facet in raw_str, (
            f"BREACH raw_response (empty facets) does not name missing facet "
            f"{required_facet!r}; raw_response={result['raw_response']!r}"
        )


# ---------------------------------------------------------------------------
# T5 — Indicator 2: unrecognized freeze_discipline → BREACH
# ---------------------------------------------------------------------------


def test_unrecognized_freeze_discipline_returns_breach():
    """Indicator 2: any facet with discipline outside NN1_HONEST_DISCIPLINES ∪ {BACKTEST_SELECTION}
    must return BREACH — defense-in-depth against unknown values.

    Fixture scenario: unrecognized_discipline_breach.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("unrecognized_discipline_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Unrecognized freeze_discipline returned verdict={result['verdict']!r}; "
        "expected BREACH (defense-in-depth)"
    )


def test_unrecognized_discipline_raw_response_names_the_offending_facet():
    """Indicator 2: raw_response must identify the facet with the unrecognized discipline."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("unrecognized_discipline_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    raw_str = json.dumps(result["raw_response"]).lower()
    # The offending facet is 'gamma' per the fixture.
    assert "gamma" in raw_str, (
        f"Indicator 2 BREACH raw_response does not name the offending facet 'gamma'; "
        f"raw_response={result['raw_response']!r}"
    )


def test_backtest_selection_discipline_triggers_breach():
    """Indicator 2: BACKTEST_SELECTION is not in NN1_HONEST_DISCIPLINES and must
    also produce BREACH when it appears as a freeze_discipline value.

    This tests a known-violation discipline (not just an unknown forward-compat value).
    Defense-in-depth: even if NN1_HONEST_DISCIPLINES is somehow widened to include
    BACKTEST_SELECTION, the Spec Critic must independently flag it.
    """
    mod = _import_spec_critic()
    facets_with_backtest_selection = [
        {
            "facet_name": "gamma",
            "facet_value": "2.0",
            "freeze_discipline": "BACKTEST_SELECTION",
            "frozen_at": _fresh_frozen_at(),
        },
        {
            "facet_name": "utility_family",
            "facet_value": "CRRA",
            "freeze_discipline": "THEORY",
            "frozen_at": _fresh_frozen_at(),
        },
        {
            "facet_name": "wealth_argument",
            "facet_value": "compounded_return",
            "freeze_discipline": "THEORY",
            "frozen_at": _fresh_frozen_at(),
        },
    ]
    result = mod.compute_spec_critic_observation(
        "bundle-backtest-sel-001", facets_with_backtest_selection, _now=_REFERENCE_NOW
    )
    assert result["verdict"] == "BREACH", (
        f"BACKTEST_SELECTION freeze_discipline returned verdict={result['verdict']!r}; "
        "expected BREACH — Spec Critic is defense-in-depth on top of NN1 compliance check"
    )


# ---------------------------------------------------------------------------
# T6 — Indicator 3: stale frozen_at → WATCH
# ---------------------------------------------------------------------------


def test_stale_frozen_at_returns_watch():
    """Indicator 3: facets frozen > 90 days ago must return WATCH (advisory, non-blocking).

    Fixture scenario: stale_frozen_at_watch.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("stale_frozen_at_watch")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "WATCH", (
        f"Stale bundle (frozen 91 days ago) returned verdict={result['verdict']!r}; "
        "expected WATCH — stale frozen_at is advisory, not a hard block"
    )


def test_frozen_at_exactly_90_days_is_watch():
    """Indicator 3 boundary: frozen_at exactly 90 days ago must be WATCH (inclusive boundary).

    Dispatch brief: 'frozen_at is > 90 days old → flag spec age risk'.
    The inclusive interpretation: >= 90 days → WATCH.
    Boundary documented so the implementer cannot silently treat it as CLEAR.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("frozen_at_exactly_90_days_watch")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "WATCH", (
        f"frozen_at exactly 90 days ago returned verdict={result['verdict']!r}; "
        "expected WATCH — inclusive boundary: >=90 days is WATCH per dispatch brief"
    )


def test_stale_watch_is_advisory_only():
    """Indicator 3: WATCH for stale frozen_at must set is_advisory_only=1.

    A stale bundle does not block the run — it is an informational flag only.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("stale_frozen_at_watch")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["is_advisory_only"] == 1, (
        f"Stale WATCH is_advisory_only={result['is_advisory_only']!r}; "
        "expected 1 — stale frozen_at does not block the run"
    )


def test_stale_raw_response_mentions_spec_age():
    """Indicator 3: raw_response must mention the spec age concern."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("stale_frozen_at_watch")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    raw_str = json.dumps(result["raw_response"]).lower()
    assert any(kw in raw_str for kw in ("stale", "age", "days", "frozen_at", "90")), (
        f"Stale WATCH raw_response does not mention spec age; "
        f"raw_response={result['raw_response']!r}"
    )


# ---------------------------------------------------------------------------
# T7 — Indicator 4: Phase-2 facets seeded prematurely → BREACH
# ---------------------------------------------------------------------------


def test_phase2_facet_lambda_returns_breach():
    """Indicator 4: 'lambda' in spec_facets must return BREACH ('phase scope leak').

    Dispatch brief: 'Phase-1 strict; reject premature Phase-2 facet'.
    Fixture scenario: phase2_facet_lambda_breach.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("phase2_facet_lambda_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Bundle with 'lambda' facet returned verdict={result['verdict']!r}; "
        "expected BREACH — Phase-2 facet seeded prematurely must be rejected"
    )


def test_phase2_facet_lambda_raw_response_names_scope_leak():
    """Indicator 4: raw_response must name the Phase-2 facet and 'phase scope leak'."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("phase2_facet_lambda_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    raw_str = json.dumps(result["raw_response"]).lower()
    assert "lambda" in raw_str, (
        f"Indicator 4 BREACH raw_response does not name the Phase-2 facet 'lambda'; "
        f"raw_response={result['raw_response']!r}"
    )
    assert any(
        kw in raw_str for kw in ("phase", "scope", "leak", "premature", "phase-2", "phase 2")
    ), (
        f"Indicator 4 BREACH raw_response does not mention phase scope concern; "
        f"raw_response={result['raw_response']!r}"
    )


def test_phase2_facet_hysteresis_threshold_returns_breach():
    """Indicator 4: 'hysteresis-threshold' in spec_facets must return BREACH.

    Fixture scenario: phase2_facet_hysteresis_breach.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("phase2_facet_hysteresis_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    assert result["verdict"] == "BREACH", (
        f"Bundle with 'hysteresis-threshold' facet returned verdict={result['verdict']!r}; "
        "expected BREACH — Phase-2 facet 'hysteresis-threshold' must be rejected"
    )


def test_phase2_facet_hysteresis_raw_response_names_the_facet():
    """Indicator 4: raw_response must identify 'hysteresis-threshold' as the offender."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("phase2_facet_hysteresis_breach")
    result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
    raw_str = json.dumps(result["raw_response"]).lower()
    assert "hysteresis" in raw_str, (
        f"Indicator 4 BREACH raw_response does not name 'hysteresis-threshold'; "
        f"raw_response={result['raw_response']!r}"
    )


# ---------------------------------------------------------------------------
# T8 — Wall integrity: spec_critic must not call get_connection / get_ro_connection
# ---------------------------------------------------------------------------


def test_spec_critic_module_does_not_import_get_connection_directly():
    """advisors/spec_critic.py must not call get_connection or get_ro_connection.

    Wall integrity rule: all DB reads flow through database.advisor_ro_query.
    Direct connection access bypasses both the COALESCE guard and the
    wall-breach tripwire — it is structurally prohibited.

    Static inspection: reads the module source and checks for the forbidden calls.
    """
    spec_critic_path = pathlib.Path(__file__).parents[2] / "advisors" / "spec_critic.py"
    if not spec_critic_path.exists():
        pytest.fail(
            f"advisors/spec_critic.py not found at {spec_critic_path}. "
            "Create the module first (implementer task)."
        )
    source = spec_critic_path.read_text(encoding="utf-8")
    for forbidden_call in ("get_connection(", "get_ro_connection("):
        assert forbidden_call not in source, (
            f"advisors/spec_critic.py contains forbidden call {forbidden_call!r}. "
            "All DB reads must flow through database.advisor_ro_query — "
            "direct connection access bypasses the wall-integrity contract."
        )


# ---------------------------------------------------------------------------
# T9 — Read-only contract: run_spec_critic writes only to advisor_observations
# ---------------------------------------------------------------------------


def test_run_spec_critic_calls_insert_advisor_observation():
    """run_spec_critic must persist the observation via insert_advisor_observation.

    Patches insert_advisor_observation and verifies it is called once with the
    correct advisor_role and verdict.
    """
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")

    with patch("database.insert_advisor_observation", return_value=42) as mock_insert:
        row_id = mod.run_spec_critic(bundle_id, facets, _now=_REFERENCE_NOW)

    assert mock_insert.called, (
        "run_spec_critic did not call database.insert_advisor_observation; "
        "the observation must be persisted via the standard write path"
    )
    assert mock_insert.call_count == 1, (
        f"run_spec_critic called insert_advisor_observation {mock_insert.call_count} times; "
        "expected exactly 1 call per run"
    )

    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs.get("advisor_role") == "SPEC_CRITIC", (
        f"insert_advisor_observation called with advisor_role={call_kwargs.get('advisor_role')!r}; "
        "expected 'SPEC_CRITIC'"
    )


def test_run_spec_critic_returns_integer_row_id():
    """run_spec_critic must return the integer row id from insert_advisor_observation."""
    mod = _import_spec_critic()
    bundle_id, facets = _scenario("honest_complete_fresh_clear")

    with patch("database.insert_advisor_observation", return_value=99):
        row_id = mod.run_spec_critic(bundle_id, facets, _now=_REFERENCE_NOW)

    assert row_id == 99, (
        f"run_spec_critic returned {row_id!r}; expected the row_id (99) "
        "returned by insert_advisor_observation"
    )


# ---------------------------------------------------------------------------
# T10 — Phase-2 deferral: Spec Critic does not compute Regime Narrator observations
# ---------------------------------------------------------------------------


def test_spec_critic_does_not_produce_regime_narrator_role():
    """Spec Critic must never produce observations with advisor_role='REGIME_NARRATOR'.

    Phase-2 deferral: the Spec Critic is Phase-1 only. Producing Regime Narrator
    observations would be a phase scope violation in the production code.
    """
    mod = _import_spec_critic()
    for scenario_name in (
        "honest_complete_fresh_clear",
        "missing_gamma_breach",
        "stale_frozen_at_watch",
        "phase2_facet_lambda_breach",
    ):
        bundle_id, facets = _scenario(scenario_name)
        result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
        assert result["advisor_role"] != "REGIME_NARRATOR", (
            f"scenario {scenario_name!r}: Spec Critic produced advisor_role='REGIME_NARRATOR'. "
            "Phase-2 deferral: Spec Critic is Phase-1 only and must not compute Regime Narrator observations."
        )


# ---------------------------------------------------------------------------
# T11 — is_advisory_only is always 1 across all paths
# ---------------------------------------------------------------------------


def test_is_advisory_only_is_always_1_across_all_verdict_paths():
    """is_advisory_only must be 1 on CLEAR, WATCH, and BREACH paths.

    The Spec Critic never moves money — is_advisory_only is unconditionally 1.
    A test that only checks the CLEAR path is insufficient because a wrong
    implementation could set is_advisory_only=0 on BREACH.
    """
    mod = _import_spec_critic()
    scenario_to_expected_verdict = {
        "honest_complete_fresh_clear": "CLEAR",
        "stale_frozen_at_watch": "WATCH",
        "missing_gamma_breach": "BREACH",
    }
    for scenario_name, expected_verdict in scenario_to_expected_verdict.items():
        bundle_id, facets = _scenario(scenario_name)
        result = mod.compute_spec_critic_observation(bundle_id, facets, _now=_REFERENCE_NOW)
        # Confirm we're actually testing the expected verdict path.
        assert result["verdict"] == expected_verdict, (
            f"scenario {scenario_name!r}: expected verdict {expected_verdict!r}, "
            f"got {result['verdict']!r} — test would be vacuous on wrong path"
        )
        assert result["is_advisory_only"] == 1, (
            f"scenario {scenario_name!r} ({expected_verdict} path): "
            f"is_advisory_only={result['is_advisory_only']!r}; expected 1. "
            "The Spec Critic is always advisory — it must never be authoritative."
        )


# ---------------------------------------------------------------------------
# T12 — Indicator precedence: BREACH wins over WATCH when both indicators fire
# ---------------------------------------------------------------------------


def test_breach_wins_over_watch_when_stale_and_missing_facet():
    """When both I-1 (missing facet → BREACH) and I-3 (stale → WATCH) fire,
    verdict must be BREACH — the highest-severity indicator wins.

    Constructs a bundle that is stale AND missing wealth_argument to exercise
    the multi-indicator precedence rule directly.
    """
    mod = _import_spec_critic()
    stale_incomplete_facets = [
        {
            "facet_name": "gamma",
            "facet_value": "2.0",
            "freeze_discipline": "THEORY",
            "frozen_at": _stale_frozen_at(),  # stale → I-3 fires
        },
        {
            "facet_name": "utility_family",
            "facet_value": "CRRA",
            "freeze_discipline": "THEORY",
            "frozen_at": _stale_frozen_at(),
        },
        # wealth_argument intentionally absent → I-1 fires
    ]
    result = mod.compute_spec_critic_observation(
        "bundle-multi-indicator-001", stale_incomplete_facets, _now=_REFERENCE_NOW
    )
    assert result["verdict"] == "BREACH", (
        f"Stale + missing facet bundle returned verdict={result['verdict']!r}; "
        "expected BREACH — highest-severity indicator must win (BREACH > WATCH)"
    )


# ---------------------------------------------------------------------------
# T13 — bundle_hash not in spec_bundles → handled error, not silent no-op
# ---------------------------------------------------------------------------


def test_unknown_bundle_hash_does_not_silently_no_op():
    """When spec_bundle_id does not match any row in spec_bundles, the function
    must not silently return CLEAR. It must either raise or return BREACH with
    an informative raw_response.

    A silent no-op (returning CLEAR for an unknown bundle) would allow a corrupt
    or out-of-process call to bypass the critic entirely — a structural hazard.

    The dispatch brief: 'bundle_hash not in spec_bundles → error path (handled,
    does not silently no-op)'. The pure function receives spec_facets_rows
    already fetched by the caller; an empty list for an unknown bundle_id
    will fire I-1 (no required facets). This test verifies that the empty-facets
    path does not produce CLEAR.
    """
    mod = _import_spec_critic()
    # Unknown bundle: no facets fetched (caller would return empty list for unknown hash).
    result = mod.compute_spec_critic_observation(
        "bundle-nonexistent-hash-xyz", [], _now=_REFERENCE_NOW
    )
    assert result["verdict"] != "CLEAR", (
        f"Empty facets for unknown bundle returned verdict={result['verdict']!r}; "
        "expected BREACH (all facets missing) — "
        "a silent CLEAR for an unknown bundle is a structural hazard"
    )
