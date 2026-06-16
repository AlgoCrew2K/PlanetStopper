"""
RED tests for AC-4: Lens warehouse producer integration point (anti-hollow).

Plan AC-4: "the existing FRED/macro + GDELT/sentiment pulls call
`persist_lens_snapshot` after each fetch."

These tests assert that `_build_sentiment_section` (GDELT) and
`_build_macro_section` (FRED) each call `persist_lens_snapshot` after their
fetch — with the correct lens name, source, available flag (mirroring the
producer's own available field), and a non-fabricated payload.

Strategy:
- Mock the REAL network calls (lens_gdelt._fetch_gdelt_sentiment,
  _fetch_with_backoff / requests.get) so no live HTTP fires.
- Mock `advisors.lens_warehouse.persist_lens_snapshot` so no real DB write
  happens, but we can assert it was called with the right arguments.
- Do NOT mock the producers' math or availability logic — test the real call
  path end-to-end (except network + DB).

The wiring must be a lazy import (CC-2: never at module level in ai_advisor.py)
and off-execution-path (never on the trade/engine path).

ALL tests in this file MUST FAIL until the wiring is implemented.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — canned producer return shapes (shape/presence only, no literals)
# ---------------------------------------------------------------------------


def _gdelt_tone_result(available: bool, tone=None):
    """Minimal tone result shape from lens_gdelt._fetch_gdelt_sentiment."""
    return {
        "available": available,
        "tone": tone if available else None,
        "per_ticker": None,
        "source": "GDELT 2.0 DOC API timelinetone",
        "reason": None if available else "no_tone_data",
    }


def _artlist_response(articles=None):
    """Minimal mock Response for the GDELT artlist endpoint."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"articles": articles or []}
    return mock_resp


def _fred_response(series_id, value="1.23", date="2026-06-01"):
    """Minimal mock Response for a FRED observations endpoint call."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "observations": [{"value": value, "date": date}],
        "realtime_end": date,
    }
    return mock_resp


# ---------------------------------------------------------------------------
# AC-4a: GDELT sentiment section wires persist_lens_snapshot
# ---------------------------------------------------------------------------


class TestSentimentSectionWarehouseWiring:
    """_build_sentiment_section must call persist_lens_snapshot after its fetch."""

    def test_persist_called_after_successful_gdelt_fetch(self):
        """On a successful GDELT fetch (available=True), persist_lens_snapshot
        must be called once with lens='sentiment', available=True, and a
        non-None payload.

        FAILS until _build_sentiment_section adds a persist_lens_snapshot call.
        """
        import ai_advisor

        tone_result = _gdelt_tone_result(available=True, tone=0.1)
        artlist_resp = _artlist_response(articles=[])

        with (
            patch("advisors.lens_gdelt._fetch_gdelt_sentiment", return_value=tone_result),
            patch("ai_advisor._fetch_with_backoff", return_value=artlist_resp),
            patch("advisors.lens_warehouse.persist_lens_snapshot") as mock_persist,
        ):
            ai_advisor._build_sentiment_section()

        mock_persist.assert_called_once()
        kwargs = mock_persist.call_args
        # Accept positional or keyword — normalize to kwargs dict.
        all_kwargs = kwargs[1] if kwargs[1] else {}
        all_args = kwargs[0]

        # lens must be "sentiment"
        lens_val = all_kwargs.get("lens") or (all_args[0] if all_args else None)
        assert lens_val == "sentiment", (
            f"persist_lens_snapshot must be called with lens='sentiment'; got {lens_val!r}"
        )

        # available must be True (mirrors the block's available field)
        avail_val = all_kwargs.get("available")
        assert avail_val is True or avail_val == 1, (
            f"persist_lens_snapshot must be called with available=True on success; got {avail_val!r}"
        )

        # raw_payload must not be None (not fabricated)
        payload_val = all_kwargs.get("raw_payload")
        assert payload_val is not None, (
            "persist_lens_snapshot raw_payload must not be None on a successful fetch"
        )

    def test_persist_called_with_available_false_when_gdelt_down(self):
        """On a failed GDELT fetch (both tone and artlist fail), persist_lens_snapshot
        must be called with available=False — honest unavailability, no fabrication.

        FAILS until _build_sentiment_section adds a persist_lens_snapshot call.
        """
        import ai_advisor

        tone_result = _gdelt_tone_result(available=False)
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = ConnectionError("network down")

        with (
            patch("advisors.lens_gdelt._fetch_gdelt_sentiment", return_value=tone_result),
            patch("ai_advisor._fetch_with_backoff", return_value=failing_resp),
            patch("advisors.lens_warehouse.persist_lens_snapshot") as mock_persist,
        ):
            block = ai_advisor._build_sentiment_section()

        # The block itself must be available=False.
        assert block["available"] is False, (
            "Block must be available=False when both GDELT sources fail"
        )

        # persist must still be called — we record even down sources (AC-6).
        mock_persist.assert_called_once()
        kwargs = mock_persist.call_args
        all_kwargs = kwargs[1] if kwargs[1] else {}
        avail_val = all_kwargs.get("available")
        assert avail_val is False or avail_val == 0, (
            "persist_lens_snapshot must be called with available=False when GDELT is down; "
            f"got {avail_val!r}"
        )

    def test_persist_payload_is_not_fabricated_when_down(self):
        """When GDELT is down, the raw_payload passed to persist must NOT be a
        fabricated success payload — it must be None or a reason-bearing dict,
        never a dict pretending to have real tone/article data.

        FAILS until the wiring is present.
        """
        import ai_advisor

        tone_result = _gdelt_tone_result(available=False)
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = ConnectionError("network down")

        with (
            patch("advisors.lens_gdelt._fetch_gdelt_sentiment", return_value=tone_result),
            patch("ai_advisor._fetch_with_backoff", return_value=failing_resp),
            patch("advisors.lens_warehouse.persist_lens_snapshot") as mock_persist,
        ):
            ai_advisor._build_sentiment_section()

        # persist must have been called — if not, fail with a clear message.
        assert mock_persist.called, (
            "persist_lens_snapshot must be called even when GDELT is down "
            "(record the down-source row with available=False)"
        )
        kwargs = mock_persist.call_args
        all_kwargs = kwargs[1] if (kwargs and kwargs[1]) else {}
        payload_val = all_kwargs.get("raw_payload")

        # Payload must not look like fabricated success data (no tone_score key
        # with a real value, no article_count > 0).
        if isinstance(payload_val, dict):
            assert not (
                "tone_score" in payload_val
                and payload_val.get("tone_score") is not None
            ), (
                "persist raw_payload must not contain a non-None tone_score when GDELT is down "
                "(no fabricated data)"
            )

    def test_persist_lens_snapshot_is_lazy_imported_in_sentiment(self):
        """lens_warehouse must NOT be imported at the ai_advisor module level —
        it must be a lazy import (CC-2 boundary) inside _build_sentiment_section.

        FAILS if the implementer adds a top-level import instead of a lazy one.
        """
        import importlib
        import ai_advisor as ai_mod

        # If lens_warehouse is already in the ai_advisor module namespace as a
        # top-level attribute (from a module-level import), this test fails.
        # A lazy import inside the function body does NOT create a module-level
        # attribute on ai_advisor.
        assert not hasattr(ai_mod, "lens_warehouse"), (
            "lens_warehouse must NOT be imported at the ai_advisor module level "
            "(CC-2: lazy import inside _build_sentiment_section only)"
        )


# ---------------------------------------------------------------------------
# AC-4b: FRED macro section wires persist_lens_snapshot
# ---------------------------------------------------------------------------


class TestMacroSectionWarehouseWiring:
    """_build_macro_section must call persist_lens_snapshot after its fetch."""

    def test_persist_called_after_successful_fred_fetch(self, monkeypatch):
        """On a successful FRED fetch (available=True), persist_lens_snapshot
        must be called once with lens='macro', available=True, and a non-None
        payload containing the fetched series data.

        FAILS until _build_macro_section adds a persist_lens_snapshot call.
        """
        import ai_advisor

        monkeypatch.setenv("FRED_API_KEY", "test_key_placeholder")

        fred_resp = _fred_response("DGS10")

        with (
            patch("ai_advisor._fetch_with_backoff", return_value=fred_resp),
            patch("advisors.lens_warehouse.persist_lens_snapshot") as mock_persist,
        ):
            ai_advisor._build_macro_section()

        mock_persist.assert_called_once()
        kwargs = mock_persist.call_args
        all_kwargs = kwargs[1] if kwargs[1] else {}
        all_args = kwargs[0]

        lens_val = all_kwargs.get("lens") or (all_args[0] if all_args else None)
        assert lens_val == "macro", (
            f"persist_lens_snapshot must be called with lens='macro'; got {lens_val!r}"
        )

        avail_val = all_kwargs.get("available")
        assert avail_val is True or avail_val == 1, (
            f"persist_lens_snapshot must be called with available=True on success; got {avail_val!r}"
        )

        payload_val = all_kwargs.get("raw_payload")
        assert payload_val is not None, (
            "persist_lens_snapshot raw_payload must not be None on a successful FRED fetch"
        )

    def test_persist_not_called_when_fred_key_absent(self, monkeypatch):
        """When FRED_API_KEY is absent, _build_macro_section returns early with
        available=False. persist_lens_snapshot MAY or MAY NOT be called (both
        are acceptable — key-absent is a config issue, not a fetch failure).

        This test asserts that IF persist is called, it gets available=False
        (no fabricated success data when we never even tried to fetch).

        FAILS if persist is called with available=True when key is absent.
        """
        import ai_advisor

        monkeypatch.delenv("FRED_API_KEY", raising=False)

        with patch("advisors.lens_warehouse.persist_lens_snapshot") as mock_persist:
            block = ai_advisor._build_macro_section()

        assert block["available"] is False, (
            "Block must be available=False when FRED_API_KEY is not set"
        )

        # If persist was called, it must NOT have available=True.
        if mock_persist.called:
            kwargs = mock_persist.call_args
            all_kwargs = kwargs[1] if kwargs[1] else {}
            avail_val = all_kwargs.get("available")
            assert avail_val is not True and avail_val != 1, (
                "persist_lens_snapshot must NOT be called with available=True "
                "when FRED key is absent (no fetch occurred, no fabrication)"
            )

    def test_persist_called_with_available_false_when_all_fred_series_fail(
        self, monkeypatch
    ):
        """When FRED_API_KEY is set but all series fetches raise, persist must
        be called with available=False — honest unavailability, not suppressed.

        FAILS until the wiring is present.
        """
        import ai_advisor

        monkeypatch.setenv("FRED_API_KEY", "test_key_placeholder")

        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = ConnectionError("FRED down")

        with (
            patch("ai_advisor._fetch_with_backoff", return_value=failing_resp),
            patch("advisors.lens_warehouse.persist_lens_snapshot") as mock_persist,
        ):
            block = ai_advisor._build_macro_section()

        assert block["available"] is False

        mock_persist.assert_called_once()
        kwargs = mock_persist.call_args
        all_kwargs = kwargs[1] if kwargs[1] else {}
        avail_val = all_kwargs.get("available")
        assert avail_val is False or avail_val == 0, (
            "persist_lens_snapshot must be called with available=False when all FRED "
            f"series fail; got {avail_val!r}"
        )

    def test_persist_lens_snapshot_is_lazy_imported_in_macro(self):
        """lens_warehouse must be lazy-imported inside _build_macro_section,
        not at ai_advisor module level.

        FAILS if the implementer adds a top-level import.
        """
        import ai_advisor as ai_mod

        assert not hasattr(ai_mod, "lens_warehouse"), (
            "lens_warehouse must NOT be imported at the ai_advisor module level "
            "(CC-2: lazy import inside _build_macro_section only)"
        )
