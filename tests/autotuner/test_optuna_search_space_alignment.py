"""RED tests for OPTUNA-9a (search-space bound asymmetry documentation) audit fix.

Background — math re-audit finding OPTUNA-9a (LOW):

The Optuna search-space bounds for ``VWAP_CROSS_HWM_PCT`` differ between
the production walk-forward path and the V1 calibration sweep:

- Production (``run_autotuner`` objective, autotuner.py:~1583):
  ``trial.suggest_float("VWAP_CROSS_HWM_PCT", 0.5, 2.5)``
  (via ``_SS_VWAP_CROSS_HWM_MIN`` / ``_SS_VWAP_CROSS_HWM_MAX``)

- V1 calibration sweep (``run_calibration_sweep`` objective,
  autotuner.py:~2020):
  ``trial.suggest_float("VWAP_CROSS_HWM_PCT", 0.3, 2.0)``
  (via ``_SS_VWAP_CROSS_HWM_V1_MIN`` / ``_SS_VWAP_CROSS_HWM_V1_MAX``)

OPTUNA-9a is an HONEST PROVENANCE ASYMMETRY: the bounds genuinely differ
for math reasons (the lower bound 0.3 sits below 0.5 because the 3-tick
confirm gate in ``math_engine`` prevents spurious single-tick exits at
that level, giving the calibration sweep more room to find the true
optimum; the upper bound 2.0 sits below 2.5 because above ~2sigma daily
return System A is effectively disabled for normal sessions and
calibration becomes unreliable). The decision is Path B (documented
asymmetry), NOT Path A (alignment) — Path A would either weaken the
production floor (widening to 0.3) or lose the wider V1 exploration
(narrowing to 0.5), both of which are methodology changes.

Path B requires three documentation surfaces:

1. The V1 bound constants must carry a source-comment block explaining
   the bound differences AND citing at least one math-rationale token
   (3-tick confirm gate / ~2sigma / System A).
   STATUS ON HEAD: the block at autotuner.py:189-195 already satisfies
   this; the test is a regression guard that trips if a future PR
   strips the explanatory comment.

2. The ``run_calibration_sweep`` docstring must explicitly mention the
   bound asymmetry (the facet name + a directionality word like
   ``narrowed`` / ``differs from`` / ``production``).
   STATUS ON HEAD: the current docstring only says "search space is
   limited to the two V1 parameters" — silent on bounds. RED.

3. The production-side constants must carry a discoverability comment
   pointing at the V1 narrowed pair (and vice versa) so a reader
   following EITHER call chain sees the asymmetry at the constant site
   without tracing.
   STATUS ON HEAD: the production constants (lines 178-179) carry no
   "see also V1" pointer. RED.

Tests are located by AST + module attribute inspection, NOT by line
number — refactors that re-flow the sites without preserving the
documentation contract still fail. All tests RED on
cycle/optuna-9a-search-space @ 223b053 (the fork-point commit before
any GREEN impl), except for the regression guards which are RED only on
a regression.
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
    / "optuna_search_space_alignment_contract.json"
)


@pytest.fixture(scope="module")
def alignment_contract() -> dict:
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
# AST helpers
# ---------------------------------------------------------------------------

def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"AST: expected to find function def {name!r} in autotuner.py — "
        "the test cannot run without it. Did the function get renamed?"
    )


def _module_float_assignments(tree: ast.Module) -> dict[str, tuple[float, int]]:
    """Return {target_name: (value, lineno)} for module-level numeric (int OR
    float) constant assignments. Used to locate the search-space bound
    constants and the source line of each so the comment-proximity test can
    scan the surrounding window."""
    out: dict[str, tuple[float, int]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        v = node.value
        # Accept plain Constant(float|int) and UnaryOp(-Constant(...)) for
        # forward compatibility — none of the V1/production bounds are
        # negative today, but the helper should not silently miss them.
        if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) and not isinstance(v.value, bool):
            value = float(v.value)
        elif (
            isinstance(v, ast.UnaryOp)
            and isinstance(v.op, ast.USub)
            and isinstance(v.operand, ast.Constant)
            and isinstance(v.operand.value, (int, float))
            and not isinstance(v.operand.value, bool)
        ):
            value = -float(v.operand.value)
        else:
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = (value, node.lineno)
    return out


def _suggest_float_calls_for_facet(
    func: ast.FunctionDef, facet: str
) -> list[ast.Call]:
    """Return every ``trial.suggest_float("<facet>", ...)`` call inside func.

    Matches on the first positional arg being a Constant string equal to
    facet — which is how both call sites are written today. If a future
    refactor moves the facet name to a kwarg, the helper will need
    updating; the test will surface that as a precondition failure with a
    clear message rather than silently passing on zero matches.
    """
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        is_suggest_float = (
            isinstance(f, ast.Attribute) and f.attr == "suggest_float"
        )
        if not is_suggest_float:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == facet:
            calls.append(node)
    return calls


def _comment_window(source: str, lineno: int, span: int) -> str:
    """Return the source lines from (lineno - span) to (lineno + span),
    joined. Used to scan the comment context surrounding a constant
    definition.
    """
    lines = source.splitlines()
    start = max(0, lineno - 1 - span)
    end = min(len(lines), lineno + span)
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Tier 1 — Named-constant existence + asymmetry-is-real invariant
# ---------------------------------------------------------------------------

def test_production_and_v1_bound_constants_exist_as_module_attrs(
    autotuner_module, alignment_contract
):
    """All four named bound constants (production min/max + V1 min/max for
    VWAP_CROSS_HWM_PCT) must exist as module-level float attributes on
    autotuner. If a future refactor renames any of them, the fixture must
    be updated in the same diff — silent renames break every downstream
    test in this file.
    """
    names = alignment_contract["named_constants_contract"]
    expected = [
        names["production_min_name"],
        names["production_max_name"],
        names["v1_min_name"],
        names["v1_max_name"],
    ]
    missing: list[str] = []
    for n in expected:
        val = getattr(autotuner_module, n, None)
        if val is None or not isinstance(val, (int, float)) or isinstance(val, bool):
            missing.append(n)
    assert not missing, (
        f"OPTUNA-9a precondition: expected module-level numeric constants "
        f"{expected} to exist on autotuner; missing or non-numeric: {missing}. "
        f"Either restore the names or update the fixture's "
        f"named_constants_contract block to match the new names."
    )


def test_production_bounds_genuinely_differ_from_v1_bounds(
    autotuner_module, alignment_contract
):
    """OPTUNA-9a is explicitly an ASYMMETRY finding. If a future diff
    silently collapses the V1 bounds onto the production bounds (or vice
    versa), the documentation contract this cycle adds is moot — the
    asymmetry-is-documented invariant must be defended at the value
    level.

    This test trips loudly on accidental Path-A drift dressed up as
    Path B. If a future cycle DECIDES to align (Path A), the alignment
    must update both this test AND the documentation contract in the
    same diff.
    """
    names = alignment_contract["named_constants_contract"]
    if not alignment_contract["named_constants_contract"][
        "production_bounds_must_differ_from_v1"
    ]:
        pytest.skip(
            "Fixture has flipped to Path A (alignment); this asymmetry "
            "invariant is intentionally inert."
        )
    prod_min = float(getattr(autotuner_module, names["production_min_name"]))
    prod_max = float(getattr(autotuner_module, names["production_max_name"]))
    v1_min = float(getattr(autotuner_module, names["v1_min_name"]))
    v1_max = float(getattr(autotuner_module, names["v1_max_name"]))
    # Exact equality check is correct here: these are literal source-coded
    # bounds, not the result of arithmetic. If they ever match, that is a
    # source-code collapse and a methodology change.
    assert (prod_min, prod_max) != (v1_min, v1_max), (
        f"OPTUNA-9a asymmetry-is-real: production VWAP_CROSS_HWM_PCT "
        f"bounds [{prod_min}, {prod_max}] match V1 bounds "
        f"[{v1_min}, {v1_max}]. If alignment was deliberate (Path A) it "
        f"is a methodology change requiring PM surface AND a fixture "
        f"update (set production_bounds_must_differ_from_v1=false). "
        f"Silent collapse defeats the documented-asymmetry contract."
    )


# ---------------------------------------------------------------------------
# Tier 2 — V1 source-comment block (regression guard for existing comment)
# ---------------------------------------------------------------------------

def test_v1_min_bound_constant_carries_asymmetry_source_comment(
    autotuner_source, autotuner_ast, alignment_contract
):
    """The V1 lower-bound constant must carry a source comment within ~12
    lines citing the asymmetry (one of: ``narrowed``, ``asymmetry``,
    ``calibration sweep``, ``V1 calibration``, ``differs from``) AND at
    least one math-rationale token (3-tick / confirm gate / sigma /
    System A).

    This is a regression guard: the existing block at autotuner.py:189-195
    already satisfies the contract. The test trips if a future PR strips
    the explanatory comment — defeats the audit fix.
    """
    names = alignment_contract["named_constants_contract"]
    v1_min_name = names["v1_min_name"]
    label_tokens = alignment_contract["documentation_contract"][
        "v1_constant_comment_required_substrings_any"
    ]
    math_tokens = alignment_contract["documentation_contract"][
        "v1_constant_comment_required_math_token_any"
    ]

    assigns = _module_float_assignments(autotuner_ast)
    assert v1_min_name in assigns, (
        f"AST: expected module-level numeric assignment {v1_min_name!r} in "
        f"autotuner.py. Found keys (sample): "
        f"{sorted(k for k in assigns.keys() if 'VWAP' in k)}"
    )
    _val, lineno = assigns[v1_min_name]
    window = _comment_window(autotuner_source, lineno, span=12)

    has_label = any(tok in window for tok in label_tokens)
    has_math = any(tok in window for tok in math_tokens)

    assert has_label and has_math, (
        f"OPTUNA-9a V1-constant source-comment regression: the comment "
        f"window (~12 lines) around {v1_min_name} (defined at line "
        f"{lineno}) must contain at least one ASYMMETRY-label token from "
        f"{label_tokens} AND at least one MATH-rationale token from "
        f"{math_tokens}. Got label={has_label}, math={has_math}. Window:\n"
        f"---\n{window}\n---"
    )


# ---------------------------------------------------------------------------
# Tier 3 — Call-site discipline (both sites reference the named constants)
# ---------------------------------------------------------------------------

def test_production_site_suggest_float_references_production_bound_names(
    autotuner_ast, alignment_contract
):
    """The production-path ``trial.suggest_float("VWAP_CROSS_HWM_PCT", ...)``
    call inside run_autotuner must reference the named production bound
    constants — NOT float literals, and NOT the V1 constants.

    The test is a regression guard: the current call site at
    autotuner.py:1583 already uses the production-named constants. The
    test trips on (a) a literal-collapse refactor, or (b) a wrong-pair
    refactor that has run_autotuner suddenly reach for the V1 narrow
    pair.
    """
    cc = alignment_contract["call_site_contract"]
    names = alignment_contract["named_constants_contract"]
    facet = cc["facet_name"]
    prod_func_name = cc["production_site_function"]
    expected_min = names["production_min_name"]
    expected_max = names["production_max_name"]
    v1_min = names["v1_min_name"]
    v1_max = names["v1_max_name"]

    func = _function_def(autotuner_ast, prod_func_name)
    calls = _suggest_float_calls_for_facet(func, facet)
    assert calls, (
        f"AST: expected at least one trial.suggest_float({facet!r}, ...) "
        f"call in {prod_func_name}; none found. Did the facet name move "
        f"to a kwarg, or did the call get refactored away?"
    )

    offenders: list[str] = []
    for call in calls:
        # Positional args: [name, low, high, ...]. Most call sites pass
        # low/high positionally; if a future refactor moves them to kwargs
        # the helper below catches both forms.
        low_expr: ast.expr | None = call.args[1] if len(call.args) >= 2 else None
        high_expr: ast.expr | None = call.args[2] if len(call.args) >= 3 else None
        for kw in call.keywords:
            if kw.arg == "low":
                low_expr = kw.value
            elif kw.arg == "high":
                high_expr = kw.value

        if low_expr is None or high_expr is None:
            offenders.append(
                f"line {call.lineno}: suggest_float({facet!r}, ...) missing "
                f"low or high — cannot pin call-site discipline."
            )
            continue

        for label, expr, want, forbid in (
            ("low", low_expr, expected_min, v1_min),
            ("high", high_expr, expected_max, v1_max),
        ):
            if isinstance(expr, ast.Constant) and isinstance(expr.value, (int, float)):
                offenders.append(
                    f"line {call.lineno}: suggest_float({facet!r}, ...) "
                    f"{label}={expr.value!r} is a numeric literal — must "
                    f"reference the named constant {want!r} per OPTUNA-9a."
                )
            elif isinstance(expr, ast.Name):
                if expr.id == forbid:
                    offenders.append(
                        f"line {call.lineno}: suggest_float({facet!r}, ...) "
                        f"{label}={expr.id} references the V1-sweep constant "
                        f"at the PRODUCTION site — wrong-pair refactor."
                    )
                elif expr.id != want:
                    offenders.append(
                        f"line {call.lineno}: suggest_float({facet!r}, ...) "
                        f"{label}={expr.id} does not reference the expected "
                        f"production-path constant {want!r}."
                    )
            else:
                offenders.append(
                    f"line {call.lineno}: suggest_float({facet!r}, ...) "
                    f"{label} is {ast.dump(expr)} — must be a Name reference "
                    f"to {want!r}."
                )

    assert not offenders, (
        "OPTUNA-9a production-site call-site violations:\n  - "
        + "\n  - ".join(offenders)
    )


def test_v1_site_suggest_float_references_v1_bound_names(
    autotuner_ast, alignment_contract
):
    """The V1-sweep ``trial.suggest_float("VWAP_CROSS_HWM_PCT", ...)`` call
    inside run_calibration_sweep must reference the named V1 bound
    constants — NOT float literals, and NOT the production constants.

    Regression guard: the current call site at autotuner.py:2020-2021
    already uses the V1-named constants. The test trips on (a) literal
    collapse, or (b) a wrong-pair refactor that has run_calibration_sweep
    silently widen to the production bounds.
    """
    cc = alignment_contract["call_site_contract"]
    names = alignment_contract["named_constants_contract"]
    facet = cc["facet_name"]
    v1_func_name = cc["v1_site_function"]
    expected_min = names["v1_min_name"]
    expected_max = names["v1_max_name"]
    prod_min = names["production_min_name"]
    prod_max = names["production_max_name"]

    func = _function_def(autotuner_ast, v1_func_name)
    calls = _suggest_float_calls_for_facet(func, facet)
    assert calls, (
        f"AST: expected at least one trial.suggest_float({facet!r}, ...) "
        f"call in {v1_func_name}; none found."
    )

    offenders: list[str] = []
    for call in calls:
        low_expr: ast.expr | None = call.args[1] if len(call.args) >= 2 else None
        high_expr: ast.expr | None = call.args[2] if len(call.args) >= 3 else None
        for kw in call.keywords:
            if kw.arg == "low":
                low_expr = kw.value
            elif kw.arg == "high":
                high_expr = kw.value

        if low_expr is None or high_expr is None:
            offenders.append(
                f"line {call.lineno}: suggest_float({facet!r}, ...) missing "
                f"low or high — cannot pin call-site discipline."
            )
            continue

        for label, expr, want, forbid in (
            ("low", low_expr, expected_min, prod_min),
            ("high", high_expr, expected_max, prod_max),
        ):
            if isinstance(expr, ast.Constant) and isinstance(expr.value, (int, float)):
                offenders.append(
                    f"line {call.lineno}: suggest_float({facet!r}, ...) "
                    f"{label}={expr.value!r} is a numeric literal — must "
                    f"reference the V1 named constant {want!r}."
                )
            elif isinstance(expr, ast.Name):
                if expr.id == forbid:
                    offenders.append(
                        f"line {call.lineno}: suggest_float({facet!r}, ...) "
                        f"{label}={expr.id} references the PRODUCTION "
                        f"constant at the V1 site — wrong-pair refactor "
                        f"silently widens the calibration sweep."
                    )
                elif expr.id != want:
                    offenders.append(
                        f"line {call.lineno}: suggest_float({facet!r}, ...) "
                        f"{label}={expr.id} does not reference the expected "
                        f"V1 constant {want!r}."
                    )
            else:
                offenders.append(
                    f"line {call.lineno}: suggest_float({facet!r}, ...) "
                    f"{label} is {ast.dump(expr)} — must be a Name reference "
                    f"to {want!r}."
                )

    assert not offenders, (
        "OPTUNA-9a V1-site call-site violations:\n  - "
        + "\n  - ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Tier 4 — Documentation contract (RED on HEAD)
# ---------------------------------------------------------------------------

def test_calibration_sweep_docstring_mentions_bound_asymmetry(
    autotuner_module, alignment_contract
):
    """The ``run_calibration_sweep`` docstring must explicitly mention the
    bound asymmetry between the V1 sweep and the production path.

    The current docstring (HEAD @ 223b053) only says "search space is
    limited to the two V1 parameters" — that is necessary but not
    sufficient: it does NOT tell a reader that the BOUNDS for those
    parameters differ from the production walk-forward path. The reader
    must trace into the constants to discover the asymmetry. Per Path B
    of OPTUNA-9a, the docstring is one of the three documentation
    surfaces that must make the asymmetry visible.

    RED on HEAD: the docstring contains the facet identifier
    'VWAP_CROSS_HWM_PCT' (it must, to say what the sweep tunes) but no
    directionality token explaining that the bounds for that facet are
    NARROWER / DIFFER FROM / etc. the production path.
    """
    directionality_tokens = alignment_contract["documentation_contract"][
        "calibration_docstring_required_directionality_any"
    ]
    facet_token = alignment_contract["documentation_contract"][
        "calibration_docstring_facet_token"
    ]

    func = getattr(autotuner_module, "run_calibration_sweep", None)
    assert func is not None and callable(func), (
        "Precondition: autotuner.run_calibration_sweep must exist."
    )
    doc = func.__doc__ or ""

    # Facet identifier is matched case-sensitively (the codebase spells it
    # 'VWAP_CROSS_HWM_PCT' uppercase, and a lowercase variant would be a
    # different identifier). Directionality tokens are matched
    # case-insensitively so the fixture stays human-readable.
    has_facet = facet_token in doc
    haystack_lower = doc.lower()
    has_directionality = any(t.lower() in haystack_lower for t in directionality_tokens)

    assert has_facet and has_directionality, (
        f"OPTUNA-9a docstring contract: run_calibration_sweep.__doc__ must "
        f"mention BOTH the facet identifier {facet_token!r} AND at least "
        f"one DIRECTIONALITY token from {directionality_tokens}. Got: "
        f"has_facet={has_facet}, has_directionality={has_directionality}. "
        f"Current docstring:\n---\n{doc}\n---\n"
        f"A reader of this function should see the bound asymmetry without "
        f"tracing into the constants."
    )


def test_production_bound_constants_carry_v1_cross_reference(
    autotuner_source, autotuner_ast, alignment_contract
):
    """The PRODUCTION VWAP_CROSS_HWM_PCT bound constants must carry a
    discoverability source comment within ~10 lines that references the
    V1-narrowed sibling pair — so a reader following the call chain into
    run_autotuner sees the asymmetry at the constant site, not only when
    they happen to scroll further down into the V1 block.

    The reverse direction (V1 -> production) is already covered by the
    existing block at autotuner.py:189-195 which begins "V1 calibration
    sweep — narrowed VWAP_CROSS_HWM_PCT bounds." There is no equivalent
    pointer at the production constants today (lines 178-179) — RED.

    Acceptable cross-reference forms (case-insensitive, any one):
      - the V1 constant name (e.g. ``_SS_VWAP_CROSS_HWM_V1_MIN``)
      - the literal string ``V1 calibration``
      - ``see V1`` / ``narrower V1`` / similar — captured by the token
        ``V1``  appearing within the production-constant comment window
        AND at least one directionality token (``narrower`` / ``wider`` /
        ``calibration sweep`` / ``differs``).
    """
    names = alignment_contract["named_constants_contract"]
    prod_min_name = names["production_min_name"]
    v1_min_name = names["v1_min_name"]
    v1_max_name = names["v1_max_name"]

    assigns = _module_float_assignments(autotuner_ast)
    assert prod_min_name in assigns, (
        f"AST: expected module-level numeric assignment {prod_min_name!r} "
        f"in autotuner.py. Found VWAP-related keys: "
        f"{sorted(k for k in assigns.keys() if 'VWAP' in k)}"
    )
    _val, lineno = assigns[prod_min_name]
    window = _comment_window(autotuner_source, lineno, span=10)

    # Acceptable cross-reference: explicit V1 constant name reference OR
    # the phrase 'V1 calibration' OR the token 'V1' paired with at least
    # one directionality token.
    explicit_name_ref = (v1_min_name in window) or (v1_max_name in window)
    v1_phrase = "V1 calibration" in window
    directionality_tokens = (
        "narrower",
        "wider",
        "calibration sweep",
        "differs",
        "asymmetry",
    )
    v1_token = "V1" in window
    has_directionality = any(t.lower() in window.lower() for t in directionality_tokens)
    paired = v1_token and has_directionality

    assert explicit_name_ref or v1_phrase or paired, (
        f"OPTUNA-9a production-side cross-reference contract: the comment "
        f"window (~10 lines) around {prod_min_name} (defined at line "
        f"{lineno}) must point at the V1-narrowed sibling pair via one of: "
        f"(a) explicit name reference to {v1_min_name!r} or {v1_max_name!r}, "
        f"(b) the phrase 'V1 calibration', or (c) the token 'V1' paired with "
        f"a directionality token from {directionality_tokens}. Got: "
        f"explicit_name_ref={explicit_name_ref}, v1_phrase={v1_phrase}, "
        f"paired={paired}. Window:\n---\n{window}\n---"
    )


# ---------------------------------------------------------------------------
# Tier 5 — Regression guards (sister audit fixes UNCHANGED)
# ---------------------------------------------------------------------------

def test_optuna_1_2_6_7_regression_guards_preserved(
    autotuner_module, autotuner_ast, alignment_contract
):
    """OPTUNA-9a must NOT regress OPTUNA-1 (TPESampler pin), OPTUNA-2
    (NopPruner pin), OPTUNA-6 (n_jobs env resolver), or OPTUNA-7
    (n_trials named constants). The OPTUNA-9a diff is targeted to the
    VWAP_CROSS_HWM_PCT bound-asymmetry documentation only — the sister
    audit machinery is in scope to preserve.

    This is a single guard test rather than four separate ones because
    each sister audit already has its own dedicated test file
    (test_optuna_sampler_pin_and_n_jobs.py, test_optuna_pruner_pinned.py,
    test_optuna_n_trials_named.py). The check here is a cheap structural
    sweep so OPTUNA-9a does not have to rely on cross-file ordering for
    the full suite to surface a sister-audit regression.
    """
    rg = alignment_contract["regression_guards"]

    # OPTUNA-6: n_jobs env helper exists.
    helper_name = rg["optuna_6_n_jobs_helper_name"]
    helper = getattr(autotuner_module, helper_name, None)
    assert helper is not None and callable(helper), (
        f"OPTUNA-6 regression: autotuner.{helper_name} must continue to "
        f"exist and be callable."
    )

    # OPTUNA-7: production + calibration n_trials values are still exposed
    # as module-level named ints somewhere on the module.
    prod_n_trials = int(rg["optuna_7_production_n_trials_value"])
    calib_n_trials = int(rg["optuna_7_calibration_n_trials_value"])
    int_attrs = {
        n: getattr(autotuner_module, n)
        for n in dir(autotuner_module)
        if isinstance(getattr(autotuner_module, n, None), int)
        and not isinstance(getattr(autotuner_module, n, None), bool)
    }
    assert any(v == prod_n_trials for v in int_attrs.values()), (
        f"OPTUNA-7 regression: no module-level int constant on autotuner "
        f"has the production n_trials value {prod_n_trials}."
    )
    assert any(v == calib_n_trials for v in int_attrs.values()), (
        f"OPTUNA-7 regression: no module-level int constant on autotuner "
        f"has the calibration n_trials value {calib_n_trials}."
    )

    # OPTUNA-1 + OPTUNA-2: every create_study(...) in both run_autotuner
    # and run_calibration_sweep still passes sampler=TPESampler(...) AND
    # pruner=NopPruner().
    for func_name in ("run_autotuner", "run_calibration_sweep"):
        func = _function_def(autotuner_ast, func_name)
        create_calls = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_study"
        ]
        assert create_calls, (
            f"AST: expected optuna.create_study(...) in {func_name}; none "
            f"found — likely a sister-audit regression."
        )
        for call in create_calls:
            kw_names = {kw.arg for kw in call.keywords}
            assert "sampler" in kw_names, (
                f"OPTUNA-1 regression in {func_name} (line {call.lineno}): "
                f"create_study(...) missing sampler= kwarg."
            )
            assert "pruner" in kw_names, (
                f"OPTUNA-2 regression in {func_name} (line {call.lineno}): "
                f"create_study(...) missing pruner= kwarg."
            )
