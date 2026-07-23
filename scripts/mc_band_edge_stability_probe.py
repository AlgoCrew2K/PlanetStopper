"""R3-a deliverable (b) — 300-path band-edge arm-decision stability probe.

Pre-retune checklist item (b) (DECISIONS.md a/b; feature-plans/math-r3a-checklist.md):
quantify how stable the REPLAY Monte-Carlo estimator's arm decision is at its
current path count (``synthetic_history._MC_REPLAY_SIMULATION_PATHS``, 300 as of
R3-a) near an arm-band boundary, versus higher reference path counts, and emit a
committed bump-vs-accept recommendation — BEFORE the R3-d retune leans on that
estimator.

THE MEASURAND (AC-6): the live engine arms a protective trailing stop when
    acc_TAKE_PROFIT_MC_PCT <= prob_underperforming < acc_TRIGGER_THRESHOLD_PCT
(alpha_bot_execution.py:1373-1381). ``math_engine.run_monte_carlo`` estimates
prob_underperforming as (paths_at_or_above / paths) * 100 — a Binomial/paths
estimate whose sampling error shrinks with path count. Near a band BOUNDARY, a
300-path estimate can land on the opposite side of the boundary from a
higher-path reference, flipping the arm decision. This probe measures that
disagreement rate (the "flip-rate"), honestly framed as "300 vs reference-N
disagreement" — not "300 vs unknowable truth" (plan Edge Cases) — even though,
for THIS synthetic scenario, the true probability is exactly known by
construction (see FlipRateResult.p_true_estimate); the flip metric itself is
still measured against the noisy reference estimator, matching how a real
historical-data scenario (true probability unknown) would be assessed.

SCOPE GUARD (AC-8/AC-10): this module NEVER imports ``alpha_bot_execution``
(the live-execution module) — the arm-band boundary values below are mirrored
constants, not an import, so this probe stays off the live-execution import
graph. The only code change R3-a may make elsewhere is the single replay/
analysis constant ``synthetic_history._MC_REPLAY_SIMULATION_PATHS``, and only
if this probe recommends a bump — see ``SANCTIONED_KNOB`` and ``run_probe``.
The live engine's ``alpha_bot_execution.SIMULATION_PATHS`` (5000) is never
touched.

Off-execution-path: no live API, no DB, no network, no blocking I/O beyond the
two committed artifact files this module writes on request.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import numpy as np

import math_engine
import synthetic_history

# ---------------------------------------------------------------------------
# Arm-band boundary + focal path count.
#
# _LOWER_ARM_BOUNDARY_PCT mirrors alpha_bot_execution.TAKE_PROFIT_MC_PCT's
# default (alpha_bot_execution.py:90, default env value "5.0") — mirrored, NOT
# imported, per the scope guard above. If that live default is ever retuned,
# re-run this probe against the new value.
#
# _PROBE_FOCAL_PATH_COUNT is sourced FROM synthetic_history (not duplicated as
# a second magic number) so this probe always evaluates whatever the replay's
# CURRENT path count actually is — the thing under test.
# ---------------------------------------------------------------------------
_LOWER_ARM_BOUNDARY_PCT: float = 5.0
_PROBE_FOCAL_PATH_COUNT: int = synthetic_history._MC_REPLAY_SIMULATION_PATHS

# alpha_bot_execution.SIMULATION_PATHS's default (alpha_bot_execution.py:106) —
# mirrored, NOT imported (scope guard). This is the "live-fidelity" reference:
# the headline recommendation is driven by disagreement against THIS count.
_PRODUCTION_PARITY_PATHS: int = 5000

# ---------------------------------------------------------------------------
# Synthetic kNN-pool fixture — closed-form true probability by construction.
#
# _POOL_SIZE candidate days + the early-window days run_monte_carlo excludes
# from its kNN candidate pool (MC_VOL_WINDOW_DAYS - 1 = 19). neighbor_k is set
# to _POOL_SIZE so run_monte_carlo's "len(distances) <= neighbor_k" branch
# selects EVERY candidate day unconditionally (np.arange, not argpartition) —
# sidesteps SPY-return/vol-based nearest-neighbor selection entirely, so the
# resampling pool is exactly the _POOL_SIZE values placed below, independent
# of the (deliberately flat) SPY inputs. Since run_monte_carlo bootstraps this
# FIXED, KNOWN pool uniformly with replacement, the true limiting probability
# (infinite paths) is exactly the pool's own empirical fraction at-or-above the
# threshold — closed-form, no simulation needed to know it (used only as the
# informational .p_true_estimate; the flip-rate itself is still measured
# against the noisy reference estimator per the module docstring).
# ---------------------------------------------------------------------------
_POOL_SIZE: int = 1000  # resolution of target_true_prob_pct placement = 100/_POOL_SIZE = 0.1pp
_EARLY_WINDOW_EXCLUDED_DAYS: int = math_engine.MC_VOL_WINDOW_DAYS - 1  # 19
_POOL_RETURN_SPREAD_PCT: float = 8.0  # arbitrary symmetric daily-return spread (+/- 8pp)

# Evidence-based bump-target search ladder (PM ruling on the R3-a plan): if a
# bump is recommended, search ascending for the SMALLEST path count strictly
# above the current focal count whose OWN flip-rate vs _PRODUCTION_PARITY_PATHS
# is proven stable. Never bump to an unmeasured value.
_BUMP_CANDIDATE_LADDER: tuple[int, ...] = (
    400,
    500,
    600,
    750,
    1000,
    1250,
    1500,
    2000,
    2500,
    3000,
    4000,
    5000,
)

RECOMMENDATION_THRESHOLD: float = 0.05
# [PM-ASSUMED] bump line (feature-plans/math-r3a-checklist.md Decisions table):
# a >=5% arm-decision disagreement near the edge is a defensible "materially
# unstable" line for a live-money arm decision.

DEFAULT_REFERENCE_COUNTS: tuple[int, ...] = (1000, _PRODUCTION_PARITY_PATHS, 20000)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACT_MD_PATH = _REPO_ROOT / "docs" / "generated" / "mc-band-edge-stability.md"
ARTIFACT_JSON_PATH = _REPO_ROOT / "docs" / "generated" / "mc-band-edge-stability.json"

SANCTIONED_KNOB: str = "synthetic_history._MC_REPLAY_SIMULATION_PATHS"

_HEADLINE_NEAR_EDGE_OFFSET_PCT: float = (
    0.3  # matches the near-edge stress offset the RED non-vacuity crux validates
)
_HEADLINE_BOUNDARY_PCT: float = _LOWER_ARM_BOUNDARY_PCT
_HEADLINE_TARGET_TRUE_PROB_PCT: float = _HEADLINE_BOUNDARY_PCT + _HEADLINE_NEAR_EDGE_OFFSET_PCT
# Bounded/cheap (plan AC-5) but enough for a reasonably tight point estimate:
# binomial std ~= sqrt(p(1-p)/n_seeds) ~= 1.2-1.7pp near p in [0.05, 0.15] at
# n_seeds=300 — reported alongside the point estimate is out of scope for this
# module (the artifact reports the point estimate + candidate ladder; CI math
# is left to a human reviewer per the plan's honest-framing requirement).
_ARTIFACT_N_SEEDS: int = 300
_ARTIFACT_BASE_SEED: int = 20260718  # fixed, arbitrary — the R3-a authorship date


@dataclasses.dataclass(frozen=True)
class FlipRateResult:
    """Result of ``measure_flip_rate`` for one fixed synthetic scenario.

    flip_rate_by_reference: reference path count -> fraction in [0, 1] of the
        n_seeds independent 300-path draws whose side of ``boundary_pct``
        disagrees with an independently-drawn reference-N estimate of the same
        underlying scenario.
    n300_paths: the focal (under-test) path count — synthetic_history's
        current ``_MC_REPLAY_SIMULATION_PATHS``.
    p_true_estimate: the exact closed-form true probability of this synthetic
        scenario (see the pool-fixture note above) — informational only, not
        used to compute the flip-rate.
    """

    flip_rate_by_reference: dict[int, float]
    n300_paths: int
    p_true_estimate: float


def _build_band_edge_fixture(
    target_true_prob_pct: float,
) -> tuple[list[dict], dict[str, dict], float, int, float]:
    """Build a synthetic single-ticker kNN fixture whose true underperformance
    probability is, as closely as the pool resolution allows, ``target_true_prob_pct``.

    Returns ``(holdings, historical_data, spy_today_return, neighbor_k, p_true)``.
    See the module-level pool-fixture note for the closed-form-truth design.
    """
    n = _POOL_SIZE
    pool_pct = np.linspace(-_POOL_RETURN_SPREAD_PCT, _POOL_RETURN_SPREAD_PCT, n)  # ascending

    n_at_or_above = int(round(n * target_true_prob_pct / 100.0))
    n_at_or_above = max(1, min(n - 1, n_at_or_above))
    lower_idx = n - n_at_or_above - 1
    upper_idx = n - n_at_or_above
    threshold_pct = float((pool_pct[lower_idx] + pool_pct[upper_idx]) / 2.0)
    p_true = n_at_or_above / n * 100.0

    total_days = n + _EARLY_WINDOW_EXCLUDED_DAYS
    historical_data: dict[str, dict] = {}
    for i in range(total_days):
        date = f"probe-day-{i:05d}"
        if i < _EARLY_WINDOW_EXCLUDED_DAYS:
            ticker_ret = 0.0  # excluded from run_monte_carlo's candidate pool — value irrelevant
        else:
            ticker_ret = float(pool_pct[i - _EARLY_WINDOW_EXCLUDED_DAYS]) / 100.0
        historical_data[date] = {
            "PROBE": {"daily_ret": ticker_ret},
            "SPY": {"daily_ret": 0.0},
        }

    holdings = [
        {"ticker": "PROBE", "allocation": 1.0, "last_percent_change": threshold_pct / 100.0}
    ]
    spy_today_return = 0.0
    neighbor_k = n

    return holdings, historical_data, spy_today_return, neighbor_k, p_true


def _flip_rate_between(
    *,
    holdings: list[dict],
    historical_data: dict[str, dict],
    spy_today_return: float,
    neighbor_k: int,
    boundary_pct: float,
    n_seeds: int,
    count_a: int,
    count_b: int,
    base_seed: int,
) -> float:
    """Pairwise flip-rate between two independently-drawn path counts on the
    SAME fixed scenario: fraction of ``n_seeds`` seed-indices where a
    count_a-path estimate's side of ``boundary_pct`` disagrees with an
    independent count_b-path estimate's side.

    Each draw is independently seeded by (base_seed, slot, paths, seed_index)
    — the "slot" (a/b) keeps the two draws independent even when
    count_a == count_b (used by the bump-target search's production-parity
    self-comparison).
    """

    def _draw(paths: int, slot: str, seed_index: int) -> float | None:
        seed = math_engine.derive_cycle_mc_seed(
            f"mc_band_edge_probe_{base_seed}_{slot}_{paths}_{seed_index}"
        )
        # Module-qualified call (never aliased) — the seam a spy test patches.
        return math_engine.run_monte_carlo(
            holdings,
            historical_data,
            spy_today_return,
            simulation_paths=paths,
            neighbor_k=neighbor_k,
            seed=seed,
        )

    flips = 0
    valid = 0
    for i in range(n_seeds):
        prob_a = _draw(count_a, "a", i)
        prob_b = _draw(count_b, "b", i)
        # D-1: an unexpected insufficient-history sentinel is skipped, never
        # miscounted — shouldn't occur (the fixture always has _POOL_SIZE
        # eligible days, far above MC_MIN_HISTORY_DAYS), but this keeps the
        # probe honest if that invariant is ever violated.
        if prob_a is None or prob_b is None:
            continue
        valid += 1
        if (prob_a >= boundary_pct) != (prob_b >= boundary_pct):
            flips += 1

    return (flips / valid) if valid else 0.0


def measure_flip_rate(
    *,
    target_true_prob_pct: float,
    boundary_pct: float,
    n_seeds: int,
    reference_counts: tuple[int, ...],
    base_seed: int,
) -> FlipRateResult:
    """Measure the replay's focal-path-count (300) arm-decision flip-rate near
    a single arm-band boundary, against each of ``reference_counts``.

    Deterministic: identical arguments produce byte-identical
    ``.flip_rate_by_reference`` (every seed derives from ``base_seed`` via
    ``math_engine.derive_cycle_mc_seed`` — no wall clock, no global RNG).
    """
    holdings, historical_data, spy_today_return, neighbor_k, p_true = _build_band_edge_fixture(
        target_true_prob_pct
    )

    flip_rate_by_reference = {
        int(ref): _flip_rate_between(
            holdings=holdings,
            historical_data=historical_data,
            spy_today_return=spy_today_return,
            neighbor_k=neighbor_k,
            boundary_pct=boundary_pct,
            n_seeds=n_seeds,
            count_a=_PROBE_FOCAL_PATH_COUNT,
            count_b=int(ref),
            base_seed=base_seed,
        )
        for ref in reference_counts
    }

    return FlipRateResult(
        flip_rate_by_reference=flip_rate_by_reference,
        n300_paths=_PROBE_FOCAL_PATH_COUNT,
        p_true_estimate=p_true,
    )


def recommend(flip_rate: float, *, threshold: float = RECOMMENDATION_THRESHOLD) -> str:
    """Pure threshold decision: ``"bump"`` if ``flip_rate >= threshold`` else ``"accept"``."""
    return "bump" if flip_rate >= threshold else "accept"


def _select_bump_target(
    *,
    target_true_prob_pct: float,
    boundary_pct: float,
    n_seeds: int,
    base_seed: int,
    threshold: float,
) -> dict:
    """Evidence-based bump-target search (PM ruling on the R3-a (b) plan).

    Search ``_BUMP_CANDIDATE_LADDER`` ascending and certify the SMALLEST
    candidate whose OWN flip-rate vs ``_PRODUCTION_PARITY_PATHS`` is below
    ``threshold``. Never returns an unmeasured value.

    If NO candidate up to and including production parity (5000) clears the
    bar, returns ``stable=False`` — an honest degenerate finding, not a forced
    number. This can genuinely happen: comparing 5000 against itself is still
    two INDEPENDENTLY drawn estimates, so even parity is not guaranteed stable
    when the true probability sits extremely close to the boundary relative to
    the pool's own p*(1-p) sampling variance.
    """
    holdings, historical_data, spy_today_return, neighbor_k, _p_true = _build_band_edge_fixture(
        target_true_prob_pct
    )

    candidates_tried: dict[int, float] = {}
    for candidate in _BUMP_CANDIDATE_LADDER:
        rate = _flip_rate_between(
            holdings=holdings,
            historical_data=historical_data,
            spy_today_return=spy_today_return,
            neighbor_k=neighbor_k,
            boundary_pct=boundary_pct,
            n_seeds=n_seeds,
            count_a=candidate,
            count_b=_PRODUCTION_PARITY_PATHS,
            base_seed=base_seed,
        )
        candidates_tried[candidate] = rate
        if rate < threshold:
            return {
                "stable": True,
                "target_path_count": candidate,
                "target_flip_rate_vs_5000": rate,
                "candidates_tried": candidates_tried,
            }

    return {
        "stable": False,
        "target_path_count": None,
        "target_flip_rate_vs_5000": candidates_tried.get(_PRODUCTION_PARITY_PATHS),
        "candidates_tried": candidates_tried,
    }


def _render_markdown(payload: dict) -> str:
    """Render the recommendation payload as a human-readable Markdown report."""
    lines = [
        "# MC Band-Edge Stability Probe",
        "",
        f"Boundary: {payload['boundary_pct']}% | "
        f"Near-edge true prob (closed-form): {payload['p_true_estimate']:.2f}% | "
        f"n_seeds: {payload['n_seeds']}",
        "",
        f"## Flip-rate: {payload['n300_paths']}-path replay vs each reference "
        f"(fraction of {payload['n_seeds']} independent seeded runs whose arm-side disagrees)",
        "",
        "| reference paths | flip-rate |",
        "|---|---|",
    ]
    for ref, rate in sorted(payload["flip_rate_by_reference"].items()):
        lines.append(f"| {ref} | {rate:.4f} |")
    lines += [
        "",
        f"**Decision threshold:** {payload['decision_threshold']:.2%} "
        "([PM-ASSUMED] — see feature-plans/math-r3a-checklist.md)",
        f"**Decision reference (production parity):** {payload['decision_reference_paths']} paths",
        f"**Recommendation:** {payload['recommendation'].upper()}",
        "",
    ]

    bump_target = payload.get("bump_target")
    if bump_target is not None:
        lines.append("## Evidence-based bump target")
        lines.append("")
        if bump_target["stable"]:
            tradeoff = payload["compute_tradeoff"]
            lines += [
                "Smallest path count proven stable "
                f"(<{payload['decision_threshold']:.0%} flip-rate vs "
                f"{payload['decision_reference_paths']}-path production parity): "
                f"**{bump_target['target_path_count']}**",
                "",
                f"- Measured flip-rate at that target: {bump_target['target_flip_rate_vs_5000']:.4f}",
                f"- Compute vs current {payload['n300_paths']}: "
                f"{tradeoff['target_vs_300_multiplier']:.2f}x",
                f"- Compute vs full {payload['decision_reference_paths']}-parity: "
                f"{tradeoff['target_vs_5000_multiplier']:.2f}x",
                f"- Sanctioned knob: `{payload['sanctioned_knob']}`",
            ]
        else:
            lines.append(
                "**No candidate path count up to and including production parity "
                f"({payload['decision_reference_paths']}) achieved a flip-rate below "
                f"{payload['decision_threshold']:.0%}** vs the parity reference itself. "
                "The instability at this offset is dominated by proximity to the "
                "boundary, not reducible by more paths alone. No constant change is "
                "made — see the candidates-tried table below."
            )
        lines.append("")
        lines.append("### Candidates tried (vs 5000-path production parity)")
        lines.append("")
        lines.append("| candidate paths | flip-rate vs 5000 |")
        lines.append("|---|---|")
        for candidate, rate in sorted(bump_target["candidates_tried"].items()):
            lines.append(f"| {candidate} | {rate:.4f} |")
        lines.append("")

    return "\n".join(lines)


def run_probe(*, md_path=ARTIFACT_MD_PATH, json_path=ARTIFACT_JSON_PATH) -> dict:
    """Run the headline near-edge scenario, decide bump/accept, and write both
    the human-readable Markdown artifact and the machine-readable JSON sidecar.

    Returns the SAME payload dict persisted to ``json_path`` — the returned
    summary and the committed artifact always agree on the verdict.
    """
    result = measure_flip_rate(
        target_true_prob_pct=_HEADLINE_TARGET_TRUE_PROB_PCT,
        boundary_pct=_HEADLINE_BOUNDARY_PCT,
        n_seeds=_ARTIFACT_N_SEEDS,
        reference_counts=DEFAULT_REFERENCE_COUNTS,
        base_seed=_ARTIFACT_BASE_SEED,
    )
    headline_flip_rate = result.flip_rate_by_reference[_PRODUCTION_PARITY_PATHS]
    verdict = recommend(headline_flip_rate, threshold=RECOMMENDATION_THRESHOLD)

    payload: dict = {
        "boundary_pct": _HEADLINE_BOUNDARY_PCT,
        "target_true_prob_pct": _HEADLINE_TARGET_TRUE_PROB_PCT,
        "p_true_estimate": result.p_true_estimate,
        "n300_paths": result.n300_paths,
        "n_seeds": _ARTIFACT_N_SEEDS,
        "flip_rate_by_reference": result.flip_rate_by_reference,
        "decision_threshold": RECOMMENDATION_THRESHOLD,
        "decision_reference_paths": _PRODUCTION_PARITY_PATHS,
        "recommendation": verdict,
        "sanctioned_knob": SANCTIONED_KNOB,
    }

    if verdict == "bump":
        bump_target = _select_bump_target(
            target_true_prob_pct=_HEADLINE_TARGET_TRUE_PROB_PCT,
            boundary_pct=_HEADLINE_BOUNDARY_PCT,
            n_seeds=_ARTIFACT_N_SEEDS,
            base_seed=_ARTIFACT_BASE_SEED,
            threshold=RECOMMENDATION_THRESHOLD,
        )
        payload["bump_target"] = bump_target
        if bump_target["stable"]:
            payload["compute_tradeoff"] = {
                "target_vs_300_multiplier": bump_target["target_path_count"] / result.n300_paths,
                "target_vs_5000_multiplier": bump_target["target_path_count"]
                / _PRODUCTION_PARITY_PATHS,
            }

    md_path = pathlib.Path(md_path)
    json_path = pathlib.Path(json_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")

    return payload


if __name__ == "__main__":
    run_probe()
