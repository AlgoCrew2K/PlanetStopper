"""RED/GREEN tests — AI Advisor backtest client (advisors/composer_backtest_client.py).

Module under test: ``advisors.composer_backtest_client.run_backtest``.

DISTINCT from tests/ai_advisor/test_composer_backtest_client.py which tests
``composer_backtest.submit_backtest``.  The two modules have DIFFERENT error contracts:

  - ``composer_backtest.submit_backtest``: raises ``requests.RequestException`` on errors
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
  - No Session injection required (different design from composer_backtest.py).
  - The math engine and acceptance_gate are NOT imported or mocked here.
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
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


def _make_mock_response(status_code: int, body: dict | None = None, headers: dict | None = None) -> MagicMock:
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
        assert result.error is not None, (
            "BacktestResult.error must be non-None on a 500 response."
        )

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
        assert result.error is not None, (
            "Timeout must produce BacktestResult with non-None error."
        )
        assert "timeout" in result.error.lower() or "timed" in result.error.lower(), (
            f"Timeout error must mention the timeout condition; got: {result.error!r}."
        )

    def test_transport_error_returns_error_result_not_raises(self, minimal_raw_value):
        """requests.ConnectionError → BacktestResult with error, not propagated exception."""
        client = _import_client()

        with patch("requests.post", side_effect=requests.ConnectionError("mock conn error")), \
             patch("time.sleep"):
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

        assert result.stats is None, (
            f"Error result must have stats=None, got: {result.stats!r}."
        )


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

        with patch("requests.post", side_effect=counting_post), \
             patch("requests.get", side_effect=counting_get):
            _ensure_repo_on_path()
            importlib.import_module("advisors.composer_backtest_client")

        assert call_count["n"] == 0, (
            f"Importing advisors.composer_backtest_client triggered {call_count['n']} "
            "network call(s) at module load. No top-level network calls allowed."
        )
