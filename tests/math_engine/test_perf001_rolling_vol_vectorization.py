"""
RED tests — PERF-001 (math-reaudit MEDIUM): vectorize run_monte_carlo's rolling
SPY-vol computation.

Finding:
  math_engine.run_monte_carlo currently computes the rolling 20-day SPY vol via
  a Python for-loop iterating once per historical day. On the autotuner walk-
  forward hot path (~750 days, called many times per fold) this is the dominant
  cost. Replace with pd.Series.rolling().std() (or numpy equivalent) for a
  ~10-50x speedup. NO MATH CHANGE — bit-for-bit identical output expected
  across the curated input set.

Acceptance:
  AC-PERF001.1  A helper `_compute_rolling_spy_vol(spy_returns)` exists in
                math_engine and is callable. The helper takes a 1-D numpy
                array and returns a 1-D numpy array of identical length.
  AC-PERF001.2  The helper is numerically identical to a Python-loop reference
                computing `np.std(spy_returns[start:i+1])` for each i, with
                spy_vols[0] = 0.0. Identity is asserted across the curated
                scenarios in fixtures/.../curated_input_scenarios.json.
  AC-PERF001.3  `run_monte_carlo`'s end-to-end output is bit-identical, under
                a fixed seed, to an in-test reference implementation of the
                Python-loop path, across the curated input set.
  AC-PERF001.4  The hot path no longer contains a `for ... in range(len(...))`
                loop computing spy_vols (AST-based shape check).
  AC-PERF001.5  `_compute_rolling_spy_vol` is >=5x faster than a Python-loop
                reference on a 750-day input.

Invariants preserved (load-bearing):
  - [[project_mc_sentinel_consumer_blast_radius]]: run_monte_carlo's return
    type/domain is unchanged (still float in [0, 100] or None sentinel).
  - [[project_mc_eligible_pool_vs_raw_day_boundary]]: min raw history =
    MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1); guard fires below that.

PA-18 compliance:
  No mc_prob literals. No spy_vols literals. Assertions compare the production
  callable against a Python-loop reference computed in-test from the same
  inputs. The reference IS the spec.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import pathlib
import time
from typing import Any

import numpy as np
import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "perf001_rolling_vol_vectorization"
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
    # Year-roll if we ever exceed 12 months (we won't with current scenarios,
    # but keep the function defensive against future fixture growth).
    year = 2024 + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _expand_scenario(scenario: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """Build a historical_data dict from a curated scenario spec.

    raw_day_offset_from_minimum is added to MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1)
    so the fixture never hardcodes the boundary constants.
    """
    minimum_raw_days = math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
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
                "SPY": {"daily_ret": sign * spy_amp},
                "AAA": {"daily_ret": sign * aaa_amp},
                "BBB": {"daily_ret": sign * bbb_amp},
            }
    elif kind == "monotone":
        for i in range(num_days):
            history[_date_key(i)] = {
                "SPY": {"daily_ret": scenario["spy_start"] + i * scenario["spy_step"]},
                "AAA": {"daily_ret": scenario["aaa_start"] + i * scenario["aaa_step"]},
                "BBB": {"daily_ret": 0.0},
            }
    elif kind == "constant_return":
        for i in range(num_days):
            history[_date_key(i)] = {
                "SPY": {"daily_ret": scenario["spy_daily_ret"]},
                "AAA": {"daily_ret": scenario["holding_daily_ret"]},
                "BBB": {"daily_ret": scenario["holding_daily_ret"]},
            }
    else:
        raise ValueError(f"Unknown scenario kind: {kind!r}")

    return history


def _python_loop_rolling_vol_reference(spy_returns: np.ndarray) -> np.ndarray:
    """Reference implementation — the current production Python loop, isolated.

    Spec for the vectorized replacement. This is exactly the loop in
    math_engine.run_monte_carlo prior to PERF-001 vectorization.
    """
    spy_vols = np.zeros_like(spy_returns, dtype=float)
    window = math_engine.MC_VOL_WINDOW_DAYS
    for i in range(len(spy_returns)):
        start_idx = max(0, i - (window - 1))
        if i > 0:
            spy_vols[i] = np.std(spy_returns[start_idx : i + 1])
        else:
            spy_vols[i] = 0.0
    return spy_vols


def _spy_returns_from_history(history: dict) -> np.ndarray:
    """Mirror run_monte_carlo's spy_returns extraction. Same key order."""
    valid_dates = sorted(history.keys())
    return np.array([history[d].get("SPY", {}).get("daily_ret", 0.0) for d in valid_dates])


# ---------------------------------------------------------------------------
# AC-PERF001.1 — Helper exists, callable, correct shape
# ---------------------------------------------------------------------------


def test_compute_rolling_spy_vol_helper_exists_and_is_callable() -> None:
    """The vectorized rolling-vol helper must be a public symbol on math_engine
    so it can be tested in isolation. A monolithic in-place rewrite of
    run_monte_carlo passes AC-PERF001.3 but fails this — and we want the
    helper exposed so the same vectorized code can be reused by
    compute_portfolio_cvar and compute_regime_match_quality (which currently
    duplicate the same loop at math_engine.py:~1344 and ~1557).
    """
    assert hasattr(math_engine, "_compute_rolling_spy_vol"), (
        "math_engine is missing _compute_rolling_spy_vol. Implement: "
        "def _compute_rolling_spy_vol(spy_returns: np.ndarray) -> np.ndarray"
    )
    assert callable(math_engine._compute_rolling_spy_vol), (
        "math_engine._compute_rolling_spy_vol is not callable."
    )


def test_compute_rolling_spy_vol_returns_same_shape_as_input() -> None:
    """Output length must equal input length — callers index it against
    the original valid_dates list."""
    rng = np.random.default_rng(seed=0)
    for n in (40, 60, 100, 250, 750):
        spy_returns = rng.normal(0.0, 0.01, size=n)
        out = math_engine._compute_rolling_spy_vol(spy_returns)
        assert out.shape == spy_returns.shape, (
            f"Length mismatch for n={n}: input {spy_returns.shape}, output {out.shape}."
        )


# ---------------------------------------------------------------------------
# AC-PERF001.2 — Numerical identity, helper vs Python-loop reference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario_id",
    [
        "case_01_long_smooth_history",
        "case_02_at_eligible_boundary",
        "case_03_one_above_boundary",
        "case_04_monotone_returns",
        "case_05_constant_returns",
        "case_06_high_low_alternation",
    ],
)
def test_helper_matches_python_loop_reference_per_scenario(scenario_id: str) -> None:
    """For every curated scenario, _compute_rolling_spy_vol must produce values
    indistinguishable from the Python-loop reference. Tolerance is tight:
    a pandas/numpy vectorization of the SAME population-std formula
    (np.std with ddof=0 default) should agree to within float64 rounding.

    Tolerance rationale: numpy.std uses a two-pass / Welford-style
    accumulator; pandas.Series.rolling().std() defaults to ddof=1 (sample
    std), which would FAIL this test. The implementer must either use
    ddof=0 explicitly or implement the variance manually. Tolerance set to
    1e-12 absolute — well above float64 epsilon (~2.2e-16), tight enough
    to catch any ddof mistake (which would diverge at ~5% on a 20-window).
    """
    fixture = _load_fixture("curated_input_scenarios.json")
    scenario = next(s for s in fixture["scenarios"] if s["id"] == scenario_id)
    history = _expand_scenario(scenario)
    spy_returns = _spy_returns_from_history(history)

    expected = _python_loop_rolling_vol_reference(spy_returns)
    actual = math_engine._compute_rolling_spy_vol(spy_returns)

    # spy_vols[0] must be exactly 0.0 — pinned because pandas rolling
    # produces NaN by default at index 0 and a missed manual override
    # would propagate NaN into the z-score and distance computation.
    assert actual[0] == 0.0, (
        f"[{scenario_id}] spy_vols[0] must be 0.0 (matches current production "
        f"behavior), got {actual[0]!r}. A pandas .rolling().std() rewrite that "
        f"leaves NaN at index 0 would crash the downstream z-score."
    )

    # Element-wise identity check. Reference uses ddof=0 (np.std default);
    # a pd.Series.rolling().std() rewrite that uses ddof=1 will diverge here.
    np.testing.assert_allclose(
        actual,
        expected,
        rtol=0.0,
        atol=1e-12,
        err_msg=(
            f"[{scenario_id}] _compute_rolling_spy_vol diverges from the "
            f"Python-loop reference. Likely cause: ddof=1 vs ddof=0 "
            f"(pd.Series.rolling().std() default is ddof=1; production np.std "
            f"default is ddof=0). Use ddof=0 explicitly or compute variance "
            f"manually."
        ),
    )


def test_helper_handles_short_arrays_below_window() -> None:
    """For an array shorter than the vol window, every spy_vols[i] for i>=1
    uses the full prefix (start_idx clamped to 0). This is the early-window
    behavior; the helper must match the reference there too — even though
    run_monte_carlo's eligibility guard excludes these days from the
    candidate pool, compute_portfolio_cvar and compute_regime_match_quality
    use the same array and rely on the same early-window values.
    """
    spy_returns = np.array([0.001, -0.002, 0.003, -0.001, 0.0, 0.002])
    expected = _python_loop_rolling_vol_reference(spy_returns)
    actual = math_engine._compute_rolling_spy_vol(spy_returns)
    np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1e-12)


def test_helper_handles_single_element_input() -> None:
    """Degenerate input — a 1-element array must return [0.0]. Pins the
    same boundary as the i==0 branch in the reference loop.
    """
    out = math_engine._compute_rolling_spy_vol(np.array([0.005]))
    assert out.shape == (1,)
    assert out[0] == 0.0


def test_helper_handles_empty_input() -> None:
    """Empty input must return an empty array, not raise. The eligibility
    guard prevents this on the run_monte_carlo path, but
    compute_regime_match_quality calls the same helper unconditionally
    after its own eligibility check — empty arrays should not crash.
    """
    out = math_engine._compute_rolling_spy_vol(np.array([], dtype=float))
    assert out.shape == (0,)


def test_helper_does_not_mutate_input() -> None:
    """The helper must be pure. A pandas-backed implementation that does
    in-place ops on a numpy view could corrupt callers' arrays.
    """
    spy_returns = np.array([0.001, -0.002, 0.003, -0.001, 0.0, 0.002, 0.004])
    snapshot = spy_returns.copy()
    _ = math_engine._compute_rolling_spy_vol(spy_returns)
    np.testing.assert_array_equal(
        spy_returns,
        snapshot,
        err_msg="_compute_rolling_spy_vol mutated its input array.",
    )


# ---------------------------------------------------------------------------
# AC-PERF001.3 — End-to-end identity: run_monte_carlo output unchanged
# ---------------------------------------------------------------------------


def _reference_run_monte_carlo(
    holdings,
    historical_data,
    spy_today_return,
    simulation_paths,
    neighbor_k,
    seed,
):
    """In-test reference replicating run_monte_carlo's logic with the
    Python-loop rolling-vol computation. Built from the same constants and
    arithmetic as the current production function.

    PA-18: this is the SPEC, not a producer output. The reference is the
    pre-PERF-001 implementation, frozen in the test file.
    """
    from math_engine import (
        MC_INSUFFICIENT_HISTORY_SENTINEL,
        MC_MIN_HISTORY_DAYS,
        MC_VOL_WINDOW_DAYS,
        PCT_SCALAR,
    )

    valid_dates = sorted(historical_data.keys())
    eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)
    if eligible_days < MC_MIN_HISTORY_DAYS:
        return MC_INSUFFICIENT_HISTORY_SENTINEL

    current_symphony_return = sum(
        (h.get("last_percent_change", 0.0) * PCT_SCALAR) * h.get("allocation", 0.0)
        for h in holdings
        if h.get("last_percent_change") is not None
    )

    spy_returns = np.array(
        [historical_data[d].get("SPY", {}).get("daily_ret", 0.0) for d in valid_dates]
    )
    spy_vols = _python_loop_rolling_vol_reference(spy_returns)
    spy_today_ret_dec = spy_today_return / PCT_SCALAR
    today_vol = np.std(np.append(spy_returns[-(MC_VOL_WINDOW_DAYS - 1) :], spy_today_ret_dec))

    candidate_idx = np.arange(MC_VOL_WINDOW_DAYS - 1, len(spy_returns))
    cand_returns = spy_returns[candidate_idx]
    cand_vols = spy_vols[candidate_idx]

    ret_mean, ret_std = float(np.mean(cand_returns)), float(np.std(cand_returns))
    vol_mean, vol_std = float(np.mean(cand_vols)), float(np.std(cand_vols))

    def _z(values, mean, std):
        if std == 0.0:
            return np.zeros_like(values, dtype=float)
        return (values - mean) / std

    cand_returns_z = _z(cand_returns, ret_mean, ret_std)
    cand_vols_z = _z(cand_vols, vol_mean, vol_std)
    today_ret_z = 0.0 if ret_std == 0.0 else (spy_today_ret_dec - ret_mean) / ret_std
    today_vol_z = 0.0 if vol_std == 0.0 else (today_vol - vol_mean) / vol_std

    distances = np.sqrt((cand_returns_z - today_ret_z) ** 2 + (cand_vols_z - today_vol_z) ** 2)

    # PERF-001 is a math-preservation refactor; we don't need to replicate the
    # full path simulation here. We test end-to-end via the public function:
    # if its spy_vols differ, the kNN selection differs, and the seeded
    # mc_prob differs. So we ONLY need to compare a derivable upstream signal
    # — but the cleanest contract is: call run_monte_carlo twice — once with
    # a monkeypatched reference helper, once with the production helper —
    # under the same seed, and assert equality. We implement that comparison
    # in the test below using monkeypatch.
    raise NotImplementedError(
        "This reference is exercised indirectly via the monkeypatched test "
        "below; do not call directly."
    )


@pytest.mark.parametrize(
    "scenario_id",
    [
        "case_01_long_smooth_history",
        "case_02_at_eligible_boundary",
        "case_03_one_above_boundary",
        "case_04_monotone_returns",
        "case_05_constant_returns",
        "case_06_high_low_alternation",
    ],
)
def test_run_monte_carlo_end_to_end_identical_to_python_loop_path(
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end identity: under a fixed seed, run_monte_carlo's output
    is bit-identical whether the rolling-vol uses the vectorized helper
    or the Python-loop reference.

    Test mechanism: call run_monte_carlo with the production helper, then
    monkey-patch _compute_rolling_spy_vol to the Python-loop reference and
    call again. The two results MUST be equal — that is the math-preservation
    guarantee of PERF-001 (the audit explicitly says "no math change").

    PA-18: the expected value is the in-test reference's output, computed
    from the same inputs. No literal mc_prob is pinned.

    Tolerance rationale: strict equality (==). The vectorized helper, given
    correct ddof=0 semantics, produces float64 output that should match the
    Python loop bit-for-bit (np.std is the same primitive in both paths).
    If the implementer uses a different algorithm (e.g. Welford), tighten
    to atol=1e-15; do NOT loosen past 1e-12. PERF-001 is a refactor, not a
    numerical-precision change.
    """
    fixture = _load_fixture("curated_input_scenarios.json")
    scenario = next(s for s in fixture["scenarios"] if s["id"] == scenario_id)
    history = _expand_scenario(scenario)
    holdings = fixture["holdings"]
    spy_today_return = fixture["spy_today_return"]
    simulation_paths = fixture["simulation_paths"]
    neighbor_k = fixture["neighbor_k"]
    seed = fixture["seed"]

    # Sanity gate: run_monte_carlo must actually CALL the helper for the
    # monkey-patch to be meaningful. A monolithic in-place rewrite of
    # run_monte_carlo that inlines pd.Series.rolling().std() bypasses the
    # helper and would falsely pass below (both runs identical because the
    # patch is unused). The AST sweep also enforces this, but pin it here too
    # so the test is self-contained.
    rmc_src = inspect.getsource(math_engine.run_monte_carlo)
    assert "_compute_rolling_spy_vol" in rmc_src, (
        f"[{scenario_id}] run_monte_carlo source does not reference "
        f"_compute_rolling_spy_vol — the helper exists but is unused, so "
        f"this monkeypatch-based identity test is trivially passing. The "
        f"production code must delegate rolling-vol computation to the helper."
    )

    # Run 1: production helper
    result_production = math_engine.run_monte_carlo(
        holdings,
        history,
        spy_today_return,
        simulation_paths=simulation_paths,
        neighbor_k=neighbor_k,
        seed=seed,
    )

    # Run 2: monkey-patch the helper to the Python-loop reference and re-call.
    # If math is preserved, this MUST yield identical output.
    monkeypatch.setattr(
        math_engine,
        "_compute_rolling_spy_vol",
        _python_loop_rolling_vol_reference,
    )
    result_reference = math_engine.run_monte_carlo(
        holdings,
        history,
        spy_today_return,
        simulation_paths=simulation_paths,
        neighbor_k=neighbor_k,
        seed=seed,
    )

    # Both finite — sanity check.
    assert math.isfinite(result_production), (
        f"[{scenario_id}] production run_monte_carlo returned non-finite {result_production!r}."
    )
    assert math.isfinite(result_reference), (
        f"[{scenario_id}] reference path returned non-finite {result_reference!r}."
    )

    # Strict equality — same RNG seed + same kNN selection ⇒ same draws ⇒
    # same probability. Any divergence indicates a real math change.
    assert result_production == result_reference, (
        f"[{scenario_id}] run_monte_carlo output diverges between vectorized "
        f"({result_production!r}) and Python-loop reference "
        f"({result_reference!r}) under the same seed. PERF-001 is a "
        f"math-preserving refactor — any divergence is a bug."
    )


# ---------------------------------------------------------------------------
# AC-PERF001.3b — Return-type / blast-radius preserved
# ---------------------------------------------------------------------------


def test_run_monte_carlo_returns_float_in_range_on_sufficient_history() -> None:
    """Per [[project_mc_sentinel_consumer_blast_radius]]: the return type
    must remain `float in [0, 100]` on a sufficient history. A vectorization
    that accidentally returns a numpy scalar or an ndarray would break the
    7+ consumer sites that do `f"{prob_underperforming:.1f}"` or numeric comparison.
    """
    fixture = _load_fixture("curated_input_scenarios.json")
    scenario = next(s for s in fixture["scenarios"] if s["id"] == "case_01_long_smooth_history")
    history = _expand_scenario(scenario)
    result = math_engine.run_monte_carlo(
        fixture["holdings"],
        history,
        fixture["spy_today_return"],
        simulation_paths=fixture["simulation_paths"],
        neighbor_k=fixture["neighbor_k"],
        seed=fixture["seed"],
    )
    # A numpy float subclasses Python float, but isinstance(np.float64(x), float)
    # is True on supported platforms. Test the contract used by consumers:
    # f-string formatting must succeed.
    assert result is not None, "Sufficient history must not return the sentinel."
    assert math.isfinite(result), f"Result {result!r} must be finite."
    assert 0.0 <= float(result) <= 100.0, f"Result {result!r} out of [0, 100]."
    # f-string format used by alpha_bot_execution.py:~1265 and reporting.py.
    # If the result is an ndarray this raises TypeError.
    _ = f"{result:.1f}"


def test_run_monte_carlo_returns_sentinel_one_below_eligible_boundary() -> None:
    """Per [[project_mc_eligible_pool_vs_raw_day_boundary]]: at
    minimum_raw_days - 1, the function must return MC_INSUFFICIENT_HISTORY_SENTINEL.
    Vectorization must not change this boundary.
    """
    minimum_raw_days = math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
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
        f"PERF-001 vectorization must preserve the eligible-pool boundary."
    )


def test_run_monte_carlo_returns_float_at_eligible_boundary() -> None:
    """At exactly minimum_raw_days, the function must return a valid float
    (not the sentinel). Twin of the above; pins the inclusive lower bound.
    """
    minimum_raw_days = math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
    history = {}
    for i in range(minimum_raw_days):
        # Mild alternation so vols and returns are non-degenerate.
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
        f"PERF-001 vectorization must preserve the eligible-pool boundary."
    )
    assert 0.0 <= float(result) <= 100.0


# ---------------------------------------------------------------------------
# AC-PERF001.4 — Implementation shape: no Python loop over history
# ---------------------------------------------------------------------------


def _function_source(fn) -> str:
    return inspect.getsource(fn)


def test_run_monte_carlo_source_does_not_contain_python_for_loop_computing_spy_vols() -> None:
    """Shape check: the PERF-001 fix is meaningless if the loop is still there.
    Walk the AST of run_monte_carlo and assert no `for ... in range(len(...))`
    loop assigns into spy_vols. A pandas/numpy rewrite uses no such loop.

    Why AST and not a string match: a comment containing 'for i in range' would
    false-positive a grep. AST is precise about real control flow.
    """
    src = _function_source(math_engine.run_monte_carlo)
    tree = ast.parse(src)

    offending_loops: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        # Look for `for VAR in range(...)` where the body assigns to spy_vols[...]
        is_range_call = (
            isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "range"
        )
        if not is_range_call:
            continue
        body_assigns_spy_vols = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for tgt in sub.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "spy_vols"
                    ):
                        body_assigns_spy_vols = True
        if body_assigns_spy_vols:
            offending_loops.append(ast.unparse(node))

    assert not offending_loops, (
        "run_monte_carlo still contains a Python `for ... in range(...)` loop "
        "that assigns to spy_vols. PERF-001 requires vectorization via "
        "pd.Series.rolling().std() or a numpy-only equivalent. Offending "
        "loops:\n" + "\n---\n".join(offending_loops)
    )


def test_compute_rolling_spy_vol_helper_source_does_not_contain_python_for_loop() -> None:
    """The helper itself must be vectorized. A helper that just wraps the
    same Python loop trivially passes the in-place AST check (because the
    loop moved out of run_monte_carlo), but does not deliver the perf gain.
    """
    src = _function_source(math_engine._compute_rolling_spy_vol)
    tree = ast.parse(src)
    range_loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "range"
    ]
    assert not range_loops, (
        "_compute_rolling_spy_vol contains a `for ... in range(...)` loop. "
        "Vectorize with pd.Series.rolling().std() (ddof=0) or numpy stride "
        "tricks. Found loops:\n" + "\n---\n".join(ast.unparse(n) for n in range_loops)
    )


# ---------------------------------------------------------------------------
# AC-PERF001.5 — Performance: >=5x faster than Python-loop reference
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_compute_rolling_spy_vol_is_at_least_5x_faster_than_python_loop() -> None:
    """Wall-clock benchmark. The PERF-001 finding claims ~10-50x speedup;
    we assert >=5x as the floor — leaves headroom for CI variance while
    still catching a no-op rewrite (which would be ~1x).

    Methodology:
      - 750-day input (the autotuner walk-forward horizon, per project CLAUDE.md
        synthetic_history.py — "125-day live Alpaca historical fetcher" used
        ~6x per walk-forward fold so an order-of-magnitude proxy is fine).
      - 50 repetitions each, best-of-3 median (reduces JIT/cache noise).
      - reference_time / vectorized_time >= 5.0.

    Tolerance rationale: 5x floor (not 10x) because:
      1. The Python loop calls np.std which itself is vectorized for the
         window slice — so the speedup ceiling is the loop overhead alone,
         not full vectorization gain.
      2. CI hosts vary; a 10x floor would flake.
      3. The implementer is free to ship a >10x solution; this only blocks
         no-op rewrites.

    Marked @pytest.mark.perf — excluded from default run, opt-in via -m perf.
    """
    rng = np.random.default_rng(seed=20260527)
    spy_returns = rng.normal(0.0, 0.012, size=750)

    repetitions = 50
    best_of = 3

    def _time(callable_fn) -> float:
        # Warm-up
        callable_fn(spy_returns)
        samples = []
        for _ in range(best_of):
            t0 = time.perf_counter()
            for _ in range(repetitions):
                callable_fn(spy_returns)
            samples.append(time.perf_counter() - t0)
        return min(samples)

    vectorized_time = _time(math_engine._compute_rolling_spy_vol)
    reference_time = _time(_python_loop_rolling_vol_reference)

    speedup = reference_time / vectorized_time if vectorized_time > 0 else float("inf")
    assert speedup >= 5.0, (
        f"_compute_rolling_spy_vol speedup is {speedup:.2f}x; PERF-001 requires "
        f">=5x vs the Python-loop reference on a 750-day input. "
        f"vectorized={vectorized_time * 1000:.2f}ms total over "
        f"{repetitions} reps; reference={reference_time * 1000:.2f}ms. "
        f"A no-op rewrite scores ~1x; pd.Series.rolling().std() with ddof=0 "
        f"or a numpy strided implementation scores >>5x."
    )


@pytest.mark.perf
def test_compute_rolling_spy_vol_wall_clock_under_5ms_at_750_days() -> None:
    """Absolute wall-clock budget. Even if the Python loop were already
    fast on the CI host (skewing the relative-speedup test), the vectorized
    helper must complete a single 750-day call in < 5 ms.

    Why 5 ms: the autotuner runs ~500 trials per symphony, each trial does
    ~125 daily walk-forward steps, each step calls run_monte_carlo (and
    soon compute_portfolio_cvar) which calls this helper. So the helper
    runs ~62,500 times per study. At 5 ms each that is ~5 minutes of pure
    rolling-vol cost per study — already steep. Real target is sub-ms; 5 ms
    is the hard ceiling we refuse to cross.
    """
    rng = np.random.default_rng(seed=20260527)
    spy_returns = rng.normal(0.0, 0.012, size=750)
    # Warm-up
    math_engine._compute_rolling_spy_vol(spy_returns)
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        math_engine._compute_rolling_spy_vol(spy_returns)
        samples.append(time.perf_counter() - t0)
    median_ms = sorted(samples)[len(samples) // 2] * 1000.0
    assert median_ms < 5.0, (
        f"_compute_rolling_spy_vol median wall-clock is {median_ms:.3f} ms on a "
        f"750-day input; PERF-001 requires < 5 ms. Samples (ms): "
        f"{[round(s * 1000, 3) for s in samples]}."
    )
