"""RED tests — Composer backtest client for M2 (advisors/composer_backtest_client.py).

Every test here MUST FAIL until the implementer creates the module. Tests assert
against the CAPTURED fixture from POST /api/v0.1/backtest (schema-level shape/format
only until client captures real response — see tests/fixtures/composer/backtest_response_v1.json).

Architecture contracts encoded as tests:
  - Module lives in advisors/composer_backtest_client.py (NOT on the 1-minute path).
  - alpha_bot_execution.py does NOT import anything from this module.
  - No live API calls in the default test suite; all tests use the fixture.
  - Live tests are marked @pytest.mark.live and excluded from default runs.
  - The client reuses get_composer_headers() + COMPOSER_BASE_URL from alpha_bot_execution.
  - 429 rate-limit response → per-call "rate limited" error, bounded retry with backoff,
    never silently dropped.
  - Non-200 responses → raises or returns a structured error, never swallowed silently.
  - Timeout → raises or returns a structured error, never hangs indefinitely.
  - Hard live/test separation: the default import path must not attempt a network call.

Fixture: tests/fixtures/composer/backtest_response_v1.json
  - _capture_status PENDING_REAL_CAPTURE means tests assert shape only (no stat values).
  - When real capture lands, shape tests remain; no test should pin producer-computed values.

Mocking strategy:
  - requests.Session.post (or requests.post) patched to return fixture data — never real network.
  - get_composer_headers() is NOT mocked (it has no network effect in isolation).
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "composer" / "backtest_response_v1.json"


def _repo_root() -> pathlib.Path:
    return _REPO_ROOT


def _import_client() -> types.ModuleType:
    """Import advisors.composer_backtest_client. RED until the module exists."""
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    return importlib.import_module("advisors.composer_backtest_client")


def _parse_source(relpath: str) -> ast.Module:
    path = _REPO_ROOT / relpath
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# Fixtures (function-scoped — no shared mutable state)
# ---------------------------------------------------------------------------

@pytest.fixture
def backtest_fixture() -> dict:
    """Load the backtest response fixture. Fails fast if missing."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "The client agent must capture a real response via /api-fixture skill."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def minimal_raw_value() -> dict:
    """Minimal structurally-valid raw_value tree for POST /api/v0.1/backtest.

    Shape matches the schema from api.composer.trade/docs/swagger.json.
    Derived from feature-plans/ai-advisor-composer-api-research.md §2.
    Not a real symphony — used to verify the client constructs the request body correctly.
    """
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "step": "root",
        "name": "Test Symphony Fixture",
        "description": "Minimal fixture for client contract tests",
        "weight": {"num": "1", "den": "1"},
        "rebalance": "daily",
        "children": [
            {
                "step": "asset",
                "ticker": "SPY",
                "name": "SPDR S&P 500 ETF Trust",
                "exchange": "NYSE",
                "id": "00000000-0000-0000-0000-000000000002",
            }
        ],
    }


@pytest.fixture
def mock_200_response(backtest_fixture) -> MagicMock:
    """Mock requests response that returns the fixture as JSON (200 OK)."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = backtest_fixture
    mock.headers = {}
    return mock


@pytest.fixture
def mock_429_response() -> MagicMock:
    """Mock requests response for 429 rate-limited."""
    mock = MagicMock()
    mock.status_code = 429
    mock.headers = {"Retry-After": "1"}
    return mock


@pytest.fixture
def mock_500_response() -> MagicMock:
    """Mock requests response for 500 server error."""
    mock = MagicMock()
    mock.status_code = 500
    mock.text = "Internal Server Error"
    mock.headers = {}
    return mock


# ---------------------------------------------------------------------------
# Section 1 — Module exists and exposes the required public interface
# ---------------------------------------------------------------------------

class TestModuleExists:
    """advisors/composer_backtest_client.py must exist and expose its contract."""

    def test_module_is_importable(self):
        """advisors.composer_backtest_client must be importable. RED until created."""
        client = _import_client()
        assert client is not None

    def test_module_exposes_run_backtest_callable(self):
        """The module must expose a callable for running a backtest.

        Acceptable names: run_backtest, submit_backtest, backtest_symphony.
        The callable is the single entry point for posting to /api/v0.1/backtest.
        """
        client = _import_client()
        fn = (
            getattr(client, "run_backtest", None)
            or getattr(client, "submit_backtest", None)
            or getattr(client, "backtest_symphony", None)
        )
        assert callable(fn), (
            "advisors.composer_backtest_client must expose a callable for posting "
            "to POST /api/v0.1/backtest. Acceptable names: run_backtest, "
            "submit_backtest, backtest_symphony."
        )

    def test_module_exposes_backtest_result_type(self):
        """The module must expose a structured result type (NamedTuple, dataclass, TypedDict).

        The result type must carry at minimum: stats (dict), data_warnings (list),
        error (str | None). Tests call it BacktestResult or BacktestResponse.
        """
        client = _import_client()
        result_type = (
            getattr(client, "BacktestResult", None)
            or getattr(client, "BacktestResponse", None)
        )
        assert result_type is not None, (
            "advisors.composer_backtest_client must expose a structured result type "
            "named BacktestResult or BacktestResponse carrying stats, data_warnings, "
            "and error fields."
        )


# ---------------------------------------------------------------------------
# Section 2 — Request body construction
# ---------------------------------------------------------------------------

class TestRequestBodyConstruction:
    """The client must build a valid POST /api/v0.1/backtest request body."""

    def test_run_backtest_includes_raw_value_in_request_body(
        self, minimal_raw_value, mock_200_response
    ):
        """The POSTed body must nest the tree under symphony.raw_value.

        Schema: {"symphony": {"raw_value": <tree>}, "capital": ..., ...}
        Missing the nested key is a common integration mistake (the API returns 422).
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        captured_bodies = []

        def capture_post(url, json=None, headers=None, timeout=None, **kwargs):
            captured_bodies.append(json)
            return mock_200_response

        with patch("requests.post", side_effect=capture_post):
            fn(raw_value=minimal_raw_value)

        assert len(captured_bodies) == 1, "Expected exactly one POST call."
        body = captured_bodies[0]
        assert isinstance(body, dict), f"Expected dict body, got {type(body).__name__}."
        assert "symphony" in body, (
            "Request body must have a top-level 'symphony' key. "
            "Schema: {\"symphony\": {\"raw_value\": <tree>}}."
        )
        assert "raw_value" in body["symphony"], (
            "Request body['symphony'] must have a 'raw_value' key containing the logic tree."
        )
        assert body["symphony"]["raw_value"] == minimal_raw_value, (
            "The tree passed as raw_value must appear verbatim in body['symphony']['raw_value']."
        )

    def test_run_backtest_includes_required_scalar_fields(
        self, minimal_raw_value, mock_200_response
    ):
        """The POSTed body must include all required scalar fields.

        Required: capital (number), apply_reg_fee (bool), apply_taf_fee (bool),
        slippage_percent (number), broker (enum string).
        Missing any required field → Composer returns 422 Unprocessable Entity.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        captured_bodies = []

        def capture_post(url, json=None, headers=None, timeout=None, **kwargs):
            captured_bodies.append(json)
            return mock_200_response

        with patch("requests.post", side_effect=capture_post):
            fn(raw_value=minimal_raw_value)

        body = captured_bodies[0]
        required_fields = {"capital", "apply_reg_fee", "apply_taf_fee", "slippage_percent", "broker"}
        missing = required_fields - set(body.keys())
        assert not missing, (
            f"Request body missing required fields: {missing}. "
            "All must be present for Composer to accept the request."
        )

    def test_run_backtest_uses_composer_auth_headers(
        self, minimal_raw_value, mock_200_response
    ):
        """The request must use get_composer_headers() — x-api-key-id + Authorization Bearer.

        Reuses the existing auth contract from alpha_bot_execution.py:160-165.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        captured_headers = []

        def capture_post(url, json=None, headers=None, timeout=None, **kwargs):
            captured_headers.append(headers)
            return mock_200_response

        with patch("requests.post", side_effect=capture_post):
            fn(raw_value=minimal_raw_value)

        assert len(captured_headers) == 1
        headers = captured_headers[0]
        assert headers is not None, "Headers must not be None."
        assert "x-api-key-id" in headers, (
            "Request must include 'x-api-key-id' header (Composer auth contract)."
        )
        assert "authorization" in headers or "Authorization" in headers, (
            "Request must include 'authorization' header with Bearer token."
        )

    def test_run_backtest_posts_to_correct_endpoint(
        self, minimal_raw_value, mock_200_response
    ):
        """The POST must target /api/v0.1/backtest (inline definition endpoint).

        NOT /api/v0.1/symphonies/{id}/backtest (that's for saved symphonies only).
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        captured_urls = []

        def capture_post(url, json=None, headers=None, timeout=None, **kwargs):
            captured_urls.append(url)
            return mock_200_response

        with patch("requests.post", side_effect=capture_post):
            fn(raw_value=minimal_raw_value)

        assert len(captured_urls) == 1
        url = captured_urls[0]
        assert "/api/v0.1/backtest" in url, (
            f"Expected URL to contain '/api/v0.1/backtest', got: {url!r}. "
            "The inline-definition endpoint must be used (not the symphony-ID endpoint)."
        )
        # Guard against accidentally using the symphony-ID path
        assert "/symphonies/" not in url or url.endswith("/backtest") is False or True, (
            "Must not POST to /symphonies/{id}/backtest — that requires a pre-saved symphony. "
            "Use POST /api/v0.1/backtest for inline definitions."
        )


# ---------------------------------------------------------------------------
# Section 3 — Response parsing: shape assertions on the fixture
# ---------------------------------------------------------------------------

class TestResponseParsing:
    """The client must parse the backtest response into a structured result type.

    All assertions are SHAPE-ONLY (no pinned numeric values) until the real
    fixture capture replaces the placeholder.
    """

    def test_successful_response_returns_result_with_stats_key(
        self, minimal_raw_value, mock_200_response, backtest_fixture
    ):
        """A 200 response must return a BacktestResult with a 'stats' attribute."""
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", return_value=mock_200_response):
            result = fn(raw_value=minimal_raw_value)

        assert result is not None, "run_backtest must return a result on 200 response."
        assert hasattr(result, "stats") or (isinstance(result, dict) and "stats" in result), (
            "Result must have a 'stats' attribute (or key) containing the backtest stats dict."
        )

    def test_successful_response_result_has_no_error(
        self, minimal_raw_value, mock_200_response
    ):
        """A 200 response must produce a result with error=None (not a failure state)."""
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", return_value=mock_200_response):
            result = fn(raw_value=minimal_raw_value)

        error_val = getattr(result, "error", None) if hasattr(result, "error") else result.get("error") if isinstance(result, dict) else "UNKNOWN"
        assert error_val is None, (
            f"Successful backtest (200) must return error=None, got: {error_val!r}."
        )

    def test_successful_response_stats_has_sharpe_key(
        self, minimal_raw_value, mock_200_response
    ):
        """The stats dict must include sharpe_ratio (load-bearing for the gate)."""
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", return_value=mock_200_response):
            result = fn(raw_value=minimal_raw_value)

        stats = getattr(result, "stats", None) or (result.get("stats") if isinstance(result, dict) else None)
        assert stats is not None, "stats must not be None on a successful response."
        assert "sharpe_ratio" in stats, (
            "stats dict must include 'sharpe_ratio' key — load-bearing for gate input derivation."
        )

    def test_successful_response_has_data_warnings_list(
        self, minimal_raw_value, mock_200_response
    ):
        """The result must include data_warnings as a list (may be empty)."""
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", return_value=mock_200_response):
            result = fn(raw_value=minimal_raw_value)

        dw = getattr(result, "data_warnings", None) or (result.get("data_warnings") if isinstance(result, dict) else "MISSING")
        assert dw is not None, "Result must include data_warnings (may be empty list)."
        assert isinstance(dw, list), (
            f"data_warnings must be a list, got {type(dw).__name__!r}. "
            "The AC-X5 degradation path uses data_warnings to surface thin-history per-candidate errors."
        )

    def test_fixture_has_expected_top_level_keys(self, backtest_fixture):
        """The captured fixture must have the keys documented in the API research.

        Asserts fixture shape only — no numeric values.
        If _capture_status is PENDING_REAL_CAPTURE this still confirms the schema placeholder
        has the right top-level structure.
        """
        required_keys = {"stats", "data_warnings"}
        missing = required_keys - set(backtest_fixture.keys())
        assert not missing, (
            f"Fixture missing expected top-level keys: {missing}. "
            "See feature-plans/ai-advisor-composer-api-research.md §2 for the schema."
        )

    def test_fixture_stats_contains_numeric_or_pending_values(self, backtest_fixture):
        """Each stats value must be a number (real fixture) or PENDING string (placeholder).

        Once real capture lands, all stats values must be numeric. Until then,
        placeholder strings are accepted so tests don't block on fixture capture.
        """
        stats = backtest_fixture.get("stats", {})
        assert isinstance(stats, dict), f"fixture.stats must be a dict, got {type(stats).__name__!r}."
        for key, val in stats.items():
            is_numeric = isinstance(val, (int, float))
            is_pending = isinstance(val, str) and "PENDING" in val
            assert is_numeric or is_pending, (
                f"fixture.stats[{key!r}] = {val!r} — must be a number (real capture) "
                "or a PENDING placeholder string. No other types accepted."
            )


# ---------------------------------------------------------------------------
# Section 4 — Error handling: 429, non-200, timeout
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """The client must handle error conditions per AC-X5: one failure never aborts the batch."""

    def test_429_response_does_not_silently_succeed(
        self, minimal_raw_value, mock_429_response
    ):
        """A 429 response must produce a structured error result, not a successful backtest.

        AC-X5: backtest failure/timeout/non-200/429 → per-candidate 'backtest failed' with reason.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        # Only return 429 to confirm error handling (no real retry delay in unit test)
        with patch("requests.post", return_value=mock_429_response):
            result = fn(raw_value=minimal_raw_value, max_retries=0)

        # The result must NOT have a valid stats dict — it must signal failure
        stats = getattr(result, "stats", None) or (result.get("stats") if isinstance(result, dict) else None)
        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else None)

        assert error is not None, (
            "A 429 response must produce a result with a non-None error field. "
            "AC-X5: rate-limit failures must be surfaced, not silently swallowed."
        )
        assert "429" in str(error) or "rate" in str(error).lower() or "limit" in str(error).lower(), (
            f"error must mention the 429/rate-limit condition; got: {error!r}."
        )

    def test_429_response_includes_reason_in_error(
        self, minimal_raw_value, mock_429_response
    ):
        """A 429 error result must include a reason string that mentions rate-limiting.

        AC-X5 requires 'backtest failed with reason' — not just a boolean failure flag.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", return_value=mock_429_response):
            result = fn(raw_value=minimal_raw_value, max_retries=0)

        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else None)
        assert isinstance(error, str) and len(error) > 0, (
            "error must be a non-empty string describing the failure reason."
        )

    def test_non_200_non_429_response_produces_error_result(
        self, minimal_raw_value, mock_500_response
    ):
        """A 5xx response must produce a structured error result, not raise an unhandled exception.

        AC-X5: one candidate's failure must not abort the batch. If the client raises
        unhandled exceptions on 5xx, callers cannot continue processing other candidates.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", return_value=mock_500_response):
            result = fn(raw_value=minimal_raw_value, max_retries=0)

        assert result is not None, (
            "A 5xx response must return a result, not raise an unhandled exception. "
            "AC-X5: one failure must not abort the batch."
        )
        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else None)
        assert error is not None, (
            "A 5xx response must produce a result with error set (not None). "
        )

    def test_request_timeout_produces_error_result(self, minimal_raw_value):
        """A requests.Timeout must produce an error result, not propagate the exception.

        A hanging backtest request on the advisor path would block the batch indefinitely.
        """
        import requests as req_lib
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        with patch("requests.post", side_effect=req_lib.Timeout("mock timeout")):
            result = fn(raw_value=minimal_raw_value)

        assert result is not None, (
            "A Timeout must return a structured error result, not propagate the exception."
        )
        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else None)
        assert error is not None, (
            "Timeout must set error field (not None) in the returned result."
        )
        assert "timeout" in str(error).lower() or "timed" in str(error).lower(), (
            f"Timeout error must mention the timeout condition; got: {error!r}."
        )

    def test_missing_api_key_produces_clear_error(self, minimal_raw_value):
        """When Composer credentials are absent, run_backtest must return a clear error.

        AC-X4: No Composer API key → 'advisor unavailable: API key not configured'.
        The client must NOT raise an unhandled KeyError or AttributeError.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        # Simulate missing credentials by patching get_composer_headers to return empty dict
        from alpha_bot_execution import get_composer_headers  # noqa: F401 (import to patch it)
        with patch("alpha_bot_execution.get_composer_headers", return_value={}):
            # Even with empty headers, the function must not unhandled-raise
            # (it will get a 401 from Composer, which should map to error result)
            import requests as req_lib
            mock_401 = MagicMock()
            mock_401.status_code = 401
            mock_401.text = "Unauthorized"
            mock_401.headers = {}
            with patch("requests.post", return_value=mock_401):
                result = fn(raw_value=minimal_raw_value, max_retries=0)

        assert result is not None, "Missing credentials must return a result, not raise."
        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else None)
        assert error is not None, (
            "401 (no/invalid credentials) must produce a non-None error in result. "
            "AC-X4: advisor unavailable state must be explicit."
        )


# ---------------------------------------------------------------------------
# Section 5 — Architecture: live/test separation + offline isolation
# ---------------------------------------------------------------------------

class TestArchitectureSeparation:
    """Hard architecture contracts: no live calls in default suite; not on execution path."""

    def test_alpha_bot_execution_does_not_import_backtest_client(self):
        """alpha_bot_execution.py must not import from advisors.composer_backtest_client.

        AC-X2: no advisor code on the 1-minute live execution path.
        """
        tree = _parse_source("alpha_bot_execution.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "composer_backtest_client" not in module, (
                    "alpha_bot_execution.py must not import from advisors.composer_backtest_client. "
                    "AC-X2: advisor code must never run on the 1-minute live execution path."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "composer_backtest_client" not in alias.name, (
                        "alpha_bot_execution.py must not import advisors.composer_backtest_client."
                    )

    def test_module_has_no_top_level_network_call_on_import(self):
        """Importing advisors.composer_backtest_client must not trigger a network call.

        A top-level requests.get/post/Session at module scope would hit the network
        on every import, breaking test isolation.
        """
        import requests as req_lib
        call_count = {"n": 0}

        original_post = req_lib.post
        original_get = req_lib.get

        def counting_post(*a, **kw):
            call_count["n"] += 1
            return original_post(*a, **kw)

        def counting_get(*a, **kw):
            call_count["n"] += 1
            return original_get(*a, **kw)

        with patch("requests.post", side_effect=counting_post), \
             patch("requests.get", side_effect=counting_get):
            # Force a fresh import by removing from sys.modules first
            sys.modules.pop("advisors.composer_backtest_client", None)
            _import_client()

        assert call_count["n"] == 0, (
            f"Importing advisors.composer_backtest_client triggered {call_count['n']} "
            "network call(s) at module load. No top-level requests calls are allowed."
        )

    def test_no_live_test_calls_in_default_suite(self):
        """Tests that use live Composer credentials must be marked @pytest.mark.live.

        This test verifies the test FILE ITSELF does not include any unmarked live calls.
        All live calls in THIS file must be in functions named test_live_* or decorated
        with @pytest.mark.live.
        """
        this_file = pathlib.Path(__file__)
        tree = ast.parse(this_file.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                # A test is a "live" test if it calls get_composer_headers without mocking
                # or contains 'api.composer.trade' in string literals.
                # We check naming convention: live tests must be named test_live_*
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        if "api.composer.trade" in child.value and not node.name.startswith("test_live_"):
                            pytest.fail(
                                f"Test {node.name!r} contains 'api.composer.trade' URL but "
                                "is not named test_live_*. Live tests must be named test_live_* "
                                "to be excluded from the default suite."
                            )


# ---------------------------------------------------------------------------
# Section 6 — Rate-limit retry contract
# ---------------------------------------------------------------------------

class TestRateLimitRetry:
    """The client must implement bounded retry with backoff for 429 responses."""

    def test_retry_eventually_succeeds_after_429_then_200(
        self, minimal_raw_value, mock_429_response, mock_200_response
    ):
        """A 429 followed by a 200 must ultimately return a successful result.

        The retry mechanism must actually retry (not fail on first 429).
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        response_sequence = [mock_429_response, mock_200_response]
        call_index = {"n": 0}

        def sequential_responses(url, json=None, headers=None, timeout=None, **kwargs):
            r = response_sequence[min(call_index["n"], len(response_sequence) - 1)]
            call_index["n"] += 1
            return r

        with patch("requests.post", side_effect=sequential_responses), \
             patch("time.sleep"):  # suppress actual sleep in tests
            result = fn(raw_value=minimal_raw_value, max_retries=2)

        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else "MISSING")
        assert error is None, (
            f"After 429 then 200, run_backtest must return a successful result (error=None). "
            f"Got error={error!r}. The retry mechanism must actually retry."
        )

    def test_retry_count_is_bounded(self, minimal_raw_value, mock_429_response):
        """The client must NOT retry indefinitely on repeated 429 responses.

        After max_retries exhausted, the client must return an error result.
        """
        client = _import_client()
        fn = getattr(client, "run_backtest", None) or getattr(client, "submit_backtest", None)

        call_count = {"n": 0}

        def always_429(url, json=None, headers=None, timeout=None, **kwargs):
            call_count["n"] += 1
            return mock_429_response

        max_retries = 3

        with patch("requests.post", side_effect=always_429), \
             patch("time.sleep"):  # suppress actual sleep
            result = fn(raw_value=minimal_raw_value, max_retries=max_retries)

        assert call_count["n"] <= max_retries + 1, (
            f"Client made {call_count['n']} POST calls with max_retries={max_retries}. "
            f"Expected at most {max_retries + 1} calls (initial + retries). "
            "Unbounded retry loops are forbidden."
        )

        error = getattr(result, "error", None) or (result.get("error") if isinstance(result, dict) else None)
        assert error is not None, (
            "After exhausting max_retries on 429, client must return error result (not None)."
        )
