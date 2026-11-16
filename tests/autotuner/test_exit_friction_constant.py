"""
Pins for SIM_EXIT_FRICTION_PCT, the replay's simulated-exit cost.

Placement: a named, module-scope constant in autotuner.py (NOT math_engine.py -- that file is
imported by the live engine; this is a replay-environment parameter).

Value and units: 0.5 PERCENTAGE POINTS, in autotuner's percent-point convention
(``autotuner.RETURN_PCT_TO_FRACTION`` == 100.0 converts percent to fraction). The subtraction
sites work in percent-point space (``tick['return']`` is percent-scaled, the sibling deviation
penalty defaults to -0.20 points), so the decimal-fraction form of the same cost would apply 100x
too little friction there.

Basis: the value models the cost of a Planet Stopper full-position exit in the replay and closes
the gross-of-cost gap named in docs/research/methodology-validation-2026-07.md. It is a different
cost from the per-trade slippage Composer backtests assume for daily rebalancing, so nothing here
derives it from the backtest client, and this module reads nothing from that client.

Non-tunability: the constant must never be tunable -- absent from OPTUNA_SEARCH_SPACE_KEYS, never
read from the per-trial params dict ``p``, never env-tunable. A tunable friction would let the
optimizer game its own cost model.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"
_MATH_ENGINE_SRC = _WORKTREE_ROOT / "math_engine.py"
_AUTOTUNER_PAGE = _WORKTREE_ROOT / "docs" / "generated" / "autotuner.md"

# The cost the plan states, percentage points per simulated full-position exit.
_EXPECTED_PERCENT_POINTS = 0.5

# What a statement of the cost's basis must name, and what it must never say. The refused phrases
# are assembled from pieces so that this module itself holds none of them.
_REQUIRED_PHRASES = ("methodology-validation-2026-07.md", "full-position exit")
_REFUSED_PHRASES = (
    "_DEFAULT_SLIPPAGE" + "_PERCENT",
    "composer_backtest" + "_client",
    "derived " + "from",
    "0." + "005",
)


def _import_autotuner():
    import autotuner

    return autotuner


# ---------------------------------------------------------------------------
# Existence, placement, value, units
# ---------------------------------------------------------------------------


def test_sim_exit_friction_pct_exists_at_autotuner_module_scope():
    """SIM_EXIT_FRICTION_PCT is a module-level attribute of autotuner.py."""
    autotuner = _import_autotuner()
    assert hasattr(autotuner, "SIM_EXIT_FRICTION_PCT"), (
        "autotuner.SIM_EXIT_FRICTION_PCT must exist as a module-level constant."
    )


def test_sim_exit_friction_pct_not_defined_in_math_engine():
    """The constant lives in autotuner.py, not math_engine.py -- math_engine is imported by the
    LIVE engine (alpha_bot_execution.py); this constant is a replay-only parameter and must stay
    structurally out of the file the live decision path imports from.
    """
    import math_engine

    assert not hasattr(math_engine, "SIM_EXIT_FRICTION_PCT"), (
        "SIM_EXIT_FRICTION_PCT must NOT be defined in math_engine.py -- that "
        "module is imported by the live engine's decision path. It belongs "
        "in autotuner.py module scope."
    )


def test_sim_exit_friction_pct_is_a_float():
    autotuner = _import_autotuner()
    value = getattr(autotuner, "SIM_EXIT_FRICTION_PCT", None)
    assert isinstance(value, float), (
        f"SIM_EXIT_FRICTION_PCT must be a float, got {type(value).__name__}: {value!r}."
    )


def test_sim_exit_friction_pct_units_are_percentage_points_not_decimal_fraction():
    """Units contract: 0.5 percentage points, never the decimal-fraction form of the same cost.

    The sites that subtract the constant operate in percent-point space, so the fraction form
    (the percentage-point value divided by RETURN_PCT_TO_FRACTION) would apply 100x too little
    friction there, silently defeating the feature while looking shipped.
    """
    autotuner = _import_autotuner()

    # Local variable, not the bare ALL_CAPS attribute, on the left of the comparisons below --
    # avoids ruff SIM300 (Yoda condition) while reading as `actual == expected`.
    actual = autotuner.SIM_EXIT_FRICTION_PCT
    as_fraction = _EXPECTED_PERCENT_POINTS / autotuner.RETURN_PCT_TO_FRACTION

    assert actual == pytest.approx(_EXPECTED_PERCENT_POINTS, abs=1e-9), (
        f"SIM_EXIT_FRICTION_PCT must be {_EXPECTED_PERCENT_POINTS} percentage points, got {actual}."
    )
    assert actual * autotuner.RETURN_PCT_TO_FRACTION == pytest.approx(
        _EXPECTED_PERCENT_POINTS * autotuner.RETURN_PCT_TO_FRACTION, abs=1e-9
    )
    assert actual != pytest.approx(as_fraction, abs=1e-9), (
        "SIM_EXIT_FRICTION_PCT must NOT be the decimal-fraction form of the cost -- that value "
        "is copied without converting to this file's percent-point convention."
    )


def test_this_module_reads_no_backtest_client_default_to_compute_the_expected_value():
    """The expected value is the plan's literal: no import of the backtest client and no read of
    its default anywhere in this module, so the exit cost cannot follow that default."""
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    refused_import = "composer_backtest" + "_client"
    refused_name = "_DEFAULT_SLIPPAGE" + "_PERCENT"

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            named = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            named = [node.module or "", *(alias.name for alias in node.names)]
        else:
            continue
        if any(refused_import in name for name in named):
            imports.append(node.lineno)
    reads = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == refused_name)
        or (isinstance(node, ast.Attribute) and node.attr == refused_name)
    ]

    assert imports == [], f"imports the backtest client at line(s) {imports}"
    assert reads == [], f"reads the backtest default at line(s) {reads}"


# ---------------------------------------------------------------------------
# The statement of the cost's basis: source and generated page
# ---------------------------------------------------------------------------


def _constant_window() -> str:
    """The 15 source lines above the SIM_EXIT_FRICTION_PCT assignment, and the assignment."""
    lines = _AUTOTUNER_SRC.read_text(encoding="utf-8").splitlines()
    index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("SIM_EXIT_FRICTION_PCT")),
        None,
    )
    assert index is not None, "Could not locate the SIM_EXIT_FRICTION_PCT assignment line."
    return "\n".join(lines[max(0, index - 15) : index + 1])


def _page_section() -> str:
    """The generated page's Exit-Friction Constant section, up to the next #### heading after
    the constant's own."""
    lines = _AUTOTUNER_PAGE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("### Exit-Friction Constant"))
    own = next(
        i
        for i in range(start + 1, len(lines))
        if lines[i].startswith("#### `SIM_EXIT_FRICTION_PCT")
    )
    end = next((i for i in range(own + 1, len(lines)) if lines[i].startswith("#### ")), len(lines))
    return "\n".join(lines[start:end])


def _page_constants_row() -> str:
    lines = _AUTOTUNER_PAGE.read_text(encoding="utf-8").splitlines()
    return next(line for line in lines if line.startswith("| `SIM_EXIT_FRICTION_PCT`"))


def test_sim_exit_friction_pct_source_comment_cites_validation_report_and_full_position_exit():
    """The constant's source comment cites the validation report whose gap the constant closes
    and says the value models the cost of a full-position exit in the replay."""
    window = _constant_window()

    assert "methodology-validation" in window, (
        "the source comment must cite the validation report "
        "(docs/research/methodology-validation-2026-07.md) whose gap this constant closes"
    )
    assert "full-position exit" in window, (
        "the source comment must say the value models the cost of a full-position exit in "
        "the replay"
    )


@pytest.mark.parametrize(
    "surface",
    ["source_window", "generated_section", "generated_constants_row"],
)
def test_every_statement_of_the_exit_cost_stands_on_its_own_basis(surface):
    """Source window, generated section and constants row each cite the validation report, name
    the full-position exit, and none ties the value to the backtest client or its default."""
    text = {
        "source_window": _constant_window,
        "generated_section": _page_section,
        "generated_constants_row": _page_constants_row,
    }[surface]()

    missing = [phrase for phrase in _REQUIRED_PHRASES if phrase not in text]
    held = [phrase for phrase in _REFUSED_PHRASES if phrase in text]

    assert (missing, held) == ([], []), (
        f"{surface} does not state {missing} and still states the retired derivation {held}"
    )


# ---------------------------------------------------------------------------
# Non-tunability
# ---------------------------------------------------------------------------


def test_sim_exit_friction_pct_absent_from_optuna_search_space():
    """SIM_EXIT_FRICTION_PCT must never be a suggestible Optuna param."""
    autotuner = _import_autotuner()
    assert "SIM_EXIT_FRICTION_PCT" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "SIM_EXIT_FRICTION_PCT must NOT be a member of OPTUNA_SEARCH_SPACE_KEYS "
        "-- a tunable friction constant would let the optimizer game its own "
        "cost model."
    )


def test_sim_exit_friction_pct_never_read_from_trial_params_dict():
    """No call site may read the friction value via p.get("SIM_EXIT_FRICTION_PCT", ...).

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
        f"{offending}. It is a pure module constant."
    )


def test_sim_exit_friction_pct_never_read_from_environment():
    """Not env-tunable -- no os.environ access keyed on this name."""
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    assert 'environ.get("SIM_EXIT_FRICTION_PCT"' not in src, (
        "SIM_EXIT_FRICTION_PCT must not be read from an environment variable "
        "-- it must be a fixed, non-operator-tunable-at-runtime "
        "constant (only a code change may adjust it)."
    )
    assert "SIM_EXIT_FRICTION_PCT" not in _MATH_ENGINE_SRC.read_text(encoding="utf-8"), (
        "SIM_EXIT_FRICTION_PCT must not appear anywhere in math_engine.py "
        "(placement + blast-radius intent)."
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
