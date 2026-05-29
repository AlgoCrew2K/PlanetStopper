"""RED tests for OPTUNA-7 (n_trials named-constant pin) audit fix.

Background — math re-audit finding OPTUNA-7 (MEDIUM):

The autotuner's main study site (autotuner.py:1626) calls
``study.optimize(objective, n_trials=500, n_jobs=_n_jobs)`` with a magic
integer literal ``500``. The project CLAUDE.md hard rule for math layers
is "No magic numbers in math_engine.py — every constant named + source
comment", and the autotuner is a math-engine consumer subject to the
same discipline (the trial-floor IS a math-soundness constant: it
controls the BHY Yekutieli c(N) calibration adequacy and the TPE
sampler's exploration density).

The same site at ``run_calibration_sweep`` (autotuner.py:2029) uses
``n_trials=100`` — the project's documented 100-trial statistical-
stability floor — also as a literal.

OPTUNA-7 requires:

1. Module-level named constant whose value is 500 (the production
   n_trials), with a source comment citing the BHY haircut Yekutieli
   c(N) calibration, the 100-trial statistical-stability floor (project
   rule), and the 5x headroom rationale.
2. Module-level named constant whose value is 100 (the calibration-
   sweep n_trials), with a source comment confirming it equals the
   statistical-stability floor.
3. ``run_autotuner.study.optimize(..., n_trials=<name>, ...)`` — the
   kwarg references the named constant, never a literal.
4. ``run_calibration_sweep.study.optimize(..., n_trials=<name>, ...)``
   — same discipline at the calibration site.
5. NO integer-literal n_trials kwargs anywhere in autotuner.py.
6. OPTUNA-1 (TPESampler pin + seed env), OPTUNA-2 (NopPruner pin), and
   OPTUNA-6 (n_jobs env resolver) all preserved — the OPTUNA-7 diff is
   targeted to the n_trials kwarg only.

All tests are RED on cycle/optuna-7-named-trial-floor @ 651dbb6 (the
parent commit before any GREEN impl). The site is located by AST walk
over ``autotuner.py``, not by line number — refactors that re-flow the
site without restoring the pin will still fail.
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import re
import sys

import pytest


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "optuna_n_trials_pin_contract.json"
)


@pytest.fixture(scope="module")
def pin_contract() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def autotuner_module():
    """Import autotuner under the worktree sys.path; reload to clear any
    stale cached module state from sibling tests."""
    if "autotuner" in sys.modules:
        return importlib.reload(sys.modules["autotuner"])
    return importlib.import_module("autotuner")


@pytest.fixture(scope="module")
def autotuner_source() -> str:
    import autotuner as _at
    return pathlib.Path(_at.__file__).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def autotuner_ast(autotuner_source: str) -> ast.Module:
    return ast.parse(autotuner_source)


# ---------------------------------------------------------------------------
# AST helpers — locate the study.optimize call sites
# ---------------------------------------------------------------------------

_RUN_AUTOTUNER = "run_autotuner"
_RUN_CALIBRATION = "run_calibration_sweep"


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"AST: expected to find function def {name!r} in autotuner.py — "
        "the test cannot run without it. Did the function get renamed?"
    )


def _study_optimize_calls(func: ast.FunctionDef) -> list[ast.Call]:
    """Return every ``<x>.optimize(...)`` call inside ``func``."""
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "optimize":
            calls.append(node)
    return calls


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _module_int_attrs(module) -> dict[str, int]:
    """Return {name: value} for every module-level int attribute (excluding
    bool, which is a subclass of int in Python and would dilute the search)."""
    out: dict[str, int] = {}
    for name in dir(module):
        val = getattr(module, name, None)
        if isinstance(val, int) and not isinstance(val, bool):
            out[name] = val
    return out


def _module_int_assignments(tree: ast.Module) -> dict[str, tuple[int, int]]:
    """Return {target_name: (value, lineno)} for module-level int constant
    assignments. Used by the source-comment proximity test, which must
    locate the line where the constant is defined to scan the surrounding
    comment window."""
    out: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (
            isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
            and not isinstance(node.value.value, bool)
        ):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = (node.value.value, node.lineno)
    return out


# ---------------------------------------------------------------------------
# Named-constant existence + value pins
# ---------------------------------------------------------------------------

def test_autotuner_exposes_production_n_trials_named_constant(
    autotuner_module, pin_contract
):
    """``autotuner.py`` must expose a module-level named int constant whose
    VALUE is the production n_trials (500). The implementation chooses the
    symbol name (e.g. ``OPTUNA_N_TRIALS_PRODUCTION``, ``N_TRIALS``,
    ``_N_TRIALS_PRODUCTION``); this test pins the value only.

    Per Planet Stopper Coding Standards "No magic numbers in math_engine.py —
    every constant named + source comment"; same discipline applies to
    autotuner.py's math-soundness-controlling constants.
    """
    expected = int(pin_contract["constants"]["production_n_trials_value"])
    attrs = _module_int_attrs(autotuner_module)
    matches = [name for name, val in attrs.items() if val == expected]
    assert matches, (
        f"OPTUNA-7: autotuner.py must expose a module-level named int "
        f"constant whose value is {expected} (the production n_trials). "
        f"None of the {len(attrs)} module-level int attrs have this value. "
        f"Found int attrs: {sorted((n, v) for n, v in attrs.items())[:30]}"
    )


def test_autotuner_exposes_calibration_n_trials_named_constant(
    autotuner_module, pin_contract
):
    """``autotuner.py`` must expose a module-level named int constant whose
    VALUE is the calibration n_trials (100). This MUST equal the project's
    documented statistical-stability floor exactly.
    """
    expected = int(pin_contract["constants"]["calibration_n_trials_value"])
    floor = int(pin_contract["constants"]["statistical_stability_floor"])
    assert expected == floor, (
        f"Fixture invariant: calibration_n_trials_value ({expected}) must "
        f"equal statistical_stability_floor ({floor}). The calibration "
        f"sweep IS the floor by design."
    )
    attrs = _module_int_attrs(autotuner_module)
    matches = [name for name, val in attrs.items() if val == expected]
    assert matches, (
        f"OPTUNA-7: autotuner.py must expose a module-level named int "
        f"constant whose value is {expected} (the calibration sweep "
        f"n_trials = the statistical-stability floor). None of the "
        f"{len(attrs)} module-level int attrs have this value. "
        f"Found int attrs: {sorted((n, v) for n, v in attrs.items())[:30]}"
    )


def test_production_n_trials_meets_floor_headroom_ratio(
    autotuner_module, pin_contract
):
    """Production n_trials must be >= headroom_ratio (5) * floor (100) = 500.

    This is the BHY-adequacy invariant: c(500) ~6.79 vs c(100) ~5.19, a
    ~30% larger selection-bias correction. Reducing the headroom (e.g.
    setting production to 2x floor = 200) is a methodology change
    requiring PM surface; the test pins the >= boundary.
    """
    prod_value = int(pin_contract["constants"]["production_n_trials_value"])
    floor = int(pin_contract["constants"]["statistical_stability_floor"])
    headroom = int(pin_contract["constants"]["production_floor_headroom_ratio"])

    # Re-locate the production constant by value (impl is free on naming).
    attrs = _module_int_attrs(autotuner_module)
    prod_constants = [name for name, val in attrs.items() if val == prod_value]
    assert prod_constants, (
        f"Precondition: production n_trials constant (value={prod_value}) "
        f"must exist as a module-level named int (see "
        f"test_autotuner_exposes_production_n_trials_named_constant)."
    )
    # Floor invariant: the named-and-defined production value must be
    # >= headroom * floor. This catches a regression where the symbol
    # exists but the value drifted below the BHY-adequacy boundary.
    for name in prod_constants:
        val = attrs[name]
        assert val >= headroom * floor, (
            f"OPTUNA-7 BHY-adequacy floor: {name}={val} must be >= "
            f"headroom_ratio ({headroom}) * floor ({floor}) = "
            f"{headroom * floor}. Reducing below {headroom * floor} "
            f"weakens the BHY Yekutieli c(N) correction below the "
            f"5x-headroom adequacy line — methodology change requiring "
            f"PM surface."
        )


# ---------------------------------------------------------------------------
# Source-comment contract: BHY + floor mention near the production constant
# ---------------------------------------------------------------------------

def _comment_window(source: str, lineno: int, span: int = 25) -> str:
    """Return the source lines from (lineno - span) to (lineno + span),
    joined. Used to scan the comment context surrounding a constant
    definition."""
    lines = source.splitlines()
    start = max(0, lineno - 1 - span)
    end = min(len(lines), lineno + span)
    return "\n".join(lines[start:end])


def test_production_n_trials_constant_has_bhy_source_comment(
    autotuner_source, autotuner_ast, pin_contract
):
    """The production n_trials constant must be accompanied by a source
    comment citing the BHY / Harvey-Liu / Yekutieli / haircut rationale.
    At least one of those tokens must appear within ~25 source lines of
    the constant's definition — so a future PR that proposes to lower
    the constant sees the BHY dependency immediately at the constant
    site, without tracing.
    """
    prod_value = int(pin_contract["constants"]["production_n_trials_value"])
    required_any = pin_contract["source_comment_contract"][
        "required_substrings_any_for_bhy"
    ]

    assigns = _module_int_assignments(autotuner_ast)
    prod_candidates = [
        (name, val, lineno)
        for name, (val, lineno) in assigns.items()
        if val == prod_value
    ]
    assert prod_candidates, (
        f"Precondition: production n_trials constant (value={prod_value}) "
        f"must be defined as a module-level int assignment."
    )

    # At least one candidate's comment window must contain a BHY-family token.
    found_window: str | None = None
    for name, _val, lineno in prod_candidates:
        window = _comment_window(autotuner_source, lineno)
        if any(tok in window for tok in required_any):
            found_window = window
            break

    assert found_window is not None, (
        f"OPTUNA-7 source-comment contract: the production n_trials "
        f"constant (value={prod_value}) must carry a source comment within "
        f"~25 lines citing at least one of {required_any}. None of the "
        f"candidate constants ({[n for n, _, _ in prod_candidates]}) have "
        f"such a comment. A constant without the BHY rationale comment "
        f"is invisible at PR-review time — defeats the audit fix."
    )


def test_production_n_trials_constant_has_floor_source_comment(
    autotuner_source, autotuner_ast, pin_contract
):
    """The production n_trials constant must also carry a comment mention
    of the 100-trial floor / statistical-stability relationship. The
    BHY-rationale comment alone is insufficient: a future PR proposing to
    cut to e.g. 200 trials might preserve BHY adequacy in the reviewer's
    mind but quietly halve the floor headroom; the floor mention is what
    makes the floor-relationship visible.
    """
    prod_value = int(pin_contract["constants"]["production_n_trials_value"])
    required_any = pin_contract["source_comment_contract"][
        "required_substrings_any_for_floor"
    ]

    assigns = _module_int_assignments(autotuner_ast)
    prod_candidates = [
        (name, val, lineno)
        for name, (val, lineno) in assigns.items()
        if val == prod_value
    ]
    assert prod_candidates, (
        f"Precondition: production n_trials constant (value={prod_value}) "
        f"must be defined as a module-level int assignment."
    )

    found = False
    for name, _val, lineno in prod_candidates:
        window = _comment_window(autotuner_source, lineno)
        # Match each substring case-sensitively; "100" and "floor" and
        # "stability" are all specific enough that case doesn't matter
        # and the literal "100" must not be accidentally rewritten.
        if any(tok in window for tok in required_any):
            found = True
            break

    assert found, (
        f"OPTUNA-7 source-comment contract: the production n_trials "
        f"constant must carry a source comment within ~25 lines citing "
        f"at least one of {required_any} (the 100-trial floor / "
        f"statistical-stability relationship). None of the candidates "
        f"have such a comment. Without the floor-relationship mention, "
        f"a 'cut to 250 for speed' PR sees no warning at the constant "
        f"site."
    )


# ---------------------------------------------------------------------------
# Call-site pin: study.optimize(..., n_trials=<name>) at both sites
# ---------------------------------------------------------------------------

def test_run_autotuner_study_optimize_n_trials_is_not_a_literal(autotuner_ast):
    """``run_autotuner.study.optimize(..., n_trials=...)`` must NOT pass an
    integer literal — the value must be a Name (or attribute) reference
    to the named constant. A regression that re-introduces
    ``n_trials=500`` (the OPTUNA-7 magic number) trips this.
    """
    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)
    calls = _study_optimize_calls(func)
    assert calls, (
        "AST: expected at least one study.optimize(...) call in "
        "run_autotuner — none found."
    )
    offenders: list[str] = []
    for call in calls:
        n_trials_expr = _kw(call, "n_trials")
        if n_trials_expr is None:
            offenders.append(
                f"line {call.lineno}: study.optimize(...) missing n_trials= "
                f"kwarg entirely — must reference the named constant."
            )
            continue
        is_int_literal = (
            isinstance(n_trials_expr, ast.Constant)
            and isinstance(n_trials_expr.value, int)
            and not isinstance(n_trials_expr.value, bool)
        )
        is_unary_int_literal = (
            isinstance(n_trials_expr, ast.UnaryOp)
            and isinstance(n_trials_expr.operand, ast.Constant)
            and isinstance(n_trials_expr.operand.value, int)
        )
        if is_int_literal or is_unary_int_literal:
            lit_repr = (
                str(n_trials_expr.value) if is_int_literal
                else "-" + str(n_trials_expr.operand.value)
            )
            offenders.append(
                f"line {call.lineno}: study.optimize(..., n_trials="
                f"{lit_repr}) is the OPTUNA-7 magic number. Source from "
                f"the named module-level constant instead."
            )
    assert not offenders, (
        "OPTUNA-7 run_autotuner call-site violations:\n  - "
        + "\n  - ".join(offenders)
    )


def test_run_autotuner_n_trials_references_production_constant(
    autotuner_ast, autotuner_module, pin_contract
):
    """The n_trials= kwarg at the run_autotuner site must be a Name (or
    attribute) whose resolved module-level value equals the production
    n_trials (500). This couples the call site to the named constant: a
    refactor that introduces a different constant with a non-500 value
    trips this even though the literal-check above passes.
    """
    prod_value = int(pin_contract["constants"]["production_n_trials_value"])
    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)
    calls = _study_optimize_calls(func)
    assert calls

    attrs = _module_int_attrs(autotuner_module)

    matched = False
    name_refs_seen: list[str] = []
    for call in calls:
        n_trials_expr = _kw(call, "n_trials")
        if n_trials_expr is None:
            continue
        if isinstance(n_trials_expr, ast.Name):
            name_refs_seen.append(n_trials_expr.id)
            if attrs.get(n_trials_expr.id) == prod_value:
                matched = True
                break
        elif isinstance(n_trials_expr, ast.Attribute):
            name_refs_seen.append(ast.unparse(n_trials_expr))
            # Resolve <module>.<name> against the imported autotuner module.
            attr_name = n_trials_expr.attr
            if attrs.get(attr_name) == prod_value:
                matched = True
                break

    assert matched, (
        f"OPTUNA-7: run_autotuner.study.optimize(n_trials=<name>) must "
        f"reference a module-level named constant whose value is "
        f"{prod_value}. Saw n_trials kwarg references: {name_refs_seen!r}. "
        f"Module int attrs in scope: "
        f"{sorted((n, v) for n, v in attrs.items() if v == prod_value)}"
    )


def test_run_calibration_sweep_study_optimize_n_trials_is_not_a_literal(
    autotuner_ast,
):
    """``run_calibration_sweep.study.optimize(..., n_trials=...)`` must NOT
    pass an integer literal. The calibration site's literal ``100`` is
    the SECOND OPTUNA-7 magic number and is in scope.
    """
    func = _function_def(autotuner_ast, _RUN_CALIBRATION)
    calls = _study_optimize_calls(func)
    assert calls, (
        "AST: expected at least one study.optimize(...) call in "
        "run_calibration_sweep — none found."
    )
    offenders: list[str] = []
    for call in calls:
        n_trials_expr = _kw(call, "n_trials")
        if n_trials_expr is None:
            offenders.append(
                f"line {call.lineno}: study.optimize(...) missing n_trials= "
                f"kwarg entirely — must reference the named constant."
            )
            continue
        is_int_literal = (
            isinstance(n_trials_expr, ast.Constant)
            and isinstance(n_trials_expr.value, int)
            and not isinstance(n_trials_expr.value, bool)
        )
        is_unary_int_literal = (
            isinstance(n_trials_expr, ast.UnaryOp)
            and isinstance(n_trials_expr.operand, ast.Constant)
            and isinstance(n_trials_expr.operand.value, int)
        )
        if is_int_literal or is_unary_int_literal:
            lit_repr = (
                str(n_trials_expr.value) if is_int_literal
                else "-" + str(n_trials_expr.operand.value)
            )
            offenders.append(
                f"line {call.lineno}: study.optimize(..., n_trials="
                f"{lit_repr}) is an OPTUNA-7 magic number at the "
                f"calibration site. Source from the named constant instead."
            )
    assert not offenders, (
        "OPTUNA-7 run_calibration_sweep call-site violations:\n  - "
        + "\n  - ".join(offenders)
    )


def test_run_calibration_sweep_n_trials_references_calibration_constant(
    autotuner_ast, autotuner_module, pin_contract
):
    """The calibration-site n_trials kwarg must reference a named constant
    whose value is 100 — NOT the production constant. A regression that
    uses the production constant at both sites collapses the calibration/
    production distinction and is a methodology change.
    """
    calib_value = int(pin_contract["constants"]["calibration_n_trials_value"])
    prod_value = int(pin_contract["constants"]["production_n_trials_value"])

    func = _function_def(autotuner_ast, _RUN_CALIBRATION)
    calls = _study_optimize_calls(func)
    assert calls

    attrs = _module_int_attrs(autotuner_module)

    matched = False
    name_refs_seen: list[str] = []
    for call in calls:
        n_trials_expr = _kw(call, "n_trials")
        if n_trials_expr is None:
            continue
        if isinstance(n_trials_expr, ast.Name):
            name_refs_seen.append(n_trials_expr.id)
            referenced_value = attrs.get(n_trials_expr.id)
            if referenced_value == calib_value:
                matched = True
                break
            # Negative invariant: must not be the production value at this site.
            if referenced_value == prod_value:
                pytest.fail(
                    f"OPTUNA-7 site-collapse: run_calibration_sweep "
                    f"n_trials=<{n_trials_expr.id}> resolves to {prod_value} "
                    f"(the production value). Calibration site must use "
                    f"the distinct calibration constant ({calib_value})."
                )
        elif isinstance(n_trials_expr, ast.Attribute):
            attr_name = n_trials_expr.attr
            name_refs_seen.append(ast.unparse(n_trials_expr))
            if attrs.get(attr_name) == calib_value:
                matched = True
                break
            if attrs.get(attr_name) == prod_value:
                pytest.fail(
                    f"OPTUNA-7 site-collapse: run_calibration_sweep "
                    f"n_trials={ast.unparse(n_trials_expr)} resolves to "
                    f"{prod_value} (the production value). Calibration "
                    f"site must use the calibration constant ({calib_value})."
                )

    assert matched, (
        f"OPTUNA-7: run_calibration_sweep.study.optimize(n_trials=<name>) "
        f"must reference a module-level named constant whose value is "
        f"{calib_value}. Saw n_trials kwarg references: "
        f"{name_refs_seen!r}. Module int attrs at value {calib_value}: "
        f"{[n for n, v in attrs.items() if v == calib_value]}"
    )


# ---------------------------------------------------------------------------
# Static guard: no integer-literal n_trials kwargs anywhere in autotuner.py
# ---------------------------------------------------------------------------

def test_autotuner_source_has_no_integer_literal_n_trials_kwarg(autotuner_ast):
    """Whole-module sweep: NO ``n_trials=<int-literal>`` may appear
    anywhere in autotuner.py. The previous two tests pin the two known
    sites; this catches a future PR that adds a third study.optimize
    call with a fresh magic-number literal.
    """
    offenders: list[str] = []
    for node in ast.walk(autotuner_ast):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "n_trials":
                continue
            val_expr = kw.value
            is_int_literal = (
                isinstance(val_expr, ast.Constant)
                and isinstance(val_expr.value, int)
                and not isinstance(val_expr.value, bool)
            )
            is_unary_int_literal = (
                isinstance(val_expr, ast.UnaryOp)
                and isinstance(val_expr.operand, ast.Constant)
                and isinstance(val_expr.operand.value, int)
            )
            if is_int_literal or is_unary_int_literal:
                lit_repr = (
                    str(val_expr.value) if is_int_literal
                    else "-" + str(val_expr.operand.value)
                )
                offenders.append(
                    f"line {kw.value.lineno}: n_trials={lit_repr} is an "
                    f"integer-literal kwarg in autotuner.py. Source from "
                    f"a named module-level constant per OPTUNA-7."
                )
    assert not offenders, (
        "OPTUNA-7 autotuner.py integer-literal n_trials violations:\n  - "
        + "\n  - ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Regression guards: OPTUNA-1 + OPTUNA-2 + OPTUNA-6 UNCHANGED
# ---------------------------------------------------------------------------

def test_optuna_1_sampler_pin_preserved_at_both_sites(autotuner_ast, pin_contract):
    """OPTUNA-7 must NOT regress OPTUNA-1: every ``optuna.create_study(...)``
    call in autotuner.py must still pass an explicit ``sampler=`` kwarg
    constructing a ``TPESampler``. A diff that touches only n_trials
    leaves the sampler kwarg untouched; this test confirms.
    """
    expected_sampler_dotted = pin_contract["regression_guards"][
        "optuna_1_sampler_class"
    ]
    assert expected_sampler_dotted.endswith("TPESampler")

    sites: list[str] = []
    for func_name in (_RUN_AUTOTUNER, _RUN_CALIBRATION):
        func = _function_def(autotuner_ast, func_name)
        create_calls = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_study"
        ]
        assert create_calls, (
            f"AST: expected optuna.create_study(...) in {func_name}"
        )
        for call in create_calls:
            sampler_expr = _kw(call, "sampler")
            assert sampler_expr is not None, (
                f"OPTUNA-1 regression in {func_name} (line {call.lineno}): "
                f"create_study(...) missing sampler= kwarg. OPTUNA-7 must "
                f"not regress the OPTUNA-1 sampler pin."
            )
            # Accept either an inline TPESampler(...) call OR a Name bound
            # to one via local assignment (the calibration site does the
            # latter — sampler = optuna.samplers.TPESampler(seed=...)).
            ok = False
            if isinstance(sampler_expr, ast.Call):
                f = sampler_expr.func
                if (isinstance(f, ast.Attribute) and f.attr == "TPESampler") or (
                    isinstance(f, ast.Name) and f.id == "TPESampler"
                ):
                    ok = True
            elif isinstance(sampler_expr, ast.Name):
                # Local assignment to TPESampler(...)?
                target_name = sampler_expr.id
                for assign in ast.walk(func):
                    if not isinstance(assign, ast.Assign):
                        continue
                    targets = [
                        t.id for t in assign.targets if isinstance(t, ast.Name)
                    ]
                    if target_name in targets and isinstance(
                        assign.value, ast.Call
                    ):
                        af = assign.value.func
                        if (
                            isinstance(af, ast.Attribute)
                            and af.attr == "TPESampler"
                        ) or (isinstance(af, ast.Name) and af.id == "TPESampler"):
                            ok = True
                            break
            assert ok, (
                f"OPTUNA-1 regression in {func_name} (line {call.lineno}): "
                f"sampler= kwarg no longer constructs a TPESampler."
            )
            sites.append(func_name)

    assert len(sites) >= 2, (
        f"Expected sampler pins at both run_autotuner and "
        f"run_calibration_sweep; located {sites}"
    )


def test_optuna_2_pruner_pin_preserved_at_both_sites(autotuner_ast, pin_contract):
    """OPTUNA-7 must NOT regress OPTUNA-2: every ``optuna.create_study(...)``
    call in autotuner.py must still pass ``pruner=optuna.pruners.NopPruner()``.
    """
    expected_pruner_dotted = pin_contract["regression_guards"][
        "optuna_2_pruner_class"
    ]
    assert expected_pruner_dotted.endswith("NopPruner")

    sites_checked = 0
    for func_name in (_RUN_AUTOTUNER, _RUN_CALIBRATION):
        func = _function_def(autotuner_ast, func_name)
        create_calls = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_study"
        ]
        assert create_calls
        for call in create_calls:
            pruner_expr = _kw(call, "pruner")
            assert pruner_expr is not None, (
                f"OPTUNA-2 regression in {func_name} (line {call.lineno}): "
                f"create_study(...) missing pruner= kwarg."
            )
            assert isinstance(pruner_expr, ast.Call), (
                f"OPTUNA-2 regression in {func_name} (line {call.lineno}): "
                f"pruner= must be a NopPruner() call, got "
                f"{type(pruner_expr).__name__}"
            )
            f = pruner_expr.func
            is_nop = (
                isinstance(f, ast.Attribute) and f.attr == "NopPruner"
            ) or (isinstance(f, ast.Name) and f.id == "NopPruner")
            assert is_nop, (
                f"OPTUNA-2 regression in {func_name} (line {call.lineno}): "
                f"pruner= must construct NopPruner."
            )
            sites_checked += 1

    assert sites_checked >= 2


def test_optuna_6_n_jobs_helper_preserved(autotuner_module, pin_contract):
    """OPTUNA-7 must NOT regress OPTUNA-6: the shared
    ``_resolve_optuna_n_jobs_from_env`` helper must still exist on the
    module. A targeted n_trials-only diff leaves the helper untouched.
    """
    helper_name = pin_contract["regression_guards"]["optuna_6_n_jobs_helper_name"]
    helper = getattr(autotuner_module, helper_name, None)
    assert helper is not None and callable(helper), (
        f"OPTUNA-6 regression: autotuner.py must continue to expose the "
        f"module-level helper `{helper_name}()`. OPTUNA-7 diff must be "
        f"targeted to n_trials only — the n_jobs env machinery is in "
        f"scope to preserve."
    )
