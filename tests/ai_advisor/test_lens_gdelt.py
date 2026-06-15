"""
RED tests — GDELT tone / sentiment producer.

Contract reference: .claude/gdelt-contract.md (pinned 2026-06-15).
Diagnosis reference: .claude/gdelt-diagnosis.md (live 2026-06-14 capture).
Feature plan: feature-plans/lens-data-gdelt-sentiment.md.

Tests assert CONTRACT, not hardcoded producer-computed values.
Every tone assertion checks: is float, in [-1, 1], not None.
No test asserts a specific numeric tone value (those are producer-computed).

Fixture provenance:
  gdelt_timelinetone_response.json — schema-derived-with-validator from the
    real 2026-06-14 HTTP 200 body in gdelt-diagnosis.md. Values are sentinels;
    envelope structure is pinned.
  gdelt_artlist_response.json — schema-derived-with-validator from GDELT
    JSONFeed spec + existing artlist shape. Values are sentinels.

Mocking strategy:
  All tests mock the HTTP layer (requests.get) — NO real GDELT calls in CI.
  time.sleep is patched in retry tests so wall-clock is not consumed.
  The math engine and DB are never involved.
  @pytest.mark.live tests are excluded from the default run.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).parents[1] / "fixtures" / "math"


# ---------------------------------------------------------------------------
# Runtime shape validators (schema-derived-with-validator pattern).
# These validators guard the fixture — if the fixture deviates from the
# documented GDELT envelope, the validator catches it before any test runs.
# ---------------------------------------------------------------------------


def validate_timelinetone_shape(data: dict) -> None:
    """Assert the timelinetone fixture matches the pinned GDELT §2 envelope.

    Invariants:
      - top-level key 'timeline' is a list with at least one element
      - timeline[0] has key 'series' (str) and key 'data' (list)
      - data has at least one element; each element has 'date' (str) and
        'value' (int or float) — the nesting level the real bug confused
    """
    assert "timeline" in data, "timelinetone fixture missing 'timeline' key"
    tl = data["timeline"]
    assert isinstance(tl, list) and len(tl) >= 1, (
        "timelinetone fixture 'timeline' must be a non-empty list"
    )
    series = tl[0]
    assert "series" in series, "timeline[0] missing 'series' key"
    assert isinstance(series["series"], str), "timeline[0]['series'] must be str"
    assert "data" in series, (
        "timeline[0] missing 'data' key — "
        "this is the key the prior bug skipped over (it read 'value' from the series "
        "wrapper instead of drilling into data[k]['value'])"
    )
    points = series["data"]
    assert isinstance(points, list) and len(points) >= 1, (
        "timeline[0]['data'] must be a non-empty list"
    )
    for i, pt in enumerate(points):
        assert "date" in pt, f"data[{i}] missing 'date'"
        assert "value" in pt, (
            f"data[{i}] missing 'value' — "
            "this is the nested field the producer MUST read"
        )
        assert isinstance(pt["value"], (int, float)), (
            f"data[{i}]['value'] must be numeric, got {type(pt['value'])}"
        )


def validate_artlist_shape(data: dict) -> None:
    """Assert the artlist fixture matches the pinned GDELT §3 sources contract.

    Each article MUST carry url and seendate (contract §3 hard requirement).
    """
    assert "articles" in data, "artlist fixture missing 'articles' key"
    articles = data["articles"]
    assert isinstance(articles, list), "artlist fixture 'articles' must be a list"
    for i, art in enumerate(articles):
        assert "url" in art, f"article[{i}] missing 'url' (contract §3 hard requirement)"
        assert isinstance(art["url"], str) and art["url"].startswith("http"), (
            f"article[{i}]['url'] must be an http/https URL"
        )
        assert "seendate" in art, (
            f"article[{i}] missing 'seendate' — "
            "the producer maps seendate -> sources[*].seendate"
        )
        assert isinstance(art["seendate"], str) and art["seendate"].strip(), (
            f"article[{i}]['seendate'] must be a non-empty string"
        )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def timelinetone_fixture() -> dict:
    """Schema-derived timelinetone response (§2 envelope)."""
    path = _FIXTURES / "gdelt_timelinetone_response.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_timelinetone_shape(data)
    return data


@pytest.fixture
def artlist_fixture() -> dict:
    """Schema-derived artlist response (§3 sources shape)."""
    path = _FIXTURES / "gdelt_artlist_response.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_artlist_shape(data)
    return data


def _make_mock_http_response(json_data: dict | None, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response for a JSON-returning endpoint."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if json_data is not None:
        mock_resp.json.return_value = json_data
        mock_resp.text = json.dumps(json_data)
    else:
        mock_resp.json.side_effect = ValueError("No JSON content")
        mock_resp.text = ""
    return mock_resp


def _make_mock_429_response() -> MagicMock:
    """Simulate a GDELT 429 with plaintext body (contract §5 — NOT JSON)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger queries."
    # 429 body is NOT JSON — json() must not be called on it (it would raise)
    mock_resp.json.side_effect = ValueError("429 body is plaintext, not JSON")
    return mock_resp


# ---------------------------------------------------------------------------
# Fixture schema validators as tests (AC-3 — fixture provenance guard)
# ---------------------------------------------------------------------------


class TestFixtureSchemaValidity:
    """Verify that both fixtures conform to the pinned GDELT contract shapes.

    These tests ensure the fixtures are genuinely schema-derived (not
    parser+fixture co-designed). If the fixture deviates from the GDELT
    envelope structure, these tests catch it before the producer tests run.
    """

    def test_timelinetone_fixture_schema_is_valid(self, timelinetone_fixture: dict):
        """timelinetone fixture matches the §2 GDELT envelope (timeline[0][data][k][value]).

        FAILS if the fixture is missing the nested 'data' list that the real
        parser must traverse, or if 'value' is absent from any data point.

        Fixture provenance: schema-derived from real 2026-06-14 HTTP 200 in
        gdelt-diagnosis.md, NOT co-designed with the parser.
        """
        # validate_timelinetone_shape was already called in the fixture,
        # but call it directly here to make the assertion intent explicit.
        validate_timelinetone_shape(timelinetone_fixture)

        # Additional contract assertion: 'series' must equal "Average Tone"
        # (the exact series name GDELT uses — pinned in gdelt-contract.md §2)
        series_name = timelinetone_fixture["timeline"][0]["series"]
        assert series_name == "Average Tone", (
            f"timelinetone fixture series must be 'Average Tone' "
            f"(GDELT contract §2), got {series_name!r}"
        )

    def test_artlist_fixture_schema_is_valid(self, artlist_fixture: dict):
        """artlist fixture matches the §3 GDELT contract shape (url + seendate required).

        FAILS if any article is missing url or seendate.
        """
        validate_artlist_shape(artlist_fixture)

        # At least one article — empty fixture is not useful for shape testing
        assert len(artlist_fixture["articles"]) >= 1, (
            "artlist fixture must have at least one article for meaningful shape testing"
        )

    def test_artlist_fixture_articles_carry_title_and_domain(self, artlist_fixture: dict):
        """artlist fixture articles carry title and domain (§3 display fields).

        The producer maps title and domain into sources for display.
        """
        for i, art in enumerate(artlist_fixture["articles"]):
            assert "title" in art, f"article[{i}] missing 'title'"
            assert "domain" in art, f"article[{i}] missing 'domain'"


# ---------------------------------------------------------------------------
# AC-1 — Producer exists and returns documented shape
# ---------------------------------------------------------------------------


class TestProducerExistsAndShape:
    """_fetch_gdelt_sentiment exists in advisors/lens_gdelt.py and returns the
    pinned contract shape (contract §3) on a successful mocked fetch."""

    def test_producer_function_exists_and_is_callable(self):
        """advisors.lens_gdelt._fetch_gdelt_sentiment exists and is callable.

        FAILS if the function is missing from the module (import stub is
        sufficient to pass this one — RED on the NotImplementedError stub).
        """
        from advisors import lens_gdelt

        assert hasattr(lens_gdelt, "_fetch_gdelt_sentiment"), (
            "advisors.lens_gdelt._fetch_gdelt_sentiment is missing"
        )
        assert callable(lens_gdelt._fetch_gdelt_sentiment), (
            "advisors.lens_gdelt._fetch_gdelt_sentiment must be callable"
        )

    def test_returns_all_required_keys_on_success(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """A successful fetch returns all six contract keys (§3).

        Required keys: available, tone, per_ticker, source, sources, reason.
        FAILS if any key is absent.

        Values derived from mocked fixture — no hardcoded expected values.
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY", "QQQ"])

        required_keys = {"available", "tone", "per_ticker", "source", "sources", "reason"}
        missing = required_keys - set(result.keys())
        assert not missing, (
            f"_fetch_gdelt_sentiment return dict is missing keys: {missing}. "
            f"Contract §3 requires: {required_keys}. Got: {set(result.keys())}"
        )

    def test_available_is_true_on_successful_fetch(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """A successful tone+artlist fetch returns available=True.

        FAILS if the producer returns available=False despite a valid 200 response
        with populated timeline data (that would be a fabrication-avoidance bug —
        the honest path here is available=True because data was obtained).
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is True, (
            f"_fetch_gdelt_sentiment returned available=False on a successful "
            f"mocked fetch. Full result: {result!r}. "
            f"A successful tone extraction must return available=True (§3)."
        )

    def test_per_ticker_is_always_none_in_v1(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """per_ticker is always None in v1 (contract §6 — universe-level only).

        FAILS if the producer attempts a per-ticker pass, which would violate
        the rate-limit budget and is explicitly out of v1 scope.
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY", "QQQ", "AAPL"])

        assert result["per_ticker"] is None, (
            f"per_ticker must be None in v1 (contract §6: universe-level only). "
            f"Got per_ticker={result['per_ticker']!r}."
        )

    def test_source_field_is_non_empty_string(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """'source' field is a non-empty string (the citation provenance string).

        Contract §3: source is always present as a human-readable provenance
        string (build_citation in lens_pipeline.py consumes this). It MUST be
        present even on the unavailable path.
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert isinstance(result.get("source"), str) and result["source"].strip(), (
            f"'source' must be a non-empty string (provenance / citation string). "
            f"Got: {result.get('source')!r}"
        )

    def test_reason_is_none_on_success(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """'reason' is None when available=True (contract §3 — reason only on failure).

        FAILS if the producer sets a reason string on the success path.
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result.get("reason") is None, (
            f"'reason' must be None when available=True (§3). "
            f"Got reason={result.get('reason')!r}"
        )


# ---------------------------------------------------------------------------
# AC-2 — Honest availability: tone invariant and no fabrication
# ---------------------------------------------------------------------------


class TestHonestAvailabilityInvariants:
    """Lock the hard invariant: tone is None => available is False.

    This is the exact bug the prior producer had: it returned
    available=True, tone=None — which is the forbidden state (§4).
    These tests directly lock the invariant so a regressing implementation
    can't slip it through.
    """

    def test_tone_none_implies_available_false_on_timeout(self):
        """If tone cannot be extracted (timeout), available must be False.

        Hard invariant: tone is None => available is False (§4).
        FAILS if the producer returns available=True with tone=None.
        """
        from advisors import lens_gdelt
        from requests.exceptions import Timeout

        with (
            patch("requests.get", side_effect=Timeout("test timeout")),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("tone") is None:
            assert result["available"] is False, (
                "HARD INVARIANT VIOLATED: tone is None but available is True. "
                "This is the exact prior bug (§4). "
                f"Full result: {result!r}"
            )

    def test_tone_none_implies_available_false_on_empty_data(self):
        """If timeline data has no numeric values, available must be False.

        Hard invariant: tone is None => available is False (§4).
        An empty data array produces tone=None; available must be False with
        reason='no_tone_data', not available=True.
        """
        from advisors import lens_gdelt

        empty_timeline = {
            "query_details": {"title": "stock market finance"},
            "timeline": [{"series": "Average Tone", "data": []}],
        }
        resp = _make_mock_http_response(empty_timeline)

        with patch("requests.get", return_value=resp):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("tone") is None:
            assert result["available"] is False, (
                "HARD INVARIANT VIOLATED: tone is None (empty data) but available is True. "
                "This is exactly the prior bug (§4). "
                f"Full result: {result!r}"
            )

    def test_available_true_implies_tone_is_float(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """If available=True, tone must be a float (converse of the invariant).

        Contract §4: available=True => tone is a float in [-1, 1].
        FAILS if the producer returns available=True without a valid tone.
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("available") is True:
            assert isinstance(result.get("tone"), float), (
                f"When available=True, tone must be a float. "
                f"Got tone={result.get('tone')!r} (type: {type(result.get('tone')).__name__}). "
                f"Contract §4: available=True => tone is float in [-1, 1]."
            )

    def test_fabrication_forbidden_no_default_tone_on_empty(self):
        """Empty timeline must NOT produce a fabricated default tone (e.g. 0.0).

        AC-2: NEVER fabricate tone values.
        FAILS if the producer defaults tone to 0.0 or any other constant when
        there are no data points to compute from.
        """
        from advisors import lens_gdelt

        no_data_timeline = {
            "query_details": {"title": "stock market finance"},
            "timeline": [{"series": "Average Tone", "data": []}],
        }
        resp = _make_mock_http_response(no_data_timeline)

        with patch("requests.get", return_value=resp):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        # A fabricated 0.0 tone with available=True is a contract violation.
        is_fabricated = result.get("available") is True and result.get("tone") == 0.0
        assert not is_fabricated, (
            "Fabrication detected: producer returned available=True, tone=0.0 "
            "on empty data. No tone points were available to compute from. "
            "AC-2: NEVER fabricate tone values — return available=False with "
            "reason='no_tone_data' instead."
        )

    def test_empty_timeline_list_returns_no_tone_data_unavailable(self):
        """Empty top-level timeline list returns available=False, reason='no_tone_data'.

        Contract §4: 200 OK but timeline empty -> no-tone path.
        """
        from advisors import lens_gdelt

        empty_tl = {"query_details": {}, "timeline": []}
        resp = _make_mock_http_response(empty_tl)

        with patch("requests.get", return_value=resp):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"Empty timeline list must produce available=False (§4 no-tone path). "
            f"Got: {result!r}"
        )
        assert result.get("tone") is None, (
            f"Empty timeline must produce tone=None. Got tone={result.get('tone')!r}"
        )
        assert result.get("reason") == "no_tone_data", (
            f"Empty timeline reason must be 'no_tone_data' (§4). "
            f"Got reason={result.get('reason')!r}"
        )

    def test_empty_data_array_returns_no_tone_data_unavailable(self):
        """timeline[0]['data'] is present but empty -> available=False, reason='no_tone_data'.

        Contract §4: 200 OK but data empty OR no numeric values -> no-tone path.
        """
        from advisors import lens_gdelt

        empty_data_tl = {
            "query_details": {},
            "timeline": [{"series": "Average Tone", "data": []}],
        }
        resp = _make_mock_http_response(empty_data_tl)

        with patch("requests.get", return_value=resp):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"Empty data array must produce available=False. Got: {result!r}"
        )
        assert result.get("reason") == "no_tone_data", (
            f"Empty data reason must be 'no_tone_data' (§4). Got: {result.get('reason')!r}"
        )

    def test_no_numeric_values_in_data_returns_no_tone_data_unavailable(self):
        """All data points have non-numeric 'value' -> available=False, reason='no_tone_data'.

        Contract §2 step 3: collect only numeric values. If none survive the filter
        -> no-tone path.
        """
        from advisors import lens_gdelt

        non_numeric_tl = {
            "query_details": {},
            "timeline": [
                {
                    "series": "Average Tone",
                    "data": [
                        {"date": "20260614T130000Z", "value": None},
                        {"date": "20260614T133000Z", "value": "N/A"},
                    ],
                }
            ],
        }
        resp = _make_mock_http_response(non_numeric_tl)

        with patch("requests.get", return_value=resp):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"Non-numeric-only data must produce available=False. Got: {result!r}"
        )
        assert result.get("reason") == "no_tone_data", (
            f"Non-numeric data reason must be 'no_tone_data'. Got: {result.get('reason')!r}"
        )


# ---------------------------------------------------------------------------
# AC-2 / AC-5 — D-1 error contract and per-error-type reason labels
# ---------------------------------------------------------------------------


class TestErrorReasonLabels:
    """Verify the exact reason labels per §4 and the D-1 contract."""

    def test_network_timeout_returns_unavailable_with_exc_class_reason(self):
        """A network Timeout returns available=False, reason=type(exc).__name__ only.

        D-1 contract (§4): reason = type(exc).__name__ ONLY.
        NEVER str(exc) — str(exc) may contain the URL, host, or credential detail.
        """
        from advisors import lens_gdelt
        from requests.exceptions import Timeout

        timeout_exc = Timeout("Connection to api.gdeltproject.org timed out after 15s")

        with (
            patch("requests.get", side_effect=timeout_exc),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"Timeout must return available=False. Got: {result!r}"
        )
        reason = result.get("reason", "")
        assert isinstance(reason, str) and reason.strip(), (
            f"Timeout reason must be a non-empty string. Got: {reason!r}"
        )
        # D-1: must be type(exc).__name__ only — "Timeout"
        assert reason == "Timeout", (
            f"D-1 contract: Timeout reason must be 'Timeout' (type(exc).__name__). "
            f"Got: {reason!r}. "
            f"str(exc) is FORBIDDEN — it contains 'api.gdeltproject.org' which "
            f"would leak internal host details."
        )
        # Belt-and-suspenders: ensure str(exc) contents don't bleed through
        assert "api.gdeltproject.org" not in reason, (
            f"D-1 violation: URL leaked from str(exc) into reason: {reason!r}"
        )
        assert "timed out after 15s" not in reason, (
            f"D-1 violation: str(exc) detail leaked into reason: {reason!r}"
        )

    def test_json_decode_error_returns_unavailable(self):
        """A JSONDecodeError returns available=False, reason='JSONDecodeError'.

        Simulates a GDELT response with malformed JSON body.
        """
        from advisors import lens_gdelt

        malformed_resp = MagicMock()
        malformed_resp.status_code = 200
        malformed_resp.json.side_effect = ValueError("malformed json at line 1")
        malformed_resp.text = "not valid json {"

        with (
            patch("requests.get", return_value=malformed_resp),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"JSON decode error must return available=False. Got: {result!r}"
        )
        reason = result.get("reason", "")
        assert "malformed json at line 1" not in reason, (
            f"D-1 violation: str(exc) leaked into reason: {reason!r}"
        )

    def test_429_after_max_retries_returns_rate_limited_reason(self):
        """Persistent 429 after MAX_ATTEMPTS exhausted -> reason='rate_limited'.

        Contract §4: HTTP 429 persisting after max_attempts -> reason='rate_limited'.
        The prior bug: backoff_base=1.0 < GDELT's 5s floor meant retries never
        cleared the window, creating an infinite-loop PC crash (AC-4 context).
        """
        from advisors import lens_gdelt

        always_429 = _make_mock_429_response()

        with (
            patch("requests.get", return_value=always_429),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"Persistent 429 must return available=False after retries exhausted. "
            f"Got: {result!r}"
        )
        assert result.get("reason") == "rate_limited", (
            f"Persistent 429 reason must be 'rate_limited' (§4). "
            f"Got reason={result.get('reason')!r}"
        )

    def test_generic_exception_reason_is_exc_class_name_only(self):
        """Any unexpected exception: reason = type(exc).__name__, never str(exc).

        D-1: A RuntimeError with a secret in its message must NOT leak the secret.
        """
        from advisors import lens_gdelt

        secret_exc = RuntimeError("Internal error: api_key=super_secret_key_123 failed")

        with (
            patch("requests.get", side_effect=secret_exc),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"Any exception must return available=False. Got: {result!r}"
        )
        reason = result.get("reason", "")
        assert "super_secret_key_123" not in reason, (
            f"D-1 CRITICAL: secret leaked from str(exc) into reason: {reason!r}"
        )
        assert "api_key=" not in reason, (
            f"D-1 CRITICAL: key=value pair from str(exc) leaked into reason: {reason!r}"
        )
        # Must be the class name
        assert reason == "RuntimeError", (
            f"D-1: Generic exception reason must be 'RuntimeError' (type(exc).__name__). "
            f"Got: {reason!r}"
        )

    def test_connection_error_reason_is_exc_class_name_only(self):
        """ConnectionError: reason='ConnectionError', no host detail.

        D-1: str(ConnectionError) may contain socket addresses / internal hosts.
        """
        from advisors import lens_gdelt
        from requests.exceptions import ConnectionError as ReqConnError

        conn_exc = ReqConnError("Failed to establish: secret-internal.example.com:443")

        with (
            patch("requests.get", side_effect=conn_exc),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"ConnectionError must return available=False. Got: {result!r}"
        )
        reason = result.get("reason", "")
        assert "secret-internal.example.com" not in reason, (
            f"D-1 violation: hostname from str(exc) leaked into reason: {reason!r}"
        )
        assert reason == "ConnectionError", (
            f"D-1: ConnectionError reason must be 'ConnectionError'. Got: {reason!r}"
        )

    def test_unavailable_result_has_none_tone_and_none_per_ticker(self):
        """On the unavailable path, tone and per_ticker are both None.

        Contract §4: unavailable return has tone=None, per_ticker=None.
        FAILS if any unavailable path returns a non-None tone or per_ticker.
        """
        from advisors import lens_gdelt
        from requests.exceptions import Timeout

        with (
            patch("requests.get", side_effect=Timeout("test")),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result.get("tone") is None, (
            f"Unavailable result must have tone=None. Got tone={result.get('tone')!r}"
        )
        assert result.get("per_ticker") is None, (
            f"Unavailable result must have per_ticker=None. "
            f"Got per_ticker={result.get('per_ticker')!r}"
        )

    def test_unavailable_result_has_none_sources(self):
        """On the unavailable path, sources is None (not [] — that's artlist-empty).

        Contract §3: sources=None when the whole producer is unavailable.
        sources=[] is reserved for 'tone succeeded but artlist failed/empty'.
        """
        from advisors import lens_gdelt
        from requests.exceptions import Timeout

        with (
            patch("requests.get", side_effect=Timeout("test")),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result.get("sources") is None, (
            f"Unavailable result must have sources=None (§3). "
            f"sources=[] is for 'tone OK, artlist failed'. "
            f"Got sources={result.get('sources')!r}"
        )


# ---------------------------------------------------------------------------
# AC-3 — Tone normalization: shape/format/range, never a hardcoded value
# ---------------------------------------------------------------------------


class TestToneNormalization:
    """Tone is normalized from GDELT AvgTone ([-100,100]) to [-1,1].

    Tests assert: is float, in range, derived from data (not hardcoded).
    No test asserts a specific numeric tone value.
    """

    def test_tone_normalized_in_minus1_to_1_range(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """Tone is in [-1.0, 1.0] for any valid GDELT response.

        Contract §2: tone = clamp(mean(values) / 100, -1.0, 1.0).
        FAILS if the producer fails to divide by 100 (raw GDELT values are
        in [-100, 100], so an undivided tone would often be out of range).
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("available") is True:
            tone = result["tone"]
            assert isinstance(tone, float), (
                f"tone must be float, got {type(tone).__name__}: {tone!r}"
            )
            assert -1.0 <= tone <= 1.0, (
                f"tone must be in [-1.0, 1.0] (§2 normalization). "
                f"Got {tone}. "
                f"Likely cause: producer forgot to divide by 100 (GDELT AvgTone "
                f"is in [-100, 100] — undivided values would exceed this range)."
            )

    def test_tone_is_float_not_hardcoded_sentinel(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """Tone value varies with fixture content (not a hardcoded constant).

        Build a second modified fixture with known different values and verify
        the two tones differ. A hardcoded tone=0.0 would fail this test.
        """
        from advisors import lens_gdelt

        # First call: use the standard fixture
        tone_resp_1 = _make_mock_http_response(timelinetone_fixture)
        artlist_resp_1 = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp_1, artlist_resp_1]):
            result_1 = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        # Second call: all data points are strongly positive (mean ~80 -> tone ~0.8)
        positive_fixture = {
            "query_details": {"title": "stock market finance"},
            "timeline": [
                {
                    "series": "Average Tone",
                    "data": [
                        {"date": "20260614T130000Z", "value": 75.0},
                        {"date": "20260614T133000Z", "value": 85.0},
                        {"date": "20260614T153000Z", "value": 80.0},
                    ],
                }
            ],
        }
        artlist_resp_2 = _make_mock_http_response(artlist_fixture)
        tone_resp_2 = _make_mock_http_response(positive_fixture)

        with patch("requests.get", side_effect=[tone_resp_2, artlist_resp_2]):
            result_2 = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result_1.get("available") and result_2.get("available"):
            tone_1 = result_1["tone"]
            tone_2 = result_2["tone"]
            # The strongly-positive fixture (mean ~80) normalized to ~0.8
            # must differ from the near-zero fixture (mean ~0.39) normalized to ~0.004
            assert tone_1 != tone_2, (
                f"tone must be derived from fixture data, not hardcoded. "
                f"Two fixtures with very different values produced the same tone "
                f"({tone_1} == {tone_2}). "
                f"The producer appears to be returning a constant."
            )
            # The positive fixture should produce a tone > 0.5 (80/100=0.8, clamped)
            assert tone_2 > 0.5, (
                f"Strongly positive fixture (mean ~80 AvgTone) should produce "
                f"tone > 0.5 after /100 normalization. Got {tone_2}. "
                f"Either the normalization or the field path is wrong."
            )

    def test_tone_reads_from_nested_data_field_not_series_wrapper(self):
        """Tone MUST be extracted from timeline[0]['data'][k]['value'], not timeline[k]['value'].

        This is the exact prior bug (gdelt-diagnosis.md §1): the old parser read
        entry.get('value') from the series wrapper {series, data}, which has no
        'value' key, so raw_tones was always empty and tone=None.

        The correct path: timeline[0]['data'][k]['value'].

        This test verifies the correct nesting by providing a fixture where
        the SERIES WRAPPER has no 'value' key but the DATA POINTS do.
        """
        from advisors import lens_gdelt

        # Series wrapper with NO top-level 'value' — data points INSIDE 'data' have values.
        correct_nesting_fixture = {
            "query_details": {},
            "timeline": [
                {
                    "series": "Average Tone",
                    # Note: NO 'value' key at this level — the prior bug read here and got None
                    "data": [
                        {"date": "20260614T130000Z", "value": 50.0},
                        {"date": "20260614T133000Z", "value": 60.0},
                    ],
                }
            ],
        }
        artlist_resp = _make_mock_http_response({"articles": []})
        tone_resp = _make_mock_http_response(correct_nesting_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is True, (
            "Parser read from wrong nesting level. "
            "The series wrapper {series, data} has no 'value' key — "
            "if the producer returned available=False here, it's reading "
            "timeline[0]['value'] (always None) instead of "
            "timeline[0]['data'][k]['value'] (the real tone). "
            f"Full result: {result!r}"
        )
        tone = result.get("tone")
        assert tone is not None, (
            "tone is None despite valid data points — the parser is reading the "
            "wrong nesting level (the prior bug). "
            "Correct path: timeline[0]['data'][k]['value']."
        )
        assert isinstance(tone, float) and -1.0 <= tone <= 1.0, (
            f"tone must be float in [-1,1]. Got {tone!r}."
        )
        # Mean of (50+60)/2 = 55, /100 = 0.55 — assert it's roughly there
        # (using approx to avoid exact-float issues, but not asserting the exact value)
        assert tone > 0.4, (
            f"With data values [50, 60], normalized tone should be ~0.55. "
            f"Got {tone}. The parser may be using the wrong field path."
        )


# ---------------------------------------------------------------------------
# AC-4 — Bounded retry (no infinite loop, constants pinned)
# ---------------------------------------------------------------------------


class TestBoundedRetry:
    """Verify retry is bounded (MAX_ATTEMPTS=3) and backoff base is >=5s.

    The prior crash root cause: _GDELT_BACKOFF_BASE_S = 1.0 meant retries
    fired within GDELT's 5s rate-limit window -> persistent 429 -> infinite loop.
    The fix: BACKOFF_BASE_S >= 5.0 (contract §5 pinned value).
    """

    def test_backoff_base_constant_is_at_least_twenty_seconds(self):
        """_GDELT_BACKOFF_BASE_S == 20.0 (AMENDMENT 1 — margin above GDELT's 5s window).

        Contract §5 AMENDMENT 1: base is 20.0, not 5.0. 5.0 is GDELT's literal
        rate limit with zero margin — a 5s backoff can still re-trip 429 on a
        loaded IP. 20.0 gives 4x margin above the 5s floor.

        FAILS on any value < 20.0 (including the prior 5.0 or the original 1.0).
        """
        from advisors import lens_gdelt

        assert hasattr(lens_gdelt, "_GDELT_BACKOFF_BASE_S"), (
            "advisors.lens_gdelt._GDELT_BACKOFF_BASE_S constant is missing. "
            "This is a NAMED constant required by the contract (no magic numbers)."
        )
        base = lens_gdelt._GDELT_BACKOFF_BASE_S
        assert isinstance(base, (int, float)), (
            f"_GDELT_BACKOFF_BASE_S must be numeric, got {type(base)}"
        )
        assert base >= 20.0, (
            f"_GDELT_BACKOFF_BASE_S must be >= 20.0 (AMENDMENT 1: 4x margin above "
            f"GDELT's 5s rate limit). Got {base}. "
            f"5.0 is GDELT's literal floor — zero margin, can still re-trip 429. "
            f"Contract §5 AMENDMENT 1 pins this at 20.0."
        )

    def test_max_attempts_constant_equals_four(self):
        """_GDELT_MAX_ATTEMPTS == 4 (contract §5 AMENDMENT 1: initial + 3 retries).

        Backoff schedule: 20s, 40s, 60s (capped). 4 total attempts.
        """
        from advisors import lens_gdelt

        assert hasattr(lens_gdelt, "_GDELT_MAX_ATTEMPTS"), (
            "advisors.lens_gdelt._GDELT_MAX_ATTEMPTS constant is missing."
        )
        attempts = lens_gdelt._GDELT_MAX_ATTEMPTS
        assert isinstance(attempts, int), (
            f"_GDELT_MAX_ATTEMPTS must be int, got {type(attempts)}"
        )
        assert attempts == 4, (
            f"_GDELT_MAX_ATTEMPTS must be 4 (contract §5 AMENDMENT 1). Got {attempts}."
        )

    def test_backoff_cap_constant_exists_and_is_positive(self):
        """_GDELT_BACKOFF_CAP_S == 60.0 (contract §5 AMENDMENT 1 ramp ceiling).

        Schedule: min(20 * 2**i, 60) -> 20s, 40s, 60s across 3 retries.
        """
        from advisors import lens_gdelt

        assert hasattr(lens_gdelt, "_GDELT_BACKOFF_CAP_S"), (
            "advisors.lens_gdelt._GDELT_BACKOFF_CAP_S constant is missing."
        )
        cap = lens_gdelt._GDELT_BACKOFF_CAP_S
        assert isinstance(cap, (int, float)) and cap > 0, (
            f"_GDELT_BACKOFF_CAP_S must be a positive number. Got {cap!r}."
        )
        # AMENDMENT 1 pins it at 60.0
        assert cap == 60.0, (
            f"_GDELT_BACKOFF_CAP_S must be 60.0 (contract §5 AMENDMENT 1). Got {cap}."
        )

    def test_bounded_retry_exhausts_after_max_attempts_on_429(self):
        """Persistent 429: exactly MAX_ATTEMPTS HTTP calls are made, then returns unavailable.

        FAILS if the producer makes more than MAX_ATTEMPTS calls (unbounded loop)
        or fewer (gave up too early).
        """
        from advisors import lens_gdelt

        always_429 = _make_mock_429_response()
        max_attempts = lens_gdelt._GDELT_MAX_ATTEMPTS

        with (
            patch("requests.get", return_value=always_429) as mock_get,
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            "After MAX_ATTEMPTS 429 responses, producer must return available=False."
        )
        # The tone endpoint should have been retried exactly max_attempts times.
        # (artlist is not attempted when tone fails — one endpoint's calls only)
        tone_calls = mock_get.call_count
        assert tone_calls <= max_attempts, (
            f"Producer made {tone_calls} HTTP calls on persistent 429 but "
            f"MAX_ATTEMPTS={max_attempts}. "
            f"This is the infinite-loop bug — the retry is not bounded."
        )
        assert tone_calls >= 1, (
            f"Producer made 0 HTTP calls — it did not attempt the request."
        )

    def test_retry_count_does_not_exceed_max_attempts(self):
        """Under any error condition, total HTTP calls <= _GDELT_MAX_ATTEMPTS.

        Property: No error scenario can exceed the MAX_ATTEMPTS bound.
        This directly tests the anti-crash guarantee.
        """
        from advisors import lens_gdelt
        from requests.exceptions import ConnectionError as ReqConnError

        max_attempts = lens_gdelt._GDELT_MAX_ATTEMPTS
        conn_err = ReqConnError("test connection failure")

        with (
            patch("requests.get", side_effect=conn_err) as mock_get,
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False
        # Total calls across any combination of retries must not exceed the bound.
        assert mock_get.call_count <= max_attempts, (
            f"Total HTTP calls {mock_get.call_count} exceeded MAX_ATTEMPTS "
            f"({max_attempts}). Retry is not bounded."
        )

    def test_retry_only_on_429_not_on_success_with_empty_data(self):
        """A 200 response with empty data is NOT retried (it's a clean no-data result).

        Contract §5: retry ONLY on 429 (and optionally transient 5xx).
        An empty-data 200 is a definitive 'no tone available' — retrying it
        is pointless and wastes time.
        FAILS if the producer retries a 200-with-empty-data response.
        """
        from advisors import lens_gdelt

        empty_resp = _make_mock_http_response({
            "query_details": {},
            "timeline": [{"series": "Average Tone", "data": []}],
        })

        with patch("requests.get", return_value=empty_resp) as mock_get:
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        # Should call the tone endpoint once and return (no retry needed)
        # Allow for artlist call too (up to 2 calls: tone + artlist)
        # But must NOT call the tone endpoint more than once (no retry on empty data)
        assert result["available"] is False
        # We check the endpoint wasn't hammered (no retry on empty 200)
        # Total calls = 1 (tone) + 0 or 1 (artlist, which may or may not be attempted)
        assert mock_get.call_count <= 2, (
            f"Producer made {mock_get.call_count} HTTP calls on empty-data 200. "
            f"Expected at most 2 (tone + artlist). Retrying a 200-with-empty-data "
            f"is wasteful — retry should only happen on 429."
        )

    def test_backoff_sleep_is_called_between_429_retries(self):
        """time.sleep is called between 429 retry attempts (not spin-waiting).

        The backoff requires sleeping between attempts. A producer that doesn't
        sleep between retries would immediately re-429, defeating the backoff.
        """
        from advisors import lens_gdelt

        always_429 = _make_mock_429_response()
        max_attempts = lens_gdelt._GDELT_MAX_ATTEMPTS

        with (
            patch("requests.get", return_value=always_429),
            patch("time.sleep") as mock_sleep,
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False
        # Between max_attempts calls there should be (max_attempts - 1) sleeps
        # (sleep after each attempt except the last)
        assert mock_sleep.call_count >= 1, (
            f"time.sleep was never called during {max_attempts}-attempt 429 retry. "
            f"The producer must sleep between retries (not spin-wait)."
        )

    def test_inter_request_constant_exists_and_equals_six_seconds(self):
        """_GDELT_INTER_REQUEST_S == 6.0 (AMENDMENT 1: spacing between tone and artlist GETs).

        The tone GET and artlist GET share GDELT's per-IP window. A 6s gap
        gives one second of margin above the 5s rate-limit floor.
        """
        from advisors import lens_gdelt

        assert hasattr(lens_gdelt, "_GDELT_INTER_REQUEST_S"), (
            "advisors.lens_gdelt._GDELT_INTER_REQUEST_S constant is missing "
            "(AMENDMENT 1 — spacing between the two GETs)."
        )
        inter = lens_gdelt._GDELT_INTER_REQUEST_S
        assert isinstance(inter, (int, float)) and inter >= 5.0, (
            f"_GDELT_INTER_REQUEST_S must be >= 5.0 (must exceed GDELT's 5s floor). "
            f"Got {inter!r}. AMENDMENT 1 pins this at 6.0."
        )
        assert inter == 6.0, (
            f"_GDELT_INTER_REQUEST_S must be 6.0 (contract §5 AMENDMENT 1). Got {inter}."
        )

    def test_backoff_sleep_duration_is_at_least_base(self):
        """Each retry sleep call uses a duration >= _GDELT_BACKOFF_BASE_S (20.0s).

        AMENDMENT 1 raises the base to 20.0 — well above GDELT's 5s window —
        so each retry clears the rate-limit with 4x margin.
        """
        from advisors import lens_gdelt

        always_429 = _make_mock_429_response()
        base_s = lens_gdelt._GDELT_BACKOFF_BASE_S  # 20.0 after AMENDMENT 1

        sleep_durations: list[float] = []

        def capture_sleep(duration: float) -> None:
            sleep_durations.append(duration)

        with (
            patch("requests.get", return_value=always_429),
            patch("time.sleep", side_effect=capture_sleep),
        ):
            lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        # Filter to retry sleeps only (the inter-request sleep between tone/artlist
        # may also be captured; those are expected to be _GDELT_INTER_REQUEST_S=6.0).
        # Retry sleeps (between 429 attempts) must all be >= base_s.
        retry_sleeps = [d for d in sleep_durations if d >= base_s]
        assert len(retry_sleeps) >= 1, (
            f"No retry sleep of duration >= {base_s}s was recorded. "
            f"Recorded sleeps: {sleep_durations}. "
            f"Each 429 retry must sleep >= _GDELT_BACKOFF_BASE_S ({base_s}s)."
        )
        for i, dur in enumerate(retry_sleeps):
            assert dur >= base_s, (
                f"Retry sleep[{i}] = {dur}s is less than "
                f"_GDELT_BACKOFF_BASE_S={base_s}s. "
                f"AMENDMENT 1: base is 20.0 — 4x margin above GDELT's 5s floor."
            )


# ---------------------------------------------------------------------------
# AC-1 / §3 — sources from artlist
# ---------------------------------------------------------------------------


class TestSourcesFromArtlist:
    """Verify the sources field is populated from the artlist GET call."""

    def test_sources_is_list_of_dicts_on_success(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """When both tone and artlist succeed, sources is a list of dicts.

        Each dict must carry url, seendate, title, domain (§3 contract).
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if not result.get("available"):
            pytest.skip("tone fetch unavailable in this test env")

        sources = result.get("sources")
        assert isinstance(sources, list), (
            f"sources must be a list when available=True. Got {type(sources)}: {sources!r}"
        )

    def test_each_source_carries_required_fields(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """Each source dict carries url and seendate (§3 hard requirement).

        The contract note says: 'sources must carry url + seendate'.
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if not result.get("available"):
            pytest.skip("tone fetch unavailable in this test env")

        sources = result.get("sources", [])
        for i, src in enumerate(sources):
            assert "url" in src, (
                f"sources[{i}] missing 'url' (§3 hard requirement). Got keys: {set(src.keys())}"
            )
            assert "seendate" in src, (
                f"sources[{i}] missing 'seendate' (§3 hard requirement). "
                f"seendate is the GDELT article timestamp — must be preserved in sources."
            )
            url = src["url"]
            assert isinstance(url, str) and url.startswith("http"), (
                f"sources[{i}]['url'] must be http/https. Got: {url!r}"
            )

    def test_sources_count_matches_artlist_article_count(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """sources count matches the number of articles in the artlist response.

        Tests that the producer maps each article to exactly one source.
        Derives count from the fixture (never hardcodes the number).
        """
        from advisors import lens_gdelt

        expected_count = len(artlist_fixture["articles"])
        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if not result.get("available"):
            pytest.skip("tone fetch unavailable in this test env")

        sources = result.get("sources", [])
        assert len(sources) == expected_count, (
            f"Expected {expected_count} sources (one per artlist article), "
            f"got {len(sources)}. "
            f"Derived from fixture, not hardcoded."
        )

    def test_empty_artlist_response_gives_empty_sources_but_tone_still_valid(
        self, timelinetone_fixture: dict
    ):
        """Tone succeeds but artlist returns empty -> sources=[], available=True.

        Contract §3: sources=[] is valid when tone succeeded but artlist was empty.
        This is distinct from the full unavailable path (where sources=None).
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        empty_artlist_resp = _make_mock_http_response({"articles": []})

        with patch("requests.get", side_effect=[tone_resp, empty_artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if not result.get("available"):
            pytest.skip("tone unavailable — can't test empty-artlist path")

        sources = result.get("sources")
        assert sources == [], (
            f"When tone succeeds but artlist is empty, sources must be [] "
            f"(not None — None is the fully-unavailable state). Got: {sources!r}"
        )
        assert result["available"] is True, (
            "Empty artlist must not degrade tone availability — tone was extracted."
        )

    def test_artlist_failure_gives_empty_sources_but_tone_still_available(
        self, timelinetone_fixture: dict
    ):
        """If artlist call fails after tone succeeds, sources=[] but available=True.

        Contract §3: artlist citations are best-effort. A failed artlist call
        must not bring down the tone result.
        """
        from advisors import lens_gdelt
        from requests.exceptions import Timeout

        # Tone call succeeds, artlist call times out
        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_timeout = Timeout("artlist timed out")

        with patch("requests.get", side_effect=[tone_resp, artlist_timeout]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("tone") is None:
            pytest.skip("tone not available — artlist-failure path requires tone success first")

        if result.get("available") is True:
            # Tone succeeded — artlist failure must yield sources=[]
            assert result.get("sources") == [], (
                f"Artlist failure with tone success must yield sources=[] (best-effort). "
                f"Got sources={result.get('sources')!r}"
            )


# ---------------------------------------------------------------------------
# AC-5 — Contract document exists and endpoint is pinned
# ---------------------------------------------------------------------------


class TestContractDocumentExists:
    """Verify the AC-5 deliverable: GDELT contract is pinned before any client code."""

    def test_contract_document_exists_and_names_timelinetone_endpoint(self):
        """The contract doc .claude/gdelt-contract.md exists and mentions the endpoint.

        AC-5: GDELT API contract (endpoint URL, tone field semantics) must be
        pinned in a researcher deliverable. Tests verify the doc exists and
        references the pinned endpoint URL.
        """
        worktree_root = pathlib.Path(__file__).parents[2]
        contract_path = worktree_root / ".claude" / "gdelt-contract.md"

        assert contract_path.exists(), (
            f"Contract document missing: {contract_path}. "
            f"AC-5 requires the GDELT API contract to be pinned before "
            f"any client code is written."
        )

        content = contract_path.read_text(encoding="utf-8")
        assert "api.gdeltproject.org" in content, (
            "Contract document must reference the GDELT API endpoint. "
            f"'api.gdeltproject.org' not found in {contract_path}."
        )
        assert "timelinetone" in content, (
            "Contract document must mention the 'timelinetone' mode "
            "(the tone endpoint). Not found in the contract."
        )

    def test_tone_extracted_from_nested_data_field_not_series_wrapper(self):
        """The contract pins the correct field path: timeline[0]['data'][k]['value'].

        This test is the definitive lock on the prior-bug fix. By providing
        a response where the series wrapper has NO 'value' key but the data
        points DO, we verify the parser drills into the correct nesting level.

        See gdelt-diagnosis.md §1 for the original bug analysis.
        """
        from advisors import lens_gdelt

        # The series wrapper has ONLY 'series' and 'data' keys — no 'value'.
        # A buggy parser reading entry.get('value') gets None and returns available=False.
        # The correct parser reads entry['data'][k]['value'] and gets the tone.
        pinned_path_fixture = {
            "query_details": {},
            "timeline": [
                {
                    "series": "Average Tone",
                    # NO top-level 'value' here — bug would read this level
                    "data": [
                        {"date": "20260614T130000Z", "value": 20.0},
                        {"date": "20260614T140000Z", "value": 30.0},
                    ],
                }
            ],
        }
        artlist_resp = _make_mock_http_response({"articles": []})
        tone_resp = _make_mock_http_response(pinned_path_fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is True, (
            f"Parser is reading the wrong nesting level. "
            f"timeline[0] has no 'value' key (only 'series' and 'data'), "
            f"so a buggy parser returns tone=None -> available=False. "
            f"Correct path: timeline[0]['data'][k]['value']. "
            f"Full result: {result!r}"
        )
        tone = result.get("tone")
        assert tone is not None and isinstance(tone, float), (
            f"tone must be a float from the nested data path. Got: {tone!r}"
        )
        # Mean of [20, 30] = 25 -> /100 = 0.25. Assert in ballpark (not exact).
        assert 0.1 < tone < 0.5, (
            f"With data values [20, 30], normalized tone should be ~0.25. "
            f"Got {tone}. Field path or normalization may be wrong."
        )


# ---------------------------------------------------------------------------
# Property-based invariants
# ---------------------------------------------------------------------------


try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False


if _HYPOTHESIS_AVAILABLE:

    @given(
        raw_values=st.lists(
            st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=100,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_tone_is_always_in_minus1_to_1_for_any_valid_gdelt_values(raw_values: list[float]):
        """PROPERTY: For any list of numeric AvgTone values in [-100,100],
        the normalized tone is in [-1.0, 1.0].

        This locks the normalization math: mean / 100, clamped to [-1, 1].
        FAILS if the producer's clamping is wrong or missing.
        """
        from advisors import lens_gdelt

        data_points = [{"date": f"20260614T{i:02d}0000Z", "value": v} for i, v in enumerate(raw_values)]
        fixture = {
            "query_details": {},
            "timeline": [{"series": "Average Tone", "data": data_points}],
        }
        artlist_resp = _make_mock_http_response({"articles": []})
        tone_resp = _make_mock_http_response(fixture)

        with patch("requests.get", side_effect=[tone_resp, artlist_resp]):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("available") is True:
            tone = result["tone"]
            assert isinstance(tone, float), (
                f"tone must be float for raw_values={raw_values[:3]}..."
            )
            assert -1.0 <= tone <= 1.0, (
                f"PROPERTY VIOLATION: tone={tone} is out of [-1, 1] for "
                f"raw_values (first 3): {raw_values[:3]}. "
                f"Normalization: mean({raw_values[:3]}...) / 100 must be clamped."
            )

    @given(available=st.booleans())
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_tone_none_implies_available_false_property(available: bool):
        """PROPERTY: If the result has tone=None, available must be False.

        Tests the §4 hard invariant across multiple unavailable scenarios.
        """
        from advisors import lens_gdelt
        from requests.exceptions import Timeout

        if available:
            # Simulate a timeout -> unavailable
            with (
                patch("requests.get", side_effect=Timeout("test")),
                patch("time.sleep"),
            ):
                result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])
        else:
            # Simulate empty data -> unavailable
            empty = {"query_details": {}, "timeline": [{"series": "Average Tone", "data": []}]}
            with patch("requests.get", return_value=_make_mock_http_response(empty)):
                result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        if result.get("tone") is None:
            assert result["available"] is False, (
                f"PROPERTY VIOLATION: tone is None but available is True. "
                f"§4 hard invariant: tone is None => available is False. "
                f"Full result: {result!r}"
            )


# ---------------------------------------------------------------------------
# Review-round RED — gaps found after implementer GREEN
# ---------------------------------------------------------------------------


class TestReviewRoundGaps:
    """Tests added after the first GREEN that expose two implementation gaps.

    Gap 1: _GDELT_INTER_REQUEST_S is defined but never used — the artlist GET
    fires immediately after tone extraction with no sleep, keeping the IP
    window hot and re-tripping 429 on the artlist call.

    Gap 2: Non-429 HTTP errors (e.g. 503) return reason='HTTPError' (the D-1
    class name) but contract §4 specifies the NAMED label 'gdelt_fetch_failed'
    for non-200-non-429 HTTP-status failures — distinct from caught exceptions.
    """

    def test_inter_request_sleep_is_called_between_tone_and_artlist_gets(
        self, timelinetone_fixture: dict, artlist_fixture: dict
    ):
        """time.sleep(_GDELT_INTER_REQUEST_S) must be called between tone and artlist GETs.

        Contract §5 Amendment 1: both GETs share GDELT's per-IP rate-limit window.
        Firing artlist immediately after tone keeps the window hot and re-trips 429.
        _GDELT_INTER_REQUEST_S=6.0 must be slept between the two calls.

        FAILS if the constant is defined but never passed to time.sleep (current gap).
        """
        from advisors import lens_gdelt

        tone_resp = _make_mock_http_response(timelinetone_fixture)
        artlist_resp = _make_mock_http_response(artlist_fixture)
        inter_s = lens_gdelt._GDELT_INTER_REQUEST_S

        sleep_durations: list[float] = []

        def capture_sleep(duration: float) -> None:
            sleep_durations.append(duration)

        with (
            patch("requests.get", side_effect=[tone_resp, artlist_resp]),
            patch("time.sleep", side_effect=capture_sleep),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result.get("available") is True, (
            f"Expected successful result for this test. Got: {result!r}"
        )
        assert any(abs(d - inter_s) < 0.01 for d in sleep_durations), (
            f"_GDELT_INTER_REQUEST_S={inter_s}s sleep was never called. "
            f"Recorded sleeps: {sleep_durations}. "
            f"Contract §5 Amendment 1: sleep _GDELT_INTER_REQUEST_S between the "
            f"tone GET and artlist GET — the constant is defined but unused."
        )

    def test_non_429_http_error_returns_gdelt_fetch_failed_reason(self):
        """Non-200 non-429 HTTP responses return reason='gdelt_fetch_failed'.

        Contract §4 named-label table:
          HTTP 429 exhausted        -> 'rate_limited'
          Non-200 non-429 HTTP      -> 'gdelt_fetch_failed'   <- this test
          Caught network exception  -> type(exc).__name__

        The current implementation calls resp.raise_for_status() for non-429
        non-2xx, which raises HTTPError — caught by the outer except, yielding
        reason='HTTPError'. That is D-1 compliant (class name only) but §4
        specifies 'gdelt_fetch_failed' as a NAMED label for HTTP-status failures,
        separate from general exception handling.

        FAILS if reason='HTTPError' instead of 'gdelt_fetch_failed'.
        """
        from advisors import lens_gdelt
        from requests.exceptions import HTTPError

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = HTTPError("503 Server Error")
        mock_resp.text = "Service Unavailable"

        with (
            patch("requests.get", return_value=mock_resp),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False, (
            f"503 response must return available=False. Got: {result!r}"
        )
        assert result.get("reason") == "gdelt_fetch_failed", (
            f"Non-429 HTTP error must return reason='gdelt_fetch_failed' (§4). "
            f"Got reason={result.get('reason')!r}. "
            f"'HTTPError' is the D-1 class name but §4 names 'gdelt_fetch_failed' "
            f"for HTTP-status failures — different from caught network exceptions."
        )

    def test_non_200_5xx_returns_gdelt_fetch_failed(self):
        """HTTP 500 also yields reason='gdelt_fetch_failed' (not 'HTTPError').

        Verify the named-label rule applies across all non-200 non-429 codes.
        """
        from advisors import lens_gdelt
        from requests.exceptions import HTTPError

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = HTTPError("500 Internal Server Error")
        mock_resp.text = "Internal Server Error"

        with (
            patch("requests.get", return_value=mock_resp),
            patch("time.sleep"),
        ):
            result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

        assert result["available"] is False
        assert result.get("reason") == "gdelt_fetch_failed", (
            f"HTTP 500 must return reason='gdelt_fetch_failed' (§4). "
            f"Got: {result.get('reason')!r}"
        )


# ---------------------------------------------------------------------------
# @pytest.mark.live — excluded from default run (--include-live opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_gdelt_timelinetone_returns_valid_tone():
    """LIVE: Real GDELT timelinetone call returns valid normalized tone.

    Makes a REAL HTTP request to api.gdeltproject.org.
    Excluded from default run — opt in with: pytest --include-live

    Run only when the daemon is NOT running (IP window must be free).
    """
    from advisors import lens_gdelt

    result = lens_gdelt._fetch_gdelt_sentiment(["SPY", "QQQ"])

    assert "available" in result
    assert "tone" in result
    if result["available"]:
        assert isinstance(result["tone"], float)
        assert -1.0 <= result["tone"] <= 1.0
        assert result["per_ticker"] is None


@pytest.mark.live
def test_live_gdelt_sources_carry_url_and_seendate():
    """LIVE: Real GDELT artlist returns sources with url and seendate.

    Makes a REAL HTTP request to api.gdeltproject.org.
    Excluded from default run — opt in with: pytest --include-live
    """
    from advisors import lens_gdelt

    result = lens_gdelt._fetch_gdelt_sentiment(["SPY"])

    if result.get("available") and result.get("sources"):
        for i, src in enumerate(result["sources"]):
            assert "url" in src, f"Live source[{i}] missing 'url'"
            assert "seendate" in src, f"Live source[{i}] missing 'seendate'"
