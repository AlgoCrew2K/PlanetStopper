"""
RED tests — PERF-002 (math-reaudit MEDIUM): hoist sorted(historical_data.keys()).

Finding:
  math_engine.py re-sorts the same `historical_data` dict at five call sites
  (currently lines 936, 1045, 1080, 1358, 1576 — was lines 797/912/947/1225 at
  the time of audit-2; line numbers drift but the pattern is identical):

      valid_dates = sorted(list(historical_data.keys()))

  During one symphony cycle, the SAME `historical_data` dict is threaded
  through up to five math_engine functions (run_monte_carlo,
  compute_portfolio_cvar [potentially twice for short+long],
  compute_regime_match_quality, calculate_20d_vol, calculate_14d_atr_pct).
  Each function independently re-sorts. That is O(N log N) redundant Python
  work per symphony per cycle (and per autotuner trial day) — pure overhead.

  The fix: compute the sorted date list ONCE per `historical_data` object
  and reuse. Per the audit instruction:

      "either a top-of-function local _sorted_dates = sorted(historical_data.keys())
       reused for subsequent operations, OR a small helper _sorted_dates(historical_data)
       cached per-call."

  Since each function already sorts at most once internally, the only
  meaningful hoist crosses function boundaries — i.e. a module-level helper
  `_sorted_dates(historical_data)` that memoises by dict identity. These
  tests demand that helper and its use at all five call sites.

  NO MATH CHANGE — bit-for-bit identical output expected across the curated
  input set. Sort output is deterministic on a fixed dict, so hoisting is a
  pure refactor.

Acceptance:
  AC-PERF002.1  A helper `_sorted_dates(historical_data)` exists on
                math_engine and is callable. The helper takes the historical
                data dict and returns a list of date keys in sorted order.
  AC-PERF002.2  The helper returns values that equal
                `sorted(historical_data.keys())` for the same input —
                element-wise equality across the curated scenarios.
  AC-PERF002.3  When called twice with the SAME dict object, the helper
                does not re-sort (call-count assertion via a sorted spy).
                The cache is per-call-chain and keyed by object identity
                (not by value) so a mutated dict is never reused stale.
  AC-PERF002.4  All five production consumers route through the helper
                (or share a single hoisted local within their caller):
                run_monte_carlo, compute_portfolio_cvar,
                compute_regime_match_quality, calculate_20d_vol,
                calculate_14d_atr_pct. AST/source check.
  AC-PERF002.5  No production call site contains the raw
                `sorted(list(historical_data.keys()))` literal expression
                any more — every occurrence must route through the helper
                (string + AST check on math_engine.py).
  AC-PERF002.6  Sequential invocation of run_monte_carlo +
                compute_portfolio_cvar + compute_regime_match_quality with
                the SAME historical_data object triggers AT MOST ONE call
                to the built-in `sorted` on that dict's keys. Pre-hoist
                this was 3+; the hoist drops it to 1 (or 0 if the helper
                pre-computed once on an earlier call in this chain).
  AC-PERF002.7  End-to-end output of each of the five consumers is
                bit-identical before/after the hoist, under the curated
                fixtures. Verified by monkeypatching the helper to a
                stateless reference and comparing.

Invariants preserved (load-bearing):
  - [[project_mc_sentinel_consumer_blast_radius]]: run_monte_carlo's return
    type/domain unchanged (still float in [0, 100] or None sentinel).
  - [[project_mc_eligible_pool_vs_raw_day_boundary]]: min raw history =
    MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1); guard fires below that.
  - [[project_cvar_divergence_validation_wall]]: CVaR-divergence REJECT
    wall preserved — sufficiency/insufficiency boundary identical before
    and after.

PA-18 compliance:
  No mc_prob literals. No cvar literals. No vol/ATR literals. Assertions
  compare the post-hoist production callable against an in-test reference
  (the pre-hoist behaviour invoked through monkeypatching the helper to a
  stateless reimplementation). The reference IS the spec.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import pathlib
from typing import Any
from unittest.mock import patch

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "perf002_hoist_sorted_keys"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(filename: str) -> dict[str, Any]:
    with (FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
    """Synthetic lexicographically-sortable date string."""
    month = 1 + i // 28
    day = 1 + i % 28
    year = 2024 + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _expand_scenario(scenario: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """Build a historical_data dict from a curated scenario spec. Mirrors
    the helper used by the PERF-001 test file (intentional duplication so the
    two test files are independently runnable)."""
    minimum_raw_days = (
        math_engine.MC_MIN_HISTORY_DAYS
        + (math_engine.MC_VOL_WINDOW_DAYS - 1)
    )
    num_days = minimum_raw_days + scenario["raw_day_offset_from_minimum"]
    kind = scenario["kind"]
    history: dict = {}

    if kind == "alternating":
        spy_amp = scenario["spy_amplitude"]
        aaa_amp = scenario["aaa_amplitude"]
        bbb_amp = scenario.get("bbb_amplitude", 0.0)
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {
                    "daily_ret": sign * spy_amp,
                    "high": 100.0 + sign * 1.0,
                    "low": 100.0 - sign * 1.0,
                    "close": 100.0 + sign * 0.5,
                },
                "AAA": {
                    "daily_ret": sign * aaa_amp,
                    "high": 50.0 + sign * 1.0,
                    "low": 50.0 - sign * 1.0,
                    "close": 50.0 + sign * 0.3,
                },
                "BBB": {
                    "daily_ret": sign * bbb_amp,
                    "high": 25.0 + sign * 0.5,
                    "low": 25.0 - sign * 0.5,
                    "close": 25.0 + sign * 0.2,
                },
            }
    elif kind == "monotone":
        for i in range(num_days):
            history[_date_key(i)] = {
                "SPY": {
                    "daily_ret": scenario["spy_start"] + i * scenario["spy_step"],
                    "high": 100.0 + i * 0.01,
                    "low": 99.0 + i * 0.01,
                    "close": 99.5 + i * 0.01,
                },
                "AAA": {
                    "daily_ret": scenario["aaa_start"] + i * scenario["aaa_step"],
                    "high": 50.0 + i * 0.01,
                    "low": 49.0 + i * 0.01,
                    "close": 49.5 + i * 0.01,
                },
                "BBB": {
                    "daily_ret": 0.0,
                    "high": 25.0,
                    "low": 24.5,
                    "close": 24.75,
                },
            }
    elif kind == "constant_return":
        for i in range(num_days):
            history[_date_key(i)] = {
                "SPY": {
                    "daily_ret": scenario["spy_daily_ret"],
                    "high": 100.5,
                    "low": 99.5,
                    "close": 100.0,
                },
                "AAA": {
                    "daily_ret": scenario["holding_daily_ret"],
                    "high": 50.5,
                    "low": 49.5,
                    "close": 50.0,
                },
                "BBB": {
                    "daily_ret": scenario["holding_daily_ret"],
                    "high": 25.5,
                    "low": 24.5,
                    "close": 25.0,
                },
            }
    else:
        raise ValueError(f"Unknown scenario kind: {kind!r}")

    return history


SCENARIO_IDS = [
    "case_01_long_smooth_history",
    "case_02_at_eligible_boundary",
    "case_03_one_above_boundary",
    "case_04_monotone_returns",
    "case_05_constant_returns",
]


def _get_scenario(scenario_id: str) -> dict[str, Any]:
    fixture = _load_fixture("curated_input_scenarios.json")
    return next(s for s in fixture["scenarios"] if s["id"] == scenario_id)


# ---------------------------------------------------------------------------
# AC-PERF002.1 — Helper exists, callable
# ---------------------------------------------------------------------------


def test_sorted_dates_helper_exists_and_is_callable() -> None:
    """The hoisted-sort helper must be a public-but-underscored symbol on
    math_engine so it can be tested in isolation. A pure refactor that
    inlines a sorted-cache lambda inside each function bypasses this — and
    we want a single helper so all five consumers share the same cache and
    contract."""
    assert hasattr(math_engine, "_sorted_dates"), (
        "math_engine is missing _sorted_dates. Implement: "
        "def _sorted_dates(historical_data: dict) -> list[str] -- a helper "
        "that returns sorted(historical_data.keys()) with per-call-chain "
        "memoisation keyed by id(historical_data)."
    )
    assert callable(math_engine._sorted_dates), (
        "math_engine._sorted_dates is not callable."
    )


# ---------------------------------------------------------------------------
# AC-PERF002.2 — Helper value equals sorted(historical_data.keys())
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_sorted_dates_returns_sorted_keys(scenario_id: str) -> None:
    """For every curated scenario, _sorted_dates(historical_data) must equal
    sorted(historical_data.keys()) element-wise. This pins the value
    contract — a cache implementation that returns a stale list, a tuple, a
    reversed list, or anything other than the canonical sort would fail
    here. The reference (sorted(historical_data.keys())) is computed in-test
    from the SAME input."""
    history = _expand_scenario(_get_scenario(scenario_id))
    expected = sorted(history.keys())
    actual = math_engine._sorted_dates(history)
    # list, not tuple — consumers index/slice it. A tuple would still pass
    # the equality check but break list-only operations downstream.
    assert isinstance(actual, list), (
        f"[{scenario_id}] _sorted_dates returned {type(actual).__name__}; "
        f"must return list (callers do `valid_dates[-LOOKBACK_DAYS:]` etc)."
    )
    assert actual == expected, (
        f"[{scenario_id}] _sorted_dates returned {actual!r}; expected "
        f"{expected!r} (canonical sorted-keys output)."
    )


# ---------------------------------------------------------------------------
# AC-PERF002.3 — Per-call-chain memoisation by object identity
# ---------------------------------------------------------------------------


def test_sorted_dates_caches_by_identity_on_repeated_calls() -> None:
    """When the SAME dict object is passed twice, the helper must not
    re-sort. We assert this by spying on `sorted` for the duration of the
    second call and asserting it is called zero times with this dict's
    keys.

    Implementation note for the implementer: the cache must be keyed by
    id(historical_data) (not by value — dicts are unhashable, and value-
    keyed memoisation would be O(N) per lookup, defeating the optimisation).
    A WeakValueDictionary keyed by id is fine; a small module-level
    {id: (weakref, sorted_list)} mapping is fine; functools.lru_cache on a
    frozen-keys variant is fine.

    Tolerance rationale: strict — 0 calls on the second invocation.
    Sorting is deterministic on a fixed dict; if the second call invokes
    `sorted` even once, the cache failed.
    """
    history = _expand_scenario(_get_scenario("case_01_long_smooth_history"))

    # First call — primes the cache.
    first = math_engine._sorted_dates(history)
    assert first == sorted(history.keys())  # sanity gate

    # Second call with the SAME object. Spy on the `sorted` builtin in the
    # math_engine module namespace. A correct cache returns without invoking
    # sorted on the same dict.
    call_count = {"n": 0}
    real_sorted = sorted

    def _spy_sorted(*args, **kwargs):
        call_count["n"] += 1
        return real_sorted(*args, **kwargs)

    with patch.object(math_engine, "sorted", _spy_sorted, create=True):
        second = math_engine._sorted_dates(history)

    assert second == first, (
        "Cached return value diverges from primed value — cache is corrupt."
    )
    assert call_count["n"] == 0, (
        f"_sorted_dates invoked the `sorted` builtin {call_count['n']} time(s) "
        f"on a repeat call with the same dict object. The cache must be "
        f"keyed by id(historical_data) so a repeat call is O(1). If the "
        f"intent is to cache only across a single math_engine call chain, "
        f"this primed-then-repeat pattern still must reuse the cache "
        f"because the dict object is unchanged."
    )


def test_sorted_dates_does_not_share_cache_across_distinct_dicts() -> None:
    """A new dict object with the same keys must NOT hit the cache from a
    prior dict — the cache is by identity, not by value. This pins the
    contract that the implementer cannot use a hash-of-keys cache key (which
    would silently return stale results if a key is added/removed in a
    new dict that happens to share a hash)."""
    history_a = _expand_scenario(_get_scenario("case_01_long_smooth_history"))
    history_b = dict(history_a)  # shallow copy — distinct object, same keys
    assert history_a is not history_b
    assert sorted(history_a.keys()) == sorted(history_b.keys())

    # Prime with A.
    math_engine._sorted_dates(history_a)

    # Now call B. Spy on sorted. A value-keyed cache would hit (incorrect
    # contract for our identity-keyed helper); an identity-keyed cache
    # must invoke sorted on B because id(history_b) != id(history_a).
    call_count = {"n": 0}
    real_sorted = sorted

    def _spy_sorted(*args, **kwargs):
        call_count["n"] += 1
        return real_sorted(*args, **kwargs)

    with patch.object(math_engine, "sorted", _spy_sorted, create=True):
        out_b = math_engine._sorted_dates(history_b)

    assert out_b == sorted(history_b.keys())
    assert call_count["n"] >= 1, (
        "_sorted_dates appears to share its cache across distinct dict "
        "objects. The cache MUST be keyed by id(historical_data) so a new "
        "dict (potentially mutated, potentially different) is always "
        "recomputed. A value-keyed cache risks stale results."
    )


# ---------------------------------------------------------------------------
# AC-PERF002.4 — Production consumers route through the helper
# ---------------------------------------------------------------------------


CONSUMER_FUNCTIONS = [
    "run_monte_carlo",
    "compute_portfolio_cvar",
    "compute_regime_match_quality",
    "calculate_20d_vol",
    "calculate_14d_atr_pct",
]


@pytest.mark.parametrize("fn_name", CONSUMER_FUNCTIONS)
def test_consumer_routes_through_sorted_dates_helper(fn_name: str) -> None:
    """Each of the five consumers must reference `_sorted_dates` in its
    source — i.e. it must invoke the helper rather than calling
    `sorted(list(historical_data.keys()))` directly. AST/source check.

    Why source-level and not behavioural: a consumer that inlines a private
    cache satisfies the perf goal but defeats the shared-cache benefit
    across consumers. The audit instruction is explicit about a small
    helper; this test pins that.
    """
    fn = getattr(math_engine, fn_name)
    src = inspect.getsource(fn)
    assert "_sorted_dates" in src, (
        f"math_engine.{fn_name} does not reference _sorted_dates. After "
        f"PERF-002, every consumer that previously had "
        f"`valid_dates = sorted(list(historical_data.keys()))` must route "
        f"through math_engine._sorted_dates(historical_data) so the cache "
        f"is shared across functions within one cycle."
    )


# ---------------------------------------------------------------------------
# AC-PERF002.5 — No raw `sorted(list(historical_data.keys()))` literals remain
# ---------------------------------------------------------------------------


def test_math_engine_has_no_remaining_raw_sorted_historical_keys_literals() -> None:
    """Source-level sweep: after PERF-002, no production call site may
    contain the literal `sorted(list(historical_data.keys()))` expression
    (or the `sorted(historical_data.keys())` variant). The only place that
    pattern is allowed is the body of `_sorted_dates` itself — every other
    occurrence is a missed hoist.

    Why text + AST: a raw text grep is necessary because the test must
    catch the literal even inside docstrings (which the AST drops), but
    the assertion logic uses ast to find call expressions outside the
    helper body — a docstring-only mention does not count as a call.
    """
    src_path = pathlib.Path(math_engine.__file__)
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Build {name -> (start_line, end_line)} for every top-level FunctionDef.
    fn_ranges: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            fn_ranges[node.name] = (node.lineno, node.end_lineno or node.lineno)

    # Walk every Call node. Look for sorted(list(historical_data.keys()))
    # or sorted(historical_data.keys()).
    offending: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "sorted"):
            continue
        # Strip an optional list(...) wrapper around the first arg.
        if len(node.args) != 1:
            continue
        arg = node.args[0]
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == "list"
            and arg.args
        ):
            arg = arg.args[0]
        # Now arg should be `historical_data.keys()` to match the pattern.
        if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)):
            continue
        if arg.func.attr != "keys":
            continue
        if not (isinstance(arg.func.value, ast.Name) and arg.func.value.id == "historical_data"):
            continue

        # Skip the helper body itself — it is allowed to call sorted.
        line = node.lineno
        in_helper = False
        if "_sorted_dates" in fn_ranges:
            lo, hi = fn_ranges["_sorted_dates"]
            if lo <= line <= hi:
                in_helper = True
        if not in_helper:
            offending.append((line, ast.unparse(node)))

    assert not offending, (
        "math_engine.py still contains raw `sorted(...historical_data.keys())` "
        "calls outside the _sorted_dates helper. Every consumer must route "
        "through the helper. Offending sites:\n"
        + "\n".join(f"  line {ln}: {expr}" for ln, expr in offending)
    )


# ---------------------------------------------------------------------------
# AC-PERF002.6 — Sequential consumers share the cache
# ---------------------------------------------------------------------------


def test_sequential_consumers_share_sorted_cache() -> None:
    """The whole point of PERF-002: across the call chain of one symphony
    cycle, the SAME historical_data dict flows through up to 5 consumers
    (run_monte_carlo, compute_portfolio_cvar [short + long],
    compute_regime_match_quality, calculate_20d_vol, calculate_14d_atr_pct).
    After PERF-002, the dict's keys must be sorted AT MOST ONCE across
    that chain — not 4-5 times.

    Test mechanism: count invocations of the `sorted` builtin (in
    math_engine's namespace) for keys-of-this-dict, across sequential
    consumer calls. Pre-hoist this would be 3 (we call 3 consumers below);
    post-hoist it must be 1.

    Why we call 3 not all 5: calculate_20d_vol / calculate_14d_atr_pct are
    legitimate users of the same pattern but their internal flow differs
    enough that adding them would obscure the cache-sharing assertion.
    Three is sufficient to prove the cache crosses function boundaries;
    AC-PERF002.4 separately pins helper use in all 5.

    Tolerance rationale: hard ceiling of 1. The helper may be called many
    times (once per consumer); but the inner `sorted(...)` call must run
    AT MOST ONCE on this dict's keys across the chain.
    """
    history = _expand_scenario(_get_scenario("case_01_long_smooth_history"))
    holdings = _load_fixture("curated_input_scenarios.json")["holdings"]
    spy_today_return = 0.0

    # Track sorted-on-this-dict's-keys calls. A loose check (sorted called
    # anywhere) is too permissive: math_engine internals legitimately sort
    # other sequences (e.g. sim results). We pin specifically to "sorted
    # called with an iterable that equals this dict's keys".
    expected_keys = list(history.keys())
    expected_keys_sorted = sorted(expected_keys)
    sort_calls_for_this_dict = {"n": 0}
    real_sorted = sorted

    def _spy_sorted(iterable, *args, **kwargs):
        # Materialise once so we can both inspect and consume safely.
        materialised = list(iterable)
        result = real_sorted(materialised, *args, **kwargs)
        if result == expected_keys_sorted and set(materialised) == set(expected_keys):
            sort_calls_for_this_dict["n"] += 1
        return result

    with patch.object(math_engine, "sorted", _spy_sorted, create=True):
        _ = math_engine.run_monte_carlo(
            holdings,
            history,
            spy_today_return,
            simulation_paths=2000,
            neighbor_k=10,
            seed=12345,
        )
        _ = math_engine.compute_portfolio_cvar(
            cycle_id="perf002-test",
            holdings=holdings,
            historical_data=history,
            spy_today_return=spy_today_return,
            simulation_paths=2000,
            neighbor_k=10,
            mode=None,
        )
        _ = math_engine.compute_regime_match_quality(
            historical_data=history,
            spy_today_return=spy_today_return,
        )

    assert sort_calls_for_this_dict["n"] <= 1, (
        f"After PERF-002, sequential calls to run_monte_carlo + "
        f"compute_portfolio_cvar + compute_regime_match_quality with the "
        f"SAME historical_data dict invoked `sorted` on that dict's keys "
        f"{sort_calls_for_this_dict['n']} times. Hard ceiling is 1. The "
        f"hoist requires that _sorted_dates caches the sort across "
        f"consumer boundaries within a single call chain."
    )


# ---------------------------------------------------------------------------
# AC-PERF002.7 — End-to-end output identity across the curated set
# ---------------------------------------------------------------------------


def _make_uncached_reference():
    """A stateless reference implementation of `_sorted_dates`. Used to
    monkeypatch the helper and verify that consumer outputs are
    bit-identical whether the helper caches or recomputes. This is the
    pre-hoist behaviour, frozen in the test file."""

    def _uncached(historical_data):
        return sorted(list(historical_data.keys()))

    return _uncached


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_run_monte_carlo_output_identical_with_and_without_cache(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_monte_carlo's output under a fixed seed must be bit-identical
    whether _sorted_dates caches (production) or recomputes (reference).
    Sort output is deterministic on a fixed dict, so any divergence
    indicates a real bug in the hoist.

    Tolerance rationale: strict equality. The two paths see the same
    valid_dates list (the cached version is the same list object the
    uncached call would produce), so all downstream arithmetic must
    match bit-for-bit.
    """
    history = _expand_scenario(_get_scenario(scenario_id))
    fixture = _load_fixture("curated_input_scenarios.json")
    holdings = fixture["holdings"]
    spy_today_return = fixture["spy_today_return"]

    # Skip the under-boundary scenario for the sentinel-return path: the
    # comparison still works (both return MC_INSUFFICIENT_HISTORY_SENTINEL)
    # so we DO run it through the test below.

    # Run 1: production helper (cached).
    out_production = math_engine.run_monte_carlo(
        holdings, history, spy_today_return,
        simulation_paths=fixture["simulation_paths"],
        neighbor_k=fixture["neighbor_k"],
        seed=fixture["seed"],
    )

    # Run 2: stateless reference. Must call with a FRESH dict so the cache
    # cannot poison the comparison (id-keyed caches stay scoped to the
    # original dict object).
    history_fresh = _expand_scenario(_get_scenario(scenario_id))
    monkeypatch.setattr(
        math_engine, "_sorted_dates", _make_uncached_reference()
    )
    out_reference = math_engine.run_monte_carlo(
        holdings, history_fresh, spy_today_return,
        simulation_paths=fixture["simulation_paths"],
        neighbor_k=fixture["neighbor_k"],
        seed=fixture["seed"],
    )

    # Both return either the sentinel (None) or a finite float.
    if out_production is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL:
        assert out_reference is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
            f"[{scenario_id}] production returned the insufficient-history "
            f"sentinel but reference returned {out_reference!r}. Sentinel "
            f"boundary diverges between cached and uncached _sorted_dates."
        )
    else:
        assert out_reference is not math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL
        assert math.isfinite(out_production)
        assert math.isfinite(out_reference)
        assert out_production == out_reference, (
            f"[{scenario_id}] run_monte_carlo diverges between cached "
            f"({out_production!r}) and uncached ({out_reference!r}) "
            f"_sorted_dates under the same seed. The hoist is a pure "
            f"refactor — any divergence is a bug."
        )


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_compute_portfolio_cvar_output_identical_with_and_without_cache(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compute_portfolio_cvar's CVaRAssessment must be bit-identical (in
    every numeric field that is not None) whether _sorted_dates caches or
    recomputes. Preserves the CVaR REJECT-wall invariant
    ([[project_cvar_divergence_validation_wall]]).
    """
    history = _expand_scenario(_get_scenario(scenario_id))
    fixture = _load_fixture("curated_input_scenarios.json")
    holdings = fixture["holdings"]
    spy_today_return = fixture["spy_today_return"]

    out_production = math_engine.compute_portfolio_cvar(
        cycle_id="perf002-test",
        holdings=holdings,
        historical_data=history,
        spy_today_return=spy_today_return,
        simulation_paths=fixture["simulation_paths"],
        neighbor_k=fixture["neighbor_k"],
        mode=None,
    )

    history_fresh = _expand_scenario(_get_scenario(scenario_id))
    monkeypatch.setattr(
        math_engine, "_sorted_dates", _make_uncached_reference()
    )
    out_reference = math_engine.compute_portfolio_cvar(
        cycle_id="perf002-test",
        holdings=holdings,
        historical_data=history_fresh,
        spy_today_return=spy_today_return,
        simulation_paths=fixture["simulation_paths"],
        neighbor_k=fixture["neighbor_k"],
        mode=None,
    )

    # Field-by-field comparison; None sentinels must align exactly.
    assert (out_production.cvar_pct is None) == (out_reference.cvar_pct is None), (
        f"[{scenario_id}] cvar_pct None-ness diverges: production "
        f"{out_production.cvar_pct!r} vs reference {out_reference.cvar_pct!r}."
    )
    if out_production.cvar_pct is not None:
        assert out_production.cvar_pct == out_reference.cvar_pct, (
            f"[{scenario_id}] cvar_pct diverges: {out_production.cvar_pct!r} "
            f"vs {out_reference.cvar_pct!r}."
        )
    assert out_production.breach == out_reference.breach
    assert out_production.tail_obs_count == out_reference.tail_obs_count


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_compute_regime_match_quality_output_identical_with_and_without_cache(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compute_regime_match_quality's RegimeMatchAssessment must be
    bit-identical (eligible/insufficient boundary and mean_sq_mahalanobis)
    whether _sorted_dates caches or recomputes."""
    history = _expand_scenario(_get_scenario(scenario_id))
    fixture = _load_fixture("curated_input_scenarios.json")
    spy_today_return = fixture["spy_today_return"]

    out_production = math_engine.compute_regime_match_quality(
        historical_data=history, spy_today_return=spy_today_return,
    )

    history_fresh = _expand_scenario(_get_scenario(scenario_id))
    monkeypatch.setattr(
        math_engine, "_sorted_dates", _make_uncached_reference()
    )
    out_reference = math_engine.compute_regime_match_quality(
        historical_data=history_fresh, spy_today_return=spy_today_return,
    )

    assert (
        (out_production.mean_sq_mahalanobis is None)
        == (out_reference.mean_sq_mahalanobis is None)
    ), (
        f"[{scenario_id}] mean_sq_mahalanobis None-ness diverges."
    )
    if out_production.mean_sq_mahalanobis is not None:
        assert (
            out_production.mean_sq_mahalanobis
            == out_reference.mean_sq_mahalanobis
        )
    assert out_production.is_unprecedented == out_reference.is_unprecedented


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_calculate_20d_vol_output_identical_with_and_without_cache(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """calculate_20d_vol — pure scalar return; trivial equality check."""
    history = _expand_scenario(_get_scenario(scenario_id))
    fixture = _load_fixture("curated_input_scenarios.json")
    holdings = fixture["holdings"]

    out_production = math_engine.calculate_20d_vol(holdings, history)

    history_fresh = _expand_scenario(_get_scenario(scenario_id))
    monkeypatch.setattr(
        math_engine, "_sorted_dates", _make_uncached_reference()
    )
    out_reference = math_engine.calculate_20d_vol(holdings, history_fresh)

    assert out_production == out_reference, (
        f"[{scenario_id}] calculate_20d_vol diverges: "
        f"{out_production!r} vs {out_reference!r}."
    )


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_calculate_14d_atr_pct_output_identical_with_and_without_cache(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """calculate_14d_atr_pct — pure scalar return; trivial equality check."""
    history = _expand_scenario(_get_scenario(scenario_id))
    fixture = _load_fixture("curated_input_scenarios.json")
    holdings = fixture["holdings"]

    out_production = math_engine.calculate_14d_atr_pct(holdings, history)

    history_fresh = _expand_scenario(_get_scenario(scenario_id))
    monkeypatch.setattr(
        math_engine, "_sorted_dates", _make_uncached_reference()
    )
    out_reference = math_engine.calculate_14d_atr_pct(holdings, history_fresh)

    assert out_production == out_reference, (
        f"[{scenario_id}] calculate_14d_atr_pct diverges: "
        f"{out_production!r} vs {out_reference!r}."
    )


# ---------------------------------------------------------------------------
# Invariant guards: MC sentinel + eligible-pool boundary preserved
# ---------------------------------------------------------------------------


def test_run_monte_carlo_returns_sentinel_one_below_eligible_boundary_after_hoist() -> None:
    """Per [[project_mc_eligible_pool_vs_raw_day_boundary]]: at
    minimum_raw_days - 1, run_monte_carlo must return
    MC_INSUFFICIENT_HISTORY_SENTINEL. The hoist must not shift this
    boundary by one (e.g. by an off-by-one in a cached len(valid_dates))."""
    minimum_raw_days = (
        math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
    )
    history = {}
    for i in range(minimum_raw_days - 1):
        history[_date_key(i)] = {
            "SPY": {"daily_ret": 0.001},
            "AAA": {"daily_ret": 0.001},
        }
    result = math_engine.run_monte_carlo(
        [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}],
        history,
        spy_today_return=0.0,
        simulation_paths=2000,
        neighbor_k=8,
        seed=42,
    )
    assert result is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"At minimum_raw_days - 1 = {minimum_raw_days - 1} raw days, "
        f"run_monte_carlo must return the sentinel "
        f"({math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL!r}); got {result!r}. "
        f"PERF-002 hoist must preserve the eligible-pool boundary."
    )


def test_run_monte_carlo_returns_float_at_eligible_boundary_after_hoist() -> None:
    """Twin of the above; pins the inclusive lower bound. At exactly
    minimum_raw_days the function must NOT return the sentinel."""
    minimum_raw_days = (
        math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
    )
    history = {}
    for i in range(minimum_raw_days):
        sign = 1 if i % 2 == 0 else -1
        history[_date_key(i)] = {
            "SPY": {"daily_ret": sign * 0.005},
            "AAA": {"daily_ret": sign * 0.008},
        }
    result = math_engine.run_monte_carlo(
        [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}],
        history,
        spy_today_return=0.0,
        simulation_paths=2000,
        neighbor_k=8,
        seed=42,
    )
    assert result is not math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"At minimum_raw_days = {minimum_raw_days} raw days, run_monte_carlo "
        f"must return a valid float; got the insufficient-history sentinel. "
        f"PERF-002 hoist must preserve the eligible-pool boundary."
    )
    assert 0.0 <= float(result) <= 100.0


def test_compute_portfolio_cvar_insufficient_branch_boundary_after_hoist() -> None:
    """Per [[project_cvar_divergence_validation_wall]]: the
    CVaR-insufficient branch must fire at exactly the same boundary as
    run_monte_carlo. The hoist must not shift it."""
    minimum_raw_days = (
        math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
    )
    history = {}
    for i in range(minimum_raw_days - 1):
        history[_date_key(i)] = {
            "SPY": {"daily_ret": 0.001},
            "AAA": {"daily_ret": 0.001},
        }
    result = math_engine.compute_portfolio_cvar(
        cycle_id="perf002-boundary",
        holdings=[{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}],
        historical_data=history,
        spy_today_return=0.0,
        simulation_paths=2000,
        neighbor_k=8,
        mode=None,
    )
    assert result.cvar_pct is None, (
        f"At minimum_raw_days - 1, compute_portfolio_cvar must emit the "
        f"insufficient-history sentinel (cvar_pct=None); got "
        f"cvar_pct={result.cvar_pct!r}."
    )
    assert result.insufficient_reason is not None
