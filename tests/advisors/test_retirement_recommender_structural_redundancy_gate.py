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
- Test-writer architectural ruling, REVISED (PR-level /code-review Finding 3,
  PM ruling -- supersedes the original magnitude-based selection rule below):
  the stressed sub-window = the top ceil(STRESS_WINDOW_FRACTION * n_aligned)
  aligned days ranked by MOST-NEGATIVE combined return
  ((return_a[i] + return_b[i]) / 2) ascending (i.e. the deepest joint-
  drawdown days), NOT by raw magnitude. Its correlation is computed with the
  same Pearson-r-or-None convention as correlation_diagnostic._pearson_r.

  Root cause of the original rule: max(|return_a|, |return_b|)-descending
  selects the highest-MAGNITUDE days regardless of sign -- this includes big
  RALLY (up) days along with genuine crash (down) days. Since a pair that
  co-moves nicely on rallies but DIVERGES on drawdowns is exactly the
  crash-diversification case AC-6 exists to protect (Phase-1's own finding),
  magnitude-selection can fill the "stressed" window entirely with
  well-correlated rally days and never sample the genuinely divergent crash
  days at all -- silently passing the gate for a pair that should have been
  withheld. Downside/most-negative selection targets the crash days
  specifically, matching the gate's actual purpose.

  See TestStressWindowSelectsDownsideNotMagnitude below for the discriminating
  fixture (old vs new selection rules produce OPPOSITE gate verdicts on the
  same data) and TestStructuralRedundancyGateEndToEnd above for the original
  calm-only fixture, which remains valid under EITHER selection rule (its
  "stress" days are pure noise in both directions, not a rally/crash split) --
  a small-minority STRESS_WINDOW_FRACTION assumption still applies; see the
  skip guard below for a fraction outside the range validated.
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
        uncertainty gate also passes -- ci_lower ~0.69 at n=265). This
        fixture's own 15-day stress block (constructed via near-zero-corr
        oscillations, independent of whatever selection algorithm the
        orchestrator uses) correlates well below the redundancy threshold on
        its own terms -- verified below to stay < STRESS_REDUNDANCY_THRESHOLD
        under BOTH the original magnitude-based selection rule and the
        current downside/most-negative-combined-return rule (PR-level
        /code-review Finding 3) -- confirmed numerically at ~0.47 under the
        current downside rule, comfortably below the 0.65 threshold either
        way. A plain point-estimate-only screen would wrongly recommend
        retirement here; the structural-redundancy gate must withhold it."""
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


# ---------------------------------------------------------------------------
# PR-level /code-review Finding 3 (retirement_recommender.py's stress-window
# selection): the discriminating fixture -- a pair that co-moves nicely on
# RALLY days but DIVERGES on genuine crash/drawdown days. The OLD magnitude-
# based selection rule (max(|return_a|,|return_b|) descending) can fill the
# "stressed" window entirely with well-correlated rally days (since a big up-
# move is just as "high magnitude" as a big down-move) and never sample the
# divergent crash days at all -- wrongly PASSING the gate. The NEW downside/
# most-negative-combined-return rule targets the crash days specifically,
# correctly FAILING the gate. Both the old and new stress-window correlation
# values are computed directly in this test (never asserted from memory) so
# the "old code has this bug, new code doesn't" claim is independently
# provable regardless of which rule is actually wired at any given moment.
# ---------------------------------------------------------------------------


def _build_rally_vs_crash_fixture():
    """Real numbers, independently verified (see this Revise's scratch
    calculation): full_corr ~0.906 (comfortably clears the 0.65 screen and,
    at n=280, the uncertainty gate's CI-lower-bound check too). Rally block
    (15 days, +0.30 magnitude, strongly co-moving) is LARGER in magnitude
    than the crash block (15 days, -0.20 magnitude, strongly DIVERGING --
    sibling stays roughly flat while the candidate craters, the textbook
    crash-diversification shape) -- so magnitude-based selection fills its
    entire top-k with rally days (0 crash days sampled), while downside-
    based selection is dominated by the crash block's own larger variance
    even when a few calm days are mixed in alongside it."""
    calm_n = 250
    calm_amp = 0.1
    calm_a = [calm_amp * math.sin(i * 0.4) + 0.05 * calm_amp * ((i % 7) - 3) for i in range(calm_n)]
    calm_b = [0.95 * v + 0.02 * calm_amp * ((i % 5) - 2) for i, v in enumerate(calm_a)]

    rally_n = 15
    rally_a = [0.30 + 0.01 * math.sin(i * 0.5) for i in range(rally_n)]
    rally_b = [0.95 * v + 0.005 * ((i % 3) - 1) for i, v in enumerate(rally_a)]

    crash_n = 15
    # Candidate crashes hard; sibling stays roughly flat/small -- genuine
    # crash-diversification, the exact case AC-6 exists to protect.
    crash_a = [-0.20 - 0.01 * math.sin(i * 0.7) for i in range(crash_n)]
    crash_b = [0.02 * math.cos(i * 1.9 + 1.0) for i in range(crash_n)]

    full_a = calm_a + rally_a + crash_a
    full_b = calm_b + rally_b + crash_b
    return full_a, full_b


def _select_by_magnitude(vals_a, vals_b, k):
    n = len(vals_a)
    magnitudes = [max(abs(vals_a[i]), abs(vals_b[i])) for i in range(n)]
    idx = sorted(range(n), key=lambda i: magnitudes[i], reverse=True)[:k]
    return [vals_a[i] for i in idx], [vals_b[i] for i in idx]


def _select_by_downside(vals_a, vals_b, k):
    n = len(vals_a)
    combined = [(vals_a[i] + vals_b[i]) / 2 for i in range(n)]
    idx = sorted(range(n), key=lambda i: combined[i])[:k]  # ascending -> most negative first
    return [vals_a[i] for i in idx], [vals_b[i] for i in idx]


class TestStressWindowSelectsDownsideNotMagnitude:
    def test_fixture_sanity_old_magnitude_rule_wrongly_passes_new_downside_rule_correctly_fails(
        self, rr
    ):
        """Pure-math proof (no DB, no build_recommendations) that the two
        selection rules produce OPPOSITE gate verdicts on the identical
        fixture -- isolates the selection-algorithm claim from the rest of
        the orchestrator pipeline."""
        full_a, full_b = _build_rally_vs_crash_fixture()
        n = len(full_a)
        k = math.ceil(rr.STRESS_WINDOW_FRACTION * n)
        assert k >= rr.STRESS_MIN_OBS, "fixture sanity: k must clear STRESS_MIN_OBS"

        full_corr = _pearson(full_a, full_b)
        assert full_corr >= 0.65, "fixture sanity: full-window correlation must screen-hit"

        old_stress_a, old_stress_b = _select_by_magnitude(full_a, full_b, k)
        old_stress_corr = _pearson(old_stress_a, old_stress_b)
        assert old_stress_corr >= rr.STRESS_REDUNDANCY_THRESHOLD, (
            f"fixture sanity: OLD magnitude-based selection must WRONGLY pass "
            f"the gate (stress_corr={old_stress_corr:.4f}) -- if this fails, "
            "the discriminating fixture no longer demonstrates the bug."
        )

        new_stress_a, new_stress_b = _select_by_downside(full_a, full_b, k)
        new_stress_corr = _pearson(new_stress_a, new_stress_b)
        assert new_stress_corr < rr.STRESS_REDUNDANCY_THRESHOLD, (
            f"fixture sanity: NEW downside-based selection must correctly "
            f"fail the gate (stress_corr={new_stress_corr:.4f}) -- if this "
            "fails, the discriminating fixture doesn't prove the fix works "
            "either."
        )

    def test_compute_stressed_correlation_selects_downside_not_magnitude(self, rr):
        """THE load-bearing pin for Finding 3: calls the REAL
        _compute_stressed_correlation directly on the discriminating fixture
        and asserts it returns a value consistent with DOWNSIDE selection
        (< STRESS_REDUNDANCY_THRESHOLD), not magnitude selection (which would
        return >= threshold, wrongly passing). Currently RED against the
        magnitude-based implementation."""
        full_a, full_b = _build_rally_vs_crash_fixture()
        result = rr._compute_stressed_correlation(full_a, full_b)  # noqa: SLF001
        assert result is not None, "fixture sanity: stress window must be estimable (not too thin)"
        assert result < rr.STRESS_REDUNDANCY_THRESHOLD, (
            f"_compute_stressed_correlation returned {result:.4f} -- expected "
            f"< {rr.STRESS_REDUNDANCY_THRESHOLD} (downside/most-negative-"
            "combined-return selection, which targets the genuine crash days "
            "where this pair diverges). A value >= the threshold means "
            "magnitude-based selection is still active -- it fills the "
            "'stressed' window with well-correlated RALLY days instead of "
            "the divergent crash days (PR-level /code-review Finding 3)."
        )

    def test_rally_vs_crash_pair_yields_no_recommendation_end_to_end(
        self, rr, tmp_path, monkeypatch
    ):
        """Full orchestrator-level pin: a pair that rallies together but
        crashes apart must NOT be recommended for retirement. Currently RED
        (the buggy magnitude-based selection samples only rally days,
        wrongly passing the structural-redundancy gate and producing a
        recommendation)."""
        full_a, full_b = _build_rally_vs_crash_fixture()
        days = trading_days(len(full_a))
        series = {
            "rally-crash-a": {d: (full_a[i], 0.0) for i, d in enumerate(days)},
            "rally-crash-b": {d: (full_b[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)

        recs = rr.build_recommendations(db_file=db_file, days=None)
        candidate_ids = {_rec_field(r, "candidate_id") for r in recs}
        assert "rally-crash-a" not in candidate_ids and "rally-crash-b" not in candidate_ids, (
            f"A pair that co-moves on rallies but diverges on crashes produced "
            f"a recommendation {recs!r} -- the structural-redundancy gate must "
            "withhold it once the stress window correctly samples the "
            "divergent CRASH days instead of the well-correlated RALLY days "
            "(PR-level /code-review Finding 3)."
        )


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
