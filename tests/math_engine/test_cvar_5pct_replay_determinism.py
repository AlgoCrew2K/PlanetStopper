"""
RED tests — M2 cvar_5pct replay determinism (plan deliverable 3).

Contract under test:
  compute_portfolio_cvar(cycle_id, holdings, historical_data, ...) must return
  bit-identical cvar_5pct and cvar_n_tail when called twice with the same
  (cycle_id, holdings, historical_data).

  The seed is derive_cycle_mc_seed(cycle_id) — the Phase-1 anchor (§3.7:
  exactly 1 anchor in Phase 1).  No other seeding path is permitted.

  Tests 3 and 5 from the plan are RED on trunk before M2 lands (plan §Definition
  of Done (b)).

Binding refs:
  - live-vs-replay-safety-boundary plan deliverable (3)
  - decision-science-council-synthesis.md §3.8 (Gate 1 bit-identical replay)
  - math_engine.py:695-702 (derive_cycle_mc_seed) — Phase-1 seed anchor
  - math_engine.py:828 (isolated np.random.default_rng) — required shape
  - Plan risk R3: global RNG bleed (np.random.seed or np.random.choice = fail)
  - Plan risk R4: mode= default disallowed

Fixtures:
  tests/fixtures/math_engine/cvar_5pct_replay_determinism/01_standard_inputs.json  (reproducibility tests)
  tests/fixtures/math_engine/cvar_5pct_replay_determinism/02_distinguishability_inputs.json  (seed-distinguishability test)

PA-18: no cvar_5pct or cvar_n_tail literals anywhere. Bit-equality is asserted
as run_a == run_b, never against a hardcoded expected value.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "cvar_5pct_replay_determinism"
)


# ---------------------------------------------------------------------------
# Fixture loading and history builder (reuses mc_rng_seeding pattern)
# ---------------------------------------------------------------------------


def _load_fixture(filename: str) -> dict:
    with (FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
    """Synthetic ISO date key; same helper as mc_rng_seeding tests."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict) -> dict:
    """Expand historical_data_spec into the shape expected by compute_portfolio_cvar.

    Supports three kinds:
      "alternating"  — two-level ±amplitude pattern (reproducibility tests)
      "multi_level"  — cycles through N level dicts (discrete tiers, limited use)
      "explicit"     — pre-computed list of {spy, aaa, bbb} dicts stored in the
                       fixture as literals. Required for distinguishability tests
                       because kNN selection is deterministic on the pool; with
                       continuous-valued explicit returns, different seeds draw
                       different tail samples and produce distinguishable CVaR means.
    """
    kind = spec["kind"]
    history: dict = {}

    if kind == "explicit":
        # days is a list of {spy, aaa, bbb} dicts with pre-computed continuous returns.
        for i, day in enumerate(spec["days"]):
            history[_date_key(i)] = {
                "SPY": {"daily_ret": day["spy"]},
                "AAA": {"daily_ret": day["aaa"]},
                "BBB": {"daily_ret": day["bbb"]},
            }
        return history

    num_days = spec["num_days"]

    if kind == "multi_level":
        levels = spec["levels"]
        n = len(levels)
        for i in range(num_days):
            lvl = levels[i % n]
            history[_date_key(i)] = {
                "SPY": {"daily_ret": lvl["spy"]},
                "AAA": {"daily_ret": lvl["aaa"]},
                "BBB": {"daily_ret": lvl["bbb"]},
            }
        return history

    if kind == "alternating":
        spy_amp = spec["spy_amplitude"]
        aaa_amp = spec["aaa_amplitude"]
        bbb_amp = spec.get("bbb_amplitude", 0.0)
        for i in range(num_days):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {"daily_ret": sign * spy_amp},
                "AAA": {"daily_ret": sign * aaa_amp},
                "BBB": {"daily_ret": sign * bbb_amp},
            }
    else:
        raise ValueError(f"Unknown historical_data_spec kind: {kind!r}")

    return history


# ---------------------------------------------------------------------------
# 1. compute_portfolio_cvar exists — RED gate
# ---------------------------------------------------------------------------


def test_compute_portfolio_cvar_exists():
    """M2 function compute_portfolio_cvar must be importable from math_engine.

    This fails RED on trunk until the implementer adds it.
    """
    assert hasattr(math_engine, "compute_portfolio_cvar"), (
        "math_engine is missing compute_portfolio_cvar. "
        "Plan deliverable (3): the Phase-1 M2 replay-determinism anchor. "
        "Add: def compute_portfolio_cvar(cycle_id, holdings, historical_data, ...) -> CVaRAssessment"
    )


# ---------------------------------------------------------------------------
# 2. Bit-identical replay — same cycle_id + inputs → same cvar_5pct (RED)
# ---------------------------------------------------------------------------


def test_compute_portfolio_cvar_cvar_5pct_is_bit_identical_across_two_calls():
    """AC §3.8 Gate-1: two independent calls with the same cycle_id must yield
    bit-identical cvar_5pct.

    This is the Phase-1 single replay-determinism anchor.  The seed is
    derived exclusively via derive_cycle_mc_seed(cycle_id) — the function
    must not use the global RNG or any other source of entropy.

    PA-18: no expected literal. Equality is asserted as run_a == run_b.
    """
    if not hasattr(math_engine, "compute_portfolio_cvar"):
        pytest.fail(
            "compute_portfolio_cvar not found in math_engine. "
            "Plan deliverable (3) is RED — implement M2 to go GREEN."
        )

    fixture = _load_fixture("01_standard_inputs.json")
    cycle_id = fixture["cycle_id"]
    holdings = fixture["inputs"]["holdings"]
    history = _build_history(fixture["inputs"]["historical_data_spec"])

    # Run A — independent call
    result_a = math_engine.compute_portfolio_cvar(
        cycle_id=cycle_id,
        holdings=holdings,
        historical_data=history,
        spy_today_return=fixture["inputs"]["spy_today_return"],
        simulation_paths=fixture["inputs"]["simulation_paths"],
        neighbor_k=fixture["inputs"]["neighbor_k"],
    )

    # Run B — independent call; the function must re-derive the seed from cycle_id
    result_b = math_engine.compute_portfolio_cvar(
        cycle_id=cycle_id,
        holdings=holdings,
        historical_data=history,
        spy_today_return=fixture["inputs"]["spy_today_return"],
        simulation_paths=fixture["inputs"]["simulation_paths"],
        neighbor_k=fixture["inputs"]["neighbor_k"],
    )

    # Both results must be CVaRAssessment instances
    assert isinstance(result_a, math_engine.CVaRAssessment), (
        f"compute_portfolio_cvar must return CVaRAssessment; got {type(result_a)}"
    )
    assert isinstance(result_b, math_engine.CVaRAssessment), (
        f"compute_portfolio_cvar must return CVaRAssessment; got {type(result_b)}"
    )

    # Bit-identical cvar_5pct — strict equality, not approx (§3.8)
    assert result_a.cvar_pct == result_b.cvar_pct, (
        f"compute_portfolio_cvar returned different cvar_5pct across two calls "
        f"with identical (cycle_id, holdings, historical_data). "
        f"Run A: {result_a.cvar_pct!r}, Run B: {result_b.cvar_pct!r}. "
        "The Phase-1 seed anchor (derive_cycle_mc_seed) must fully determine the "
        "RNG state. Global RNG bleed (np.random.seed) breaks this — use "
        "np.random.default_rng(derive_cycle_mc_seed(cycle_id)) exclusively."
    )


def test_compute_portfolio_cvar_n_tail_is_bit_identical_across_two_calls():
    """cvar_n_tail must also be bit-identical across two calls with the same inputs.

    n_tail counts the tail observations drawn from the seeded simulation — it
    must be the same integer when the seed is identical (follows from bit-identical
    cvar_5pct but stated explicitly per plan deliverable 3 assertion scope).

    PA-18: no expected literal. Equality is asserted as run_a == run_b.
    """
    if not hasattr(math_engine, "compute_portfolio_cvar"):
        pytest.fail("compute_portfolio_cvar not found — see test_compute_portfolio_cvar_exists.")

    fixture = _load_fixture("01_standard_inputs.json")
    cycle_id = fixture["cycle_id"]
    holdings = fixture["inputs"]["holdings"]
    history = _build_history(fixture["inputs"]["historical_data_spec"])

    kwargs = dict(
        cycle_id=cycle_id,
        holdings=holdings,
        historical_data=history,
        spy_today_return=fixture["inputs"]["spy_today_return"],
        simulation_paths=fixture["inputs"]["simulation_paths"],
        neighbor_k=fixture["inputs"]["neighbor_k"],
    )

    result_a = math_engine.compute_portfolio_cvar(**kwargs)
    result_b = math_engine.compute_portfolio_cvar(**kwargs)

    assert result_a.tail_obs_count == result_b.tail_obs_count, (
        f"cvar_n_tail mismatch: run_a={result_a.tail_obs_count!r}, run_b={result_b.tail_obs_count!r}. "
        "Both calls have identical inputs — n_tail must be bit-identical when the seed is fixed."
    )


# ---------------------------------------------------------------------------
# 3. Seed is derive_cycle_mc_seed(cycle_id) — not global RNG (RED)
# ---------------------------------------------------------------------------


def test_compute_portfolio_cvar_does_not_use_global_rng():
    """Hostile: compute_portfolio_cvar must not mutate the numpy global RNG.

    Plan risk R3: np.random.seed() or np.random.choice() inside M2 breaks
    cross-restart determinism. The generator must be isolated via
    np.random.default_rng(derive_cycle_mc_seed(cycle_id)).

    Method: advance global RNG to a known position; call compute_portfolio_cvar
    with a seeded cycle_id; assert global RNG continues from the same position
    (no mutation).
    """
    if not hasattr(math_engine, "compute_portfolio_cvar"):
        pytest.fail("compute_portfolio_cvar not found — see test_compute_portfolio_cvar_exists.")

    fixture = _load_fixture("01_standard_inputs.json")
    holdings = fixture["inputs"]["holdings"]
    history = _build_history(fixture["inputs"]["historical_data_spec"])

    # Branch A: record next global value before M2 call
    np.random.seed(42)
    expected_next = np.random.random()

    # Branch B: reset global to same position, call M2, record next global value
    np.random.seed(42)
    math_engine.compute_portfolio_cvar(
        cycle_id=fixture["cycle_id"],
        holdings=holdings,
        historical_data=history,
        spy_today_return=fixture["inputs"]["spy_today_return"],
        simulation_paths=fixture["inputs"]["simulation_paths"],
        neighbor_k=fixture["inputs"]["neighbor_k"],
    )
    actual_next = np.random.random()

    assert actual_next == pytest.approx(expected_next, rel=0.0, abs=0.0), (
        f"compute_portfolio_cvar mutated the numpy global RNG. "
        f"Expected next np.random.random() = {expected_next!r}, got {actual_next!r}. "
        "Use np.random.default_rng(derive_cycle_mc_seed(cycle_id)) — never np.random.seed()."
    )


# ---------------------------------------------------------------------------
# 4. Different cycle_ids produce different seeds → different cvar_5pct
# ---------------------------------------------------------------------------


def test_different_cycle_ids_produce_different_cvar_5pct():
    """Two different cycle_ids must (probabilistically) produce different cvar_5pct.

    This guards against an implementation that ignores cycle_id and always
    uses a fixed or global seed. With 2000 paths and a multi-level distribution,
    the chance of two different seeds producing identical 5th-percentile CVaR is
    negligible.

    Uses 02_distinguishability_inputs.json (multi_level kind) instead of the
    standard fixture — the alternating ±amplitude pattern in 01_standard_inputs.json
    produces only two distinct return values, which saturates the 5th percentile to
    the same constant regardless of seed (fixture saturation). The multi_level kind
    has 4 distinct tiers so seed differences propagate into the tail.

    PA-18: no expected literals. Inequality is asserted as run_a != run_b.
    """
    if not hasattr(math_engine, "compute_portfolio_cvar"):
        pytest.fail("compute_portfolio_cvar not found.")

    fixture = _load_fixture("02_distinguishability_inputs.json")
    holdings = fixture["inputs"]["holdings"]
    history = _build_history(fixture["inputs"]["historical_data_spec"])

    kwargs_common = dict(
        holdings=holdings,
        historical_data=history,
        spy_today_return=fixture["inputs"]["spy_today_return"],
        simulation_paths=fixture["inputs"]["simulation_paths"],
        neighbor_k=fixture["inputs"]["neighbor_k"],
    )

    result_a = math_engine.compute_portfolio_cvar(cycle_id="20240101_0930", **kwargs_common)
    result_b = math_engine.compute_portfolio_cvar(cycle_id="20240102_0930", **kwargs_common)

    # Only check when both results have a non-None cvar_pct (sufficient data available)
    if result_a.cvar_pct is not None and result_b.cvar_pct is not None:
        assert result_a.cvar_pct != result_b.cvar_pct, (
            f"Different cycle_ids produced identical cvar_5pct={result_a.cvar_pct!r}. "
            "If this is a stable fixture failure (saturation), revise fixture inputs. "
            "If cycle_id is being ignored as the seed, this is a correctness defect."
        )


# ---------------------------------------------------------------------------
# 5. compute_portfolio_cvar returns CVaRAssessment (shape contract)
# ---------------------------------------------------------------------------


def test_compute_portfolio_cvar_returns_cvar_assessment_instance():
    """compute_portfolio_cvar must return a CVaRAssessment dataclass.

    Shape test: assert the return type is CVaRAssessment with the expected
    fields present (cvar_pct, breach, tail_obs_count, insufficient_reason).
    No value assertions — PA-18.
    """
    if not hasattr(math_engine, "compute_portfolio_cvar"):
        pytest.fail("compute_portfolio_cvar not found.")

    fixture = _load_fixture("01_standard_inputs.json")
    result = math_engine.compute_portfolio_cvar(
        cycle_id=fixture["cycle_id"],
        holdings=fixture["inputs"]["holdings"],
        historical_data=_build_history(fixture["inputs"]["historical_data_spec"]),
        spy_today_return=fixture["inputs"]["spy_today_return"],
        simulation_paths=fixture["inputs"]["simulation_paths"],
        neighbor_k=fixture["inputs"]["neighbor_k"],
    )

    assert isinstance(result, math_engine.CVaRAssessment), (
        f"compute_portfolio_cvar must return CVaRAssessment; got {type(result)!r}"
    )
    # All required fields must be present (CVaRAssessment is a frozen dataclass)
    for field in ("cvar_pct", "breach", "tail_obs_count", "insufficient_reason"):
        assert hasattr(result, field), (
            f"CVaRAssessment result is missing field {field!r}. "
            "Check that compute_portfolio_cvar returns a valid CVaRAssessment."
        )
    # Fail-safe invariant: breach must be False when cvar_pct is None
    if result.cvar_pct is None:
        assert result.breach is False, (
            "CVaRAssessment fail-safe violated: cvar_pct is None but breach is True. "
            "An absent estimate must never trigger a breach signal."
        )
    # Non-negative tail_obs_count
    assert result.tail_obs_count >= 0, (
        f"tail_obs_count must be non-negative; got {result.tail_obs_count!r}"
    )
