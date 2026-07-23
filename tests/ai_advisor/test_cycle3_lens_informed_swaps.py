"""
Cycle 3 — RED: Lens-informed asset-swap candidate ranking.

Scope (BRIEF: .design-handoff/cycle3-lens-swaps/BRIEF.md):
  AC-1  extract_lens_scores helper: given assembled advisor context lens blocks,
        produce {ticker: {lens_name: score}} per-ticker dict. Only available=True
        lenses contribute; available=False lenses are skipped entirely (no fabrication).
  AC-2  generate_objective_directed_candidates gains an OPTIONAL lens_scores param
        (default None → byte-identical behavior, backward-compatible). When provided,
        lens scores blend into existing correlation/variance ranking via named-constant
        weighted blend (LENS_BLEND_WEIGHT). Ranking only; never bypasses the gate.
  AC-3  Gate unchanged: evaluate_candidate_batch (BHY-FDR) still gates all candidates;
        lens scoring affects only pre-gate ranking.
  AC-4  Persistence: _persist_observation raw_response now carries lens_evidence
        ({ticker: {signal, source_lens, confidence}}) and sources (citation dicts).
  AC-5  Per-candidate rationale includes lens evidence + correlation/variance + gate
        verdict. Each candidate stands on own merits; no single-winner verdict.
  AC-6  Safety/contracts: honest-availability (unavailable lens → no score); D-1
        (any error → type(exc).__name__ only); additions/swaps only; advise-only;
        off the live execution path.

Mocking strategy:
  - advisors.backtest_gate_engine.evaluate_candidate_batch: patched for ranking
    tests (AC-1, AC-2) so gate behavior is not conflated with ranking tests.
  - database.insert_advisor_observation: patched for persistence tests (AC-4).
  - No live network calls. No live Claude calls. Math engine NEVER mocked.
  - lens_scores dicts are constructed directly (no live lens fetch).

Adversarial RED intent:
  - AC-1: extract_lens_scores function must NOT exist yet → ImportError / AttributeError.
  - AC-2: generate_objective_directed_candidates must NOT accept lens_scores → TypeError.
  - AC-4: _persist_observation raw_response must NOT contain lens_evidence / sources.
  - AC-5: objective_rationale must NOT reference lens evidence.
  - AC-6: unavailable lens must produce no score (not a test-fail, an invariant test).
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_engine():
    _ensure_repo_on_path()
    return importlib.import_module("advisors.asset_swap_engine")


def _import_ai_advisor():
    _ensure_repo_on_path()
    return importlib.import_module("ai_advisor")


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_ensure_repo_on_path()
from advisors import symphony_schema  # noqa: E402 - path must be ensured first

# A minimal Composer score tree with two holdings. R2-3: rebuilt via the real
# symphony_schema constructors so it satisfies symphony_schema.validate_tree
# -- the old hand-built {"ticker": None, "children": [...]} shape (no "step"
# vocabulary at all) fails the engine's validate_tree guard, now wired
# unconditionally for every swap variant (asset_swap_engine.py's
# _evaluate_single_variant).
_SCORE_TREE = symphony_schema.make_root(
    "TestSymphony",
    "daily",
    [
        symphony_schema.make_weight_equal(
            [symphony_schema.make_asset("SPY"), symphony_schema.make_asset("GLD")]
        )
    ],
)

# Correlation data: return series per ticker.
_CORR_DATA = {
    "SPY": [0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.01, 0.02, -0.02, 0.01],
    "GLD": [-0.01, 0.02, -0.01, 0.01, -0.02, 0.01, 0.02, -0.01, 0.01, -0.02],
    "BND": [0.001, -0.001, 0.001, 0.0, -0.001, 0.001, 0.0, -0.001, 0.001, 0.0],
    "TLT": [0.002, -0.002, 0.001, -0.001, 0.002, -0.001, 0.001, -0.002, 0.001, 0.0],
    "SHY": [0.0005, 0.0, -0.0005, 0.0005, 0.0, -0.0005, 0.0005, 0.0, -0.0005, 0.0],
}

# A lens_scores dict as extract_lens_scores should produce.
# Tickers: BND, TLT, SHY (candidates). Source lenses: sentiment (available), macro (available).
_LENS_SCORES_FIXTURE = {
    "BND": {"sentiment": 0.7, "macro": 0.5},
    "TLT": {"sentiment": 0.4, "macro": 0.8},
    "SHY": {"sentiment": 0.2, "macro": 0.3},
}

# Minimal advisor context as assemble_advisor_context produces for lens tests.
#
# STALE-FIXTURE FIX (2026-07-12, live droplet-DB E2E follow-up): the prior version
# put a fabricated "ticker_scores" key inside sentiment/macro payloads. NO real
# producer ever emits that key (0 grep hits across ai_advisor.py outside this
# fixture/its consumer) -- a parser+fixture co-design failure (Gate-1 fixture-
# provenance FAIL). REAL shapes, verified directly against the producers:
#   technicals.payload (ai_advisor.py:542-552, advisors/lens_technicals.py:265-272):
#     {"ma_posture": {ticker: {above_sma50, above_sma200}}, "breadth": float,
#      "momentum": {ticker: float}}  -- the ONLY real per-ticker signal.
#   sentiment.payload (ai_advisor.py:673-684): {"tone_score", "corpus", "events",
#     "article_count"} -- market-wide, no per-ticker structure.
#   macro.payload (ai_advisor.py around _build_macro_section): {"series": {...}}
#     keyed by FRED series_id, not ticker -- market-wide.
# Three lenses available=True (technicals contributes; sentiment/macro are
# available but market-wide and must contribute NOTHING), two available=False
# (stubs, contribute nothing for the honest-availability reason instead).
_ADVISOR_CONTEXT_WITH_LENSES = {
    "scope": "symphony",
    "symphony_id": "test-sym-001",
    "technicals": {
        "lens": "technicals",
        "available": True,
        "payload": {
            "ma_posture": {
                "BND": {"above_sma50": True, "above_sma200": True},
                "TLT": {"above_sma50": False, "above_sma200": True},
                "SHY": {"above_sma50": True, "above_sma200": False},
            },
            "breadth": 0.5,
            # Real per-ticker 20-day momentum -- UNBOUNDED raw return, not [0, 1].
            "momentum": {"BND": 0.02, "TLT": -0.01, "SHY": 0.005},
        },
        "sources": [
            {
                "title": "Technicals momentum snapshot",
                "url": "https://example.com/technicals",
                "published": "2026-06-01",
                "lens": "technicals",
            },
        ],
    },
    "sentiment": {
        "lens": "sentiment",
        # AVAILABLE but market-wide (no per-ticker structure) -- must contribute
        # NOTHING to any ticker's score, despite being available=True.
        "available": True,
        "payload": {
            "tone_score": 0.30,
            "corpus": [],
            "events": [],
            "article_count": 3,
        },
        "sources": [
            {
                "title": "Bond rally",
                "url": "https://example.com/1",
                "published": "2026-06-01",
                "lens": "sentiment",
            },
        ],
    },
    "macro": {
        "lens": "macro",
        # AVAILABLE but market-wide (FRED series, not per-ticker) -- must
        # contribute NOTHING.
        "available": True,
        "payload": {
            "series": {
                "DGS10": {"label": "10-Year Treasury", "value": "4.25", "date": "2026-06-01"},
            },
        },
        "sources": [
            {
                "title": "10-Yr Treasury",
                "url": "https://fred.stlouisfed.org/series/DGS10",
                "published": "2026-06-01",
                "lens": "macro",
            },
        ],
    },
    "derivatives": {
        "lens": "derivatives",
        "available": False,
        "reason": "derivatives source not connected — cycle-2b deliverable",
        "payload": None,
        "sources": [],
    },
    "fundamentals": {
        "lens": "fundamentals",
        "available": False,
        "reason": "ticker symbol required",
        "payload": None,
        "sources": [],
    },
}

# Advisor context where ALL lenses are unavailable — extract_lens_scores must return {}.
_ADVISOR_CONTEXT_ALL_UNAVAILABLE = {
    "scope": "symphony",
    "symphony_id": "test-sym-002",
    "sentiment": {
        "lens": "sentiment",
        "available": False,
        "reason": "stub",
        "payload": None,
        "sources": [],
    },
    "macro": {
        "lens": "macro",
        "available": False,
        "reason": "stub",
        "payload": None,
        "sources": [],
    },
    "technicals": {
        "lens": "technicals",
        "available": False,
        "reason": "stub",
        "payload": None,
        "sources": [],
    },
    "derivatives": {
        "lens": "derivatives",
        "available": False,
        "reason": "stub",
        "payload": None,
        "sources": [],
    },
    "fundamentals": {
        "lens": "fundamentals",
        "available": False,
        "reason": "stub",
        "payload": None,
        "sources": [],
    },
}


# ---------------------------------------------------------------------------
# AC-1: extract_lens_scores helper
# ---------------------------------------------------------------------------


class TestExtractLensScores:
    """AC-1: extract_lens_scores(context) → {ticker: {lens_name: score}}."""

    def test_function_exists_on_engine_module(self):
        """RED: extract_lens_scores must exist in asset_swap_engine or ai_advisor."""
        engine = _import_engine()
        # Either location is acceptable per BRIEF (ai_advisor.py or asset_swap_engine.py).
        has_engine = hasattr(engine, "extract_lens_scores")
        try:
            ai = _import_ai_advisor()
            has_advisor = hasattr(ai, "extract_lens_scores")
        except Exception:
            has_advisor = False
        assert has_engine or has_advisor, (
            "extract_lens_scores not found in advisors.asset_swap_engine or ai_advisor — "
            "must be added (AC-1)"
        )

    def test_available_lenses_contribute_scores(self):
        """RED: technicals' real per-ticker momentum payload contributes to output.

        STALE-FIXTURE FIX: technicals is the ONLY real per-ticker signal (momentum).
        sentiment/macro are available=True but market-wide -- see
        test_market_wide_available_lenses_produce_no_per_ticker_scores below.
        """
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        result = fn(_ADVISOR_CONTEXT_WITH_LENSES)

        # BND has a real per-ticker momentum value in the technicals block.
        assert "BND" in result, "BND missing from extract_lens_scores output"
        bnd_scores = result["BND"]
        assert isinstance(bnd_scores, dict), "Per-ticker scores must be a dict"
        assert "technicals" in bnd_scores, "technicals (momentum-derived) score missing for BND"

    def test_market_wide_available_lenses_produce_no_per_ticker_scores(self):
        """RED (the live-E2E-caught bug): sentiment and macro are available=True in
        the fixture but are MARKET-WIDE (tone_score scalar / FRED series keyed by
        series_id, not ticker) -- they must honestly contribute NOTHING to any
        ticker's score, never a fabricated per-ticker value derived from a
        market-wide number."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        result = fn(_ADVISOR_CONTEXT_WITH_LENSES)

        assert result, "Expected at least BND/TLT/SHY from the technicals momentum data."
        for ticker, ticker_scores in result.items():
            assert "sentiment" not in ticker_scores, (
                f"sentiment is market-wide (no per-ticker structure) and must NOT "
                f"contribute a score for {ticker!r}; got {ticker_scores!r}"
            )
            assert "macro" not in ticker_scores, (
                f"macro is market-wide (FRED series, no per-ticker structure) and must "
                f"NOT contribute a score for {ticker!r}; got {ticker_scores!r}"
            )

    def test_unavailable_lenses_produce_no_scores(self):
        """RED: available=False lenses must not contribute any scores (AC-6 honest-availability)."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        result = fn(_ADVISOR_CONTEXT_WITH_LENSES)

        # derivatives and fundamentals are unavailable in the fixture — must never appear.
        for ticker_scores in result.values():
            assert "derivatives" not in ticker_scores, (
                "derivatives (unavailable) must not contribute scores"
            )
            assert "fundamentals" not in ticker_scores, (
                "fundamentals (unavailable) must not contribute scores"
            )

    def test_all_unavailable_returns_empty_dict(self):
        """RED: when all lenses are unavailable, result is {} (no fabricated scores)."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        result = fn(_ADVISOR_CONTEXT_ALL_UNAVAILABLE)
        assert result == {}, f"All-unavailable context must return empty dict; got {result!r}"

    def test_returns_dict_of_dicts(self):
        """RED: return type is dict[str, dict] — outer key is ticker, inner is lens→score."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        result = fn(_ADVISOR_CONTEXT_WITH_LENSES)
        assert isinstance(result, dict), "extract_lens_scores must return a dict"
        for ticker, scores in result.items():
            assert isinstance(ticker, str), f"Ticker key must be str, got {type(ticker)}"
            assert isinstance(scores, dict), f"Per-ticker scores must be dict, got {type(scores)}"
            for lens_name, score in scores.items():
                assert isinstance(lens_name, str), f"Lens name must be str, got {type(lens_name)}"
                assert isinstance(score, (int, float)), (
                    f"Score must be numeric, got {type(score)} for {ticker}.{lens_name}"
                )

    def test_missing_lens_keys_in_context_do_not_raise(self):
        """RED: context missing some lens keys must not raise — degrade gracefully."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        sparse_context = {
            "scope": "symphony",
            "technicals": {
                "lens": "technicals",
                "available": True,
                "payload": {"ma_posture": None, "breadth": None, "momentum": {"BND": 0.01}},
                "sources": [],
            },
            # sentiment, macro, derivatives, fundamentals keys absent
        }
        # Must not raise
        result = fn(sparse_context)
        assert isinstance(result, dict)
        assert "BND" in result, (
            "technicals momentum must still contribute even when other lens keys "
            "are entirely absent from the context"
        )


# ---------------------------------------------------------------------------
# Live-E2E follow-up: extract_lens_scores must squash technicals' UNBOUNDED raw
# momentum (~20-day return, roughly ±0.05..0.15 in practice, but not formally
# bounded) onto the [0, 1] favorability scale _apply_lens_blend expects
# (LENS_BLEND_WEIGHT * (mean_lens - _LENS_NEUTRAL_SCORE), neutral=0.5). These
# tests pin the INVARIANT (monotonic, bounded, neutral-at-zero, adversarially
# separated), NOT a specific squashing formula -- same "pin the invariant, not
# the formula" pattern the D-workstream cumulative-gap fix used.
# ---------------------------------------------------------------------------


def _technicals_context(momentum: dict) -> dict:
    """Minimal context with ONLY technicals available, carrying the given
    per-ticker momentum dict — isolates the squashing transform from every
    other lens/extraction concern."""
    return {
        "scope": "symphony",
        "technicals": {
            "lens": "technicals",
            "available": True,
            "payload": {"ma_posture": None, "breadth": None, "momentum": momentum},
            "sources": [],
        },
    }


class TestExtractLensScoresMomentumSquashing:
    """Live-E2E follow-up: technicals.payload.momentum is an UNBOUNDED raw
    return; extract_lens_scores must map it onto [0, 1] before it reaches
    _apply_lens_blend (which treats scores as an already-normalised [0, 1]
    favorability with 0.5 as neutral)."""

    def _extract(self):
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores
        return fn

    def test_extracted_score_is_bounded_to_unit_interval_for_large_momentum(self):
        """A large positive AND a large negative raw momentum value must both
        squash into [0, 1] — the raw value (e.g. 0.50 or -0.50) is far outside
        that range if passed through unmodified."""
        fn = self._extract()
        result = fn(_technicals_context({"BIG_POS": 0.50, "BIG_NEG": -0.50}))

        assert "BIG_POS" in result and "BIG_NEG" in result, (
            f"Both tickers must produce a score; got {result!r}"
        )
        for ticker in ("BIG_POS", "BIG_NEG"):
            score = result[ticker]["technicals"]
            assert 0.0 <= score <= 1.0, (
                f"extract_lens_scores must squash unbounded raw momentum to [0, 1]; "
                f"{ticker} momentum=0.50/-0.50 produced score={score!r} (out of range)."
            )

    def test_zero_momentum_maps_to_approximately_neutral_score(self):
        """Zero momentum (no trend either way) must map to ~0.5 (_LENS_NEUTRAL_SCORE)
        — the same "no evidence / no opinion" value _apply_lens_blend already
        assigns to a ticker absent from lens_scores entirely. A momentum-neutral
        ticker must not silently nudge the blend as if it were lens-favored or
        lens-disfavored."""
        fn = self._extract()
        result = fn(_technicals_context({"FLAT": 0.0}))

        assert "FLAT" in result, f"Expected a score for FLAT; got {result!r}"
        score = result["FLAT"]["technicals"]
        # abs=0.05: the exact squashing formula is the implementer's choice (pinned
        # by invariant, not formula); a tight-but-not-exact tolerance around the
        # documented neutral point (0.5) accommodates minor formula variance while
        # still catching a transform that is clearly NOT neutral-centered at 0.
        assert score == pytest.approx(0.5, abs=0.05), (
            f"Zero momentum must map to ~0.5 (neutral); got {score!r}"
        )

    def test_higher_momentum_maps_to_strictly_higher_score(self):
        """Monotonicity: a strictly increasing momentum sequence must produce a
        strictly increasing (never flat, never inverted) score sequence."""
        fn = self._extract()
        momentum = {
            "M1_LOW": -0.10,
            "M2_MID_LOW": -0.02,
            "M3_ZERO": 0.00,
            "M4_MID_HIGH": 0.05,
            "M5_HIGH": 0.15,
        }
        result = fn(_technicals_context(momentum))

        ordered_tickers = ["M1_LOW", "M2_MID_LOW", "M3_ZERO", "M4_MID_HIGH", "M5_HIGH"]
        scores = [result[t]["technicals"] for t in ordered_tickers]

        for i in range(len(scores) - 1):
            assert scores[i] < scores[i + 1], (
                f"Monotonicity violated: momentum {momentum[ordered_tickers[i]]!r} -> "
                f"score {scores[i]!r} is not strictly less than momentum "
                f"{momentum[ordered_tickers[i + 1]]!r} -> score {scores[i + 1]!r}. "
                f"Full sequence: {list(zip(ordered_tickers, scores, strict=True))!r}"
            )

    def test_large_positive_and_negative_momentum_produce_clearly_separated_scores(self):
        """Adversarial: a degenerate transform that clamps or collapses everything
        near 0.5 would satisfy 'bounded to [0,1]' and even 'monotonic' (weakly)
        while being USELESS for ranking. A large positive vs a large negative
        momentum must land on CLEARLY different sides of the scale (not both
        huddled near neutral)."""
        fn = self._extract()
        result = fn(_technicals_context({"STRONG_UP": 0.15, "STRONG_DOWN": -0.15}))

        up_score = result["STRONG_UP"]["technicals"]
        down_score = result["STRONG_DOWN"]["technicals"]

        assert up_score > down_score, (
            f"Strong positive momentum must score higher than strong negative "
            f"momentum; got up={up_score!r} down={down_score!r}"
        )
        # A degenerate near-constant transform (e.g. always ~0.5 regardless of
        # input) would trivially satisfy monotonicity (non-strict) and bounds but
        # be dead for ranking purposes -- require real separation.
        assert (up_score - down_score) >= 0.2, (
            f"Large positive (0.15) vs large negative (-0.15) momentum must "
            f"produce a clearly separated score gap (>= 0.2 on the [0,1] scale); "
            f"got up={up_score!r} down={down_score!r} (gap={up_score - down_score!r}). "
            f"A near-constant/degenerate squashing transform would fail this."
        )


# ---------------------------------------------------------------------------
# AC-2: generate_objective_directed_candidates — optional lens_scores param
# ---------------------------------------------------------------------------


# TestGenerateObjectiveDirectedCandidatesLensParam RETIRED (R2-3, 2026-07-14):
# every test in this class called generate_objective_directed_candidates()
# directly — that deterministic generator was DELETED ([PM-ASSUMED Q4]) and
# replaced by the LLM-reasoned generate_reasoned_swap_candidates, which does
# not do lens-informed statistical ranking at all (selection is the LLM's).
# The lens-blend HELPER these tests were really protecting (_apply_lens_blend,
# LENS_BLEND_WEIGHT) is explicitly preserved byte-unchanged by R2-3 AC-12 and
# has its own dedicated, generator-independent coverage in
# tests/ai_advisor/test_lens_blend_efficacy.py (never called the deleted
# generator — confirmed clean) and
# tests/advisors/test_asset_swap_engine_explicit_pair_preserved.py::
# test_apply_lens_blend_unchanged.


# ---------------------------------------------------------------------------
# AC-3: Gate path unchanged — evaluate_candidate_batch still called
# ---------------------------------------------------------------------------


class TestGatePathUnchanged:
    """AC-3: lens_scores in ranking must not alter the gate call path."""

    def test_gate_still_called_when_lens_scores_provided(self):
        """RED: evaluate_candidate_batch must be called even when lens_scores is provided."""
        engine = _import_engine()

        # Patch out backtest so we can confirm gate is reached.
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {"sharpe": 1.0}
        mock_bt_result.daily_returns = {"2026-01-01": 0.01, "2026-01-02": -0.005}
        mock_bt_result.data_warnings = []

        mock_gate_batch = MagicMock()
        mock_gate_batch.results = []

        # R2-3 RECONCILIATION: generate_reasoned_swap_candidates (the LLM-reasoned
        # replacement for the deleted generate_objective_directed_candidates)
        # mocked directly so at least one candidate reaches the (also mocked) gate —
        # this test proves gate-reachability, not candidate generation.
        reasoned_pairs = [
            engine.SwapCandidate(incumbent_asset="SPY", candidate_asset=t, rationale="x")
            for t in ("BND", "TLT")
        ]
        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch(
                "advisors.asset_swap_engine.evaluate_candidate_batch", return_value=mock_gate_batch
            ) as mock_gate,
            patch("database.insert_advisor_observation"),
            patch(
                "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                return_value=reasoned_pairs,
            ),
        ):
            obj = engine.SwapObjective(
                objective_type="reduce_correlation",
                target_pair=("SPY", "GLD"),
                measured_value=0.85,
            )
            engine.suggest_swaps(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                objective=obj,
                correlation_data=_CORR_DATA,
                available_assets=["BND", "TLT"],
                lens_scores=_LENS_SCORES_FIXTURE,
            )
        # Gate must have been invoked — regardless of lens_scores.
        mock_gate.assert_called_once()

    def test_suggest_swaps_accepts_lens_scores_param(self):
        """RED: suggest_swaps must accept a lens_scores keyword argument."""
        engine = _import_engine()

        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {}
        mock_bt_result.daily_returns = {}
        mock_bt_result.data_warnings = []

        mock_gate_batch = MagicMock()
        mock_gate_batch.results = []

        reasoned_pairs = [
            engine.SwapCandidate(incumbent_asset="SPY", candidate_asset="BND", rationale="x")
        ]
        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch(
                "advisors.asset_swap_engine.evaluate_candidate_batch", return_value=mock_gate_batch
            ),
            patch("database.insert_advisor_observation"),
            patch(
                "advisors.asset_swap_engine.generate_reasoned_swap_candidates",
                return_value=reasoned_pairs,
            ),
        ):
            obj = engine.SwapObjective(
                objective_type="reduce_correlation",
                target_pair=("SPY", "GLD"),
                measured_value=0.85,
            )
            # Must NOT raise TypeError — lens_scores is a new accepted param.
            result = engine.suggest_swaps(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                objective=obj,
                correlation_data=_CORR_DATA,
                available_assets=["BND"],
                lens_scores=_LENS_SCORES_FIXTURE,
            )
        assert result is not None


# ---------------------------------------------------------------------------
# AC-4: Persistence — raw_response carries lens_evidence + sources
# ---------------------------------------------------------------------------


class TestPersistenceLensEvidence:
    """AC-4: _persist_observation raw_response must include lens_evidence and sources."""

    def _capture_persist_calls(self, engine, score_tree, corr_data, lens_scores):
        """Run propose_operator_swap with a patched DB and return captured raw_response dicts."""
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {"sharpe": 1.2}
        # Provide 30 daily returns so fold-transform produces a non-trivial oos_alpha.
        mock_bt_result.daily_returns = {
            f"day{i}": 0.001 * (1 if i % 2 == 0 else -1) for i in range(30)
        }
        mock_bt_result.data_warnings = []

        captured_calls = []

        def _capture_insert(**kwargs):
            captured_calls.append(kwargs)

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("database.insert_advisor_observation", side_effect=_capture_insert),
        ):
            engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=score_tree,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_scores=lens_scores,
            )

        return captured_calls

    def test_raw_response_contains_lens_evidence_key(self):
        """RED: raw_response dict written to advisor_observations must have 'lens_evidence' key."""
        engine = _import_engine()
        calls = self._capture_persist_calls(engine, _SCORE_TREE, _CORR_DATA, _LENS_SCORES_FIXTURE)
        assert calls, "insert_advisor_observation must have been called"
        raw = calls[0].get("raw_response", {})
        assert "lens_evidence" in raw, (
            f"raw_response missing 'lens_evidence' key (AC-4); keys present: {list(raw.keys())}"
        )

    def test_raw_response_contains_sources_key(self):
        """RED: raw_response dict must have 'sources' key (citation dicts)."""
        engine = _import_engine()
        calls = self._capture_persist_calls(engine, _SCORE_TREE, _CORR_DATA, _LENS_SCORES_FIXTURE)
        assert calls
        raw = calls[0].get("raw_response", {})
        assert "sources" in raw, (
            f"raw_response missing 'sources' key (AC-4); keys present: {list(raw.keys())}"
        )

    def test_lens_evidence_is_dict(self):
        """RED: lens_evidence value must be a dict (ticker → signal metadata)."""
        engine = _import_engine()
        calls = self._capture_persist_calls(engine, _SCORE_TREE, _CORR_DATA, _LENS_SCORES_FIXTURE)
        assert calls
        raw = calls[0].get("raw_response", {})
        lens_ev = raw.get("lens_evidence")
        assert isinstance(lens_ev, dict), f"lens_evidence must be a dict; got {type(lens_ev)}"

    def test_sources_is_list(self):
        """RED: sources value must be a list."""
        engine = _import_engine()
        calls = self._capture_persist_calls(engine, _SCORE_TREE, _CORR_DATA, _LENS_SCORES_FIXTURE)
        assert calls
        raw = calls[0].get("raw_response", {})
        sources = raw.get("sources")
        assert isinstance(sources, list), f"sources must be a list; got {type(sources)}"

    def test_propose_operator_swap_accepts_lens_scores_kwarg(self):
        """RED: propose_operator_swap must accept lens_scores keyword argument."""
        engine = _import_engine()
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {}
        mock_bt_result.daily_returns = {f"d{i}": 0.001 for i in range(10)}
        mock_bt_result.data_warnings = []

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("database.insert_advisor_observation"),
        ):
            # Must NOT raise TypeError.
            result = engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_scores=_LENS_SCORES_FIXTURE,
            )
        assert result is not None


# ---------------------------------------------------------------------------
# AC-5: Per-candidate rationale includes lens evidence
# ---------------------------------------------------------------------------


class TestPerCandidateRationale:
    """AC-5: each candidate's objective_rationale includes lens evidence summary."""

    def test_rationale_mentions_lens_when_scores_provided(self):
        """RED: when lens_scores provided, objective_rationale must reference lens evidence.

        Adversarial: a rationale generated purely from objective/correlation data
        will NOT mention lens evidence — this test must fail against it.
        """
        engine = _import_engine()
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {}
        mock_bt_result.daily_returns = {
            f"d{i}": 0.001 * (-1 if i % 3 == 0 else 1) for i in range(30)
        }
        mock_bt_result.data_warnings = []

        mock_gate_batch = MagicMock()
        mock_gate_result = MagicMock()
        mock_gate_result.candidate_id = "test-sym-001:SPY->BND"
        mock_gate_result.verdict = MagicMock()
        mock_gate_result.verdict.decision = "ADOPT_CANDIDATE"
        mock_gate_result.oos_alpha = 0.05
        mock_gate_result.validation_days = 25
        mock_gate_result.caveats = []
        mock_gate_batch.results = [mock_gate_result]

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        lens_scores_with_bnd = {
            "BND": {"sentiment": 0.8, "macro": 0.6},
        }

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch(
                "advisors.asset_swap_engine.evaluate_candidate_batch", return_value=mock_gate_batch
            ),
            patch("database.insert_advisor_observation"),
        ):
            result = engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_scores=lens_scores_with_bnd,
            )

        assert result.proposals, "Expected at least one proposal"
        rationale = result.proposals[0].objective_rationale
        assert rationale, "objective_rationale must not be empty"
        # The rationale must reference lens evidence — "lens", "sentiment", "macro",
        # or "evidence" must appear somewhere in the text.
        rationale_lower = rationale.lower()
        lens_terms = {"lens", "sentiment", "macro", "evidence", "signal"}
        assert any(t in rationale_lower for t in lens_terms), (
            f"objective_rationale does not mention lens evidence when lens_scores provided; "
            f"got: {rationale!r}"
        )


# ---------------------------------------------------------------------------
# AC-6: Safety contracts — honest-availability, D-1, advise-only
# ---------------------------------------------------------------------------


class TestSafetyContracts:
    """AC-6: honest-availability, D-1, additions-only, advise-only."""

    def test_no_score_fabricated_for_unavailable_lens(self):
        """RED: a ticker whose only real per-ticker data sits in an UNAVAILABLE
        technicals block must not appear in extract result -- available=False must
        win regardless of what the payload nominally contains (honest-availability
        is checked BEFORE payload content, never bypassed by a rich-looking payload)."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        # technicals is UNAVAILABLE but its payload nominally has real per-ticker
        # momentum data for "XYZ" -- this must NOT leak through. Every other lens
        # is either market-wide (sentiment, available but no per-ticker structure)
        # or genuinely unavailable (derivatives, macro, fundamentals) -- so the
        # honest result is the EMPTY dict, no ticker at all.
        context = {
            "technicals": {
                "lens": "technicals",
                "available": False,
                "reason": "stub",
                "payload": {"ma_posture": None, "breadth": None, "momentum": {"XYZ": 0.99}},
                "sources": [],
            },
            "sentiment": {
                "lens": "sentiment",
                "available": True,
                "payload": {"tone_score": 0.5, "corpus": [], "events": [], "article_count": 0},
                "sources": [],
            },
            "macro": {
                "lens": "macro",
                "available": False,
                "reason": "FRED_API_KEY not configured",
                "payload": None,
                "sources": [],
            },
            "derivatives": {
                "lens": "derivatives",
                "available": False,
                "reason": "stub",
                "payload": None,
                "sources": [],
            },
            "fundamentals": {
                "lens": "fundamentals",
                "available": False,
                "reason": "stub",
                "payload": None,
                "sources": [],
            },
        }

        result = fn(context)
        # XYZ must NOT appear — its only score comes from an unavailable lens.
        assert "XYZ" not in result, (
            f"Unavailable-lens ticker XYZ must not appear in extract_lens_scores output; got {result}"
        )
        # Stronger: with technicals unavailable and every other lens either
        # market-wide or unavailable, NOTHING should be fabricated at all.
        assert result == {}, (
            f"No lens in this context has a legitimately-available per-ticker signal; "
            f"expected an empty dict, got {result!r}"
        )

    def test_extract_lens_scores_never_raises_on_malformed_context(self):
        """RED: extract_lens_scores must not raise on malformed/partial context (D-1 degradation)."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        bad_contexts = [
            {},
            {"sentiment": None},
            {"sentiment": {"lens": "sentiment"}},  # missing available key
            {"macro": {"lens": "macro", "available": True, "payload": None, "sources": []}},
        ]
        for ctx in bad_contexts:
            try:
                result = fn(ctx)
                assert isinstance(result, dict), (
                    f"Must return dict on malformed input; got {type(result)}"
                )
            except Exception as exc:
                pytest.fail(
                    f"extract_lens_scores raised {type(exc).__name__} on malformed input {ctx!r}: {exc}"
                )

    def test_advisor_observation_role_unchanged(self):
        """RED: advisor_role must still be 'ASSET_SWAP' (addition, not replacement)."""
        engine = _import_engine()
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {}
        mock_bt_result.daily_returns = {f"d{i}": 0.001 for i in range(10)}
        mock_bt_result.data_warnings = []

        captured = []

        def _capture(**kwargs):
            captured.append(kwargs)

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_scores=_LENS_SCORES_FIXTURE,
            )

        assert captured, "insert_advisor_observation must have been called"
        assert captured[0].get("advisor_role") == "ASSET_SWAP", (
            f"advisor_role must remain 'ASSET_SWAP'; got {captured[0].get('advisor_role')!r}"
        )
        assert captured[0].get("is_advisory_only") == 1, (
            "is_advisory_only must remain 1 (advise-only contract)"
        )

    # test_lens_scores_empty_dict_behaves_like_none RETIRED (R2-3, 2026-07-14
    # -- found by r2-3-engine while implementing §1l's deletion, same class as
    # test_lens_blend_efficacy.py's identical retirement): called
    # generate_objective_directed_candidates() directly to prove
    # lens_scores={} behaves like lens_scores=None through the production
    # path. That deterministic generator was DELETED ([PM-ASSUMED Q4]); the
    # SAME invariant against _apply_lens_blend directly (the surviving,
    # generator-independent home) is
    # tests/ai_advisor/test_lens_blend_efficacy.py::
    # TestApplyLensBlendUsesContinuousScoreNotPosition::
    # test_lens_scores_none_or_empty_returns_input_order_unchanged.


# ---------------------------------------------------------------------------
# Reviewer hardening — BLOCK resolutions (post-merge follow-up)
# ---------------------------------------------------------------------------


class TestFixtureBackedContract:
    """BLOCK resolution: lens_score_extraction_basic.json must be referenced by a test.

    Reviewer BLOCK: fixture was committed but unreferenced — a dead artifact.
    This class enforces the fixture's shape/contract by loading and running it.
    """

    def test_extract_lens_scores_against_fixture(self):
        """Load lens_score_extraction_basic.json, run extract_lens_scores, assert contract.

        The fixture's expected_output_contract specifies which lens keys must be
        PRESENT per ticker (available lenses) and which must be ABSENT (unavailable lenses).
        This test enforces those constraints — it does NOT hardcode numeric scores
        (those are implementation-defined, per fixture note).
        """
        import json

        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = ai.extract_lens_scores

        fixture_path = (
            _REPO_ROOT
            / "tests"
            / "fixtures"
            / "ai_advisor"
            / "cycle3"
            / "lens_score_extraction_basic.json"
        )
        if not fixture_path.exists():
            pytest.skip(
                "Fixture file not found — schema-derived fixture expected at tests/fixtures/ai_advisor/cycle3/lens_score_extraction_basic.json"
            )

        with fixture_path.open(encoding="utf-8") as fh:
            fixture = json.load(fh)

        input_blocks = fixture["input_lens_blocks"]
        contract = fixture["expected_output_contract"]

        result = fn(input_blocks)

        for ticker, ticker_contract in contract.items():
            if ticker.startswith("_"):
                continue  # skip metadata keys

            assert ticker in result, (
                f"Fixture contract: ticker {ticker!r} must appear in extract_lens_scores output. "
                f"Available lenses in fixture have ticker_scores for {ticker!r}. "
                f"Got output tickers: {sorted(result.keys())}"
            )

            ticker_scores = result[ticker]
            assert isinstance(ticker_scores, dict), (
                f"Per-ticker scores for {ticker!r} must be dict; got {type(ticker_scores)}"
            )

            for lens_name in ticker_contract.get("_present_lens_keys", []):
                assert lens_name in ticker_scores, (
                    f"Fixture contract: lens {lens_name!r} must be present in result[{ticker!r}]; "
                    f"got keys: {sorted(ticker_scores.keys())}"
                )

            for lens_name in ticker_contract.get("_absent_lens_keys", []):
                assert lens_name not in ticker_scores, (
                    f"Fixture contract: lens {lens_name!r} must be ABSENT from result[{ticker!r}] "
                    f"(unavailable lens — honest-availability AC-6); got: {ticker_scores}"
                )

            for lens_name, score in ticker_scores.items():
                assert isinstance(score, (int, float)), (
                    f"Score for {ticker!r}.{lens_name!r} must be numeric; got {type(score)}"
                )
                assert score == score, (  # NaN check: NaN != NaN
                    f"Score for {ticker!r}.{lens_name!r} must not be NaN; got {score}"
                )
                if ticker_contract.get("_score_in_unit_interval"):
                    # technicals' momentum is an UNBOUNDED raw 20-day return, not
                    # [0, 1] -- extract_lens_scores must squash it before returning
                    # (AC-D2's blend expects [0, 1] with neutral 0.5).
                    assert 0.0 <= score <= 1.0, (
                        f"Score for {ticker!r}.{lens_name!r} must be squashed to "
                        f"[0, 1] (raw momentum is unbounded); got {score!r}"
                    )


class TestCitationValidationOnPersistence:
    """BLOCK resolution: malformed lens_sources entries must be filtered, not written raw.

    Reviewer BLOCK: build_citation bypassed — raw unvalidated dicts could corrupt audit rows.
    """

    def test_malformed_citation_filtered_from_raw_response(self):
        """Malformed lens_sources entries (missing url) must not appear in persisted sources."""
        engine = _import_engine()
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {}
        mock_bt_result.daily_returns = {f"d{i}": 0.001 for i in range(10)}
        mock_bt_result.data_warnings = []

        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        mixed_sources = [
            {
                "title": "Valid bond article",
                "url": "https://example.com/bond",
                "published": "2026-06-01",
                "lens": "sentiment",
            },
            {"title": "Missing URL", "published": "2026-06-01", "lens": "macro"},
            {
                "title": "Bad scheme",
                "url": "ftp://example.com/data",
                "published": "2026-06-01",
                "lens": "macro",
            },
        ]

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_sources=mixed_sources,
            )

        assert captured, "insert_advisor_observation must have been called"
        persisted_sources = captured[0].get("raw_response", {}).get("sources", [])
        persisted_urls = [s.get("url") for s in persisted_sources if isinstance(s, dict)]
        assert "https://example.com/bond" in persisted_urls, (
            f"Valid citation URL must survive filtering; got persisted_urls={persisted_urls}"
        )
        assert not any("ftp://" in (u or "") for u in persisted_urls), (
            f"ftp:// citation must be filtered out; got persisted_urls={persisted_urls}"
        )
        assert all(s.get("url") for s in persisted_sources if isinstance(s, dict)), (
            f"All persisted citations must have a non-empty url; got {persisted_sources}"
        )

    def test_valid_citations_pass_through_unchanged(self):
        """Well-formed lens_sources entries must appear in the persisted sources list."""
        engine = _import_engine()
        mock_bt_result = MagicMock()
        mock_bt_result.error = None
        mock_bt_result.stats = {}
        mock_bt_result.daily_returns = {f"d{i}": 0.001 for i in range(10)}
        mock_bt_result.data_warnings = []

        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        valid_sources = [
            {
                "title": "Bond rally driven by FOMC",
                "url": "https://example.com/bond",
                "published": "2026-06-01",
                "lens": "sentiment",
            },
            {
                "title": "FRED 10-Yr yield",
                "url": "https://fred.stlouisfed.org/series/DGS10",
                "published": "2026-06-01",
                "lens": "macro",
            },
        ]

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_sources=valid_sources,
            )

        assert captured
        persisted_sources = captured[0].get("raw_response", {}).get("sources", [])
        persisted_urls = {s.get("url") for s in persisted_sources if isinstance(s, dict)}
        for src in valid_sources:
            assert src["url"] in persisted_urls, (
                f"Valid citation URL {src['url']!r} was dropped by citation filter; "
                f"persisted_urls={persisted_urls}"
            )


# TestLensBlendPrimaryMetricDominance RETIRED IN FULL (R2-3, 2026-07-14 —
# found by r2-3-engine while implementing §1l's deletion, same class as
# test_lens_blend_efficacy.py's identical retirement; the empty class shell
# left behind by an earlier pass of this retirement was itself caught by CI's
# tests/meta/test_all_test_files_parse.py::test_no_empty_test_classes_across_all_test_files
# meta-guard and removed here). This class held exactly one test,
# test_primary_metric_dominates_opposing_lens_preference, which called
# generate_objective_directed_candidates() directly to prove the AC-D2
# invariant (a large real primary-score gap can never be inverted by lens
# evidence) through the production candidate-generation path. That
# deterministic generator was DELETED ([PM-ASSUMED Q4]); the SAME invariant
# against _apply_lens_blend directly (the surviving, generator-independent
# home, using the identical real-computed-gap fixture-construction
# discipline this test's own docstring documented) is
# tests/ai_advisor/test_lens_blend_efficacy.py::
# TestApplyLensBlendUsesContinuousScoreNotPosition::
# test_large_primary_margin_cannot_be_inverted_by_extreme_lens_favor.
