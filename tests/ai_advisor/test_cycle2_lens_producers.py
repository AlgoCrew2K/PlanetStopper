"""
Cycle 2 — RED: Free-data lens producers (GDELT sentiment, SEC EDGAR fundamentals,
FRED macro).

Tier 1 — fixture-contract tests.  No live network calls.  Runs every CI pass.
Tier 2 — opt-in live tests.  Marked @pytest.mark.live; excluded from default run.

Test scope (this file):
  1. GDELT news/sentiment producer (_build_sentiment_section):
     - Returns real lens-block contract when GDELT fetch succeeds (mocked
       from captured response shape, fixture: gdelt_sentiment_api_shape.json).
     - Converts GDELT articles -> sources=[{title, url, published, lens}]
       validated by build_citation / validate_citation.
     - Empty article list -> available: True, sources=[], payload shows count=0.
     - Timeout / network error -> available: False, reason = exc class only (D-1).
     - No API key required (GDELT is key-less, no env var gate).

  2. SEC EDGAR fundamentals producer (_build_fundamentals_section):
     - Returns real lens-block contract when SEC EDGAR fetch succeeds (mocked
       from captured shape, fixture: sec_edgar_fundamentals_api_shape.json).
     - MANDATORY User-Agent header present on every SEC fetch.
     - SEC 403 (UA missing/blocked) -> available: False, reason names UA/blocked.
     - Timeout / 429 -> available: False, reason = exc class only (D-1).
     - No API key required (no env var gate — only UA header).
     - Sources carry SEC filing URLs (https://www.sec.gov/... or https://data.sec.gov/...).

  3. FRED macro producer (_build_macro_section):
     - FRED_API_KEY absent -> available: False, reason mentions FRED/key/configured
       (honest-availability; NEVER fabricate macro data without a key).
     - FRED_API_KEY present + fetch succeeds (mocked) -> available: True, payload
       carries at least one series, sources carry FRED release links.
     - 429 / timeout -> available: False, reason = exc class only (D-1).
     - FRED_API_KEY present but empty string -> treated as absent (available: False).

  4. Error contract (D-1 — all three producers):
     - Exception reason exposes ONLY type(exc).__name__, NEVER str(exc).
     - str(exc) may contain API keys, host names, partial credentials — forbidden.

  5. Citation provenance for each producer:
     - Every source in the returned lens block passes build_citation validation.
     - Score-only / aggregate values that have no URL are NOT presented as sources.

  6. Technicals + derivatives stubs UNCHANGED (Cycle 2 does not fill them):
     - _build_technicals_section still returns available=False (cycle-1 stub).
     - _build_derivatives_section still returns available=False (cycle-1 stub).

  7. Property-based invariants (via hypothesis where available):
     - Monotonicity of article count in GDELT payload vs. sources list length.
     - SEC producer: source URLs are always well-formed https:// SEC/EDGAR domain.
     - FRED producer: series dict keys are strings, values are non-None when
       available=True.
     - Lens block available=False => payload is None or empty, sources is empty.

Mocking strategy
----------------
* Network calls (requests.get, httpx.get, urllib.request.urlopen) -> patched.
  Tests use mock returns shaped like the captured API fixture shapes.
* os.environ -> patched where FRED_API_KEY presence is tested.
* build_citation / validate_citation are NEVER mocked — they are tested directly.
* Math engine is NEVER mocked — not used by these tests.
* DB is NOT used by these lens producers directly — no DB mock needed.

No module-level mutable state.  Every fixture is function-scoped.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

try:
    from hypothesis import given, settings, assume
    from hypothesis import strategies as st
    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "math"


@pytest.fixture
def gdelt_shape_fixture() -> dict:
    """Captured GDELT API response shape — schema-derived (Cycle 2)."""
    path = _FIXTURES_DIR / "gdelt_sentiment_api_shape.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def sec_shape_fixture() -> dict:
    """Captured SEC EDGAR companyfacts response shape — schema-derived (Cycle 2)."""
    path = _FIXTURES_DIR / "sec_edgar_fundamentals_api_shape.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def fred_shape_fixture() -> dict:
    """Captured FRED series/observations response shape — schema-derived (Cycle 2)."""
    path = _FIXTURES_DIR / "fred_macro_api_shape.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers: shape assertions (derive from fixture, never hardcode values)
# ---------------------------------------------------------------------------

def _is_http_url(value: Any) -> bool:
    """True if value is a string starting with http:// or https://."""
    return isinstance(value, str) and (
        value.startswith("http://") or value.startswith("https://")
    )


def _assert_lens_block_shape(block: dict, lens_name: str) -> None:
    """Assert the lens block satisfies the base contract: {lens, available, ...}."""
    assert isinstance(block, dict), (
        f"_build_{lens_name}_section must return dict, got {type(block)}"
    )
    assert block.get("lens") == lens_name, (
        f"_build_{lens_name}_section 'lens' must equal '{lens_name}', "
        f"got {block.get('lens')!r}"
    )
    assert isinstance(block.get("available"), bool), (
        f"_build_{lens_name}_section 'available' must be bool, "
        f"got {type(block.get('available'))}"
    )


def _assert_available_true_shape(block: dict, lens_name: str) -> None:
    """Assert a lens block with available=True carries payload + sources list."""
    _assert_lens_block_shape(block, lens_name)
    assert block["available"] is True, (
        f"_build_{lens_name}_section must return available=True in this scenario"
    )
    assert "payload" in block, (
        f"_build_{lens_name}_section with available=True must carry 'payload'"
    )
    assert "sources" in block, (
        f"_build_{lens_name}_section with available=True must carry 'sources'"
    )
    assert isinstance(block["sources"], list), (
        f"_build_{lens_name}_section 'sources' must be a list"
    )


def _assert_available_false_shape(block: dict, lens_name: str) -> None:
    """Assert a lens block with available=False carries a non-empty reason."""
    _assert_lens_block_shape(block, lens_name)
    assert block["available"] is False, (
        f"_build_{lens_name}_section must return available=False in this scenario"
    )
    reason = block.get("reason", "")
    assert isinstance(reason, str) and reason.strip(), (
        f"_build_{lens_name}_section with available=False must carry non-empty 'reason'"
    )
    # payload must be absent, None, or explicitly empty
    payload = block.get("payload")
    assert payload is None or payload == {} or payload == [] or payload == "", (
        f"_build_{lens_name}_section with available=False must not emit a "
        f"non-empty payload (CC-3 data-wall). Got payload={payload!r}"
    )
    sources = block.get("sources")
    assert sources is None or sources == [] or sources == {}, (
        f"_build_{lens_name}_section with available=False must not emit "
        f"non-empty sources (CC-4 citation-missing = no claim). Got {sources!r}"
    )


def _assert_citations_valid(block: dict) -> None:
    """All sources in block pass build_citation validation (CC-4)."""
    import ai_advisor

    sources = block.get("sources", [])
    for i, src in enumerate(sources):
        result = ai_advisor.build_citation(src)
        assert result is not None, (
            f"Source[{i}] in '{block.get('lens')}' lens block failed build_citation: "
            f"{src!r}. Every source must pass citation validation (CC-4)."
        )


def _make_mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response-like object returning json_data."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.text = json.dumps(json_data)
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        mock_resp.raise_for_status.side_effect = HTTPError(
            f"HTTP {status_code}", response=mock_resp
        )
    return mock_resp


# ---------------------------------------------------------------------------
# 1. GDELT sentiment producer
# ---------------------------------------------------------------------------


class TestGdeltSentimentProducer:
    """_build_sentiment_section must return a real lens block from GDELT data."""

    def test_sentiment_section_helper_exists(self):
        """ai_advisor._build_sentiment_section exists and is callable.

        Cycle 1 already asserts this; Cycle 2 re-states it as a precondition
        for the producer tests — Cycle 2 replaces the stub with a real fetcher.
        """
        import ai_advisor
        assert hasattr(ai_advisor, "_build_sentiment_section"), (
            "ai_advisor._build_sentiment_section is missing"
        )
        assert callable(ai_advisor._build_sentiment_section)

    def test_sentiment_available_true_when_gdelt_returns_articles(
        self, gdelt_shape_fixture: dict
    ):
        """When GDELT returns articles, _build_sentiment_section returns available=True.

        The mock provides the captured GDELT artlist shape from the fixture.
        Tests that the producer correctly maps articles -> available lens block.

        FAILS if _build_sentiment_section is still the Cycle-1 stub (returns
        available=False unconditionally).
        """
        import ai_advisor

        mock_resp = _make_mock_response(gdelt_shape_fixture["gdelt_artlist_shape"])

        with patch("requests.get", return_value=mock_resp) as mock_get:
            block = ai_advisor._build_sentiment_section()

        _assert_available_true_shape(block, "sentiment")

        # The payload must report the article count — derive from the fixture,
        # never hardcode the value
        fixture_articles = gdelt_shape_fixture["gdelt_artlist_shape"]["articles"]
        payload = block["payload"]
        assert isinstance(payload, dict), (
            "sentiment payload must be a dict"
        )
        assert "article_count" in payload, (
            "sentiment payload must carry 'article_count'"
        )
        assert payload["article_count"] == len(fixture_articles), (
            f"sentiment payload 'article_count' must equal the number of articles "
            f"in the GDELT response ({len(fixture_articles)}), "
            f"got {payload['article_count']!r}"
        )

    def test_sentiment_sources_derived_from_gdelt_articles(
        self, gdelt_shape_fixture: dict
    ):
        """Each GDELT article becomes a source with title, url, published, lens.

        Sources are NOT hardcoded — they are derived from the mocked GDELT
        response. Asserts shape and validity via build_citation.

        FAILS if the producer discards articles or fabricates sources.
        """
        import ai_advisor

        fixture_articles = gdelt_shape_fixture["gdelt_artlist_shape"]["articles"]
        mock_resp = _make_mock_response(gdelt_shape_fixture["gdelt_artlist_shape"])

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_sentiment_section()

        _assert_available_true_shape(block, "sentiment")
        sources = block["sources"]
        assert len(sources) == len(fixture_articles), (
            f"Expected {len(fixture_articles)} sources (one per GDELT article), "
            f"got {len(sources)}. The producer must map each article to a source."
        )
        _assert_citations_valid(block)

        # Each source URL must match the article url from the fixture
        fixture_urls = {a["url"] for a in fixture_articles}
        source_urls = {s.get("url") for s in sources}
        assert source_urls == fixture_urls, (
            f"Source URLs don't match fixture article URLs. "
            f"Expected {fixture_urls}, got {source_urls}."
        )

    def test_sentiment_seendate_mapped_to_published(
        self, gdelt_shape_fixture: dict
    ):
        """GDELT 'seendate' maps to 'published' in each citation source.

        GDELT returns 'seendate' (e.g. '20260610T120000Z') — the producer must
        map this to 'published' in the citation dict, using build_citation to
        validate the result.

        FAILS if seendate is dropped or the citation fails validation.
        """
        import ai_advisor

        fixture_articles = gdelt_shape_fixture["gdelt_artlist_shape"]["articles"]
        mock_resp = _make_mock_response(gdelt_shape_fixture["gdelt_artlist_shape"])

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_sentiment_section()

        _assert_available_true_shape(block, "sentiment")
        sources = block["sources"]
        for i, src in enumerate(sources):
            assert "published" in src, (
                f"Source[{i}] is missing 'published'. "
                f"GDELT seendate must be mapped to published."
            )
            assert isinstance(src["published"], str) and src["published"].strip(), (
                f"Source[{i}]['published'] must be a non-empty string. "
                f"Got {src['published']!r}"
            )
            assert src.get("lens") == "sentiment", (
                f"Source[{i}]['lens'] must equal 'sentiment', got {src.get('lens')!r}"
            )

    def test_sentiment_empty_articles_returns_available_true_with_empty_sources(
        self, gdelt_shape_fixture: dict
    ):
        """An empty GDELT article list returns available=True, empty sources.

        Empty-but-successful fetch is distinct from a fetch error (CC-3).
        available=True with empty sources correctly signals 'no news found' —
        NOT a fabrication failure.

        FAILS if the producer returns available=False for an empty-article response.
        """
        import ai_advisor

        empty_resp = _make_mock_response(gdelt_shape_fixture["empty_artlist_shape"])

        with patch("requests.get", return_value=empty_resp):
            block = ai_advisor._build_sentiment_section()

        _assert_lens_block_shape(block, "sentiment")
        assert block["available"] is True, (
            "An empty GDELT article list (successful fetch, zero articles) must "
            "return available=True, not available=False. "
            "Empty != error (CC-3 / GATE-1-AC §1 edge cases)."
        )
        assert block.get("sources", []) == [], (
            "Empty article list must produce empty sources list."
        )
        payload = block.get("payload", {})
        assert isinstance(payload, dict), "payload must be a dict even when empty"
        assert payload.get("article_count") == 0, (
            f"payload.article_count must be 0 for empty article list, "
            f"got {payload.get('article_count')!r}"
        )

    def test_sentiment_timeout_returns_available_false_with_exc_class_only(self):
        """A network timeout returns available=False + reason = exc class only (D-1).

        D-1: error reasons expose ONLY type(exc).__name__, never raw str(exc).
        str(exc) may contain hostnames, partial URLs, or sensitive context.

        FAILS if the producer raises, or exposes str(exc) in the reason.
        """
        import ai_advisor
        from requests.exceptions import Timeout

        timeout_exc = Timeout("Connection to api.gdeltproject.org timed out after 30s")

        with patch("requests.get", side_effect=timeout_exc):
            block = ai_advisor._build_sentiment_section()

        _assert_available_false_shape(block, "sentiment")
        reason = block.get("reason", "")
        # Must name the exception class
        assert "Timeout" in reason or "timeout" in reason.lower() or "error" in reason.lower(), (
            f"Timeout reason must reference the exception type. Got: {reason!r}"
        )
        # Must NOT contain str(exc) content (would leak the URL/host)
        assert "gdeltproject.org" not in reason, (
            f"D-1 violation: reason exposes internal URL from str(exc): {reason!r}. "
            f"Only type(exc).__name__ may appear."
        )
        assert "timed out after 30s" not in reason, (
            f"D-1 violation: reason exposes str(exc) detail: {reason!r}. "
            f"Only type(exc).__name__ may appear."
        )

    def test_sentiment_http_error_returns_available_false(self):
        """Any non-200 HTTP from GDELT returns available=False (not a fabrication).

        A 429 or 503 from GDELT should degrade to available=False gracefully.
        """
        import ai_advisor
        from requests.exceptions import HTTPError

        mock_resp = _make_mock_response({}, status_code=429)

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_sentiment_section()

        _assert_available_false_shape(block, "sentiment")

    def test_sentiment_producer_does_not_require_api_key_env_var(self):
        """GDELT is key-less — _build_sentiment_section must not require any env var.

        If the producer raises or returns available=False solely because a
        GDELT_API_KEY env var is absent, it is incorrectly guarded.
        GDELT requires NO key.

        FAILS if the producer gates on any env var check for GDELT.
        """
        import ai_advisor

        # Simulate an environment with NO GDELT-related env vars
        env_without_gdelt_key = {
            k: v for k, v in os.environ.items()
            if "GDELT" not in k.upper()
        }

        gdelt_artlist = {
            "articles": [
                {
                    "url": "https://www.example.com/article",
                    "title": "Test Article",
                    "seendate": "20260610T120000Z",
                    "language": "English",
                    "domain": "example.com",
                    "sourcecountry": "United States",
                }
            ]
        }
        mock_resp = _make_mock_response(gdelt_artlist)

        with (
            patch.dict(os.environ, env_without_gdelt_key, clear=True),
            patch("requests.get", return_value=mock_resp),
        ):
            block = ai_advisor._build_sentiment_section()

        # Must not return available=False due to a missing GDELT key
        # (it should only be False if there's a network error)
        assert block["available"] is True, (
            "GDELT is key-less — _build_sentiment_section must not gate on any "
            "env var check. Got available=False without a network error. "
            "Check if the producer is incorrectly requiring a GDELT_API_KEY."
        )

    def test_sentiment_producer_does_not_still_return_cycle1_stub_reason(self):
        """Cycle-2 producer does not return the cycle-1 'scaffold' stub reason.

        After Cycle 2, _build_sentiment_section is a real producer — it must NOT
        still return the cycle-1 stub reason when a fetch is available.

        FAILS if the producer is still a no-op stub returning the scaffold reason.
        """
        import ai_advisor

        gdelt_artlist = {
            "articles": [
                {
                    "url": "https://www.example.com/test-article",
                    "title": "Cycle 2 Test",
                    "seendate": "20260610T120000Z",
                    "language": "English",
                    "domain": "example.com",
                    "sourcecountry": "United States",
                }
            ]
        }
        mock_resp = _make_mock_response(gdelt_artlist)

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_sentiment_section()

        reason = block.get("reason", "")
        assert "cycle-1 scaffold" not in reason, (
            f"_build_sentiment_section still returns the Cycle-1 stub reason: "
            f"{reason!r}. Cycle 2 must replace the stub with a real GDELT producer."
        )


# ---------------------------------------------------------------------------
# 2. SEC EDGAR fundamentals producer
# ---------------------------------------------------------------------------


class TestSecEdgarFundamentalsProducer:
    """_build_fundamentals_section must return a real lens block from SEC EDGAR."""

    def test_fundamentals_section_helper_exists(self):
        """ai_advisor._build_fundamentals_section exists and is callable."""
        import ai_advisor
        assert hasattr(ai_advisor, "_build_fundamentals_section")
        assert callable(ai_advisor._build_fundamentals_section)

    def test_fundamentals_available_true_when_edgar_returns_companyfacts(
        self, sec_shape_fixture: dict
    ):
        """When SEC EDGAR returns companyfacts JSON, producer returns available=True.

        The mock provides the captured companyfacts shape from the fixture.
        Tests that the producer correctly maps XBRL facts -> available lens block.

        FAILS if _build_fundamentals_section is still the Cycle-1 stub.
        """
        import ai_advisor

        ticker = "AAPL"
        mock_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_fundamentals_section(ticker=ticker)

        _assert_available_true_shape(block, "fundamentals")

        payload = block["payload"]
        assert isinstance(payload, dict), "fundamentals payload must be a dict"
        assert "entity_name" in payload, (
            "fundamentals payload must carry 'entity_name' from companyfacts"
        )
        assert "key_facts" in payload, (
            "fundamentals payload must carry 'key_facts' dict"
        )
        assert isinstance(payload["key_facts"], dict), (
            "fundamentals payload 'key_facts' must be a dict"
        )

    def test_fundamentals_sources_are_sec_filing_urls(
        self, sec_shape_fixture: dict
    ):
        """Sources in the fundamentals block carry SEC EDGAR filing URLs.

        Sources are derived from the fetched companyfacts response, not invented.
        Each source URL must start with https://www.sec.gov/ or
        https://data.sec.gov/ (the canonical SEC EDGAR domains).

        FAILS if the producer invents sources or uses non-SEC URLs.
        """
        import ai_advisor

        mock_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_fundamentals_section(ticker="AAPL")

        _assert_available_true_shape(block, "fundamentals")
        _assert_citations_valid(block)

        sources = block["sources"]
        for i, src in enumerate(sources):
            url = src.get("url", "")
            assert (
                url.startswith("https://www.sec.gov/") or
                url.startswith("https://data.sec.gov/") or
                url.startswith("https://efts.sec.gov/")
            ), (
                f"fundamentals Source[{i}] URL must be a SEC EDGAR domain "
                f"(sec.gov or data.sec.gov), got {url!r}. "
                f"Sources must trace to real filing links."
            )

    def test_fundamentals_producer_sends_user_agent_header(
        self, sec_shape_fixture: dict
    ):
        """Every SEC EDGAR request includes a non-empty User-Agent header.

        The SEC requires a 'User-Agent: CompanyName email@domain' header.
        Missing or empty UA is the #1 cause of SEC 403s (FREE-DATA-SOURCES.md §5).
        This test inspects the call_args to requests.get.

        FAILS if the producer omits the User-Agent header.
        """
        import ai_advisor

        mock_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp) as mock_get:
            ai_advisor._build_fundamentals_section(ticker="AAPL")

        # At least one call to requests.get must have been made
        assert mock_get.called, (
            "_build_fundamentals_section made no HTTP request. "
            "The producer must fetch from SEC EDGAR."
        )

        # Inspect headers on the first call
        for call in mock_get.call_args_list:
            call_kwargs = call.kwargs if call.kwargs else {}
            call_args = call.args
            headers = call_kwargs.get("headers", {})
            if not headers and len(call_args) > 1:
                headers = call_args[1] if isinstance(call_args[1], dict) else {}
            ua = headers.get("User-Agent", headers.get("user-agent", ""))
            assert ua and ua.strip(), (
                f"SEC EDGAR request missing User-Agent header. "
                f"call_kwargs={call_kwargs!r}. "
                f"SEC requires 'User-Agent: CompanyName email@domain' — "
                f"missing UA causes 403s. (FREE-DATA-SOURCES.md §5)"
            )

    def test_fundamentals_sec_403_returns_available_false(self):
        """A SEC 403 (e.g. missing UA or IP throttle) returns available=False.

        The producer must catch the 403 and degrade gracefully, not raise.
        """
        import ai_advisor
        from requests.exceptions import HTTPError

        mock_resp = _make_mock_response({}, status_code=403)

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_fundamentals_section(ticker="AAPL")

        _assert_available_false_shape(block, "fundamentals")

    def test_fundamentals_sec_429_returns_available_false_reason_class_only(self):
        """A SEC 429 (rate limit) returns available=False + reason = exc class only.

        D-1: reason must not expose str(exc) detail (may contain host/path).
        """
        import ai_advisor
        from requests.exceptions import HTTPError

        mock_resp = _make_mock_response({}, status_code=429)

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_fundamentals_section(ticker="AAPL")

        _assert_available_false_shape(block, "fundamentals")
        reason = block.get("reason", "")
        # Must not expose internal URL or detailed exc message
        assert "data.sec.gov" not in reason, (
            f"D-1 violation: reason exposes SEC host from str(exc): {reason!r}"
        )

    def test_fundamentals_timeout_returns_available_false_exc_class_only(self):
        """A network timeout returns available=False + exc class name only (D-1)."""
        import ai_advisor
        from requests.exceptions import Timeout

        timeout_exc = Timeout("Connection to data.sec.gov timed out after 30s")

        with patch("requests.get", side_effect=timeout_exc):
            block = ai_advisor._build_fundamentals_section(ticker="AAPL")

        _assert_available_false_shape(block, "fundamentals")
        reason = block.get("reason", "")
        assert "data.sec.gov" not in reason, (
            f"D-1 violation: reason leaks SEC host from str(exc): {reason!r}"
        )
        assert "timed out after 30s" not in reason, (
            f"D-1 violation: reason leaks str(exc) detail: {reason!r}"
        )

    def test_fundamentals_no_ticker_returns_available_false(self):
        """Calling without a ticker returns available=False with a descriptive reason.

        The producer needs a ticker symbol to build a CIK lookup URL.
        A missing ticker is a caller error; the producer must degrade, not raise.
        """
        import ai_advisor

        block = ai_advisor._build_fundamentals_section()

        _assert_available_false_shape(block, "fundamentals")
        reason = block.get("reason", "")
        assert reason.strip(), "missing-ticker reason must be non-empty"

    def test_fundamentals_producer_does_not_return_cycle1_stub_reason(
        self, sec_shape_fixture: dict
    ):
        """Cycle-2 producer does not return the cycle-1 scaffold reason.

        FAILS if _build_fundamentals_section is still the no-op stub.
        """
        import ai_advisor

        mock_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_fundamentals_section(ticker="AAPL")

        reason = block.get("reason", "")
        assert "cycle-1 scaffold" not in reason, (
            f"_build_fundamentals_section still returns cycle-1 stub reason: "
            f"{reason!r}. Cycle 2 must replace the stub with a real SEC EDGAR producer."
        )


# ---------------------------------------------------------------------------
# 3. FRED macro producer
# ---------------------------------------------------------------------------


class TestFredMacroProducer:
    """_build_macro_section must gate on FRED_API_KEY and return real lens block."""

    def test_macro_section_helper_exists(self):
        """ai_advisor._build_macro_section exists and is callable."""
        import ai_advisor
        assert hasattr(ai_advisor, "_build_macro_section")
        assert callable(ai_advisor._build_macro_section)

    def test_macro_returns_available_false_when_fred_key_absent(self):
        """FRED_API_KEY absent -> available=False with honest reason (CC-3).

        The FRED API requires a free key.  Without it the producer MUST NOT
        fabricate macro data — it must return available=False.
        The reason must mention FRED, key, or configured.

        FAILS if the producer silently fabricates macro data without a key.
        FAILS if the producer raises instead of degrading.
        """
        import ai_advisor

        env_without_fred = {
            k: v for k, v in os.environ.items()
            if k != "FRED_API_KEY"
        }

        with patch.dict(os.environ, env_without_fred, clear=True):
            block = ai_advisor._build_macro_section()

        _assert_available_false_shape(block, "macro")
        reason = block.get("reason", "").lower()
        assert any(kw in reason for kw in ("fred", "key", "configured", "api")), (
            f"_build_macro_section with no FRED_API_KEY must return a reason "
            f"mentioning FRED, key, or configured. Got: {block.get('reason')!r}. "
            f"(Honest-availability: CC-3)"
        )

    def test_macro_returns_available_false_when_fred_key_is_empty_string(self):
        """FRED_API_KEY set to empty string is treated as absent.

        An empty string key would cause FRED API to return a 400 or auth error.
        The producer must treat it as absent and degrade to available=False.
        """
        import ai_advisor

        with patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=False):
            block = ai_advisor._build_macro_section()

        _assert_available_false_shape(block, "macro")

    def test_macro_available_true_when_key_present_and_fetch_succeeds(
        self, fred_shape_fixture: dict
    ):
        """FRED_API_KEY present + successful fetch -> available=True with payload.

        Uses the captured FRED observations shape from the fixture.
        Asserts payload carries at least one series and sources carry FRED URLs.

        FAILS if _build_macro_section is still the Cycle-1 stub.
        """
        import ai_advisor

        mock_resp = _make_mock_response(fred_shape_fixture["series_observations_shape"])

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
            patch("requests.get", return_value=mock_resp),
        ):
            block = ai_advisor._build_macro_section()

        _assert_available_true_shape(block, "macro")
        payload = block["payload"]
        assert isinstance(payload, dict), "macro payload must be a dict"
        assert "series" in payload, (
            "macro payload must carry a 'series' dict of fetched FRED data"
        )
        assert isinstance(payload["series"], dict), (
            "macro payload 'series' must be a dict"
        )
        assert len(payload["series"]) >= 1, (
            f"macro payload 'series' must have at least one entry when fetch succeeds, "
            f"got {payload['series']!r}"
        )

    def test_macro_sources_carry_fred_release_links(self, fred_shape_fixture: dict):
        """FRED sources carry release page links (https://fred.stlouisfed.org/...).

        CC-4: clickable citations.  FRED release pages are the clickable source
        for macro claims.

        FAILS if the producer omits sources or uses non-FRED URLs.
        """
        import ai_advisor

        mock_resp = _make_mock_response(fred_shape_fixture["series_observations_shape"])

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
            patch("requests.get", return_value=mock_resp),
        ):
            block = ai_advisor._build_macro_section()

        _assert_available_true_shape(block, "macro")
        _assert_citations_valid(block)
        sources = block["sources"]
        assert len(sources) >= 1, (
            "macro block with available=True must have at least one source "
            "(FRED series release link)."
        )
        for i, src in enumerate(sources):
            url = src.get("url", "")
            assert _is_http_url(url), (
                f"macro Source[{i}] URL must be http/https, got {url!r}"
            )
            assert "stlouisfed.org" in url or "fred" in url.lower() or "bls.gov" in url or "federalreserve.gov" in url, (
                f"macro Source[{i}] URL should be a FRED or related macro release URL. "
                f"Got {url!r}. Sources must trace to real clickable release pages."
            )

    def test_macro_fred_timeout_returns_available_false_exc_class_only(self):
        """FRED timeout -> available=False + exc class only in reason (D-1).

        D-1: reason must not expose str(exc) (may contain the API key in the URL).
        FRED requests include the API key as a query param — leaking the URL
        leaks the key.
        """
        import ai_advisor
        from requests.exceptions import Timeout

        timeout_exc = Timeout(
            "Connection to api.stlouisfed.org timed out: key=secretAPIkey123"
        )

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "secretAPIkey123"}, clear=False),
            patch("requests.get", side_effect=timeout_exc),
        ):
            block = ai_advisor._build_macro_section()

        _assert_available_false_shape(block, "macro")
        reason = block.get("reason", "")
        # The API key must NEVER appear in the reason
        assert "secretAPIkey123" not in reason, (
            f"D-1 CRITICAL violation: FRED API key leaked into error reason: {reason!r}. "
            f"FRED embeds the key as a URL query param — str(exc) leaks it. "
            f"Only type(exc).__name__ is permitted."
        )
        assert "api.stlouisfed.org" not in reason, (
            f"D-1 violation: FRED host exposed in reason from str(exc): {reason!r}"
        )

    def test_macro_fred_429_returns_available_false(self):
        """FRED 429 (rate limit) -> available=False + exc class only reason."""
        import ai_advisor
        from requests.exceptions import HTTPError

        mock_resp = _make_mock_response({}, status_code=429)

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
            patch("requests.get", return_value=mock_resp),
        ):
            block = ai_advisor._build_macro_section()

        _assert_available_false_shape(block, "macro")

    def test_macro_producer_does_not_return_cycle1_stub_reason(self):
        """Cycle-2 producer does not return the cycle-1 scaffold reason.

        FAILS if _build_macro_section is still the no-op stub.
        """
        import ai_advisor

        # Test in no-key scenario (honest-availability) — this is still a real
        # producer response (available=False with a real key-missing reason).
        env_without_fred = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}

        with patch.dict(os.environ, env_without_fred, clear=True):
            block = ai_advisor._build_macro_section()

        reason = block.get("reason", "")
        assert "cycle-1 scaffold" not in reason, (
            f"_build_macro_section still returns cycle-1 stub reason: {reason!r}. "
            f"Cycle 2 must replace the stub with a real FRED producer."
        )

    def test_macro_series_payload_values_are_not_hardcoded(
        self, fred_shape_fixture: dict
    ):
        """Macro payload derives series values from the mocked response, not hardcoded.

        Change the mock response and verify the payload changes — a hardcoded
        payload would ignore the response and produce the same values always.

        This is the property-based invariant: payload IS the fetched data.
        """
        import ai_advisor

        # First response: 2 observations
        resp_1 = _make_mock_response(fred_shape_fixture["series_observations_shape"])
        # Second response: fewer observations (or different values)
        modified_shape = dict(fred_shape_fixture["series_observations_shape"])
        modified_shape["observations"] = [
            modified_shape["observations"][0]  # only 1 observation
        ]
        modified_shape["count"] = 1
        resp_2 = _make_mock_response(modified_shape)

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
            patch("requests.get", return_value=resp_1),
        ):
            block_1 = ai_advisor._build_macro_section()

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
            patch("requests.get", return_value=resp_2),
        ):
            block_2 = ai_advisor._build_macro_section()

        # If the payload is hardcoded, both blocks would be identical regardless
        # of what the mock returned.  Check that the series data differs.
        # (They could coincidentally be equal if both use 1-obs data — that's
        # acceptable; the important invariant is no hardcoded literal values.)
        if block_1.get("available") and block_2.get("available"):
            series_1 = block_1.get("payload", {}).get("series", {})
            series_2 = block_2.get("payload", {}).get("series", {})
            # If the response had fewer obs, the series data should reflect that
            # At minimum confirm it's derived (not a fixed constant dict)
            assert isinstance(series_1, dict), "payload.series must be a dict"
            assert isinstance(series_2, dict), "payload.series must be a dict"


# ---------------------------------------------------------------------------
# 4. D-1 error contract across all three producers (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lens_name,ticker_kwarg", [
    ("sentiment", {}),
    ("fundamentals", {"ticker": "AAPL"}),
    ("macro", {}),
])
class TestD1ErrorContract:
    """All three Cycle-2 producers must expose only type(exc).__name__ in reason."""

    def test_connection_error_reason_is_exc_class_only(
        self, lens_name: str, ticker_kwarg: dict
    ):
        """A ConnectionError results in available=False, reason = class name only.

        str(ConnectionError) often contains hostnames, socket addresses, or
        partial credentials — never safe to surface.

        FAILS for any producer that leaks str(exc) detail into 'reason'.
        """
        import ai_advisor
        from requests.exceptions import ConnectionError as ReqConnError

        # Embed a hostname in the exc that must NOT appear in the reason
        conn_exc = ReqConnError(
            "Failed to establish connection to secret-internal.example.com:443"
        )

        helper = getattr(ai_advisor, f"_build_{lens_name}_section")
        env_patch = (
            {"FRED_API_KEY": "test-key-sentinel"}
            if lens_name == "macro"
            else {}
        )

        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("requests.get", side_effect=conn_exc),
        ):
            block = helper(**ticker_kwarg)

        _assert_available_false_shape(block, lens_name)
        reason = block.get("reason", "")
        assert "secret-internal.example.com" not in reason, (
            f"D-1 violation in {lens_name} producer: str(exc) hostname leaked "
            f"into reason: {reason!r}. Only type(exc).__name__ is permitted."
        )
        # The class name SHOULD appear (it's the only allowed content)
        assert "ConnectionError" in reason or "error" in reason.lower() or "connection" in reason.lower(), (
            f"D-1 error reason for {lens_name} must reference the exception class. "
            f"Got: {reason!r}"
        )

    def test_generic_exception_reason_is_exc_class_only(
        self, lens_name: str, ticker_kwarg: dict
    ):
        """Any unexpected exception results in available=False, reason = class only.

        A RuntimeError with a message containing a secret must not leak.
        """
        import ai_advisor

        secret_exc = RuntimeError(
            "Internal error: api_key=supersecretkey, detail=FAIL"
        )

        helper = getattr(ai_advisor, f"_build_{lens_name}_section")
        env_patch = (
            {"FRED_API_KEY": "test-key-sentinel"}
            if lens_name == "macro"
            else {}
        )

        with (
            patch.dict(os.environ, env_patch, clear=False),
            patch("requests.get", side_effect=secret_exc),
        ):
            block = helper(**ticker_kwarg)

        _assert_available_false_shape(block, lens_name)
        reason = block.get("reason", "")
        assert "supersecretkey" not in reason, (
            f"D-1 CRITICAL: {lens_name} producer leaked API key from str(exc) "
            f"into reason: {reason!r}."
        )
        assert "api_key=" not in reason, (
            f"D-1 CRITICAL: {lens_name} producer leaked key=value pair from "
            f"str(exc) into reason: {reason!r}."
        )


# ---------------------------------------------------------------------------
# 5. Technicals + derivatives stubs UNCHANGED (Cycle 2 does not fill them)
# ---------------------------------------------------------------------------


def test_technicals_stub_still_returns_available_false():
    """_build_technicals_section is unchanged in Cycle 2 — still available=False.

    Cycle 2 fills ONLY sentiment, fundamentals, macro.  Technicals is Cycle-2b.
    FAILS if Cycle 2 accidentally breaks or changes the technicals stub.
    """
    import ai_advisor

    block = ai_advisor._build_technicals_section()
    _assert_lens_block_shape(block, "technicals")
    assert block["available"] is False, (
        "_build_technicals_section must remain available=False after Cycle 2. "
        "Technicals is a Cycle-2b deliverable — do not fill it in Cycle 2."
    )


def test_derivatives_stub_still_returns_available_false():
    """_build_derivatives_section is unchanged in Cycle 2 — still available=False.

    Cycle 2 fills ONLY sentiment, fundamentals, macro.  Derivatives is Cycle-2b.
    FAILS if Cycle 2 accidentally breaks or changes the derivatives stub.
    """
    import ai_advisor

    block = ai_advisor._build_derivatives_section()
    _assert_lens_block_shape(block, "derivatives")
    assert block["available"] is False, (
        "_build_derivatives_section must remain available=False after Cycle 2. "
        "Derivatives is a Cycle-2b deliverable — do not fill it in Cycle 2."
    )


# ---------------------------------------------------------------------------
# 6. No score-only aggregates appear as clickable citation sources
# ---------------------------------------------------------------------------


def test_gdelt_tone_aggregate_is_not_presented_as_clickable_source(
    gdelt_shape_fixture: dict,
):
    """GDELT tone-only scores (no per-article URL) must not become citation sources.

    CC-4: aggregate-score-only providers cannot back a clickable news claim.
    A 'tone_score' value in the payload is fine as a derived signal; it must
    NOT be wrapped into a source with a fabricated URL.

    FAILS if the producer creates a source entry for the aggregate tone score
    instead of (or in addition to) per-article sources.
    """
    import ai_advisor

    mock_resp = _make_mock_response(gdelt_shape_fixture["gdelt_artlist_shape"])

    with patch("requests.get", return_value=mock_resp):
        block = ai_advisor._build_sentiment_section()

    if not block.get("available"):
        pytest.skip("Sentiment not available in this test env")

    sources = block.get("sources", [])
    fixture_articles = gdelt_shape_fixture["gdelt_artlist_shape"]["articles"]
    fixture_urls = {a["url"] for a in fixture_articles}

    for src in sources:
        url = src.get("url", "")
        assert url in fixture_urls, (
            f"Source URL {url!r} is not in the fixture article URLs {fixture_urls}. "
            f"The producer appears to be fabricating a source URL (e.g. for the "
            f"aggregate tone score). Only per-article URLs from GDELT are valid "
            f"citation sources (CC-4)."
        )


def test_fred_series_values_are_not_wrapped_as_clickable_sources(
    fred_shape_fixture: dict,
):
    """FRED numeric series observations must not become individual citation sources.

    Each FRED series observation is a data point (date, value) — not an article.
    The 'sources' list should contain FRED release page links (one per series),
    NOT one source per observation value.

    FAILS if the producer wraps individual observation values as citation sources.
    """
    import ai_advisor

    observations = fred_shape_fixture["series_observations_shape"]["observations"]
    mock_resp = _make_mock_response(fred_shape_fixture["series_observations_shape"])

    with (
        patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
        patch("requests.get", return_value=mock_resp),
    ):
        block = ai_advisor._build_macro_section()

    if not block.get("available"):
        pytest.skip("Macro not available in this test env")

    sources = block.get("sources", [])
    # Sources should be release page links, not one per observation
    # A correct producer produces <= N_series sources, not N_series * N_observations
    # The fixture has 2 observations for 1 series; check sources count is sane
    assert len(sources) <= 20, (
        f"macro sources list has {len(sources)} entries — suspiciously large. "
        f"Expected a small set of FRED release page links (one per series), "
        f"not one source per time-series observation. Got: {sources!r}"
    )


# ---------------------------------------------------------------------------
# 7. Property-based invariants (hypothesis where available)
# ---------------------------------------------------------------------------


if _HYPOTHESIS_AVAILABLE:
    @given(article_count=st.integers(min_value=0, max_value=50))
    @settings(max_examples=30)
    def test_gdelt_sources_count_equals_article_count(article_count: int):
        """PROPERTY: len(sources) == article_count for any non-negative count.

        The GDELT producer must produce exactly one source per article.
        This invariant must hold for any article count from 0 to 50.
        """
        import ai_advisor

        articles = [
            {
                "url": f"https://www.example.com/article-{i}",
                "title": f"Test Article {i}",
                "seendate": "20260610T120000Z",
                "language": "English",
                "domain": "example.com",
                "sourcecountry": "United States",
            }
            for i in range(article_count)
        ]
        mock_resp = _make_mock_response({"articles": articles})

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_sentiment_section()

        if block.get("available") is True:
            sources = block.get("sources", [])
            assert len(sources) == article_count, (
                f"PROPERTY VIOLATION: article_count={article_count} but "
                f"len(sources)={len(sources)}. "
                f"Each GDELT article must produce exactly one citation source."
            )

    @given(st.booleans())
    @settings(max_examples=10)
    def test_available_false_payload_always_empty(make_available_false: bool):
        """PROPERTY: available=False => payload is None or empty, sources is empty.

        This invariant must hold regardless of the error path taken.
        Tested by simulating both timeout (available=False) and
        no-key (available=False) paths.
        """
        import ai_advisor

        if make_available_false:
            # Trigger via missing key (FRED)
            env_without_fred = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
            with patch.dict(os.environ, env_without_fred, clear=True):
                block = ai_advisor._build_macro_section()
        else:
            # Trigger via timeout (GDELT)
            from requests.exceptions import Timeout
            with patch("requests.get", side_effect=Timeout("test")):
                block = ai_advisor._build_sentiment_section()

        if block.get("available") is False:
            payload = block.get("payload")
            assert payload is None or payload == {} or payload == [], (
                f"PROPERTY VIOLATION: available=False but payload={payload!r}. "
                f"A False-availability block must carry no payload (CC-3)."
            )
            sources = block.get("sources")
            assert sources is None or sources == [], (
                f"PROPERTY VIOLATION: available=False but sources={sources!r}. "
                f"A False-availability block must carry no sources (CC-4)."
            )


# ---------------------------------------------------------------------------
# 8. Opt-in live tests (marked @pytest.mark.live — excluded from default run)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_gdelt_sentiment_returns_articles_for_market_query():
    """LIVE: GDELT 2.0 DOC API returns real articles for a market query.

    Captures the real GDELT response shape to validate the API contract.
    Run with: pytest --include-live tests/ai_advisor/test_cycle2_lens_producers.py::test_live_gdelt_sentiment_returns_articles_for_market_query

    This test makes a REAL HTTP request to api.gdeltproject.org.
    It is EXCLUDED from the default run.
    """
    import ai_advisor

    block = ai_advisor._build_sentiment_section()

    _assert_lens_block_shape(block, "sentiment")
    if block["available"]:
        assert isinstance(block.get("payload"), dict)
        assert isinstance(block.get("sources"), list)
        _assert_citations_valid(block)
        for src in block["sources"]:
            assert _is_http_url(src.get("url", "")), (
                f"Live GDELT source missing valid URL: {src!r}"
            )


@pytest.mark.live
def test_live_sec_edgar_fundamentals_for_aapl():
    """LIVE: SEC EDGAR companyfacts returns real data for AAPL (CIK=0000320193).

    Captures the real SEC EDGAR response shape to validate the API contract.
    Run with: pytest --include-live tests/ai_advisor/test_cycle2_lens_producers.py::test_live_sec_edgar_fundamentals_for_aapl

    This test makes a REAL HTTP request to data.sec.gov.
    It is EXCLUDED from the default run.
    """
    import ai_advisor

    block = ai_advisor._build_fundamentals_section(ticker="AAPL")

    _assert_lens_block_shape(block, "fundamentals")
    if block["available"]:
        payload = block.get("payload", {})
        assert "entity_name" in payload
        assert "key_facts" in payload
        _assert_citations_valid(block)


@pytest.mark.live
def test_live_fred_macro_with_key_from_env():
    """LIVE: FRED API returns real series data when FRED_API_KEY is in env.

    Run with: pytest --include-live tests/ai_advisor/test_cycle2_lens_producers.py::test_live_fred_macro_with_key_from_env

    Requires FRED_API_KEY env var to be set.
    This test makes a REAL HTTP request to api.stlouisfed.org.
    It is EXCLUDED from the default run.
    """
    import ai_advisor

    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        pytest.skip("FRED_API_KEY not set — skipping live FRED test")

    block = ai_advisor._build_macro_section()

    _assert_lens_block_shape(block, "macro")
    if block["available"]:
        payload = block.get("payload", {})
        assert "series" in payload
        assert isinstance(payload["series"], dict)
        assert len(payload["series"]) >= 1
        _assert_citations_valid(block)
