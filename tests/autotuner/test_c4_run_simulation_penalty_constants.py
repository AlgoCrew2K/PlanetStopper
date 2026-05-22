"""
Cluster 4 — AC-4 (audit finding H-10): every run_simulation penalty scalar and
threshold becomes a named, sourced module constant; the objective is documented
as an explicit loss-averse utility; a fixture-backed test proves the combined
objective preserves correct ordering on a known better-vs-worse policy pair.

RED tests, written before the implementation.

Audit finding H-10: five unsourced numbers shape the run_simulation objective
(autotuner.py:653-666):
  - missed-upside multiplier 1.5, applied above threshold 1.0
  - drawdown multiplier 0.75, applied above threshold 1.5
  - negative-guard-alpha multiplier 2.0
None is named or sourced. The negative-guard-alpha 2.0 makes the objective
asymmetric (loss aversion); missed_upside and drawdown are both functions of
triggered_return, so the same exit event is penalised up to three times through
three lenses — a policy-inverting interaction.

AC-4 requires:
  - every scalar AND threshold a named module constant with a sourced rationale
    comment,
  - the objective documented as an explicit loss-averse utility,
  - a fixture-backed ordering-preservation test.

The named-constant tests below FAIL on RED (the literals are still inline). The
ordering-preservation test is a regression guard — it must hold before and after
the refactor (an extraction that preserves behaviour keeps the ordering; a
refactor that silently changes a scalar and inverts a dominated pair fails it).

Fixture provenance: tests/fixtures/math/run_simulation_objective_ordering.json —
a hand-constructed dominated policy pair; the test derives the expected ORDERING
from the documented objective contract, never from a producer output.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"
_FIXTURE = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math"
    / "run_simulation_objective_ordering.json"
)

# The five magic numbers finding H-10 names. After AC-4 none of these may appear
# as a bare numeric literal inside the run_simulation function body.
_PENALTY_LITERALS = {1.5, 0.75, 2.0, 1.0}
# (thresholds 1.0 and 1.5 + scalars 1.5 / 0.75 / 2.0 — 1.5 is both a scalar and
#  a threshold; the set is the distinct numeric values.)


def _import_autotuner():
    import autotuner
    return autotuner


def _run_simulation_func_node() -> ast.FunctionDef:
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "run_simulation"),
        None,
    )
    assert func is not None, "run_simulation not found in autotuner.py."
    return func


def _load_fixture() -> dict:
    with open(str(_FIXTURE), encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# AC-4 — penalty scalars/thresholds are named module constants
# ===========================================================================


def test_run_simulation_body_has_no_bare_penalty_literals():
    """
    AC-4: the run_simulation function body must contain NO bare FLOAT literal
    from the H-10 set {1.5, 0.75, 2.0, 1.0} in its penalty block.

    AST inspection: collect every ast.Constant whose value is a `float` inside
    the run_simulation FunctionDef and assert none equals a penalty literal.
    After AC-4 these values live in named module constants referenced by name.

    Scope note: the five H-10 penalty scalars/thresholds are all written as
    FLOAT literals in source (1.5, 0.75, 2.0, 1.0). Structural INTEGER literals —
    a `[-1]` index, a loop bound, a tick count — are a different syntactic class
    (`int`, not `float`) and are intentionally NOT flagged: `ticks[-1]` parses as
    UnaryOp(USub, Constant(int 1)) and must not be confused with the float
    threshold 1.0. Restricting the scan to `float` constants cleanly separates
    the penalty literals from structural integers; `bool` is excluded explicitly
    (a Python bool is an int subclass, never a penalty literal).
    """
    func = _run_simulation_func_node()

    offending: list[str] = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, float)  # penalty literals are floats; ints are structural
            and node.value in _PENALTY_LITERALS
        ):
            offending.append(
                f"line {getattr(node, 'lineno', '?')}: float literal {node.value!r}"
            )

    assert not offending, (
        "run_simulation still contains bare penalty literals from the H-10 set "
        "{1.5, 0.75, 2.0, 1.0}. AC-4 requires each scalar and threshold to be a "
        "named module constant with a sourced rationale comment. Offending "
        "literals:\n  " + "\n  ".join(offending)
    )


def test_penalty_constants_are_named_at_module_scope():
    """
    AC-4: the missed-upside multiplier, missed-upside threshold, drawdown
    multiplier, drawdown threshold, and negative-guard-alpha multiplier must each
    exist as a named module-level constant in autotuner.py.

    This test does NOT pin the constant VALUES — it pins that named constants
    exist (a value pin would hardcode a producer-chosen number, forbidden by
    feedback_no_hardcoded_test_values). It checks five module-level assignments
    whose names describe the five distinct penalty roles.

    The implementer chooses the exact names; this test accepts any module-level
    constant whose UPPER-CASE name contains the role keywords below. If the
    implementer's naming differs, this test is the place to reconcile it.
    """
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))

    module_const_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.isupper()
    }

    # Five required roles. Each entry: a human label + the keyword sets, ANY of
    # which (all keywords present) identifies a constant for that role.
    required_roles = {
        "missed-upside multiplier": [
            {"MISSED", "UPSIDE", "MULT"},
            {"MISSED", "UPSIDE", "PENALTY"},
            {"MISSED", "UPSIDE", "WEIGHT"},
        ],
        "missed-upside threshold": [
            {"MISSED", "UPSIDE", "THRESHOLD"},
            {"MISSED", "UPSIDE", "MIN"},
            {"MISSED", "UPSIDE", "FLOOR"},
        ],
        "drawdown multiplier": [
            {"DRAWDOWN", "MULT"},
            {"DRAWDOWN", "PENALTY"},
            {"DRAWDOWN", "WEIGHT"},
        ],
        "drawdown threshold": [
            {"DRAWDOWN", "THRESHOLD"},
            {"DRAWDOWN", "MIN"},
            {"DRAWDOWN", "FLOOR"},
        ],
        "negative-guard-alpha multiplier": [
            {"NEGATIVE", "MULT"},
            {"LOSS", "AVERSION"},
            {"LOSS", "AVERSE"},
            {"NEGATIVE", "GUARD", "ALPHA"},
            {"DOWNSIDE", "MULT"},
        ],
    }

    missing: list[str] = []
    for role, keyword_sets in required_roles.items():
        matched = any(
            all(kw in name for kw in kwset)
            for name in module_const_names
            for kwset in keyword_sets
        )
        if not matched:
            missing.append(role)

    assert not missing, (
        f"autotuner.py is missing named module constants for these run_simulation "
        f"penalty roles: {missing}.\n"
        f"AC-4 requires every penalty scalar and threshold to be a named module "
        f"constant. Module UPPER-case names found: {sorted(module_const_names)}.\n"
        f"If your constant names differ from the keyword heuristics here, update "
        f"this test's `required_roles` map to match — but the constants must exist."
    )


def test_penalty_constants_carry_sourced_rationale_comments():
    """
    AC-4: each penalty constant must carry a sourced rationale comment — project
    rule (no magic numbers; every constant named + source comment) and finding
    H-10 specifically (the scalars must be documented as an explicit loss-averse
    utility).

    Heuristic source check: the autotuner.py text near the run_simulation penalty
    constants must contain rationale language. We require the phrase "loss-averse"
    (or "loss aversion") to appear — finding H-10's exact remediation wording ("the
    objective is documented as an explicit loss-averse utility").
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8").lower()

    assert "loss-averse" in src or "loss aversion" in src or "loss averse" in src, (
        "autotuner.py must document the run_simulation objective as an explicit "
        "loss-averse utility (audit finding H-10). The phrase 'loss-averse' / "
        "'loss aversion' was not found anywhere in the module."
    )


# ===========================================================================
# AC-4 — ordering-preservation regression guard
# ===========================================================================


def test_objective_preserves_ordering_on_dominated_policy_pair():
    """
    AC-4: the combined run_simulation objective must rank a strictly-dominated
    worse policy below a better one.

    The fixture pair (run_simulation_objective_ordering.json) is constructed so
    the BAD policy is worse on ALL THREE penalty axes simultaneously (guard-alpha,
    missed upside, peak-to-exit drawdown). No legitimate choice of positive
    penalty scalars can rank BAD better — so this is a robust regression guard
    against the finding H-10 inversion.

    Objective contract (from the fixture): run_simulation returns -total_guard_alpha
    and the OOS cascade computes oos_alpha = -run_simulation(...). A HIGHER
    oos_alpha is a better policy, therefore a LOWER run_simulation return is the
    better policy. The test asserts:
        run_simulation(GOOD) < run_simulation(BAD)   (strictly)

    Each policy is one simulated day fed through run_simulation with the
    math_engine exit helpers stubbed so the exit fires on the fixture's chosen
    tick_idx. NO run_simulation output value is hardcoded — only the relative
    ordering, derived from the documented objective contract.
    """
    from unittest.mock import patch

    autotuner = _import_autotuner()
    fixture = _load_fixture()

    assert fixture["objective_contract"]["lower_is_better"] is True, (
        "Fixture objective_contract changed — re-derive the ordering direction."
    )

    deviation_dict = {
        k: v for k, v in fixture["deviation_dict"].items() if k != "comment"
    }
    current_date = fixture["current_date"]
    scenario_date = fixture["scenario_date"]
    pair = fixture["pairs"][0]

    def _run_policy(ticks: list, exit_tick_idx: int) -> float:
        """Drive run_simulation for one day; force the exit on exit_tick_idx."""
        history = {"sym-A": {scenario_date: ticks}}

        def _vwap_trigger_on_exit_tick(**kwargs):
            # compute_vwap_breakdown_update is called once per tick; the replay
            # passes weighted_vwap_diff through from the tick. The fixture marks
            # the exit tick with vwap_diff == -9.9; trigger only on that tick.
            if kwargs.get("weighted_vwap_diff", 0.0) <= -9.0:
                return (0, 0, True, False)
            return (0, 0, False, False)

        with (
            patch("autotuner.math_engine.compute_para_arm_decision",
                  side_effect=lambda **kw: (0.0, False)),
            patch("autotuner.math_engine.compute_time_squeeze_decay",
                  side_effect=lambda tr: (1.5, 0.5)),
            patch("autotuner.math_engine.compute_active_trailing_stop",
                  side_effect=lambda *a, **kw: 5.0),
            patch("autotuner.math_engine.compute_breakeven_update",
                  side_effect=lambda *a, **kw: (a[3], a[4], a[2])),
            patch("autotuner.math_engine.compute_tp_confirmation",
                  side_effect=lambda **kw: (kw.get("tp_armed", False), 0, False)),
            patch("autotuner.math_engine.compute_exit_confirmation",
                  side_effect=lambda **kw: (0, False)),
            patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
                  side_effect=lambda *a, **kw: -100.0),
            patch("autotuner.math_engine.compute_vwap_breakdown_update",
                  side_effect=_vwap_trigger_on_exit_tick),
            patch("autotuner._replay_grace_minutes", return_value=0),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return autotuner.run_simulation(
                {}, history, ["sym-A"], current_date, deviation_dict
            )

    good_result = _run_policy(
        pair["good_policy_ticks"], pair["good_policy_exit_tick_idx"]
    )
    bad_result = _run_policy(
        pair["bad_policy_ticks"], pair["bad_policy_exit_tick_idx"]
    )

    assert good_result < bad_result, (
        f"OBJECTIVE INVERSION (finding H-10): run_simulation ranked the "
        f"strictly-dominated BAD policy at least as well as the GOOD policy.\n"
        f"  pair: {pair['name']}\n"
        f"  run_simulation(GOOD) = {good_result!r}\n"
        f"  run_simulation(BAD)  = {bad_result!r}\n"
        f"  Lower run_simulation return == better policy (oos_alpha = -run_simulation).\n"
        f"  BAD is worse on guard-alpha, missed-upside AND drawdown — no positive "
        f"scalar choice can legitimately rank it better. The penalty constants "
        f"must preserve ordering on a dominated pair."
    )
