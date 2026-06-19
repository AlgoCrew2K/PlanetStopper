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

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers — canned producer return shapes (shape/presence only, no literals)
# ---------------------------------------------------------------------------


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
    """CC-2 structural guard for _build_sentiment_section's lens_warehouse import.

    The three persist-wiring tests that previously lived here
    (test_persist_called_after_successful_gdelt_fetch,
     test_persist_called_with_available_false_when_gdelt_down,
     test_persist_payload_is_not_fabricated_when_down)
    were patching the OLD seam (lens_gdelt._fetch_gdelt_sentiment +
    ai_advisor._fetch_with_backoff).  The cycle moved _build_sentiment_section
    to call news_corpus.build_news_corpus() — those patches no longer intercepted
    _fetch_all_feeds(), which issued live RSS HTTP on every run, making the
    available=False test non-deterministic.

    Superseded by TestWarehousePersistence in test_news_corpus.py
    (test_persist_lens_snapshot_called_on_success_path,
     test_persist_lens_snapshot_called_on_unavailable_path,
     test_persist_payload_contains_tone_and_corpus_summary)
    which patch news_corpus.build_news_corpus directly (the correct seam)
    and fire zero live HTTP.

    Only the CC-2 lazy-import guard is retained below — it fires no HTTP.
    """

    def test_persist_lens_snapshot_is_lazy_imported_in_sentiment(self):
        """lens_warehouse must NOT be imported at the ai_advisor module level —
        it must be a lazy import (CC-2 boundary) inside _build_sentiment_section.

        FAILS if the implementer adds a top-level import instead of a lazy one.
        """
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

    def test_persist_called_with_available_false_when_all_fred_series_fail(self, monkeypatch):
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
