"""RED tests -- Retirement Recommender screen + basis (AC-1, AC-2).

Module under test: advisors.retirement_recommender (NEW -- does not exist yet;
every test in this file is expected to fail RED at fixture setup until the
implementer creates the module -- see tests/advisors/test_build_plan_generator_property.py
for the same deferred-import-via-fixture precedent used here).

Contract pinned in .claude/tdd-handoff.md "retirement_recommender.py -- screen":
- screen_correlated_pairs(series_by_symphony) -> list[PairResult] is a THIN
  WRAPPER over advisors.correlation_diagnostic.compute_pairwise_correlations,
  called via MODULE-QUALIFIED access (correlation_diagnostic.compute_pairwise_correlations(...),
  not a `from ... import compute_pairwise_correlations` local binding) so this
  file's monkeypatch on the correlation_diagnostic module itself is
  authoritative regardless of the implementer's exact import statement --
  mirrors the house convention already used throughout this codebase
  (app.py -> database.<fn>() module-qualified calls, patched via
  monkeypatch.setattr(db_module, ...) in every route test in tests/app/).
- Filters to correlation >= CORRELATION_SCREEN_THRESHOLD (0.65, plan-pinned
  literal -- AC-1 fixes this value explicitly, so it is NOT a hardcoded
  producer-computed value, it is the spec).
- A pair whose PairResult.correlation is None is never a screen hit.
- build_recommendations(*, db_file=None, days=...) sources the CONTINUOUS bot
  series via analytics.get_symphony_bot_and_held_daily_returns(...)[1] (AC-2)
  -- NEVER [2] (held/if-held). This file's basis-pin test is NON-VACUOUS: it
  seeds shadow_history rows where bot and held are engineered to disagree on
  correlation sign, so a wrong [1]-vs-[2] read flips the screen outcome.
"""

from __future__ import annotations

import math

import pytest

import advisors.correlation_diagnostic as correlation_diagnostic
from tests.advisors._retirement_recommender_reference import (
    seed_state_db,
    trading_days,
)


@pytest.fixture(scope="module")
def rr():
    import advisors.retirement_recommender as _rr  # noqa: PLC0415

    return _rr


# ---------------------------------------------------------------------------
# AC-1: screen wrapper -- filtering contract, isolated from real Pearson math
# ---------------------------------------------------------------------------


class TestScreenCorrelatedPairsFiltering:
    def test_pair_above_threshold_is_a_screen_hit(self, rr, monkeypatch):
        fake_pairs = [
            correlation_diagnostic.PairResult(
                sym_a="alpha",
                sym_b="beta",
                n_obs=100,
                correlation=0.70,
                thin_data=False,
                window=("2026-01-01", "2026-06-01"),
            )
        ]
        monkeypatch.setattr(
            correlation_diagnostic, "compute_pairwise_correlations", lambda series: fake_pairs
        )
        hits = rr.screen_correlated_pairs({"alpha": {}, "beta": {}})
        pair_keys = {(p.sym_a, p.sym_b) for p in hits}
        assert ("alpha", "beta") in pair_keys, (
            "A pair with correlation=0.70 (>= CORRELATION_SCREEN_THRESHOLD=0.65) "
            "must be included in the screen result."
        )

    def test_pair_below_threshold_is_excluded(self, rr, monkeypatch):
        fake_pairs = [
            correlation_diagnostic.PairResult(
                sym_a="alpha",
                sym_b="beta",
                n_obs=100,
                correlation=0.40,
                thin_data=False,
                window=("2026-01-01", "2026-06-01"),
            )
        ]
        monkeypatch.setattr(
            correlation_diagnostic, "compute_pairwise_correlations", lambda series: fake_pairs
        )
        hits = rr.screen_correlated_pairs({"alpha": {}, "beta": {}})
        pair_keys = {(p.sym_a, p.sym_b) for p in hits}
        assert ("alpha", "beta") not in pair_keys, (
            "A pair with correlation=0.40 (< 0.65) must NOT be a screen hit."
        )

    def test_none_correlation_is_excluded_not_a_screen_hit(self, rr, monkeypatch):
        """AC-1: 'A pair whose PairResult.correlation is None ... is NOT a screen
        hit.' A buggy implementation comparing `None >= 0.65` would raise
        TypeError in Python 3 -- this also structurally guards against that."""
        fake_pairs = [
            correlation_diagnostic.PairResult(
                sym_a="alpha",
                sym_b="beta",
                n_obs=1,
                correlation=None,
                thin_data=True,
                window=None,
            )
        ]
        monkeypatch.setattr(
            correlation_diagnostic, "compute_pairwise_correlations", lambda series: fake_pairs
        )
        hits = rr.screen_correlated_pairs({"alpha": {}, "beta": {}})
        pair_keys = {(p.sym_a, p.sym_b) for p in hits}
        assert ("alpha", "beta") not in pair_keys

    def test_boundary_exact_threshold_is_included(self, rr, monkeypatch):
        """AC-1 says '>= 0.65' -- an off-by-one `>` implementation would wrongly
        exclude a pair at exactly the threshold."""
        fake_pairs = [
            correlation_diagnostic.PairResult(
                sym_a="alpha",
                sym_b="beta",
                n_obs=100,
                correlation=0.65,
                thin_data=False,
                window=("2026-01-01", "2026-06-01"),
            )
        ]
        monkeypatch.setattr(
            correlation_diagnostic, "compute_pairwise_correlations", lambda series: fake_pairs
        )
        hits = rr.screen_correlated_pairs({"alpha": {}, "beta": {}})
        pair_keys = {(p.sym_a, p.sym_b) for p in hits}
        assert ("alpha", "beta") in pair_keys, (
            "correlation == CORRELATION_SCREEN_THRESHOLD exactly must be included "
            "(the AC uses >=, not >)."
        )

    def test_screen_threshold_constant_is_pinned_to_065(self, rr):
        """AC-1 pins this exact value in the plan -- not a producer-computed
        value, the spec literal itself."""
        assert pytest.approx(0.65) == rr.CORRELATION_SCREEN_THRESHOLD

    def test_multiple_symphonies_yields_only_pairs_meeting_threshold(self, rr, monkeypatch):
        """3 symphonies -> C(3,2)=3 candidate pairs from correlation_diagnostic;
        only those >= threshold survive the screen."""
        fake_pairs = [
            correlation_diagnostic.PairResult(
                sym_a="a",
                sym_b="b",
                n_obs=100,
                correlation=0.90,
                thin_data=False,
                window=("2026-01-01", "2026-06-01"),
            ),
            correlation_diagnostic.PairResult(
                sym_a="a",
                sym_b="c",
                n_obs=100,
                correlation=0.10,
                thin_data=False,
                window=("2026-01-01", "2026-06-01"),
            ),
            correlation_diagnostic.PairResult(
                sym_a="b",
                sym_b="c",
                n_obs=100,
                correlation=None,
                thin_data=True,
                window=None,
            ),
        ]
        monkeypatch.setattr(
            correlation_diagnostic, "compute_pairwise_correlations", lambda series: fake_pairs
        )
        hits = rr.screen_correlated_pairs({"a": {}, "b": {}, "c": {}})
        pair_keys = {(p.sym_a, p.sym_b) for p in hits}
        assert pair_keys == {("a", "b")}


# ---------------------------------------------------------------------------
# AC-2: basis pin -- build_recommendations MUST read the bot series (index
# [1]), never the held/if-held series (index [2]). Non-vacuous: bot and held
# disagree on correlation SIGN, so a [1]-vs-[2] swap flips the screen outcome.
# ---------------------------------------------------------------------------


class TestBasisPinBotNotHeld:
    def test_screen_hit_derives_from_bot_series_not_held_series(self, rr, tmp_path, monkeypatch):
        """Two symphonies whose BOT (shadow_return) series are a near-perfect
        POSITIVE linear scaling of each other (r ~ 0.999 -- comfortably clears
        the screen threshold, the uncertainty gate's CI, AND the structural-
        redundancy gate, since every sub-window of a near-perfect linear
        relationship is itself near-perfectly correlated), while their HELD
        (current_return) series over the SAME days are a near-perfect NEGATIVE
        scaling (r ~ -0.999 -- would never screen-hit at all, since
        correlation_diagnostic's screen only admits correlation >= 0.65).

        This makes the test NON-VACUOUS in the strongest way available: if
        build_recommendations read the HELD series instead of BOT (the AC-2
        inversion trap), the pair would never even reach the screen, and
        build_recommendations would return an EMPTY list -- not just a
        differently-signed one. A correct (bot-basis) implementation MUST
        produce at least one recommendation here; a basis-inverted one
        produces zero. n=200 days with near-1.0 correlation is deliberately
        oversized so no plausible MIN_OBS_FLOOR/STRESS_MIN_OBS/CI-width
        concern can confound the result -- the ONLY thing that can make this
        fixture yield zero recommendations is a wrong basis read.
        """
        n = 200
        days = trading_days(n)
        # Linear ramp, not a sine wave -- review-response cycle 3 F3 fallout:
        # see test_retirement_recommender_composite.py's _fleet_scope_series
        # docstring for the full derivation.
        bot_a = [-0.10 + 0.20 * i / (n - 1) for i in range(n)]
        bot_b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(bot_a)]
        held_b = [-0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(bot_a)]

        series = {
            "sym-a": {d: (bot_a[i], bot_a[i]) for i, d in enumerate(days)},
            "sym-b": {d: (bot_b[i], held_b[i]) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)

        # Fixture sanity, computed independently of retirement_recommender:
        bot_pairs = correlation_diagnostic.compute_pairwise_correlations(
            {sym: {d: v[0] for d, v in day_map.items()} for sym, day_map in series.items()}
        )
        held_pairs = correlation_diagnostic.compute_pairwise_correlations(
            {sym: {d: v[1] for d, v in day_map.items()} for sym, day_map in series.items()}
        )
        assert bot_pairs[0].correlation >= 0.65, (
            "fixture sanity: bot-basis correlation must screen-hit"
        )
        assert held_pairs[0].correlation <= -0.65, (
            "fixture sanity: held-basis correlation must be strongly negative"
        )

        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert len(recs) >= 1, (
            "build_recommendations returned ZERO recommendations for a pair "
            "whose BOT series are near-perfectly positively correlated "
            "(r~0.999, comfortably clearing every gate). The only way this "
            "fixture yields zero is if the HELD (if-held/current_return, "
            "index [2]) series was read instead of BOT (shadow_return, index "
            "[1]) -- the AC-2 basis-pin defect this test exists to catch."
        )
        for rec in recs:
            corr = _rec_field(rec, "correlation")
            if corr is not None:
                assert corr > 0, (
                    f"A recommendation reported correlation={corr!r} -- must be "
                    "positive (the BOT-basis sign), never negative (the "
                    "HELD-basis sign)."
                )

    def test_basis_label_names_the_actual_traded_series(self, rr, tmp_path, monkeypatch):
        """AC-8 requires a basis_label field on every persisted recommendation.
        This test locks its semantic content (not exact wording): it must
        reference the actual-traded/bot basis, never say 'held' or 'if-held'."""
        n = 200
        days = trading_days(n)
        # Linear ramp, not a sine wave -- review-response cycle 3 F3 fallout:
        # see test_retirement_recommender_composite.py's _fleet_scope_series
        # docstring for the full derivation.
        bot_a = [-0.10 + 0.20 * i / (n - 1) for i in range(n)]
        bot_b = [0.95 * v + 0.001 * ((i % 3) - 1) for i, v in enumerate(bot_a)]
        series = {
            "sym-a": {d: (bot_a[i], 0.0) for i, d in enumerate(days)},
            "sym-b": {d: (bot_b[i], 0.0) for i, d in enumerate(days)},
        }
        db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=series)
        recs = rr.build_recommendations(db_file=db_file, days=None)
        assert len(recs) >= 1, (
            "fixture sanity: this near-perfectly-correlated bot pair should screen-hit and clear all gates"
        )
        for rec in recs:
            label = _rec_field(rec, "basis_label")
            if label is not None:
                lowered = str(label).lower()
                assert "held" not in lowered.replace("if-held", "").replace("held-out", ""), (
                    f"basis_label={label!r} must not claim a held/if-held basis"
                )


def _rec_field(rec, name):
    """Recommendation items may be a dataclass or a dict -- tolerate either
    representation while still pinning the field's presence/value."""
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
