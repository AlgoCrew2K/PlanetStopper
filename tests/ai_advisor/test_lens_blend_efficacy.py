"""RED tests — Workstream D: lens-blend efficacy (advisors/asset_swap_engine.py
_apply_lens_blend, lines 372-433).

THE BUG (mathematically proven, not just fixture-observed): the current blend
computes

    blended_key[i] = position[i] - LENS_BLEND_WEIGHT * normalised_lens_mean[i]

with LENS_BLEND_WEIGHT = 0.25 and normalised_lens_mean in [0, 1]. For ANY two
adjacent candidates at positions i and i+1:

    blended_key[i+1] - blended_key[i]
        = 1 - 0.25 * (lens[i+1] - lens[i])
        >= 1 - 0.25 * 1   (since lens values are bounded to [0, 1])
        = 0.75 > 0

blended_key[i+1] is ALWAYS strictly greater than blended_key[i], no matter how
extreme the lens differential -- the integer position gap (>= 1) structurally
dominates the maximum possible lens contribution (0.25). Sorting ascending by
blended_key therefore NEVER changes the input order, for ANY candidate list,
ANY lens_scores. The lens blend is mathematically inert.

AC-D1: "combines lens evidence with the CONTINUOUS primary score (normalized
per-objective) BEFORE any rank discretization. It must NOT sort on the integer
enumerate() position."

AC-D2 (invariant): "A lens-favored candidate whose primary-score gap to its
neighbor is SMALL CAN move up >= 1 rank; a candidate differing by a LARGE
primary margin CANNOT be inverted by lens evidence (supporting evidence only,
never override)."

AC-D3: "The downstream BHY-FDR gate (evaluate_candidate_batch) output is
unchanged for a fixed candidate set (pre-backtest reordering changes only
try-order, not FDR math)."

AC-D4 (RED-first): the pre-existing test_lens_scores_reranks_candidates
(tests/ai_advisor/test_cycle3_lens_informed_swaps.py) does NOT actually assert
re-ranking -- its flat_corr fixture gives every candidate an IDENTICAL primary
score, so the pre-blend "sort" already places TLT ahead of SHY (input order:
BND, TLT, SHY) for reasons having nothing to do with the lens blend; a stub
that ignores lens_scores entirely would also pass it. The tests below use a
SMALL-but-nonzero primary gap so the win can ONLY come from the lens blend.
"""

from __future__ import annotations

import random

import pytest

# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_engine():
    import advisors.asset_swap_engine as engine

    return engine


# ===========================================================================
# Direct unit tests on _apply_lens_blend — full control over "score"/position.
# ===========================================================================


class TestApplyLensBlendUsesContinuousScoreNotPosition:
    def test_small_primary_gap_candidate_moves_up_with_strong_lens_favor(self):
        """AC-D1/AC-D2/AC-D4: a razor-thin primary-score gap (0.10 vs 0.1001) with an
        extreme lens differential (0.0 vs 1.0) must invert the order -- the trailing
        candidate (near-tied on primary, strongly lens-favored) ranks FIRST.

        This is impossible under the current position-based design (proven above:
        blended_key[1] - blended_key[0] >= 0.75 > 0 always) -- RED until the blend
        uses the continuous score gap instead of the integer position gap.
        """
        engine = _import_engine()
        candidates = [
            {"ticker": "LEADER", "score": 0.10},
            {"ticker": "TRAILER", "score": 0.1001},
        ]
        lens_scores = {
            "LEADER": {"sentiment": 0.0},
            "TRAILER": {"sentiment": 1.0},
        }

        result = engine._apply_lens_blend(candidates, lens_scores)
        tickers = [c["ticker"] for c in result]

        assert tickers[0] == "TRAILER", (
            f"With a near-zero primary-score gap (0.10 vs 0.1001) and an extreme lens "
            f"differential (LEADER=0.0, TRAILER=1.0), TRAILER must move to rank 0.\n"
            f"  Got order: {tickers!r}\n"
            f"  Position-based blending cannot do this (position gap 1 >= max lens "
            f"contribution 0.25) -- the blend must use the continuous 'score' field."
        )

    def test_large_primary_margin_cannot_be_inverted_by_extreme_lens_favor(self):
        """AC-D2 invariant ('supporting evidence only, never override'): a large,
        unambiguous primary-score margin (0.05 vs 0.95) must NOT be inverted even by
        the most extreme possible lens differential (0.0 vs 1.0). This must hold both
        before AND after the fix -- lens is a nudge, never a veto of a strong primary
        signal."""
        engine = _import_engine()
        candidates = [
            {"ticker": "STRONG_PRIMARY", "score": 0.05},
            {"ticker": "WEAK_PRIMARY", "score": 0.95},
        ]
        lens_scores = {
            "STRONG_PRIMARY": {"sentiment": 0.0},
            "WEAK_PRIMARY": {"sentiment": 1.0},
        }

        result = engine._apply_lens_blend(candidates, lens_scores)
        tickers = [c["ticker"] for c in result]

        assert tickers[0] == "STRONG_PRIMARY", (
            f"A large primary-score margin (0.05 vs 0.95) must NOT be inverted by "
            f"extreme lens evidence (AC-D2: 'supporting evidence only, never "
            f"override'). Got order: {tickers!r}"
        )

    def test_moderate_gap_three_candidates_lens_reorders_only_the_near_tie(self):
        """Three candidates: two are near-tied on primary score (should be
        lens-orderable), one has a commanding lead (must stay first regardless of
        lens). Exercises both halves of AC-D2 in one fixture."""
        engine = _import_engine()
        candidates = [
            {"ticker": "CHAMPION", "score": 0.01},  # commanding primary lead
            {"ticker": "TIED_A", "score": 0.50},
            {"ticker": "TIED_B", "score": 0.501},  # near-tied with TIED_A
        ]
        lens_scores = {
            "CHAMPION": {"sentiment": 0.0},  # even zero lens can't dislodge it
            "TIED_A": {"sentiment": 0.0},
            "TIED_B": {"sentiment": 1.0},  # strongly favored -> should pass TIED_A
        }

        result = engine._apply_lens_blend(candidates, lens_scores)
        tickers = [c["ticker"] for c in result]

        assert tickers[0] == "CHAMPION", (
            f"CHAMPION's commanding primary lead must survive any lens evidence; "
            f"got order {tickers!r}"
        )
        assert tickers.index("TIED_B") < tickers.index("TIED_A"), (
            f"TIED_B (near-tied primary score, strong lens favor) must rank ahead of "
            f"TIED_A (near-tied primary score, zero lens); got order {tickers!r}"
        )

    def test_lens_blend_preserves_full_candidate_set(self):
        """No candidate may be dropped or duplicated by the blend, regardless of the
        lens_scores content -- lens scoring re-ranks, it never eliminates (that is the
        gate's job, per the module docstring)."""
        engine = _import_engine()
        candidates = [
            {"ticker": "A", "score": 0.1},
            {"ticker": "B", "score": 0.2},
            {"ticker": "C", "score": 0.3},
            {"ticker": "D", "score": 0.4},
        ]
        lens_scores = {"A": {"x": 1.0}, "C": {"x": 1.0}}

        result = engine._apply_lens_blend(candidates, lens_scores)

        assert {c["ticker"] for c in result} == {"A", "B", "C", "D"}, (
            f"_apply_lens_blend must preserve the full candidate set; got "
            f"{[c['ticker'] for c in result]!r}"
        )
        assert len(result) == 4, f"Expected 4 candidates, got {len(result)}"

    def test_lens_scores_none_or_empty_returns_input_order_unchanged(self):
        """Backward-compat: None or {} lens_scores must be a true no-op (byte-identical
        input order) -- this must hold both before AND after the AC-D1 fix."""
        engine = _import_engine()
        candidates = [
            {"ticker": "X", "score": 0.1},
            {"ticker": "Y", "score": 0.2},
            {"ticker": "Z", "score": 0.3},
        ]

        result_none = engine._apply_lens_blend(candidates, None)
        result_empty = engine._apply_lens_blend(candidates, {})

        assert [c["ticker"] for c in result_none] == ["X", "Y", "Z"]
        assert [c["ticker"] for c in result_empty] == ["X", "Y", "Z"]

    def test_lens_blend_weight_is_a_named_constant_in_zero_one_range(self):
        """No-magic-numbers guard: LENS_BLEND_WEIGHT must remain a named module
        constant with a sane (0, 1] bound (regression -- must survive the AC-D1 fix,
        whatever the new formula turns out to be)."""
        engine = _import_engine()

        assert hasattr(engine, "LENS_BLEND_WEIGHT"), (
            "advisors.asset_swap_engine must expose a named LENS_BLEND_WEIGHT constant "
            "(no magic numbers in the blend formula)."
        )
        weight = engine.LENS_BLEND_WEIGHT
        assert isinstance(weight, float) and 0.0 < weight <= 1.0, (
            f"LENS_BLEND_WEIGHT must be a float in (0, 1]; got {weight!r}"
        )


# ===========================================================================
# End-to-end through generate_objective_directed_candidates (production path)
# ===========================================================================


class TestGenerateObjectiveDirectedCandidatesLensReranking:
    def test_reduce_correlation_small_gap_candidate_reranks_with_lens(self):
        """Two candidates with near-identical (but not tied) correlation to the
        target series; lens strongly favors the marginally-worse one. Production
        callers (suggest_swaps) go through this function, not _apply_lens_blend
        directly -- this proves the fix is reachable end-to-end."""
        engine = _import_engine()
        obj = engine.SwapObjective(
            objective_type="reduce_correlation", target_pair=("SPY", "OTHER"), measured_value=0.9
        )

        # SPY series and two near-twin candidate series (BND, TLT) whose correlation
        # to SPY differs only slightly, plus a clearly-uncorrelated third (SHY) to
        # keep the fixture non-degenerate.
        rng = random.Random(7)
        spy_series = [rng.gauss(0.0, 1.0) for _ in range(40)]
        # BND: highly correlated with SPY, but slightly LESS so than TLT (more noise
        # dilutes the linear relationship) -> BND ranks first (lower abs corr = better
        # for reduce_correlation) at baseline, by a small (~0.005) margin.
        # Verified numerically (see PR discussion): |corr(SPY,BND)|=0.9930 vs
        # |corr(SPY,TLT)|=0.9982 for this seed/noise combination.
        bnd_series = [v + rng.gauss(0.0, 0.09) for v in spy_series]
        # TLT: marginally MORE correlated than BND -> ranks second at baseline, by a
        # small primary-score gap (the AC-D2 "small gap -> lens CAN move it" case).
        tlt_series = [v + rng.gauss(0.0, 0.05) for v in spy_series]
        # SHY: near-zero correlation (unambiguous, must not be disturbed).
        shy_series = [rng.gauss(0.0, 1.0) for _ in range(40)]

        correlation_data = {
            "SPY": spy_series,
            "BND": bnd_series,
            "TLT": tlt_series,
            "SHY": shy_series,
        }
        lens_scores = {
            "BND": {"sentiment": 0.0},
            "TLT": {"sentiment": 1.0},  # strongly favored despite the (tiny) worse corr
        }

        baseline = engine.generate_objective_directed_candidates(
            symphony_id="sym-lens-rerank",
            objective=obj,
            correlation_data=correlation_data,
            available_assets=["BND", "TLT", "SHY"],
            lens_scores=None,
        )
        baseline_tickers = [c["ticker"] for c in baseline]

        blended = engine.generate_objective_directed_candidates(
            symphony_id="sym-lens-rerank",
            objective=obj,
            correlation_data=correlation_data,
            available_assets=["BND", "TLT", "SHY"],
            lens_scores=lens_scores,
        )
        blended_tickers = [c["ticker"] for c in blended]

        assert blended_tickers != baseline_tickers, (
            f"Providing lens_scores with a near-tied primary gap must change the "
            f"ranking relative to the no-lens baseline.\n"
            f"  baseline (no lens): {baseline_tickers!r}\n"
            f"  blended (with lens): {blended_tickers!r}\n"
            f"  Both orders are identical -- the blend had no effect end-to-end."
        )


# ===========================================================================
# AC-D3 — the downstream BHY-FDR gate is order-invariant for a fixed set
# ===========================================================================


class TestGateOutputUnchangedByCandidateOrder:
    """AC-D3: reordering the SAME candidate set (as the lens blend now does) must
    not change evaluate_candidate_batch's per-candidate verdicts or survivor set --
    only which try-order they were submitted in."""

    def _make_series(self, seed: int, n: int = 120, mean_pct: float = 0.05) -> list:
        rng = random.Random(seed)
        return [rng.gauss(mean_pct, 0.8) for _ in range(n)]

    def _make_candidates(self):
        import advisors.backtest_gate_engine as gate_engine

        specs = [
            ("cand-strong", 11, 0.35),
            ("cand-weak", 22, -0.05),
            ("cand-mid", 33, 0.15),
            ("cand-noisy", 44, 0.0),
        ]
        return [
            gate_engine.BacktestCandidate(
                candidate_id=cid,
                daily_returns_pct=self._make_series(seed, mean_pct=mean_pct),
                candidate_params={},
                incumbent_params={},
                theory_prior_params={},
            )
            for cid, seed, mean_pct in specs
        ]

    def test_reversed_order_produces_identical_per_candidate_verdicts(self):
        import advisors.backtest_gate_engine as gate_engine

        candidates = self._make_candidates()
        forward = gate_engine.evaluate_candidate_batch(candidates)
        reversed_batch = gate_engine.evaluate_candidate_batch(list(reversed(candidates)))

        forward_by_id = {r.candidate_id: r for r in forward.results}
        reversed_by_id = {r.candidate_id: r for r in reversed_batch.results}

        assert set(forward_by_id) == set(reversed_by_id), (
            "Both orderings must produce results for the identical candidate_id set."
        )
        for cid, fwd_result in forward_by_id.items():
            rev_result = reversed_by_id[cid]
            assert fwd_result.verdict.decision == rev_result.verdict.decision, (
                f"Candidate {cid!r} got decision={fwd_result.verdict.decision!r} in "
                f"forward order but {rev_result.verdict.decision!r} reversed -- the "
                f"BHY-FDR gate must be order-invariant for a fixed candidate set "
                f"(AC-D3)."
            )
            # winner_p_adj is the BHY-adjusted p-value for THIS candidate's own
            # p-value -- must be identical regardless of submission order.
            assert fwd_result.winner_p_adj == pytest.approx(rev_result.winner_p_adj, abs=1e-9), (
                f"Candidate {cid!r} winner_p_adj differs by order: "
                f"forward={fwd_result.winner_p_adj!r} reversed={rev_result.winner_p_adj!r}"
            )

    def test_reversed_order_produces_identical_survivor_set(self):
        import advisors.backtest_gate_engine as gate_engine

        candidates = self._make_candidates()
        forward = gate_engine.evaluate_candidate_batch(candidates)
        reversed_batch = gate_engine.evaluate_candidate_batch(list(reversed(candidates)))

        forward_survivor_ids = {r.candidate_id for r in forward.survivors}
        reversed_survivor_ids = {r.candidate_id for r in reversed_batch.survivors}

        assert forward_survivor_ids == reversed_survivor_ids, (
            f"Survivor set must be order-invariant (AC-D3): forward="
            f"{forward_survivor_ids!r} reversed={reversed_survivor_ids!r}"
        )
