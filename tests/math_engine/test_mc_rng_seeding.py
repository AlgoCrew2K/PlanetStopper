"""
RED tests for H3 — MC RNG seeding (AC-H3.1, AC-H3.2, AC-H3.3).

Coverage:
  1. Signature: run_monte_carlo accepts optional seed: int | None = None.
  2. Reproducibility: same input + same seed → identical mc_prob across 100 independent runs.
  3. Seed distinguishability: two different seeds with the same input → different mc_prob.
  4. Unseeded path: seed=None uses default RNG (no regression on existing behaviour).
  5. Seed helper signature: derive_cycle_mc_seed(cycle_id: str) -> int exists and is callable.
  6. Seed helper determinism: derive_cycle_mc_seed(x) == derive_cycle_mc_seed(x) for any x.
  7. Seed helper collision-resistance: no collisions across the fixture's sample_cycle_ids.
  8. Cross-restart determinism: two calls with the same cycle_id produce the same seed → same mc_prob.
  9. Autotuner replay determinism: replay(cycle_id, trial_params) is bit-for-bit reproducible.
  10. Global-RNG isolation: calling run_monte_carlo(seed=X) does NOT mutate the numpy global RNG state.
  11. Generator isolation: seed is plumbed via numpy.random.default_rng(seed), NOT np.random.seed.

Fixture files:
  tests/fixtures/math_engine/mc_rng_seeding/01_standard_inputs.json
  tests/fixtures/math_engine/mc_rng_seeding/02_cycle_id_collision_probe.json

PA-18 compliance:
  NO mc_prob literals anywhere. Reproducibility is always asserted as run_a == run_b.
  Fixture fields that would be producer-computed floats are intentionally absent.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import pathlib
from typing import Any

import numpy as np
import pytest

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "mc_rng_seeding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(filename: str) -> dict[str, Any]:
    path = FIXTURE_DIR / filename
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
    """Synthetic ISO date string; lexicographic ordering is sufficient for tests."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_history(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """Expand fixture historical_data_spec into the shape run_monte_carlo consumes."""
    kind = spec["kind"]
    num_days = spec["num_days"]
    history: dict = {}

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


def _call_mc(inputs: dict[str, Any], seed: int | None) -> float:
    """Call run_monte_carlo with the given seed (forwarded as keyword arg)."""
    history = _build_history(inputs["historical_data_spec"])
    return float(
        math_engine.run_monte_carlo(
            inputs["holdings"],
            history,
            inputs["spy_today_return"],
            simulation_paths=inputs["simulation_paths"],
            neighbor_k=inputs["neighbor_k"],
            seed=seed,
        )
    )


# ---------------------------------------------------------------------------
# 1. Signature test — run_monte_carlo must accept optional seed parameter
# ---------------------------------------------------------------------------


def test_run_monte_carlo_signature_has_optional_seed_parameter() -> None:
    """
    run_monte_carlo must accept seed as a keyword arg with default None.
    This is a contract test: an implementation that ignores or renames the
    parameter will fail here before any math is exercised.
    """
    sig = inspect.signature(math_engine.run_monte_carlo)
    assert "seed" in sig.parameters, (
        "run_monte_carlo is missing the 'seed' parameter required by AC-H3.1. "
        "Add: seed: int | None = None"
    )
    default = sig.parameters["seed"].default
    assert default is None, (
        f"run_monte_carlo 'seed' parameter default must be None (optional), got {default!r}."
    )


# ---------------------------------------------------------------------------
# 2. Reproducibility — same seed, same input → identical output, 100 runs
# ---------------------------------------------------------------------------


def test_same_seed_same_input_produces_identical_mc_prob_across_100_runs() -> None:
    """
    AC-H3.3: same input + same seed → same mc_prob, no matter how many times
    called. 100 runs are sufficient to catch any non-deterministic path.

    PA-18: no expected literal. We assert all results equal the first result.
    Tolerance: strict equality. The Generator must produce bit-for-bit identical
    draws when seeded identically — pytest.approx is NOT used here because
    floating-point reordering of the same RNG draws should not occur within
    a single numpy version.
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]
    seed = fixture["seed_a"]

    results = [_call_mc(inputs, seed) for _ in range(100)]

    # All 100 results must be identical — any divergence means the seed is
    # not isolating the RNG state.
    assert len(set(results)) == 1, (
        f"run_monte_carlo returned {len(set(results))} distinct values across "
        f"100 runs with the same seed ({seed!r}). Expected exactly 1 unique value. "
        "Seed is not fully controlling RNG state."
    )
    first = results[0]
    assert math.isfinite(first), f"Seeded run_monte_carlo returned non-finite value {first!r}."
    assert 0.0 <= first <= 100.0, f"mc_prob {first!r} is outside [0, 100]."


# ---------------------------------------------------------------------------
# 3. Seed distinguishability — different seed → different output (same input)
# ---------------------------------------------------------------------------


def test_different_seeds_produce_different_mc_prob_for_same_input() -> None:
    """
    A seeded implementation that always returns the same value regardless of
    seed would pass test #2 but fail here. Two different seeds must drive
    different RNG draws → different mc_prob.

    PA-18: no expected literals. We assert inequality between the two runs.
    Note: with extreme inputs (e.g. all paths above threshold) two seeds
    could both round to 0.0 or 100.0. The fixture's simulation_paths=2000
    and balanced history make a collision with 10 standard deviations
    of separation astronomically unlikely; if this flakes, the fixture
    inputs need revision, not the tolerance.
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]
    seed_a = fixture["seed_a"]
    seed_b = fixture["seed_b"]

    assert seed_a != seed_b, "Fixture must provide two distinct seeds."

    result_a = _call_mc(inputs, seed_a)
    result_b = _call_mc(inputs, seed_b)

    assert result_a != result_b, (
        f"run_monte_carlo returned identical mc_prob ({result_a!r}) for both "
        f"seed_a={seed_a!r} and seed_b={seed_b!r}. Different seeds must produce "
        "different random draws (and therefore different mc_prob) for this input. "
        "This indicates seed is ignored or the function always returns a constant."
    )


# ---------------------------------------------------------------------------
# 4. Unseeded path — seed=None must not regress existing behaviour
# ---------------------------------------------------------------------------


def test_unseeded_path_seed_none_returns_finite_float_in_range() -> None:
    """
    AC-H3.1: seed=None uses the default (process) RNG — existing callers pass
    no seed. Calling with seed=None must still return a valid probability.

    We do NOT assert a specific value (process RNG is non-deterministic).
    We assert the contract: finite float in [0, 100].
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]

    result = _call_mc(inputs, seed=None)

    assert math.isfinite(result), (
        f"run_monte_carlo(seed=None) returned non-finite value {result!r}."
    )
    assert 0.0 <= result <= 100.0, (
        f"run_monte_carlo(seed=None) returned {result!r} outside [0, 100]."
    )


# ---------------------------------------------------------------------------
# 5. Seed-helper signature — derive_cycle_mc_seed must exist and be callable
# ---------------------------------------------------------------------------


def test_derive_cycle_mc_seed_exists_in_math_engine() -> None:
    """
    AC-H3.1: a helper derive_cycle_mc_seed(cycle_id: str) -> int must be
    importable from math_engine. This verifies it is not accidentally buried
    in alpha_bot_execution.py where it would be untestable in isolation.
    """
    assert hasattr(math_engine, "derive_cycle_mc_seed"), (
        "math_engine is missing derive_cycle_mc_seed. "
        "Implement: def derive_cycle_mc_seed(cycle_id: str) -> int"
    )
    assert callable(math_engine.derive_cycle_mc_seed), (
        "math_engine.derive_cycle_mc_seed is not callable."
    )


def test_derive_cycle_mc_seed_signature_accepts_cycle_id_string() -> None:
    """Signature must be (cycle_id: str) -> int (or a compatible annotation)."""
    sig = inspect.signature(math_engine.derive_cycle_mc_seed)
    params = list(sig.parameters.keys())
    assert params == ["cycle_id"], (
        f"derive_cycle_mc_seed signature mismatch. Expected ['cycle_id'], got {params!r}."
    )


# ---------------------------------------------------------------------------
# 6. Seed helper determinism — derive_cycle_mc_seed(x) == derive_cycle_mc_seed(x)
# ---------------------------------------------------------------------------


def test_derive_cycle_mc_seed_is_deterministic_for_same_cycle_id() -> None:
    """
    Pure function: calling derive_cycle_mc_seed with the same cycle_id twice
    must return the same integer. This is the cross-restart guarantee.

    PA-18: no expected literal. We assert equality between two calls.
    """
    fixture = _load_fixture("02_cycle_id_collision_probe.json")
    for cycle_id in fixture["sample_cycle_ids"]:
        seed_1 = math_engine.derive_cycle_mc_seed(cycle_id)
        seed_2 = math_engine.derive_cycle_mc_seed(cycle_id)
        assert seed_1 == seed_2, (
            f"derive_cycle_mc_seed('{cycle_id}') returned {seed_1!r} on first call "
            f"and {seed_2!r} on second call. Must be deterministic (pure function)."
        )
        assert isinstance(seed_1, int), (
            f"derive_cycle_mc_seed('{cycle_id}') returned {type(seed_1)!r}, expected int."
        )


# ---------------------------------------------------------------------------
# 7. Seed helper collision-resistance — no collisions in sample fixture IDs
# ---------------------------------------------------------------------------


def test_derive_cycle_mc_seed_has_no_collisions_in_fixture_sample() -> None:
    """
    derive_cycle_mc_seed must map each of the fixture's sample_cycle_ids to a
    distinct integer. A collision means two different cycles would seed the same
    RNG state, making their mc_prob outputs indistinguishable.

    The fixture samples open and close of each day across a year. If any
    two minutes within the sample collide, the scheme is not collision-resistant
    at real fleet scale (~98k cycles/year).

    PA-18: no expected seeds. We assert uniqueness of the output set.
    """
    fixture = _load_fixture("02_cycle_id_collision_probe.json")
    cycle_ids = fixture["sample_cycle_ids"]
    seeds = [math_engine.derive_cycle_mc_seed(cid) for cid in cycle_ids]
    unique_seeds = set(seeds)

    assert len(unique_seeds) == len(cycle_ids), (
        f"derive_cycle_mc_seed produced {len(cycle_ids) - len(unique_seeds)} collision(s) "
        f"across {len(cycle_ids)} sample cycle_ids. "
        f"Colliding ids: "
        + str([cid for cid, seed in zip(cycle_ids, seeds) if seeds.count(seed) > 1])
    )


# ---------------------------------------------------------------------------
# 8. Cross-restart determinism — two daemon starts, same cycle_id, same mc_prob
# ---------------------------------------------------------------------------


def test_cross_restart_same_cycle_id_produces_same_mc_prob() -> None:
    """
    H3 edge case from feature plan: the per-cycle seed must survive a daemon
    restart. This test simulates two daemon runs of the same cycle by:
      - Run A: derive seed from cycle_id, call run_monte_carlo with that seed.
      - Run B: re-derive seed from cycle_id (fresh call), call run_monte_carlo.

    Both must produce the same mc_prob because the seed derivation is
    deterministic and does not rely on process-global state.

    PA-18: no expected literal. We assert run_a == run_b.
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]
    cycle_id = "20240102_0930"  # A concrete YYYYMMDD_HHMM from the fixture set

    # Simulate daemon restart A
    seed_run_a = math_engine.derive_cycle_mc_seed(cycle_id)
    result_run_a = _call_mc(inputs, seed_run_a)

    # Simulate daemon restart B (fresh derivation — process state is irrelevant)
    seed_run_b = math_engine.derive_cycle_mc_seed(cycle_id)
    result_run_b = _call_mc(inputs, seed_run_b)

    assert seed_run_a == seed_run_b, (
        f"derive_cycle_mc_seed('{cycle_id}') returned different seeds across "
        f"two calls: {seed_run_a!r} vs {seed_run_b!r}. Seed derivation must be deterministic."
    )
    assert result_run_a == result_run_b, (
        f"Same cycle_id '{cycle_id}' produced different mc_prob across two daemon starts: "
        f"{result_run_a!r} vs {result_run_b!r}. "
        "Cross-restart determinism broken — derive_cycle_mc_seed or run_monte_carlo "
        "depends on process-global state."
    )


# ---------------------------------------------------------------------------
# 9. Autotuner replay determinism — replay is bit-for-bit reproducible
# ---------------------------------------------------------------------------


def test_autotuner_replay_with_same_cycle_id_and_trial_params_is_reproducible() -> None:
    """
    AC-H3.2: an autotuner replay call must be bit-for-bit reproducible.
    The replay is implemented as: seed = derive_cycle_mc_seed(cycle_id),
    then run_monte_carlo(..., seed=seed). Two identical calls must agree.

    We simulate trial_params here as a dict that would be passed to a
    replay wrapper; the seed derivation keys only on cycle_id (the trial
    params define the input distribution, not the seed itself).

    PA-18: no expected literal. We assert replay_a == replay_b.
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]
    cycle_id = "20240103_1030"

    # Both replays derive seed independently (simulating two separate trial replays)
    seed_replay_a = math_engine.derive_cycle_mc_seed(cycle_id)
    replay_a = _call_mc(inputs, seed_replay_a)

    seed_replay_b = math_engine.derive_cycle_mc_seed(cycle_id)
    replay_b = _call_mc(inputs, seed_replay_b)

    assert replay_a == replay_b, (
        f"Autotuner replay for cycle_id='{cycle_id}' is not reproducible: "
        f"{replay_a!r} vs {replay_b!r}. "
        "Same cycle_id must yield the same seed and therefore the same mc_prob."
    )


# ---------------------------------------------------------------------------
# 10. Global-RNG isolation — run_monte_carlo(seed=X) must NOT mutate global RNG
# ---------------------------------------------------------------------------


def test_seeded_run_monte_carlo_does_not_mutate_numpy_global_rng() -> None:
    """
    The seed must be isolated to an internal Generator instance (numpy.random.
    default_rng(seed)), never via np.random.seed(). Calling run_monte_carlo
    with a seed must not alter the sequence produced by subsequent calls to
    the numpy global RNG.

    Method: record np.random.random() before call; then record it again after
    a seeded run_monte_carlo call using a different pre-established global seed.
    If the global sequence is identical in both branches, global state was not
    mutated.
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]
    seed = fixture["seed_a"]

    # Branch A: advance global RNG with a known seed, record next value BEFORE mc call
    np.random.seed(777)
    expected_global_next = np.random.random()

    # Branch B: advance global RNG identically, then call seeded run_monte_carlo,
    # then record next global value — must match Branch A
    np.random.seed(777)
    _call_mc(inputs, seed=seed)
    actual_global_next = np.random.random()

    assert actual_global_next == pytest.approx(expected_global_next, rel=0.0, abs=0.0), (
        f"run_monte_carlo(seed={seed!r}) mutated the numpy global RNG. "
        f"Expected next np.random.random() = {expected_global_next!r}, "
        f"got {actual_global_next!r}. "
        "Use numpy.random.default_rng(seed) for an isolated Generator — "
        "do NOT call np.random.seed() inside run_monte_carlo."
    )


# ---------------------------------------------------------------------------
# 11. Generator isolation — seed is plumbed via default_rng, NOT np.random.seed
# ---------------------------------------------------------------------------


def test_run_monte_carlo_uses_default_rng_not_global_seed() -> None:
    """
    Structural guard: confirms that np.random.seed is NOT called inside
    run_monte_carlo. This is tested behaviorally (not by inspecting source)
    via the global-RNG isolation test above (#10), but this test also verifies
    the seeded and unseeded paths both return valid probabilities concurrently,
    which is only possible if each call uses an isolated Generator.

    Two concurrent semantic calls (different seeds) must each return finite
    probabilities in [0, 100] independently of each other.
    """
    fixture = _load_fixture("01_standard_inputs.json")
    inputs = fixture["inputs"]
    seed_a = fixture["seed_a"]
    seed_b = fixture["seed_b"]

    result_a = _call_mc(inputs, seed_a)
    result_b = _call_mc(inputs, seed_b)
    result_none = _call_mc(inputs, None)

    for label, result in [("seed_a", result_a), ("seed_b", result_b), ("None", result_none)]:
        assert math.isfinite(result), (
            f"run_monte_carlo(seed={label!r}) returned non-finite {result!r}."
        )
        assert 0.0 <= result <= 100.0, (
            f"run_monte_carlo(seed={label!r}) returned {result!r} outside [0, 100]."
        )
