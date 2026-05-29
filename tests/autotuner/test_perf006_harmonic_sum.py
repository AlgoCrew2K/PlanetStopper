"""RED regression tests for PERF-006 (math-reaudit LOW, "non-issue at current scale").

Finding (math-reaudit):
    ``c_n = sum(1/j for j in range(1, n+1))`` is O(N) per call (N=500;
    non-issue at current scale) — at ``autotuner.py:419``.

This is the BHY Yekutieli c(N) coefficient computation inside
``benjamini_hochberg_adjust``. At Planet Stopper's production scale (N_effective
≈ n_trials = 500) the sum runs in tens of microseconds; the reaudit
flagged it as a code-smell rather than a perf bug.

Toxic-Pair scope — pin Path A (cached helper)
============================================

Two non-starter resolutions and the chosen one:

* **Path B (closed-form approximation)** ``c_n ≈ ln(n) + γ + 1/(2n)`` is
  **rejected** because ``test_m1_bhy_haircut_preservation.py::``
  ``test_yekutieli_c_n_is_harmonic_number_not_log_approximation``
  explicitly pins the exact harmonic — a log approximation under-corrects
  by ~4% at small N and would flip a previously-rejected trial.
* **Path C (document + add regression test only, no code change)** is
  acceptable but leaves the per-call sum in place; the audit finding then
  re-surfaces in any future re-audit.
* **Path A (cached harmonic-number helper)** is chosen — extract the c(N)
  computation into a top-level helper decorated with
  ``functools.lru_cache`` (or an equivalent memoization), reuse from
  ``benjamini_hochberg_adjust``. Mathematically byte-identical
  (same summation order, same denominators), performance O(N) on first
  call per N and O(1) thereafter.

What these tests pin
--------------------
1. **Existence + name** — a module-level helper named
   ``_yekutieli_c_n`` (or ``yekutieli_c_n``) lives in ``autotuner.py``
   and accepts a single positional ``n: int``.
2. **Memoization decorator (AST shape)** — the helper is wrapped with
   ``functools.lru_cache`` or ``functools.cache``. AST inspection makes
   this resilient to whitespace and to ``import functools as _ft``
   aliasing.
3. **Cache reuse (cache_info)** — calling the helper twice with the same
   ``n`` produces exactly one miss and one hit; ``cache_info().hits``
   and ``misses`` reflect this. This is the load-bearing perf claim:
   a future PR that strips the decorator fails here.
4. **Byte-identical output vs the inline sum** — for N in {1, 5, 10,
   100, 500, 1000} the helper returns exactly
   ``sum(1.0/j for j in range(1, n+1))`` (tolerance 1e-15; same
   ascending-order summation).
5. **Fixture-pin re-coverage** — pins from
   ``bhy_byte_identical_pin.json`` (c_1, c_5, c_10, c_100, c_500) are
   re-asserted against the cached helper. Catches a future refactor
   that breaks the harmonic identity even while keeping the cache.
6. **BHY end-to-end byte-identity** — the N=10 ``benjamini_hochberg_adjust``
   p_adj vector from the fixture must still match (tolerance 1e-15).
   The cache is an optimization, not a refactor of the algorithm.
7. **No log-approximation regression** — the helper output must differ
   from ``math.log(n) + γ_em`` by more than 1e-3 at small N (re-pinning
   the M1 T2 contract against the new helper surface).
8. **AST: the call site no longer holds an inline harmonic sum** — the
   body of ``benjamini_hochberg_adjust`` must NOT contain the literal
   inline generator ``1.0 / j for j in range(1, n + 1)`` (or
   ``1 / j``). It must instead call the helper. This is the
   regression lock: a future PR that inlines the sum back fails here.

These tests are RED on the fork-point ``468aad9`` (no helper exists yet)
and turn GREEN after impl-perf006 extracts the helper.

Pre-existing pins this file complements (NOT supersedes)
--------------------------------------------------------
* ``test_m1_bhy_haircut_preservation.py::test_*`` — BHY-correctness
  contract (algorithm, c(N) identity, p-value clamp). PERF-006 reuses
  the c_n_pins fixture but adds the *cache* contract those tests do
  not cover.

References
----------
* ``autotuner.py:512-516`` (current inline sum, fork-point ``468aad9``)
* ``autotuner.py:490-527`` (full ``benjamini_hochberg_adjust``)
* ``tests/fixtures/math/bhy_byte_identical_pin.json`` (c_n pin values)
* Harvey & Liu 2015, DOI 10.3905/jpm.2015.42.1.013
"""
from __future__ import annotations

import ast
import inspect
import json
import math
import pathlib
import re

import pytest

import autotuner


_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_BHY_FIXTURE_PATH = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "bhy_byte_identical_pin.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bhy_fixture() -> dict:
    """Load the BHY hand-derived pin (re-used from M1)."""
    with _BHY_FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_helper():
    """Return the cached c(N) helper, or fail with a descriptive message.

    The helper name is impl-perf006's choice but must be one of the two
    conventional spellings. Both private (``_yekutieli_c_n``) and public
    (``yekutieli_c_n``) are accepted; AST tests do not depend on which.
    """
    for name in ("_yekutieli_c_n", "yekutieli_c_n"):
        helper = getattr(autotuner, name, None)
        if helper is not None:
            return name, helper
    pytest.fail(
        "PERF-006: expected a module-level cached helper named "
        "`_yekutieli_c_n` or `yekutieli_c_n` in autotuner.py; neither "
        "found. The helper must encapsulate the Yekutieli c(N) "
        "harmonic-number sum so it can be memoized."
    )


# ---------------------------------------------------------------------------
# T1: Helper exists with the right signature
# ---------------------------------------------------------------------------


def test_yekutieli_helper_exists_and_takes_single_int():
    """PERF-006 Path A requires a module-level helper that accepts
    ``n: int`` positionally.

    Why a separate helper (rather than memoizing the whole
    ``benjamini_hochberg_adjust``): the function takes a ``list[float]``,
    which is unhashable and so cannot be memoized directly. Lifting the
    pure-numeric c(N) into its own helper is the standard refactor.
    """
    name, helper = _resolve_helper()

    sig = inspect.signature(helper.__wrapped__ if hasattr(helper, "__wrapped__") else helper)
    params = list(sig.parameters.values())
    assert len(params) == 1, (
        f"{name} must accept exactly one positional parameter (n: int); "
        f"got {len(params)}: {[p.name for p in params]}"
    )
    # Accept any name for the parameter, but it must be positional-capable.
    p = params[0]
    assert p.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ), f"{name}'s parameter must be positional; got kind={p.kind}"


# ---------------------------------------------------------------------------
# T2: Memoization decorator (AST shape)
# ---------------------------------------------------------------------------


def _autotuner_module_ast() -> ast.Module:
    src_path = pathlib.Path(autotuner.__file__)
    return ast.parse(src_path.read_text(encoding="utf-8"))


def _find_function_def(tree: ast.Module, names: tuple[str, ...]) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            return node
    pytest.fail(
        f"AST: no FunctionDef matching any of {names!r} found in autotuner.py"
    )


def test_yekutieli_helper_has_functools_cache_decorator():
    """The helper must be decorated with ``functools.lru_cache`` or
    ``functools.cache``.

    The decorator IS the perf fix. Stripping it (or replacing with a
    no-op) reverts to O(N)-per-call. We assert this at the AST level
    (not runtime) so the contract is robust against namespace shenanigans
    like ``cache = lambda f: f``.
    """
    tree = _autotuner_module_ast()
    fn = _find_function_def(tree, ("_yekutieli_c_n", "yekutieli_c_n"))

    if not fn.decorator_list:
        pytest.fail(
            "PERF-006: cached helper has no decorators; expected "
            "@functools.lru_cache or @functools.cache (or a maxsize-bearing "
            "lru_cache call)."
        )

    def _decorator_name(dec: ast.AST) -> str:
        # Unwrap Call(deco_expr, args, kwargs) → deco_expr
        if isinstance(dec, ast.Call):
            dec = dec.func
        if isinstance(dec, ast.Attribute):
            # e.g. functools.lru_cache, functools.cache
            return dec.attr
        if isinstance(dec, ast.Name):
            # e.g. lru_cache, cache (from functools import lru_cache)
            return dec.id
        return ""

    decorator_names = {_decorator_name(d) for d in fn.decorator_list}
    assert decorator_names & {"lru_cache", "cache"}, (
        f"PERF-006: cached helper must be decorated with functools.lru_cache "
        f"or functools.cache; found decorators: {decorator_names!r}"
    )


# ---------------------------------------------------------------------------
# T3: Cache reuse — runtime contract via cache_info()
# ---------------------------------------------------------------------------


def test_yekutieli_helper_cache_reuses_across_calls():
    """The cache must actually hit on the second call for the same n.

    AST asserts the decorator exists; this asserts the runtime behavior:
    calling twice with the same argument produces one miss and one hit.
    A future PR that swaps lru_cache for a no-op shim (e.g. for
    pickling) would still pass T2 but fail here.

    We use a fresh ``n`` value (n=37 — not 500, not 100, not in the
    fixture) so prior test runs in the same pytest process can't have
    populated the cache with a hit.
    """
    name, helper = _resolve_helper()

    if not hasattr(helper, "cache_info"):
        pytest.fail(
            f"PERF-006: {name} has no cache_info() — the decorator is not "
            f"functools.lru_cache. Path A explicitly chose lru_cache for "
            f"its observable cache contract."
        )

    # n=37 — unlikely to be pre-populated by any other test or import.
    n = 37
    before = helper.cache_info()
    v1 = helper(n)
    mid = helper.cache_info()
    v2 = helper(n)
    after = helper.cache_info()

    assert v1 == v2, (
        f"Cached helper returned different values for the same n={n}: "
        f"v1={v1!r}, v2={v2!r}"
    )
    assert mid.misses - before.misses == 1, (
        f"First call must be a miss; got misses delta "
        f"{mid.misses - before.misses}"
    )
    assert after.hits - mid.hits == 1, (
        f"Second call with the same n must be a hit; got hits delta "
        f"{after.hits - mid.hits}. The lru_cache decorator is not "
        f"actually caching."
    )


# ---------------------------------------------------------------------------
# T4: Byte-identical output vs the inline harmonic sum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 5, 10, 100, 500, 1000])
def test_yekutieli_helper_byte_identical_to_inline_sum(n):
    """For every N in {1, 5, 10, 100, 500, 1000} the cached helper must
    equal the inline sum to within 1e-15.

    Tolerance 1e-15: IEEE-754 double precision; the cached helper MUST
    perform the same ascending-order summation
    ``sum(1.0 / j for j in range(1, n + 1))``. A different summation
    order (e.g. descending j for better numerical stability) would
    legitimately produce a different last bit and fail this pin —
    intentional, because byte-identity is the BHY-preservation
    contract (M1 plan §"BHY slice — zero-change preservation").
    """
    _, helper = _resolve_helper()
    inline = sum(1.0 / j for j in range(1, n + 1))
    cached = helper(n)
    assert cached == pytest.approx(inline, abs=1e-15), (
        f"Cached c({n}) = {cached!r} but inline sum = {inline!r}. "
        f"Difference {cached - inline!r} exceeds the 1e-15 byte-identity "
        f"tolerance. BHY preservation requires the cached helper to use "
        f"the SAME ascending-j summation order as the original inline sum."
    )


# ---------------------------------------------------------------------------
# T5: Fixture-pin re-coverage
# ---------------------------------------------------------------------------


def test_yekutieli_helper_matches_fixture_pins(bhy_fixture):
    """Re-pin the c(N) values from ``bhy_byte_identical_pin.json``
    against the cached helper.

    The M1 T2 test asserts these values against the inline sum; this
    asserts them against the helper. Both contracts must hold after
    PERF-006 ships.
    """
    _, helper = _resolve_helper()
    pins = bhy_fixture["yekutieli_c_n_pins"]["values"]
    for label, c_expected in pins.items():
        # "c_500" → 500
        n = int(label.split("_", 1)[1])
        c_actual = helper(n)
        assert c_actual == pytest.approx(c_expected, abs=1e-15), (
            f"Fixture pin c({n}) = {c_expected!r}; cached helper "
            f"returned {c_actual!r}. The cached helper must reproduce "
            f"the hand-derived harmonic-number pins."
        )


# ---------------------------------------------------------------------------
# T6: BHY end-to-end byte-identity preserved through the cache
# ---------------------------------------------------------------------------


def test_benjamini_hochberg_adjust_byte_identical_after_cache(bhy_fixture):
    """The N=10 expected p_adj vector from the fixture must still match
    after PERF-006.

    This is the load-bearing guarantee: the cache is an optimization,
    NOT a refactor of the BHY step-up. Tolerance 1e-15 because the
    algorithm is deterministic double-precision arithmetic; any change
    to summation order, running-min direction, or candidate formula
    fails this test.
    """
    p_values = list(bhy_fixture["p_values"])
    expected = list(bhy_fixture["expected"]["p_adj"])

    actual = autotuner.benjamini_hochberg_adjust(p_values)

    assert len(actual) == len(expected), (
        f"BHY output length mismatch: got {len(actual)} adjusted values, "
        f"expected {len(expected)}"
    )
    for i, (a, e) in enumerate(zip(actual, expected)):
        assert a == pytest.approx(e, abs=1e-15), (
            f"BHY p_adj[{i}] = {a!r}, expected {e!r}; difference "
            f"{a - e!r} exceeds 1e-15 byte-identity tolerance. The cache "
            f"must not change the algorithm's output."
        )


# ---------------------------------------------------------------------------
# T7: No log-approximation regression on the helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 5, 10])
def test_yekutieli_helper_is_exact_not_log_approximation(n):
    """The cached helper must NOT collapse to the ``ln(n) + γ`` log
    approximation.

    At small N this approximation under-corrects c(N) by several percent
    (at N=5: harmonic=2.283, log_approx=2.187, ~4.2% gap), which would
    flip a previously-rejected trial through the FDR gate. M1 T2 pins
    this against the inline sum; this re-pins it against the helper.
    """
    _, helper = _resolve_helper()
    harmonic = helper(n)
    log_approx = math.log(n) + 0.5772156649015328  # Euler-Mascheroni γ
    delta = abs(harmonic - log_approx)
    assert delta > 1e-3, (
        f"Helper c({n}) = {harmonic!r} is suspiciously close to "
        f"ln({n}) + γ = {log_approx!r} (delta {delta!r}). The helper "
        f"must compute the exact harmonic number, not the log "
        f"approximation."
    )


# ---------------------------------------------------------------------------
# T8: Call-site no longer contains the inline harmonic sum
# ---------------------------------------------------------------------------


def test_benjamini_hochberg_adjust_does_not_inline_harmonic_sum():
    """``benjamini_hochberg_adjust`` must delegate c(N) to the helper —
    no inline ``sum(1.0/j for j in range(1, n + 1))`` survives.

    This is the structural regression lock: a future PR that inlines
    the sum back into the function body (defeating the cache) fails
    here even if the cached helper still exists elsewhere in the module.
    """
    tree = _autotuner_module_ast()
    fn = _find_function_def(tree, ("benjamini_hochberg_adjust",))

    # Walk the function body for a generator expression of the form
    #   <numeric> / <var> for <var> in range(...)
    # We accept any literal numerator (1, 1.0) and any variable name.
    found_inline_sum = False
    for node in ast.walk(fn):
        if isinstance(node, (ast.GeneratorExp, ast.ListComp)):
            elt = node.elt
            # elt should look like BinOp(left=<num>, op=Div(), right=Name(...))
            if (
                isinstance(elt, ast.BinOp)
                and isinstance(elt.op, ast.Div)
                and isinstance(elt.left, ast.Constant)
                and isinstance(elt.left.value, (int, float))
                and elt.left.value == 1
                or (
                    isinstance(elt, ast.BinOp)
                    and isinstance(elt.op, ast.Div)
                    and isinstance(elt.left, ast.Constant)
                    and elt.left.value in (1, 1.0)
                )
            ):
                # Check the generator iterates over range(1, ..., n+1)
                for gen in node.generators:
                    if isinstance(gen.iter, ast.Call) and isinstance(
                        gen.iter.func, ast.Name
                    ) and gen.iter.func.id == "range":
                        found_inline_sum = True
                        break
        if found_inline_sum:
            break

    assert not found_inline_sum, (
        "PERF-006: benjamini_hochberg_adjust still contains an inline "
        "`sum(1.0 / j for j in range(1, n + 1))` (or equivalent) generator. "
        "The Path-A fix requires delegating to the cached helper "
        "_yekutieli_c_n / yekutieli_c_n; the inline sum defeats the cache."
    )


# ---------------------------------------------------------------------------
# T9: Source-text negative — no math.log used as a c(N) substitute
# ---------------------------------------------------------------------------


def test_benjamini_hochberg_module_does_not_use_log_for_c_n():
    """Belt-and-braces: scan the source text of
    ``benjamini_hochberg_adjust`` AND the cached helper for any
    ``math.log`` call.

    A log-based c(N) would be the easiest unintended Path-B regression.
    Neither function should ever call ``math.log`` — c(N) is exact.
    """
    src = inspect.getsource(autotuner.benjamini_hochberg_adjust)
    assert "math.log" not in src and "log(" not in src, (
        "benjamini_hochberg_adjust source contains math.log / log( — "
        "PERF-006 Path A is the cached helper, NOT the log "
        "approximation. The M1 T2 contract forbids the latter."
    )

    _, helper = _resolve_helper()
    # Unwrap lru_cache to get the underlying function source.
    underlying = getattr(helper, "__wrapped__", helper)
    helper_src = inspect.getsource(underlying)
    assert "math.log" not in helper_src and not re.search(
        r"(?<!math\.)\blog\s*\(", helper_src
    ), (
        "Cached helper source contains math.log / log( — c(N) must be "
        "computed as an exact harmonic sum, never as a log "
        "approximation."
    )
