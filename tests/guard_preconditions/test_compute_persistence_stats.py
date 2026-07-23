"""
RED tests -- guard_preconditions.compute_persistence_stats (AC-1).

Golden fixtures: tests/fixtures/math/persistence_stats_ar1_positive.json,
persistence_stats_negative_edge.json, persistence_stats_iid_control.json.
All three are DETERMINISTICALLY CONSTRUCTED series (see each fixture's
"provenance" field) -- not producer-captured values. Expected rho/sharpe are
computed HERE from the raw series via the independent reference formulas in
_reference_stats.py, never hardcoded (feedback_no_hardcoded_test_values) and
never obtained by calling the SUT.

Contract under test (AC-1):
- compute_persistence_stats(daily_returns) -> PersistenceStats(rho, rho_ci,
  sharpe_daily, n_obs, insufficient_reason)
- Pure function: no I/O, no DB access, no network.
- rho: Box-Jenkins lag-1 sample ACF (see _reference_stats.reference_acf1).
- rho_ci: 95% CI half-width, stderr ~= 1/sqrt(N) (Bartlett white-noise SE).
- sharpe_daily: mean / sample_std(ddof=1), daily/non-annualized.
- n_obs: count of non-NaN observations actually used.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

import guard_preconditions as gp
from tests.guard_preconditions._reference_stats import (
    reference_acf1,
    reference_acf1_pairwise,
    reference_ci95,
    reference_sharpe_daily,
)

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math"

# pytest.approx tolerance rationale: the reference formulas and the SUT should
# agree to float-precision on identical arithmetic (both are closed-form sums
# over the same finite series) -- rel=1e-9 catches any real formula
# discrepancy (e.g. wrong ddof, wrong mean) while tolerating float-order-of-
# summation noise.
_TOL = dict(rel=1e-9)


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC-1: golden fixture -- positive-persistence series
# ---------------------------------------------------------------------------


class TestGoldenFixturePositivePersistence:
    def test_rho_matches_independent_reference_formula(self):
        fixture = _load("persistence_stats_ar1_positive.json")
        returns = fixture["daily_returns_pct"]
        expected_rho = reference_acf1(returns)

        stats = gp.compute_persistence_stats(returns)

        assert stats.rho == pytest.approx(expected_rho, **_TOL), (
            f"compute_persistence_stats rho={stats.rho!r} does not match the "
            f"independent Box-Jenkins lag-1 ACF reference={expected_rho!r} "
            "computed from the same fixture series."
        )

    def test_sharpe_daily_matches_independent_reference_formula(self):
        fixture = _load("persistence_stats_ar1_positive.json")
        returns = fixture["daily_returns_pct"]
        expected_sr = reference_sharpe_daily(returns)

        stats = gp.compute_persistence_stats(returns)

        assert stats.sharpe_daily == pytest.approx(expected_sr, **_TOL), (
            f"sharpe_daily={stats.sharpe_daily!r} does not match mean/sample_std(ddof=1) "
            f"reference={expected_sr!r} -- check ddof convention (house convention is "
            "ddof=1, see analytics.py:488)."
        )

    def test_rho_ci_matches_bartlett_white_noise_se(self):
        fixture = _load("persistence_stats_ar1_positive.json")
        returns = fixture["daily_returns_pct"]
        expected_ci = reference_ci95(len(returns))

        stats = gp.compute_persistence_stats(returns)

        assert stats.rho_ci == pytest.approx(expected_ci, **_TOL), (
            f"rho_ci={stats.rho_ci!r} does not match 1.96/sqrt(N)={expected_ci!r} "
            "(AC-1: 'stderr ~= 1/sqrt(N), method documented via constant + source comment')."
        )

    def test_n_obs_equals_fixture_length(self):
        fixture = _load("persistence_stats_ar1_positive.json")
        returns = fixture["daily_returns_pct"]

        stats = gp.compute_persistence_stats(returns)

        assert stats.n_obs == len(returns) == fixture["n_obs"]


# ---------------------------------------------------------------------------
# AC-1: golden fixture -- IID control (no persistence)
# ---------------------------------------------------------------------------


class TestGoldenFixtureIidControl:
    def test_rho_near_zero_matches_reference(self):
        fixture = _load("persistence_stats_iid_control.json")
        returns = fixture["daily_returns_pct"]
        expected_rho = reference_acf1(returns)

        stats = gp.compute_persistence_stats(returns)

        assert stats.rho == pytest.approx(expected_rho, **_TOL)
        # This fixture is generated with phi=0 (IID) -- documents intent, not
        # a hardcoded producer value; the actual bound is loose because a
        # finite-N IID draw is never EXACTLY zero.
        assert abs(stats.rho) < 0.3, (
            f"IID-control fixture (phi=0) produced rho={stats.rho!r}, expected "
            "near-zero lag-1 persistence. Either the fixture's own construction "
            "is broken or the SUT's rho formula is wrong -- cross-check against "
            "the AR1-positive fixture (rho~0.49) to disambiguate."
        )

    def test_sharpe_daily_matches_reference(self):
        fixture = _load("persistence_stats_iid_control.json")
        returns = fixture["daily_returns_pct"]
        expected_sr = reference_sharpe_daily(returns)

        stats = gp.compute_persistence_stats(returns)

        assert stats.sharpe_daily == pytest.approx(expected_sr, **_TOL)


# ---------------------------------------------------------------------------
# AC-1: golden fixture -- negative-edge series (rho still positive, SR < 0)
# ---------------------------------------------------------------------------


class TestGoldenFixtureNegativeEdgeSeries:
    def test_rho_matches_reference_and_is_still_positive(self):
        """This fixture shares the SAME AR(1) shock structure as the
        ar1_positive fixture (only the drift differs) -- rho must be
        numerically IDENTICAL to the positive-drift fixture's rho, proving
        rho is invariant to an additive mean shift (as it must be: rho is
        computed on mean-centered deviations)."""
        neg = _load("persistence_stats_negative_edge.json")
        pos = _load("persistence_stats_ar1_positive.json")
        expected_rho = reference_acf1(neg["daily_returns_pct"])

        stats_neg = gp.compute_persistence_stats(neg["daily_returns_pct"])
        stats_pos = gp.compute_persistence_stats(pos["daily_returns_pct"])

        assert stats_neg.rho == pytest.approx(expected_rho, **_TOL)
        assert stats_neg.rho == pytest.approx(stats_pos.rho, **_TOL), (
            "rho must be additive-shift-invariant (mean-centered statistic) -- "
            "the negative-edge and positive-drift fixtures share the identical "
            "AR(1) shock sequence and differ only by a constant drift, so their "
            f"rho values must match. Got neg.rho={stats_neg.rho!r} vs "
            f"pos.rho={stats_pos.rho!r}."
        )

    def test_sharpe_daily_is_negative(self):
        neg = _load("persistence_stats_negative_edge.json")
        stats = gp.compute_persistence_stats(neg["daily_returns_pct"])
        assert stats.sharpe_daily < 0, (
            f"persistence_stats_negative_edge.json is constructed with a negative "
            f"drift specifically so sharpe_daily < 0 (the NEGATIVE_EDGE trap fixture); "
            f"got sharpe_daily={stats.sharpe_daily!r}."
        )


# ---------------------------------------------------------------------------
# AC-1: scale invariance (documents + pins the input-unit contract)
# ---------------------------------------------------------------------------


class TestScaleInvariance:
    def test_rho_and_sharpe_invariant_to_positive_linear_scaling(self):
        """rho (a correlation, mean-centered) and sharpe_daily (a mean/std
        ratio) are BOTH mathematically invariant under uniform positive
        scaling of the whole series. This test documents the input-unit
        contract: compute_persistence_stats accepts whatever consistent
        units the caller uses (this feature's accessors both natively
        produce PERCENT units, e.g. 1.5 meaning +1.5%, so no
        RETURN_PCT_TO_FRACTION conversion is required at the call site --
        unlike the CRRA-EU/PBO wealth-multiplication boundary in autotuner.py,
        where the units DO matter because (1+r) is not scale-invariant).
        A non-scale-invariant implementation (e.g. one with a hardcoded
        fraction-scale epsilon) would fail this test.
        """
        fixture = _load("persistence_stats_ar1_positive.json")
        returns_pct = fixture["daily_returns_pct"]
        returns_fraction = [r / 100.0 for r in returns_pct]

        stats_pct = gp.compute_persistence_stats(returns_pct)
        stats_fraction = gp.compute_persistence_stats(returns_fraction)

        assert stats_pct.rho == pytest.approx(stats_fraction.rho, rel=1e-6)
        assert stats_pct.sharpe_daily == pytest.approx(stats_fraction.sharpe_daily, rel=1e-6)


# ---------------------------------------------------------------------------
# AC-1 / Edge Cases: N_MIN_OBS gate (boundary, not just direction)
# ---------------------------------------------------------------------------


class TestNMinObsGate:
    def test_n_min_obs_is_a_named_constant(self):
        assert hasattr(gp, "N_MIN_OBS"), (
            "guard_preconditions.py must export N_MIN_OBS as a named module-scope "
            "constant (AC-3, no-magic-numbers rule)."
        )
        assert gp.N_MIN_OBS == 40, (
            f"N_MIN_OBS={gp.N_MIN_OBS!r}, expected 40 per feature plan AC-3 "
            "(PM-ASSUMED, aligned with the MC minimum-raw-history floor)."
        )

    def test_below_floor_yields_insufficient_data_reason(self):
        n = gp.N_MIN_OBS - 1
        returns = [0.1 * ((-1) ** i) for i in range(n)]  # trivial oscillating series
        stats = gp.compute_persistence_stats(returns)
        assert stats.n_obs == n
        assert stats.insufficient_reason is not None, (
            f"N={n} is one below N_MIN_OBS={gp.N_MIN_OBS} -- insufficient_reason "
            "must be set (non-None), never silently computing a stat on too-few points."
        )

    def test_at_floor_is_not_automatically_insufficient(self):
        """Pins the >= vs > boundary explicitly: N == N_MIN_OBS must NOT be
        treated as insufficient (only N < N_MIN_OBS is)."""
        n = gp.N_MIN_OBS
        returns = [0.1 * ((-1) ** i) + 0.01 * i for i in range(n)]
        stats = gp.compute_persistence_stats(returns)
        assert stats.n_obs == n
        assert stats.insufficient_reason is None, (
            f"N={n} == N_MIN_OBS={gp.N_MIN_OBS} must NOT be flagged insufficient "
            f"(got insufficient_reason={stats.insufficient_reason!r}). Only N < "
            "N_MIN_OBS is insufficient -- an off-by-one here silently discards a "
            "symphony's ONLY exactly-at-floor read."
        )


# ---------------------------------------------------------------------------
# Edge Cases: zero-variance / flat series (no div-by-zero)
# ---------------------------------------------------------------------------


class TestZeroVarianceGuard:
    def test_flat_series_does_not_raise_and_is_insufficient(self):
        n = gp.N_MIN_OBS + 5
        returns = [0.0] * n  # zero variance -> std=0 -> naive mean/std blows up

        stats = gp.compute_persistence_stats(returns)  # must not raise

        assert stats.insufficient_reason is not None, (
            "A zero-variance (flat) series has an undefined Sharpe ratio (0/0) -- "
            "per the plan's Edge Cases, this must guard against div-by-zero and "
            "map to insufficient_reason being set, never raise ZeroDivisionError "
            "and never silently return NaN/inf."
        )
        assert not (isinstance(stats.sharpe_daily, float) and math.isnan(stats.sharpe_daily)) or (
            stats.insufficient_reason is not None
        )


# ---------------------------------------------------------------------------
# Edge Cases: NaN / missing-day handling -- PAIRWISE deletion, never forward-filled
# ---------------------------------------------------------------------------


class TestNaNPairwiseHandling:
    def test_nan_days_reduce_n_obs_and_are_never_forward_filled(self):
        """A missing day in the middle of the series: n_obs must reflect the
        post-drop count, and the value must NOT be forward-filled (which
        would silently manufacture a fake zero-return day and corrupt both
        rho and sharpe_daily)."""
        base = [1.0, 2.0, None, 4.0, 5.0, -1.0, 3.0, 0.5, -2.0, 1.5] * (gp.N_MIN_OBS // 10 + 2)
        base = base[: gp.N_MIN_OBS + 3]
        # Ensure at least one None survives the truncation/repeat.
        assert None in base

        stats = gp.compute_persistence_stats(base)

        n_present = sum(1 for x in base if x is not None)
        assert stats.n_obs == n_present, (
            f"n_obs={stats.n_obs!r} must equal the count of non-missing values "
            f"({n_present}), not len(daily_returns)={len(base)!r} (which would "
            "imply forward-filling or otherwise counting the missing day)."
        )

    def test_pairwise_deletion_not_listwise_deletion(self):
        """The discriminating test: a gap must NOT make the values on either
        side of it newly 'adjacent' for the lag-1 product (that is listwise
        deletion and corrupts the lag-1 semantic -- see AC-2's sibling trap
        class, on the data side instead of the unit side)."""
        # Short series padded to N_MIN_OBS with a stable, non-contributing
        # tail so the gap's effect on the reference-vs-listwise formulas
        # (computed on the FULL series) stays the dominant, observable signal.
        head = [1.0, 2.0, None, 4.0, 5.0]
        tail = [0.2, -0.2] * ((gp.N_MIN_OBS - len(head)) // 2 + 1)
        series = (head + tail)[: gp.N_MIN_OBS + 5]

        expected_rho, expected_n = reference_acf1_pairwise(series)
        # The WRONG (listwise) computation: drop Nones, then treat remaining
        # values as newly adjacent.
        listwise_series = [x for x in series if x is not None]
        wrong_rho = reference_acf1(listwise_series)

        stats = gp.compute_persistence_stats(series)

        assert stats.n_obs == expected_n
        assert stats.rho == pytest.approx(expected_rho, rel=1e-9), (
            f"rho={stats.rho!r} does not match the pairwise-deletion reference "
            f"{expected_rho!r}. If it instead matches the LISTWISE-deletion "
            f"value {wrong_rho!r}, the implementation is collapsing the gap and "
            "treating non-adjacent days as adjacent -- exactly the corruption "
            "the plan's 'dropped pairwise... never forward-filled' edge case forbids."
        )
