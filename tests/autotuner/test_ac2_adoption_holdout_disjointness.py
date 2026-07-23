"""
AC-2 (MA-5, HIGH) -- the adoption cascade's "OOS validation" must evaluate
the Optuna winner on data OUTSIDE its selection window; baselines and the AI
arm must be evaluated symmetrically; the hardcoded purge_integrity_ok=True
false attestation must be killed.

Bug (confirmed by direct read, autotuner.py:2365): ``history_test =
history_validation_full`` -- the raw validation fold, which is a SUBSET of
``sorted_dates[:frozen_start_idx]`` (train+validation combined), the exact
window AC-1's CPCV splits draw their selection scoring from. "OOS
validation passed!" log lines are not OOS for the arm being adopted -- the
window trials were selected on and the window the adoption decision is
gated on OVERLAP entirely.

RULED FRAMING (team-lead, math-r2.md ADDENDUM): "Your generic AC-2 framing
('the CPCV-eligible window' as the selection-window definition) survives
[the AC-1 split-level ruling] unchanged." This file tests the OBSERVABLE
holdout-disjointness contract -- whatever window ``history_test`` ends up
sourced from (frozen fold, or any other genuinely-outside-selection window)
must have EMPTY date intersection with the CPCV-eligible window
(``sorted_dates[:frozen_start_idx]``) that fed trial selection -- mechanism-
agnostic as to r2-stats's exact resolution.

Golden discipline: no producer-computed value is hardcoded; disjointness is
checked via SET INTERSECTION against the CPCV-eligible window definition
(train_ratio/validation_ratio-derived, matching run_autotuner's own split
formula), and arm-symmetry / attestation-kill are checked via source
inspection (AST-adjacent, cheap and precise) rather than a full run_autotuner
drive.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"


def _parse_run_autotuner_fn() -> ast.FunctionDef:
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_autotuner"),
        None,
    )
    assert fn is not None, "run_autotuner function not found in autotuner.py"
    return fn


# ===========================================================================
# (1) Holdout-disjointness: history_test must not be assigned from
# history_validation_full (the current, provably in-sample assignment --
# a subset of the CPCV-eligible selection window).
# ===========================================================================


def test_history_test_is_not_assigned_from_history_validation_full() -> None:
    """RED (pre-fix, confirmed live at autotuner.py:2365): ``history_test =
    history_validation_full`` assigns the OOS-cascade evaluation window to a
    SUBSET of the CPCV-eligible window (sorted_dates[:frozen_start_idx]) that
    AC-1's 15 splits draw their selection scoring from -- provably in-sample,
    not a genuine holdout."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))
    run_autotuner_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_autotuner"),
        None,
    )
    assert run_autotuner_fn is not None

    offending_assignments = [
        node
        for node in ast.walk(run_autotuner_fn)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "history_test"
        and isinstance(node.value, ast.Name)
        and node.value.id == "history_validation_full"
    ]
    assert not offending_assignments, (
        "run_autotuner still assigns 'history_test = history_validation_full' -- this "
        "window is a SUBSET of the CPCV-eligible selection window (sorted_dates"
        "[:frozen_start_idx]), making the adoption cascade's OOS check in-sample. "
        "AC-2 (MA-5) requires history_test to be sourced from a window with EMPTY "
        "intersection against the CPCV-eligible selection window (e.g. the frozen "
        "fold, which _generate_cpcv_folds already structurally excludes)."
    )


class TestHoldoutWindowDisjointFromCpcvEligibleWindow:
    """Direct behavioral proof, via the same date-partition arithmetic
    run_autotuner itself uses, that a correct history_test window
    (whatever fold r2-stats sources it from) is disjoint from the
    CPCV-eligible selection window."""

    def _partition(self, total_days: int) -> dict:
        """Reproduces run_autotuner's own 60/20/20 split formula
        (autotuner.py TRAIN_RATIO/VALIDATION_RATIO) so this test's
        definition of "the CPCV-eligible window" tracks the production
        formula automatically rather than a hand-typed literal."""
        import autotuner

        sorted_dates = [f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(total_days)]
        val_start_idx = int(total_days * autotuner.TRAIN_RATIO)
        frozen_start_idx = int(total_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO))
        return {
            "sorted_dates": sorted_dates,
            "cpcv_eligible_dates": set(sorted_dates[:frozen_start_idx]),
            "frozen_dates": set(sorted_dates[frozen_start_idx:]),
            "validation_dates_full": set(sorted_dates[val_start_idx:frozen_start_idx]),
        }

    def test_frozen_fold_is_disjoint_from_cpcv_eligible_window(self):
        """Sanity: the frozen fold (the natural holdout candidate,
        structurally excluded from _generate_cpcv_folds's eligible dates
        per autotuner.py's own _cpcv_eligible_dates = sorted_dates[:frozen_start_idx])
        is disjoint from the CPCV-eligible window by construction. This is
        the reference property a correct history_test-sourcing fix must
        satisfy -- true today of the frozen fold itself, false today of
        history_test (which uses validation_dates_full instead)."""
        parts = self._partition(250)
        overlap = parts["frozen_dates"] & parts["cpcv_eligible_dates"]
        assert not overlap, (
            f"Frozen fold overlaps the CPCV-eligible window by {len(overlap)} dates -- "
            "partition arithmetic bug in this test, not production."
        )

    def test_validation_dates_full_is_not_disjoint_from_cpcv_eligible_window(self):
        """Documents WHY today's history_test assignment is wrong: the
        validation fold is a SUBSET of (hence maximally overlapping with)
        the CPCV-eligible window -- the opposite of a holdout."""
        parts = self._partition(250)
        overlap = parts["validation_dates_full"] & parts["cpcv_eligible_dates"]
        assert overlap == parts["validation_dates_full"], (
            "validation_dates_full must be entirely contained within the CPCV-"
            "eligible window (documents the in-sample bug today's history_test "
            "assignment produces)."
        )


# ===========================================================================
# (2) Arm-symmetry: AI/fallback/default must be evaluated on the byte-
# identical history_test object -- no pro-adoption asymmetry. ALREADY TRUE
# today (confirmatory regression pin, protects the invariant through
# AC-2's fix).
# ===========================================================================


def test_ai_fallback_default_all_evaluated_on_the_same_history_test_variable() -> None:
    """All three run_simulation(...) calls that produce oos_alpha /
    fallback_oos_alpha / default_oos_alpha must pass the SAME history_test
    name as their history_data argument -- a structural guarantee against
    a future asymmetric change (e.g. accidentally favoring the AI arm with
    a larger or different window than the baselines see)."""
    run_autotuner_fn = _parse_run_autotuner_fn()

    run_sim_calls = [
        node
        for node in ast.walk(run_autotuner_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_simulation"
    ]
    # Expect exactly 3: AI (best_p), fallback, default.
    assert len(run_sim_calls) == 3, (
        f"Expected exactly 3 run_simulation(...) calls (AI/fallback/default) in "
        f"run_autotuner; found {len(run_sim_calls)}. If this count changed "
        "intentionally, update this test's expectation with the new call sites."
    )

    history_arg_names = []
    for call in run_sim_calls:
        # history_data is the 2nd positional arg (index 1) per
        # run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict).
        assert len(call.args) >= 2, f"run_simulation call at line {call.lineno} has < 2 args."
        second_arg = call.args[1]
        assert isinstance(second_arg, ast.Name), (
            f"run_simulation call at line {call.lineno}: history_data argument is not a "
            f"simple Name reference ({ast.dump(second_arg)}) -- cannot verify symmetry."
        )
        history_arg_names.append(second_arg.id)

    assert len(set(history_arg_names)) == 1, (
        f"run_simulation's history_data argument differs across the 3 adoption-cascade "
        f"calls: {history_arg_names!r}. AI, fallback, and default MUST be evaluated on "
        "the byte-identical window -- any asymmetry is a pro-adoption (or anti-adoption) "
        "bias in the OOS comparison."
    )


# ===========================================================================
# (3) Attestation-kill: the hardcoded purge_integrity_ok=True literal must
# be gone from the evaluate_acceptance_gate call site.
# ===========================================================================


def test_purge_integrity_ok_is_not_a_hardcoded_true_literal() -> None:
    """RED (pre-fix, confirmed live at autotuner.py:2842):
    ``purge_integrity_ok=True,  # purge invariant enforced at fold-build time above``
    is a FALSE ATTESTATION post-MA-2 -- the comment's claim ("enforced at
    fold-build time") was never actually verified at the call site; it is a
    bare literal, not a computed value. AC-2 requires this to be either (a)
    computed for real from the fold structure, or (b) removed with
    evaluate_acceptance_gate's signature + all its callers reconciled --
    either way, no hardcoded True literal may survive at this call site."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))

    gate_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "evaluate_acceptance_gate")
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "evaluate_acceptance_gate"
            )
        )
    ]
    assert gate_calls, "No evaluate_acceptance_gate(...) call found in autotuner.py."

    for call in gate_calls:
        for kw in call.keywords:
            if kw.arg == "purge_integrity_ok":
                is_hardcoded_true = isinstance(kw.value, ast.Constant) and kw.value.value is True
                assert not is_hardcoded_true, (
                    f"evaluate_acceptance_gate call at line {call.lineno} still passes "
                    "purge_integrity_ok=True as a hardcoded literal -- a false attestation "
                    "post-MA-2 (the comment's 'enforced at fold-build time' claim was never "
                    "actually checked here). Must be a computed expression, or the "
                    "call/signature must drop the kwarg entirely (with all callers "
                    "reconciled) if r2-stats's trace finds no real post-AC-1 purge role."
                )
