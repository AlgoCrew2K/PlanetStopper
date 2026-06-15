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
from typing import Any
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

# A minimal Composer score tree with two holdings.
_SCORE_TREE = {
    "name": "TestSymphony",
    "ticker": None,
    "children": [
        {"ticker": "SPY", "children": []},
        {"ticker": "GLD", "children": []},
    ],
}

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
# Three lenses available=True with ticker-level payload, two available=False (stubs).
_ADVISOR_CONTEXT_WITH_LENSES = {
    "scope": "symphony",
    "symphony_id": "test-sym-001",
    "sentiment": {
        "lens": "sentiment",
        "available": True,
        "payload": {
            "article_count": 3,
            "ticker_scores": {"BND": 0.7, "TLT": 0.4, "SHY": 0.2},
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
        "available": True,
        "payload": {
            "series": {
                "DGS10": {"label": "10-Year Treasury", "value": "4.25", "date": "2026-06-01"},
            },
            "ticker_scores": {"BND": 0.5, "TLT": 0.8, "SHY": 0.3},
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
    "technicals": {
        "lens": "technicals",
        "available": False,
        "reason": "technicals source not connected — cycle-2b deliverable",
        "payload": None,
        "sources": [],
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
        """RED: available=True lenses with ticker_scores payload contribute to output."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = getattr(ai, "extract_lens_scores")

        result = fn(_ADVISOR_CONTEXT_WITH_LENSES)

        # BND appears in both sentiment and macro ticker_scores — must appear in result.
        assert "BND" in result, "BND missing from extract_lens_scores output"
        bnd_scores = result["BND"]
        assert isinstance(bnd_scores, dict), "Per-ticker scores must be a dict"
        assert "sentiment" in bnd_scores, "sentiment score missing for BND"
        assert "macro" in bnd_scores, "macro score missing for BND"

    def test_unavailable_lenses_produce_no_scores(self):
        """RED: available=False lenses must not contribute any scores (AC-6 honest-availability)."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = getattr(ai, "extract_lens_scores")

        result = fn(_ADVISOR_CONTEXT_WITH_LENSES)

        # technicals, derivatives, fundamentals are all unavailable — none should appear.
        for ticker_scores in result.values():
            assert "technicals" not in ticker_scores, (
                "technicals (unavailable) must not contribute scores"
            )
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
            fn = getattr(ai, "extract_lens_scores")

        result = fn(_ADVISOR_CONTEXT_ALL_UNAVAILABLE)
        assert result == {}, f"All-unavailable context must return empty dict; got {result!r}"

    def test_returns_dict_of_dicts(self):
        """RED: return type is dict[str, dict] — outer key is ticker, inner is lens→score."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = getattr(ai, "extract_lens_scores")

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
            fn = getattr(ai, "extract_lens_scores")

        sparse_context = {
            "scope": "symphony",
            "sentiment": {
                "lens": "sentiment",
                "available": True,
                "payload": {"ticker_scores": {"BND": 0.6}},
                "sources": [],
            },
            # macro, technicals, derivatives, fundamentals keys absent
        }
        # Must not raise
        result = fn(sparse_context)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# AC-2: generate_objective_directed_candidates — optional lens_scores param
# ---------------------------------------------------------------------------


class TestGenerateObjectiveDirectedCandidatesLensParam:
    """AC-2: lens_scores=None → byte-identical behavior; lens_scores provided → reranks."""

    def _make_objective(self, obj_type="reduce_correlation"):
        engine = _import_engine()
        return engine.SwapObjective(
            objective_type=obj_type,
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

    def test_accepts_lens_scores_none_param(self):
        """RED: generate_objective_directed_candidates must accept lens_scores=None without TypeError."""
        engine = _import_engine()
        obj = self._make_objective()
        available = ["BND", "TLT", "SHY"]
        # Must not raise TypeError — lens_scores=None is the backward-compatible default.
        result = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
            lens_scores=None,
        )
        assert isinstance(result, list), "Result must be a list"

    def test_backward_compat_no_lens_scores_arg(self):
        """RED: calling WITHOUT lens_scores must still work (existing call sites unbroken)."""
        engine = _import_engine()
        obj = self._make_objective()
        available = ["BND", "TLT", "SHY"]
        # Legacy call without lens_scores — must not raise.
        result = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
        )
        assert isinstance(result, list)

    def test_lens_scores_reranks_candidates(self):
        """RED: providing lens_scores must change the ranking relative to no-lens baseline.

        Adversarial: a generate_objective_directed_candidates that ignores lens_scores
        entirely will produce identical rankings — this test must fail against it.

        We use a lens_scores where TLT has very high scores and SHY has very low scores,
        then verify TLT appears before SHY in the reranked output.
        """
        engine = _import_engine()
        obj = self._make_objective("lift_risk_adjusted")
        available = ["BND", "TLT", "SHY"]

        # Correlation data is deliberately equal for all three candidates (no-op baseline).
        flat_corr = {
            "SPY": [0.01] * 10,
            "BND": [0.0] * 10,
            "TLT": [0.0] * 10,
            "SHY": [0.0] * 10,
        }

        # Lens scores: TLT is strongly preferred, SHY is strongly disfavored.
        lens_scores = {
            "BND": {"sentiment": 0.5, "macro": 0.5},
            "TLT": {"sentiment": 0.95, "macro": 0.90},  # high score → should rank first
            "SHY": {"sentiment": 0.05, "macro": 0.05},  # low score → should rank last
        }

        result = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=flat_corr,
            available_assets=available,
            lens_scores=lens_scores,
        )

        tickers = [c["ticker"] if isinstance(c, dict) else c for c in result]
        assert len(tickers) >= 2, f"Expected at least 2 candidates, got {tickers}"
        tlt_idx = tickers.index("TLT") if "TLT" in tickers else None
        shy_idx = tickers.index("SHY") if "SHY" in tickers else None

        assert tlt_idx is not None and shy_idx is not None, (
            f"Both TLT and SHY must appear in result; got {tickers}"
        )
        assert tlt_idx < shy_idx, (
            f"Lens-informed ranking must put TLT (high lens score) before SHY (low lens score); "
            f"got TLT at {tlt_idx}, SHY at {shy_idx} in {tickers}"
        )

    def test_lens_scores_none_produces_same_result_as_omitted(self):
        """RED: lens_scores=None and omitting lens_scores must produce identical output."""
        engine = _import_engine()
        obj = self._make_objective()
        available = ["BND", "TLT", "SHY"]

        result_none = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
            lens_scores=None,
        )
        result_omitted = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
        )

        tickers_none = [c["ticker"] if isinstance(c, dict) else c for c in result_none]
        tickers_omitted = [c["ticker"] if isinstance(c, dict) else c for c in result_omitted]
        assert tickers_none == tickers_omitted, (
            f"lens_scores=None must produce identical ranking to omitting the param; "
            f"got {tickers_none} vs {tickers_omitted}"
        )

    def test_named_constants_exist_for_lens_blend_weight(self):
        """RED: the lens blend weight must be a named module-level constant (no magic numbers)."""
        engine = _import_engine()
        # The constant must be accessible as an attribute — name must be descriptive.
        # We accept LENS_BLEND_WEIGHT or any name containing LENS and WEIGHT.
        constant_names = [
            attr for attr in dir(engine) if "LENS" in attr.upper() and "WEIGHT" in attr.upper()
        ]
        assert constant_names, (
            "No named LENS_BLEND_WEIGHT constant found in asset_swap_engine — "
            "no magic numbers per project coding standard (BRIEF AC-2)"
        )
        # The constant must be a float in (0.0, 1.0] — it's a blend weight, not a multiplier.
        weight = getattr(engine, constant_names[0])
        assert isinstance(weight, float), f"{constant_names[0]} must be a float"
        assert 0.0 < weight <= 1.0, f"{constant_names[0]}={weight} must be in (0.0, 1.0]"

    def test_lens_score_does_not_replace_gate(self):
        """RED: lens scoring must influence ranking only, never replace evaluate_candidate_batch.

        Structural: the function must still return candidates for the gate to process,
        not filter them out based on lens scores alone.
        """
        engine = _import_engine()
        obj = self._make_objective("reduce_correlation")
        available = ["BND", "TLT", "SHY"]

        # Very low lens scores for all candidates — must still return all candidates.
        lens_scores_all_low = {t: {"sentiment": 0.01} for t in available}

        result = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
            lens_scores=lens_scores_all_low,
        )
        tickers = [c["ticker"] if isinstance(c, dict) else c for c in result]
        # All candidates must survive ranking — gate happens later.
        assert set(tickers) == {"BND", "TLT", "SHY"}, (
            f"Lens scoring must not eliminate candidates pre-gate; got {tickers}"
        )


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

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch(
                "advisors.asset_swap_engine.evaluate_candidate_batch", return_value=mock_gate_batch
            ) as mock_gate,
            patch("database.insert_advisor_observation"),
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

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=mock_bt_result),
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch(
                "advisors.asset_swap_engine.evaluate_candidate_batch", return_value=mock_gate_batch
            ),
            patch("database.insert_advisor_observation"),
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
        """RED: a ticker with scores only from unavailable lenses must not appear in extract result."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = getattr(ai, "extract_lens_scores")

        # Context where only technicals is "available" but has no ticker_scores payload.
        # The ticker "XYZ" appears only in an unavailable macro lens — must not be fabricated.
        context = {
            "macro": {
                "lens": "macro",
                "available": False,
                "reason": "FRED_API_KEY not configured",
                "payload": {"ticker_scores": {"XYZ": 0.9}},  # unavailable → must be ignored
                "sources": [],
            },
            "sentiment": {
                "lens": "sentiment",
                "available": True,
                "payload": {"ticker_scores": {}},  # available but empty
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

        result = fn(context)
        # XYZ must NOT appear — its only score comes from an unavailable lens.
        assert "XYZ" not in result, (
            f"Unavailable-lens ticker XYZ must not appear in extract_lens_scores output; got {result}"
        )

    def test_extract_lens_scores_never_raises_on_malformed_context(self):
        """RED: extract_lens_scores must not raise on malformed/partial context (D-1 degradation)."""
        engine = _import_engine()
        fn = getattr(engine, "extract_lens_scores", None)
        if fn is None:
            ai = _import_ai_advisor()
            fn = getattr(ai, "extract_lens_scores")

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

    def test_lens_scores_empty_dict_behaves_like_none(self):
        """RED: lens_scores={} (empty) must not crash and must not rerank (no scores to blend)."""
        engine = _import_engine()
        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )
        available = ["BND", "TLT", "SHY"]

        result_empty = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
            lens_scores={},
        )
        result_none = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=_CORR_DATA,
            available_assets=available,
            lens_scores=None,
        )

        tickers_empty = [c["ticker"] if isinstance(c, dict) else c for c in result_empty]
        tickers_none = [c["ticker"] if isinstance(c, dict) else c for c in result_none]
        assert tickers_empty == tickers_none, (
            f"lens_scores={{}} must produce same ranking as None; got {tickers_empty} vs {tickers_none}"
        )


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
            fn = getattr(ai, "extract_lens_scores")

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


class TestLensBlendPrimaryMetricDominance:
    """Reviewer advisory gap: primary metric must dominate when clearly better than alternatives."""

    def test_primary_metric_dominates_opposing_lens_preference(self):
        """When primary metric clearly favors BND, BND ranks first even with strong opposing lens.

        LENS_BLEND_WEIGHT=0.25 max perturbation is 0.25 positions.
        A 1-position primary gap cannot be inverted by lens alone.
        """
        engine = _import_engine()
        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        primary_corr = {
            "SPY": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            "BND": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # corr~0 → best
            "TLT": [0.5, 0.6, 0.7, 0.8, 0.9, 0.5, 0.6, 0.7, 0.8, 0.9],  # corr~0.95 → worse
            "AGG": [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9],  # corr~1.0 → worst
        }

        # Lens strongly opposes primary: TLT=0.99, BND=0.01.
        # Max lens bump for TLT vs BND = 0.25*(0.99-0.01)=0.245 < 1.0-position gap.
        lens_scores_oppose = {
            "BND": {"sentiment": 0.01},
            "TLT": {"sentiment": 0.99},
            "AGG": {"sentiment": 0.5},
        }

        result = engine.generate_objective_directed_candidates(
            symphony_id="test-sym-001",
            objective=obj,
            correlation_data=primary_corr,
            available_assets=["BND", "TLT", "AGG"],
            lens_scores=lens_scores_oppose,
        )

        tickers = [c["ticker"] if isinstance(c, dict) else c for c in result]
        assert tickers, "Must return at least one candidate"
        assert tickers[0] == "BND", (
            f"Primary metric (low correlation) must dominate lens preference. "
            f"BND has near-zero correlation and must rank first even though lens "
            f"strongly prefers TLT. With LENS_BLEND_WEIGHT=0.25 the max lens "
            f"perturbation (0.245) cannot bridge a 1.0-position primary gap. "
            f"Got ranking: {tickers}."
        )
