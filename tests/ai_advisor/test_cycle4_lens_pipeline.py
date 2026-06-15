"""
Cycle 4 — RED: Off-hours lens pipeline + Market Prism always-on summary.

Scope (BRIEF: CYCLE4-BRIEF.md):
  AC-1  run_pipeline(*, dry_run=False) -> dict with keys run_ts, lenses_attempted,
        lenses_available, market_prism_row_id, error_count. Never raises.
  AC-2  Per-lens isolation: a failing _build_<lens>_section() does NOT abort the
        pipeline. The failing lens records available=False + type(exc).__name__ reason.
        The final MARKET_PRISM observation still writes.
  AC-3  Market Prism always written: every run_pipeline() (non-dry_run) writes exactly
        ONE advisor_role="MARKET_PRISM" row to advisor_observations, even when all 5
        lenses are available=False. verdict="limited-inputs" in the all-unavailable case.
  AC-4  raw_response carries required fields: run_ts, per_lens_digest (5 lens names),
        overall_sentiment, sentiment_rationale, available_lens_count, total_lens_count,
        sources (list, possibly empty). JSON-serializable.
  AC-5  D-1 contract: type(exc).__name__ only in raw_response and WARNING+ log records.
        No raw str(exc) anywhere in persisted or logged output.
  AC-6  CC-2 import-boundary: alpha_bot_execution.py source contains no "lens_pipeline"
        or "advisors." import.
  AC-7  Scheduler wiring: run_scheduler() in app.py registers _run_lens_pipeline daily.
        The wrapper spawns a daemon thread (non-blocking).
  AC-8  Sources round-trip: raw_response["sources"] contains only build_citation-valid
        dicts. Malformed entries (missing url, bad scheme) are dropped before write.
  AC-9  database.get_latest_market_prism_summary() returns most recent MARKET_PRISM row
        or None when no row exists.
  AC-10 docs/generated/advisors_lens_pipeline.md exists; DECISIONS.md contains DE-CY4-001.
        (Doc assertion deferred to end of cycle after doc-writer commits.)

Mocking strategy:
  - database.insert_advisor_observation: captured in a list via side_effect — never
    hits the real DB.
  - database.get_latest_market_prism_summary: real implementation tested against a
    temp-db fixture (conftest.py _isolate_db handles isolation).
  - ai_advisor._build_client: patched to a MagicMock so the synthesis Claude call
    does not go live.
  - ai_advisor._build_*_section: patched per-test to simulate available/unavailable
    and error conditions.
  - No live network. No live Anthropic API. Math engine never mocked.

Adversarial RED intent:
  - AC-1: advisors.lens_pipeline does not exist → ImportError on import.
  - AC-2: a raising lens section must bubble out uncaught if pipeline has no isolation.
  - AC-3: pipeline with no persistence call fails the assertion trivially.
  - AC-4: missing required raw_response keys fail the schema check.
  - AC-5: if str(exc) appears, the D-1 assertion fails.
  - AC-6: passes trivially if alpha_bot_execution.py has no advisor imports (GREEN on
    AC-6 from day 1 is correct; the test is a regression guard).
  - AC-7: run_scheduler() currently has no _run_lens_pipeline → assertion fails.
  - AC-8: malformed sources pass through if build_citation gate is absent.
  - AC-9: database.get_latest_market_prism_summary does not exist → AttributeError.
"""

from __future__ import annotations

import importlib
import json
import logging
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root helpers
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _WORKTREE  # worktree IS the cycle repo root


def _import_pipeline():
    """Import advisors.lens_pipeline; raises ImportError if not yet implemented."""
    if "advisors.lens_pipeline" in sys.modules:
        importlib.reload(sys.modules["advisors.lens_pipeline"])
    return importlib.import_module("advisors.lens_pipeline")


def _import_database():
    import database

    return database


def _import_ai_advisor():
    import ai_advisor

    return ai_advisor


# ---------------------------------------------------------------------------
# Shared stub helpers
# ---------------------------------------------------------------------------


def _stub_lens_available(lens_name: str) -> dict:
    """Return a minimal available=True lens block for a given lens name."""
    return {
        "lens": lens_name,
        "available": True,
        "payload": {"stub": True},
        "sources": [
            {
                "title": f"Stub {lens_name} source",
                "url": f"https://example.com/{lens_name}",
                "published": "2026-06-13",
                "lens": lens_name,
            }
        ],
    }


def _stub_lens_unavailable(lens_name: str, reason: str = "stub — unavailable") -> dict:
    """Return an available=False lens block."""
    return {
        "lens": lens_name,
        "available": False,
        "reason": reason,
        "payload": None,
        "sources": [],
    }


_ALL_LENS_NAMES = ("technicals", "sentiment", "derivatives", "macro", "fundamentals")


# ---------------------------------------------------------------------------
# AC-1 — Pipeline module exists and run_pipeline is callable
# ---------------------------------------------------------------------------


class TestPipelineModuleExists:
    """AC-1: advisors/lens_pipeline.py must export run_pipeline(*, dry_run=False) -> dict."""

    def test_pipeline_module_importable(self):
        """advisors.lens_pipeline must be importable without error.

        RED intent: the module does not exist yet — ImportError expected until implemented.
        """
        pipeline = _import_pipeline()
        assert pipeline is not None, "advisors.lens_pipeline must be importable"

    def test_run_pipeline_is_callable(self):
        """run_pipeline must be a callable exported from advisors.lens_pipeline."""
        pipeline = _import_pipeline()
        fn = getattr(pipeline, "run_pipeline", None)
        assert callable(fn), (
            f"advisors.lens_pipeline must export run_pipeline as a callable. Got: {fn!r}"
        )

    def test_run_pipeline_accepts_dry_run_kwarg(self):
        """run_pipeline must accept dry_run as a keyword-only argument."""
        import inspect

        pipeline = _import_pipeline()
        fn = pipeline.run_pipeline
        sig = inspect.signature(fn)
        assert "dry_run" in sig.parameters, (
            f"run_pipeline must have a dry_run parameter; got params: {list(sig.parameters)}"
        )
        param = sig.parameters["dry_run"]
        assert param.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), "dry_run must be keyword-accessible"

    def test_run_pipeline_dry_run_returns_dict(self):
        """run_pipeline(dry_run=True) must return a dict — no DB or Claude calls."""
        pipeline = _import_pipeline()
        # Mock all lens sections to stub so no live network calls happen in dry_run
        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation") as mock_insert,
        ):
            result = pipeline.run_pipeline(dry_run=True)

        assert isinstance(result, dict), (
            f"run_pipeline(dry_run=True) must return a dict; got {type(result)}"
        )
        (
            mock_insert.assert_not_called(),
            ("dry_run=True must not call database.insert_advisor_observation"),
        )

    def test_run_pipeline_dry_run_has_required_keys(self):
        """run_pipeline(dry_run=True) dict must contain all required keys."""
        pipeline = _import_pipeline()
        required_keys = {
            "run_ts",
            "lenses_attempted",
            "lenses_available",
            "market_prism_row_id",
            "error_count",
        }

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation"),
        ):
            result = pipeline.run_pipeline(dry_run=True)

        missing = required_keys - set(result.keys())
        assert not missing, (
            f"run_pipeline(dry_run=True) result is missing required keys: {missing}. "
            f"Got keys: {sorted(result.keys())}"
        )

    def test_run_pipeline_dry_run_market_prism_row_id_is_none(self):
        """In dry_run=True, market_prism_row_id must be None (no DB write happened)."""
        pipeline = _import_pipeline()

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation"),
        ):
            result = pipeline.run_pipeline(dry_run=True)

        assert result["market_prism_row_id"] is None, (
            f"dry_run=True must produce market_prism_row_id=None; got {result['market_prism_row_id']!r}"
        )

    def test_run_pipeline_never_raises(self):
        """run_pipeline() must never raise even when all lenses fail.

        Any exception inside must be caught and reflected in error_count.
        """
        pipeline = _import_pipeline()

        def _exploding_lens(_data=None):
            raise RuntimeError("network timeout")

        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 999

        with (
            patch("ai_advisor._build_technicals_section", side_effect=_exploding_lens),
            patch("ai_advisor._build_sentiment_section", side_effect=_exploding_lens),
            patch("ai_advisor._build_derivatives_section", side_effect=_exploding_lens),
            patch("ai_advisor._build_macro_section", side_effect=_exploding_lens),
            patch("ai_advisor._build_fundamentals_section", side_effect=_exploding_lens),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            try:
                result = pipeline.run_pipeline(dry_run=False)
            except Exception as exc:
                pytest.fail(
                    f"run_pipeline() must never raise, but raised {type(exc).__name__}: {exc}"
                )

        assert isinstance(result, dict), "run_pipeline() must return a dict even on total-failure"


# ---------------------------------------------------------------------------
# AC-2 — Per-lens isolation: a failing section does not abort the pipeline
# ---------------------------------------------------------------------------


class TestPerLensIsolation:
    """AC-2: a raising _build_<lens>_section does not abort the pipeline."""

    def test_one_failing_lens_does_not_abort(self):
        """When one lens section raises, the pipeline continues and still writes.

        Specifically: the remaining lenses are still attempted (lenses_attempted == 5),
        the failed lens appears in per_lens_digest as available=False, and the
        MARKET_PRISM observation is still persisted.
        """
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 42

        def _exploding_macro(_data=None):
            raise ConnectionError("FRED unreachable")

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_available("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", side_effect=_exploding_macro),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_available("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            result = pipeline.run_pipeline(dry_run=False)

        assert result["lenses_attempted"] == 5, (
            f"All 5 lenses must be attempted even when one raises; got {result['lenses_attempted']}"
        )
        assert result["error_count"] >= 1, (
            f"error_count must reflect the failed lens; got {result['error_count']}"
        )
        assert captured, "MARKET_PRISM observation must still be written even with a failing lens"

        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        digest = raw.get("per_lens_digest", {})
        assert "macro" in digest, (
            f"per_lens_digest must include 'macro' even when it failed; got keys: {list(digest)}"
        )
        macro_block = digest["macro"]
        assert macro_block.get("available") is False, (
            f"failed macro lens must appear as available=False in per_lens_digest; got {macro_block}"
        )

    def test_failing_lens_reason_is_d1_compliant(self):
        """The reason stored for a failing lens must be type(exc).__name__, not str(exc).

        D-1 contract: no raw exception message (may contain hosts/partial credentials).
        """
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 42

        secret_message = "connection refused: redis://internal-host:6379/secret-key-abc123"

        def _exploding_fundamentals(_data=None):
            raise ConnectionRefusedError(secret_message)

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch("ai_advisor._build_fundamentals_section", side_effect=_exploding_fundamentals),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured, "Observation must be written"
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        raw_str = json.dumps(raw)
        assert secret_message not in raw_str, (
            f"D-1 violation: raw exception message leaked into raw_response. "
            f"Secret message was found in persisted output."
        )
        assert "ConnectionRefusedError" in raw_str, (
            f"D-1: type(exc).__name__ = 'ConnectionRefusedError' must appear in per_lens_digest reason"
        )

    def test_all_lenses_failing_still_writes_observation(self):
        """Even when all 5 lenses raise, the pipeline writes the MARKET_PRISM observation."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 42

        def _exploding(_data=None):
            raise OSError("timeout")

        with (
            patch("ai_advisor._build_technicals_section", side_effect=_exploding),
            patch("ai_advisor._build_sentiment_section", side_effect=_exploding),
            patch("ai_advisor._build_derivatives_section", side_effect=_exploding),
            patch("ai_advisor._build_macro_section", side_effect=_exploding),
            patch("ai_advisor._build_fundamentals_section", side_effect=_exploding),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            result = pipeline.run_pipeline(dry_run=False)

        assert captured, "Observation must be written even when all lenses fail"
        assert result["error_count"] == 5, (
            f"error_count must be 5 when all lenses fail; got {result['error_count']}"
        )


# ---------------------------------------------------------------------------
# AC-3 — Market Prism observation always written
# ---------------------------------------------------------------------------


class TestMarketPrismAlwaysWritten:
    """AC-3: every non-dry_run pipeline call writes exactly one MARKET_PRISM observation."""

    def test_market_prism_observation_written_on_normal_run(self):
        """A normal run with some available lenses writes exactly one MARKET_PRISM row."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 100

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_available("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_available("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        market_prism_calls = [c for c in captured if c.get("advisor_role") == "MARKET_PRISM"]
        assert len(market_prism_calls) == 1, (
            f"Exactly one MARKET_PRISM observation must be written per run; "
            f"got {len(market_prism_calls)}. All captured advisor_roles: "
            f"{[c.get('advisor_role') for c in captured]}"
        )

    def test_market_prism_written_when_all_lenses_unavailable(self):
        """When all 5 lenses are available=False, the observation still writes with limited-inputs verdict."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 101

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        market_prism_calls = [c for c in captured if c.get("advisor_role") == "MARKET_PRISM"]
        assert len(market_prism_calls) == 1, (
            "MARKET_PRISM observation must be written even when all lenses unavailable"
        )

        verdict = market_prism_calls[0].get("verdict", "")
        assert verdict == "limited-inputs", (
            f"When all lenses unavailable, verdict must be 'limited-inputs'; got {verdict!r}"
        )

    def test_market_prism_not_written_in_dry_run(self):
        """In dry_run=True, NO insert_advisor_observation call must occur."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 102

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=True)

        assert not captured, (
            f"dry_run=True must not call insert_advisor_observation; got {len(captured)} calls"
        )

    def test_market_prism_row_id_in_result(self):
        """result['market_prism_row_id'] must be the row id returned by insert_advisor_observation."""
        pipeline = _import_pipeline()
        expected_row_id = 9999

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", return_value=expected_row_id),
        ):
            result = pipeline.run_pipeline(dry_run=False)

        assert result["market_prism_row_id"] == expected_row_id, (
            f"result['market_prism_row_id'] must be the row id from insert_advisor_observation; "
            f"got {result['market_prism_row_id']!r}"
        )


# ---------------------------------------------------------------------------
# AC-4 — Observation schema carries required fields and is JSON-serializable
# ---------------------------------------------------------------------------


class TestObservationSchema:
    """AC-4: raw_response carries all required fields and is JSON-serializable."""

    _REQUIRED_RAW_KEYS = {
        "run_ts",
        "per_lens_digest",
        "overall_sentiment",
        "sentiment_rationale",
        "available_lens_count",
        "total_lens_count",
        "sources",
    }
    _REQUIRED_LENS_NAMES = {"technicals", "sentiment", "derivatives", "macro", "fundamentals"}

    def _run_and_capture(self) -> dict:
        """Helper: run pipeline with 2 available lenses, capture raw_response."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 200

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_available("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_available("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured, "Observation must be written"
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)
        return raw

    def test_raw_response_has_required_keys(self):
        """raw_response must contain all required top-level keys."""
        raw = self._run_and_capture()
        missing = self._REQUIRED_RAW_KEYS - set(raw.keys())
        assert not missing, (
            f"raw_response is missing required keys: {missing}. Got keys: {sorted(raw.keys())}"
        )

    def test_per_lens_digest_contains_all_5_lenses(self):
        """per_lens_digest must contain entries for all 5 lens names."""
        raw = self._run_and_capture()
        digest = raw.get("per_lens_digest", {})
        missing = self._REQUIRED_LENS_NAMES - set(digest.keys())
        assert not missing, (
            f"per_lens_digest must contain all 5 lens names; missing: {missing}. "
            f"Got: {sorted(digest.keys())}"
        )

    def test_per_lens_digest_entries_have_available_key(self):
        """Each entry in per_lens_digest must have an 'available' key."""
        raw = self._run_and_capture()
        digest = raw.get("per_lens_digest", {})
        for lens_name, entry in digest.items():
            assert "available" in entry, (
                f"per_lens_digest[{lens_name!r}] must have 'available' key; got {entry}"
            )

    def test_available_lens_count_is_integer(self):
        """available_lens_count must be a non-negative integer."""
        raw = self._run_and_capture()
        count = raw.get("available_lens_count")
        assert isinstance(count, int) and count >= 0, (
            f"available_lens_count must be a non-negative integer; got {count!r}"
        )

    def test_total_lens_count_is_5(self):
        """total_lens_count must be 5 (the fixed set of 5 lenses)."""
        raw = self._run_and_capture()
        total = raw.get("total_lens_count")
        assert total == 5, f"total_lens_count must be 5; got {total!r}"

    def test_sources_is_list(self):
        """raw_response['sources'] must be a list (possibly empty)."""
        raw = self._run_and_capture()
        sources = raw.get("sources")
        assert isinstance(sources, list), (
            f"raw_response['sources'] must be a list; got {type(sources)}"
        )

    def test_raw_response_is_json_serializable(self):
        """raw_response must be JSON-serializable (no datetime objects, no sets, etc.)."""
        raw = self._run_and_capture()
        try:
            json.dumps(raw)
        except (TypeError, ValueError) as exc:
            pytest.fail(
                f"raw_response is not JSON-serializable: {exc}. "
                f"Non-serializable content prevents DB storage."
            )

    def test_run_ts_is_string(self):
        """run_ts must be a string (ISO timestamp or similar)."""
        raw = self._run_and_capture()
        run_ts = raw.get("run_ts")
        assert isinstance(run_ts, str) and run_ts, (
            f"run_ts must be a non-empty string; got {run_ts!r}"
        )

    def test_overall_sentiment_is_valid_value(self):
        """overall_sentiment must be one of the defined valid values."""
        valid_sentiments = {"risk-on", "neutral", "risk-off", "limited-inputs"}
        raw = self._run_and_capture()
        sentiment = raw.get("overall_sentiment")
        assert sentiment in valid_sentiments, (
            f"overall_sentiment must be one of {valid_sentiments}; got {sentiment!r}"
        )


# ---------------------------------------------------------------------------
# AC-5 — D-1 error contract
# ---------------------------------------------------------------------------


class TestD1ErrorContract:
    """AC-5: type(exc).__name__ only; no raw str(exc) in persisted or logged output."""

    def test_exception_message_not_in_raw_response(self):
        """Internal exception message (str(exc)) must not appear in raw_response."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 300

        secret_details = "redis://user:password@internal-cache:6379/0 failed at line 42"

        def _exploding_sentiment(_data=None):
            raise ConnectionError(secret_details)

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch("ai_advisor._build_sentiment_section", side_effect=_exploding_sentiment),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured, "Observation must be written"
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)
        raw_str = json.dumps(raw)

        assert secret_details not in raw_str, (
            "D-1 violation: raw exception message leaked into raw_response. "
            "Only type(exc).__name__ is permitted."
        )
        assert "ConnectionError" in raw_str, (
            "D-1: type(exc).__name__ ('ConnectionError') must appear in raw_response reason"
        )

    def test_exception_message_not_in_warning_logs(self, caplog):
        """Raw str(exc) must not appear in WARNING or higher log records."""
        pipeline = _import_pipeline()
        secret_details = "password=hunter2 in connection string"

        def _exploding_macro(_data=None):
            raise ValueError(secret_details)

        with caplog.at_level(logging.WARNING):
            with (
                patch(
                    "ai_advisor._build_technicals_section",
                    return_value=_stub_lens_unavailable("technicals"),
                ),
                patch(
                    "ai_advisor._build_sentiment_section",
                    return_value=_stub_lens_unavailable("sentiment"),
                ),
                patch(
                    "ai_advisor._build_derivatives_section",
                    return_value=_stub_lens_unavailable("derivatives"),
                ),
                patch("ai_advisor._build_macro_section", side_effect=_exploding_macro),
                patch(
                    "ai_advisor._build_fundamentals_section",
                    return_value=_stub_lens_unavailable("fundamentals"),
                ),
                patch("ai_advisor._build_client", return_value=MagicMock()),
                patch("database.insert_advisor_observation", return_value=400),
            ):
                pipeline.run_pipeline(dry_run=False)

        warning_plus_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert secret_details not in warning_plus_text, (
            "D-1 violation: raw exception message appeared in WARNING+ log records. "
            "Only type(exc).__name__ is permitted."
        )


# ---------------------------------------------------------------------------
# AC-6 — CC-2 import-boundary: no advisor import on execution path
# ---------------------------------------------------------------------------


class TestImportBoundary:
    """AC-6: alpha_bot_execution.py must not import advisors.lens_pipeline or any advisors.*."""

    def test_alpha_bot_execution_does_not_import_lens_pipeline(self):
        """Static scan: alpha_bot_execution.py source must not contain 'lens_pipeline'."""
        source_path = _REPO_ROOT / "alpha_bot_execution.py"
        assert source_path.exists(), f"alpha_bot_execution.py not found at {source_path}"
        source = source_path.read_text(encoding="utf-8")
        assert "lens_pipeline" not in source, (
            "CC-2 violation: alpha_bot_execution.py imports lens_pipeline. "
            "The lens pipeline must NEVER be on the 1-minute execution path."
        )

    def test_alpha_bot_execution_does_not_import_advisors_module(self):
        """Static scan: alpha_bot_execution.py must not import any advisors.* module."""
        source_path = _REPO_ROOT / "alpha_bot_execution.py"
        source = source_path.read_text(encoding="utf-8")
        # Look for any `import advisors` or `from advisors` at module level
        import re

        advisor_imports = re.findall(
            r"^(?:import advisors|from advisors)\b.*$",
            source,
            re.MULTILINE,
        )
        assert not advisor_imports, (
            f"CC-2 violation: alpha_bot_execution.py has top-level advisors imports: {advisor_imports}"
        )


# ---------------------------------------------------------------------------
# AC-7 — Scheduler wiring: app.py registers _run_lens_pipeline daily
# ---------------------------------------------------------------------------


class TestSchedulerWiring:
    """AC-7: run_scheduler() in app.py registers _run_lens_pipeline; wrapper is non-blocking."""

    def test_run_scheduler_contains_lens_pipeline_registration(self):
        """Static scan: app.py run_scheduler() must reference _run_lens_pipeline."""
        source_path = _REPO_ROOT / "app.py"
        source = source_path.read_text(encoding="utf-8")
        assert "_run_lens_pipeline" in source, (
            "AC-7: app.py must define and register _run_lens_pipeline in run_scheduler(). "
            "Currently not found — implementation missing."
        )

    def test_run_scheduler_uses_daily_schedule(self):
        """Static scan: the _run_lens_pipeline registration uses schedule.every().day."""
        source_path = _REPO_ROOT / "app.py"
        source = source_path.read_text(encoding="utf-8")
        import re

        # Accept any schedule.every().day.at(...).do(_run_lens_pipeline) pattern
        pattern = r"schedule\.every\(\)\.day\.at\([^\)]+\)\.do\(_run_lens_pipeline\)"
        match = re.search(pattern, source)
        assert match, (
            "AC-7: app.py must register _run_lens_pipeline via schedule.every().day.at(...).do(). "
            f"Pattern not found in app.py. Found scheduler registrations: "
            f"{re.findall(r'schedule\\.every.*', source)}"
        )

    def test_lens_pipeline_wrapper_spawns_daemon_thread(self):
        """Static scan: _run_lens_pipeline wrapper must spawn a daemon=True Thread."""
        source_path = _REPO_ROOT / "app.py"
        source = source_path.read_text(encoding="utf-8")
        import re

        # Find the _run_lens_pipeline function body and check for daemon=True
        # Look for threading.Thread with daemon=True near the _run_lens_pipeline context
        assert "daemon=True" in source, (
            "AC-7: _run_lens_pipeline wrapper must spawn a daemon=True thread "
            "to avoid blocking the scheduler. 'daemon=True' not found in app.py."
        )

    def test_lens_pipeline_uses_lazy_import_in_worker(self):
        """Static scan: app.py must not have a module-level 'from advisors.lens_pipeline import'."""
        source_path = _REPO_ROOT / "app.py"
        source = source_path.read_text(encoding="utf-8")
        import re

        # Module-level import = not inside a function (this is a heuristic; check for
        # the import being inside a function body via indentation check)
        # Conservative: check that if lens_pipeline appears in the file, it's not in
        # the top-level import block (first 100 lines before any `def`)
        lines = source.splitlines()
        in_function = False
        for i, line in enumerate(lines[:150]):
            stripped = line.strip()
            if stripped.startswith("def "):
                in_function = True
            if not in_function and "from advisors.lens_pipeline" in line:
                pytest.fail(
                    f"AC-7/CC-2: app.py has module-level 'from advisors.lens_pipeline' import at line {i + 1}. "
                    "The import must be lazy (inside the worker function), not at module level."
                )
        # Also ensure it's not a top-level bare import
        for i, line in enumerate(lines[:150]):
            if (
                not in_function
                and "import advisors.lens_pipeline" in line
                and not line.strip().startswith("#")
            ):
                pytest.fail(f"AC-7/CC-2: app.py has module-level advisor import at line {i + 1}.")


# ---------------------------------------------------------------------------
# AC-8 — Sources round-trip: build_citation gate applied
# ---------------------------------------------------------------------------


class TestSourcesRoundTrip:
    """AC-8: raw_response['sources'] contains only build_citation-valid dicts."""

    def test_valid_sources_from_available_lens_appear_in_raw_response(self):
        """Citations from an available lens must appear in raw_response['sources']."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 500

        valid_source = {
            "title": "FRED 10-yr yield",
            "url": "https://fred.stlouisfed.org/series/DGS10",
            "published": "2026-06-13",
            "lens": "macro",
        }

        macro_block = {
            "lens": "macro",
            "available": True,
            "payload": {"stub": True},
            "sources": [valid_source],
        }

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=macro_block),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        sources = raw.get("sources", [])
        persisted_urls = [s.get("url") for s in sources if isinstance(s, dict)]
        assert valid_source["url"] in persisted_urls, (
            f"Valid citation URL must appear in raw_response['sources']; got: {persisted_urls}"
        )

    def test_malformed_citations_dropped(self):
        """Citations missing 'url' or with non-https scheme must be dropped."""
        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 501

        # Mix of valid and malformed
        mixed_sources = [
            {
                "title": "Valid",
                "url": "https://example.com/article",
                "published": "2026-06-13",
                "lens": "sentiment",
            },
            {"title": "No URL", "published": "2026-06-13", "lens": "sentiment"},
            {
                "title": "FTP bad",
                "url": "ftp://example.com/data",
                "published": "2026-06-13",
                "lens": "sentiment",
            },
        ]

        sentiment_block = {
            "lens": "sentiment",
            "available": True,
            "payload": {"article_count": 3},
            "sources": mixed_sources,
        }

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch("ai_advisor._build_sentiment_section", return_value=sentiment_block),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        sources = raw.get("sources", [])
        urls = [s.get("url", "") for s in sources if isinstance(s, dict)]

        assert "https://example.com/article" in urls, "Valid citation must survive filtering"
        assert not any("ftp://" in (u or "") for u in urls), "ftp:// citation must be filtered out"
        assert all(u for u in urls if u is not None), (
            "All persisted citations must have a non-empty url"
        )


# ---------------------------------------------------------------------------
# AC-9 — database.get_latest_market_prism_summary()
# ---------------------------------------------------------------------------


class TestGetLatestMarketPrismSummary:
    """AC-9: database.get_latest_market_prism_summary() returns most recent MARKET_PRISM row or None."""

    def test_function_exists_on_database_module(self):
        """database module must export get_latest_market_prism_summary."""
        db = _import_database()
        fn = getattr(db, "get_latest_market_prism_summary", None)
        assert callable(fn), (
            f"database must export get_latest_market_prism_summary as a callable. Got: {fn!r}"
        )

    def test_returns_none_when_no_rows_exist(self):
        """Returns None when no MARKET_PRISM observations have been persisted."""
        db = _import_database()
        # _isolate_db autouse fixture in conftest.py ensures a clean DB for each test
        result = db.get_latest_market_prism_summary()
        assert result is None, (
            f"get_latest_market_prism_summary() must return None when no rows exist; got {result!r}"
        )

    def test_returns_dict_when_row_exists(self):
        """Returns a dict when at least one MARKET_PRISM observation exists."""
        db = _import_database()
        # Insert a MARKET_PRISM row directly, then retrieve it
        raw_payload = {
            "run_ts": "2026-06-13T03:00:00Z",
            "per_lens_digest": {
                "technicals": {"available": False, "reason": "stub"},
                "sentiment": {"available": False, "reason": "stub"},
                "derivatives": {"available": False, "reason": "stub"},
                "macro": {"available": False, "reason": "stub"},
                "fundamentals": {"available": False, "reason": "stub"},
            },
            "overall_sentiment": "limited-inputs",
            "sentiment_rationale": "no lenses available",
            "available_lens_count": 0,
            "total_lens_count": 5,
            "sources": [],
        }
        db.insert_advisor_observation(
            advisor_role="MARKET_PRISM",
            subject_type="portfolio",
            subject_id="global",
            verdict="limited-inputs",
            raw_response=raw_payload,
        )

        result = db.get_latest_market_prism_summary()
        assert isinstance(result, dict), (
            f"get_latest_market_prism_summary() must return a dict when a row exists; "
            f"got {type(result)}"
        )

    def test_returns_most_recent_row(self):
        """Returns the most recently inserted MARKET_PRISM row (newest-first)."""
        db = _import_database()

        def _insert(run_ts: str, sentiment: str) -> None:
            db.insert_advisor_observation(
                advisor_role="MARKET_PRISM",
                subject_type="portfolio",
                subject_id="global",
                verdict=sentiment,
                raw_response={
                    "run_ts": run_ts,
                    "per_lens_digest": {},
                    "overall_sentiment": sentiment,
                    "sentiment_rationale": "test",
                    "available_lens_count": 0,
                    "total_lens_count": 5,
                    "sources": [],
                },
            )

        _insert("2026-06-12T03:00:00Z", "risk-off")
        _insert("2026-06-13T03:00:00Z", "neutral")  # newer

        result = db.get_latest_market_prism_summary()
        # The most recent row must be the one with run_ts 2026-06-13
        raw = result.get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        run_ts = raw.get("run_ts", "")
        assert "2026-06-13" in run_ts, (
            f"get_latest_market_prism_summary must return the most recent row; "
            f"got run_ts={run_ts!r} (expected 2026-06-13)"
        )

    def test_is_advisory_only(self):
        """The row returned must have is_advisory_only=1 (hard-wired by insert_advisor_observation)."""
        db = _import_database()
        db.insert_advisor_observation(
            advisor_role="MARKET_PRISM",
            subject_type="portfolio",
            subject_id="global",
            verdict="neutral",
            raw_response={
                "run_ts": "2026-06-13T03:00:00Z",
                "per_lens_digest": {},
                "overall_sentiment": "neutral",
                "sentiment_rationale": "t",
                "available_lens_count": 0,
                "total_lens_count": 5,
                "sources": [],
            },
        )
        result = db.get_latest_market_prism_summary()
        assert result is not None
        is_advisory_only = result.get("is_advisory_only")
        assert is_advisory_only == 1, (
            f"MARKET_PRISM observation must be advisory-only (is_advisory_only=1); "
            f"got {is_advisory_only!r}"
        )


# ---------------------------------------------------------------------------
# Golden fixture: market_prism_observation_schema.json
# ---------------------------------------------------------------------------


class TestGoldenFixtureSchemaContract:
    """AC-4 (fixture-backed): market_prism_observation_schema.json schema contract.

    The fixture defines the EXPECTED KEY PRESENCE in the raw_response — not the values
    (which are producer-computed and must never be hardcoded per global feedback rule
    feedback_no_hardcoded_test_values).
    """

    _FIXTURE_PATH = (
        _REPO_ROOT
        / "tests"
        / "fixtures"
        / "ai_advisor"
        / "cycle4"
        / "market_prism_observation_schema.json"
    )

    def test_fixture_exists(self):
        """The schema fixture file must exist at the expected path."""
        if not self._FIXTURE_PATH.exists():
            pytest.skip(
                f"Schema fixture not yet committed at {self._FIXTURE_PATH}. "
                "Create tests/fixtures/ai_advisor/cycle4/market_prism_observation_schema.json "
                "with the required_raw_keys and required_lens_names contract."
            )

    def test_raw_response_matches_fixture_schema(self):
        """raw_response keys match the fixture schema contract."""
        if not self._FIXTURE_PATH.exists():
            pytest.skip("Schema fixture not yet committed")

        with self._FIXTURE_PATH.open(encoding="utf-8") as fh:
            schema = json.load(fh)

        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 600

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_unavailable("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=MagicMock()),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        required_keys = schema.get("required_raw_keys", [])
        for key in required_keys:
            assert key in raw, (
                f"Fixture schema contract: raw_response must contain key {key!r}; "
                f"got keys: {sorted(raw.keys())}"
            )

        required_lens_names = schema.get("required_lens_names", [])
        digest = raw.get("per_lens_digest", {})
        for lens_name in required_lens_names:
            assert lens_name in digest, (
                f"Fixture schema contract: per_lens_digest must contain lens {lens_name!r}; "
                f"got: {sorted(digest.keys())}"
            )


# ---------------------------------------------------------------------------
# Robust Claude response parsing — confirmed live defect 2026-06-13
#
# Real Claude Haiku returns JSON wrapped in ```json...``` fences or preceded by
# prose.  The original code did json.loads(raw_text) which always raised
# JSONDecodeError, causing every synthesis to degrade to "limited-inputs".
# These tests verify _extract_json_object and the _synthesize_via_claude path.
# ---------------------------------------------------------------------------


def _make_claude_response_mock(text: str) -> MagicMock:
    """Build a MagicMock that mimics anthropic SDK response.content[0].text."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_client_mock(response_text: str) -> MagicMock:
    """Build a MagicMock client whose messages.create returns the given text."""
    response = _make_claude_response_mock(response_text)
    client = MagicMock()
    client.messages.create.return_value = response
    return client


_VALID_JSON_PAYLOAD = (
    '{"overall_sentiment": "risk-on", "sentiment_rationale": "Equities led by momentum."}'
)


class TestRobustClaudeJsonParsing:
    """Regression tests for the live defect: Claude Haiku wraps JSON in code fences.

    Defect confirmed 2026-06-13 nightly run: json.loads(raw_text) raised
    JSONDecodeError on every response; synthesis always degraded to limited-inputs.
    Fix: _extract_json_object strips fences and locates the first { ... } block.
    """

    def test_fenced_json_response_parsed_correctly(self):
        """A response with ```json ... ``` fences must parse to correct sentiment + rationale.

        This is the primary regression case: Claude Haiku wraps its response in
        markdown code fences, causing bare json.loads() to raise JSONDecodeError.
        The fix must strip the fences and parse the inner JSON correctly.
        """
        fenced_response = f"```json\n{_VALID_JSON_PAYLOAD}\n```"
        client_mock = _make_client_mock(fenced_response)

        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 700

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_available("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=client_mock),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            result = pipeline.run_pipeline(dry_run=False)

        assert captured, "Observation must be written"
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        sentiment = raw.get("overall_sentiment")
        rationale = raw.get("sentiment_rationale")

        assert sentiment == "risk-on", (
            f"Fenced JSON response must parse correctly; expected 'risk-on', got {sentiment!r}. "
            "This is the primary live defect regression: bare json.loads() fails on fenced output."
        )
        assert rationale and rationale != "Synthesis returned no rationale.", (
            f"Rationale must be parsed from the response, not the fallback sentinel; got {rationale!r}"
        )
        assert sentiment != "limited-inputs", (
            "Fenced JSON response must NOT degrade to limited-inputs — that was the bug."
        )

    def test_prose_then_json_response_parsed_correctly(self):
        """A response with leading prose before the JSON object must parse correctly.

        Claude sometimes adds an explanatory sentence before the JSON payload.
        The fix must skip the prose and extract the first { ... } object.
        """
        prose_response = (
            "Based on the available lens data, here is my assessment:\n\n" + _VALID_JSON_PAYLOAD
        )
        client_mock = _make_client_mock(prose_response)

        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 701

        with (
            patch(
                "ai_advisor._build_technicals_section",
                return_value=_stub_lens_available("technicals"),
            ),
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=_stub_lens_unavailable("sentiment"),
            ),
            patch(
                "ai_advisor._build_derivatives_section",
                return_value=_stub_lens_unavailable("derivatives"),
            ),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch(
                "ai_advisor._build_fundamentals_section",
                return_value=_stub_lens_unavailable("fundamentals"),
            ),
            patch("ai_advisor._build_client", return_value=client_mock),
            patch("database.insert_advisor_observation", side_effect=_capture),
        ):
            pipeline.run_pipeline(dry_run=False)

        assert captured, "Observation must be written"
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        sentiment = raw.get("overall_sentiment")
        assert sentiment == "risk-on", (
            f"Prose-prefixed JSON response must parse correctly; "
            f"expected 'risk-on', got {sentiment!r}."
        )
        assert sentiment != "limited-inputs", (
            "Prose-prefixed JSON response must NOT degrade to limited-inputs."
        )

    def test_genuinely_unparseable_response_degrades_gracefully(self):
        """A response with no JSON object at all must degrade to limited-inputs without raising.

        The graceful-degradation path must still hold: if Claude returns prose
        with no JSON object, the pipeline must not raise and must return
        limited-inputs with a D-1-compliant rationale (type name only, no raw text).
        """
        unparseable_response = (
            "I'm sorry, I cannot provide a market sentiment analysis at this time "
            "due to insufficient data from the available lens sources."
        )
        client_mock = _make_client_mock(unparseable_response)

        pipeline = _import_pipeline()
        captured: list[dict] = []

        def _capture(**kwargs):
            captured.append(kwargs)
            return 702

        try:
            with (
                patch(
                    "ai_advisor._build_technicals_section",
                    return_value=_stub_lens_available("technicals"),
                ),
                patch(
                    "ai_advisor._build_sentiment_section",
                    return_value=_stub_lens_unavailable("sentiment"),
                ),
                patch(
                    "ai_advisor._build_derivatives_section",
                    return_value=_stub_lens_unavailable("derivatives"),
                ),
                patch(
                    "ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")
                ),
                patch(
                    "ai_advisor._build_fundamentals_section",
                    return_value=_stub_lens_unavailable("fundamentals"),
                ),
                patch("ai_advisor._build_client", return_value=client_mock),
                patch("database.insert_advisor_observation", side_effect=_capture),
            ):
                result = pipeline.run_pipeline(dry_run=False)
        except Exception as exc:
            pytest.fail(
                f"run_pipeline() must never raise even on unparseable Claude response; "
                f"raised {type(exc).__name__}: {exc}"
            )

        assert isinstance(result, dict), "run_pipeline() must return a dict"
        assert captured, "Observation must still be written even on unparseable response"
        raw = captured[0].get("raw_response", {})
        if isinstance(raw, str):
            raw = json.loads(raw)

        sentiment = raw.get("overall_sentiment")
        rationale = raw.get("sentiment_rationale", "")

        assert sentiment == "limited-inputs", (
            f"Unparseable Claude response must degrade to 'limited-inputs'; got {sentiment!r}"
        )
        # D-1: rationale must not contain the raw unparseable text.
        assert unparseable_response not in rationale, (
            "D-1 violation: raw Claude response text must not appear in persisted rationale."
        )
        # Rationale must contain the exception type name (JSONDecodeError), not raw prose.
        assert "JSONDecodeError" in rationale, (
            f"D-1: rationale for a parse failure must contain 'JSONDecodeError' (type name); "
            f"got {rationale!r}"
        )
