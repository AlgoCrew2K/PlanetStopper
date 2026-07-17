"""
AC-5 rider (M1-adjacent, per team-lead's ADDENDUM 4 ruling and r2-analytics's
exact formula) -- backtest_gate_engine._fold_transform_single.oos_alpha must
compound genuinely, not naive-sum.

WHY THIS RIDES ON AC-5: composer_backtest_client._extract_returns is being
fixed to emit SIMPLE returns directly (curr/prev - 1) instead of LOG returns.
Under LOG returns, ``oos_alpha = sum(validation_returns_pct)`` was EXACT
compounding (sum of logs = log of the product) -- a happy accident of the
old (buggy) emission convention. Once the producer emits SIMPLE returns,
``sum()`` becomes an APPROXIMATION that ignores variance drag (Jensen's
inequality: naive-summing simple returns systematically OVER-states the
true compounded return of a volatile series relative to a smooth one with
the same sum). The genuine compounded formula, confirmed by r2-analytics's
call-site sweep (7 sites, all percent-scale):

    oos_alpha = (math.prod(1.0 + r / 100.0 for r in validation_returns) - 1.0) * 100.0

Golden discipline (ratified fixture standard -- direction + relative
scaling, NOT the audit's illustrative absolute numbers, since this is a
newly-introduced fix with no audit-quoted target): this file constructs two
validation-fold return series with the SAME naive sum but different
variance, verifies (self-check) that naive-sum ranks them one way while
genuine compounding ranks them the OPPOSITE way (the "flip case" -- a
direct, derivable consequence of variance drag, not tuned to pass), then
asserts _fold_transform_single.oos_alpha must agree with the COMPOUND
ranking, not the naive-sum ranking.
"""

from __future__ import annotations

import math

import pytest

from advisors.backtest_gate_engine import (
    EMBARGO_DAYS,
    PURGE_DAYS,
    TRAIN_RATIO,
    VALIDATION_RATIO,
    _fold_transform_single,
)

_N_TOTAL_DAYS = 100  # -> val_start_idx=60, frozen_start_idx=80, 20-day validation fold


def _naive_sum(returns_pct: list[float]) -> float:
    return sum(returns_pct)


def _genuine_compound(returns_pct: list[float]) -> float:
    """The correct compounded percent return -- r2-analytics's exact ruled
    formula, reproduced here as the golden reference (not re-derived
    independently, so this pins the RULED formula itself)."""
    product = math.prod(1.0 + r / 100.0 for r in returns_pct)
    return (product - 1.0) * 100.0


def _low_vol_validation_fold() -> list[float]:
    """20 days of consistent +0.3% -- naive sum = 6.0."""
    return [0.3] * 20


def _high_vol_validation_fold() -> list[float]:
    """20 days alternating +6.35% / -5.70% (10 pairs) -- naive sum = 6.5,
    HIGHER than the low-vol series' 6.0, despite dramatically higher
    variance. Genuine compounding must rank this BELOW the low-vol series
    (variance drag) -- the flip case."""
    fold: list[float] = []
    for _ in range(10):
        fold.append(6.35)
        fold.append(-5.70)
    return fold


def _build_daily_returns_pct(validation_fold: list[float]) -> list[float]:
    """Wrap a 20-element validation-fold series into a full _N_TOTAL_DAYS
    series with arbitrary (zero) filler train/frozen days on either side --
    _fold_transform_single only reads daily_returns_pct[60:80] for oos_alpha,
    so filler content elsewhere is inert by construction (train-side purge
    integrity is a separate, already-covered concern)."""
    assert len(validation_fold) == 20
    val_start_idx = int(_N_TOTAL_DAYS * TRAIN_RATIO)
    frozen_start_idx = int(_N_TOTAL_DAYS * (TRAIN_RATIO + VALIDATION_RATIO))
    assert frozen_start_idx - val_start_idx == 20, (
        "Fixture construction assumption broken: TRAIN_RATIO/VALIDATION_RATIO "
        f"changed such that the {_N_TOTAL_DAYS}-day validation slice is no longer "
        "20 days. Adjust _N_TOTAL_DAYS or the validation-fold length."
    )
    return (
        [0.0] * val_start_idx
        + validation_fold
        + [0.0] * (_N_TOTAL_DAYS - frozen_start_idx)
    )


class TestFlipCaseSelfCheck:
    """Confirms the constructed fixture genuinely discriminates BEFORE
    testing the production function -- if this self-check fails, the
    fixture doesn't demonstrate the flip case and the downstream assertion
    would be vacuous."""

    def test_naive_sum_favors_high_vol_but_compound_favors_low_vol(self):
        low_vol = _low_vol_validation_fold()
        high_vol = _high_vol_validation_fold()

        naive_low, naive_high = _naive_sum(low_vol), _naive_sum(high_vol)
        compound_low, compound_high = _genuine_compound(low_vol), _genuine_compound(high_vol)

        assert naive_high > naive_low, (
            f"Fixture self-check failed: naive sum should favor high_vol "
            f"({naive_high!r}) over low_vol ({naive_low!r}) for this to be a "
            "genuine flip case."
        )
        assert compound_low > compound_high, (
            f"Fixture self-check failed: genuine compounding should favor "
            f"low_vol ({compound_low!r}) over high_vol ({compound_high!r}) -- "
            "variance drag should reverse the naive-sum ranking. If this "
            "fails, the constructed series no longer demonstrates a flip case."
        )


class TestOosAlphaUsesGenuineCompounding:
    def test_oos_alpha_matches_genuine_compound_not_naive_sum(self):
        """RED (pre-fix): _fold_transform_single.oos_alpha == sum(validation_returns),
        which is the naive-sum value -- it will NOT match the genuine compound
        reference for the high-vol series (where the two formulas diverge
        materially due to variance drag)."""
        high_vol = _high_vol_validation_fold()
        daily_returns_pct = _build_daily_returns_pct(high_vol)

        result = _fold_transform_single(daily_returns_pct)

        expected = _genuine_compound(high_vol)
        assert result.oos_alpha == pytest.approx(expected, abs=1e-9), (
            f"_fold_transform_single.oos_alpha={result.oos_alpha!r} does not match "
            f"the genuine compound reference {expected!r} (naive sum would give "
            f"{_naive_sum(high_vol)!r}). AC-5 rider: oos_alpha must compound "
            "genuinely, not naive-sum, once the producer emits simple returns."
        )

    def test_oos_alpha_ranking_agrees_with_compound_not_naive_sum_across_candidates(self):
        """The decisive behavioral consequence: when gating two candidates
        whose naive-sum and compound rankings DISAGREE, _fold_transform_single
        must produce oos_alpha values that preserve the COMPOUND ranking
        (low_vol > high_vol), not the naive-sum ranking (high_vol > low_vol).
        A gate driven by the wrong ranking would adopt the WORSE candidate."""
        low_vol = _low_vol_validation_fold()
        high_vol = _high_vol_validation_fold()

        result_low = _fold_transform_single(_build_daily_returns_pct(low_vol))
        result_high = _fold_transform_single(_build_daily_returns_pct(high_vol))

        assert result_low.oos_alpha > result_high.oos_alpha, (
            f"_fold_transform_single ranked high_vol (oos_alpha={result_high.oos_alpha!r}) "
            f"ABOVE low_vol (oos_alpha={result_low.oos_alpha!r}) -- this is the naive-sum "
            "ranking (high_vol's naive sum is higher). A gate consuming this would adopt "
            "the candidate with genuinely WORSE compounded performance. Must agree with "
            "the compound ranking: low_vol > high_vol."
        )

    def test_validation_days_and_purge_integrity_unaffected_by_the_compounding_fix(self):
        """Regression guard: the compounding fix touches oos_alpha ONLY --
        validation_days and purge_integrity_ok must be byte-identical to
        their pre-fix values for a well-formed fold."""
        low_vol = _low_vol_validation_fold()
        result = _fold_transform_single(_build_daily_returns_pct(low_vol))
        assert result.validation_days == 20
        assert result.purge_integrity_ok is True
        assert result.thin_window is False
