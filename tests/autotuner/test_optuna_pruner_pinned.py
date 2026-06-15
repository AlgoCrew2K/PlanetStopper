"""RED tests for OPTUNA-2 (pruner pin) audit fix.

Background — math re-audit finding (MEDIUM):

OPTUNA-2: Both autotuner study sites — ``run_autotuner`` (autotuner.py:1562
area) and ``run_calibration_sweep`` (autotuner.py:1940 area) — call
``optuna.create_study(...)`` with NO ``pruner=`` kwarg. Optuna's implicit
default is ``MedianPruner``. The objective is end-of-trial-scored (a single
scalar from ``compute_sortino_ratio`` / ``compute_crra_eu_objective`` after
the full guard-alpha sim); there are no ``trial.report(...)`` calls anywhere
in the codebase, so the default MedianPruner is SILENTLY INACTIVE today.

The concern: a future PR adds ``trial.report(intermediate_value, step=...)``
inside the objective loop (e.g. mid-simulation guard-alpha checkpoints) "to
monitor progress." With the implicit MedianPruner this would silently
activate — censoring the trial set the BHY haircut consumes. The BHY c(N)
Yekutieli factor and the N_effective additive accounting both assume the
COMPLETE set of N trials; a censored set invalidates both math layers
without any in-tree signal.

The fix (pruner-choice plan):
1. Pass ``pruner=optuna.pruners.NopPruner()`` explicitly at every
   ``create_study`` call site.
2. Expose a module-level named constant (e.g. ``ACTIVE_OPTUNA_PRUNER_FAMILY
   = "NOP"``) so the choice is inspectable from cross-file searches and the
   no-magic-numbers rule is honoured.
3. Static-guard the absence of ``trial.report`` and ``.should_prune`` in
   autotuner.py — adding either is a methodology change that must surface
   to PM before merge.
4. Source comment at the pin must cite the BHY haircut + N_effective
   additive accounting dependencies.

All tests are RED on cycle/optuna-2-pin-pruner @ 55d0204 (fork point) and
must remain green after implementation. Study sites are located by AST walk
over ``autotuner.py``, not by line numbers — refactors that re-flow the
sites without restoring the pin will still fail.
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import sys

import optuna
import pytest


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "optuna_pruner_pin_contract.json"
)


@pytest.fixture(scope="module")
def pin_contract() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def autotuner_module():
    """Import autotuner; reload to clear any stale module state from sibling
    tests (some tests in this tree monkeypatch module-level attributes).
    """
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
# AST helpers — locate the pinned study sites
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


def _create_study_calls(func: ast.FunctionDef) -> list[ast.Call]:
    """Return every ``optuna.create_study(...)`` call inside ``func``."""
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "create_study":
            calls.append(node)
    return calls


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_nop_pruner_call(expr: ast.expr) -> bool:
    """True iff ``expr`` is a Call whose target resolves to NopPruner.

    Accepts both ``optuna.pruners.NopPruner()`` and a bare ``NopPruner()``
    name; the import shape in the impl is free, but the constructed class
    must be NopPruner.
    """
    if not isinstance(expr, ast.Call):
        return False
    f = expr.func
    if isinstance(f, ast.Attribute) and f.attr == "NopPruner":
        return True
    if isinstance(f, ast.Name) and f.id == "NopPruner":
        return True
    return False


def _expr_resolves_to_nop_pruner(expr: ast.expr, func: ast.FunctionDef, tree: ast.Module) -> bool:
    """True iff ``expr`` is a NopPruner() Call OR a Name bound to one in scope.

    Resolution walks ``func``'s local Assign nodes first, then the module's
    top-level Assigns, looking for ``<name> = <expr-resolving-to-NopPruner>``.
    """
    if _is_nop_pruner_call(expr):
        return True
    if isinstance(expr, ast.Name):
        target = expr.id
        # Local-scope assigns first.
        for assign in ast.walk(func):
            if isinstance(assign, ast.Assign):
                tgt_names = [t.id for t in assign.targets if isinstance(t, ast.Name)]
                if target in tgt_names and _is_nop_pruner_call(assign.value):
                    return True
        # Module-level assigns.
        for node in tree.body:
            if isinstance(node, ast.Assign):
                tgt_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if target in tgt_names and _is_nop_pruner_call(node.value):
                    return True
    return False


# ---------------------------------------------------------------------------
# Module-level named constant for the pruner family
# ---------------------------------------------------------------------------


def test_autotuner_exposes_pruner_family_named_constant(autotuner_module, pin_contract):
    """A module-level named constant in autotuner.py must carry the
    ``"NOP"`` pruner-family label. Per Planet Stopper Coding Standards: every
    constant gets a name + source comment. The label makes the pruner
    choice cross-file-inspectable (grep ``"NOP"`` over the repo); a
    methodology change would flip the label and become visible at diff
    review.

    The fixture pins the VALUE only — the impl is free to name the symbol
    (e.g. ``_ACTIVE_OPTUNA_PRUNER_FAMILY``, ``ACTIVE_OPTUNA_PRUNER_FAMILY``,
    ``_OPTUNA_PRUNER_FAMILY``).
    """
    expected_value = pin_contract["constants"]["pruner_family_label"]
    str_attrs = {
        name: getattr(autotuner_module, name)
        for name in dir(autotuner_module)
        if isinstance(getattr(autotuner_module, name, None), str)
    }
    assert expected_value in str_attrs.values(), (
        f"OPTUNA-2: autotuner.py must expose a module-level named constant "
        f"whose value is {expected_value!r} (the active Optuna pruner family "
        f"label per pruner-choice plan D2). Found string attrs (truncated): "
        f"{sorted(str_attrs.values())[:30]!r}..."
    )


# ---------------------------------------------------------------------------
# OPTUNA-2: explicit NopPruner at the run_autotuner site
# ---------------------------------------------------------------------------


def test_run_autotuner_create_study_passes_explicit_pruner(autotuner_ast):
    """Every ``optuna.create_study(...)`` call inside ``run_autotuner`` must
    pass an explicit ``pruner=`` kwarg — never relying on Optuna's implicit
    default (MedianPruner). This is the OPTUNA-2 audit fix.
    """
    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)
    calls = _create_study_calls(func)
    assert calls, (
        "AST: run_autotuner is expected to contain an optuna.create_study(...) "
        "call (none found). Did the call site move out of run_autotuner?"
    )
    missing = [c for c in calls if _kw(c, "pruner") is None]
    assert not missing, (
        f"OPTUNA-2: every optuna.create_study(...) call inside run_autotuner "
        f"must pass an explicit pruner= kwarg (got {len(missing)} site(s) "
        f"without one, at line(s) {[c.lineno for c in missing]}). "
        "Implicit default MedianPruner would silently activate if a future "
        "trial.report addition appears — censoring the BHY haircut's "
        "complete-trial-set assumption + the N_effective additive accounting."
    )


def test_run_autotuner_pruner_is_nop_not_default(autotuner_source):
    """The ``pruner=`` kwarg inside ``run_autotuner`` must resolve to
    ``optuna.pruners.NopPruner()`` — not MedianPruner, not any other family.

    Accepts either an inline ``pruner=optuna.pruners.NopPruner()`` call or
    a ``pruner=<name>`` reference where ``<name>`` is assigned to a
    NopPruner() construction either in-scope or at module level. The full
    module AST is re-parsed here (rather than reusing ``autotuner_ast``)
    so the resolver can walk module-level Assigns for Name-bound pruners.
    """
    tree = ast.parse(autotuner_source)
    func = _function_def(tree, _RUN_AUTOTUNER)

    calls = _create_study_calls(func)
    assert calls, "AST: run_autotuner must contain a create_study call."
    bad: list[str] = []
    for call in calls:
        pruner_expr = _kw(call, "pruner")
        if pruner_expr is None:
            bad.append(
                f"line {call.lineno}: pruner= kwarg missing entirely "
                f"(implicit MedianPruner default)."
            )
            continue
        if not _expr_resolves_to_nop_pruner(pruner_expr, func, tree):
            bad.append(
                f"line {call.lineno}: pruner= must construct "
                f"optuna.pruners.NopPruner() (inline or via a Name bound to "
                f"a NopPruner() construction); got {ast.dump(pruner_expr)}"
            )
    assert not bad, "OPTUNA-2 run_autotuner pruner-pin violations:\n  - " + "\n  - ".join(bad)


# ---------------------------------------------------------------------------
# OPTUNA-2: same pin at the run_calibration_sweep site
# ---------------------------------------------------------------------------


def test_run_calibration_sweep_create_study_passes_explicit_pruner(autotuner_ast):
    """Every ``optuna.create_study(...)`` call inside
    ``run_calibration_sweep`` must pass an explicit ``pruner=`` kwarg —
    uniform application of the OPTUNA-2 pin across all autotuner study
    sites. Same rationale as run_autotuner (BHY + N_effective).
    """
    func = _function_def(autotuner_ast, _RUN_CALIBRATION)
    calls = _create_study_calls(func)
    assert calls, (
        "AST: run_calibration_sweep is expected to contain an "
        "optuna.create_study(...) call (none found)."
    )
    missing = [c for c in calls if _kw(c, "pruner") is None]
    assert not missing, (
        f"OPTUNA-2: every optuna.create_study(...) call inside "
        f"run_calibration_sweep must pass an explicit pruner= kwarg "
        f"(got {len(missing)} site(s) without one, at line(s) "
        f"{[c.lineno for c in missing]}). Uniform pin across both autotuner "
        f"study sites is required."
    )


def test_run_calibration_sweep_pruner_is_nop_not_default(autotuner_source):
    """The ``pruner=`` kwarg inside ``run_calibration_sweep`` must resolve
    to ``optuna.pruners.NopPruner()``. Same kwarg-must-exist enforcement as
    the run_autotuner companion test."""
    tree = ast.parse(autotuner_source)
    func = _function_def(tree, _RUN_CALIBRATION)
    calls = _create_study_calls(func)
    assert calls, "AST: run_calibration_sweep must contain a create_study call."
    bad: list[str] = []
    for call in calls:
        pruner_expr = _kw(call, "pruner")
        if pruner_expr is None:
            bad.append(
                f"line {call.lineno}: pruner= kwarg missing entirely "
                f"(implicit MedianPruner default)."
            )
            continue
        if not _expr_resolves_to_nop_pruner(pruner_expr, func, tree):
            bad.append(
                f"line {call.lineno}: pruner= must construct "
                f"optuna.pruners.NopPruner(); got {ast.dump(pruner_expr)}"
            )
    assert not bad, "OPTUNA-2 run_calibration_sweep pruner-pin violations:\n  - " + "\n  - ".join(
        bad
    )


# ---------------------------------------------------------------------------
# Static guard: no trial.report or .should_prune in autotuner.py
# ---------------------------------------------------------------------------


def test_no_trial_report_or_should_prune_in_autotuner(autotuner_source, pin_contract):
    """autotuner.py source must NOT contain ``trial.report`` or
    ``.should_prune``. Plan D3/T3: adding either is a methodology change —
    NopPruner makes it safe at runtime, but the static guard forces the
    implementing team to consciously consider whether the methodology
    pivot (Hyperband-style adaptive bracketing) actually applies. Surface
    to PM before merging any such addition.

    Scope: autotuner.py source only. Other modules (tests, docs) may
    legitimately reference these substrings.
    """
    forbidden = pin_contract["static_guard_contract"]["forbidden_substrings"]
    offenders: list[str] = []
    for needle in forbidden:
        if needle in autotuner_source:
            # Quote the line context for the failure message.
            for lineno, line in enumerate(autotuner_source.splitlines(), start=1):
                if needle in line:
                    offenders.append(f"{needle!r} at line {lineno}: {line.strip()}")
    assert not offenders, (
        "OPTUNA-2 static guard tripped (methodology-change tripwire): "
        "autotuner.py source must not contain trial.report or .should_prune. "
        "Adding either silently activates the implicit MedianPruner default "
        "and censors the BHY haircut's trial set. Surface to PM before "
        "merging — this may warrant a pivot to Hyperband + a haircut "
        "re-calibration. Offenders:\n  - " + "\n  - ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Source-comment contract: BHY / N_effective rationale at the pin
# ---------------------------------------------------------------------------


def test_pruner_pin_site_documents_bhy_and_n_effective(
    autotuner_source, autotuner_ast, pin_contract
):
    """Each ``create_study(..., pruner=...)`` site in autotuner.py must
    carry a nearby source-comment block that cites BHY/Harvey/haircut AND
    N_effective — the two math layers that depend on the complete trial
    set. The comment is what makes the methodology-change discipline
    visible at PR diff review.

    Window: comments within ~15 lines BEFORE the create_study call (inclusive
    of the line itself). 15 is a generous bound that admits a multi-paragraph
    rationale block without coupling to formatting choices.
    """
    must_any = pin_contract["source_comment_contract"]["required_substrings_any"]
    must_also = pin_contract["source_comment_contract"]["must_also_mention"]
    lines = autotuner_source.splitlines()
    pin_window = 15

    findings: list[str] = []
    for fn_name in (_RUN_AUTOTUNER, _RUN_CALIBRATION):
        func = _function_def(autotuner_ast, fn_name)
        for call in _create_study_calls(func):
            # Contract applies to EVERY create_study site whether or not the
            # pruner= kwarg is currently passed; the rationale block must
            # be in-place so the methodology-change-discipline is visible
            # at PR review regardless of impl half-states.
            top = max(0, call.lineno - 1 - pin_window)
            bottom = min(len(lines), call.lineno + pin_window)
            window_text = "\n".join(lines[top:bottom])
            has_any = any(token in window_text for token in must_any)
            has_must_also = must_also in window_text
            if not (has_any and has_must_also):
                findings.append(
                    f"{fn_name} create_study at line {call.lineno}: rationale "
                    f"comment within +/-{pin_window} lines must mention one of "
                    f"{must_any!r} AND the token {must_also!r}. "
                    f"Found any-of: {has_any}; must-also: {has_must_also}."
                )
    assert not findings, "OPTUNA-2 source-comment contract violations:\n  - " + "\n  - ".join(
        findings
    )


# ---------------------------------------------------------------------------
# Behavioural property: a study constructed with NopPruner has
# isinstance(study.pruner, NopPruner) — not MedianPruner
# ---------------------------------------------------------------------------


def test_nop_pruner_type_distinct_from_median_pruner(pin_contract):
    """Property pin: ``NopPruner`` and ``MedianPruner`` are not the same
    class. The behavioural tests below assert ``isinstance(study.pruner,
    NopPruner)`` and ``not isinstance(study.pruner, MedianPruner)``; this
    sanity-check ensures the two are genuinely distinct (a future Optuna
    upgrade that aliased them would make the assertion vacuous).
    """
    assert optuna.pruners.NopPruner is not optuna.pruners.MedianPruner, (
        "Sanity precondition: optuna.pruners.NopPruner and MedianPruner must "
        "be distinct classes for the OPTUNA-2 isinstance pin to be meaningful."
    )
    nop = optuna.pruners.NopPruner()
    median = optuna.pruners.MedianPruner()
    assert isinstance(nop, optuna.pruners.NopPruner)
    assert not isinstance(nop, optuna.pruners.MedianPruner)
    assert isinstance(median, optuna.pruners.MedianPruner)


def test_study_built_with_nop_pruner_reports_nop_pruner_instance(pin_contract):
    """End-to-end pin via the Optuna API surface: constructing a study with
    ``pruner=optuna.pruners.NopPruner()`` must produce ``study.pruner``
    that is an instance of NopPruner and NOT MedianPruner.

    This is the assertion the OPTUNA-2 fix must satisfy at BOTH autotuner
    sites; we exercise the API contract directly here so a future Optuna
    upgrade that changed create_study's storage of the pruner kwarg is
    caught independently of the AST tests above.
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize",
        pruner=optuna.pruners.NopPruner(),
        sampler=optuna.samplers.TPESampler(seed=0),
    )
    assert isinstance(study.pruner, optuna.pruners.NopPruner), (
        f"Sanity: create_study(pruner=NopPruner()) must yield "
        f"isinstance(study.pruner, NopPruner); got {type(study.pruner)!r}."
    )
    assert not isinstance(study.pruner, optuna.pruners.MedianPruner), (
        "OPTUNA-2 contract: study.pruner must NOT be a MedianPruner."
    )


def test_implicit_default_is_median_pruner_negative_control():
    """Negative control: confirm that Optuna's implicit default IS
    MedianPruner today. If this control ever fails (Optuna changed its
    default), the OPTUNA-2 finding's framing needs re-evaluation — but
    the explicit-pin fix remains correct regardless, because the audit
    rationale is "make the choice explicit," not "MedianPruner is the
    specific enemy."
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    assert isinstance(study.pruner, optuna.pruners.MedianPruner), (
        "Optuna's implicit-default pruner has changed away from "
        f"MedianPruner (got {type(study.pruner)!r}). The OPTUNA-2 finding's "
        "implicit-default framing should be re-checked; the explicit-pin "
        "fix remains valid."
    )
