"""
RED — AC-1/AC-3 (exit-friction-realized-savings): SIM_EXIT_FRICTION_PCT.

AC-1 (AMENDED 2026-07-24, recon Finding E — units): a named, module-scope
constant in autotuner.py (NOT math_engine.py — that file is imported by the
live engine; this is a replay-environment parameter). Value MUST be 0.5
(percentage points), derived as
``composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT (0.005, a decimal
fraction = 0.5%) * autotuner.RETURN_PCT_TO_FRACTION (100.0)`` -- NOT the bare
0.005 literal, which would apply 100x too little friction at the
percent-point-scaled subtraction sites (AC-2v2). Source comment must cite
both the Composer client assumption and the validation report.

AC-3: the constant must never be tunable -- absent from
OPTUNA_SEARCH_SPACE_KEYS, never read from the per-trial params dict ``p``,
never env-tunable. A tunable friction would let the optimizer game its own
cost model.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"
_MATH_ENGINE_SRC = _WORKTREE_ROOT / "math_engine.py"


def _import_autotuner():
    import autotuner

    return autotuner


# ---------------------------------------------------------------------------
# AC-1: existence, placement, value, units
# ---------------------------------------------------------------------------


def test_sim_exit_friction_pct_exists_at_autotuner_module_scope():
    """AC-1: SIM_EXIT_FRICTION_PCT is a module-level attribute of autotuner.py."""
    autotuner = _import_autotuner()
    assert hasattr(autotuner, "SIM_EXIT_FRICTION_PCT"), (
        "autotuner.SIM_EXIT_FRICTION_PCT must exist as a module-level constant "
        "(AC-1). Not found -- feature not yet implemented."
    )


def test_sim_exit_friction_pct_not_defined_in_math_engine():
    """AC-1 (RULED 2026-07-24): the constant lives in autotuner.py, not
    math_engine.py -- math_engine is imported by the LIVE engine
    (alpha_bot_execution.py); this constant is a replay-only parameter and
    must stay structurally out of the file the live decision path imports
    from, per AC-10's blast-radius intent.
    """
    import math_engine

    assert not hasattr(math_engine, "SIM_EXIT_FRICTION_PCT"), (
        "SIM_EXIT_FRICTION_PCT must NOT be defined in math_engine.py -- that "
        "module is imported by the live engine's decision path. It belongs "
        "in autotuner.py module scope (AC-1 ruling, 2026-07-24)."
    )


def test_sim_exit_friction_pct_is_a_float():
    autotuner = _import_autotuner()
    value = getattr(autotuner, "SIM_EXIT_FRICTION_PCT", None)
    assert isinstance(value, float), (
        f"SIM_EXIT_FRICTION_PCT must be a float, got {type(value).__name__}: {value!r}."
    )


def test_sim_exit_friction_pct_units_are_percentage_points_not_decimal_fraction():
    """AC-1 units contract (Finding E, CRITICAL).

    composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT is a DECIMAL FRACTION
    (0.005 = 0.5%, per its own docstring). autotuner's exit-accounting sites
    (AC-2v2) operate in PERCENT-POINT space: tick['return'] is percent-scaled
    (autotuner.RETURN_PCT_TO_FRACTION == 100.0 converts percent -> fraction),
    and the sibling deviation-dict penalty defaults to -0.20 (percentage
    points), not -0.002. SIM_EXIT_FRICTION_PCT must therefore equal
    _DEFAULT_SLIPPAGE_PERCENT * RETURN_PCT_TO_FRACTION == 0.5, NOT the bare
    0.005 -- a bare 0.005 would apply 100x too little friction at the
    subtraction site, silently defeating the feature while looking shipped.

    The expected value is DERIVED from the two real producer constants, never
    hardcoded as a bare literal (house test rule).
    """
    from advisors import composer_backtest_client

    autotuner = _import_autotuner()

    expected = composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT * autotuner.RETURN_PCT_TO_FRACTION

    assert autotuner.SIM_EXIT_FRICTION_PCT == pytest.approx(expected, abs=1e-9), (
        f"SIM_EXIT_FRICTION_PCT must equal _DEFAULT_SLIPPAGE_PERCENT "
        f"({composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT}) * "
        f"RETURN_PCT_TO_FRACTION ({autotuner.RETURN_PCT_TO_FRACTION}) = {expected} "
        f"percentage points, got {autotuner.SIM_EXIT_FRICTION_PCT}. A value of "
        f"0.005 here (the Composer client's fraction, uncorrected) would apply "
        f"100x too little friction at the percent-point-scaled subtraction site."
    )

    # Sanity guard against the exact wrong-unit trap: the bare fraction must
    # NOT be what shipped.
    assert autotuner.SIM_EXIT_FRICTION_PCT != pytest.approx(
        composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT, abs=1e-9
    ), (
        "SIM_EXIT_FRICTION_PCT must NOT equal the raw Composer client fraction "
        "(0.005) -- that is a decimal-fraction value copied without converting "
        "to this file's percent-point convention (Finding E)."
    )


def test_sim_exit_friction_pct_source_comment_cites_composer_client_and_validation_report():
    """AC-1: the constant's source comment must cite both provenance sources.

    Structural check via the raw source text (not AST, since comments are not
    part of the AST) -- the comment block immediately preceding or on the
    same logical assignment must mention the Composer client constant name
    and the validation-report doc path.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    assert "SIM_EXIT_FRICTION_PCT" in src, (
        "SIM_EXIT_FRICTION_PCT assignment not found in autotuner.py."
    )

    # Locate the assignment line and look at a window of preceding comment
    # lines (the house convention for named-constant provenance comments,
    # e.g. STAGE1_SNAPSHOT_CUTOFF_ET in reporting.py).
    lines = src.splitlines()
    assign_idx = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("SIM_EXIT_FRICTION_PCT")),
        None,
    )
    assert assign_idx is not None, "Could not locate the SIM_EXIT_FRICTION_PCT assignment line."

    window_start = max(0, assign_idx - 15)
    window = "\n".join(lines[window_start : assign_idx + 1])

    assert "_DEFAULT_SLIPPAGE_PERCENT" in window or "composer_backtest_client" in window, (
        "SIM_EXIT_FRICTION_PCT's source comment must cite the Composer backtest "
        "client's slippage assumption (_DEFAULT_SLIPPAGE_PERCENT / "
        "composer_backtest_client) -- AC-1 provenance requirement."
    )
    assert "methodology-validation" in window, (
        "SIM_EXIT_FRICTION_PCT's source comment must cite the validation report "
        "(docs/research/methodology-validation-2026-07.md) whose gap this "
        "constant closes -- AC-1 provenance requirement."
    )


# ---------------------------------------------------------------------------
# AC-3: non-tunability
# ---------------------------------------------------------------------------


def test_sim_exit_friction_pct_absent_from_optuna_search_space():
    """AC-3: SIM_EXIT_FRICTION_PCT must never be a suggestible Optuna param."""
    autotuner = _import_autotuner()
    assert "SIM_EXIT_FRICTION_PCT" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "SIM_EXIT_FRICTION_PCT must NOT be a member of OPTUNA_SEARCH_SPACE_KEYS "
        "-- a tunable friction constant would let the optimizer game its own "
        "cost model (AC-3)."
    )


def test_sim_exit_friction_pct_never_read_from_trial_params_dict():
    """AC-3: no call site may read the friction value via p.get("SIM_EXIT_FRICTION_PCT", ...).

    AST scan for any ``p.get("SIM_EXIT_FRICTION_PCT", ...)`` (or
    ``params.get(...)`` / subscript ``p["SIM_EXIT_FRICTION_PCT"]``) call
    pattern anywhere in autotuner.py -- the constant must be referenced
    directly as ``SIM_EXIT_FRICTION_PCT`` / ``autotuner.SIM_EXIT_FRICTION_PCT``,
    never sourced from the per-trial strategy-params dict.
    """
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))

    offending: list[int] = []
    for node in ast.walk(tree):
        # p.get("SIM_EXIT_FRICTION_PCT", ...) / params.get(...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "SIM_EXIT_FRICTION_PCT"
        ):
            offending.append(node.lineno)
        # p["SIM_EXIT_FRICTION_PCT"]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "SIM_EXIT_FRICTION_PCT"
        ):
            offending.append(node.lineno)

    assert not offending, (
        f"SIM_EXIT_FRICTION_PCT must never be read from a per-trial params "
        f"dict (p.get(...) / p[...]) -- found dict-style access at line(s) "
        f"{offending}. It is a pure module constant (AC-3)."
    )


def test_sim_exit_friction_pct_never_read_from_environment():
    """AC-3: not env-tunable -- no os.environ access keyed on this name."""
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    assert 'environ.get("SIM_EXIT_FRICTION_PCT"' not in src, (
        "SIM_EXIT_FRICTION_PCT must not be read from an environment variable "
        "-- AC-3 requires it to be a fixed, non-operator-tunable-at-runtime "
        "constant (only a code change may adjust it)."
    )
    assert "SIM_EXIT_FRICTION_PCT" not in _MATH_ENGINE_SRC.read_text(encoding="utf-8"), (
        "SIM_EXIT_FRICTION_PCT must not appear anywhere in math_engine.py "
        "(AC-1 placement + AC-10 blast-radius intent)."
    )


def test_validate_search_space_nn1_does_not_need_sim_exit_friction_pct_forbidden_entry():
    """Sanity: SIM_EXIT_FRICTION_PCT is not a THEORY/CADENCE/MANDATE/CALIBRATION
    spec-facet name either -- it's a replay cost parameter, not a frozen
    spec facet, so it should not appear in validate_search_space_nn1's
    forbidden-facet set. This guards against a future maintainer conflating
    "non-tunable" with "spec-frozen facet" and wiring it into the wrong
    guard.
    """
    autotuner = _import_autotuner()
    # validate_search_space_nn1 must not raise merely because
    # SIM_EXIT_FRICTION_PCT exists at module scope -- it is unrelated to the
    # NN1 spec-freeze facet set.
    autotuner.validate_search_space_nn1()
