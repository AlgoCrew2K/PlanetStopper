"""
AC-1 / RM-H1 — bootstrap Sortino SE t-statistic (REVISED per risk-engine
design memo 2026-05-22 and team-lead approval).

The Cluster 4 remediation kept Sortino as the haircut metric but
constructed the haircut t-statistic as ``t = Sortino * sqrt(T)`` — the
Sharpe-specific Wald scaling. Sortino's asymptotic SE differs (downside
deviation, not full std — Bertrand & Prigent 2019). The resulting p-values
are mis-calibrated under H0 and BHY's FDR guarantee fails.

Binding design (team-lead approved, risk-engine-specialist authored):

  ``t = Sortino / SE_bootstrap``

where SE_bootstrap is the sample standard deviation (ddof=1) of Sortinos
computed across ``_BOOTSTRAP_RESAMPLES = 2000`` independent nonparametric
bootstrap resamples of the per-day return series. The RNG is
``numpy.random.default_rng(seed)`` with ``rng.integers(0, T, size=T)``.

Resample-count rationale:
  - Efron 1979 (Annals of Statistics 7(1):1-26) — bootstrap SE convergence.
  - Efron & Tibshirani 1986 (Statistical Science 1(1):54-77) — B=50-200
    for SE point estimation; B>=1000 for p-value/CI calibration.
  - Davidson & MacKinnon 2000 (Econometric Reviews 19(1):55-68) — B=999
    standard for test-statistic bootstrap.

SE-unavailable semantics (binding): the helper returns None when:
  - T < _BOOTSTRAP_MIN_T = 5;
  - constant series (zero variance);
  - fewer than _BOOTSTRAP_MIN_VALID_RESAMPLES = 100 non-sentinel Sortinos.
compute_sortino_tstat then returns 0.0 -> compute_haircut_pvalue(0.0) =
0.5 -> trial fails the FDR gate by default (conservative, honest).

All oracle numerics in this file come from the independent derivation
script ``scripts/derive_h1_bootstrap_fixture.py`` which re-implements
Efron 1979 from scratch and does NOT import autotuner. The numerics are
reproducible by running the script and matching the printed values.
"""

from __future__ import annotations

import math
import re

import numpy as np
import pytest

import autotuner


# ---------------------------------------------------------------------------
# Oracle fixtures. Series + (Sortino, SE, t, p) numerics come verbatim from
# scripts/derive_h1_bootstrap_fixture.py at seed=20260522, B=2000.
# Updating ANY number here without re-running the derivation script breaks
# the provenance chain — re-run the script and paste the new values.
# ---------------------------------------------------------------------------

_FIXTURE_SEED = 20260522

_RETURN_SERIES_BASIC: tuple[float, ...] = (
    0.012, -0.005, 0.008, 0.003, -0.011, 0.006,
    0.001, 0.015, -0.002, 0.009, 0.004, -0.007,
)
_BASIC_SORTINO = 0.675300044982798
_BASIC_SE      = 1.666246779934804
_BASIC_TSTAT   = 0.4052821305432445
_BASIC_WALD    = 2.339307976527509      # mutation-guard separation reference

_RETURN_SERIES_POSITIVE_SORTINO: tuple[float, ...] = (
    0.020, 0.015, 0.018, -0.005, 0.022, 0.012, 0.019, -0.003,
    0.025, 0.014, 0.017, 0.011, -0.006, 0.020, 0.013, 0.016,
    0.024, 0.018, 0.012, 0.021,
)
_POSITIVE_SORTINO_T = 1.6473041744598693

_RETURN_SERIES_NEGATIVE_SORTINO: tuple[float, ...] = (
    -0.010, -0.015, -0.008, -0.012, -0.005, -0.020, -0.011,
    -0.007, -0.014, -0.009, -0.013, -0.006, -0.018, -0.016, -0.010,
)
_NEGATIVE_SORTINO_T = -59.48057334660955

_RETURN_SERIES_NULL_SORTINO: tuple[float, ...] = (
    0.010, -0.010, 0.008, -0.008, 0.012, -0.012, 0.005, -0.005,
    0.015, -0.015, 0.007, -0.007, 0.011, -0.011, 0.009, -0.009,
    0.013, -0.013, 0.006, -0.006,
)
_NULL_SORTINO_T = 0.0

_RETURN_SERIES_SENTINEL_RICH: tuple[float, ...] = (
    *([0.0] * 28),
    0.001, 0.002,
)


# ---------------------------------------------------------------------------
# 1 — Module-level constants are NAMED and CANONICAL.
# ---------------------------------------------------------------------------


class TestBootstrapConstants:
    """Risk-engine's binding constants exposed on the autotuner module."""

    def test_bootstrap_resamples_constant_named_and_value(self):
        """_BOOTSTRAP_RESAMPLES must equal 2000 (Efron & Tibshirani 1986 +
        Davidson & MacKinnon 2000 calibrating-use-case floor)."""
        assert hasattr(autotuner, "_BOOTSTRAP_RESAMPLES"), (
            "AC-1 / RM-H1: autotuner._BOOTSTRAP_RESAMPLES must be named "
            "as a module-level constant per risk-engine's binding design."
        )
        assert isinstance(autotuner._BOOTSTRAP_RESAMPLES, int)
        assert autotuner._BOOTSTRAP_RESAMPLES == 2000, (
            f"_BOOTSTRAP_RESAMPLES must equal 2000; got "
            f"{autotuner._BOOTSTRAP_RESAMPLES}."
        )

    def test_bootstrap_min_t_constant_named(self):
        """_BOOTSTRAP_MIN_T must be a named constant >= 5."""
        assert hasattr(autotuner, "_BOOTSTRAP_MIN_T"), (
            "autotuner._BOOTSTRAP_MIN_T must be a named constant."
        )
        assert isinstance(autotuner._BOOTSTRAP_MIN_T, int)
        assert autotuner._BOOTSTRAP_MIN_T >= 5, (
            f"_BOOTSTRAP_MIN_T must be >= 5; got "
            f"{autotuner._BOOTSTRAP_MIN_T}."
        )

    def test_bootstrap_min_valid_resamples_constant_named(self):
        """_BOOTSTRAP_MIN_VALID_RESAMPLES — floor on non-sentinel
        resampled Sortinos."""
        assert hasattr(autotuner, "_BOOTSTRAP_MIN_VALID_RESAMPLES"), (
            "autotuner._BOOTSTRAP_MIN_VALID_RESAMPLES must be a named "
            "constant."
        )
        assert isinstance(autotuner._BOOTSTRAP_MIN_VALID_RESAMPLES, int)
        assert autotuner._BOOTSTRAP_MIN_VALID_RESAMPLES >= 50, (
            f"_BOOTSTRAP_MIN_VALID_RESAMPLES must be at least 50; "
            f"risk-engine recommends 100. Got "
            f"{autotuner._BOOTSTRAP_MIN_VALID_RESAMPLES}."
        )


# ---------------------------------------------------------------------------
# 2 — Bootstrap SE helper: API + numeric oracle pin.
# ---------------------------------------------------------------------------


def _resolve_se_helper():
    """Resolve the bootstrap SE helper across acceptable names."""
    for n in (
        "compute_sortino_se_bootstrap",
        "_bootstrap_sortino_se",
        "bootstrap_sortino_se",
    ):
        if hasattr(autotuner, n):
            return getattr(autotuner, n)
    pytest.fail("Bootstrap SE helper not exposed on autotuner.")


class TestBootstrapSortinoSeHelper:
    """SE helper signature, numeric pin, determinism, None-fallback."""

    def test_helper_exists(self):
        for n in (
            "compute_sortino_se_bootstrap",
            "_bootstrap_sortino_se",
            "bootstrap_sortino_se",
        ):
            if hasattr(autotuner, n):
                return
        pytest.fail(
            "AC-1 / RM-H1: a bootstrap SE helper must be exposed on "
            "autotuner (one of compute_sortino_se_bootstrap, "
            "_bootstrap_sortino_se, bootstrap_sortino_se)."
        )

    def test_basic_fixture_matches_independent_oracle(self):
        """The producer's SE on the BASIC fixture matches the value the
        independent derivation script produced (seed=20260522, B=2000).

        rel=1e-12: producer and oracle both use numpy.random.default_rng
        with the same seed; the sequences are bit-identical.
        """
        helper = _resolve_se_helper()
        returns = list(_RETURN_SERIES_BASIC)

        # Producer signature per risk-engine's spec:
        # (returns, target=SORTINO_TARGET_RETURN, B=_BOOTSTRAP_RESAMPLES, seed)
        se = helper(returns, seed=_FIXTURE_SEED)

        assert se == pytest.approx(_BASIC_SE, rel=1e-12), (
            f"AC-1 / RM-H1 VIOLATED: bootstrap SE on BASIC fixture is "
            f"{se!r}; oracle is {_BASIC_SE!r}. Producer must use "
            "numpy.random.default_rng with rng.integers(0, T, size=T) "
            "and the same B as the derivation script."
        )

    def test_helper_is_deterministic_under_same_seed(self):
        """Same input + seed -> same SE across two calls."""
        helper = _resolve_se_helper()
        returns = list(_RETURN_SERIES_BASIC)
        se1 = helper(returns, seed=_FIXTURE_SEED)
        se2 = helper(returns, seed=_FIXTURE_SEED)
        assert se1 == se2, (
            f"Determinism VIOLATED: same input + seed produced different "
            f"SE values ({se1!r} vs {se2!r})."
        )

    def test_helper_returns_none_for_small_T(self):
        """T < _BOOTSTRAP_MIN_T -> SE = None."""
        helper = _resolve_se_helper()
        result = helper([0.01, -0.005, 0.008, 0.002], seed=1)  # T=4
        assert result is None, (
            f"Small-T (T=4): helper must return None. Got {result!r}."
        )

    def test_helper_returns_none_for_zero_variance_series(self):
        """Constant series -> SE = None."""
        helper = _resolve_se_helper()
        result = helper([0.01] * 30, seed=1)
        assert result is None, (
            f"Zero-variance series: helper must return None. Got "
            f"{result!r}."
        )

    def test_helper_returns_none_for_sentinel_rich_series(self):
        """28 zeros + 2 small positives: most resamples land all-non-
        negative -> +1e6 sentinel -> < _BOOTSTRAP_MIN_VALID_RESAMPLES
        non-sentinel Sortinos -> SE = None."""
        helper = _resolve_se_helper()
        result = helper(list(_RETURN_SERIES_SENTINEL_RICH), seed=_FIXTURE_SEED)
        assert result is None, (
            f"Sentinel-rich series must return SE=None. Got {result!r}."
        )

    def test_helper_is_nonnegative_on_arbitrary_input(self):
        """SE >= 0 when not None (it's a stdev)."""
        helper = _resolve_se_helper()
        for label, series in (
            ("basic", _RETURN_SERIES_BASIC),
            ("positive", _RETURN_SERIES_POSITIVE_SORTINO),
            ("negative", _RETURN_SERIES_NEGATIVE_SORTINO),
            ("null", _RETURN_SERIES_NULL_SORTINO),
        ):
            se = helper(list(series), seed=_FIXTURE_SEED)
            if se is None:
                continue
            assert math.isfinite(se) and se >= 0.0, (
                f"Series '{label}': SE must be finite and >= 0; got "
                f"{se!r}."
            )


# ---------------------------------------------------------------------------
# 3 — compute_sortino_tstat: API + oracle pin.
# ---------------------------------------------------------------------------


def _resolve_tstat(returns, seed=_FIXTURE_SEED):
    """Call compute_sortino_tstat under acceptable signature shapes."""
    try:
        return autotuner.compute_sortino_tstat(returns=returns, seed=seed)
    except TypeError:
        pass
    try:
        return autotuner.compute_sortino_tstat(returns, seed=seed)
    except TypeError:
        pass
    # risk-engine memo signature: (sortino, returns, seed)
    try:
        sortino = autotuner.compute_sortino_ratio(returns)
        return autotuner.compute_sortino_tstat(sortino, returns, seed)
    except TypeError:
        pass
    pytest.fail(
        "compute_sortino_tstat signature not recognised. Risk-engine "
        "spec: `(returns, seed)` or `(sortino, returns, seed)`."
    )


class TestComputeSortinoTstatBootstrapApi:
    """compute_sortino_tstat consumes the return series + a seed and
    produces Sortino / SE_bootstrap."""

    def test_basic_fixture_tstat_matches_oracle(self):
        result = _resolve_tstat(list(_RETURN_SERIES_BASIC))
        assert result == pytest.approx(_BASIC_TSTAT, rel=1e-12), (
            f"AC-1 / RM-H1 VIOLATED: t-stat on BASIC fixture is "
            f"{result!r}; oracle is {_BASIC_TSTAT!r}."
        )

    def test_tstat_separates_from_wald_scaling_on_basic_fixture(self):
        """MUTATION GUARD: Wald = Sortino * sqrt(T) = 2.339 vs
        bootstrap-based t = 0.405 on the BASIC fixture. The producer
        must land on the bootstrap value, far from Wald.

        Pin: |result - oracle| < 0.1 * |oracle - wald| — the producer's
        distance from the correct value is at most 10% of the gap
        between correct and Wald. If the implementer regressed to Wald,
        |result - oracle| would jump from ~0 to ~|oracle - wald|, far
        exceeding the threshold.
        """
        result = _resolve_tstat(list(_RETURN_SERIES_BASIC))
        oracle_distance = abs(result - _BASIC_TSTAT)
        oracle_wald_gap = abs(_BASIC_TSTAT - _BASIC_WALD)
        assert oracle_distance < 0.1 * oracle_wald_gap, (
            f"AC-1 / RM-H1 REGRESSION: compute_sortino_tstat returned "
            f"{result!r}, distance {oracle_distance!r} from the "
            f"bootstrap-based oracle {_BASIC_TSTAT!r}. Threshold: < "
            f"{0.1 * oracle_wald_gap!r} (10% of the bootstrap-vs-Wald "
            f"gap {oracle_wald_gap!r}). If the implementer regressed to "
            f"Wald = {_BASIC_WALD!r}, this assertion fails."
        )


# ---------------------------------------------------------------------------
# 4 — Sign / direction property (risk-engine test #2).
# ---------------------------------------------------------------------------


class TestSortinoTstatSignAndDirection:
    """Positive Sortino -> t > 0; negative -> t < 0; null -> t ≈ 0."""

    def test_positive_sortino_series_produces_positive_tstat(self):
        result = _resolve_tstat(list(_RETURN_SERIES_POSITIVE_SORTINO))
        assert result > 0.0, f"Expected t > 0; got {result!r}."
        assert result == pytest.approx(_POSITIVE_SORTINO_T, rel=1e-12), (
            f"POSITIVE_SORTINO t-stat oracle mismatch: expected "
            f"{_POSITIVE_SORTINO_T!r}; got {result!r}."
        )

    def test_negative_sortino_series_produces_negative_tstat(self):
        result = _resolve_tstat(list(_RETURN_SERIES_NEGATIVE_SORTINO))
        assert result < 0.0, f"Expected t < 0; got {result!r}."
        assert result == pytest.approx(_NEGATIVE_SORTINO_T, rel=1e-12), (
            f"NEGATIVE_SORTINO t-stat oracle mismatch: expected "
            f"{_NEGATIVE_SORTINO_T!r}; got {result!r}."
        )

    def test_null_sortino_series_produces_tstat_zero(self):
        result = _resolve_tstat(list(_RETURN_SERIES_NULL_SORTINO))
        assert result == pytest.approx(_NULL_SORTINO_T, abs=0.0), (
            f"NULL_SORTINO t-stat oracle mismatch: expected "
            f"{_NULL_SORTINO_T!r}; got {result!r}."
        )


# ---------------------------------------------------------------------------
# 5 — SE-unavailable -> t=0 -> p=0.5 (binding spec chain).
# ---------------------------------------------------------------------------


class TestSeUnavailableProducesZeroTstat:
    """The full chain: SE=None -> t=0 -> compute_haircut_pvalue=0.5."""

    def test_zero_variance_series_produces_zero_tstat(self):
        result = _resolve_tstat([0.01] * 30)
        assert result == 0.0, (
            f"Zero-variance series: tstat must be 0.0. Got {result!r}."
        )

    def test_small_T_series_produces_zero_tstat(self):
        result = _resolve_tstat([0.01, -0.005, 0.008, 0.002])
        assert result == 0.0, (
            f"Small-T: tstat must be 0.0. Got {result!r}."
        )

    def test_sentinel_rich_series_produces_zero_tstat(self):
        result = _resolve_tstat(list(_RETURN_SERIES_SENTINEL_RICH))
        assert result == 0.0, (
            f"Sentinel-rich: tstat must be 0.0. Got {result!r}."
        )

    def test_compute_haircut_pvalue_of_zero_is_half(self):
        """Downstream invariant: p(t=0) = 0.5; trial fails FDR gate."""
        p = autotuner.compute_haircut_pvalue(0.0)
        assert p == pytest.approx(0.5, rel=1e-9), (
            f"compute_haircut_pvalue(0.0) must be 0.5; got {p!r}."
        )


# ---------------------------------------------------------------------------
# 6 — _haircut_select caller-side integration (risk-engine test #8).
# ---------------------------------------------------------------------------


class TestHaircutSelectPassesReturnsAndSeed:
    """_haircut_select must pass each trial's daily_returns and a
    deterministic seed into compute_sortino_tstat."""

    def test_haircut_select_is_deterministic_on_same_trial_set(self):
        """Two runs on the same trial set produce identical p_adj."""
        import types

        def make_trial(value, returns):
            t = types.SimpleNamespace()
            t.value = value
            t.user_attrs = {"daily_returns": list(returns)}
            t.params = {"placeholder": 1.0}
            return t

        trials = [
            make_trial(0.5, list(_RETURN_SERIES_BASIC)),
            make_trial(0.8, list(_RETURN_SERIES_POSITIVE_SORTINO)),
        ]

        r1 = autotuner._haircut_select(trials)
        r2 = autotuner._haircut_select(trials)
        assert r1[1] == r2[1], (
            f"AC-1: _haircut_select non-deterministic — two runs produced "
            f"p_adj {r1[1]!r} vs {r2[1]!r}. The seed passed to "
            "compute_sortino_tstat must be derived from trial number / "
            "fold context, not wallclock."
        )
        assert r1[2] == r2[2], (
            f"AC-1: _haircut_select non-deterministic t-stat ({r1[2]!r} "
            f"vs {r2[2]!r})."
        )


# ---------------------------------------------------------------------------
# 7 — H0 calibration property (slow; risk-engine test #9).
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestH0CalibrationProperty:
    """Under H0 the bootstrap-SE-based p-value CDF is approximately
    uniform on [0,1].

    Tolerance per risk-engine-specialist F3: with 200 H0 series the
    one-sample KS test's 95% null critical value (Massey table) is
    ~0.094, so a strict `< 0.10` threshold is borderline-flaky. The test
    uses `< 0.15` to leave headroom for the bootstrap-of-bootstrap
    variance without softening the audit-required calibration claim. A
    regression to Wald scaling would push KS far above 0.15 — empirical
    runs of the Wald formula on the same H0 series yield KS ≈ 0.45.

    Reference run from scripts/derive_h1_bootstrap_fixture.py produced
    KS = 0.0640 — well under both 0.10 and 0.15.
    """

    def test_h0_pvalues_are_approximately_uniform(self):
        rng = np.random.default_rng(99)
        p_values: list[float] = []
        for k in range(200):
            series = list(rng.normal(loc=0.0, scale=0.01, size=60))
            t = _resolve_tstat(series, seed=_FIXTURE_SEED + k)
            p = autotuner.compute_haircut_pvalue(t)
            p_values.append(p)
        sorted_p = sorted(p_values)
        n = len(sorted_p)
        ks = max(
            max(abs((i + 1) / n - p), abs(i / n - p))
            for i, p in enumerate(sorted_p)
        )
        assert ks < 0.15, (
            f"AC-1 / RM-H1 CALIBRATION VIOLATED: KS distance to U[0,1] = "
            f"{ks:.4f}; threshold < 0.15 (per risk-engine F3 — 0.10 is "
            "borderline at the 200-trial Massey critical value 0.094). "
            "Reference run produced KS ≈ 0.064; a regression to Wald "
            "scaling would push KS above 0.45."
        )


# ---------------------------------------------------------------------------
# 8 — No-Wald-survival grep + docstring honesty.
# ---------------------------------------------------------------------------


class TestNoWaldSurvivalInTstatPath:
    """Belt-and-braces grep guard against the literal Wald expression."""

    def test_no_wald_scaling_in_compute_sortino_tstat_source(self):
        import inspect
        src = inspect.getsource(autotuner.compute_sortino_tstat)
        pattern = re.compile(
            r"sortino\s*\*\s*(math\.)?sqrt\s*\(\s*T\s*\)",
            re.IGNORECASE,
        )
        match = pattern.search(src)
        assert match is None, (
            f"AC-1 / RM-H1 REGRESSION: Wald expression "
            f"`sortino * sqrt(T)` survives in compute_sortino_tstat "
            f"at `{match.group(0) if match else ''}`."
        )

    def test_docstring_describes_bootstrap_not_metric_neutral(self):
        doc = (autotuner.compute_sortino_tstat.__doc__ or "").lower()
        assert "bootstrap" in doc, (
            f"compute_sortino_tstat docstring must describe the bootstrap. "
            f"Got: {doc!r}"
        )
        assert "metric-neutral" not in doc, (
            f"compute_sortino_tstat docstring must NOT contain "
            f"'metric-neutral'. Got: {doc!r}"
        )
