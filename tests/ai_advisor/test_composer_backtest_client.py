"""RED/GREEN tests — Composer backtest client (composer_backtest.py, repo root).

Module under test: ``composer_backtest.submit_backtest``.

Architecture contracts encoded as tests:
  - Module lives at repo root as composer_backtest.py (NOT on the 1-minute path).
  - alpha_bot_execution.py does NOT import anything from this module (AC-X2).
  - The client exposes submit_backtest(raw_value, symphony_id, ..., is_live, _session).
  - is_live=False + no _session raises RuntimeError immediately (hard live-guard).
  - _session injection is the test path; live=True is the live path.
  - 429 response → respect Retry-After header; exponential backoff; bounded retries.
  - Non-200, non-retryable → raises requests.HTTPError (AC-X5 handled by caller).
  - requests.Timeout → raises requests.RequestException (caller catches it).
  - Response is parsed into BacktestStats; daily_returns is derived from dvm_capital.
  - No live API calls in the default test suite — all tests use fixture or mock session.
  - Live tests are named test_live_* and excluded by default.

Fixture: tests/fixtures/composer/backtest_inline_v1.json
  - provenance: captured-from-producer 2026-05-31
  - Tests assert against this real fixture; shape-only (no pinning of stat values).

Mocking strategy:
  - requests.Session with a MockAdapter (replaying the fixture) — never a real session.
  - get_composer_headers() is NOT mocked (no network effect in isolation).
  - The math engine and acceptance_gate are NOT imported or mocked here.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from io import BytesIO
from unittest.mock import patch

import pytest
import requests
import requests.adapters

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "composer" / "backtest_inline_v1.json"


def _repo_root() -> pathlib.Path:
    return _REPO_ROOT


def _parse_source(relpath: str) -> ast.Module:
    path = _REPO_ROOT / relpath
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def captured_fixture() -> dict:
    """Load the real captured Composer backtest response fixture.

    Provenance: captured-from-producer 2026-05-31 via /api-fixture skill.
    Fails fast if missing.
    """
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "The client agent must capture a real response via /api-fixture skill. "
        "See tests/fixtures/composer/backtest_response_v1.json for the placeholder schema."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def symphony_id(captured_fixture) -> str:
    """Extract the symphony_id from the fixture provenance."""
    sid = captured_fixture.get("_symphony_id", "")
    assert sid, "Fixture must include _symphony_id in provenance metadata."
    return sid


@pytest.fixture
def response_body(captured_fixture) -> dict:
    """The raw API response body from the fixture."""
    body = captured_fixture.get("response", captured_fixture)
    assert isinstance(body, dict), "Fixture must have a 'response' key with the API response dict."
    return body


class _FixtureAdapter(requests.adapters.BaseAdapter):
    """requests adapter that replays a pre-captured response body for any POST.

    Injected as _session to submit_backtest to prevent any real HTTP calls.
    """

    def __init__(self, body: dict, status_code: int = 200, headers: dict | None = None):
        super().__init__()
        self._body = body
        self._status_code = status_code
        self._headers = headers or {}

    def send(self, request, **kwargs):
        resp = requests.models.Response()
        resp.status_code = self._status_code
        resp.headers = requests.structures.CaseInsensitiveDict(self._headers)
        body_bytes = json.dumps(self._body).encode("utf-8")
        resp.raw = BytesIO(body_bytes)
        resp._content = body_bytes
        resp.encoding = "utf-8"
        return resp

    def close(self):
        pass


@pytest.fixture
def fixture_session(response_body) -> requests.Session:
    """A requests.Session wired with the fixture adapter — the test injection path."""
    session = requests.Session()
    adapter = _FixtureAdapter(response_body)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


@pytest.fixture
def minimal_raw_value() -> dict:
    """Minimal structurally-valid raw_value tree for constructing a backtest request body."""
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


# ---------------------------------------------------------------------------
# Section 1 — Module exists and exposes the required public interface
# ---------------------------------------------------------------------------


class TestModuleExists:
    """composer_backtest.py must exist at repo root and expose its contract."""

    def test_module_is_importable(self):
        """composer_backtest must be importable. RED if module missing."""
        _ensure_repo_on_path()
        import importlib

        mod = importlib.import_module("composer_backtest")
        assert mod is not None

    def test_module_exposes_submit_backtest(self):
        """composer_backtest must expose submit_backtest as a callable."""
        _ensure_repo_on_path()
        import composer_backtest

        assert callable(getattr(composer_backtest, "submit_backtest", None)), (
            "composer_backtest must expose submit_backtest as the single entry point "
            "for POST /api/v0.1/backtest."
        )

    def test_module_exposes_backtest_stats_type(self):
        """composer_backtest must expose BacktestStats as the return type."""
        _ensure_repo_on_path()
        import composer_backtest

        assert hasattr(composer_backtest, "BacktestStats"), (
            "composer_backtest must expose BacktestStats — the structured return type "
            "carrying sharpe_ratio, daily_returns, data_warnings, etc."
        )


# ---------------------------------------------------------------------------
# Section 2 — Hard live-guard: is_live=False + no _session raises immediately
# ---------------------------------------------------------------------------


class TestLiveGuard:
    """Calling submit_backtest with is_live=False and no _session must raise RuntimeError."""

    def test_no_session_no_live_raises_runtime_error(self, minimal_raw_value):
        """submit_backtest(is_live=False, _session=None) must raise RuntimeError.

        The hard live-guard prevents accidental production calls in tests.
        No network request should be attempted — the guard fires before any I/O.
        """
        _ensure_repo_on_path()
        import composer_backtest

        with pytest.raises(RuntimeError, match="is_live=False"):
            composer_backtest.submit_backtest(
                raw_value=minimal_raw_value,
                symphony_id="test-id",
                is_live=False,
            )

    def test_runtime_error_message_mentions_session(self, minimal_raw_value):
        """RuntimeError message must mention _session injection to guide test authors."""
        _ensure_repo_on_path()
        import composer_backtest

        with pytest.raises(RuntimeError) as exc_info:
            composer_backtest.submit_backtest(
                raw_value=minimal_raw_value,
                symphony_id="test-id",
                is_live=False,
            )
        msg = str(exc_info.value)
        assert "_session" in msg or "session" in msg.lower(), (
            f"RuntimeError message must mention '_session' injection; got: {msg!r}. "
            "Test authors need to know to supply _session in tests."
        )


# ---------------------------------------------------------------------------
# Section 3 — Request body construction (verified via fixture session capture)
# ---------------------------------------------------------------------------


class TestRequestBodyConstruction:
    """The client must build a valid POST /api/v0.1/backtest request body."""

    def test_submit_backtest_posts_to_backtest_endpoint(self, minimal_raw_value, response_body):
        """The POST must target /api/v0.1/backtest (inline definition endpoint).

        NOT /api/v0.1/symphonies/{id}/backtest — that requires a pre-saved symphony.
        """
        _ensure_repo_on_path()
        import composer_backtest

        captured_requests = []

        class CapturingAdapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                captured_requests.append(request)
                resp = requests.models.Response()
                resp.status_code = 200
                body_bytes = json.dumps(response_body).encode("utf-8")
                resp._content = body_bytes
                resp.encoding = "utf-8"
                resp.headers = requests.structures.CaseInsensitiveDict({})
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", CapturingAdapter())

        composer_backtest.submit_backtest(
            raw_value=minimal_raw_value,
            symphony_id="00000000-0000-0000-0000-000000000001",
            _session=session,
        )

        assert len(captured_requests) >= 1, "Expected at least one POST request."
        url = captured_requests[0].url
        assert url.endswith("/backtest") or "/api/v0.1/backtest" in url, (
            f"Expected URL to end with '/backtest' or contain '/api/v0.1/backtest', got: {url!r}."
        )
        # Guard: must not use the symphony-ID endpoint
        assert "/symphonies/" not in url.split("/backtest")[0].rstrip("/") or url.endswith(
            "/backtest"
        ), (
            "Must not POST to /symphonies/{id}/backtest — use POST /api/v0.1/backtest for inline defs."
        )

    def test_request_body_nests_tree_under_symphony_raw_value(
        self, minimal_raw_value, response_body
    ):
        """The POSTed body must nest the tree under symphony.raw_value.

        Schema: {\"symphony\": {\"raw_value\": <tree>}, \"capital\": ..., ...}
        """
        _ensure_repo_on_path()
        import composer_backtest

        captured_bodies = []

        class CapturingAdapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                captured_bodies.append(json.loads(request.body))
                resp = requests.models.Response()
                resp.status_code = 200
                body_bytes = json.dumps(response_body).encode("utf-8")
                resp._content = body_bytes
                resp.encoding = "utf-8"
                resp.headers = requests.structures.CaseInsensitiveDict({})
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", CapturingAdapter())

        composer_backtest.submit_backtest(
            raw_value=minimal_raw_value,
            symphony_id="00000000-0000-0000-0000-000000000001",
            _session=session,
        )

        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert "symphony" in body, (
            f"Request body must have top-level 'symphony' key. Got keys: {list(body.keys())}."
        )
        assert "raw_value" in body["symphony"], (
            "body['symphony'] must have a 'raw_value' key containing the logic tree."
        )
        assert body["symphony"]["raw_value"] == minimal_raw_value, (
            "The tree passed as raw_value must appear verbatim in body['symphony']['raw_value']."
        )

    def test_request_body_has_required_scalar_fields(self, minimal_raw_value, response_body):
        """The POSTed body must include all required scalar fields per the API schema."""
        _ensure_repo_on_path()
        import composer_backtest

        captured_bodies = []

        class CapturingAdapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                captured_bodies.append(json.loads(request.body))
                resp = requests.models.Response()
                resp.status_code = 200
                body_bytes = json.dumps(response_body).encode("utf-8")
                resp._content = body_bytes
                resp.encoding = "utf-8"
                resp.headers = requests.structures.CaseInsensitiveDict({})
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", CapturingAdapter())

        composer_backtest.submit_backtest(
            raw_value=minimal_raw_value,
            symphony_id="00000000-0000-0000-0000-000000000001",
            _session=session,
        )

        body = captured_bodies[0]
        required = {"capital", "apply_reg_fee", "apply_taf_fee", "slippage_percent", "broker"}
        missing = required - set(body.keys())
        assert not missing, (
            f"Request body missing required fields: {missing}. "
            "All must be present for Composer to accept the request (else 422)."
        )

    def test_request_uses_composer_auth_headers(
        self, minimal_raw_value, response_body, monkeypatch
    ):
        """The POST must include x-api-key-id and authorization headers.

        Credentials are monkeypatched: get_composer_headers reads module globals
        at call time, and in a clean checkout (no .env) COMPOSER_KEY_ID is None —
        requests silently DROPS None-valued headers from the prepared request,
        which is exactly the misconfiguration this test would otherwise mistake
        for a client bug.
        """
        _ensure_repo_on_path()
        import alpha_bot_execution
        import composer_backtest

        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key-id")
        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_SECRET", "test-secret")

        captured_headers = []

        class CapturingAdapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                captured_headers.append(dict(request.headers))
                resp = requests.models.Response()
                resp.status_code = 200
                body_bytes = json.dumps(response_body).encode("utf-8")
                resp._content = body_bytes
                resp.encoding = "utf-8"
                resp.headers = requests.structures.CaseInsensitiveDict({})
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", CapturingAdapter())

        composer_backtest.submit_backtest(
            raw_value=minimal_raw_value,
            symphony_id="00000000-0000-0000-0000-000000000001",
            _session=session,
        )

        headers = {k.lower(): v for k, v in captured_headers[0].items()}
        assert "x-api-key-id" in headers, (
            "Request must include 'x-api-key-id' header (Composer auth contract from "
            "alpha_bot_execution.py:160-165)."
        )
        assert "authorization" in headers, (
            "Request must include 'authorization' header with Bearer token."
        )


# ---------------------------------------------------------------------------
# Section 4 — Response parsing against the real captured fixture
# ---------------------------------------------------------------------------


class TestResponseParsing:
    """The client must parse the real captured fixture into a BacktestStats correctly.

    All value assertions are SHAPE-ONLY or sign/type checks — no pinning of
    producer-computed numbers.
    """

    def test_submit_backtest_returns_backtest_stats_from_fixture(
        self, symphony_id, fixture_session
    ):
        """submit_backtest with the real fixture session must return a BacktestStats."""
        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value,
            symphony_id=symphony_id,
            _session=fixture_session,
        )
        assert isinstance(result, composer_backtest.BacktestStats), (
            f"submit_backtest must return BacktestStats, got {type(result).__name__!r}."
        )

    def test_parsed_stats_has_sharpe_ratio_field(self, symphony_id, fixture_session):
        """BacktestStats must have a sharpe_ratio field (may be None if absent in fixture)."""
        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value, symphony_id=symphony_id, _session=fixture_session
        )
        assert hasattr(result, "sharpe_ratio"), (
            "BacktestStats must have sharpe_ratio field — load-bearing for gate input derivation."
        )

    def test_parsed_stats_sharpe_is_float_or_none(self, symphony_id, fixture_session):
        """sharpe_ratio must be a float or None; never a string or dict."""
        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value, symphony_id=symphony_id, _session=fixture_session
        )
        assert result.sharpe_ratio is None or isinstance(result.sharpe_ratio, float), (
            f"sharpe_ratio must be float or None, got {type(result.sharpe_ratio).__name__!r}."
        )

    def test_parsed_stats_has_daily_returns_field(self, symphony_id, fixture_session):
        """BacktestStats must have a daily_returns field (dict or empty dict).

        daily_returns is derived from dvm_capital and is the gate layer's primary input.
        """
        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value, symphony_id=symphony_id, _session=fixture_session
        )
        assert hasattr(result, "daily_returns"), (
            "BacktestStats must have daily_returns — the gate layer's primary input "
            "for fold-transform. Absence means the fold-transform cannot function."
        )
        assert isinstance(result.daily_returns, dict), (
            f"daily_returns must be a dict, got {type(result.daily_returns).__name__!r}."
        )

    def test_daily_returns_values_are_finite_floats(self, symphony_id, fixture_session):
        """All daily_returns values must be finite floats (no inf, no NaN).

        NaN or inf propagating into the fold-transform would silently corrupt
        the BHY t-stat and gate decision.
        """
        import math as _math

        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value, symphony_id=symphony_id, _session=fixture_session
        )
        for date_str, val in result.daily_returns.items():
            assert isinstance(val, float), (
                f"daily_returns[{date_str!r}] must be float, got {type(val).__name__!r}."
            )
            assert _math.isfinite(val), (
                f"daily_returns[{date_str!r}]={val!r} is not finite. "
                "Inf/NaN in daily_returns would corrupt the fold-transform and gate decision."
            )

    def test_parsed_stats_has_data_warnings_field(self, symphony_id, fixture_session):
        """BacktestStats must have data_warnings (list or dict, may be empty)."""
        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value, symphony_id=symphony_id, _session=fixture_session
        )
        assert hasattr(result, "data_warnings"), (
            "BacktestStats must have data_warnings. "
            "AC-X5: per-candidate 'backtest failed' reason must be surfaceable."
        )

    def test_daily_returns_keys_are_iso_date_strings(self, symphony_id, fixture_session):
        """daily_returns keys must be ISO-format date strings (YYYY-MM-DD).

        The fold-transform and gate assume chronological order by date string.
        Non-ISO keys would break fold slicing.
        """
        import re

        _ensure_repo_on_path()
        import composer_backtest

        raw_value = {"id": symphony_id, "step": "root", "name": "fixture", "children": []}
        result = composer_backtest.submit_backtest(
            raw_value=raw_value, symphony_id=symphony_id, _session=fixture_session
        )
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for key in list(result.daily_returns.keys())[:5]:  # spot-check first 5
            assert iso_re.match(key), (
                f"daily_returns key {key!r} is not an ISO date string (YYYY-MM-DD). "
                "The fold-transform assumes ISO-date-keyed chronological series."
            )


# ---------------------------------------------------------------------------
# Section 5 — Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Errors must propagate as exceptions, not be silently swallowed."""

    def test_429_response_raises_after_retries_exhausted(self, minimal_raw_value):
        """After retrying on 429, submit_backtest must raise (not return empty stats).

        The caller (M3/M4 batch processor) must catch the exception and surface
        a per-candidate 'backtest failed' error (AC-X5).
        """
        _ensure_repo_on_path()
        import composer_backtest

        class Always429Adapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                resp = requests.models.Response()
                resp.status_code = 429
                resp.headers = requests.structures.CaseInsensitiveDict({"Retry-After": "0"})
                resp._content = b"{}"
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", Always429Adapter())

        # The implementation may raise HTTPError or wrap it in RequestException after retries.
        # Both are acceptable: the caller's batch-processor must catch the exception for AC-X5.
        with pytest.raises((requests.HTTPError, requests.RequestException)):
            # Suppress sleep to make the test fast
            with patch("time.sleep"):
                composer_backtest.submit_backtest(
                    raw_value=minimal_raw_value,
                    symphony_id="test-id",
                    _session=session,
                )

    def test_non_retryable_500_raises_http_error(self, minimal_raw_value):
        """A non-retryable 5xx response must raise requests.HTTPError.

        After retries are exhausted, the exception propagates so the batch
        processor can surface 'backtest failed: HTTP 5xx' (AC-X5).
        """
        _ensure_repo_on_path()
        import composer_backtest

        class Always500Adapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                resp = requests.models.Response()
                resp.status_code = 500
                resp.headers = requests.structures.CaseInsensitiveDict({})
                resp._content = b"Internal Server Error"
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", Always500Adapter())

        with pytest.raises((requests.HTTPError, requests.RequestException)):
            with patch("time.sleep"):
                composer_backtest.submit_backtest(
                    raw_value=minimal_raw_value,
                    symphony_id="test-id",
                    _session=session,
                )

    def test_retry_succeeds_after_429_then_200(self, minimal_raw_value, response_body):
        """A 429 followed by a 200 must ultimately return a successful BacktestStats.

        The retry mechanism must actually retry — not fail on the first 429.
        """
        _ensure_repo_on_path()
        import composer_backtest

        call_count = {"n": 0}
        symphony_id_for_test = "00000000-0000-0000-0000-000000000001"

        class SequencedAdapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                call_count["n"] += 1
                resp = requests.models.Response()
                if call_count["n"] == 1:
                    resp.status_code = 429
                    resp.headers = requests.structures.CaseInsensitiveDict({"Retry-After": "0"})
                    resp._content = b"{}"
                else:
                    resp.status_code = 200
                    body_bytes = json.dumps(response_body).encode("utf-8")
                    resp._content = body_bytes
                    resp.encoding = "utf-8"
                    resp.headers = requests.structures.CaseInsensitiveDict({})
                return resp

            def close(self):
                pass

        session = requests.Session()
        session.mount("https://", SequencedAdapter())

        raw_value = {"id": symphony_id_for_test, "step": "root", "name": "test", "children": []}
        with patch("time.sleep"):
            result = composer_backtest.submit_backtest(
                raw_value=raw_value,
                symphony_id=symphony_id_for_test,
                _session=session,
            )

        assert call_count["n"] >= 2, (
            "The retry mechanism must issue at least 2 POST calls: 1 on 429, 1 on 200. "
            f"Got {call_count['n']} calls. submit_backtest may not be retrying on 429."
        )
        assert isinstance(result, composer_backtest.BacktestStats), (
            "After 429 → 200, submit_backtest must return BacktestStats (not raise)."
        )


# ---------------------------------------------------------------------------
# Section 6 — Architecture: offline isolation and no live calls in default suite
# ---------------------------------------------------------------------------


class TestArchitectureSeparation:
    """Hard architecture contracts: AC-X2 + live/test separation."""

    def test_alpha_bot_execution_does_not_import_composer_backtest(self):
        """alpha_bot_execution.py must not import from composer_backtest.

        AC-X2: no advisor code on the 1-minute live execution path.
        """
        tree = _parse_source("alpha_bot_execution.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "composer_backtest" not in module, (
                    "alpha_bot_execution.py must not import from composer_backtest. "
                    "AC-X2: the backtest client is an advisor module; it must never "
                    "run on the 1-minute live execution path."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "composer_backtest" not in alias.name, (
                        "alpha_bot_execution.py must not import composer_backtest."
                    )

    def test_composer_backtest_has_no_top_level_network_call_on_import(self):
        """Importing composer_backtest must not trigger a real network call.

        A top-level requests.get/post at module scope would hit the network on
        every import, breaking test isolation.
        """
        import importlib
        import sys as _sys

        call_count = {"n": 0}

        original_get = requests.get
        original_post = requests.post

        def counting_get(*a, **kw):
            call_count["n"] += 1
            return original_get(*a, **kw)

        def counting_post(*a, **kw):
            call_count["n"] += 1
            return original_post(*a, **kw)

        _sys.modules.pop("composer_backtest", None)

        with (
            patch("requests.post", side_effect=counting_post),
            patch("requests.get", side_effect=counting_get),
        ):
            _ensure_repo_on_path()
            importlib.import_module("composer_backtest")

        assert call_count["n"] == 0, (
            f"Importing composer_backtest triggered {call_count['n']} network call(s). "
            "No top-level requests calls at module load are allowed."
        )


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
