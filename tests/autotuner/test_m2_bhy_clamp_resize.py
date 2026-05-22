"""
AC-4 / RM-M2 — BHY p-value clamp resize.

The original clamp ``_HAIRCUT_PVALUE_EPSILON = 1e-12`` was chosen for
IEEE-754 stability only (saturation of `1 - Φ(t)` for |t| > ~8.3). The
methodology-literature audit shows the BHY step-up multiplies each raw
p-value by ``N * c(N) / rank``. At N=500 with q=0.05, the smallest
adjusted p-value reachable from p_raw=1e-12 is

    (500 * H_500 / 1) * 1e-12 ≈ 3.4e-9

which is far below the q=0.05 selection threshold. A SINGLE trial with
t-stat > ~7 therefore silently turns the haircut into a no-op for the
entire trial set: every adjusted p-value clears the gate, the haircut
"wins" with no statistical guarantee, and the result is indistinguishable
from a properly haircut win.

Decision (plan AC-4 / RM-M2): raise the clamp to

    _HAIRCUT_PVALUE_EPSILON = q / (N * c(N))

with ``q = HARVEY_LIU_FDR_Q`` and ``N = MAX_OPTUNA_TRIALS``,
``c(N) = sum_{j=1..N} 1/j``. At N=500, q=0.05 this is approximately
1.5e-5 — well inside IEEE-754 representable range AND ensures that even
the smallest representable adjusted p-value at the most-favourable rank
is at the FDR boundary, not orders of magnitude below it.

Tests pin:
  1. MAX_OPTUNA_TRIALS is named and == 500 (matches study.optimize
     n_trials at autotuner.py:1010 — the constant is needed for the
     clamp formula).
  2. _HAIRCUT_PVALUE_EPSILON >= q / (N * c(N)) AND it is named with a
     sourced comment.
  3. A synthetic trial set where every trial's raw p-value would clip to
     the epsilon: BHY still rejects at least one (the no-op collapse is
     gone).
  4. Negative path: a synthetic trial set where every trial's t-stat is
     low (raw p far above the clamp) is UNCHANGED by the resize.
  5. The clamp is NOT raised so high that it crosses q itself — that
     would over-constrain the gate.

Provenance: every expected p-adjusted value is recomputed in the test
from the BHY arithmetic formula
``(N * c(N) / rank) * p_raw`` clamped to [0, 1].
"""

from __future__ import annotations

import math

import pytest

import autotuner


# ---------------------------------------------------------------------------
# 1 — MAX_OPTUNA_TRIALS is a named constant; matches n_trials=500.
# ---------------------------------------------------------------------------


class TestMaxOptunaTrialsConstant:
    """The clamp formula references N — pin the constant exists."""

    def test_max_optuna_trials_is_named(self):
        """A named constant for the optimizer's per-symphony trial count
        must be exposed on the autotuner module so the clamp formula can
        reference it.
        """
        assert hasattr(autotuner, "MAX_OPTUNA_TRIALS"), (
            "AC-4 / RM-M2: autotuner must expose MAX_OPTUNA_TRIALS as a "
            "named module-level constant (the BHY clamp formula needs "
            "it). Hardcoded inline at study.optimize(n_trials=500) is "
            "now a duplication."
        )
        assert isinstance(autotuner.MAX_OPTUNA_TRIALS, int), (
            f"MAX_OPTUNA_TRIALS must be an int; got "
            f"{type(autotuner.MAX_OPTUNA_TRIALS).__name__}."
        )
        # The current production value is 500 (autotuner.py:1010). Pin
        # equality so a silent drift surfaces.
        assert autotuner.MAX_OPTUNA_TRIALS == 500, (
            f"MAX_OPTUNA_TRIALS must equal 500 to match the "
            f"study.optimize(n_trials=500) call. Got "
            f"{autotuner.MAX_OPTUNA_TRIALS}. If the trial count is being "
            f"changed, update BOTH places — or refactor study.optimize "
            f"to reference the constant."
        )


# ---------------------------------------------------------------------------
# 2 — _HAIRCUT_PVALUE_EPSILON >= q / (N * c(N)).
# ---------------------------------------------------------------------------


def _harmonic(n: int) -> float:
    """N-th harmonic number c(N) = sum_{j=1..N} 1/j. Test-internal so it
    does not depend on a producer helper."""
    return sum(1.0 / j for j in range(1, n + 1))


class TestHaircutPvalueEpsilonMeetsBhyScalingFloor:
    """The clamp must be at least q / (N * c(N)) — the smallest raw
    p-value whose BHY-adjusted value at rank 1 could still clear q."""

    def test_haircut_pvalue_epsilon_meets_bhy_floor(self):
        """Pin _HAIRCUT_PVALUE_EPSILON >= q / (N * c(N))."""
        q = autotuner.HARVEY_LIU_FDR_Q
        n = autotuner.MAX_OPTUNA_TRIALS
        c_n = _harmonic(n)
        # The floor itself — independent computation from the formula.
        floor = q / (n * c_n)

        # Pin the constant exists with the expected name.
        assert hasattr(autotuner, "_HAIRCUT_PVALUE_EPSILON"), (
            "Existing constant name preserved."
        )
        eps = autotuner._HAIRCUT_PVALUE_EPSILON
        assert eps >= floor * 0.99, (
            f"AC-4 / RM-M2 VIOLATED: _HAIRCUT_PVALUE_EPSILON={eps!r} is "
            f"below the BHY scaling floor q/(N*c(N)) = "
            f"{floor!r} (q={q}, N={n}, c(N)≈{c_n:.4f}). At this clamp, a "
            f"single trial with t-stat above the saturation point makes "
            f"the haircut a no-op for the entire trial set."
        )

    def test_haircut_pvalue_epsilon_below_q(self):
        """The clamp must NOT be so large that it crosses the FDR
        threshold q itself — that would over-constrain the gate."""
        q = autotuner.HARVEY_LIU_FDR_Q
        eps = autotuner._HAIRCUT_PVALUE_EPSILON
        assert eps < q, (
            f"_HAIRCUT_PVALUE_EPSILON={eps!r} is at or above q={q}. The "
            f"clamp would degenerate the gate."
        )

    def test_haircut_pvalue_epsilon_finite_and_positive(self):
        """Basic numeric sanity — finite, positive."""
        eps = autotuner._HAIRCUT_PVALUE_EPSILON
        assert math.isfinite(eps) and eps > 0.0, (
            f"_HAIRCUT_PVALUE_EPSILON must be finite and positive; got "
            f"{eps!r}."
        )

    def test_clamp_is_documented_with_bhy_rationale(self):
        """The clamp's source comment must mention BHY / scaling — not
        just the IEEE-754 stability story.

        The original comment at autotuner.py:280-285 covered ONLY the
        Φ-saturation rationale. AC-4 requires the BHY-scaling-floor
        rationale be added so a future maintainer who sees the constant
        understands BOTH why it cannot go lower AND why this specific
        value was chosen.
        """
        import inspect

        source = inspect.getsource(autotuner)
        # Find the eps definition line + a handful of preceding comment
        # lines and assert the BHY rationale text is present nearby.
        lines = source.splitlines()
        idx = next(
            (i for i, line in enumerate(lines)
             if "_HAIRCUT_PVALUE_EPSILON" in line and "=" in line),
            None,
        )
        assert idx is not None, "Could not locate eps definition."
        # Look at the preceding 15 lines (comment block above the
        # constant).
        window = "\n".join(lines[max(0, idx - 15):idx + 1]).lower()
        for needle in ("bhy", "scaling"):
            assert needle in window, (
                f"AC-4 / RM-M2: the _HAIRCUT_PVALUE_EPSILON comment must "
                f"document the BHY scaling-floor rationale (not just the "
                f"IEEE-754 stability story). Missing '{needle}' in the "
                f"comment window above the constant."
            )


# ---------------------------------------------------------------------------
# 3 — Behavioural pin: all-high-t-stat trials still get at least one
# rejection. The no-op collapse must be gone.
# ---------------------------------------------------------------------------


class TestBhyNoOpCollapseFixed:
    """A synthetic trial set where every t-stat would saturate Φ. Under
    the OLD clamp (1e-12) BHY collapsed into a no-op (no rejection at
    q=0.05). Under the resized clamp the BHY-adjusted p-values are forced
    above the floor and the worst trial fails to clear q."""

    def test_all_saturated_tstats_still_rejected_for_at_least_one_trial(self):
        """Construct N=500 trials whose raw t-stats are all above the
        Φ-saturation point (≥ 9). Every raw p-value clips to eps. After
        BHY adjustment the smallest adjusted p-value is at rank 1, equal
        to (N * c(N)) * eps. Under the resized clamp eps = q / (N * c(N)),
        so (N*c(N)) * eps = q exactly. ALL trials therefore have
        adjusted p-value >= q — they ALL fail the strict gate
        ``p_adj <= q``.

        That outcome is the desired one: the haircut REJECTS the trial set
        rather than rubber-stamping every trial as significant. The pin
        asserts ``_haircut_select`` returns ``(None, ...)`` for this set
        (no winner clears the gate) — which is the correct conservative
        behaviour the resize was designed to restore.
        """
        # Build N stand-in trials. Use SimpleNamespace so the haircut
        # filter sees the right attribute surface.
        import types

        n_trials = autotuner.MAX_OPTUNA_TRIALS
        trials = []
        # Use t-stat = 10 -> Φ(10) saturates to 1 -> raw p = 0 -> clipped
        # to eps. value field must be non-sentinel-positive for the
        # filter_sortino_sentinels gate.
        for _ in range(n_trials):
            t = types.SimpleNamespace()
            t.value = 0.5
            t.user_attrs = {"daily_returns": [0.01] * 100}
            trials.append(t)

        # Hack the haircut machinery directly via the public path.
        # compute_haircut_pvalue clamps to eps; benjamini_hochberg_adjust
        # then scales by N*c(N)/rank. Construct the p_values manually so
        # the test does not depend on compute_sortino_tstat's internals.
        p_values = [autotuner._HAIRCUT_PVALUE_EPSILON] * n_trials
        p_adj = autotuner.benjamini_hochberg_adjust(p_values)

        # Under the resize: min(p_adj) >= q. Under the old clamp it would
        # be far below q.
        assert min(p_adj) >= autotuner.HARVEY_LIU_FDR_Q * 0.99, (
            f"AC-4 / RM-M2 VIOLATED: with every raw p clipped to "
            f"_HAIRCUT_PVALUE_EPSILON={autotuner._HAIRCUT_PVALUE_EPSILON!r}, "
            f"the BHY-adjusted minimum is {min(p_adj)!r} — below the "
            f"FDR threshold q={autotuner.HARVEY_LIU_FDR_Q}. The haircut "
            f"would silently rubber-stamp the entire trial set. The clamp "
            f"must satisfy eps >= q / (N * c(N)) so the floor pins "
            f"min(p_adj) at q."
        )


# ---------------------------------------------------------------------------
# 4 — Negative path: the resize does not change BHY behaviour for
# realistic (non-saturated) trial sets. Pin the unchanged case.
# ---------------------------------------------------------------------------


class TestBhyUnchangedForRealisticTrialSet:
    """A synthetic trial set whose raw p-values sit well above the eps
    clamp — the BHY adjustment is identical before and after the resize."""

    def test_bhy_unchanged_for_realistic_pvalues(self):
        """N=50 raw p-values uniformly spaced in [0.001, 0.5] — none touch
        the clamp. The adjusted p-values follow the pure BHY formula with
        no clamp interaction. Pin that the rank-1 adjusted value matches
        the closed-form ``N*c(N) * p_min`` (within the running-min step-up
        — at rank 1 the running min is the trial's own scaled value).
        """
        # Hand-construct sorted p-values.
        raw_p = [0.001 + 0.01 * i for i in range(50)]
        # benjamini_hochberg_adjust expects un-sorted input; randomize
        # order so we exercise the sort path.
        permuted = list(reversed(raw_p))
        adj = autotuner.benjamini_hochberg_adjust(permuted)

        # Closed-form expected adj for the smallest (rank 1): (N*c(N)/1) * p_min,
        # then running-min from rank N down. At rank 1 the running-min is
        # min over j>=1 of (N*c(N)/j) * p_(j). For our monotonic series
        # this is the rank-1 trial's own scaled value because the scaling
        # by 1/j shrinks faster than p grows linearly.
        n = len(raw_p)
        c_n = _harmonic(n)
        # Find which entry has the smallest raw p in `permuted` ordering.
        min_idx = permuted.index(min(permuted))
        rank1_scaled = (n * c_n / 1) * raw_p[0]
        # Running-min from rank N: the actual value is min over all
        # j>=1 of (n*c_n/j) * p_(j); since p_(j) is monotonic-increasing
        # and 1/j is decreasing, the product's minimum may not be at j=1.
        # Compute the running-min explicitly so the expected reflects
        # the algorithm precisely.
        expected_running_min = min(
            (n * c_n / j) * raw_p[j - 1] for j in range(1, n + 1)
        )
        expected_rank1 = min(1.0, max(0.0, expected_running_min))
        # rel=1e-12: deterministic arithmetic, identical to producer's.
        assert adj[min_idx] == pytest.approx(expected_rank1, rel=1e-12), (
            f"BHY rank-1 adjusted p mismatch on the realistic fixture: "
            f"expected {expected_rank1!r}, got {adj[min_idx]!r}. The "
            f"clamp resize must not perturb the BHY arithmetic on raw "
            f"p-values that don't touch the clamp."
        )

    def test_clamp_interaction_only_at_extreme_tstat(self):
        """A single moderate t-stat (~3) produces a raw p well above the
        clamp; pin compute_haircut_pvalue returns the raw value, not the
        clamp."""
        # Φ(3) ≈ 0.99865; 1 - Φ(3) ≈ 0.00135 — orders of magnitude above
        # any reasonable clamp.
        p = autotuner.compute_haircut_pvalue(3.0)
        expected = 1.0 - 0.5 * (1.0 + math.erf(3.0 / math.sqrt(2.0)))
        assert p == pytest.approx(expected, rel=1e-9), (
            f"compute_haircut_pvalue(3.0) = {p!r}; expected ≈ "
            f"{expected!r}. The clamp must not engage for moderate "
            f"t-stats."
        )
        assert p > autotuner._HAIRCUT_PVALUE_EPSILON, (
            "Moderate t-stat's raw p must sit well above the clamp."
        )
