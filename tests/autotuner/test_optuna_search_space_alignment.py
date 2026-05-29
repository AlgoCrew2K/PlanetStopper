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
for math reasons. The relationship is MIXED-DIRECTION, not strict
narrowing:

- V1 lower 0.3 < production lower 0.5 — V1 EXPANDS the lower bound below
  production (the 3-tick confirm gate in ``math_engine`` prevents spurious
  single-tick exits, giving the calibration sweep more room below).
- V1 upper 2.0 < production upper 2.5 — V1 NARROWS the upper bound below
  production (above ~2sigma daily return System A is effectively disabled
  and calibration becomes unreliable).

The decision is Path B (documented asymmetry), NOT Path A (alignment) —
Path A would either weaken the production floor or lose the wider V1
exploration the audited reasoning validates.

Path B requires three documentation surfaces and two anti-misframing
rules (opt-optuna9a R1 revise):

1. V1 bound constants source-comment block — citing the asymmetry +
   per-direction math rationale (3-tick confirm gate / ~2sigma /
   System A). Per opt-optuna9a BLOCK-1 the standalone words ``narrowed``
   and ``narrower`` are now FORBIDDEN as the only directionality label
   (they misdescribe a tighter box; the box is asymmetric, not narrower).
   The block must use ``asymmetric`` / ``mixed`` / explicit
   ``lower expands`` + ``narrows the upper`` framing OR pair a
   per-direction acknowledgement with both ``lower`` and ``upper``
   present.

2. ``run_calibration_sweep`` docstring — same asymmetric framing
   requirement applied to the docstring Note paragraph that calls out
   the V1-vs-production bound difference.

3. Production-side constants discoverability comment pointing at the V1
   sibling pair (so a reader following the production call chain sees
   the asymmetry at the production constant site, not only by scrolling
   down to the V1 block).

4. Out-of-production-range NOTE (opt-optuna9a R1 NOTE) — at least one of
   the documentation surfaces must explicitly acknowledge that
   calibration proposals in [0.3, 0.5) fall OUTSIDE the production
   walk-forward search space and the production optimizer therefore
   cannot reproduce them. Read-only / operator-gated machinery means
   this is not a mechanics bug — but it is a non-obvious operator
   caveat that must be visible.

Tests are located by AST + module attribute inspection, NOT by line
number — refactors that re-flow the sites without preserving the
documentation contract still fail.

STATUS ON THE R1 REVISE FORK POINT (28e49ae):
  Tier 1-3 + Tier 5 regression guards: GREEN (cycle-1 GREEN preserved).
  Tier 4 documentation contract: GREEN under the cycle-1 wording (which
  used ``narrowed`` / ``narrower``).
  Tier 6 (mixed-direction acknowledgement): RED — both surfaces use
  ``narrowed`` / ``narrower`` standalone without per-direction framing.
  Tier 7 (forbidden-bare-narrower negative): RED — both surfaces use
  the now-forbidden bare framing.
  Tier 8 (out-of-production-range NOTE): RED — neither surface
  acknowledges the [0.3, 0.5) operator caveat.
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

    NOTE: this fixed-span window can BLEED across unrelated comment
    blocks (e.g. the Sortino objective block immediately below the V1
    bounds). Prefer ``_adjacent_comment_block`` for tests that must
    confine themselves to the constant's OWN documentation block.
    """
    lines = source.splitlines()
    start = max(0, lineno - 1 - span)
    end = min(len(lines), lineno + span)
    return "\n".join(lines[start:end])


def _adjacent_comment_block(source: str, lineno: int) -> str:
    """Return the contiguous comment block IMMEDIATELY preceding the line
    at ``lineno`` (1-indexed). Scans upward from ``lineno - 1``, including
    consecutive lines whose stripped content starts with ``#``, and stops
    on the first non-comment / non-blank line. Blank lines are tolerated
    inside the block (multi-paragraph docstrings) by stopping only on a
    non-comment non-blank line.

    The returned string is the JOINED block lines in source order, with
    no trailing newline. If no adjacent comment block exists, returns
    an empty string.

    Used for the mixed-direction + bare-narrower tests, which must confine
    themselves to the V1 constant's OWN documentation block — a fixed
    ~12-line window bleeds into the Sortino objective comment block
    immediately below the V1 bounds and contaminates the asymmetric-token
    probe with an unrelated 'asymmetric utility' phrase.
    """
    lines = source.splitlines()
    # Convert to 0-indexed. lineno is the constant's own line; scan
    # backwards from the line BEFORE it.
    idx = lineno - 2
    collected: list[str] = []
    seen_comment = False
    while idx >= 0:
        stripped = lines[idx].lstrip()
        if stripped.startswith("#"):
            collected.append(lines[idx])
            seen_comment = True
            idx -= 1
            continue
        if stripped == "":
            # Blank line — keep scanning IFF we haven't yet seen any
            # comment lines (leading whitespace before the block) OR if
            # we have, only one blank is tolerated as a paragraph break.
            # To keep semantics simple and predictable, stop on any
            # blank line AFTER we have started collecting comments.
            if seen_comment:
                break
            idx -= 1
            continue
        # Non-comment, non-blank — block boundary.
        break
    # collected is in reverse source order; reverse to restore.
    collected.reverse()
    return "\n".join(collected)


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


# ---------------------------------------------------------------------------
# Tier 6 — Mixed-direction acknowledgement (opt-optuna9a R1 BLOCK-1)
#
# Per opt-optuna9a review of cycle-1 GREEN @ 28e49ae: both documentation
# surfaces (V1 source-comment block AND run_calibration_sweep docstring)
# must EXPLICITLY acknowledge that the asymmetry is mixed-direction —
# V1 lower 0.3 EXPANDS below production lower 0.5; V1 upper 2.0 NARROWS
# below production upper 2.5. A reader of either surface must see this
# without tracing into the numeric constants.
#
# Acceptable acknowledgement forms (either is sufficient):
#   (a) an explicit asymmetric/mixed token AND the words 'lower' AND
#       'upper' both present in the documentation window — so the reader
#       can map the asymmetric label onto the per-direction behaviour, OR
#   (b) a long-form phrase that names a per-direction behaviour directly
#       (e.g. 'lower expands', 'narrows the upper', 'expands below').
# ---------------------------------------------------------------------------


def _has_mixed_direction_acknowledgement(window: str, contract: dict) -> tuple[bool, dict]:
    """Return (ok, diagnostic) for whether the given documentation window
    satisfies the mixed_direction_contract. Diagnostic is a dict of the
    individual probes so the assertion message can show which check failed.
    """
    haystack_lower = window.lower()
    asym_tokens = contract["asymmetric_or_mixed_tokens_any"]
    longform_phrases = contract["long_form_directional_phrases_any"]
    lower_token = contract["lower_token"]
    upper_token = contract["upper_token"]

    has_asym = any(t.lower() in haystack_lower for t in asym_tokens)
    has_longform = any(p.lower() in haystack_lower for p in longform_phrases)
    has_lower = lower_token.lower() in haystack_lower
    has_upper = upper_token.lower() in haystack_lower

    # Form (a): asymmetric token paired with both lower AND upper present
    form_a = has_asym and has_lower and has_upper
    # Form (b): long-form per-direction phrase alone
    form_b = has_longform

    diagnostic = {
        "has_asymmetric_token": has_asym,
        "has_long_form_phrase": has_longform,
        "has_lower_token": has_lower,
        "has_upper_token": has_upper,
        "form_a_asym_paired_lower_upper": form_a,
        "form_b_long_form_phrase": form_b,
    }
    return (form_a or form_b), diagnostic


def test_v1_source_comment_block_acknowledges_mixed_direction(
    autotuner_source, autotuner_ast, alignment_contract
):
    """V1 source-comment block (within ~12 lines of the
    ``_SS_VWAP_CROSS_HWM_V1_MIN`` definition) must satisfy the
    mixed-direction acknowledgement: either an asymmetric/mixed token
    PAIRED with both ``lower`` AND ``upper`` present, OR a long-form
    per-direction phrase like ``lower expands`` / ``narrows the upper``.

    STATUS ON 28e49ae (cycle-1 GREEN): RED. The V1 block at
    autotuner.py:189-195 uses the bare framing 'V1 calibration sweep —
    narrowed VWAP_CROSS_HWM_PCT bounds.' Per opt-optuna9a BLOCK-1 this
    misdescribes the relationship — the lower bound EXPANDS below
    production, it does not narrow.
    """
    names = alignment_contract["named_constants_contract"]
    v1_min_name = names["v1_min_name"]
    contract = alignment_contract["mixed_direction_contract"]

    assigns = _module_float_assignments(autotuner_ast)
    assert v1_min_name in assigns, (
        f"AST: expected module-level numeric assignment {v1_min_name!r}."
    )
    _val, lineno = assigns[v1_min_name]
    # Use the V1 constant's OWN adjacent comment block so the
    # acknowledgement probe is confined to the V1 documentation surface
    # — a fixed ~12-line window bleeds into unrelated downstream
    # comments (e.g. the Sortino objective block at lines 197+ which
    # legitimately uses 'asymmetric utility' in a different sense).
    window = _adjacent_comment_block(autotuner_source, lineno)
    assert window, (
        f"Precondition: expected an adjacent comment block immediately "
        f"above {v1_min_name} (line {lineno}). None found — the V1 "
        f"constant lost its documentation block entirely."
    )

    ok, diag = _has_mixed_direction_acknowledgement(window, contract)
    assert ok, (
        f"OPTUNA-9a R1 mixed-direction acknowledgement (V1 block): the "
        f"adjacent comment block above {v1_min_name} (line {lineno}) "
        f"must EITHER (a) contain an asymmetric/mixed token PAIRED with "
        f"both 'lower' AND 'upper', OR (b) contain a long-form "
        f"per-direction phrase. Diagnostic: {diag}. Per opt-optuna9a "
        f"BLOCK-1: a bare 'narrowed' framing is forbidden because "
        f"V1.lower (0.3) actually EXPANDS below production.lower (0.5). "
        f"V1 block:\n---\n{window}\n---"
    )


def test_calibration_sweep_docstring_acknowledges_mixed_direction(
    autotuner_module, alignment_contract
):
    """``run_calibration_sweep.__doc__`` must satisfy the mixed-direction
    acknowledgement under the same forms as the V1 block test.

    STATUS ON 28e49ae (cycle-1 GREEN): RED. The docstring Note paragraph
    says "are narrower than the production walk-forward bounds" — bare
    'narrower than' framing, no per-direction acknowledgement.
    """
    contract = alignment_contract["mixed_direction_contract"]
    func = getattr(autotuner_module, "run_calibration_sweep", None)
    assert func is not None and callable(func), (
        "Precondition: autotuner.run_calibration_sweep must exist."
    )
    doc = func.__doc__ or ""

    ok, diag = _has_mixed_direction_acknowledgement(doc, contract)
    assert ok, (
        f"OPTUNA-9a R1 mixed-direction acknowledgement (docstring): "
        f"run_calibration_sweep.__doc__ must EITHER (a) contain an "
        f"asymmetric/mixed token PAIRED with both 'lower' AND 'upper', "
        f"OR (b) contain a long-form per-direction phrase. Diagnostic: "
        f"{diag}. Per opt-optuna9a BLOCK-1: a bare 'narrower than' "
        f"framing is forbidden because V1.lower (0.3) actually EXPANDS "
        f"below production.lower (0.5). Current docstring:\n---\n{doc}\n---"
    )


# ---------------------------------------------------------------------------
# Tier 7 — Forbidden bare-narrower framing (opt-optuna9a R1 BLOCK-1, negative)
#
# Negative invariant: the documentation MUST NOT rely on a bare
# 'narrowed' or 'narrower' framing as its sole directionality label. The
# words themselves are not forbidden — what's forbidden is using them
# WITHOUT compensating per-direction language ('lower' + 'upper' pairing
# OR a long-form per-direction phrase). This complements Tier 6: Tier 6
# requires the positive acknowledgement; Tier 7 trips on the negative
# misframing if the compensating language is absent.
#
# A future PR that adds 'narrowed' or 'narrower' without per-direction
# context still trips this test. A docstring like "the upper bound is
# narrower than production while the lower expands below it" satisfies
# Tier 7 because 'lower' and 'upper' are both present.
# ---------------------------------------------------------------------------


_FORBIDDEN_BARE_TOKENS = ("narrowed", "narrower")


def _has_bare_narrower_framing(text: str, mixed_contract: dict) -> tuple[bool, str]:
    """Return (offending, evidence) where offending=True means the text
    contains one of the bare ``narrowed`` / ``narrower`` tokens WITHOUT
    the compensating mixed-direction acknowledgement that would make the
    framing accurate. Evidence is the matched token or empty string.
    """
    lower = text.lower()
    for tok in _FORBIDDEN_BARE_TOKENS:
        if tok in lower:
            ok, _diag = _has_mixed_direction_acknowledgement(text, mixed_contract)
            if not ok:
                return True, tok
    return False, ""


def test_v1_source_comment_block_rejects_bare_narrower_framing(
    autotuner_source, autotuner_ast, alignment_contract
):
    """V1 source-comment block must not use a bare ``narrowed`` /
    ``narrower`` framing as its sole directionality label.

    STATUS ON 28e49ae: RED. autotuner.py:189 reads
    'V1 calibration sweep — narrowed VWAP_CROSS_HWM_PCT bounds.' — bare
    'narrowed' with no 'lower'+'upper' pairing or long-form phrase.
    """
    names = alignment_contract["named_constants_contract"]
    v1_min_name = names["v1_min_name"]
    mixed_contract = alignment_contract["mixed_direction_contract"]

    assigns = _module_float_assignments(autotuner_ast)
    assert v1_min_name in assigns
    _val, lineno = assigns[v1_min_name]
    # Same V1-block confinement as the mixed-direction test — the
    # forbidden-bare-narrower probe must NOT be defeated by a downstream
    # 'lower' / 'upper' token bleeding in from an unrelated block.
    window = _adjacent_comment_block(autotuner_source, lineno)
    assert window, (
        f"Precondition: expected an adjacent comment block immediately "
        f"above {v1_min_name} (line {lineno})."
    )

    offending, evidence = _has_bare_narrower_framing(window, mixed_contract)
    assert not offending, (
        f"OPTUNA-9a R1 forbidden-bare-narrower framing (V1 block): the "
        f"adjacent comment block above {v1_min_name} (line {lineno}) "
        f"contains the bare token {evidence!r} WITHOUT the compensating "
        f"mixed-direction acknowledgement (no per-direction phrase like "
        f"'lower expands' / 'narrows the upper', and no asymmetric token "
        f"paired with both 'lower' and 'upper'). Per opt-optuna9a "
        f"BLOCK-1 this misdescribes the relationship. Either pair the "
        f"framing with explicit per-direction language or replace with "
        f"'asymmetric' / 'mixed-direction'. V1 block:\n"
        f"---\n{window}\n---"
    )


def test_calibration_sweep_docstring_rejects_bare_narrower_framing(
    autotuner_module, alignment_contract
):
    """``run_calibration_sweep.__doc__`` must not use a bare ``narrowed`` /
    ``narrower`` framing as its sole directionality label.

    STATUS ON 28e49ae: RED. The docstring Note says 'are narrower than
    the production walk-forward bounds' — bare 'narrower than' with no
    'lower'+'upper' pairing or long-form phrase.
    """
    mixed_contract = alignment_contract["mixed_direction_contract"]
    func = getattr(autotuner_module, "run_calibration_sweep", None)
    assert func is not None and callable(func)
    doc = func.__doc__ or ""

    offending, evidence = _has_bare_narrower_framing(doc, mixed_contract)
    assert not offending, (
        f"OPTUNA-9a R1 forbidden-bare-narrower framing (docstring): "
        f"run_calibration_sweep.__doc__ contains the bare token "
        f"{evidence!r} WITHOUT the compensating mixed-direction "
        f"acknowledgement. Per opt-optuna9a BLOCK-1 this misdescribes "
        f"the relationship — V1.lower (0.3) actually EXPANDS below "
        f"production.lower (0.5). Either pair with per-direction "
        f"language or replace with 'asymmetric' / 'mixed-direction'. "
        f"Current docstring:\n---\n{doc}\n---"
    )


# ---------------------------------------------------------------------------
# Tier 8 — Out-of-production-range operator NOTE (opt-optuna9a R1 NOTE)
#
# The calibration sweep can return a proposed VWAP_CROSS_HWM_PCT in
# [V1.min=0.3, production.min=0.5) — a region the production
# walk-forward search space [0.5, 2.5] cannot reproduce. Calibration is
# read-only / operator-gated (AC-V1.3) so this is not a mechanics bug,
# but it is a non-obvious operator caveat that must be visible: if
# calibration finds the optimum at e.g. 0.35, the operator must
# understand that the production optimizer will never reproduce that
# recommendation in a subsequent walk-forward.
#
# The contract is satisfied by either documentation surface (V1
# source-comment block OR run_calibration_sweep docstring) — the
# implementer chooses where it lands.
# ---------------------------------------------------------------------------


def test_out_of_production_range_caveat_present_in_some_documentation_surface(
    autotuner_module, autotuner_source, autotuner_ast, alignment_contract
):
    """At least one of the two documentation surfaces (V1 source-comment
    block adjacent to ``_SS_VWAP_CROSS_HWM_V1_MIN`` OR
    ``run_calibration_sweep.__doc__``) must explicitly acknowledge that
    calibration proposals in [V1.min, production.min) — i.e. [0.3, 0.5)
    — fall outside the production search space and the production
    optimizer cannot reproduce them.

    STATUS ON 28e49ae: RED. Neither surface mentions this caveat.

    Acceptable tokens: ``informational`` / ``cannot be reproduced`` /
    ``outside the production`` / ``not reachable`` / ``will not
    reproduce`` / ``will not validate`` / ``operator advisory`` etc.
    See `out_of_production_range_note_contract.required_caveat_tokens_any`.
    """
    names = alignment_contract["named_constants_contract"]
    v1_min_name = names["v1_min_name"]
    caveat_tokens = alignment_contract[
        "out_of_production_range_note_contract"
    ]["required_caveat_tokens_any"]

    # Surface 1: V1 constant's OWN adjacent comment block. Confined to
    # the V1 block (not a wide window) so an unrelated downstream
    # comment block cannot accidentally satisfy this caveat by
    # containing a token like 'informational' in a different context.
    assigns = _module_float_assignments(autotuner_ast)
    assert v1_min_name in assigns
    _val, lineno = assigns[v1_min_name]
    v1_window = _adjacent_comment_block(autotuner_source, lineno)

    # Surface 2: run_calibration_sweep docstring.
    func = getattr(autotuner_module, "run_calibration_sweep", None)
    assert func is not None and callable(func)
    doc = func.__doc__ or ""

    combined_lower = (v1_window + "\n" + doc).lower()
    matched = [t for t in caveat_tokens if t.lower() in combined_lower]

    assert matched, (
        f"OPTUNA-9a R1 out-of-production-range operator NOTE: neither "
        f"the V1 source-comment block (~15 lines around {v1_min_name} at "
        f"line {lineno}) nor run_calibration_sweep.__doc__ contains any "
        f"of the required caveat tokens {caveat_tokens}. Per "
        f"opt-optuna9a R1 NOTE: calibration proposals in [0.3, 0.5) "
        f"cannot be reproduced by the production walk-forward "
        f"optimizer; the operator must see this caveat at one of the "
        f"two documentation surfaces.\n"
        f"V1 block window:\n---\n{v1_window}\n---\n"
        f"Docstring:\n---\n{doc}\n---"
    )
