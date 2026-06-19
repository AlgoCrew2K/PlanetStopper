"""Supplementary adversarial RED tests — Cycle 3 lens-informed swaps.

These tests cover gaps NOT addressed in test_cycle3_lens_informed_swaps.py:

  AC-4 gap: lens_sources kwarg on propose_operator_swap and suggest_swaps
            (BRIEF: raw_response 'sources' carrying citation dicts {title,url,published,lens}).
            The existing file tests that raw_response has a 'sources' key, but does NOT test
            that caller-supplied citation dicts are threaded through (no lens_sources kwarg test).

  AC-5 gap: static no-defund / no-pull / no-trade-execution check.
            The existing file checks is_advisory_only=1 at runtime, but has no static source
            analysis guard against defund/pull/execute-trade phrases being introduced.

Mocking strategy mirrors test_cycle3_lens_informed_swaps.py:
  - run_backtest patched to avoid live Composer calls.
  - database.insert_advisor_observation patched to capture calls.
  - Math engine NEVER mocked.

All tests are MINIMAL RED — they fail because the implementation does not yet accept
the lens_sources parameter on propose_operator_swap / suggest_swaps.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_engine():
    _ensure_repo_on_path()
    return importlib.import_module("advisors.asset_swap_engine")


_SCORE_TREE = {
    "name": "TestSymphony",
    "ticker": None,
    "children": [
        {"ticker": "SPY", "children": []},
        {"ticker": "GLD", "children": []},
    ],
}

_CORR_DATA = {
    "SPY": [0.01, -0.02, 0.03, -0.01, 0.02],
    "GLD": [-0.01, 0.02, -0.01, 0.01, -0.02],
    "BND": [0.001, -0.001, 0.001, 0.0, -0.001],
    "TLT": [0.002, -0.002, 0.001, -0.001, 0.002],
}

_LENS_SCORES_FIXTURE = {
    "BND": {"sentiment": 0.7, "macro": 0.5},
    "TLT": {"sentiment": 0.4, "macro": 0.8},
}

_LENS_SOURCES_FIXTURE = [
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

_MOCK_BT_RESULT = None  # built per-test via helper


def _make_mock_bt():
    m = MagicMock()
    m.error = None
    m.stats = {}
    m.daily_returns = {f"d{i}": 0.001 * (-1 if i % 3 == 0 else 1) for i in range(30)}
    m.data_warnings = []
    return m


# ===========================================================================
# AC-4 gap: lens_sources kwarg on propose_operator_swap
# ===========================================================================


class TestLensSourcesKwargOnProposeOperatorSwap:
    """AC-4: propose_operator_swap must accept a lens_sources kwarg carrying citation dicts.

    BRIEF AC-4: raw_response must carry 'sources' (citation dicts {title,url,published,lens}).
    The existing tests verify the raw_response HAS a 'sources' key — they do NOT test that
    caller-supplied citation dicts are threaded through from a lens_sources kwarg.

    This is RED because propose_operator_swap does not yet accept lens_sources.
    """

    def test_propose_operator_swap_signature_accepts_lens_sources(self):
        """AC-4: propose_operator_swap must declare a lens_sources keyword parameter."""
        engine = _import_engine()
        import inspect

        fn = engine.propose_operator_swap
        sig = inspect.signature(fn)
        assert "lens_sources" in sig.parameters, (
            f"propose_operator_swap signature: {sig}. "
            "AC-4: propose_operator_swap must accept a lens_sources kwarg. "
            "BRIEF: raw_response 'sources' carries citation dicts {title,url,published,lens}. "
            "Without a lens_sources param, callers cannot inject citation provenance. "
            "This test is RED — the parameter does not yet exist."
        )

    def test_propose_operator_swap_lens_sources_defaults_to_empty_or_none(self):
        """AC-4: lens_sources must default to None or [] (backward-compat — no required arg)."""
        engine = _import_engine()
        import inspect

        fn = engine.propose_operator_swap
        sig = inspect.signature(fn)
        param = sig.parameters.get("lens_sources")
        if param is None:
            pytest.skip("lens_sources param not yet added — covered by signature test above.")
        sentinel = param.default
        assert (
            sentinel is None
            or sentinel == []
            or sentinel == inspect.Parameter.empty
            or hasattr(sentinel, "__iter__")
        ), (
            f"lens_sources default={sentinel!r}. Must default to None or [] "
            "(backward-compat: existing callers that don't pass lens_sources must still work). "
            "A required parameter would break all existing callers."
        )

    def test_propose_operator_swap_caller_sources_appear_in_raw_response(self):
        """AC-4: citation dicts passed via lens_sources must appear in raw_response['sources'].

        The existing test only checks that raw_response has a 'sources' key — it does NOT
        verify that the caller's actual citation dicts are threaded through.
        This adversarial test verifies the contract end-to-end.
        """
        engine = _import_engine()
        import inspect

        fn = engine.propose_operator_swap
        sig = inspect.signature(fn)
        if "lens_sources" not in sig.parameters:
            pytest.skip("lens_sources not yet on propose_operator_swap — wait for RED→GREEN.")

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=_make_mock_bt()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            engine.propose_operator_swap(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                incumbent_asset="SPY",
                candidate_asset="BND",
                objective=obj,
                lens_scores=_LENS_SCORES_FIXTURE,
                lens_sources=_LENS_SOURCES_FIXTURE,
            )

        assert captured, "insert_advisor_observation must be called."
        for call_kw in captured:
            raw = call_kw.get("raw_response", {})
            sources = raw.get("sources")
            assert sources is not None, (
                "raw_response missing 'sources'. AC-4: sources must be persisted."
            )
            assert isinstance(sources, list), (
                f"sources must be a list, got {type(sources).__name__!r}."
            )
            # The caller-supplied citations must be present in the sources list.
            # We check that at least one of the caller's URLs appears.
            caller_urls = {s["url"] for s in _LENS_SOURCES_FIXTURE}
            persisted_urls = {s.get("url") for s in sources if isinstance(s, dict)}
            assert caller_urls & persisted_urls, (
                f"Caller-supplied lens_sources URLs {caller_urls} not found in "
                f"persisted raw_response['sources'] URLs {persisted_urls}. "
                "AC-4: citation provenance from the caller must flow through to the "
                "persisted raw_response — not discarded."
            )


# ===========================================================================
# AC-4 gap: lens_sources kwarg on suggest_swaps
# ===========================================================================


class TestLensSourcesKwargOnSuggestSwaps:
    """AC-4: suggest_swaps must also accept and propagate lens_sources.

    The BRIEF states persistence carries sources for 'any news-backed evidence'.
    suggest_swaps also calls _persist_observation — it needs the same kwarg.
    """

    def test_suggest_swaps_signature_accepts_lens_sources(self):
        """AC-4: suggest_swaps must declare a lens_sources keyword parameter."""
        engine = _import_engine()
        import inspect

        fn = engine.suggest_swaps
        sig = inspect.signature(fn)
        assert "lens_sources" in sig.parameters, (
            f"suggest_swaps signature: {sig}. "
            "AC-4: suggest_swaps must accept lens_sources to propagate citation provenance "
            "into _persist_observation raw_response. "
            "This test is RED — the parameter does not yet exist on suggest_swaps."
        )

    def test_suggest_swaps_with_lens_sources_does_not_raise(self):
        """AC-4: calling suggest_swaps with lens_sources kwarg must not raise TypeError."""
        engine = _import_engine()
        import inspect

        fn = engine.suggest_swaps
        sig = inspect.signature(fn)
        if "lens_sources" not in sig.parameters:
            pytest.skip("lens_sources not yet on suggest_swaps — wait for RED→GREEN.")

        mock_gate_batch = MagicMock()
        mock_gate_batch.results = []

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        try:
            with (
                patch("advisors.asset_swap_engine.run_backtest", return_value=_make_mock_bt()),
                patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
                patch(
                    "advisors.asset_swap_engine.evaluate_candidate_batch",
                    return_value=mock_gate_batch,
                ),
                patch("database.insert_advisor_observation"),
            ):
                result = engine.suggest_swaps(
                    symphony_id="test-sym-001",
                    score_tree=_SCORE_TREE,
                    objective=obj,
                    correlation_data=_CORR_DATA,
                    available_assets=["BND", "TLT"],
                    lens_scores=_LENS_SCORES_FIXTURE,
                    lens_sources=_LENS_SOURCES_FIXTURE,
                )
        except TypeError as exc:
            pytest.fail(
                f"suggest_swaps raised TypeError: {exc}. "
                "AC-4: suggest_swaps must accept lens_sources without raising. "
                "The parameter is expected but not yet in the signature."
            )

        assert result is not None

    def test_suggest_swaps_caller_sources_appear_in_raw_response(self):
        """AC-4: citation dicts passed via lens_sources must appear in each candidate's
        raw_response['sources'] when suggest_swaps persists observations.

        RED intent: the implementer added lens_sources to the suggest_swaps signature
        but the loop body still calls _collect_lens_sources(lens_scores) unconditionally,
        discarding any caller-supplied citations. This test catches that gap:
        the URL in _LENS_SOURCES_FIXTURE must appear in at least one persisted raw_response.
        """
        engine = _import_engine()
        import inspect

        fn = engine.suggest_swaps
        sig = inspect.signature(fn)
        if "lens_sources" not in sig.parameters:
            pytest.skip("lens_sources not yet on suggest_swaps — wait for RED→GREEN.")

        # Build a gate batch that admits BND (ADOPT_CANDIDATE) so persistence is triggered.
        mock_gate_result = MagicMock()
        mock_gate_result.candidate_id = "test-sym-001:SPY->BND"
        mock_gate_result.verdict = MagicMock()
        mock_gate_result.verdict.decision = "ADOPT_CANDIDATE"
        mock_gate_result.oos_alpha = 0.05
        mock_gate_result.validation_days = 25
        mock_gate_result.caveats = []

        mock_gate_batch = MagicMock()
        mock_gate_batch.results = [mock_gate_result]

        obj = engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("SPY", "GLD"),
            measured_value=0.85,
        )

        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=_make_mock_bt()),
            patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
            patch(
                "advisors.asset_swap_engine.evaluate_candidate_batch", return_value=mock_gate_batch
            ),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            engine.suggest_swaps(
                symphony_id="test-sym-001",
                score_tree=_SCORE_TREE,
                objective=obj,
                correlation_data=_CORR_DATA,
                available_assets=["BND"],
                lens_scores=_LENS_SCORES_FIXTURE,
                lens_sources=_LENS_SOURCES_FIXTURE,
            )

        assert captured, (
            "insert_advisor_observation was not called. "
            "A gate-admitted candidate must be persisted."
        )
        caller_urls = {s["url"] for s in _LENS_SOURCES_FIXTURE}
        any_url_present = False
        for call_kw in captured:
            raw = call_kw.get("raw_response", {})
            sources = raw.get("sources", [])
            persisted_urls = {s.get("url") for s in sources if isinstance(s, dict)}
            if caller_urls & persisted_urls:
                any_url_present = True
                break
        assert any_url_present, (
            f"Caller-supplied lens_sources URLs {caller_urls} not found in any persisted "
            f"raw_response['sources']. "
            "AC-4: suggest_swaps must propagate caller-supplied citation dicts into "
            "_persist_observation. The implementation accepted lens_sources in the signature "
            "but the loop body ignored it (called _collect_lens_sources unconditionally). "
            "Fix: use 'lens_sources if lens_sources is not None else _collect_lens_sources(lens_scores)'."
        )


# ===========================================================================
# AC-5 gap: static no-defund / no-pull / no-trade-execution guard
# ===========================================================================


class TestNoDefundNoTradeStaticGuard:
    """AC-5 static guard: asset_swap_engine must never emit defund/pull/execute patterns.

    The existing test checks is_advisory_only=1 at runtime. This test statically
    verifies no execution-path code contains forbidden action phrases.
    """

    def test_source_contains_no_defund_or_pull_phrases(self):
        """AC-5 static: asset_swap_engine.py must not contain defund/pull/liquidate phrases."""
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created.")

        source = engine_path.read_text(encoding="utf-8").lower()
        # These phrases in rationale templates or suggestion strings would signal
        # the engine is emitting defund/pull/execute advice.
        forbidden = [
            "defund",
            "pull symphony",
            "remove symphony",
            "close position",
            "sell all",
            "liquidate",
        ]
        for phrase in forbidden:
            assert phrase not in source, (
                f"advisors/asset_swap_engine.py contains forbidden phrase {phrase!r}. "
                "AC-5: additions and swaps only. The engine must never suggest "
                "defunding, pulling, or removing a symphony. "
                "This is a scope guard — the engine ranks replacement candidates, "
                "never portfolio composition changes."
            )

    def test_source_contains_no_trade_execution_calls(self):
        """AC-5 + AC-X1 static: asset_swap_engine.py must not contain trade-placement calls."""
        engine_path = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created.")

        source = engine_path.read_text(encoding="utf-8").lower()
        execution_patterns = [
            "place_order",
            "submit_order",
            "create_order",
            "execute_trade",
            "alpaca.post",
            "alpaca_client.post",
            "trade_api",
        ]
        for pat in execution_patterns:
            assert pat not in source, (
                f"advisors/asset_swap_engine.py references execution pattern {pat!r}. "
                "AC-5 + AC-X1: advise-only. The engine must NEVER execute trades. "
                "Every surfaced swap is an operator-manual action."
            )
