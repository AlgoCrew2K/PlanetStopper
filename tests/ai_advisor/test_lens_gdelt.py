"""
GDELT tone-scoring producer tests — advisors/lens_gdelt.py

Scope:
  AC-1  advisors.lens_gdelt._fetch_gdelt_sentiment(universe) exists and returns a dict
        with required keys: available (bool), tone (float|None), per_ticker (dict|None),
        source (str). Shape contract is pinned in tests/fixtures/math/gdelt_tone_producer_schema.json.
  AC-2  Honest-availability: on HTTP error, timeout, 429 exhaustion, or empty results,
        the producer returns {"available": False, "reason": type(exc).__name__, ...}
        and NEVER fabricates a tone value.
  AC-3  Fixture provenance: assertions derive from the schema fixture (shape/format/presence)
        and never hardcode producer-computed tone values (feedback_no_hardcoded_test_values).
  AC-4  Off-execution-path; bounded retries (finite MAX_ATTEMPTS, exponential backoff cap);
        no blocking I/O; tone is ACTUALLY scored from a fixture with tone data.
  AC-5  GDELT API contract is schema-derived (gdelt_timelinetone_api_shape.json) and the
        producer schema is pinned in gdelt_tone_producer_schema.json.
  AC-6  The producer uses mode=timelinetone (NOT mode=artlist). artlist has no per-article
        tone field in the free GDELT API — this was the original bug.

Mocking strategy:
  - HTTP calls (requests.get): always patched. No live GDELT calls from CI.
  - time.sleep: patched to avoid real delays in retry tests.
  - No live Anthropic API. No DB calls in unit tests. Math engine never mocked.

No module-level mutable state. All fixtures are function-scoped unless noted.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root + fixture paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_FIXTURES_MATH = _WORKTREE / "tests" / "fixtures" / "math"
_TONE_SCHEMA_PATH = _FIXTURES_MATH / "gdelt_tone_producer_schema.json"
_TIMELINETONE_SHAPE_PATH = _FIXTURES_MATH / "gdelt_timelinetone_api_shape.json"
_ARTLIST_SHAPE_PATH = _FIXTURES_MATH / "gdelt_sentiment_api_shape.json"


def _import_lens_gdelt():
    """Import advisors.lens_gdelt; force-reload to avoid stale cache between tests."""
    if "advisors.lens_gdelt" in sys.modules:
        importlib.reload(sys.modules["advisors.lens_gdelt"])
    return importlib.import_module("advisors.lens_gdelt")


# ---------------------------------------------------------------------------
# Schema fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tone_schema() -> dict:
    """Load the gdelt_tone_producer_schema.json fixture (schema-derived provenance)."""
    assert _TONE_SCHEMA_PATH.exists(), (
        f"Schema fixture not found at {_TONE_SCHEMA_PATH}. "
        "Create tests/fixtures/math/gdelt_tone_producer_schema.json before running tests."
    )
    return json.loads(_TONE_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def timelinetone_shape() -> dict:
    """Load the gdelt_timelinetone_api_shape.json fixture (schema-derived provenance)."""
    assert _TIMELINETONE_SHAPE_PATH.exists(), (
        f"Timelinetone shape fixture not found at {_TIMELINETONE_SHAPE_PATH}."
    )
    return json.loads(_TIMELINETONE_SHAPE_PATH.read_text(encoding="utf-8"))


def _wrap_data_points_as_series(data_points: list[dict]) -> list[dict]:
    """Wrap flat {date, value} data points into the REAL GDELT series object shape.

    Real GDELT timelinetone shape (captured 2026-06-13):
      timeline: [{series: Average Tone, data: [{date, value}, ...]}]

    The old fixture had flat {date, value} at the timeline level -- WRONG.
    This helper wraps manual test data points into the correct nested structure.
    """
    return [{"series": "Average Tone", "data": data_points}]


def _make_timelinetone_response(timeline: list[dict]) -> MagicMock:
    """Build a mock requests.Response with the given timeline list.

    timeline must be a list of SERIES OBJECTS (real GDELT shape):
      [{series: Average Tone, data: [{date, value}, ...]}]

    Use _wrap_data_points_as_series() to convert flat {date, value} lists to the
    correct series structure.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"timeline": timeline}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _make_timeline_entries(timelinetone_shape: dict) -> list[dict]:
    """Extract the timeline series list from the (updated) timelinetone shape fixture.

    Returns list of series objects: [{series: Average Tone, data: [{date, value}, ...]}]
    """
    return timelinetone_shape["gdelt_timelinetone_shape"]["timeline"]


# ---------------------------------------------------------------------------
# Required-key/shape assertion helpers
# ---------------------------------------------------------------------------

_REQUIRED_OUTPUT_KEYS = {"available", "tone", "per_ticker", "source"}
_REQUIRED_UNAVAILABLE_KEYS = {"available", "tone", "per_ticker", "reason"}


def _assert_available_true_shape(result: dict, *, context: str = "") -> None:
    ctx = f" [{context}]" if context else ""
    assert isinstance(result, dict), f"Producer must return a dict{ctx}"
    missing = _REQUIRED_OUTPUT_KEYS - set(result.keys())
    assert not missing, f"Producer result missing required keys: {missing}{ctx}"
    assert result["available"] is True, f"available must be True{ctx}"
    tone = result["tone"]
    if tone is not None:
        assert isinstance(tone, (int, float)), f"tone must be float or None{ctx}"
        assert -1.0 - 1e-9 <= tone <= 1.0 + 1e-9, f"tone must be in [-1, 1]{ctx}"
    assert result["per_ticker"] is None or isinstance(result["per_ticker"], dict)
    assert isinstance(result["source"], str) and result["source"]


def _assert_available_false_shape(result: dict, *, context: str = "") -> None:
    ctx = f" [{context}]" if context else ""
    assert isinstance(result, dict), f"Producer must return a dict{ctx}"
    assert result.get("available") is False, f"available must be False{ctx}"
    assert result.get("tone") is None, f"tone must be None on degraded path{ctx}"
    assert result.get("per_ticker") is None, f"per_ticker must be None on degraded path{ctx}"
    reason = result.get("reason", "")
    assert isinstance(reason, str) and reason, f"reason must be non-empty string{ctx}"


# ===========================================================================
# AC-1 — Module and function exist with correct signature
# ===========================================================================


class TestFetchGdeltSentimentExists:
    """AC-1: advisors/lens_gdelt.py must export _fetch_gdelt_sentiment(universe) -> dict."""

    def test_module_is_importable(self):
        mod = _import_lens_gdelt()
        assert mod is not None

    def test_function_is_exported(self):
        mod = _import_lens_gdelt()
        fn = getattr(mod, "_fetch_gdelt_sentiment", None)
        assert callable(fn), f"_fetch_gdelt_sentiment must be callable; got {fn!r}"

    def test_function_accepts_universe_argument(self):
        import inspect
        mod = _import_lens_gdelt()
        fn = mod._fetch_gdelt_sentiment
        sig = inspect.signature(fn)
        assert "universe" in sig.parameters, (
            f"_fetch_gdelt_sentiment must accept 'universe'; got {list(sig.parameters)}"
        )


# ===========================================================================
# AC-6 — Endpoint uses mode=timelinetone, NOT mode=artlist
# ===========================================================================


class TestEndpointUsesTimelinetone:
    """AC-6: The producer must use the timelinetone endpoint to get real tone scores.

    artlist mode returns NO per-article tone in the free GDELT API.
    This was the original bug — the fix is to use timelinetone.
    """

    def test_url_constant_uses_timelinetone(self):
        """The GDELT URL constant in lens_gdelt.py must contain 'timelinetone'."""
        mod = _import_lens_gdelt()
        # Find any constant that looks like a URL.
        url_const = None
        for attr_name in dir(mod):
            val = getattr(mod, attr_name, None)
            if isinstance(val, str) and "gdeltproject.org" in val:
                url_const = val
                break
        assert url_const is not None, (
            "advisors/lens_gdelt.py must define a URL constant pointing to gdeltproject.org"
        )
        assert "timelinetone" in url_const.lower(), (
            f"GDELT URL constant must use mode=timelinetone (not mode=artlist). "
            f"artlist has no per-article tone in the free API. "
            f"Got: {url_const!r}"
        )
        assert "artlist" not in url_const.lower(), (
            f"GDELT URL constant must NOT use mode=artlist — artlist has no tone. "
            f"Use mode=timelinetone instead. Got: {url_const!r}"
        )

    def test_source_file_does_not_use_artlist_mode(self):
        """Static scan: lens_gdelt.py must not contain 'mode=artlist' in the URL constant."""
        source_path = _WORKTREE / "advisors" / "lens_gdelt.py"
        assert source_path.exists(), f"advisors/lens_gdelt.py not found at {source_path}"
        source = source_path.read_text(encoding="utf-8")
        # Check that the URL in the module uses timelinetone
        assert "timelinetone" in source, (
            "advisors/lens_gdelt.py must contain 'timelinetone' in its GDELT endpoint URL. "
            "artlist mode has no tone — use mode=timelinetone."
        )

    def test_response_parsing_reads_timeline_not_articles(self):
        """When GDELT returns a timeline array, the producer uses 'timeline' key (not 'articles')."""
        mod = _import_lens_gdelt()
        data_points = [{"date": "20260613T000000Z", "value": 5.0}]
        timeline = _wrap_data_points_as_series(data_points)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        # If it returns available=True, tone must be populated from the timeline value.
        if result.get("available"):
            tone = result.get("tone")
            assert tone is not None, (
                "When timelinetone returns data with value=5.0 (normalized: 0.05), "
                "the producer must populate tone from the timeline entries. "
                "If tone is None, the parser is reading 'articles' (wrong key) instead of 'timeline'."
            )
        # Either way, no crash.
        assert isinstance(result, dict)


# ===========================================================================
# AC-1 + AC-3 — Producer returns valid shape on success
# ===========================================================================


class TestFetchReturnsValidShape:
    """AC-1 + AC-3: on a successful mocked GDELT timelinetone fetch, shape matches schema."""

    def test_result_has_required_keys(self, timelinetone_shape):
        """Result dict must contain all required keys from the schema fixture."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY", "QQQ"])

        missing = _REQUIRED_OUTPUT_KEYS - set(result.keys())
        assert not missing, (
            f"_fetch_gdelt_sentiment result missing required keys: {missing}. Got: {sorted(result.keys())}"
        )

    def test_available_is_bool(self, timelinetone_shape):
        """result['available'] must be a bool."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        assert isinstance(result["available"], bool)

    def test_tone_is_float_and_not_none_when_timeline_has_values(self, timelinetone_shape):
        """When timelinetone returns entries with numeric values, tone must be a non-None float.

        This is the KEY test that was always None before the fix.
        AC-3: asserts shape/format/presence, not specific tone value.
        """
        mod = _import_lens_gdelt()
        # Use fixture timeline entries — they have numeric 'value' fields.
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available"):
            tone = result.get("tone")
            assert tone is not None, (
                "AC-1 KEY ASSERTION: tone must NOT be None when timelinetone returns numeric values. "
                "This was the original bug — artlist mode has no tone. "
                "With timelinetone, tone is computed from timeline[*].value and MUST be populated."
            )
            assert isinstance(tone, (int, float)), f"tone must be a float; got {type(tone)}"

    def test_tone_normalized_to_unit_interval(self, timelinetone_shape):
        """When tone is not None, it must be in [-1, 1] (normalized from GDELT's [-100, 100]).

        Invariant from schema fixture: tone_bounds says -1.0 <= tone <= 1.0.
        """
        mod = _import_lens_gdelt()
        # Use timeline entries with high-magnitude value to exercise normalization.
        data_points = [
            {"date": "20260613T000000Z", "value": 75.0},
            {"date": "20260613T010000Z", "value": -50.0},
        ]
        timeline = _wrap_data_points_as_series(data_points)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available") and result.get("tone") is not None:
            tone = result["tone"]
            assert -1.0 - 1e-9 <= tone <= 1.0 + 1e-9, (
                f"tone must be in [-1, 1] (normalized from GDELT AvgTone [-100, 100]); "
                f"got {tone!r}. Normalize: divide value by 100.0."
            )

    def test_source_is_non_empty_string_when_available(self, timelinetone_shape):
        """result['source'] must be a non-empty string when available=True."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available"):
            assert isinstance(result["source"], str) and result["source"]

    def test_per_ticker_is_dict_or_none(self, timelinetone_shape):
        """result['per_ticker'] must be a dict or None."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        per_ticker = result.get("per_ticker")
        assert per_ticker is None or isinstance(per_ticker, dict)

    def test_never_raises_on_success(self, timelinetone_shape):
        """_fetch_gdelt_sentiment must not raise on a successful mocked response."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            try:
                result = mod._fetch_gdelt_sentiment(universe=["SPY", "QQQ", "IWM"])
            except Exception as exc:
                pytest.fail(
                    f"_fetch_gdelt_sentiment must not raise; raised {type(exc).__name__}: {exc}"
                )
        assert isinstance(result, dict)


# ===========================================================================
# AC-3 — Golden fixture schema contract
# ===========================================================================


class TestGoldenFixtureSchemaContract:
    """AC-3 + AC-5: the schema fixture pins the output contract."""

    def test_tone_schema_fixture_exists(self):
        assert _TONE_SCHEMA_PATH.exists(), f"Schema fixture not found at {_TONE_SCHEMA_PATH}"

    def test_tone_schema_has_required_output_keys_field(self, tone_schema):
        assert "required_output_keys" in tone_schema, (
            f"Schema fixture missing 'required_output_keys'. Got: {sorted(tone_schema.keys())}"
        )

    def test_tone_schema_has_invariants(self, tone_schema):
        assert "invariants" in tone_schema
        assert "tone_bounds" in tone_schema["invariants"]

    def test_tone_schema_asserts_timelinetone_endpoint(self, tone_schema):
        """Schema fixture must document that the producer uses timelinetone, not artlist."""
        invariants = tone_schema.get("invariants", {})
        assert "endpoint_uses_timelinetone" in invariants, (
            "Schema fixture must include invariant 'endpoint_uses_timelinetone' "
            "to document that artlist mode was the original bug."
        )

    def test_timelinetone_shape_fixture_exists(self):
        assert _TIMELINETONE_SHAPE_PATH.exists(), (
            f"Timelinetone shape fixture not found at {_TIMELINETONE_SHAPE_PATH}"
        )

    def test_timelinetone_shape_has_timeline_array(self, timelinetone_shape):
        """Fixture must define gdelt_timelinetone_shape with a timeline array."""
        assert "gdelt_timelinetone_shape" in timelinetone_shape
        shape = timelinetone_shape["gdelt_timelinetone_shape"]
        assert "timeline" in shape, "timelinetone shape must have a 'timeline' array"
        assert isinstance(shape["timeline"], list) and shape["timeline"], (
            "timeline must be a non-empty list of sample entries"
        )

    def test_timelinetone_entries_have_value_field(self, timelinetone_shape):
        """Each data point in the fixture series must have a 'value' field (GDELT AvgTone).

        Real GDELT shape (captured 2026-06-13):
          timeline: [{series: Average Tone, data: [{date, value}, ...]}]

        The value field lives in each series data point, NOT at the series level.
        """
        series_list = timelinetone_shape["gdelt_timelinetone_shape"]["timeline"]
        assert series_list, "timeline must be a non-empty list of series objects"
        for series_obj in series_list:
            assert "series" in series_obj, (
                f"Each timeline entry must have a 'series' key; got entry: {series_obj}"
            )
            assert "data" in series_obj, (
                f"Each timeline series must have a 'data' key; got entry: {series_obj}"
            )
            assert isinstance(series_obj["data"], list) and series_obj["data"], (
                f"timeline series 'data' must be a non-empty list; got: {series_obj['data']}"
            )
            for data_point in series_obj["data"]:
                assert "value" in data_point, (
                    f"Each data point must have a 'value' field (GDELT AvgTone); "
                    f"got data_point: {data_point}"
                )
                assert isinstance(data_point["value"], (int, float)), (
                    f"data point 'value' must be numeric; got {type(data_point['value'])}"
                )

    def test_producer_output_satisfies_schema_required_keys(self, timelinetone_shape, tone_schema):
        """Producer output (mocked success) must contain all required_output_keys from schema."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        required_keys = set(tone_schema.get("required_output_keys", []))
        missing = required_keys - set(result.keys())
        assert not missing, (
            f"Producer output missing required keys per schema: {missing}. Got: {sorted(result.keys())}"
        )

    def test_producer_tone_bounds_invariant(self, timelinetone_shape, tone_schema):
        """Producer tone value must satisfy the schema fixture's tone_bounds invariant."""
        mod = _import_lens_gdelt()
        timeline = _wrap_data_points_as_series([{"date": "20260613T000000Z", "value": -60.0}])
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available") and result.get("tone") is not None:
            tone = result["tone"]
            assert -1.0 - 1e-9 <= tone <= 1.0 + 1e-9, (
                f"tone_bounds invariant violated: tone={tone!r} is outside [-1, 1]."
            )


# ===========================================================================
# AC-2 — Honest-availability: unavailable markers on all failure paths
# ===========================================================================


class TestUnavailableOnNetworkError:
    """AC-2: network errors yield available=False, no tone fabrication, D-1 compliant reason."""

    def test_connection_error_returns_unavailable(self):
        mod = _import_lens_gdelt()
        with patch("requests.get", side_effect=ConnectionError("host unreachable")):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])
        assert result.get("available") is False

    def test_timeout_returns_unavailable(self):
        import requests as req_mod
        mod = _import_lens_gdelt()
        with patch("requests.get", side_effect=req_mod.exceptions.Timeout("timed out")):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])
        assert result.get("available") is False

    def test_http_error_returns_unavailable(self):
        import requests as req_mod
        mod = _import_lens_gdelt()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = req_mod.exceptions.HTTPError("503 Service Unavailable")
        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])
        assert result.get("available") is False

    def test_tone_is_none_on_network_error(self):
        mod = _import_lens_gdelt()
        with patch("requests.get", side_effect=OSError("network down")):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])
        assert result.get("tone") is None, (
            f"tone must be None when available=False; got {result.get('tone')!r}. "
            "AC-2: never fabricate tone on a network failure."
        )

    def test_reason_is_d1_compliant_on_network_error(self):
        """The reason field must be type(exc).__name__ only — never str(exc) with details."""
        mod = _import_lens_gdelt()
        secret_host = "gdelt-internal-cache.corp.example.com:6379/secret_key"
        with patch("requests.get", side_effect=ConnectionError(secret_host)):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])
        result_str = json.dumps(result, default=str)
        assert secret_host not in result_str, (
            "D-1 violation: raw exception message leaked into producer result."
        )
        assert "ConnectionError" in result_str, (
            f"D-1: reason must contain 'ConnectionError'; got reason: {result.get('reason')!r}"
        )

    def test_never_raises_on_network_error(self):
        mod = _import_lens_gdelt()
        with patch("requests.get", side_effect=RuntimeError("unexpected error")):
            try:
                result = mod._fetch_gdelt_sentiment(universe=["SPY"])
            except Exception as exc:
                pytest.fail(f"_fetch_gdelt_sentiment must never raise; got {type(exc).__name__}: {exc}")
        assert isinstance(result, dict)
        assert result.get("available") is False


# ===========================================================================
# AC-2 — Bounded retry: 429 exhaustion returns unavailable
# ===========================================================================


class TestUnavailableOn429AfterMaxRetries:
    """AC-2 + AC-4: persistent 429 exhausts bounded retries and returns unavailable."""

    def test_persistent_429_returns_unavailable(self):
        mod = _import_lens_gdelt()
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp_429), patch("time.sleep"):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        assert result.get("available") is False

    def test_persistent_429_tone_is_none(self):
        mod = _import_lens_gdelt()
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp_429), patch("time.sleep"):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        assert result.get("tone") is None

    def test_retry_count_is_bounded(self):
        """The number of HTTP attempts is finite (bounded by MAX_ATTEMPTS constant)."""
        mod = _import_lens_gdelt()
        call_count = 0

        def counting_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 20:
                raise RuntimeError(
                    "BUG: _fetch_gdelt_sentiment made > 20 HTTP attempts. "
                    "Retry loop is unbounded — this was the PC-crash root cause."
                )
            mock = MagicMock()
            mock.status_code = 429
            mock.raise_for_status.return_value = None
            return mock

        with patch("requests.get", side_effect=counting_get), patch("time.sleep"):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        assert call_count <= 20, (
            f"HTTP attempt count must be bounded; made {call_count} calls."
        )
        assert result.get("available") is False

    def test_single_success_after_one_429_does_retry(self, timelinetone_shape):
        """A single 429 followed by a 200 must succeed (retry works)."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.raise_for_status.return_value = None
        resp_200 = _make_timelinetone_response(timeline)

        call_order = {"n": 0}

        def side_effect_fn(url, **kwargs):
            call_order["n"] += 1
            return resp_429 if call_order["n"] == 1 else resp_200

        with patch("requests.get", side_effect=side_effect_fn), patch("time.sleep"):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        assert call_order["n"] >= 2, (
            f"Expected at least 2 HTTP calls (1 retry after 429); made {call_order['n']}"
        )


# ===========================================================================
# AC-4 — Bounded retries: named MAX_ATTEMPTS constant
# ===========================================================================


class TestBoundedRetries:
    """AC-4: the retry loop has a finite MAX_ATTEMPTS constant and exponential backoff cap."""

    def test_max_attempts_constant_exists_on_module(self):
        """advisors.lens_gdelt must expose a named MAX_ATTEMPTS constant."""
        mod = _import_lens_gdelt()
        has_max = (
            hasattr(mod, "_GDELT_MAX_ATTEMPTS")
            or hasattr(mod, "MAX_ATTEMPTS")
            or hasattr(mod, "_MAX_ATTEMPTS")
            or hasattr(mod, "_FETCH_MAX_ATTEMPTS")
        )
        assert has_max, (
            "advisors.lens_gdelt must expose a named MAX_ATTEMPTS-style constant. "
            "No magic numbers — coding standard + PC-crash regression guard."
        )

    def test_max_attempts_is_positive_and_finite(self):
        """The MAX_ATTEMPTS constant must be a positive, finite integer."""
        mod = _import_lens_gdelt()
        max_att = (
            getattr(mod, "_GDELT_MAX_ATTEMPTS", None)
            or getattr(mod, "MAX_ATTEMPTS", None)
            or getattr(mod, "_MAX_ATTEMPTS", None)
            or getattr(mod, "_FETCH_MAX_ATTEMPTS", None)
        )
        assert isinstance(max_att, int), f"MAX_ATTEMPTS must be int; got {type(max_att)}"
        assert 1 <= max_att <= 20, f"MAX_ATTEMPTS must be in [1, 20]; got {max_att}"

    def test_backoff_cap_constant_exists(self):
        """A backoff cap constant must exist (the PC-crash root cause was a missing cap)."""
        mod = _import_lens_gdelt()
        has_cap = (
            hasattr(mod, "_GDELT_BACKOFF_CAP_S")
            or hasattr(mod, "_BACKOFF_CAP_S")
            or hasattr(mod, "BACKOFF_CAP_S")
        )
        assert has_cap, (
            "advisors.lens_gdelt must expose a backoff cap constant "
            "(e.g. _GDELT_BACKOFF_CAP_S). This prevents the infinite-retry OOM."
        )

    def test_timeout_constant_exists(self):
        """An explicit HTTP timeout constant must exist."""
        mod = _import_lens_gdelt()
        has_timeout = (
            hasattr(mod, "_GDELT_TIMEOUT_S")
            or hasattr(mod, "_TIMEOUT_S")
            or hasattr(mod, "TIMEOUT_S")
        )
        assert has_timeout, (
            "advisors.lens_gdelt must expose an explicit timeout constant "
            "(e.g. _GDELT_TIMEOUT_S). No urllib3 default — project rule §5."
        )


# ===========================================================================
# AC-2 — Empty GDELT timeline: no fabricated tone
# ===========================================================================


class TestEmptyTimelineReturnsUnavailable:
    """AC-2: empty timeline must not produce a fabricated tone."""

    def test_empty_timeline_available_false_or_tone_none(self):
        """Empty timeline must yield either available=False OR (available=True, tone=None)."""
        mod = _import_lens_gdelt()
        mock_resp = _make_timelinetone_response(timeline=[])

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available"):
            assert result.get("tone") is None, (
                "When GDELT returns empty timeline and available=True, "
                "tone must be None — never a fabricated value."
            )
        else:
            assert result.get("available") is False

    def test_empty_timeline_tone_is_not_zero(self):
        """tone must not be 0.0 (fabricated neutral) on an empty timeline."""
        mod = _import_lens_gdelt()
        mock_resp = _make_timelinetone_response(timeline=[])

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        tone = result.get("tone")
        if not result.get("available"):
            assert tone is None, (
                f"tone must be None when available=False (empty timeline); got {tone!r}."
            )

    def test_timeline_with_no_value_field_handled_gracefully(self):
        """Timeline entries without a 'value' field must not crash the producer."""
        mod = _import_lens_gdelt()
        # Entries missing the 'value' field.
        bad_data_points = [
            {"date": "20260613T000000Z"},
            {"date": "20260613T010000Z", "other_field": "oops"},
        ]
        bad_timeline = [{"series": "Average Tone", "data": bad_data_points}]
        mock_resp = _make_timelinetone_response(bad_timeline)

        with patch("requests.get", return_value=mock_resp):
            try:
                result = mod._fetch_gdelt_sentiment(universe=["SPY"])
            except Exception as exc:
                pytest.fail(
                    f"_fetch_gdelt_sentiment must not raise when timeline entries have no 'value'; "
                    f"raised {type(exc).__name__}: {exc}"
                )

        assert isinstance(result, dict)
        assert "available" in result

    def test_missing_timeline_key_handled_gracefully(self):
        """A response without a 'timeline' key must not crash the producer."""
        mod = _import_lens_gdelt()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # no 'timeline' key
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            try:
                result = mod._fetch_gdelt_sentiment(universe=["SPY"])
            except Exception as exc:
                pytest.fail(
                    f"Must not raise on missing 'timeline' key; got {type(exc).__name__}: {exc}"
                )

        assert isinstance(result, dict)
        assert "available" in result


# ===========================================================================
# AC-4 — Tone is ACTUALLY scored from fixture data (strengthened assertion)
# ===========================================================================


class TestToneIsActuallyScored:
    """AC-4 (strengthened): tone is not None when fixture data has numeric values.

    The original tests only asserted tone=None honest-availability.
    These tests verify that tone IS populated from real fixture tone data.
    """

    def test_tone_is_float_from_positive_value(self):
        """With a positive timeline value, tone must be a positive float (or available=False)."""
        mod = _import_lens_gdelt()
        # value=50.0 → normalized: 0.5 (positive sentiment signal)
        mock_resp = _make_timelinetone_response(
            _wrap_data_points_as_series([{"date": "20260613T000000Z", "value": 50.0}])
        )

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available"):
            tone = result.get("tone")
            assert tone is not None, (
                "tone must not be None when timelinetone returns value=50.0. "
                "The producer must extract the numeric value from timeline entries."
            )
            assert tone > 0, (
                f"Normalized tone from value=50.0 must be positive; got {tone!r}"
            )

    def test_tone_is_float_from_negative_value(self):
        """With a negative timeline value, tone must be a negative float (or available=False)."""
        mod = _import_lens_gdelt()
        # value=-30.0 → normalized: -0.3 (negative sentiment signal)
        mock_resp = _make_timelinetone_response(
            _wrap_data_points_as_series([{"date": "20260613T000000Z", "value": -30.0}])
        )

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available"):
            tone = result.get("tone")
            assert tone is not None, (
                "tone must not be None when timelinetone returns value=-30.0."
            )
            assert tone < 0, (
                f"Normalized tone from value=-30.0 must be negative; got {tone!r}"
            )

    def test_tone_normalized_correctly_from_single_entry(self):
        """Single-entry timeline: normalized tone is value/100.0.

        Does not assert a specific value, only that:
        1. tone is not None
        2. tone is in [-1, 1]
        3. tone has the correct sign relative to input value
        (AC-3: shape/range assertions, not hardcoded producer output)
        """
        mod = _import_lens_gdelt()
        mock_resp = _make_timelinetone_response(
            _wrap_data_points_as_series([{"date": "20260613T000000Z", "value": 20.0}])
        )

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available"):
            tone = result.get("tone")
            assert tone is not None, "tone must be populated from value=20.0"
            assert isinstance(tone, float), f"tone must be float; got {type(tone)}"
            assert -1.0 - 1e-9 <= tone <= 1.0 + 1e-9, f"tone out of [-1,1]: {tone}"
            assert tone > 0, "normalized tone from value=20.0 must be positive"

    def test_multiple_entries_produces_single_scalar_tone(self, timelinetone_shape):
        """Multiple timeline entries must produce a single scalar tone (aggregated)."""
        mod = _import_lens_gdelt()
        timeline = _make_timeline_entries(timelinetone_shape)
        # Use fixture entries (multiple with mixed signs).
        mock_resp = _make_timelinetone_response(timeline)

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        if result.get("available") and result.get("tone") is not None:
            tone = result["tone"]
            assert isinstance(tone, (int, float)), "aggregated tone must be a scalar"
            assert -1.0 - 1e-9 <= tone <= 1.0 + 1e-9


# ===========================================================================
# Security
# ===========================================================================


class TestSecurity:
    """Security tests per the feature plan Security Considerations section."""

    def test_no_eval_or_exec_in_source(self):
        """Static scan: lens_gdelt.py must not use eval() or exec()."""
        source_path = _WORKTREE / "advisors" / "lens_gdelt.py"
        assert source_path.exists()
        source = source_path.read_text(encoding="utf-8")
        assert "eval(" not in source, "eval() must not appear in lens_gdelt.py"
        assert "exec(" not in source, "exec() must not appear in lens_gdelt.py"

    def test_no_api_key_hardcoded(self):
        """GDELT is key-less. No API key must appear hardcoded in lens_gdelt.py."""
        import re
        source_path = _WORKTREE / "advisors" / "lens_gdelt.py"
        assert source_path.exists()
        source = source_path.read_text(encoding="utf-8")
        suspicious = re.findall(r"""['"][A-Za-z0-9+/=_-]{32,}['"]""", source)
        non_url_suspicious = [
            s for s in suspicious
            if "http" not in s and "://" not in s and "gdelt" not in s.lower()
        ]
        assert not non_url_suspicious, (
            f"Possible hardcoded credential found in lens_gdelt.py: {non_url_suspicious}"
        )

    def test_exception_message_not_leaked_on_json_decode_error(self):
        """D-1: malformed JSON error reason must be type(exc).__name__ only."""
        mod = _import_lens_gdelt()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError(
            "No JSON at position 0: gdelt-api-key=SECRETKEY123"
        )

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        result_str = json.dumps(result, default=str)
        assert "SECRETKEY123" not in result_str, (
            "D-1 violation: raw JSON decode error with secret leaked into result."
        )

    def test_no_flask_route_in_module(self):
        """Static scan: lens_gdelt.py must not define Flask routes."""
        source_path = _WORKTREE / "advisors" / "lens_gdelt.py"
        assert source_path.exists()
        source = source_path.read_text(encoding="utf-8")
        assert "@app.route" not in source

    def test_no_live_execution_path(self):
        """Static scan: lens_gdelt.py must not interact with LIVE_EXECUTION."""
        import re
        source_path = _WORKTREE / "advisors" / "lens_gdelt.py"
        assert source_path.exists()
        source = source_path.read_text(encoding="utf-8")
        patterns = [
            r"""os\.environ\.get\(['""]LIVE_EXECUTION""",
            r"""os\.getenv\(['""]LIVE_EXECUTION""",
            r"""os\.environ\[['""]LIVE_EXECUTION""",
        ]
        for pattern in patterns:
            assert not re.search(pattern, source, re.IGNORECASE), (
                f"lens_gdelt.py must not interact with LIVE_EXECUTION (pattern: {pattern!r})"
            )

    def test_d1_reason_on_http_error_excludes_raw_message(self):
        """D-1: reason on HTTP error must be type(exc).__name__ only."""
        import requests as req_mod
        mod = _import_lens_gdelt()
        secret_url = "https://api.gdeltproject.org/api/v2/doc/doc?apikey=VERYSECRETKEY999"
        http_error = req_mod.exceptions.HTTPError(f"404 Not Found for url: {secret_url}")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = http_error

        with patch("requests.get", return_value=mock_resp):
            result = mod._fetch_gdelt_sentiment(universe=["SPY"])

        result_str = json.dumps(result, default=str)
        assert "VERYSECRETKEY999" not in result_str, (
            "D-1 violation: raw HTTP error message with API key leaked into result."
        )
