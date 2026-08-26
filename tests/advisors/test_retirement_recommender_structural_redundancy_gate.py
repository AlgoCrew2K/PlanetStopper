"""RED tests -- Retirement Recommender structural-redundancy gate (AC-6).

Module under test: advisors.retirement_recommender (NEW).

Per the PM's cycle mandate, THIS FILE'S calm-only-collapses-under-stress case
is the single most important test in the suite -- it guards the exact defect
Phase-1 rejected (a plain calm-regime correlation point estimate over-prunes
crash-diversification). Do not weaken it.

Contract pinned in .claude/tdd-handoff.md "retirement_recommender.py -- gates":
- evaluate_structural_redundancy_gate(pair, stressed_corr: float | None,
  holdings_overlap: float | None) -> GateVerdict (`.passed`, `.reason`).
  Passes iff stressed_corr is not None AND stressed_corr >=
  STRESS_REDUNDANCY_THRESHOLD. Fails closed when stressed_corr is None (the
  orchestrator passes None both when the stress sub-window has fewer than
  STRESS_MIN_OBS aligned days -- "too thin to estimate" -- and when the
  stress-window Pearson r is itself undefined/zero-variance, mirroring
  PairResult.correlation's own None convention). holdings_overlap is
  CORROBORATING evidence only (recorded, never a blocking or rescuing input by
  itself) -- this file asserts that invariant explicitly.
- Test-writer architectural ruling (the plan's Architecture list does not name
  a stress-window-selection function, but AC-6's orchestrator must have one):
  the stressed sub-window = the top ceil(STRESS_WINDOW_FRACTION * n_aligned)
  aligned days ranked by max(|return_a|, |return_b|) descending; its
  correlation is computed with the same Pearson-r-or-None convention as
  correlation_diagnostic._pearson_r. This file's end-to-end fixture is built
  so the "stress" days are unambiguously higher-magnitude than every "calm"
  day (a strict separation, not just an average difference), so top-K-by-
  magnitude selection identifies the same days regardless of the exact K
  (assuming a small-minority STRESS_WINDOW_FRACTION, consistent with genuine
  stress/crash-period sparsity -- see the skip guard below for a fraction
  outside the range this fixture was validated against).
"""

from __future__ import annotations

import math

import pytest

from advisors.correlation_diagnostic import PairResult
from tests.advisors._retirement_recommender_reference import (
    seed_state_db,
    trading_days,
)

# This fixture was numerically validated (see the retirement-recommender-core
# RED cycle scratch calculations) for STRESS_WINDOW_FRACTION in this range.
# A fraction outside it may dilute the "stress" subset with lower-magnitude
# calm days (pulling the calm-only case's stress-subset correlation up) or
# under/over-select relative to the intended split -- Pearson correlation
# math makes a large-minority, high-magnitude-and-anti/uncorrelated stress
# block mathematically incompatible with the full-window pair remaining a
# screen hit (see the handoff's "Structural-redundancy fixture" note).
_FRACTION_RANGE_VALIDATED_FOR = (0.01, 0.15)


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


def _pair(correlation, n_obs=265, sym_a="alpha", sym_b="beta"):
    return PairResult(
        sym_a=sym_a,
        sym_b=sym_b,
        n_obs=n_obs,
        correlation=correlation,
        thin_data=False,
        window=("2026-01-01", "2026-12-31"),
    )


def _pearson(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy)


# ---------------------------------------------------------------------------
# Pure gate unit tests: stressed_corr supplied directly, no time-series needed
# ---------------------------------------------------------------------------


class TestStructuralRedundancyGatePure:
    def test_stressed_corr_above_threshold_passes(self, rr):
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90), stressed_corr=rr.STRESS_REDUNDANCY_THRESHOLD + 0.10, holdings_overlap=None
        )
        assert verdict.passed is True

    def test_stressed_corr_below_threshold_fails(self, rr):
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90), stressed_corr=rr.STRESS_REDUNDANCY_THRESHOLD - 0.10, holdings_overlap=None
        )
        assert verdict.passed is False

    def test_stressed_corr_none_fails_closed(self, rr):
        """None represents 'too thin to estimate' OR 'undefined (zero
        variance)' -- either way, fail-closed, never treated as a pass."""
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90), stressed_corr=None, holdings_overlap=None
        )
        assert verdict.passed is False

    def test_holdings_overlap_does_not_rescue_a_failing_stressed_corr(self, rr):
        """holdings_overlap is corroborating, never blocking or rescuing on
        its own -- a high holdings overlap must not flip a below-threshold
        stressed_corr into a pass."""
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90),
            stressed_corr=rr.STRESS_REDUNDANCY_THRESHOLD - 0.10,
            holdings_overlap=1.0,
        )
        assert verdict.passed is False

    def test_holdings_overlap_absent_does_not_block_a_passing_stressed_corr(self, rr):
        """Off-hours / flat-market degradation: logic_holdings empty ->
        holdings_overlap=None must NOT block an otherwise-passing gate."""
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90), stressed_corr=rr.STRESS_REDUNDANCY_THRESHOLD + 0.10, holdings_overlap=None
        )
        assert verdict.passed is True

    def test_holdings_overlap_low_does_not_block_a_passing_stressed_corr(self, rr):
        """A LOW holdings overlap must also not veto an otherwise-passing
        gate -- holdings-overlap is corroborating evidence, not a hard
        condition, per the plan's own wording."""
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90), stressed_corr=rr.STRESS_REDUNDANCY_THRESHOLD + 0.10, holdings_overlap=0.0
        )
        assert verdict.passed is True


# ---------------------------------------------------------------------------
# End-to-end: the calm-only-collapses-under-stress case (THE load-bearing
# test) vs the genuinely-cross-regime-redundant case, via build_recommendations
# over a real seeded shadow_history DB.
# ---------------------------------------------------------------------------


def _build_calm_block(calm_n: int) -> tuple[list[float], list[float]]:
    calm_amp = 0.1
    calm_a = [calm_amp * math.sin(i * 0.4) + 0.05 * calm_amp * ((i % 7) - 3) for i in range(calm_n)]
    calm_b = [0.95 * v + 0.02 * calm_amp * ((i % 5) - 2) for i, v in enumerate(calm_a)]
    return calm_a, calm_b


def _build_stress_block_calm_only(
    stress_n: int, stress_amp: float
) -> tuple[list[float], list[float]]:
    """Near-zero-correlated stress days -- the sibling decorrelates during
    stress (realistic: a genuine crash-diversification sibling need not
    invert to r=-1, just stop being redundant)."""
    stress_a = [stress_amp * math.sin(i * 0.9) for i in range(stress_n)]
    stress_b = [stress_amp * math.cos(i * 1.7 + 0.4) for i in range(stress_n)]
    return stress_a, stress_b


def _build_stress_block_redundant(
    stress_n: int, stress_amp: float
) -> tuple[list[float], list[float]]:
    """Strongly co-moving stress days -- genuine cross-regime redundancy."""
    stress_a = [stress_amp * math.sin(i * 0.9) for i in range(stress_n)]
    stress_b = [0.92 * v + 0.03 * stress_amp * ((i % 3) - 1) for i, v in enumerate(stress_a)]
    return stress_a, stress_b


class TestStructuralRedundancyGateEndToEnd:
    _CALM_N = 250
    _STRESS_N = 15
    _STRESS_AMP = 0.23  # ~2x the calm block's peak magnitude -- see module docstring

    def _skip_if_fraction_outside_validated_range(self, rr):
        frac = getattr(rr, "STRESS_WINDOW_FRACTION", None)
        if frac is None:
            pytest.fail("retirement_recommender.STRESS_WINDOW_FRACTION is not defined")
        lo, hi = _FRACTION_RANGE_VALIDATED_FOR
        if not (lo <= frac <= hi):
            pytest.skip(
                f"STRESS_WINDOW_FRACTION={frac} is outside the range this fixture was "
                f"numerically validated for ({lo}-{hi}); see the module docstring's "
                "Pearson-correlation-math note before widening this fixture."
            )

    def test_calm_only_pair_yields_no_recommendation(self, rr, tmp_path, monkeypatch):
        """THE load-bearing test. Full-window correlation ~0.75 (screen hit,
        uncertainty gate also passes -- ci_lower ~0.69 at n=265), but the
        stress sub-window (the 15 highest-magnitude days, ~2x the calm peak)
        is near-zero correlated (~-0.02). A plain point-estimate-only screen
        would wrongly recommend retirement here; the structural-redundancy
        gate must withhold it."""
        self._skip_if_fraction_outside_validated_range(rr)

        calm_a, calm_b = _build_calm_block(self._CALM_N)
        stress_a, stress_b = _build_stress_block_calm_only(self._STRESS_N, self._STRESS_AMP)
        full_a, full_b = calm_a + stress_a, calm_b + stress_b

        full_corr = _pearson(full_a, full_b)
        stress_corr = _pearson(stress_a, stress_b)
        assert full_corr >= 0.65, "fixture sanity: full-window correlation must screen-hit"
        assert stress_corr < rr.STRESS_REDUNDANCY_THRESHOLD, (
            "fixture sanity: stress-subset correlation must be below the "
            "structural-redundancy threshold"
        )

        days = trading_days(len(full_a))
        series = {
            "candidate-sym": {d: (full_a[i], 0.0) for i, d in enumerate(days)},
            "sibling-sym": {d: (full_b[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)

        recs = rr.build_recommendations(db_file=db_file, days=None)
        candidate_ids = {_rec_field(r, "candidate_id") for r in recs}
        assert "candidate-sym" not in candidate_ids and "sibling-sym" not in candidate_ids, (
            f"A calm-only pair (full_corr={full_corr:.3f}, stress_corr="
            f"{stress_corr:.3f}) produced a recommendation {recs!r} -- the "
            "structural-redundancy gate must withhold it (crash-diversification "
            "protection, the exact defect Phase-1 rejected)."
        )

    def test_genuinely_cross_regime_redundant_pair_yields_a_recommendation(
        self, rr, tmp_path, monkeypatch
    ):
        """Mirror case: BOTH the calm and stress sub-windows are strongly
        correlated -- a genuinely redundant pair. Must pass the structural
        gate (other gates permitting) and produce a recommendation for the
        lower-composite member."""
        self._skip_if_fraction_outside_validated_range(rr)

        calm_a, calm_b = _build_calm_block(self._CALM_N)
        stress_a, stress_b = _build_stress_block_redundant(self._STRESS_N, self._STRESS_AMP)
        full_a, full_b = calm_a + stress_a, calm_b + stress_b

        full_corr = _pearson(full_a, full_b)
        stress_corr = _pearson(stress_a, stress_b)
        assert full_corr >= 0.65
        assert stress_corr >= rr.STRESS_REDUNDANCY_THRESHOLD, (
            "fixture sanity: stress-subset correlation must clear the "
            "structural-redundancy threshold in the genuinely-redundant case"
        )

        # Give the two symphonies different scale so their CAGR/composite
        # differ -- otherwise the pair could tie down to the lexical tiebreak,
        # which is fine, but an explicit performance gap makes the assertion
        # about WHICH one is the candidate meaningful rather than incidental.
        #
        # Naming (verified empirically via analytics.compute_quantstats_metrics,
        # not assumed): scaling a return series DOWN by a positive factor <1
        # shrinks both gains AND losses/drawdowns. Sharpe/Sortino are
        # ratio-based and stay ~scale-invariant, but max_drawdown improves
        # (shrinks toward zero) and Calmar improves too -- so the
        # SCALED-DOWN series ends up with a materially WORSE (lower) raw
        # CAGR but a BETTER Sortino/max_drawdown/Calmar than the unscaled
        # one; only Sharpe stays roughly tied. Net composite (CAGR-dominant
        # weight 0.40 vs the other four's combined 0.60): the unscaled,
        # bigger-swings series is the one with the LOWER composite -- it
        # loses on 4-of-5 metrics despite winning the single highest-weight
        # one. Named "volatile-sym"/"calmer-sym" rather than "strong"/"weak"
        # so the names describe the actual construction (raw swing
        # magnitude), not a performance conclusion this fixture doesn't
        # actually verify by name alone -- prior "strong-sym"/"weak-sym"
        # naming asserted the OPPOSITE of the true outcome (quant-code-
        # reviewer Finding 2 review-response: caught before commit, the
        # original comment's "materially worse CAGR/Sharpe/etc." claim for
        # the scaled-down series was only true for CAGR, not the other 4).
        days = trading_days(len(full_a))
        scale = 0.4  # shrinks gains AND losses/drawdowns proportionally
        series = {
            "volatile-sym": {d: (full_a[i], 0.0) for i, d in enumerate(days)},
            "calmer-sym": {d: (full_b[i] * scale, 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)

        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert len(recs) >= 1, (
            f"A genuinely cross-regime-redundant pair (full_corr={full_corr:.3f}, "
            f"stress_corr={stress_corr:.3f}) produced no recommendation at all -- "
            "expected the structural-redundancy AND uncertainty gates to both "
            "pass for this fixture."
        )
        candidate_ids = {_rec_field(r, "candidate_id") for r in recs}
        assert candidate_ids == {"volatile-sym"}, (
            f"expected 'volatile-sym' (the unscaled, bigger-swings series -- "
            f"empirically the LOWER-composite member: it loses on 4 of 5 "
            f"metrics -- sortino/max_drawdown/calmar to 'calmer-sym', with "
            f"sharpe ~tied -- despite winning the single highest-weight CAGR "
            f"metric) to be the sole retirement candidate, got "
            f"{candidate_ids!r}. A bare subset check (quant-code-reviewer "
            "Finding 2) doesn't actually verify WHICH symphony was picked."
        )

    def test_stress_window_too_thin_fails_closed(self, rr):
        """AC-6 edge case: stress sub-window has fewer than STRESS_MIN_OBS
        aligned days -- the gate must fail-closed (stressed_corr=None is the
        orchestrator's own signal for this, per this file's architectural
        ruling; tested directly at the pure-gate level since it does not
        require rebuilding a full time-series fixture)."""
        verdict = rr.evaluate_structural_redundancy_gate(
            _pair(0.90), stressed_corr=None, holdings_overlap=None
        )
        assert verdict.passed is False


def _rec_field(rec, name):
    if isinstance(rec, dict):
        if name in rec:
            return rec[name]
        raw = rec.get("raw_response")
        if isinstance(raw, dict):
            return raw.get(name)
        return None
    if hasattr(rec, name):
        return getattr(rec, name)
    raw = getattr(rec, "raw_response", None)
    if isinstance(raw, dict):
        return raw.get(name)
    return None
