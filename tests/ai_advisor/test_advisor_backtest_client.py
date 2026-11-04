"""RED/GREEN tests — AI Advisor backtest client (advisors/composer_backtest_client.py).

Module under test: ``advisors.composer_backtest_client.run_backtest``.

Error contract:

  - ``advisors.composer_backtest_client.run_backtest``: NEVER raises; returns
    ``BacktestResult(stats=None, error=<reason>)`` on all failures (AC-X5 contract)

Architecture contracts encoded as tests:
  - Module lives at advisors/composer_backtest_client.py.
  - alpha_bot_execution.py does NOT import anything from this module (AC-X2).
  - run_backtest NEVER raises on API or transport errors — structured error only.
  - 429 → BacktestResult with error mentioning "429" or "rate limit".
  - Non-200 → BacktestResult with error (not None); error must describe the failure.
  - requests.Timeout → BacktestResult with error mentioning "timeout".
  - Successful 200 → BacktestResult with error=None, stats dict, data_warnings list.
  - data_warnings is always a list (normalised from dict or list API responses).
  - No live calls in default suite — all tests use patched requests.post.

Fixture: tests/fixtures/composer/backtest_inline_v1.json (real captured response)

Mocking strategy:
  - ``requests.post`` patched at module call site — the client uses bare requests.post,
    not a Session, so standard patch("requests.post") works.
  - No Session injection required: the client calls ``requests.post`` directly.
  - The math engine and acceptance_gate are NOT imported or mocked here.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import pathlib
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "composer" / "backtest_inline_v1.json"


def _repo_root() -> pathlib.Path:
    return _REPO_ROOT


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_client() -> types.ModuleType:
    """Import advisors.composer_backtest_client."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.composer_backtest_client")


def _parse_source(relpath: str) -> ast.Module:
    path = _REPO_ROOT / relpath
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _make_mock_response(
    status_code: int, body: dict | None = None, headers: dict | None = None
) -> MagicMock:
    """Build a minimal mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.headers = headers or {}
    if body is not None:
        mock.json.return_value = body
        mock.text = json.dumps(body)[:200]
    else:
        mock.json.side_effect = ValueError("no body")
        mock.text = ""
    return mock


# ---------------------------------------------------------------------------
# Fixtures (function-scoped — no shared mutable state)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def captured_fixture() -> dict:
    """Real captured Composer backtest response. Fails fast if missing."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "The client agent must capture a real response via /api-fixture skill."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def response_body(captured_fixture) -> dict:
    """The raw API response body from the fixture."""
    body = captured_fixture.get("response", captured_fixture)
    assert isinstance(body, dict)
    return body


@pytest.fixture
def symphony_id(captured_fixture) -> str:
    sid = captured_fixture.get("_symphony_id", "")
    assert sid, "Fixture must include _symphony_id in provenance metadata."
    return sid


@pytest.fixture
def minimal_raw_value() -> dict:
    """Minimal structurally-valid raw_value tree for request body tests."""
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "step": "root",
        "name": "Test Symphony Fixture",
        "description": "Minimal fixture",
        "weight": {"num": "1", "den": "1"},
        "rebalance": "daily",
        "children": [],
    }


# ---------------------------------------------------------------------------
# Section 1 — Module exists and exposes the required public interface
# ---------------------------------------------------------------------------


class TestModuleExists:
    """advisors/composer_backtest_client.py must exist and expose its contract."""

    def test_module_is_importable(self):
        """advisors.composer_backtest_client must be importable."""
        client = _import_client()
        assert client is not None

    def test_module_exposes_run_backtest(self):
        """Module must expose run_backtest as the primary callable."""
        client = _import_client()
        assert callable(getattr(client, "run_backtest", None)), (
            "advisors.composer_backtest_client must expose run_backtest."
        )

    def test_module_exposes_submit_backtest_alias(self):
        """submit_backtest must be an alias for run_backtest (same object)."""
        client = _import_client()
        assert getattr(client, "submit_backtest", None) is not None, (
            "submit_backtest alias must exist for callers who prefer that name."
        )

    def test_module_exposes_backtest_result_type(self):
        """Module must expose BacktestResult as the structured return type."""
        client = _import_client()
        assert hasattr(client, "BacktestResult"), (
            "advisors.composer_backtest_client must expose BacktestResult — "
            "the structured return carrying stats, data_warnings, daily_returns, error."
        )


# ---------------------------------------------------------------------------
# Section 2 — The AC-X5 never-raises contract
# ---------------------------------------------------------------------------


class TestNeverRaisesContract:
    """run_backtest MUST NEVER raise on API or transport errors (AC-X5).

    One candidate's failure must not abort the batch. All error states are
    returned as BacktestResult(error=<reason>) — never a raised exception.
    """

    def test_429_returns_error_result_not_raises(self, minimal_raw_value):
        """429 response → BacktestResult with error, not an unhandled exception."""
        client = _import_client()
        mock_resp = _make_mock_response(429, headers={"Retry-After": "0"})

        with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)

        assert result is not None, "run_backtest must return a result on 429."
        assert not isinstance(result, Exception), "run_backtest must not raise on 429."
        assert isinstance(result, client.BacktestResult), (
            f"Expected BacktestResult, got {type(result).__name__!r}."
        )
        assert result.error is not None, (
            "BacktestResult.error must be non-None on a 429 response. "
            "AC-X5: per-candidate 'backtest failed' with reason must be surfaceable."
        )

    def test_500_returns_error_result_not_raises(self, minimal_raw_value):
        """5xx response → BacktestResult with error, not an unhandled exception."""
        client = _import_client()
        mock_resp = _make_mock_response(500)

        with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)

        assert result is not None
        assert isinstance(result, client.BacktestResult)
        assert result.error is not None, "BacktestResult.error must be non-None on a 500 response."

    def test_401_returns_error_result_not_raises(self, minimal_raw_value):
        """401 Unauthorized → BacktestResult with error (AC-X4 missing-key state)."""
        client = _import_client()
        mock_resp = _make_mock_response(401)

        with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)

        assert result is not None
        assert isinstance(result, client.BacktestResult)
        assert result.error is not None, (
            "401 (missing/invalid credentials) must return error result. "
            "AC-X4: advisor unavailable state must be explicit, not an unhandled exception."
        )

    def test_timeout_returns_error_result_not_raises(self, minimal_raw_value):
        """requests.Timeout → BacktestResult with error, not propagated exception."""
        client = _import_client()

        with patch("requests.post", side_effect=requests.Timeout("mock timeout")):
            result = client.run_backtest(raw_value=minimal_raw_value)

        assert result is not None, "Timeout must return a BacktestResult, not raise."
        assert isinstance(result, client.BacktestResult)
        assert result.error is not None, "Timeout must produce BacktestResult with non-None error."
        assert "timeout" in result.error.lower() or "timed" in result.error.lower(), (
            f"Timeout error must mention the timeout condition; got: {result.error!r}."
        )

    def test_transport_error_returns_error_result_not_raises(self, minimal_raw_value):
        """requests.ConnectionError → BacktestResult with error, not propagated exception."""
        client = _import_client()

        with (
            patch("requests.post", side_effect=requests.ConnectionError("mock conn error")),
            patch("time.sleep"),
        ):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)

        assert result is not None
        assert isinstance(result, client.BacktestResult)
        assert result.error is not None

    def test_success_result_has_error_none(self, minimal_raw_value, response_body):
        """A 200 response must produce BacktestResult with error=None."""
        client = _import_client()
        mock_resp = _make_mock_response(200, body=response_body)

        with patch("requests.post", return_value=mock_resp):
            result = client.run_backtest(raw_value=minimal_raw_value)

        assert result.error is None, (
            f"A 200 response must produce error=None. Got: {result.error!r}."
        )


# ---------------------------------------------------------------------------
# Section 3 — Error result shape: reason string is informative
# ---------------------------------------------------------------------------


class TestErrorResultShape:
    """Error results must include a non-empty reason string (AC-X5: 'with reason')."""

    def test_429_error_mentions_429_or_rate_limit(self, minimal_raw_value):
        """429 error reason must mention 429 or 'rate limit'."""
        client = _import_client()
        mock_resp = _make_mock_response(429, headers={"Retry-After": "0"})

        with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)

        error = result.error
        assert "429" in error or "rate" in error.lower() or "limit" in error.lower(), (
            f"429 error must mention '429' or 'rate limit'; got: {error!r}."
        )

    def test_error_reason_is_non_empty_string(self, minimal_raw_value):
        """All error results must have a non-empty string reason."""
        client = _import_client()

        for status in (400, 401, 403, 404, 429, 500, 502, 503):
            mock_resp = _make_mock_response(status)
            with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
                result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)
            assert isinstance(result.error, str) and len(result.error) > 0, (
                f"HTTP {status} must produce a non-empty error string; got: {result.error!r}."
            )

    def test_error_result_has_none_stats(self, minimal_raw_value):
        """Error results must have stats=None (not a partial dict)."""
        client = _import_client()
        mock_resp = _make_mock_response(500)

        with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=0)

        assert result.stats is None, f"Error result must have stats=None, got: {result.stats!r}."


# ---------------------------------------------------------------------------
# Section 4 — Successful response parsing from the real fixture
# ---------------------------------------------------------------------------


class TestSuccessfulParsing:
    """The client must parse the real fixture into a valid BacktestResult."""

    def test_success_result_has_stats_dict(self, minimal_raw_value, response_body):
        """200 response → stats must be a dict (not None)."""
        client = _import_client()
        mock_resp = _make_mock_response(200, body=response_body)

        with patch("requests.post", return_value=mock_resp):
            result = client.run_backtest(raw_value=minimal_raw_value)

        assert isinstance(result.stats, dict), (
            f"stats must be a dict on 200 response, got {type(result.stats).__name__!r}."
        )

    def test_success_result_data_warnings_is_always_list(self, minimal_raw_value, response_body):
        """data_warnings must be a list (normalised from dict or list API response).

        The live API has returned both empty-dict {} and list [] for data_warnings.
        The module must normalise to list regardless.
        """
        client = _import_client()
        mock_resp = _make_mock_response(200, body=response_body)

        with patch("requests.post", return_value=mock_resp):
            result = client.run_backtest(raw_value=minimal_raw_value)

        assert isinstance(result.data_warnings, list), (
            f"data_warnings must always be a list; got {type(result.data_warnings).__name__!r}. "
            "The normalisation must coerce empty-dict API responses to []."
        )

    def test_success_result_data_warnings_is_list_when_api_returns_empty_dict(
        self, minimal_raw_value, response_body
    ):
        """data_warnings={} in API response → data_warnings=[] in result.

        This is the specific normalisation that the original test bugs uncovered.
        An or-based fallback ('' or fallback) evaluates [] as falsy and incorrectly
        substitutes the fallback. The client must use isinstance checks, not or.
        """
        client = _import_client()
        # Override response body with data_warnings as empty dict (common API pattern)
        body_with_empty_dict_warnings = dict(response_body)
        body_with_empty_dict_warnings["data_warnings"] = {}
        mock_resp = _make_mock_response(200, body=body_with_empty_dict_warnings)

        with patch("requests.post", return_value=mock_resp):
            result = client.run_backtest(raw_value=minimal_raw_value)

        assert result.data_warnings == [], (
            f"data_warnings={{}} (empty dict) must be normalised to [] (empty list). "
            f"Got: {result.data_warnings!r}. "
            "An or-based fallback would incorrectly treat [] as falsy and return a non-list."
        )

    def test_success_result_daily_returns_is_dict(self, symphony_id, response_body):
        """daily_returns must be a dict on 200 response (may be empty if no dvm_capital)."""
        client = _import_client()
        raw_value = {"id": symphony_id, "step": "root", "name": "test", "children": []}
        mock_resp = _make_mock_response(200, body=response_body)

        with patch("requests.post", return_value=mock_resp):
            result = client.run_backtest(raw_value=raw_value, symphony_id=symphony_id)

        assert isinstance(result.daily_returns, dict), (
            f"daily_returns must be a dict, got {type(result.daily_returns).__name__!r}."
        )

    def test_success_result_daily_returns_values_are_finite_floats(
        self, symphony_id, response_body
    ):
        """All daily_returns values must be finite floats (no inf, no NaN)."""
        import math as _math

        client = _import_client()
        raw_value = {"id": symphony_id, "step": "root", "name": "test", "children": []}
        mock_resp = _make_mock_response(200, body=response_body)

        with patch("requests.post", return_value=mock_resp):
            result = client.run_backtest(raw_value=raw_value, symphony_id=symphony_id)

        for date_str, val in result.daily_returns.items():
            assert isinstance(val, float), (
                f"daily_returns[{date_str!r}] must be float, got {type(val).__name__!r}."
            )
            assert _math.isfinite(val), (
                f"daily_returns[{date_str!r}]={val!r} is not finite. "
                "NaN/inf in daily_returns would corrupt fold-transform t-stats."
            )


# ---------------------------------------------------------------------------
# Section 5 — Request body and auth contract
# ---------------------------------------------------------------------------


class TestRequestContract:
    """The client must build a valid request body with auth headers."""

    def test_posts_to_backtest_endpoint(self, minimal_raw_value, response_body):
        """The POST must target /api/v0.1/backtest."""
        client = _import_client()
        captured_urls = []

        def capture(url, json=None, headers=None, timeout=None, **kwargs):
            captured_urls.append(url)
            return _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=capture):
            client.run_backtest(raw_value=minimal_raw_value)

        assert len(captured_urls) >= 1
        assert "/api/v0.1/backtest" in captured_urls[0] or captured_urls[0].endswith("/backtest"), (
            f"Expected /api/v0.1/backtest in URL, got {captured_urls[0]!r}."
        )

    def test_body_nests_tree_under_symphony_raw_value(self, minimal_raw_value, response_body):
        """Request body must nest the tree under symphony.raw_value."""
        client = _import_client()
        captured_bodies = []

        def capture(url, json=None, headers=None, timeout=None, **kwargs):
            captured_bodies.append(json)
            return _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=capture):
            client.run_backtest(raw_value=minimal_raw_value)

        body = captured_bodies[0]
        assert "symphony" in body and "raw_value" in body["symphony"], (
            f"Body must have symphony.raw_value. Keys found: {list(body.keys())}."
        )
        assert body["symphony"]["raw_value"] == minimal_raw_value

    def test_body_has_required_scalar_fields(self, minimal_raw_value, response_body):
        """Request body must include capital, apply_reg_fee, apply_taf_fee, slippage_percent, broker."""
        client = _import_client()
        captured_bodies = []

        def capture(url, json=None, headers=None, timeout=None, **kwargs):
            captured_bodies.append(json)
            return _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=capture):
            client.run_backtest(raw_value=minimal_raw_value)

        body = captured_bodies[0]
        required = {"capital", "apply_reg_fee", "apply_taf_fee", "slippage_percent", "broker"}
        missing = required - set(body.keys())
        assert not missing, f"Request body missing required fields: {missing}."

    def test_request_uses_composer_auth_headers(self, minimal_raw_value, response_body):
        """Request must include x-api-key-id and authorization headers."""
        client = _import_client()
        captured_headers = []

        def capture(url, json=None, headers=None, timeout=None, **kwargs):
            captured_headers.append(headers or {})
            return _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=capture):
            client.run_backtest(raw_value=minimal_raw_value)

        h = {k.lower(): v for k, v in captured_headers[0].items()}
        assert "x-api-key-id" in h, "Request must include x-api-key-id header."
        assert "authorization" in h, "Request must include authorization header."


# ---------------------------------------------------------------------------
# Section 6 — Retry: 429 then 200 succeeds; bounded retries
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    """Retry is bounded; 429→200 sequence produces a successful result."""

    def test_retry_succeeds_after_429_then_200(self, minimal_raw_value, response_body):
        """429 followed by 200 must return a successful BacktestResult (error=None)."""
        client = _import_client()
        call_count = {"n": 0}

        def sequenced(url, json=None, headers=None, timeout=None, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_mock_response(429, headers={"Retry-After": "0"})
            return _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=sequenced), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=2)

        assert call_count["n"] >= 2, (
            f"Expected ≥2 POST calls (429 retry + 200), got {call_count['n']}."
        )
        assert result.error is None, (
            f"After 429 → 200, run_backtest must return error=None. Got: {result.error!r}."
        )

    def test_retry_count_bounded_by_max_retries(self, minimal_raw_value):
        """Client must not retry more than max_retries times on repeated 429."""
        client = _import_client()
        call_count = {"n": 0}

        def always_429(url, json=None, headers=None, timeout=None, **kwargs):
            call_count["n"] += 1
            return _make_mock_response(429, headers={"Retry-After": "0"})

        max_retries = 2
        with patch("requests.post", side_effect=always_429), patch("time.sleep"):
            result = client.run_backtest(raw_value=minimal_raw_value, max_retries=max_retries)

        assert call_count["n"] <= max_retries + 1, (
            f"Client made {call_count['n']} POST calls with max_retries={max_retries}. "
            f"Expected at most {max_retries + 1}. Unbounded retry loops are forbidden."
        )
        assert result.error is not None, (
            "After exhausting max_retries on 429, result.error must be non-None."
        )


# ---------------------------------------------------------------------------
# Section 7 — Architecture: AC-X2 isolation + no top-level network call
# ---------------------------------------------------------------------------


class TestArchitectureContracts:
    """AC-X2 isolation and import-time safety."""

    def test_alpha_bot_execution_does_not_import_composer_backtest_client(self):
        """alpha_bot_execution.py must not import from advisors.composer_backtest_client.

        AC-X2: no advisor code on the 1-minute live execution path.
        """
        tree = _parse_source("alpha_bot_execution.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "composer_backtest_client" not in (node.module or ""), (
                    "alpha_bot_execution.py must not import from advisors.composer_backtest_client. "
                    "AC-X2 violation."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "composer_backtest_client" not in alias.name

    def test_module_has_no_top_level_network_call_on_import(self):
        """Importing advisors.composer_backtest_client must not trigger a network call."""
        call_count = {"n": 0}
        orig_post = requests.post
        orig_get = requests.get

        def counting_post(*a, **kw):
            call_count["n"] += 1
            return orig_post(*a, **kw)

        def counting_get(*a, **kw):
            call_count["n"] += 1
            return orig_get(*a, **kw)

        sys.modules.pop("advisors.composer_backtest_client", None)

        with (
            patch("requests.post", side_effect=counting_post),
            patch("requests.get", side_effect=counting_get),
        ):
            _ensure_repo_on_path()
            importlib.import_module("advisors.composer_backtest_client")

        assert call_count["n"] == 0, (
            f"Importing advisors.composer_backtest_client triggered {call_count['n']} "
            "network call(s) at module load. No top-level network calls allowed."
        )


# ---------------------------------------------------------------------------
# Section 8: dated window and compact request body (keyword-only parameters)
# ---------------------------------------------------------------------------

_TENURE_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "composer" / "tenure-review"


def _tenure_record(name: str) -> dict:
    return json.loads((_TENURE_FIXTURES / name).read_text(encoding="utf-8"))


def _post_capturing(calls: list, response: MagicMock):
    """A requests.post stand-in that records every keyword it was called with."""

    def capture(url, *args, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    return capture


def _day_to_iso(day_int: int) -> str:
    import datetime as _dt

    return (_dt.date(1970, 1, 1) + _dt.timedelta(days=day_int)).isoformat()


class TestDatedCompactRequest:
    """start_date, end_date and compact_body: opt-in, keyword-only, default behaviour kept."""

    def test_new_parameters_are_keyword_only_and_default_to_the_existing_request(self):
        client = _import_client()
        params = inspect.signature(client.run_backtest).parameters

        for name, default in (("start_date", None), ("end_date", None), ("compact_body", False)):
            assert name in params, f"run_backtest must accept {name}"
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default == default

    def test_passing_the_defaults_explicitly_sends_the_unchanged_json_request(
        self, minimal_raw_value, response_body
    ):
        client = _import_client()
        calls: list = []
        reply = _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            client.run_backtest(
                raw_value=minimal_raw_value, start_date=None, end_date=None, compact_body=False
            )

        assert len(calls) == 1
        assert calls[0].get("data") is None
        body = calls[0]["json"]
        assert "start_date" not in body
        assert "end_date" not in body
        assert body["symphony"]["raw_value"] == minimal_raw_value

    @pytest.mark.parametrize(
        ("kwargs", "expected_keys"),
        [
            ({"start_date": "2026-07-20", "end_date": "2026-10-19"}, {"start_date", "end_date"}),
            ({"start_date": "2026-07-20"}, {"start_date"}),
            ({"end_date": "2026-10-19"}, {"end_date"}),
        ],
        ids=["both", "start_only", "end_only"],
    )
    def test_each_date_is_added_to_the_json_body_only_when_given(
        self, minimal_raw_value, response_body, kwargs, expected_keys
    ):
        client = _import_client()
        calls: list = []
        reply = _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            client.run_backtest(raw_value=minimal_raw_value, **kwargs)

        body = calls[0]["json"]
        assert calls[0].get("data") is None
        assert {"start_date", "end_date"} & set(body) == expected_keys
        for key in expected_keys:
            assert body[key] == kwargs[key]

    def test_compact_body_is_sent_as_data_with_compact_separators_and_the_json_header(
        self, minimal_raw_value, response_body
    ):
        client = _import_client()
        calls: list = []
        reply = _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            client.run_backtest(
                raw_value=minimal_raw_value,
                start_date="2026-07-20",
                end_date="2026-10-19",
                compact_body=True,
            )

        call = calls[0]
        assert call.get("json") is None, "a compact body is sent as data=, never json="
        data = call["data"]
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        parsed = json.loads(text)
        assert text == json.dumps(parsed, separators=(",", ":"), allow_nan=False)
        assert parsed["start_date"] == "2026-07-20"
        assert parsed["end_date"] == "2026-10-19"
        assert parsed["symphony"]["raw_value"] == minimal_raw_value
        assert call["headers"].get("Content-Type") == "application/json"
        assert len(text) < len(json.dumps(parsed))

    def test_compact_body_stays_ascii_when_the_tree_holds_non_ascii_text(self, response_body):
        client = _import_client()
        calls: list = []
        reply = _make_mock_response(200, body=response_body)
        tree = {"id": "x", "step": "root", "name": "Café Münzen ✓", "children": []}

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            client.run_backtest(raw_value=tree, compact_body=True)

        data = calls[0]["data"]
        text = data.decode("ascii") if isinstance(data, bytes) else data
        assert text.isascii(), "http.client encodes a str body as latin-1: keep it ASCII"
        assert json.loads(text)["symphony"]["raw_value"]["name"] == tree["name"]

    def test_compact_body_with_a_non_finite_number_returns_an_error_result_without_posting(
        self, minimal_raw_value, response_body
    ):
        client = _import_client()
        calls: list = []
        reply = _make_mock_response(200, body=response_body)

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            result = client.run_backtest(
                raw_value=minimal_raw_value, capital=float("nan"), compact_body=True
            )

        assert result.error
        assert result.stats is None
        assert calls == []

    def test_the_captured_dated_compact_response_parses_to_the_requested_window(self):
        client = _import_client()
        record = _tenure_record("case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json")
        symphony_id = record["_request"]["symphony_id"]
        params = record["_request"]["body_params"]
        body = record["response_body"]
        calls: list = []
        reply = _make_mock_response(200, body=body)

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            result = client.run_backtest(
                raw_value={"id": symphony_id, "step": "root", "children": []},
                symphony_id=symphony_id,
                start_date=params["start_date"],
                end_date=params["end_date"],
                compact_body=True,
            )

        assert result.error is None
        assert result.first_day == _day_to_iso(body["first_day"]) == params["start_date"]
        assert result.last_market_day == _day_to_iso(body["last_market_day"]) == params["end_date"]
        assert result.daily_portfolio_values
        assert min(result.daily_portfolio_values) >= result.first_day
        assert max(result.daily_portfolio_values) <= result.last_market_day

    def test_a_413_with_a_compact_body_is_an_error_result_and_is_not_retried(
        self, minimal_raw_value
    ):
        client = _import_client()
        record = _tenure_record("case6_backtest_413_too_large.json")
        calls: list = []
        reply = MagicMock()
        reply.status_code = record["response_status"]
        reply.headers = {}
        reply.text = record["response_body_text"]
        reply.json.side_effect = ValueError("not json")

        with patch("requests.post", side_effect=_post_capturing(calls, reply)):
            result = client.run_backtest(
                raw_value=minimal_raw_value, compact_body=True, max_retries=1
            )

        assert len(calls) == 1
        assert result.stats is None
        assert result.error.startswith("HTTP 413")


# ---------------------------------------------------------------------------
# Retry-After handling and what the client logs (the tenure review calls it)
# ---------------------------------------------------------------------------

_ABSENT = object()
# Longest wait the client may take on a server's say-so: the length of Composer's per-minute
# rate-limit window, so a hostile or mistaken header cannot stall a caller for hours.
_MAX_RETRY_AFTER_SECONDS = 60.0
_LEAK = "LEAKMARK-backtest-reply-3318"
_KEY_ID = "test-key-id-7c41e0aa"
_SECRET = "test-secret-5b93d2f1-not-a-real-credential"


def _sleeps_after_a_429_with(retry_after, minimal_raw_value, response_body, *, max_retries=1):
    """The sleep lengths and the result of one call whose first reply is a 429 carrying the
    given Retry-After value (or none) and whose second reply is a 200."""
    client = _import_client()
    replies: list = []

    def post(url, **kwargs):
        replies.append(url)
        if len(replies) == 1:
            headers = {} if retry_after is _ABSENT else {"Retry-After": retry_after}
            return _make_mock_response(429, headers=headers)
        return _make_mock_response(200, body=response_body)

    with patch("requests.post", side_effect=post), patch("time.sleep") as sleep:
        result = client.run_backtest(raw_value=minimal_raw_value, max_retries=max_retries)
    return [call.args[0] for call in sleep.call_args_list], result


class TestRetryAfterHandling:
    """A seconds value is honoured, anything else falls back to the existing backoff, nothing
    raises, and no wait is longer than the clamp."""

    @pytest.mark.parametrize(("header", "seconds"), [("7", 7.0), ("2.5", 2.5), ("0", 0.0)])
    def test_a_seconds_value_is_the_wait(self, minimal_raw_value, response_body, header, seconds):
        sleeps, result = _sleeps_after_a_429_with(header, minimal_raw_value, response_body)

        assert sleeps == [pytest.approx(seconds)]
        assert result.error is None

    @pytest.mark.parametrize(
        "header",
        ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", "", "   ", "7 seconds", "0x10"],
        ids=["http_date", "word", "empty", "blank", "unit_suffix", "hex"],
    )
    def test_a_value_that_is_not_a_number_of_seconds_falls_back_to_the_existing_backoff(
        self, minimal_raw_value, response_body, header
    ):
        baseline, _ = _sleeps_after_a_429_with(_ABSENT, minimal_raw_value, response_body)

        sleeps, result = _sleeps_after_a_429_with(header, minimal_raw_value, response_body)

        assert len(baseline) == 1
        assert sleeps == baseline
        assert result.error is None

    @pytest.mark.parametrize("header", ["86400", "999999999", "1e9"])
    def test_a_wait_longer_than_the_clamp_is_clamped_and_not_dropped(
        self, minimal_raw_value, response_body, header
    ):
        sleeps, result = _sleeps_after_a_429_with(header, minimal_raw_value, response_body)

        assert len(sleeps) == 1
        assert 0 < sleeps[0] <= _MAX_RETRY_AFTER_SECONDS
        assert result.error is None

    @pytest.mark.parametrize("header", ["-3", "-0.5", "nan", "inf", "-inf", "1e999"])
    def test_a_negative_or_non_finite_value_never_raises_and_never_yields_an_invalid_wait(
        self, minimal_raw_value, response_body, header
    ):
        sleeps, result = _sleeps_after_a_429_with(header, minimal_raw_value, response_body)

        assert len(sleeps) == 1
        assert 0 <= sleeps[0] <= _MAX_RETRY_AFTER_SECONDS
        assert result.error is None

    @pytest.mark.parametrize("header", ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", "-3", "nan"])
    def test_a_last_attempt_with_an_unusable_value_returns_the_429_error_and_does_not_raise(
        self, minimal_raw_value, response_body, header
    ):
        sleeps, result = _sleeps_after_a_429_with(
            header, minimal_raw_value, response_body, max_retries=0
        )

        assert sleeps == []
        assert result.stats is None
        assert "429" in result.error


class TestRunBacktestLogs:
    """No log line of run_backtest carries a credential, a response body or text, or an
    exception message; exceptions appear by type name."""

    @pytest.fixture(autouse=True)
    def _credentials(self, monkeypatch):
        import alpha_bot_execution  # noqa: PLC0415

        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_KEY_ID", _KEY_ID)
        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_SECRET", _SECRET)

    @staticmethod
    def _run(client, minimal_raw_value, post, **kwargs):
        with patch("requests.post", side_effect=post), patch("time.sleep"):
            return client.run_backtest(raw_value=minimal_raw_value, **kwargs)

    @staticmethod
    def _assert_clean(caplog):
        assert caplog.records, "the capture saw the client"
        for secret in (_KEY_ID, _SECRET, f"Bearer {_SECRET}"):
            assert secret not in caplog.text
        assert "LEAKMARK" not in caplog.text

    @pytest.mark.parametrize("compact_body", [False, True])
    def test_a_successful_call_logs_no_credential(
        self, caplog, minimal_raw_value, response_body, compact_body
    ):
        caplog.set_level("DEBUG")
        client = _import_client()

        result = self._run(
            client,
            minimal_raw_value,
            lambda url, **kwargs: _make_mock_response(200, body=response_body),
            compact_body=compact_body,
        )

        assert result.error is None
        self._assert_clean(caplog)

    @pytest.mark.parametrize("status", [400, 401, 413, 429, 500, 503])
    def test_an_error_reply_logs_neither_its_body_nor_a_credential(
        self, caplog, minimal_raw_value, status
    ):
        caplog.set_level("DEBUG")
        client = _import_client()
        body = {"marker": _LEAK, "errors": [{"status": status}]}

        result = self._run(
            client,
            minimal_raw_value,
            lambda url, **kwargs: _make_mock_response(status, body=body),
            max_retries=1,
        )

        assert result.error is not None
        self._assert_clean(caplog)

    @pytest.mark.parametrize(
        "make_exc",
        [
            pytest.param(lambda: requests.Timeout(_LEAK), id="Timeout"),
            pytest.param(lambda: requests.ConnectionError(_LEAK), id="ConnectionError"),
            pytest.param(lambda: requests.exceptions.ChunkedEncodingError(_LEAK), id="Chunked"),
        ],
    )
    def test_a_raised_exception_is_logged_by_type_name_and_never_by_message(
        self, caplog, minimal_raw_value, make_exc
    ):
        caplog.set_level("DEBUG")
        client = _import_client()
        exc = make_exc()

        def post(url, **kwargs):
            raise exc

        result = self._run(client, minimal_raw_value, post, max_retries=1)

        assert result.error is not None
        self._assert_clean(caplog)
        assert type(exc).__name__ in caplog.text


# ---------------------------------------------------------------------------
# Redirects, the retry wait floor, the retry log line and the stated wait bound
# ---------------------------------------------------------------------------

_FLOOR = 3.0  # the tenure review's spacing between any two Composer requests
_REDIRECT_STATUSES = [301, 302, 307, 308]
_CLIENT_LOGGER = "advisors.composer_backtest_client"


def _scripted(client, minimal_raw_value, replies, **kwargs):
    """One run_backtest call against scripted replies (a response, or an exception to raise,
    in turn; the last one repeats). Returns (sleep lengths, result, recorded post calls)."""
    calls: list = []

    def post(url, **post_kwargs):
        calls.append({"url": url, **post_kwargs})
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    with patch("requests.post", side_effect=post), patch("time.sleep") as sleep:
        result = client.run_backtest(raw_value=minimal_raw_value, **kwargs)
    return [call.args[0] for call in sleep.call_args_list], result, calls


def _failure(branch: str):
    """A fresh failing reply for a retry branch: a status (a transient one, or 429 with no
    Retry-After) or a transport error."""
    if branch == "ConnectionError":
        return requests.ConnectionError("conn")
    return _make_mock_response(int(branch))


class TestRedirectsAreNeverFollowed:
    """A redirect would carry the key header to another host, so no caller follows one."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"compact_body": True},
            {"start_date": "2026-07-20", "end_date": "2026-10-19", "compact_body": True},
        ],
        ids=["defaults", "compact", "dated_compact"],
    )
    @pytest.mark.parametrize("retry", [False, True], ids=["one_post", "retried_post"])
    def test_every_post_is_sent_with_redirects_disabled(
        self, minimal_raw_value, response_body, kwargs, retry
    ):
        client = _import_client()
        ok = _make_mock_response(200, body=response_body)
        replies = [_make_mock_response(503), ok] if retry else [ok]

        _, result, calls = _scripted(client, minimal_raw_value, replies, max_retries=1, **kwargs)

        assert result.error is None
        assert len(calls) == len(replies)
        assert all(call.get("allow_redirects") is False for call in calls)

    @pytest.mark.parametrize("status", _REDIRECT_STATUSES)
    def test_a_redirect_reply_is_an_error_result_that_is_neither_followed_nor_retried(
        self, minimal_raw_value, status
    ):
        client = _import_client()
        reply = _make_mock_response(status, headers={"Location": "https://redirect.invalid/x"})

        sleeps, result, calls = _scripted(client, minimal_raw_value, [reply])

        assert result.stats is None
        assert result.error.startswith(f"HTTP {status}")
        assert len(calls) == 1, "a redirect is not a transient failure"
        assert sleeps == []


class TestRetryWaitFloor:
    """min_retry_wait_seconds is a floor on every wait before a retry, whatever failure
    caused the retry; it never shortens a longer wait, and without it the waits are the
    existing backoff."""

    @pytest.mark.parametrize("branch", ["500", "503", "ConnectionError"])
    def test_the_floor_lifts_a_shorter_backoff_and_leaves_a_longer_one(
        self, minimal_raw_value, response_body, branch
    ):
        client = _import_client()
        ok = _make_mock_response(200, body=response_body)
        replies = [_failure(branch), _failure(branch), _failure(branch), ok]

        sleeps, result, calls = _scripted(
            client, minimal_raw_value, replies, max_retries=3, min_retry_wait_seconds=_FLOOR
        )

        assert result.error is None
        assert len(calls) == 4
        assert sleeps == [_FLOOR, _FLOOR, 4.0], "backoff 1 s and 2 s are lifted to the floor"

    def test_the_floor_lifts_the_wait_after_a_rate_limit_that_names_no_wait(
        self, minimal_raw_value, response_body
    ):
        client = _import_client()
        ok = _make_mock_response(200, body=response_body)
        replies = [_failure("429"), _failure("429"), ok]

        sleeps, result, _ = _scripted(
            client, minimal_raw_value, replies, max_retries=2, min_retry_wait_seconds=_FLOOR
        )

        assert result.error is None
        assert len(sleeps) == 2
        assert all(wait >= _FLOOR for wait in sleeps)

    def test_the_floor_never_shortens_a_longer_retry_after(self, minimal_raw_value, response_body):
        client = _import_client()
        replies = [
            _make_mock_response(429, headers={"Retry-After": "20"}),
            _make_mock_response(200, body=response_body),
        ]

        sleeps, _, _ = _scripted(
            client, minimal_raw_value, replies, max_retries=1, min_retry_wait_seconds=_FLOOR
        )

        assert sleeps == [pytest.approx(20.0)]

    @pytest.mark.parametrize("branch", ["500", "ConnectionError"])
    def test_without_a_floor_the_waits_are_the_existing_backoff(
        self, minimal_raw_value, response_body, branch
    ):
        client = _import_client()
        ok = _make_mock_response(200, body=response_body)

        sleeps, result, _ = _scripted(
            client,
            minimal_raw_value,
            [_failure(branch), _failure(branch), ok],
            max_retries=2,
        )

        assert result.error is None
        assert sleeps == [1.0, 2.0]

    @pytest.mark.parametrize("branch", ["500", "429", "ConnectionError"])
    def test_no_wait_follows_the_last_attempt(self, minimal_raw_value, branch):
        client = _import_client()

        sleeps, result, calls = _scripted(
            client,
            minimal_raw_value,
            [_failure(branch)],
            max_retries=1,
            min_retry_wait_seconds=_FLOOR,
        )

        assert result.stats is None and result.error
        assert len(calls) == 2
        assert len(sleeps) == 1, "one wait between the two attempts and none after the last"

    def test_a_timeout_is_not_retried_and_waits_nothing(self, minimal_raw_value):
        client = _import_client()

        sleeps, result, calls = _scripted(
            client,
            minimal_raw_value,
            [requests.Timeout("slow")],
            min_retry_wait_seconds=_FLOOR,
        )

        assert result.stats is None and result.error
        assert len(calls) == 1
        assert sleeps == []


class TestRetryLogStatesTheRealWait:
    """The line logged before a retry names the wait that is slept, floor included."""

    @pytest.mark.parametrize("floor", [0.0, _FLOOR], ids=["no_floor", "floor"])
    @pytest.mark.parametrize("branch", ["500", "429"])
    def test_the_logged_wait_is_the_slept_wait(
        self, caplog, minimal_raw_value, response_body, branch, floor
    ):
        caplog.set_level("INFO", logger=_CLIENT_LOGGER)
        client = _import_client()
        replies = [_failure(branch), _make_mock_response(200, body=response_body)]

        sleeps, result, _ = _scripted(
            client, minimal_raw_value, replies, max_retries=1, min_retry_wait_seconds=floor
        )

        assert result.error is None
        assert len(sleeps) == 1
        lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == _CLIENT_LOGGER and re.search(r"retry|sleep", r.getMessage(), re.I)
        ]
        assert len(lines) == 1, lines
        stated = re.findall(r"(\d+(?:\.\d+)?)\s*s\b", lines[0])
        assert [float(value) for value in stated] == [pytest.approx(sleeps[0], abs=0.05)], lines[0]


def _comment_lines_above(lines, index):
    comment = []
    for line in reversed(lines[:index]):
        if not line.startswith("#"):
            break
        comment.append(line)
    return comment


class TestStatedWaitBound:
    """The module states a total retry wait only if a caller can never exceed it."""

    @staticmethod
    def _total_wait(minimal_raw_value, replies):
        client = _import_client()
        sleeps, _, _ = _scripted(client, minimal_raw_value, replies)
        return sum(sleeps)

    def test_the_named_bound_covers_the_longest_total_wait_a_default_caller_can_incur(
        self, minimal_raw_value
    ):
        client = _import_client()
        huge = _make_mock_response(429, headers={"Retry-After": "86400"})
        totals = [
            self._total_wait(minimal_raw_value, [huge]),
            self._total_wait(minimal_raw_value, [_failure("503")]),
            self._total_wait(minimal_raw_value, [_failure("ConnectionError")]),
        ]

        assert max(totals) > 0, "fixture sanity: the scenarios did wait"
        assert max(totals) <= client.BACKTEST_MAX_RETRY_WAIT_SECONDS, totals

    def test_the_constant_and_the_module_text_do_not_claim_a_fifteen_second_total(self):
        source = (_REPO_ROOT / "advisors" / "composer_backtest_client.py").read_text("utf-8")
        docstring = ast.get_docstring(ast.parse(source)) or ""
        lines = source.splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("BACKTEST_MAX_RETRY_WAIT_SECONDS")
        )
        constant_text = " ".join([lines[index], *_comment_lines_above(lines, index)])

        assert not re.search(r"\b15\s*s", docstring), "the 15 s sum bounds only the backoff"
        assert not re.search(r"\b15\s*s", constant_text)
        assert "Retry-After" in docstring
        assert re.search(r"\b60\s*s", docstring), "the Retry-After clamp is named"

    def test_the_module_text_limits_the_bound_to_callers_with_the_default_minimum_wait(
        self, minimal_raw_value
    ):
        client = _import_client()
        huge = _make_mock_response(429, headers={"Retry-After": "86400"})
        sleeps, _, _ = _scripted(client, minimal_raw_value, [huge], min_retry_wait_seconds=120.0)
        source = (_REPO_ROOT / "advisors" / "composer_backtest_client.py").read_text("utf-8")
        docstring = " ".join((ast.get_docstring(ast.parse(source)) or "").split())
        sentences = re.split(r"(?<=\.)\s+", docstring)

        assert sum(sleeps) > client.BACKTEST_MAX_RETRY_WAIT_SECONDS, (
            "a large floor outruns the bound"
        )
        claims = [
            sentence
            for sentence in sentences
            if "BACKTEST_MAX_RETRY_WAIT_SECONDS" in sentence or "bound" in sentence
        ]
        assert claims, "the module text states the bound"
        assert any(
            "default" in sentence
            and ("min_retry_wait_seconds" in sentence or "minimum wait" in sentence)
            for sentence in claims
        ), f"the bound is stated for a caller with the default minimum wait only: {claims}"


# ---------------------------------------------------------------------------
# Live-daemon E2E regression (2026-06-12) — inline-backtest key mismatch
# ---------------------------------------------------------------------------


class TestDvmCapitalKeyMismatchFallback:
    """dvm_capital key semantics for INLINE synthetic-tree backtests are
    unattested. The exact-match-only lookup silently produced empty returns
    (all candidates withheld, None metrics) whenever the response key differed
    from the requested symphony_id. With exactly one returned series, that
    series is structurally the answer to the one posted tree.
    """

    def test_single_series_used_despite_key_mismatch(self):
        _ensure_repo_on_path()
        from advisors.composer_backtest_client import _extract_returns

        dvm = {"SOME-OPAQUE-COMPOSER-ID": {"19000": 100.0, "19001": 101.0, "19004": 102.0}}
        values, returns = _extract_returns(dvm, symphony_id="my-requested-id")
        assert len(values) == 3, "single returned series must be used on key mismatch"
        assert len(returns) == 2

    def test_exact_match_still_preferred_with_multiple_series(self):
        _ensure_repo_on_path()
        from advisors.composer_backtest_client import _extract_returns

        dvm = {
            "other": {"19000": 1.0, "19001": 2.0},
            "mine": {"19000": 100.0, "19001": 110.0},
        }
        values, _ = _extract_returns(dvm, symphony_id="mine")
        assert list(values.values()) == [100.0, 110.0]

    def test_multiple_series_no_match_returns_empty(self):
        _ensure_repo_on_path()
        from advisors.composer_backtest_client import _extract_returns

        dvm = {"a": {"19000": 1.0}, "b": {"19000": 2.0}}
        values, returns = _extract_returns(dvm, symphony_id="zzz")
        assert values == {} and returns == {}, (
            "ambiguous multi-series mismatch must stay empty — guessing would "
            "attribute another symphony's returns to the candidate"
        )


# ---------------------------------------------------------------------------
# The canonical client is the only Composer backtest client
# ---------------------------------------------------------------------------

_LF_DIGESTS = {
    "advisors/composer_backtest_client.py": (
        "bf1281b030e215e4f779697fd46849e854b46692f8945781c15e59cf2ab87661"
    ),
    "tests/fixtures/composer/backtest_inline_v1.json": (
        "a2dfa7992f11d0a008476591a526f0e20386e29b227fcfe61ff93940aa7ee7bf"
    ),
    "tests/fixtures/composer/backtest_response_v1.json": (
        "8112a737dbc56827b18d1dda7d4a825e383700dfb3a7a92706de2af4af0ada21"
    ),
}


def _lf_sha256(relpath: str) -> str:
    import hashlib

    data = (_REPO_ROOT / relpath).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


class TestRootBacktestModuleIsGone:
    """The unused root module ``composer_backtest`` no longer exists anywhere."""

    def test_importing_it_in_a_fresh_interpreter_at_the_repository_root_fails(self):
        import subprocess

        done = subprocess.run(
            [sys.executable, "-c", "import composer_backtest"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        assert done.returncode != 0, (
            "composer_backtest is still importable from the repository root"
        )
        assert "No module named 'composer_backtest'" in done.stderr

    def test_the_module_file_does_not_exist(self):
        assert not (_REPO_ROOT / "composer_backtest.py").exists()

    def test_pyproject_keeps_no_per_file_ignore_for_it(self):
        text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert not re.search(r"\bcomposer_backtest\.py", text)

    def test_the_readme_lists_no_row_for_it(self):
        text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert not re.search(r"\bcomposer_backtest\.py", text)

    def test_this_modules_docstring_compares_the_client_with_no_module_that_is_gone(self):
        docstring = ast.get_docstring(ast.parse((pathlib.Path(__file__)).read_text("utf-8"))) or ""
        assert not re.search(r"\bcomposer_backtest\b", docstring), docstring

    def test_no_tracked_python_file_imports_it(self):
        import subprocess

        listed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "ls-files", "*.py"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        importers = []
        for relpath in listed:
            path = _REPO_ROOT / relpath
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                if any(
                    name == "composer_backtest" or name.startswith("composer_backtest.")
                    for name in names
                ):
                    importers.append(relpath)
        assert importers == []


class TestCanonicalClientIsUnchanged:
    @pytest.mark.parametrize("relpath", sorted(_LF_DIGESTS))
    def test_the_client_and_its_captured_fixtures_keep_their_content(self, relpath):
        assert _lf_sha256(relpath) == _LF_DIGESTS[relpath], (
            f"{relpath} changed: removing the root module must leave the canonical client and "
            "its captured fixtures byte for byte as they were (line ends normalized)"
        )
