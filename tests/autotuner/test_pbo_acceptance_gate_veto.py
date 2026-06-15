"""RED tests — Change 3: PBO veto wired into evaluate_acceptance_gate + autotuner.

evaluate_acceptance_gate gains an optional `pbo: float | None` parameter.
A new STAGE-1 hard veto fires when pbo > PBO_REJECT_THRESHOLD=0.5.

Orthogonality (the load-bearing invariant):
  BHY/n_effective = multiplicity axis  (unchanged by this change)
  PBO             = sample-robustness axis (NEW veto, independent axis)

  compute_pbo / compute_n_effective / _haircut_select are BYTE-IDENTICAL after
  this change — PBO does NOT change n_effective or n_optuna.

Autotuner wiring:
  - After winner_trial = _haircut_select(...), autotuner selects the TOP-20 PRE-BHY
    trials by raw Optuna value (measures SELECTION-PROCESS overfitting, not
    post-BHY generalization), computes pbo = compute_pbo(pre_bhy_top20_date_returns,
    eligible_dates, gamma), and passes it to evaluate_acceptance_gate(pbo=pbo).
  - The AI-Advisor call to evaluate_acceptance_gate (from advisors/backtest_gate_engine.py)
    passes pbo=None — NO behavior change on the Advisor path.

Every test here MUST FAIL until the implementation is in place.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys
import types

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _import_acceptance_gate() -> types.ModuleType:
    repo = str(_WORKTREE_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    return importlib.import_module("acceptance_gate")


def _acceptance_gate_src() -> str:
    return (_WORKTREE_ROOT / "acceptance_gate.py").read_text(encoding="utf-8")


def _autotuner_src() -> str:
    return (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")


def _backtest_gate_src() -> str:
    p = _WORKTREE_ROOT / "advisors" / "backtest_gate_engine.py"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# evaluate_acceptance_gate signature: pbo optional param
# ---------------------------------------------------------------------------


class TestEvaluateAcceptanceGatePboParam:
    """evaluate_acceptance_gate must accept an optional `pbo: float | None` parameter."""

    def test_evaluate_acceptance_gate_accepts_pbo_kwarg(self):
        """evaluate_acceptance_gate must accept pbo=None without raising."""
        ag = _import_acceptance_gate()
        # Call with pbo=None; all other params set to pass all vetoes and avoid
        # triggering the panel (minimal valid call).
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=1.0,
            fallback_oos_alpha=0.5,
            default_oos_alpha=0.5,
            candidate_stability_score=0.8,
            candidate_prior_anchor_score=0.8,
            incumbent_stability_score=0.6,
            incumbent_prior_anchor_score=0.6,
            pbo=None,
        )
        # pbo=None means no PBO veto applies — should reach the panel.
        assert verdict is not None

    def test_pbo_kwarg_has_default_none(self):
        """pbo must default to None (backward-compatible — existing callers unaffected)."""
        ag = _import_acceptance_gate()
        import inspect

        sig = inspect.signature(ag.evaluate_acceptance_gate)
        assert "pbo" in sig.parameters, "evaluate_acceptance_gate must have a 'pbo' parameter"
        param = sig.parameters["pbo"]
        assert param.default is None, (
            f"pbo parameter must default to None for backward compatibility, "
            f"got default={param.default!r}"
        )

    def test_pbo_above_threshold_triggers_veto_failed_decision(self):
        """PBO > 0.5 must yield DECISION_REJECT_VETO_FAILED with panel_score=None."""
        ag = _import_acceptance_gate()

        # PBO=0.6 > 0.5 threshold — veto must fire.
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=1.0,
            fallback_oos_alpha=0.5,
            default_oos_alpha=0.5,
            candidate_stability_score=0.8,
            candidate_prior_anchor_score=0.8,
            incumbent_stability_score=0.6,
            incumbent_prior_anchor_score=0.6,
            pbo=0.6,
        )
        assert verdict.decision == ag.DECISION_REJECT_VETO_FAILED, (
            f"PBO=0.6 > 0.5 must trigger DECISION_REJECT_VETO_FAILED, "
            f"got decision={verdict.decision!r}"
        )
        assert verdict.panel_score is None, (
            "One-directional brake invariant: panel_score must be None when any veto fires. "
            f"Got panel_score={verdict.panel_score!r}"
        )

    def test_pbo_at_exactly_threshold_does_not_trigger_veto(self):
        """PBO=0.5 exactly equals threshold — the veto does NOT fire (boundary contract).

        The reject condition is pbo > PBO_REJECT_THRESHOLD (strict), so pbo=0.5
        must pass the veto check and allow the gate to proceed to the panel stage.
        """
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=1.0,
            fallback_oos_alpha=0.5,
            default_oos_alpha=0.5,
            candidate_stability_score=0.8,
            candidate_prior_anchor_score=0.8,
            incumbent_stability_score=0.6,
            incumbent_prior_anchor_score=0.6,
            pbo=0.5,  # exactly at threshold — must NOT veto
        )
        # With OOS passing and good panel scores, should not be REJECT_VETO_FAILED.
        assert verdict.decision != ag.DECISION_REJECT_VETO_FAILED, (
            "PBO=0.5 is at the threshold boundary; the veto condition is pbo > threshold "
            "(strict), so PBO=0.5 must NOT trigger a veto."
        )

    def test_pbo_none_does_not_trigger_pbo_veto(self):
        """pbo=None must not trigger the PBO veto (the AI-Advisor path uses pbo=None)."""
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=1.0,
            fallback_oos_alpha=0.5,
            default_oos_alpha=0.5,
            candidate_stability_score=0.8,
            candidate_prior_anchor_score=0.8,
            incumbent_stability_score=0.6,
            incumbent_prior_anchor_score=0.6,
            pbo=None,  # AI-Advisor path — no PBO veto
        )
        # pbo=None means "not computed" — must not veto.
        assert verdict.decision != ag.DECISION_REJECT_VETO_FAILED, (
            "pbo=None must not trigger the PBO veto (AI-Advisor call site passes pbo=None)"
        )

    def test_pbo_veto_sequenced_in_stage_1(self):
        """The PBO veto must be in STAGE-1 (hard vetoes), not STAGE-2 (panel)."""
        src = _acceptance_gate_src()
        # The veto condition check must appear before the panel computation.
        # Heuristic: the pbo veto check must appear in the source BEFORE the
        # _compute_panel_score call.
        pbo_check_pos = src.find("pbo")
        panel_pos = src.find("_compute_panel_score")
        assert pbo_check_pos != -1, "pbo must appear in acceptance_gate.py"
        assert panel_pos != -1, "_compute_panel_score must appear in acceptance_gate.py"
        assert pbo_check_pos < panel_pos, (
            "The pbo veto check must appear BEFORE _compute_panel_score — "
            "it is a STAGE-1 hard veto, not a panel component."
        )


# ---------------------------------------------------------------------------
# One-directional brake: PBO veto cannot be outvoted by panel score
# ---------------------------------------------------------------------------


class TestPboVetoOnDirectionalBrake:
    """PBO veto is un-outvotable: panel_score=None when PBO fires, always."""

    def test_stellar_panel_scores_cannot_override_pbo_veto(self):
        """Even perfect panel scores (1.0) cannot resurrect a PBO-vetoed candidate."""
        ag = _import_acceptance_gate()

        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=False,
            winner_p_adj=0.001,  # excellent BHY
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=100.0,  # stellar OOS
            fallback_oos_alpha=0.1,
            default_oos_alpha=0.1,
            candidate_stability_score=1.0,  # perfect panel
            candidate_prior_anchor_score=1.0,
            incumbent_stability_score=0.0,
            incumbent_prior_anchor_score=0.0,
            pbo=0.99,  # very high PBO — must veto despite stellar scores
        )
        assert verdict.decision == ag.DECISION_REJECT_VETO_FAILED, (
            "PBO=0.99 must veto even when all other scores are perfect. "
            f"Got decision={verdict.decision!r}"
        )
        assert verdict.panel_score is None, (
            "panel_score must be None when PBO veto fires — one-directional brake. "
            f"Got {verdict.panel_score!r}"
        )

    def test_existing_vetoes_unaffected_by_pbo_param(self):
        """BHY/NN1/purge vetoes must still fire independently of pbo value."""
        ag = _import_acceptance_gate()

        # BHY veto fires (winner_trial_is_none=True) even with pbo=0.0 (excellent PBO).
        verdict = ag.evaluate_acceptance_gate(
            winner_trial_is_none=True,  # BHY veto
            winner_p_adj=None,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=1.0,
            fallback_oos_alpha=0.5,
            default_oos_alpha=0.5,
            candidate_stability_score=0.9,
            candidate_prior_anchor_score=0.9,
            incumbent_stability_score=0.5,
            incumbent_prior_anchor_score=0.5,
            pbo=0.0,  # excellent PBO — but BHY already vetoes
        )
        assert verdict.decision == ag.DECISION_REJECT_VETO_FAILED, (
            f"BHY veto must still fire even when pbo=0.0 (excellent). Got {verdict.decision!r}"
        )


# ---------------------------------------------------------------------------
# AI-Advisor path is unaffected (backtest_gate_engine.py passes pbo=None)
# ---------------------------------------------------------------------------


class TestAiAdvisorPathUnchanged:
    """The AI-Advisor call site in backtest_gate_engine.py must pass pbo=None."""

    def test_backtest_gate_engine_passes_pbo_none(self):
        """advisors/backtest_gate_engine.py must pass pbo=None to evaluate_acceptance_gate."""
        src = _backtest_gate_src()
        assert "evaluate_acceptance_gate" in src, (
            "backtest_gate_engine.py must call evaluate_acceptance_gate"
        )
        # The call must include pbo=None explicitly or rely on the default None.
        # Either is acceptable; what is NOT acceptable is passing a computed pbo
        # value from the AI-Advisor path.
        # Check that no 'pbo=compute_pbo' or 'pbo=math_engine.compute_pbo' appears.
        assert "pbo=compute_pbo" not in src, (
            "backtest_gate_engine.py must NOT pass a computed pbo to evaluate_acceptance_gate. "
            "The AI-Advisor path uses pbo=None (no behavior change)."
        )
        assert "pbo=math_engine.compute_pbo" not in src, (
            "backtest_gate_engine.py must NOT call math_engine.compute_pbo."
        )


# ---------------------------------------------------------------------------
# Autotuner wiring: top-K PRE-BHY selection + compute_pbo call
# ---------------------------------------------------------------------------


class TestAutotunerPboWiring:
    """The autotuner must select top-K PRE-BHY trials and call compute_pbo post-selection."""

    def test_autotuner_calls_compute_pbo(self):
        """autotuner.py must call math_engine.compute_pbo (or import compute_pbo)."""
        src = _autotuner_src()
        has_compute_pbo = "compute_pbo" in src or "math_engine.compute_pbo" in src
        assert has_compute_pbo, (
            "autotuner.py must call compute_pbo (from math_engine) "
            "after the BHY haircut selection to compute the PBO gate value."
        )

    def test_autotuner_passes_pbo_to_evaluate_acceptance_gate(self):
        """autotuner.py must pass pbo=<computed_value> to evaluate_acceptance_gate."""
        src = _autotuner_src()
        assert "evaluate_acceptance_gate" in src, "autotuner.py must call evaluate_acceptance_gate"
        # Check that pbo is passed in the autotuner's evaluate_acceptance_gate call.
        assert "pbo=" in src, "autotuner.py must pass pbo= keyword arg to evaluate_acceptance_gate"

    def test_pbo_computed_from_pre_bhy_top_k_not_post_bhy(self):
        """PBO is computed on PRE-BHY top-K, not post-BHY winner only.

        This is the key design choice: PBO measures SELECTION-PROCESS overfitting
        across the full top-K candidate set. Post-BHY has already selected a winner;
        running CSCV on only one config is degenerate (K=1 → PBO=undefined).
        Using the pre-BHY top-K (sorted by raw Optuna value) measures whether
        the selection process is robust across the date-partition dimension.
        """
        src = _autotuner_src()
        # The autotuner must reference top-K selection before calling compute_pbo.
        # Check that _CSCV_TOP_K or a reference to top-20 pre-BHY appears.
        assert "_CSCV_TOP_K" in src or "top_k" in src or "top-20" in src or "top_20" in src, (
            "autotuner.py must select the PRE-BHY top-K trials before calling compute_pbo. "
            "_CSCV_TOP_K (=20) from math_engine must guide the selection."
        )

    def test_compute_n_effective_not_changed_by_pbo_addition(self):
        """compute_n_effective must be byte-identical after Change 3 (anti-double-count).

        PBO is on the sample-robustness axis; BHY/n_effective is on the multiplicity
        axis. Adding PBO must NOT change how compute_n_effective works.
        """
        import ast

        src = _autotuner_src()
        tree = ast.parse(src)
        neff_fns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "compute_n_effective"
        ]
        assert len(neff_fns) == 1, (
            f"Expected exactly 1 compute_n_effective definition, found {len(neff_fns)}"
        )
        fn = neff_fns[0]
        # compute_n_effective must not reference pbo or compute_pbo anywhere.
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and node.id in {"compute_pbo", "pbo", "_CSCV_TOP_K"}:
                pytest.fail(
                    f"compute_n_effective must not reference {node.id!r} — "
                    "PBO is the sample-robustness axis (orthogonal to n_effective). "
                    "Anti-double-count regression."
                )


# ---------------------------------------------------------------------------
# Acceptance gate source: PBO veto docstring / orthogonality comment
# ---------------------------------------------------------------------------


class TestAcceptanceGatePboDocumentation:
    """The acceptance gate source must document the PBO veto's orthogonality."""

    def test_acceptance_gate_docstring_mentions_pbo_orthogonality(self):
        """acceptance_gate.py must describe the BHY/PBO orthogonality in docstring or comment."""
        src = _acceptance_gate_src()
        has_pbo_doc = any(
            phrase in src
            for phrase in [
                "sample-robustness",
                "multiplicity",
                "orthogonal",
                "PBO",
                "Bailey",
            ]
        )
        assert has_pbo_doc, (
            "acceptance_gate.py must document the PBO veto's orthogonality with BHY: "
            "BHY=multiplicity axis, PBO=sample-robustness axis."
        )
