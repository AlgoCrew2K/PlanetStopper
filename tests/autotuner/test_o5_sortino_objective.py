"""
O5 — Composite Objective Replacement: RED tests.

These tests are written BEFORE the implementation. They will ALL FAIL
until the implementer replaces autotuner.py:219-229's ad-hoc composite
objective with a Sortino-ratio-based objective and exposes a
``compute_sortino_ratio(returns, target=0.0)`` helper at module scope.

Operator decision PA-5: Sortino (Sortino & van der Meer 1994, J. Portfolio
Management) is selected as the replacement metric. Rationale: AlphaBot is a
downside-overlay strategy with an asymmetric return distribution; Sortino
is better-matched than Sharpe for measuring risk-adjusted performance in
this regime.

Fixture provenance: tests/fixtures/autotuner/sortino_examples/
All expected Sortino values are hand-computed against the formula:
  Sortino = mean(r) / downside_deviation
  downside_deviation = sqrt(mean(min(r - target, 0)^2))
where the mean in the denominator divides by N (population denominator,
all observations), not just the count of downside observations.
This is the standard Sortino formulation (Sortino & van der Meer 1994).
"""

from __future__ import annotations

import ast
import importlib
import json
import math
import pathlib
import textwrap
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures" / "autotuner" / "sortino_examples"
)


def _load_fixture(filename: str) -> dict:
    path = _FIXTURE_DIR / filename
    with open(str(path), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Lazy autotuner import (avoid import-time Optuna side effects on Windows).
# ---------------------------------------------------------------------------

def _import_autotuner():
    import autotuner  # noqa: PLC0415
    return autotuner


# ===========================================================================
# Test 1 — compute_sortino_ratio: basic mixed returns fixture
# Mandatory RED test: test_objective_uses_sortino_formula
# ===========================================================================


def test_objective_uses_sortino_formula_basic_mixed():
    """
    Given a known returns series (fixture 01), assert compute_sortino_ratio
    returns the Sortino-equivalent value: mean / downside_deviation, where
    downside_deviation = sqrt(mean(min(r, 0)^2)) with population denominator.

    Fixture: [0.02, -0.01, 0.03, -0.02, 0.01]
    Expected Sortino: 0.6 (hand-computed; see fixture for full derivation).

    Tolerance: 1e-9 absolute. The formula is deterministic floating-point;
    any higher error indicates a wrong formula, not rounding.
    """
    fixture = _load_fixture("01_basic_mixed_returns.json")
    autotuner = _import_autotuner()

    returns = fixture["returns"]
    expected = fixture["calculation"]["expected_sortino"]

    result = autotuner.compute_sortino_ratio(returns, target=0.0)

    assert result == pytest.approx(expected, abs=1e-9), (
        f"Sortino formula mismatch on basic mixed fixture.\n"
        f"  returns={returns}\n"
        f"  expected={expected}\n"
        f"  got={result}\n"
        f"  Formula: mean(r)/sqrt(mean(min(r,0)^2)); population denominator."
    )


def test_objective_uses_sortino_formula_all_negative():
    """
    Given all-negative returns (fixture 03), assert compute_sortino_ratio
    returns the correct negative Sortino value.

    Fixture: [-0.01, -0.02, -0.03]
    Expected Sortino: -0.02 / sqrt(0.0014/3) ≈ -0.9258

    Tolerance: 1e-9 absolute.
    """
    fixture = _load_fixture("03_all_negative_returns.json")
    autotuner = _import_autotuner()

    returns = fixture["returns"]
    expected = fixture["calculation"]["expected_sortino"]

    result = autotuner.compute_sortino_ratio(returns, target=0.0)

    assert result == pytest.approx(expected, abs=1e-9), (
        f"Sortino formula mismatch on all-negative fixture.\n"
        f"  returns={returns}\n"
        f"  expected={expected}\n"
        f"  got={result}\n"
    )


def test_objective_uses_sortino_formula_single_negative():
    """
    Given a single negative return (fixture 04), assert compute_sortino_ratio
    returns -1.0 (mean=-0.05, downside_std=0.05 => ratio=-1.0).

    Tolerance: 1e-9 absolute.
    """
    fixture = _load_fixture("04_single_negative_return.json")
    autotuner = _import_autotuner()

    returns = fixture["returns"]
    expected = fixture["calculation"]["expected_sortino"]

    result = autotuner.compute_sortino_ratio(returns, target=0.0)

    assert result == pytest.approx(expected, abs=1e-9), (
        f"Sortino formula mismatch on single-negative fixture.\n"
        f"  expected={expected}, got={result}"
    )


# ===========================================================================
# Test 2 — No inline magic numbers in the new objective code
# Mandatory RED test: test_no_inline_magic_numbers_in_objective
# ===========================================================================


def test_no_inline_magic_numbers_in_objective():
    """
    Assert via AST inspection that the objective function body (the new
    Sortino-based code) contains no bare numeric literals corresponding to the
    five retired magic numbers: 1.0, 1.5, 0.75, 2.0, 0.015.

    Method: parse autotuner.py via ast, find the ``objective`` FunctionDef
    node, walk all Constant nodes in its subtree, assert none match the
    retired set.

    Named constants (e.g., ``SORTINO_TARGET_RETURN = 0.0``) at module scope
    are permitted; only BARE LITERALS inside the objective function body are
    rejected.

    The retired magic-number set:
      1.0  — missed-upside threshold (autotuner.py:220)
      1.5  — missed-upside multiplier (autotuner.py:221) AND peak threshold (225)
      0.75 — drawdown multiplier (autotuner.py:226)
      2.0  — negative guard-alpha amplifier (autotuner.py:230)
      0.015 — exponential decay rate (autotuner.py:79/217)
    """
    autotuner_path = pathlib.Path(__file__).parent.parent.parent / "autotuner.py"
    source = autotuner_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the objective FunctionDef. It is a nested function defined inside
    # run_autotuner; walk the entire module and find by name.
    objective_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "objective":
            objective_node = node
            break

    assert objective_node is not None, (
        "Could not locate 'objective' FunctionDef in autotuner.py. "
        "O5 may not have been implemented yet (expected RED)."
    )

    # The five retired magic number values. We check numeric equivalence
    # (not string repr) to catch 1.0, 1, and 1.00 as the same.
    RETIRED_MAGIC_NUMBERS = frozenset([1.0, 1.5, 0.75, 2.0, 0.015])

    violations: list[tuple[int, float]] = []
    for node in ast.walk(objective_node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            val = float(node.value)
            if val in RETIRED_MAGIC_NUMBERS:
                violations.append((node.lineno, node.value))

    assert not violations, (
        f"Found retired magic-number literals inside objective() function.\n"
        f"Violations (line, value): {violations}\n"
        f"Move these to named constants with source comments at module scope."
    )


# ===========================================================================
# Test 3 — target_return is zero by default
# Mandatory RED test: test_target_return_is_zero_by_default
# ===========================================================================


def test_target_return_is_zero_by_default():
    """
    Sortino's denominator measures deviation below a TARGET return. AlphaBot
    uses target=0 (capital preservation baseline). Pin that
    compute_sortino_ratio with no explicit target argument uses target=0.

    Fixture 01: calling without target kwarg must give same result as
    calling with target=0.0 explicitly.
    """
    fixture = _load_fixture("01_basic_mixed_returns.json")
    autotuner = _import_autotuner()

    returns = fixture["returns"]

    result_default = autotuner.compute_sortino_ratio(returns)
    result_explicit = autotuner.compute_sortino_ratio(returns, target=0.0)

    assert result_default == pytest.approx(result_explicit, abs=1e-9), (
        "compute_sortino_ratio() default target must equal target=0.0 behavior. "
        f"default={result_default}, explicit={result_explicit}"
    )

    # Cross-check: a non-zero target produces a different result, confirming
    # the parameter is actually consumed (not dead code).
    result_nonzero_target = autotuner.compute_sortino_ratio(returns, target=0.01)
    assert result_default != pytest.approx(result_nonzero_target, abs=1e-6), (
        "Non-zero target must produce a different Sortino value, confirming "
        "the target parameter is not dead code."
    )


# ===========================================================================
# Test 4 — downside deviation excludes returns at or above target
# Mandatory RED test: test_downside_deviation_excludes_above_target_returns
# ===========================================================================


def test_downside_deviation_excludes_above_target_returns():
    """
    Returns >= target (including returns exactly equal to target=0.0) must
    NOT contribute to the downside deviation denominator.

    Fixture 05: [0.0, -0.01, 0.01], target=0.0
    Only -0.01 is strictly below target. The 0.0 return is excluded (strict
    inequality r < target). Expected downside_deviation = sqrt(0.0001/3)
    (population denominator: divide by N=3, not N_downside=1).
    Expected Sortino = 0.0 / downside_deviation = 0.0 (mean=0.0).
    """
    fixture = _load_fixture("05_boundary_zero_return.json")
    autotuner = _import_autotuner()

    returns = fixture["returns"]
    expected_sortino = fixture["calculation"]["expected_sortino"]
    expected_downside_std = fixture["calculation"]["downside_std"]

    result = autotuner.compute_sortino_ratio(returns, target=0.0)

    assert result == pytest.approx(expected_sortino, abs=1e-9), (
        f"Sortino with zero mean must be 0.0.\n"
        f"  returns={returns}\n"
        f"  expected={expected_sortino}, got={result}"
    )

    # Confirm the denominator is non-zero (only -0.01 contributed, so it
    # should be sqrt(0.0001/3) ≈ 0.00577, not 0).
    assert expected_downside_std == pytest.approx(
        math.sqrt(0.0001 / 3), abs=1e-12
    ), "Fixture self-consistency check failed (fixture 05 downside_std)."

    # Additional property: adding a return exactly at target=0 does NOT
    # change the Sortino ratio versus the returns with 0.0 omitted.
    returns_without_zero = [-0.01, 0.01]
    result_without_zero = autotuner.compute_sortino_ratio(
        returns_without_zero, target=0.0
    )
    # With [-0.01, 0.01]: mean=0.0, downside_std=sqrt(0.0001/2) ≠ sqrt(0.0001/3)
    # They differ because N differs (population denominator). This confirms
    # that the 0.0 return participates in N but not in the downside_sq sum.
    assert result != pytest.approx(result_without_zero, abs=1e-6), (
        "Adding a return at target=0 changes N (population denominator) and "
        "therefore changes downside_std, so the ratio must differ. This confirms "
        "the population denominator is used, not the downside-only count."
    )


# ===========================================================================
# Test 5 — objective signature compatible with Optuna trial
# Mandatory RED test: test_objective_signature_compat_with_optuna_trial
# ===========================================================================


def test_objective_signature_compat_with_optuna_trial():
    """
    The new Sortino-based objective must:
    1. Still accept a single ``trial`` positional argument (Optuna contract).
    2. Return a single Python float (Optuna TPE sampler receives a scalar).
    3. The returned value must be finite (not NaN or inf) for non-degenerate
       inputs, so TPE can model it.

    We drive this via a minimal run_autotuner invocation with a single-tick
    fixture where at least one trigger fires (so the returns list is non-empty
    and the Sortino computation runs). The fake Optuna study's optimize() is
    intercepted so the objective is called exactly once with a real trial mock.
    """
    import contextlib
    import io

    autotuner = _import_autotuner()

    # Capture calls to the objective function via a spy on study.optimize.
    objective_call_results: list[float] = []
    objective_call_args: list[object] = []

    def capture_objective(objective_fn, **kwargs):
        # Call the objective with a minimal fake trial that satisfies
        # all suggest_float / suggest_int calls.
        fake_trial = MagicMock()
        fake_trial.suggest_float.side_effect = lambda name, lo, hi, **kw: (lo + hi) / 2
        fake_trial.suggest_int.side_effect = lambda name, lo, hi, **kw: (lo + hi) // 2
        result = objective_fn(fake_trial)
        objective_call_results.append(result)
        objective_call_args.append(fake_trial)

    fake_study = MagicMock()
    fake_study.best_params = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(side_effect=capture_objective)

    bot_state = {"sym-A": {"name": "Test Symphony", "account_uuid": "acc-1"}}
    default_params = fake_study.best_params.copy()

    # Single-tick history with a trigger-generating scenario (vwap_diff negative,
    # so the VWAP breakdown stub can fire).
    history = {
        "sym-A": {
            "2026-04-01": [{"return": 2.0, "mc_prob": 50.0, "vol": 1.0,
                            "vwap_diff": -2.0, "base_atr_pct": 1.0,
                            "valid_vwap_weight": 1.0}],
            "2026-04-02": [{"return": 2.0, "mc_prob": 50.0, "vol": 1.0,
                            "vwap_diff": -2.0, "base_atr_pct": 1.0,
                            "valid_vwap_weight": 1.0}],
        }
    }

    def vwap_always_triggers(**kwargs):
        return (0, 0, True, False)

    buf = io.StringIO()
    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history",
              return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch("autotuner.database.get_symphony_strategy",
              return_value={"params": default_params.copy(), "locked_vars": []}),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", default_params),
        patch("autotuner.math_engine.compute_para_arm_decision",
              side_effect=lambda **kw: (0.0, False)),
        patch("autotuner.math_engine.compute_time_squeeze_decay",
              side_effect=lambda tr: (1.5, 0.5)),
        patch("autotuner.math_engine.compute_active_trailing_stop",
              side_effect=lambda *a, **kw: 5.0),
        patch("autotuner.math_engine.compute_breakeven_update",
              side_effect=lambda r, v, b, h, bl, it: (h, bl, b)),
        patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
              side_effect=lambda v, m: -10.0),
        patch("autotuner.math_engine.compute_vwap_breakdown_update",
              side_effect=vwap_always_triggers),
        contextlib.redirect_stdout(buf),
    ):
        autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

    assert len(objective_call_results) == 1, (
        f"Expected objective() to be called exactly once via study.optimize; "
        f"got {len(objective_call_results)} calls."
    )

    result = objective_call_results[0]

    # Must return a finite float (Optuna TPE requires a scalar, not inf/NaN).
    assert isinstance(result, (int, float)), (
        f"objective() must return a numeric float; got {type(result).__name__}"
    )
    assert math.isfinite(result), (
        f"objective() returned non-finite value {result!r}. Optuna TPE "
        f"cannot model NaN or inf. For all-positive returns use a large "
        f"positive sentinel (e.g. 1e6) instead of float('inf')."
    )

    # The trial mock must have been called with suggest_float / suggest_int,
    # confirming the objective accepted a trial argument and exercised it.
    assert objective_call_args[0].suggest_float.called, (
        "objective() did not call trial.suggest_float — signature may have "
        "dropped the trial parameter."
    )


# ===========================================================================
# Test 6 — named constants have source comments
# Mandatory RED test: test_named_constants_have_source_comments
# ===========================================================================


def test_named_constants_have_source_comments():
    """
    Any constants retained from the old objective (e.g., an annualization
    factor if used) OR any new named constant introduced in O5 must have a
    source-citation comment per the no-magic-numbers rule in project CLAUDE.md.

    Method: parse autotuner.py with ast and tokenize, find module-level
    Assign nodes whose targets match the expected constant names, then verify
    a comment appears on the same or preceding line in the token stream.

    Constants the implementer MUST define with source comments:
      SORTINO_TARGET_RETURN  — the target return (0.0); source: operator
                               decision PA-5, capital preservation baseline.
    """
    import tokenize
    import io as _io

    autotuner_path = pathlib.Path(__file__).parent.parent.parent / "autotuner.py"
    source = autotuner_path.read_text(encoding="utf-8")

    # Collect all COMMENT tokens keyed by line number.
    comments_by_line: dict[int, str] = {}
    tokens = tokenize.generate_tokens(_io.StringIO(source).readline)
    for tok_type, tok_string, tok_start, _tok_end, _line in tokens:
        if tok_type == tokenize.COMMENT:
            comments_by_line[tok_start[0]] = tok_string

    tree = ast.parse(source)

    # Find module-level assignments (top-level or within module body).
    module_assigns: dict[str, int] = {}  # name -> lineno
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # Only top-level assigns (direct children of Module body)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_assigns[target.id] = node.lineno

    # SORTINO_TARGET_RETURN must be defined at module scope.
    assert "SORTINO_TARGET_RETURN" in module_assigns, (
        "autotuner.py must define SORTINO_TARGET_RETURN as a named module-level "
        "constant (e.g., SORTINO_TARGET_RETURN = 0.0) with a source comment. "
        "This is required by the no-magic-numbers rule (project CLAUDE.md)."
    )

    # The constant must have a comment on the same or immediately preceding line.
    lineno = module_assigns["SORTINO_TARGET_RETURN"]
    has_comment = (
        lineno in comments_by_line
        or (lineno - 1) in comments_by_line
    )
    assert has_comment, (
        f"SORTINO_TARGET_RETURN at line {lineno} has no source-citation comment "
        f"on its definition line or the immediately preceding line.\n"
        f"Add a comment citing: operator decision PA-5, Sortino & van der "
        f"Meer 1994, J. Portfolio Management — capital preservation baseline."
    )


# ===========================================================================
# Bonus — all_positive returns: downside_deviation = 0 → handled gracefully
# ===========================================================================


def test_compute_sortino_ratio_all_positive_returns_is_positive():
    """
    When all returns are above the target, downside deviation = 0.
    The implementation must not raise ZeroDivisionError; it must return a
    positive value (float('inf') or a large positive sentinel such as 1e6).

    Fixture 02: [0.01, 0.02, 0.03], target=0.0.
    """
    fixture = _load_fixture("02_all_positive_returns.json")
    autotuner = _import_autotuner()

    returns = fixture["returns"]

    # Must not raise
    result = autotuner.compute_sortino_ratio(returns, target=0.0)

    assert result > 0, (
        f"All-positive returns must yield a positive Sortino value; got {result}"
    )
    # Must be finite so Optuna TPE can model it (inf is allowed here at the
    # helper level; the objective layer is responsible for clamping to finite).
    # We only assert positivity — the sentinel choice is implementation-defined.


# ===========================================================================
# Bonus — empty returns list: no triggers fired in OOS fold
# ===========================================================================


def test_compute_sortino_ratio_empty_returns_returns_zero():
    """
    When run_simulation returns an empty list (no triggers fired on any day
    in the OOS fold), compute_sortino_ratio must return 0.0 rather than
    raising ZeroDivisionError or returning NaN.

    Rationale: an OOS fold with no exits means the strategy held through the
    entire period without being triggered. There is no return signal to measure.
    Returning 0.0 (neutral) is the correct sentinel — it does not reward a
    parameter set for simply never triggering (which could be achieved by
    setting extremely permissive thresholds), nor does it punish it.

    The empty-list case arises naturally when the implementer changes
    run_simulation to return List[float] and an OOS fold produces no exits.
    """
    autotuner = _import_autotuner()

    result = autotuner.compute_sortino_ratio([], target=0.0)

    assert result == pytest.approx(0.0, abs=1e-9), (
        f"compute_sortino_ratio([]) must return 0.0 (no-exit neutral sentinel); "
        f"got {result!r}. Must not raise ZeroDivisionError or return NaN."
    )
    assert math.isfinite(result), (
        f"compute_sortino_ratio([]) must return a finite float; got {result!r}"
    )
