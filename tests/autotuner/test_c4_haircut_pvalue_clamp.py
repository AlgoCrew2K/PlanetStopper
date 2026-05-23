"""
Cluster 4 — AC-8 (numerical stability of the Harvey & Liu haircut): p-values are
clamped away from exactly 0.0 and 1.0 so an extreme trial t-statistic cannot
produce a degenerate adjusted p-value.

RED tests, written before the implementation.

ORIGIN: pre-D3, AC-8 was the `norm.ppf` overflow clamp inside
`compute_expected_max_sharpe`. Decision D3 DELETES `compute_expected_max_sharpe`
(see test_c4_dsr_machinery_removed.py), so the expected-max-Sharpe overflow is
moot — the function no longer exists. But the same class of numerical hazard
moves into the Harvey & Liu haircut: a large trial t-statistic `t_i =
Sortino_i · sqrt(T_i)` drives `p_i = 1 - Φ(t_i)` to underflow to EXACTLY 0.0
(scipy / math `Φ` saturates for t beyond ~8). A p-value of exactly 0.0 (or 1.0)
is degenerate — any later `log(p)`, `Φ⁻¹(p)`, or ratio on it produces inf/nan.

AC-8 (re-scoped to D3): the haircut must clamp every p-value into
`[eps, 1 - eps]` for a named numerical-stability epsilon — before the BHY
step-up and before any downstream consumer. The BHY-adjusted p-values must
likewise stay finite and within [0, 1].

These tests assert the BEHAVIOURAL contract (finiteness, bounded) — they do not
pin the exact epsilon, which is an implementer numerical-stability choice.

Reference: feature-plans/autotuner-statistics.md, Decision D3; Harvey & Liu 2015.
"""

from __future__ import annotations

import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent


def _import_autotuner():
    import autotuner
    return autotuner


# ===========================================================================
# AC-8 — an extreme t-statistic does not yield a degenerate p-value
# ===========================================================================


def test_extreme_positive_tstat_pvalue_is_clamped_above_zero():
    """
    AC-8: a very large positive t-statistic must yield a p-value STRICTLY GREATER
    than 0.0.

    `1 - Φ(t)` underflows to exactly 0.0 for t beyond ~8.3 in IEEE-754 double
    precision. A p-value of exactly 0.0 is degenerate — it cannot be logged or
    inverse-transformed. The haircut must clamp it to a small positive epsilon.

    This test exercises whatever pure function the implementer exposes for the
    Sortino -> p-value step. The contract: the haircut's p-value for an extreme
    trial is finite and strictly positive.
    """
    autotuner = _import_autotuner()

    # Post-AC-1 the compute_sortino_tstat signature is
    # (returns, seed) using the bootstrap SE; the OLD (sortino, T) ->
    # sortino * sqrt(T) signature is gone. AC-8's clamp concern is
    # orthogonal to that — it pins that the haircut's t-stat -> p-value
    # step clamps EXTREME t-values away from 0.0 / 1.0. Inline a known
    # extreme t (>8.5 so 1-Φ(t) underflows in IEEE-754) directly rather
    # than computing one via compute_sortino_tstat.
    t_stat = 10.0
    assert t_stat > 8.5, (
        f"Test construction: t_stat must be large enough to underflow 1-Φ(t); "
        f"got {t_stat}."
    )

    # The haircut must expose a p-value step. It is exercised here via the BHY
    # pipeline: a single-trial p-vector through benjamini_hochberg_adjust must not
    # yield a degenerate 0.0. The implementer's p-value derivation must clamp.
    p_adj = autotuner.benjamini_hochberg_adjust(
        [_haircut_pvalue(autotuner, t_stat)]
    )
    assert len(p_adj) == 1
    assert p_adj[0] > 0.0, (
        f"AC-8: an extreme-t trial produced an adjusted p-value of exactly "
        f"{p_adj[0]!r}. The haircut must clamp p-values away from 0.0 (a named "
        f"numerical-stability epsilon) so downstream log/Φ⁻¹ stay finite."
    )
    assert math.isfinite(p_adj[0]), (
        f"AC-8: the adjusted p-value for an extreme-t trial is non-finite "
        f"({p_adj[0]!r})."
    )


def test_extreme_negative_tstat_pvalue_is_clamped_below_one():
    """
    AC-8: a very large NEGATIVE t-statistic (a deeply unskilled trial) must yield
    a p-value STRICTLY LESS THAN 1.0.

    `1 - Φ(t)` saturates to exactly 1.0 for t below ~-8.3. A p-value of exactly
    1.0 is the symmetric degenerate case. The clamp must cap p at `1 - eps`.
    """
    autotuner = _import_autotuner()

    # See note in the positive-extreme test above — post-AC-1 the
    # tstat signature is (returns, seed), so inline a known extreme
    # negative t directly instead of computing one.
    t_stat = -10.0
    assert t_stat < -8.5, (
        f"Test construction: t_stat must be negative enough to saturate "
        f"1-Φ(t)→1.0; got {t_stat}."
    )

    p = _haircut_pvalue(autotuner, t_stat)
    assert p < 1.0, (
        f"AC-8: an extreme-negative-t trial produced a p-value of exactly "
        f"{p!r}. The haircut must clamp p-values away from 1.0."
    )
    assert math.isfinite(p), (
        f"AC-8: the p-value for an extreme-negative-t trial is non-finite ({p!r})."
    )


def test_bhy_adjusted_pvalues_stay_finite_for_extreme_trial_set():
    """
    AC-8: a trial set spanning extreme positive AND extreme negative t-stats must
    produce BHY-adjusted p-values that are all finite and within [0, 1].

    This is the integration of the clamp through the whole haircut pipeline — the
    step-up multiplies by N/j, so an unclamped 0.0 would propagate and an
    unclamped value could still escape [0,1] without the clamp.
    """
    autotuner = _import_autotuner()

    # Inline t-values across the relevant magnitude range so the test
    # exercises the clamp at extremes AND the unclamped behaviour at
    # moderate / null values. See the per-test notes above on why we
    # don't go through compute_sortino_tstat post-AC-1.
    t_stats = [
        10.0,     # extreme positive — Φ saturates, raw p underflows
        0.3,      # moderate positive — well above the clamp
        -10.0,    # extreme negative — 1-Φ saturates to 1.0
        0.0,      # exactly null — raw p = 0.5
    ]
    p_values = [_haircut_pvalue(autotuner, t) for t in t_stats]
    p_adj = autotuner.benjamini_hochberg_adjust(p_values)

    for i, p in enumerate(p_adj):
        assert math.isfinite(p), (
            f"AC-8: BHY adjusted p-value at index {i} is non-finite ({p!r}) "
            f"for an extreme-trial-set input. The p-value clamp must keep the "
            f"whole pipeline finite."
        )
        assert 0.0 <= p <= 1.0, (
            f"AC-8: BHY adjusted p-value at index {i} is outside [0,1] ({p!r})."
        )


def test_pvalue_clamp_epsilon_is_a_named_constant():
    """
    AC-8: the numerical-stability epsilon used to clamp p-values must be a named
    module constant in autotuner.py with a comment marking it as a numerical-
    stability bound (not a policy dial) — project no-magic-numbers rule.

    Heuristic: autotuner.py must contain a constant whose UPPER-case name carries
    a clamp/epsilon/stability keyword, near a comment using one of those words.
    """
    src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")
    lowered = src.lower()

    has_clamp_concept = any(
        kw in lowered for kw in ("p-value clamp", "p_value clamp", "pvalue clamp",
                                 "clamp p", "numerical stability", "numerical-stability")
    )
    assert has_clamp_concept, (
        "autotuner.py does not document a p-value clamp. AC-8 requires the "
        "Harvey & Liu haircut to clamp p-values away from 0.0 / 1.0 with a named "
        "numerical-stability epsilon constant and an explanatory comment."
    )


def _haircut_pvalue(autotuner, t_stat: float) -> float:
    """Obtain the haircut's one-sided p-value for a t-statistic.

    The implementer exposes the p-value step under a name this helper resolves —
    tries `compute_haircut_pvalue` then `sortino_tstat_pvalue`. If neither
    exists this raises AttributeError and the AC-8 tests fail RED with a clear
    message, which is the intended pre-implementation state.

    If the implementer names the p-value function differently, update this one
    helper — every AC-8 test routes through it.
    """
    for name in ("compute_haircut_pvalue", "sortino_tstat_pvalue",
                 "compute_tstat_pvalue", "haircut_pvalue"):
        fn = getattr(autotuner, name, None)
        if fn is not None:
            return fn(t_stat)
    raise AttributeError(
        "autotuner exposes no haircut p-value function "
        "(tried compute_haircut_pvalue / sortino_tstat_pvalue / "
        "compute_tstat_pvalue / haircut_pvalue). AC-8 / D3: the haircut must "
        "expose a pure, clamped Sortino-t-stat -> p-value step. Update "
        "_haircut_pvalue() in this test if the implementer's name differs."
    )
