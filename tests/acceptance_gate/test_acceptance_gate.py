"""RED tests — Phase 3b democratized acceptance gate (OFFLINE, post-walk-forward only).

Every test here MUST FAIL until acceptance_gate.py is implemented correctly.
The module does not exist yet; the import at the top of each test class is
the first failure point for most tests.

Architecture contract (hard rules encoded as tests):
  - HARD VETOES (BHY/Yekutieli haircut, NN1 spec-freeze, purge/look-ahead integrity)
    fire FIRST and are un-outvotable.
  - panel_score is None whenever ANY veto fails — the panel never runs on a
    veto-failed candidate.  This is the one-directional-brake invariant.
  - WEIGHTED SURVIVOR PANEL uses FIXED named-constant weights, never learned/tuned.
  - Divergence Explainer is EXCLUDED from the gate entirely (binding constraint;
    project_cvar_divergence_validation_wall).
  - The gate is OFFLINE (post-walk-forward in autotuner.py). It MUST NOT be
    imported or called from alpha_bot_execution.py's exit path.

Fixture path: tests/fixtures/math/acceptance_gate_scenarios.json
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import types

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "math"
    / "acceptance_gate_scenarios.json"
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    """Load the acceptance_gate_scenarios fixture.  Fails fast if missing."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/acceptance_gate_scenarios.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers — module import + path resolution
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    # tests/acceptance_gate/<this_file> -> repo_root
    return pathlib.Path(__file__).resolve().parents[2]


def _import_acceptance_gate() -> types.ModuleType:
    """Import acceptance_gate from the repo root.

    Raises ImportError (RED) if the module does not exist yet.
    """
    import sys

    repo = str(_repo_root())
    if repo not in sys.path:
        sys.path.insert(0, repo)
    return importlib.import_module("acceptance_gate")


def _parse_source(filename: str) -> ast.Module:
    path = _repo_root() / filename
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# Section 1 — Module exists and exposes the required public interface
# ---------------------------------------------------------------------------


class TestModuleExists:
    """The acceptance_gate module must exist and expose its public contract."""

    def test_acceptance_gate_module_is_importable(self):
        """acceptance_gate.py must exist in the repo root and be importable.

        FAILS RED: module does not exist yet.
        """
        ag = _import_acceptance_gate()
        assert ag is not None

    def test_acceptance_gate_exposes_evaluate_function(self):
        """acceptance_gate must expose an evaluate() or evaluate_acceptance_gate() callable.

        The function is the single entry point for the gate decision.
        """
        ag = _import_acceptance_gate()
        entry = getattr(ag, "evaluate_acceptance_gate", None) or getattr(ag, "evaluate", None)
        assert callable(entry), (
            "acceptance_gate must expose a callable named 'evaluate_acceptance_gate' "
            "or 'evaluate' as the single gate entry point."
        )

    def test_acceptance_gate_exposes_acceptance_verdict_type(self):
        """acceptance_gate must expose an AcceptanceVerdict type (namedtuple, dataclass, or TypedDict).

        The return type of evaluate_acceptance_gate must carry vetoes_passed,
        panel_score, panel_breakdown, and decision fields.
        """
        ag = _import_acceptance_gate()
        assert hasattr(ag, "AcceptanceVerdict"), (
            "acceptance_gate must define AcceptanceVerdict — the structured return type "
            "that carries vetoes_passed, panel_score, panel_breakdown, and decision."
        )


# ---------------------------------------------------------------------------
# Section 2 — Verdict struct schema
# ---------------------------------------------------------------------------


class TestVerdictStructSchema:
    """AcceptanceVerdict must carry the required fields."""

    def _make_passing_verdict(self, ag):
        """Construct a minimal passing verdict via the evaluate function."""
        # We call evaluate with a scenario where all vetoes pass and OOS is superior.
        # The exact call shape is up to the implementer; we try the most natural form.
        return ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=5.0,
            fallback_oos_alpha=2.0,
            default_oos_alpha=1.0,
            candidate_stability_score=0.9,
            candidate_prior_anchor_score=0.85,
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )

    def test_verdict_has_vetoes_passed_field(self, fixture):
        """AcceptanceVerdict must contain a 'vetoes_passed' field (bool)."""
        required = fixture["verdict_struct_schema"]["required_keys"]
        assert "vetoes_passed" in required  # fixture integrity check
        ag = _import_acceptance_gate()
        verdict = self._make_passing_verdict(ag)
        assert hasattr(verdict, "vetoes_passed"), (
            "AcceptanceVerdict must have a 'vetoes_passed' field."
        )
        assert isinstance(verdict.vetoes_passed, bool)

    def test_verdict_has_panel_score_field(self, fixture):
        """AcceptanceVerdict must contain a 'panel_score' field (float or None)."""
        ag = _import_acceptance_gate()
        verdict = self._make_passing_verdict(ag)
        assert hasattr(verdict, "panel_score"), "AcceptanceVerdict must have a 'panel_score' field."
        # When vetoes pass, panel_score must be a float
        assert verdict.panel_score is not None, (
            "panel_score must be a float (not None) when all vetoes pass."
        )
        assert isinstance(verdict.panel_score, float)

    def test_verdict_has_panel_breakdown_field(self, fixture):
        """AcceptanceVerdict must contain a 'panel_breakdown' dict."""
        ag = _import_acceptance_gate()
        verdict = self._make_passing_verdict(ag)
        assert hasattr(verdict, "panel_breakdown"), (
            "AcceptanceVerdict must have a 'panel_breakdown' dict field."
        )
        assert isinstance(verdict.panel_breakdown, dict)

    def test_verdict_has_decision_field(self, fixture):
        """AcceptanceVerdict must contain a 'decision' field from the defined enum."""
        valid_decisions = set(fixture["verdict_struct_schema"]["decision_enum"])
        ag = _import_acceptance_gate()
        verdict = self._make_passing_verdict(ag)
        assert hasattr(verdict, "decision"), "AcceptanceVerdict must have a 'decision' field."
        assert verdict.decision in valid_decisions, (
            f"decision must be one of {valid_decisions}, got {verdict.decision!r}."
        )


# ---------------------------------------------------------------------------
# Section 3 — One-directional brake: panel_score is None when ANY veto fails
# ---------------------------------------------------------------------------


class TestOnedirectionalBrake:
    """The panel MUST be None whenever any veto fails.

    This is the core anti-pattern prohibition: the panel cannot outvote a veto.
    All tests in this class are the hardest invariants — a defect here is
    catastrophic (noise-laundering relocated to the gate).
    """

    def test_bhy_veto_failure_panel_score_is_none(self, fixture):
        """BHY veto failed (winner_trial=None) → panel_score MUST be None.

        Regardless of how good every other metric looks.
        """
        scenario = fixture["veto_scenarios"]["bhy_veto_fails_panel_score_must_be_none"]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=c["winner_trial_is_none"],
            winner_p_adj=c["winner_p_adj"],
            nn1_compliant=c["nn1_compliant"],
            purge_integrity_ok=c["purge_integrity_ok"],
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )

        assert verdict.panel_score is None, (
            "ONE-DIRECTIONAL BRAKE VIOLATION: panel_score must be None when the BHY "
            "veto fails (winner_trial=None). The panel cannot run on a veto-failed "
            "candidate. A non-None panel_score here means the forbidden anti-pattern "
            "(discretionary score outvoting a failed veto) is possible."
        )

    def test_bhy_veto_failure_decision_is_reject(self, fixture):
        """BHY veto failed → decision MUST be REJECT_VETO_FAILED."""
        scenario = fixture["veto_scenarios"]["bhy_veto_fails_panel_score_must_be_none"]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=True,
            winner_p_adj=None,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )

        assert verdict.decision == scenario["expected"]["decision"], (
            f"BHY veto failure must yield decision={scenario['expected']['decision']!r}, "
            f"got {verdict.decision!r}."
        )

    def test_nn1_veto_failure_panel_score_is_none(self, fixture):
        """NN1 spec-freeze veto failed → panel_score MUST be None."""
        scenario = fixture["veto_scenarios"]["nn1_violation_panel_score_must_be_none"]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=c["winner_trial_is_none"],
            winner_p_adj=c["winner_p_adj"],
            nn1_compliant=False,  # NN1 veto fails
            purge_integrity_ok=c["purge_integrity_ok"],
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )

        assert verdict.panel_score is None, (
            "ONE-DIRECTIONAL BRAKE VIOLATION: panel_score must be None when the NN1 "
            "spec-freeze veto fails. NN1 compliance is a hard structural veto."
        )

    def test_nn1_veto_failure_decision_is_reject(self, fixture):
        """NN1 spec-freeze veto failed → decision MUST be REJECT_VETO_FAILED."""
        ag = _import_acceptance_gate()
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.001,
            nn1_compliant=False,
            purge_integrity_ok=True,
            oos_alpha=10.0,
            fallback_oos_alpha=1.0,
            default_oos_alpha=0.5,
            candidate_stability_score=0.95,
            candidate_prior_anchor_score=0.9,
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )
        assert verdict.decision == "REJECT_VETO_FAILED", (
            f"NN1 veto failure must yield REJECT_VETO_FAILED, got {verdict.decision!r}."
        )

    def test_purge_integrity_veto_failure_panel_score_is_none(self, fixture):
        """Purge/look-ahead integrity veto failed → panel_score MUST be None."""
        scenario = fixture["veto_scenarios"]["purge_integrity_veto_fails_panel_score_must_be_none"]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=c["winner_trial_is_none"],
            winner_p_adj=c["winner_p_adj"],
            nn1_compliant=c["nn1_compliant"],
            purge_integrity_ok=False,  # purge veto fails
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )

        assert verdict.panel_score is None, (
            "ONE-DIRECTIONAL BRAKE VIOLATION: panel_score must be None when the "
            "purge/look-ahead integrity veto fails."
        )

    def test_purge_integrity_veto_failure_decision_is_reject(self, fixture):
        """Purge/look-ahead integrity veto failed → decision MUST be REJECT_VETO_FAILED."""
        ag = _import_acceptance_gate()
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.005,
            nn1_compliant=True,
            purge_integrity_ok=False,
            oos_alpha=7.0,
            fallback_oos_alpha=2.0,
            default_oos_alpha=1.0,
            candidate_stability_score=0.8,
            candidate_prior_anchor_score=0.75,
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )
        assert verdict.decision == "REJECT_VETO_FAILED", (
            f"Purge integrity veto failure must yield REJECT_VETO_FAILED, got {verdict.decision!r}."
        )

    def test_stellar_discretionary_cannot_outvote_bhy_failure(self, fixture):
        """THE FORBIDDEN ANTI-PATTERN: maximal discretionary scores + BHY failure = REJECT.

        A candidate with stability_score=1.0, prior_anchor=1.0, oos_alpha=999,
        but winner_trial=None. panel_score MUST be None and decision MUST be
        REJECT_VETO_FAILED. The panel has zero power to resurrect a veto-failed candidate.
        """
        scenario = fixture["veto_scenarios"][
            "stellar_discretionary_score_cannot_outvote_bhy_failure"
        ]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=True,  # BHY veto explicitly failed
            winner_p_adj=None,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.0,
            incumbent_prior_anchor_score=0.0,
        )

        assert verdict.panel_score is None, (
            "FORBIDDEN ANTI-PATTERN: panel_score must be None when BHY veto fails, "
            "even with maximal discretionary scores (stability=1.0, prior_anchor=1.0, "
            "oos_alpha=999.0). The panel score MUST NOT be computed for veto-failed candidates."
        )
        assert verdict.decision == "REJECT_VETO_FAILED", (
            "FORBIDDEN ANTI-PATTERN: decision must be REJECT_VETO_FAILED when BHY veto "
            "fails, even with maximal discretionary scores."
        )

    def test_stellar_discretionary_cannot_outvote_nn1_failure(self, fixture):
        """Maximal discretionary scores + NN1 failure = REJECT (anti-pattern test, NN1 variant)."""
        scenario = fixture["veto_scenarios"][
            "stellar_discretionary_score_cannot_outvote_nn1_failure"
        ]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.001,
            nn1_compliant=False,  # NN1 veto explicitly failed
            purge_integrity_ok=True,
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.0,
            incumbent_prior_anchor_score=0.0,
        )

        assert verdict.panel_score is None, (
            "FORBIDDEN ANTI-PATTERN: panel_score must be None when NN1 veto fails, "
            "even with maximal discretionary scores."
        )
        assert verdict.decision == "REJECT_VETO_FAILED"

    def test_any_veto_fails_vetoes_passed_is_false(self):
        """verdict.vetoes_passed must be False when ANY of the three vetoes fails."""
        ag = _import_acceptance_gate()
        for kwargs_override in [
            # BHY failure
            {"winner_trial_is_none": True, "winner_p_adj": None},
            # NN1 failure
            {"nn1_compliant": False},
            # Purge integrity failure
            {"purge_integrity_ok": False},
        ]:
            base_kwargs = dict(
                winner_trial_is_none=False,
                winner_p_adj=0.01,
                nn1_compliant=True,
                purge_integrity_ok=True,
                oos_alpha=5.0,
                fallback_oos_alpha=1.0,
                default_oos_alpha=0.5,
                candidate_stability_score=0.9,
                candidate_prior_anchor_score=0.8,
                incumbent_stability_score=0.5,
                incumbent_prior_anchor_score=0.5,
            )
            base_kwargs.update(kwargs_override)
            verdict = ag.evaluate_acceptance_gate(**base_kwargs)
            assert verdict.vetoes_passed is False, (
                f"vetoes_passed must be False when a veto fails. "
                f"Override applied: {kwargs_override}. Got vetoes_passed={verdict.vetoes_passed!r}."
            )


# ---------------------------------------------------------------------------
# Section 4 — Happy path: all vetoes pass, OOS superior, panel adopts
# ---------------------------------------------------------------------------


class TestHappyPath:
    """When all vetoes pass and OOS is strictly superior, the gate can adopt."""

    def test_all_vetoes_pass_panel_score_is_not_none(self, fixture):
        """When all vetoes pass, panel_score MUST be a float (not None).

        This is the inverse of the one-directional-brake tests — verifying the
        gate doesn't suppress the panel score when vetoes DO pass.
        """
        scenario = fixture["veto_scenarios"]["all_vetoes_pass_oos_superior_panel_adopts"]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=c["winner_trial_is_none"],
            winner_p_adj=c["winner_p_adj"],
            nn1_compliant=c["nn1_compliant"],
            purge_integrity_ok=c["purge_integrity_ok"],
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.3,
            incumbent_prior_anchor_score=0.3,
        )

        assert verdict.vetoes_passed is True
        assert verdict.panel_score is not None, (
            "panel_score must be a float when all vetoes pass — the one-directional "
            "brake only suppresses the panel when vetoes fail, not when they pass."
        )
        assert isinstance(verdict.panel_score, float), (
            f"panel_score must be a float, got {type(verdict.panel_score).__name__!r}."
        )

    def test_all_vetoes_pass_oos_superior_high_panel_score_adopts(self, fixture):
        """All vetoes pass + OOS superior + panel favors candidate → ADOPT_CANDIDATE."""
        scenario = fixture["veto_scenarios"]["all_vetoes_pass_oos_superior_panel_adopts"]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=c["winner_p_adj"],
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            # Incumbent with poor discretionary scores so candidate clearly wins
            incumbent_stability_score=0.1,
            incumbent_prior_anchor_score=0.1,
        )

        assert verdict.decision == "ADOPT_CANDIDATE", (
            f"Expected ADOPT_CANDIDATE when all vetoes pass, OOS is superior, and "
            f"candidate discretionary panel score is clearly better than incumbent. "
            f"Got {verdict.decision!r}."
        )


# ---------------------------------------------------------------------------
# Section 5 — Panel is a one-directional brake (cannot promote non-OOS-superior)
# ---------------------------------------------------------------------------


class TestPanelCannotPromote:
    """The panel can only WITHHOLD adoption, never promote a non-OOS-superior candidate."""

    def test_panel_cannot_adopt_when_oos_loses_to_fallback(self, fixture):
        """Vetoes pass but oos_alpha < fallback_oos_alpha → KEEP_INCUMBENT (not ADOPT).

        The panel being high doesn't matter — the gate cannot promote a candidate
        that lost the OOS comparison.
        """
        scenario = fixture["one_directional_brake_scenarios"][
            "panel_cannot_adopt_when_candidate_loses_oos"
        ]
        c = scenario["candidate"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            # Perfect discretionary scores — must not help
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=0.0,
            incumbent_prior_anchor_score=0.0,
        )

        assert verdict.decision == "KEEP_INCUMBENT", (
            f"Panel must not adopt a candidate that lost the OOS comparison "
            f"(oos_alpha={c['oos_alpha']} < fallback_oos_alpha={c['fallback_oos_alpha']}). "
            f"Got {verdict.decision!r}."
        )

    def test_panel_withholds_adoption_from_oos_superior_with_poor_discretionary(self, fixture):
        """Vetoes pass + OOS superior + poor panel score → KEEP_INCUMBENT.

        This is the panel's legitimate function: acting as a brake on a
        technically-qualified candidate whose discretionary profile is poor.
        """
        scenario = fixture["one_directional_brake_scenarios"][
            "panel_can_withhold_adoption_from_oos_superior_candidate"
        ]
        c = scenario["candidate"]
        inc = scenario["incumbent"]
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.02,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=c["oos_alpha"],
            fallback_oos_alpha=c["fallback_oos_alpha"],
            default_oos_alpha=c["default_oos_alpha"],
            candidate_stability_score=c["stability_score_vs_incumbent"],
            candidate_prior_anchor_score=c["prior_anchor_score"],
            incumbent_stability_score=inc["stability_score_vs_incumbent"],
            incumbent_prior_anchor_score=inc["prior_anchor_score"],
        )

        assert verdict.decision == "KEEP_INCUMBENT", (
            f"Panel must withhold adoption from a candidate with poor discretionary "
            f"scores (stability={c['stability_score_vs_incumbent']}, "
            f"prior_anchor={c['prior_anchor_score']}) even when OOS is superior. "
            f"Got {verdict.decision!r}."
        )


# ---------------------------------------------------------------------------
# Section 6 — Fixed named-constant weights (no magic numbers)
# ---------------------------------------------------------------------------


class TestFixedNamedWeights:
    """Panel criteria weights must be named module-level constants, not inline literals."""

    def test_panel_weight_stability_constant_exists(self, fixture):
        """acceptance_gate must define PANEL_WEIGHT_STABILITY as a module-level constant."""
        ag = _import_acceptance_gate()
        assert hasattr(ag, "PANEL_WEIGHT_STABILITY"), (
            "acceptance_gate must define PANEL_WEIGHT_STABILITY as a named constant. "
            "Inline float literals are a no-magic-numbers violation."
        )
        w = ag.PANEL_WEIGHT_STABILITY
        assert isinstance(w, (int, float)), (
            f"PANEL_WEIGHT_STABILITY must be numeric, got {type(w).__name__!r}."
        )
        assert 0.0 < w <= 1.0, f"PANEL_WEIGHT_STABILITY must be in (0, 1], got {w}."

    def test_panel_weight_prior_anchor_constant_exists(self, fixture):
        """acceptance_gate must define PANEL_WEIGHT_PRIOR_ANCHOR as a module-level constant."""
        ag = _import_acceptance_gate()
        assert hasattr(ag, "PANEL_WEIGHT_PRIOR_ANCHOR"), (
            "acceptance_gate must define PANEL_WEIGHT_PRIOR_ANCHOR as a named constant."
        )
        w = ag.PANEL_WEIGHT_PRIOR_ANCHOR
        assert isinstance(w, (int, float))
        assert 0.0 < w <= 1.0

    def test_panel_weights_sum_to_one(self, fixture):
        """All PANEL_WEIGHT_* constants must sum to 1.0.

        This is a structural invariant that ensures panel_score is on a
        consistent [0,1] scale regardless of the number of criteria.
        Tolerance: 1e-9 (these are hand-set constants, not computed values).
        """
        ag = _import_acceptance_gate()
        weight_attrs = [name for name in dir(ag) if name.startswith("PANEL_WEIGHT_")]
        assert len(weight_attrs) >= 2, (
            f"Expected at least 2 PANEL_WEIGHT_* constants, found: {weight_attrs}."
        )
        total = sum(getattr(ag, name) for name in weight_attrs)
        assert total == pytest.approx(1.0, abs=1e-9), (
            # tolerance: 1e-9 because these are hand-set constants stored as Python
            # floats; IEEE-754 rounding is the only source of error, not computation
            f"PANEL_WEIGHT_* constants must sum to 1.0 (got {total}). "
            f"Constants checked: {weight_attrs}."
        )

    def test_panel_adopt_margin_threshold_constant_exists(self):
        """acceptance_gate must define PANEL_ADOPT_MARGIN_THRESHOLD as a named constant.

        This is the 'burden of proof on deviation' hurdle: the candidate must
        beat the incumbent's panel score by at least this margin to be adopted.
        """
        ag = _import_acceptance_gate()
        assert hasattr(ag, "PANEL_ADOPT_MARGIN_THRESHOLD"), (
            "acceptance_gate must define PANEL_ADOPT_MARGIN_THRESHOLD — the "
            "fixed score hurdle the candidate must exceed the incumbent by to "
            "be adopted. An inline literal here is a no-magic-numbers violation."
        )
        threshold = ag.PANEL_ADOPT_MARGIN_THRESHOLD
        assert isinstance(threshold, (int, float)), (
            f"PANEL_ADOPT_MARGIN_THRESHOLD must be numeric, got {type(threshold).__name__!r}."
        )
        assert threshold >= 0.0, "PANEL_ADOPT_MARGIN_THRESHOLD must be non-negative."


# ---------------------------------------------------------------------------
# Section 7 — Panel score is bounded in [0, 1]
# ---------------------------------------------------------------------------


class TestPanelScoreBound:
    """panel_score for any veto-passing candidate must be in [0.0, 1.0]."""

    @pytest.mark.parametrize(
        "stability, prior_anchor",
        [
            (0.0, 0.0),  # worst possible
            (0.5, 0.5),  # midpoint
            (1.0, 1.0),  # best possible
            (0.0, 1.0),  # mixed
            (1.0, 0.0),  # mixed
        ],
    )
    def test_panel_score_in_zero_one_for_veto_passing_candidates(self, stability, prior_anchor):
        """panel_score must be in [0.0, 1.0] for any combination of valid sub-scores.

        Property: weighted average of sub-scores in [0,1] with weights summing
        to 1 is itself in [0,1].
        """
        ag = _import_acceptance_gate()
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=5.0,
            fallback_oos_alpha=1.0,
            default_oos_alpha=0.5,
            candidate_stability_score=stability,
            candidate_prior_anchor_score=prior_anchor,
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
        )
        assert verdict.panel_score is not None, "panel_score must not be None when all vetoes pass."
        assert 0.0 <= verdict.panel_score <= 1.0, (
            # tolerance comment: panel_score is a weighted average of [0,1] sub-scores
            # with weights summing to 1; the only floating-point concern is boundary
            # precision, which 1e-12 accommodates without hiding real defects
            f"panel_score={verdict.panel_score} is outside [0.0, 1.0] for "
            f"stability={stability}, prior_anchor={prior_anchor}."
        )


# ---------------------------------------------------------------------------
# Section 8 — Divergence Explainer is excluded (binding architectural constraint)
# ---------------------------------------------------------------------------


class TestDivergenceExplainerExclusion:
    """The Divergence Explainer must be structurally excluded from the gate.

    Wiring it in would re-import the rejected CVaR-divergence idea
    (project_cvar_divergence_validation_wall; decision-science council §B.9).
    This is a permanent binding constraint, not a soft preference.
    """

    def test_acceptance_gate_does_not_import_divergence_explainer(self):
        """acceptance_gate.py must not import from advisors.divergence_explainer."""
        gate_path = _repo_root() / "acceptance_gate.py"
        assert gate_path.exists(), (
            f"acceptance_gate.py not found at {gate_path} — implement it first."
        )
        tree = _parse_source("acceptance_gate.py")

        for node in ast.walk(tree):
            # from advisors.divergence_explainer import ...
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "divergence_explainer" not in module, (
                    "acceptance_gate.py must not import from advisors.divergence_explainer. "
                    "The Divergence Explainer is excluded by binding architectural constraint "
                    "(project_cvar_divergence_validation_wall; §B.9)."
                )
            # import advisors.divergence_explainer
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "divergence_explainer" not in alias.name, (
                        "acceptance_gate.py must not import advisors.divergence_explainer."
                    )

    def test_panel_breakdown_has_no_divergence_explainer_key(self):
        """panel_breakdown dict must not contain a 'divergence_explainer' key."""
        ag = _import_acceptance_gate()
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=5.0,
            fallback_oos_alpha=1.0,
            default_oos_alpha=0.5,
            candidate_stability_score=0.8,
            candidate_prior_anchor_score=0.7,
            incumbent_stability_score=0.4,
            incumbent_prior_anchor_score=0.4,
        )
        assert "divergence_explainer" not in verdict.panel_breakdown, (
            "panel_breakdown must not contain a 'divergence_explainer' key. "
            "The Divergence Explainer is excluded from the gate by binding constraint."
        )


# ---------------------------------------------------------------------------
# Section 9 — Gate is offline: not imported/called from alpha_bot_execution.py
# ---------------------------------------------------------------------------


class TestOfflineIsolation:
    """The acceptance gate must NOT appear on the live 1-minute exit path.

    Architecture constraint: the engine runs at 1-minute cadence during market hours
    with no blocking I/O on the execution path. The gate is post-walk-forward
    (autotuner.py, OFFLINE). Any wiring into alpha_bot_execution.py is a violation.
    """

    def test_alpha_bot_execution_does_not_import_acceptance_gate(self):
        """alpha_bot_execution.py must not import from acceptance_gate."""
        tree = _parse_source("alpha_bot_execution.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "acceptance_gate" not in module, (
                    "alpha_bot_execution.py must not import from acceptance_gate. "
                    "The gate is an OFFLINE post-walk-forward decision layer "
                    "(autotuner.py); wiring it into the 1-minute live exit path "
                    "violates architecture constraint #1 (no blocking I/O on execution path)."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "acceptance_gate" not in alias.name, (
                        "alpha_bot_execution.py must not import acceptance_gate."
                    )

    def test_alpha_bot_execution_does_not_call_acceptance_gate_functions(self):
        """alpha_bot_execution.py must not call evaluate_acceptance_gate or any gate function."""
        tree = _parse_source("alpha_bot_execution.py")

        forbidden_names = {"evaluate_acceptance_gate", "evaluate", "AcceptanceVerdict"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Direct call: evaluate_acceptance_gate(...)
                if isinstance(func, ast.Name) and func.id in forbidden_names:
                    pytest.fail(
                        f"alpha_bot_execution.py calls '{func.id}' which belongs to "
                        f"the acceptance gate. The gate must only be called from "
                        f"autotuner.py (post-walk-forward, OFFLINE)."
                    )
                # Attribute call: acceptance_gate.evaluate_acceptance_gate(...)
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in forbidden_names
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "acceptance_gate"
                ):
                    pytest.fail(
                        f"alpha_bot_execution.py calls acceptance_gate.{func.attr}. "
                        f"The gate must only be invoked from autotuner.py."
                    )


# ---------------------------------------------------------------------------
# Section 10 — Overfitting Conscience and Spec Critic are the panel's advisors
#              (not re-invented); Divergence Explainer is excluded
# ---------------------------------------------------------------------------


class TestAdvisorReuse:
    """The panel must reuse existing advisor producers, not re-implement their logic."""

    def test_acceptance_gate_imports_overfitting_conscience(self):
        """acceptance_gate.py should import from advisors.overfitting_conscience (reuse, not reinvent)."""
        gate_path = _repo_root() / "acceptance_gate.py"
        assert gate_path.exists(), f"acceptance_gate.py not found at {gate_path}."
        tree = _parse_source("acceptance_gate.py")

        found_oc = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "overfitting_conscience" in module:
                    found_oc = True
                    break
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "overfitting_conscience" in alias.name:
                        found_oc = True
                        break

        assert found_oc, (
            "acceptance_gate.py must import from advisors.overfitting_conscience. "
            "The OC verdict is a discretionary panel input (design §Sub-question 1); "
            "re-implementing its logic would violate the 'adopt existing contracts' rule."
        )

    def test_acceptance_gate_imports_spec_critic(self):
        """acceptance_gate.py should import from advisors.spec_critic (reuse, not reinvent)."""
        gate_path = _repo_root() / "acceptance_gate.py"
        assert gate_path.exists(), f"acceptance_gate.py not found at {gate_path}."
        tree = _parse_source("acceptance_gate.py")

        found_sc = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "spec_critic" in module:
                    found_sc = True
                    break
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "spec_critic" in alias.name:
                        found_sc = True
                        break

        assert found_sc, (
            "acceptance_gate.py must import from advisors.spec_critic. "
            "The SC verdict maps to structural veto / discretionary panel input "
            "(design §Sub-question 1); re-implementing is duplication."
        )
