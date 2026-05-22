"""
AC-1 / RM-H1 — bootstrap Sortino SE t-statistic.

The Cluster 4 remediation kept Sortino as the haircut metric but constructed
the haircut t-statistic as `Sortino * sqrt(T)` — which is the Sharpe-specific
Wald scaling. Sortino's asymptotic standard error differs (downside deviation,
not full std — Bertrand & Prigent 2019). The resulting p-values are
mis-calibrated under the null and BHY's FDR guarantee fails.

Decision D10 (ruled by team-lead, post-audit-hardening plan): construct the
t-statistic as

    t = Sortino / SE_bootstrap

where ``SE_bootstrap`` is the standard deviation of the Sortino computed over
``BOOTSTRAP_SORTINO_RESAMPLES >= 1000`` independent bootstrap resamples of
the per-day return series (Efron 1979, "Bootstrap Methods: Another Look at
the Jackknife", Annals of Statistics 7(1), 1-26). The BHY step-up + clamp
machinery is unchanged — only the p-value-feeding t-statistic is rebuilt.

These tests pin:
  - the public API the haircut path consumes: ``compute_sortino_tstat`` MUST
    accept the return SERIES (not just the scalar Sortino), so the bootstrap
    has its source data;
  - the resample-count constant exists, is named, and is >= 1000 (Efron);
  - the bootstrap SE is independently reproducible from first principles for
    a fixed-seed ``random.Random`` injection — implementer and test share the
    standard nonparametric bootstrap (sample-with-replacement of length T per
    resample), so the test verifies the producer's output against an
    independent re-implementation of Efron 1979's SE construction, NOT against
    a self-pin;
  - the t-statistic equals ``Sortino / SE_bootstrap`` (NOT ``Sortino * sqrt(T)``),
    so a regression to the Sharpe-Wald scaling fails;
  - the degenerate guards: T <= 0, T == 1, constant series (SE == 0) all return
    a finite, well-defined value (no division-by-zero, no NaN propagation into
    the BHY step-up);
  - small-T (T < 5) calibration boundary: a documented fallback path the
    risk-engine-specialist owns the choice for. The test only pins THAT a
    fallback exists and that it does not return non-finite; the specific
    fallback value is implementer-chosen so this test does not over-constrain.

Provenance: every expected SE value is hand-computed in the test itself from
the Efron 1979 algorithm against a fixed-seed ``random.Random`` — never pinned
from a producer call. The implementer cannot make this test pass by emitting
a regression-pinned value; the bootstrap must genuinely run the same
algorithm.
"""

from __future__ import annotations

import math
import random

import pytest

import autotuner


# ---------------------------------------------------------------------------
# Reference bootstrap — the test's INDEPENDENT implementation of Efron 1979.
#
# The producer (autotuner.compute_sortino_tstat / its bootstrap helper) is
# expected to follow this same protocol: a standard nonparametric bootstrap
# (sample-with-replacement of length T per resample), compute Sortino on each
# resample, take the population standard deviation of the resampled Sortinos.
#
# Sharing the algorithmic protocol does NOT make this a self-pin: the test
# implements the algorithm from scratch from the Efron 1979 description, and
# the producer's bootstrap helper is verified to match. If the producer uses
# a different bootstrap variant (parametric, block, jackknife) or a different
# RNG family, this test fails — which is the intended adversarial guardrail.
# ---------------------------------------------------------------------------

# Match autotuner.SORTINO_TARGET_RETURN; redefined here so the test does NOT
# depend on a producer constant for the target value.
_REFERENCE_SORTINO_TARGET = 0.0


def _reference_sortino(returns: list[float]) -> float:
    """Sortino computed from first principles (no producer dependency).

    Same formula as the Sortino & van der Meer 1991 population-denominator form
    autotuner.compute_sortino_ratio implements, redefined here so a regression
    in compute_sortino_ratio cannot mask a bootstrap-SE defect.
    """
    if not returns:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    sum_sq_downside = sum(
        min(r - _REFERENCE_SORTINO_TARGET, 0.0) ** 2 for r in returns
    )
    mean_sq_downside = sum_sq_downside / n
    downside_deviation = math.sqrt(mean_sq_downside)
    if downside_deviation == 0.0:
        # The all-non-negative case — sentinel parity with autotuner's helper
        # so the reference Sortino matches what the producer's bootstrap will
        # see when it resamples an all-non-negative subset.
        return 1e6
    return mean_r / downside_deviation


def _reference_bootstrap_sortino_se(
    returns: list[float], n_resamples: int, rng: random.Random
) -> float:
    """Standard nonparametric bootstrap SE of the Sortino (Efron 1979).

    Algorithm: draw ``n_resamples`` independent samples-with-replacement of
    length ``T = len(returns)`` from ``returns``; compute Sortino on each;
    the bootstrap SE is the population standard deviation of the resampled
    Sortinos.

    The same ``random.Random`` instance is consumed by both this reference
    and the producer (the test injects the rng into both), so identical
    sequences of draws produce identical SE values.
    """
    T = len(returns)
    if T <= 1:
        return 0.0
    sortinos: list[float] = []
    for _ in range(n_resamples):
        resample = [returns[rng.randrange(T)] for _ in range(T)]
        sortinos.append(_reference_sortino(resample))
    mean_s = sum(sortinos) / n_resamples
    var_s = sum((s - mean_s) ** 2 for s in sortinos) / n_resamples
    return math.sqrt(var_s)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

# A short, hand-curated return series with mixed positive/negative days. T=12
# is intentionally small so the test runs fast while still exercising the
# downside-deviation branch (Sortino is non-trivial — three losing days drive
# the denominator).
_RETURN_SERIES_BASIC: tuple[float, ...] = (
    0.012, -0.005, 0.008, 0.003, -0.011, 0.006,
    0.001, 0.015, -0.002, 0.009, 0.004, -0.007,
)

# An all-non-negative series — Sortino's denominator is zero and the
# population helper returns the 1e6 sentinel. The bootstrap SE is also zero
# (every resample yields the same sentinel). Exercises the
# constant-resampled-Sortino branch.
_RETURN_SERIES_ALL_POSITIVE: tuple[float, ...] = (
    0.001, 0.002, 0.003, 0.004, 0.005, 0.006,
    0.007, 0.008, 0.009, 0.010, 0.011, 0.012,
)

# A larger series for the convergence-property test. Mixed signs, T=60.
_RETURN_SERIES_LARGE: tuple[float, ...] = tuple(
    (0.0008 * (i - 30) - 0.002 * (i % 3 - 1)) for i in range(60)
)


# ---------------------------------------------------------------------------
# 1 — Resample-count constant is named, sourced, and meets Efron's floor.
# ---------------------------------------------------------------------------


class TestBootstrapResampleCountConstant:
    """The resample count must be a named constant (no magic number) and meet
    Efron 1979's floor of 1000 — the boundary below which SE estimates are too
    noisy to be calibrating BHY against.
    """

    def test_bootstrap_resample_count_constant_is_exposed(self):
        """A named constant on the autotuner module must hold the resample count.

        The plan dictates a named constant with a sourced comment; the test
        accepts either of the two natural names so the implementer has one
        degree of freedom. If the implementer hard-codes the count inline, no
        named constant exists and this test fails.
        """
        candidate_names = (
            "BOOTSTRAP_SORTINO_RESAMPLES",
            "SORTINO_BOOTSTRAP_RESAMPLES",
        )
        assert any(hasattr(autotuner, n) for n in candidate_names), (
            "AC-1 / RM-H1: the bootstrap-SE resample count must be a named "
            "module-level constant on autotuner (one of "
            f"{candidate_names}). A magic number inline would violate the "
            "project's no-magic-numbers rule for math constants."
        )

    def test_bootstrap_resample_count_meets_efron_floor(self):
        """Efron 1979 prescribes >= 1000 bootstrap resamples for a stable SE.

        Below 1000 the SE estimator's own noise dominates the haircut
        decision — the BHY gate becomes a roll of the dice rather than a
        statistical control. The plan AC pins >= 1000.
        """
        candidate_names = (
            "BOOTSTRAP_SORTINO_RESAMPLES",
            "SORTINO_BOOTSTRAP_RESAMPLES",
        )
        resamples = next(
            (getattr(autotuner, n) for n in candidate_names if hasattr(autotuner, n)),
            None,
        )
        assert resamples is not None, "see preceding test for context"
        assert isinstance(resamples, int), (
            f"Resample count must be an int; got {type(resamples).__name__}."
        )
        assert resamples >= 1000, (
            f"AC-1 / RM-H1: bootstrap resample count must be >= 1000 (Efron "
            f"1979); got {resamples}. Below 1000 the SE estimator's noise "
            f"dominates the BHY haircut decision."
        )


# ---------------------------------------------------------------------------
# 2 — The bootstrap SE helper is exposed, deterministic under an injected rng,
# and matches an independent re-implementation of Efron 1979.
#
# This is the structural pin: the producer must expose a helper that takes a
# return series, a resample count, and an INJECTED rng. Test injects its own
# random.Random(seed); reference re-implements the algorithm from scratch;
# producer's output must match. A producer that ignores the rng or uses a
# different bootstrap variant fails here.
# ---------------------------------------------------------------------------


class TestBootstrapSortinoSeHelper:
    """The bootstrap SE must be exposed as an injectable helper so tests can
    pin it independently of the t-stat layer."""

    def test_bootstrap_sortino_se_helper_exists(self):
        """A pure helper ``_bootstrap_sortino_se(returns, n_resamples, rng)``
        (or its public-name twin) must be exposed on the autotuner module.

        Without an injectable helper, the bootstrap is opaque and no test
        can verify the SE construction independently of the t-stat layer.
        """
        candidate_names = (
            "_bootstrap_sortino_se",
            "bootstrap_sortino_se",
            "_compute_sortino_bootstrap_se",
        )
        assert any(hasattr(autotuner, n) for n in candidate_names), (
            "AC-1 / RM-H1: the bootstrap SE construction must be exposed as a "
            "named helper on autotuner (one of "
            f"{candidate_names}). The helper takes (returns, n_resamples, rng) "
            "so tests inject a deterministic random.Random and verify the SE "
            "against an independent reference implementation."
        )

    def _resolve_helper(self):
        for n in (
            "_bootstrap_sortino_se",
            "bootstrap_sortino_se",
            "_compute_sortino_bootstrap_se",
        ):
            if hasattr(autotuner, n):
                return getattr(autotuner, n)
        pytest.fail("bootstrap SE helper not exposed; see preceding test.")

    def test_bootstrap_sortino_se_matches_independent_efron_reference(self):
        """The producer's bootstrap SE matches an independent re-implementation
        of Efron 1979 for a fixed seed and fixed return series.

        Both the producer and the reference consume the SAME random.Random
        instance — but two SEPARATE instances seeded identically. The
        bootstrap protocol is standardised (sample-with-replacement of length
        T, take population stdev of the resampled Sortinos), so identical
        seeds + identical algorithms yield identical SEs.

        Tolerance: exact (rel=0). The bootstrap is deterministic under a
        seeded RNG; any drift signals a different algorithm. The test would be
        meaningless with a loose tolerance — a Wald-scaled SE happens to land
        in the same order of magnitude as the bootstrap SE for many series.
        """
        helper = self._resolve_helper()
        returns = list(_RETURN_SERIES_BASIC)

        # Independent reference: implement Efron 1979 from scratch.
        ref_rng = random.Random(20260522)
        expected_se = _reference_bootstrap_sortino_se(
            returns, n_resamples=1000, rng=ref_rng
        )

        # Producer: same seed, same protocol, separate rng instance.
        prod_rng = random.Random(20260522)
        actual_se = helper(returns, 1000, prod_rng)

        # rel=0 / abs=0: a bootstrap with identical seed + identical
        # algorithm yields bit-identical SEs. Any non-zero delta is a real
        # algorithmic divergence (different sampling, different aggregation,
        # different Sortino formula, etc.).
        assert actual_se == expected_se, (
            f"AC-1 / RM-H1: bootstrap SE mismatch on the basic fixture. "
            f"Expected (Efron 1979 reference, seed=20260522, "
            f"n_resamples=1000): {expected_se!r}; got: {actual_se!r}. "
            f"A non-zero delta means the producer's bootstrap uses a "
            f"different protocol than the standard nonparametric bootstrap."
        )

    def test_bootstrap_sortino_se_is_nonnegative_on_arbitrary_series(self):
        """Property: the bootstrap SE is the stdev of a sample — must be >= 0.

        Asserted across a small set of fixtures. A negative SE would be a
        sign-handling defect.
        """
        helper = self._resolve_helper()
        for label, returns in (
            ("basic", _RETURN_SERIES_BASIC),
            ("all_positive", _RETURN_SERIES_ALL_POSITIVE),
            ("large", _RETURN_SERIES_LARGE),
        ):
            rng = random.Random(7)
            se = helper(list(returns), 1000, rng)
            assert se >= 0.0, (
                f"Bootstrap SE must be >= 0 (it is a stdev). "
                f"Series '{label}' produced SE={se!r}."
            )
            assert math.isfinite(se), (
                f"Bootstrap SE must be finite. Series '{label}' produced "
                f"non-finite SE={se!r}."
            )

    def test_bootstrap_se_zero_for_all_positive_returns(self):
        """Edge case: when every return >= 0, Sortino is the 1e6 sentinel.

        Every bootstrap resample of an all-non-negative series is also
        all-non-negative, so every resampled Sortino is the sentinel and the
        stdev across resamples is zero.
        """
        helper = self._resolve_helper()
        rng = random.Random(11)
        se = helper(list(_RETURN_SERIES_ALL_POSITIVE), 1000, rng)
        # abs=0 because the resampled population is mathematically constant
        # (all 1e6 sentinels) — any non-zero SE indicates the helper diverged
        # from the population Sortino formula.
        assert se == pytest.approx(0.0, abs=0.0), (
            "All-non-negative series: every bootstrap resample is "
            "all-non-negative, so the Sortino is the sentinel on every "
            f"resample and the SE is exactly 0. Got SE={se!r}."
        )


# ---------------------------------------------------------------------------
# 3 — compute_sortino_tstat consumes the SERIES (not just the scalar Sortino),
# so the bootstrap has its source data.
# ---------------------------------------------------------------------------


class TestComputeSortinoTstatConsumesSeries:
    """The t-statistic must be reconstructable from the return series alone —
    the haircut call site already has the series (it set it as a trial
    user_attr at autotuner.py:996). A signature that accepts only a scalar
    Sortino + T cannot run a bootstrap."""

    def test_compute_sortino_tstat_accepts_return_series(self):
        """``compute_sortino_tstat`` must accept a per-day return series.

        Two acceptable shapes:
          (a) ``compute_sortino_tstat(returns)`` — derives Sortino + T from
              the series.
          (b) ``compute_sortino_tstat(sortino, T, returns=...)`` — keyword
              series argument with the scalar Sortino still accepted for
              backward call-site compatibility.

        Either shape lets the function bootstrap. A signature that lacks any
        series channel proves the bootstrap could not be running.
        """
        import inspect

        sig = inspect.signature(autotuner.compute_sortino_tstat)
        params = sig.parameters
        param_names = list(params.keys())

        accepts_series = (
            "returns" in param_names
            or "daily_returns" in param_names
            or "series" in param_names
            # Single positional: returns-only signature.
            or (len(param_names) == 1 and param_names[0] in ("returns", "daily_returns", "series"))
        )
        assert accepts_series, (
            "AC-1 / RM-H1: compute_sortino_tstat must accept the per-day "
            "return series so the bootstrap has its source data. Current "
            f"signature: {sig}. The haircut path at autotuner.py:996 already "
            "persists the series as a trial user_attr — the t-stat builder "
            "must consume it."
        )


# ---------------------------------------------------------------------------
# 4 — The t-statistic equals Sortino / SE_bootstrap (NOT Sortino * sqrt(T)).
#
# Mutation guard: a regression that reintroduces the Sharpe-Wald scaling
# MUST fail at least one test in this class. The Wald value and the bootstrap
# value are constructed to be numerically distinguishable on the basic
# fixture.
# ---------------------------------------------------------------------------


class TestSortinoTstatUsesBootstrapSe:
    """t-stat construction is Sortino / SE_bootstrap; pin against an
    independent reference."""

    def _resolve_bootstrap_helper(self):
        for n in (
            "_bootstrap_sortino_se",
            "bootstrap_sortino_se",
            "_compute_sortino_bootstrap_se",
        ):
            if hasattr(autotuner, n):
                return getattr(autotuner, n)
        pytest.fail("bootstrap SE helper not exposed.")

    def test_tstat_equals_sortino_over_bootstrap_se_basic_fixture(self):
        """For a known series with a known fixed-seed bootstrap SE, the
        t-statistic is Sortino / SE_bootstrap.

        Procedure:
          1. Hand-compute the Sortino from the series using the reference
             helper (independent of the producer's compute_sortino_ratio).
          2. Hand-compute the bootstrap SE from the series using the
             reference Efron implementation under a fixed seed.
          3. Expected t = Sortino / SE_bootstrap.

        The producer's compute_sortino_tstat must:
          (a) accept the series so it can re-run the bootstrap;
          (b) use the SAME seed (the test will require a seed channel — see
              the seed-acceptance test).
        """
        returns = list(_RETURN_SERIES_BASIC)

        # Independent Sortino.
        expected_sortino = _reference_sortino(returns)
        assert expected_sortino != 0.0, (
            "Fixture sanity: the basic series must not have a degenerate "
            "Sortino (it should exercise the downside branch)."
        )

        # Independent bootstrap SE under a fixed seed.
        ref_rng = random.Random(20260522)
        expected_se = _reference_bootstrap_sortino_se(
            returns, n_resamples=1000, rng=ref_rng
        )
        assert expected_se > 0.0, (
            "Fixture sanity: the basic series must produce a non-zero SE."
        )

        expected_t = expected_sortino / expected_se

        # Producer must accept (1) a seed channel so the test can pin it,
        # and (2) a series channel. Try the most explicit kwargs first.
        try:
            actual_t = autotuner.compute_sortino_tstat(
                returns=returns, rng_seed=20260522
            )
        except TypeError:
            # Try alternate keyword shapes the implementer might pick.
            try:
                actual_t = autotuner.compute_sortino_tstat(
                    returns, rng_seed=20260522
                )
            except TypeError:
                pytest.fail(
                    "AC-1 / RM-H1: compute_sortino_tstat must accept the "
                    "return series and a deterministic rng seed so the "
                    "bootstrap is testable. Could not call with "
                    "(returns=..., rng_seed=...) or (returns, rng_seed=...). "
                    "Implementer: please expose a `rng_seed` kwarg."
                )

        # rel=0 / abs=0: identical seed + identical algorithm + identical
        # Sortino formula = bit-identical t-stat. Any non-zero delta is a
        # genuine algorithmic divergence (e.g. a different SE protocol, or
        # the Sharpe-Wald scaling regressed in).
        assert actual_t == expected_t, (
            f"AC-1 / RM-H1: t-stat mismatch on the basic fixture. "
            f"Expected (Sortino={expected_sortino!r} / "
            f"SE_bootstrap={expected_se!r} = {expected_t!r}); "
            f"got {actual_t!r}. A non-zero delta means the producer's t-stat "
            f"is NOT Sortino / SE_bootstrap as Decision D10 prescribes."
        )

    def test_tstat_differs_from_sharpe_wald_scaling(self):
        """Mutation guard: if a regression reintroduces `Sortino * sqrt(T)`,
        the t-stat on the basic fixture diverges from the bootstrap-based
        value by far more than any reasonable bootstrap noise.

        Independent computation of the Wald value and the bootstrap value
        under the SAME fixture; assert the two are NOT close. This is the
        single most important guard in this file — the entire reason for
        AC-1 is that the Wald scaling survived Cluster 4.
        """
        returns = list(_RETURN_SERIES_BASIC)

        expected_sortino = _reference_sortino(returns)
        T = len(returns)
        wald_value = expected_sortino * math.sqrt(T)

        ref_rng = random.Random(20260522)
        expected_se = _reference_bootstrap_sortino_se(
            returns, n_resamples=1000, rng=ref_rng
        )
        bootstrap_value = expected_sortino / expected_se

        # The two values must differ by a clear margin. On the basic fixture,
        # SE_bootstrap is not coincidentally equal to 1/sqrt(T) — verified by
        # construction (the series has non-trivial downside variance). The
        # 10% separation floor catches any chance numerical coincidence
        # without making the test fragile to fixture tweaks.
        rel_diff = abs(wald_value - bootstrap_value) / max(
            abs(wald_value), abs(bootstrap_value), 1e-12
        )
        assert rel_diff > 0.10, (
            "AC-1 / RM-H1 fixture sanity: the basic fixture must distinguish "
            "the Sharpe-Wald scaling (Sortino * sqrt(T) = "
            f"{wald_value!r}) from the bootstrap construction "
            f"(Sortino / SE_bootstrap = {bootstrap_value!r}). Current "
            f"relative gap = {rel_diff:.4f}; must be > 0.10 for the "
            "mutation guard to bite. If this assertion fails, replace the "
            "fixture — the test would otherwise pass for the wrong reason."
        )

        # Now the actual mutation guard: call the producer and assert it
        # lands on the bootstrap value, NOT the Wald value.
        try:
            actual_t = autotuner.compute_sortino_tstat(
                returns=returns, rng_seed=20260522
            )
        except TypeError:
            try:
                actual_t = autotuner.compute_sortino_tstat(
                    returns, rng_seed=20260522
                )
            except TypeError:
                pytest.fail(
                    "compute_sortino_tstat must accept (returns, rng_seed); "
                    "see the preceding tstat-equals-bootstrap test."
                )

        # The producer's output must NOT match the Wald value within
        # 1% — far inside the > 10% fixture separation. If the producer
        # lands on the Wald value, the regression is live.
        wald_proximity = abs(actual_t - wald_value) / max(
            abs(wald_value), 1e-12
        )
        assert wald_proximity > 0.01, (
            f"AC-1 / RM-H1 REGRESSION: compute_sortino_tstat returned "
            f"{actual_t!r}, which matches the Sharpe-Wald scaling "
            f"`Sortino * sqrt(T) = {wald_value!r}` within "
            f"{wald_proximity:.4f}. The Cluster 4 defect has reappeared. "
            f"Decision D10 requires the bootstrap construction "
            f"`Sortino / SE_bootstrap`."
        )


# ---------------------------------------------------------------------------
# 5 — Degenerate guards: T <= 0, T == 1, constant series.
#
# All three would produce a non-finite t-stat in a naive implementation
# (division by zero or by an unstable SE). Pin that the producer returns a
# finite value in each case so the BHY step-up never sees a NaN/inf.
# ---------------------------------------------------------------------------


class TestSortinoTstatDegenerateGuards:
    """The t-stat must be finite for every degenerate input the haircut path
    can realistically present. BHY's running-minimum step-up corrupts the
    entire trial set if a single p-value is non-finite."""

    def test_tstat_finite_for_empty_series(self):
        """T = 0: the haircut filter already excludes zero-length series, but
        the function must still be safe to call on one (defensive)."""
        try:
            t = autotuner.compute_sortino_tstat(returns=[], rng_seed=1)
        except TypeError:
            try:
                t = autotuner.compute_sortino_tstat([], rng_seed=1)
            except TypeError:
                pytest.fail(
                    "compute_sortino_tstat must accept (returns, rng_seed)."
                )
        assert math.isfinite(t) or t == 0.0, (
            f"Empty series: t-stat must be finite (typically 0.0). Got "
            f"{t!r}."
        )

    def test_tstat_finite_for_single_observation(self):
        """T = 1: bootstrap SE is mathematically undefined (a single point
        has no spread under resampling); the function must return a finite
        sentinel rather than div-by-zero."""
        try:
            t = autotuner.compute_sortino_tstat(
                returns=[0.01], rng_seed=1
            )
        except TypeError:
            t = autotuner.compute_sortino_tstat([0.01], rng_seed=1)
        assert math.isfinite(t), (
            f"T=1 single-observation: t-stat must be finite (the bootstrap "
            f"SE is undefined; producer must return a documented sentinel). "
            f"Got {t!r}."
        )

    def test_tstat_finite_for_constant_series_zero_variance(self):
        """All returns identical and positive: Sortino is the 1e6 sentinel,
        bootstrap SE is exactly zero. The t-stat must NOT divide-by-zero; an
        explicit guard must produce a finite value (the natural choice is to
        propagate the Sortino sentinel through to a large finite t-stat, or
        return a documented sentinel — implementer chooses; test pins only
        the finiteness)."""
        try:
            t = autotuner.compute_sortino_tstat(
                returns=[0.01] * 30, rng_seed=1
            )
        except TypeError:
            t = autotuner.compute_sortino_tstat([0.01] * 30, rng_seed=1)
        assert math.isfinite(t), (
            f"Constant positive series: bootstrap SE is zero. The t-stat "
            f"must NOT be a division-by-zero NaN/inf. Got {t!r}. The "
            f"producer must guard against SE == 0 explicitly."
        )


# ---------------------------------------------------------------------------
# 6 — Small-T fallback boundary (T < 5).
#
# The plan's Edge Cases section flags that "bootstrap is unstable" for very
# small T. The risk-engine-specialist owns the choice of fallback; the test
# pins only that a fallback exists and yields finiteness.
# ---------------------------------------------------------------------------


class TestSmallTFallback:
    """A small-T fallback must exist; this test does not pin the chosen
    value, only that the producer does not crash and returns a finite
    value."""

    @pytest.mark.parametrize("T", [2, 3, 4])
    def test_tstat_finite_for_small_T(self, T: int):
        """T in {2,3,4}: bootstrap SE is too noisy to calibrate BHY reliably.
        The producer must have SOME documented fallback (Wald, exclusion,
        widened p-clamp — implementer's choice with the risk-engine-specialist's
        input). This test asserts only finiteness so the implementer is not
        over-constrained on the fallback policy."""
        returns = [0.01, -0.005, 0.008, 0.002][:T]
        try:
            t = autotuner.compute_sortino_tstat(returns=returns, rng_seed=1)
        except TypeError:
            t = autotuner.compute_sortino_tstat(returns, rng_seed=1)
        assert math.isfinite(t), (
            f"T={T}: the small-T fallback must yield a finite t-stat. Got "
            f"{t!r}. Implementer: pick a documented fallback policy with the "
            f"risk-engine-specialist (Wald scaling, trial exclusion, widened "
            f"clamp, etc.) and document the choice."
        )


# ---------------------------------------------------------------------------
# 7 — Comment / docstring honesty.
#
# RM-H1 explicitly calls out the dishonest "metric-neutral bridge" comment
# at autotuner.py:289-301. AC-1 requires it be corrected to describe the
# bootstrap construction. AST-scan the comment and docstring of
# compute_sortino_tstat for the corrected language.
# ---------------------------------------------------------------------------


class TestComputeSortinoTstatDocstringHonesty:
    """The Cluster 4 docstring claimed the t-stat was a 'metric-neutral
    bridge'. Post-RM-H1 it is a Sortino-specific bootstrap construction;
    the docstring must reflect that."""

    def test_docstring_describes_bootstrap_se(self):
        """The compute_sortino_tstat docstring must mention 'bootstrap' (the
        construction) and must NOT claim 'metric-neutral' (the
        misrepresentation RM-H1 flagged).
        """
        doc = (autotuner.compute_sortino_tstat.__doc__ or "").lower()
        assert "bootstrap" in doc, (
            "AC-1 / RM-H1: compute_sortino_tstat.__doc__ must mention the "
            "bootstrap construction (Decision D10 / Efron 1979). Current "
            f"docstring: {doc!r}"
        )
        assert "metric-neutral" not in doc, (
            "AC-1 / RM-H1: the 'metric-neutral bridge' phrasing must be "
            "removed — the Sortino-into-Sharpe-Wald-scaling that this "
            "phrasing concealed was the surviving M-3 category error. The "
            f"docstring still contains it: {doc!r}"
        )
