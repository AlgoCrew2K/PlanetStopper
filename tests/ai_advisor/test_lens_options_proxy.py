"""RED/GREEN tests — derivatives/volatility lens proxy (advisors/lens_options_proxy.py).

Module under test: ``advisors.lens_options_proxy._fetch_options_proxy``

Architecture contracts encoded as tests
----------------------------------------
AC-1  Module lives at advisors/lens_options_proxy.py; NOT imported by
      alpha_bot_execution.py (off-execution-path / CC-2 boundary).
AC-2  Return dict shape: mandatory keys ``available``, ``source``.
      When available=True: also has ``vix_level`` (float), ``vix_term_structure``
      (dict with keys ``spot``, ``term_3m``, ``ratio``, ``spread``, ``regime``),
      ``risk_read`` (str, one of a defined vocabulary), and ``as_of_date`` (str).
      When available=False: also has ``reason`` (type(exc).__name__ or short label).
AC-3  Honest availability — available=False + reason when FRED is down or returns
      no usable observations. NEVER fabricates values (CC-3 / D-1).
AC-4  Regime labels: vix_term_structure["regime"] is one of
      {``contango``, ``backwardation``, ``flat``}.
      contango = spot VIX < 3m VIX (calm/low-stress).
      backwardation = spot VIX > 3m VIX (elevated stress).
      flat = within threshold.
AC-5  risk_read is one of {``risk-off``, ``risk-on``, ``neutral``}.
      backwardation + elevated-VIX → risk-off.
      contango + low-VIX → risk-on.
      Otherwise neutral.
AC-6  Retry policy: bounded retries with exponential backoff.
      MAX_OPTIONS_RETRY_WAIT_SECONDS is a named constant in the module.
      No unbounded loops.
AC-7  Explicit HTTP timeout: _OPTIONS_PROXY_TIMEOUT_S is a named constant.
      All requests.get calls carry this timeout.
AC-8  D-1: reason on unavailability is type(exc).__name__ only — never str(exc).
AC-9  No hardcoded VIX/VXVCLS literal values asserted in tests — shape/regime
      logic correctness is verified on fixture values using computed comparisons.
AC-10 The module reads FRED_API_KEY from os.environ — never hardcodes it.
AC-11 Observation parsing: skip rows where value == "." (FRED not-available marker).
      Use the latest non-"." observation as the current value.
AC-12 put_call is omitted entirely when no genuinely free FRED put/call series
      is available (honest omission, no fabrication).

Fixture provenance: schema-derived from FRED REST API v1 docs (2026-06-13).
See tests/fixtures/fred/vixcls_response_v1.json and vxvcls_response_v1.json.

Mocking strategy:
  - requests.get is patched at the call site to return fixture data.
  - No live FRED calls in this suite.
  - os.environ["FRED_API_KEY"] is patched to a test sentinel.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_VIXCLS = _REPO_ROOT / "tests" / "fixtures" / "fred" / "vixcls_response_v1.json"
_FIXTURE_VXVCLS = _REPO_ROOT / "tests" / "fixtures" / "fred" / "vxvcls_response_v1.json"


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_module() -> types.ModuleType:
    """Import advisors.lens_options_proxy fresh each call (avoids cached state)."""
    _ensure_repo_on_path()
    mod_name = "advisors.lens_options_proxy"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vixcls_fixture() -> dict:
    """Load the schema-derived FRED VIXCLS fixture."""
    assert _FIXTURE_VIXCLS.exists(), (
        f"Fixture not found: {_FIXTURE_VIXCLS}. "
        "Schema-derived from FRED REST API v1 docs."
    )
    return json.loads(_FIXTURE_VIXCLS.read_text())


@pytest.fixture(scope="module")
def vxvcls_fixture() -> dict:
    """Load the schema-derived FRED VXVCLS fixture."""
    assert _FIXTURE_VXVCLS.exists(), (
        f"Fixture not found: {_FIXTURE_VXVCLS}. "
        "Schema-derived from FRED REST API v1 docs."
    )
    return json.loads(_FIXTURE_VXVCLS.read_text())


def _make_mock_response(body: dict, status_code: int = 200) -> MagicMock:
    """Build a minimal mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = body
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    return mock


# ---------------------------------------------------------------------------
# AC-1: Module location + off-execution-path guard
# ---------------------------------------------------------------------------


class TestModuleLocation:
    def test_module_importable_at_expected_path(self):
        """advisors/lens_options_proxy.py must exist and be importable."""
        mod = _import_module()
        assert mod is not None

    def test_module_file_path(self):
        """Module file must be at advisors/lens_options_proxy.py."""
        mod = _import_module()
        mod_path = pathlib.Path(mod.__file__)
        assert mod_path.name == "lens_options_proxy.py"
        assert mod_path.parent.name == "advisors"

    def test_not_imported_by_alpha_bot_execution(self):
        """alpha_bot_execution.py must not import lens_options_proxy (CC-2 / AC-1)."""
        exec_path = _REPO_ROOT / "alpha_bot_execution.py"
        assert exec_path.exists(), "alpha_bot_execution.py must exist"
        source = exec_path.read_text(encoding="utf-8")
        assert "lens_options_proxy" not in source, (
            "lens_options_proxy must NOT be imported by alpha_bot_execution.py — "
            "it is off-execution-path (CC-2 boundary)."
        )


# ---------------------------------------------------------------------------
# AC-2: Return dict shape
# ---------------------------------------------------------------------------


class TestReturnShape:
    def test_available_true_has_required_keys(self, vixcls_fixture, vxvcls_fixture):
        """available=True result must carry all required keys."""
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()

        assert isinstance(result, dict)
        assert result["available"] is True
        assert "vix_level" in result
        assert "vix_term_structure" in result
        assert "risk_read" in result
        assert "as_of_date" in result
        assert "source" in result

    def test_vix_term_structure_has_required_subkeys(self, vixcls_fixture, vxvcls_fixture):
        """vix_term_structure must have: spot, term_3m, ratio, spread, regime."""
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()

        ts = result["vix_term_structure"]
        assert isinstance(ts, dict)
        for key in ("spot", "term_3m", "ratio", "spread", "regime"):
            assert key in ts, f"vix_term_structure missing key: {key!r}"

    def test_available_false_has_reason_key(self):
        """available=False result must carry 'reason' key."""
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                import requests
                mock_get.side_effect = requests.Timeout("simulated timeout")
                result = mod._fetch_options_proxy()

        assert result["available"] is False
        assert "reason" in result
        assert "source" in result

    def test_no_put_call_key_fabricated(self, vixcls_fixture, vxvcls_fixture):
        """put_call must be omitted — no free genuine FRED put/call series (AC-12)."""
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()

        assert "put_call" not in result, (
            "put_call must be omitted when no genuinely free FRED series exists (AC-12). "
            "Never fabricate values."
        )


# ---------------------------------------------------------------------------
# AC-4: Regime labels
# ---------------------------------------------------------------------------


class TestRegimeLabels:
    def test_regime_vocabulary_exhaustive(self, vixcls_fixture, vxvcls_fixture):
        """regime must be one of the defined vocabulary."""
        mod = _import_module()
        valid_regimes = {"contango", "backwardation", "flat"}
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()
        regime = result["vix_term_structure"]["regime"]
        assert regime in valid_regimes, f"regime {regime!r} not in {valid_regimes}"

    def test_backwardation_when_spot_greater_than_term(self):
        """spot VIX > 3m VIX → backwardation (stress regime)."""
        mod = _import_module()
        # Build fixture where spot > term (backwardation)
        spot_val = 25.0
        term_val = 20.0
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", str(spot_val))])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", str(term_val))])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["vix_term_structure"]["regime"] == "backwardation"

    def test_contango_when_spot_less_than_term(self):
        """spot VIX < 3m VIX → contango (calm/low-stress regime)."""
        mod = _import_module()
        spot_val = 14.0
        term_val = 19.0
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", str(spot_val))])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", str(term_val))])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["vix_term_structure"]["regime"] == "contango"

    def test_ratio_correct_from_fixture_values(self):
        """ratio = spot / term_3m; must be computed, not hardcoded."""
        mod = _import_module()
        spot_val = 20.0
        term_val = 25.0
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", str(spot_val))])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", str(term_val))])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        ts = result["vix_term_structure"]
        expected_ratio = spot_val / term_val
        assert abs(ts["ratio"] - expected_ratio) < 1e-9, (
            f"ratio {ts['ratio']!r} != expected {expected_ratio!r}"
        )

    def test_spread_correct_from_fixture_values(self):
        """spread = spot - term_3m; must be computed, not hardcoded."""
        mod = _import_module()
        spot_val = 22.5
        term_val = 18.0
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", str(spot_val))])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", str(term_val))])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        ts = result["vix_term_structure"]
        expected_spread = spot_val - term_val
        assert abs(ts["spread"] - expected_spread) < 1e-9


# ---------------------------------------------------------------------------
# AC-5: risk_read vocabulary + derivation logic
# ---------------------------------------------------------------------------


class TestRiskRead:
    def test_risk_read_vocabulary_exhaustive(self, vixcls_fixture, vxvcls_fixture):
        """risk_read must be one of the three valid values."""
        mod = _import_module()
        valid = {"risk-off", "risk-on", "neutral"}
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()
        assert result["risk_read"] in valid

    def test_backwardation_elevated_vix_gives_risk_off(self):
        """backwardation + elevated VIX → risk-off."""
        mod = _import_module()
        # VIX elevated (>20) + spot > term = backwardation
        spot_val = 28.0
        term_val = 22.0
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", str(spot_val))])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", str(term_val))])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["risk_read"] == "risk-off"

    def test_contango_low_vix_gives_risk_on(self):
        """contango + low VIX → risk-on/complacent."""
        mod = _import_module()
        # VIX low (<15) + spot < term = contango
        spot_val = 13.0
        term_val = 17.5
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", str(spot_val))])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", str(term_val))])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["risk_read"] == "risk-on"


# ---------------------------------------------------------------------------
# AC-3 + AC-8: Honest availability and D-1
# ---------------------------------------------------------------------------


class TestHonestAvailability:
    def test_unavailable_on_timeout(self):
        """requests.Timeout → available=False, reason='Timeout'."""
        mod = _import_module()
        import requests
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get", side_effect=requests.Timeout()):
                result = mod._fetch_options_proxy()
        assert result["available"] is False
        assert result["reason"] == "Timeout"

    def test_unavailable_on_connection_error(self):
        """requests.ConnectionError → available=False, reason='ConnectionError'."""
        mod = _import_module()
        import requests
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get", side_effect=requests.ConnectionError()):
                result = mod._fetch_options_proxy()
        assert result["available"] is False
        assert result["reason"] == "ConnectionError"

    def test_reason_is_type_name_only_not_message(self):
        """D-1: reason must be type(exc).__name__, never the exception message string."""
        mod = _import_module()
        import requests
        long_msg = "Connection refused to host api.stlouisfed.org with secret_key=abc123"
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get", side_effect=requests.ConnectionError(long_msg)):
                result = mod._fetch_options_proxy()
        assert result["available"] is False
        # D-1: type name only, none of the exception message should appear
        assert "secret_key" not in result["reason"]
        assert "api.stlouisfed.org" not in result["reason"]
        assert result["reason"] == "ConnectionError"

    def test_unavailable_when_no_valid_observations(self):
        """All observations are '.' (not-available) → available=False."""
        mod = _import_module()
        vix_body = _make_fred_body("VIXCLS", [("2026-06-12", "."), ("2026-06-11", ".")])
        vxv_body = _make_fred_body("VXVCLS", [("2026-06-12", "."), ("2026-06-11", ".")])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["available"] is False

    def test_unavailable_when_observations_list_empty(self):
        """Empty observations list → available=False."""
        mod = _import_module()
        vix_body = _make_fred_body("VIXCLS", [])
        vxv_body = _make_fred_body("VXVCLS", [])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["available"] is False

    def test_unavailable_when_no_fred_api_key(self):
        """Missing FRED_API_KEY → available=False with informative reason."""
        mod = _import_module()
        env_without_key = {k: v for k, v in __import__("os").environ.items()
                           if k != "FRED_API_KEY"}
        with patch.dict("os.environ", env_without_key, clear=True):
            with patch("requests.get") as mock_get:
                result = mod._fetch_options_proxy()
        # Must not make any network calls without a key
        mock_get.assert_not_called()
        assert result["available"] is False
        assert "reason" in result


# ---------------------------------------------------------------------------
# AC-6: Bounded retry policy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_max_retry_wait_constant_exists(self):
        """MAX_OPTIONS_RETRY_WAIT_SECONDS must be a named constant in the module."""
        mod = _import_module()
        assert hasattr(mod, "MAX_OPTIONS_RETRY_WAIT_SECONDS"), (
            "MAX_OPTIONS_RETRY_WAIT_SECONDS must be a named constant — "
            "Rule 4: state the maximum total wait as a named constant."
        )
        assert isinstance(mod.MAX_OPTIONS_RETRY_WAIT_SECONDS, (int, float))
        assert mod.MAX_OPTIONS_RETRY_WAIT_SECONDS > 0

    def test_max_retry_constant_is_finite(self):
        """MAX_OPTIONS_RETRY_WAIT_SECONDS must be finite (no unbounded loops)."""
        import math
        mod = _import_module()
        assert math.isfinite(mod.MAX_OPTIONS_RETRY_WAIT_SECONDS)

    def test_retry_attempts_bounded(self):
        """On transient 500 errors, retries are bounded (not infinite)."""
        mod = _import_module()
        import requests

        call_count = 0

        def always_500(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            mock.status_code = 500
            mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
            return mock

        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get", side_effect=always_500):
                with patch("time.sleep"):  # don't actually sleep in tests
                    result = mod._fetch_options_proxy()

        # Must degrade gracefully — never infinite loop
        assert result["available"] is False
        # Upper bound: fewer than 20 total attempts (bounded retry)
        assert call_count < 20, (
            f"call_count={call_count} suggests unbounded retry. "
            "Retry must be bounded."
        )


# ---------------------------------------------------------------------------
# AC-7: Explicit HTTP timeout
# ---------------------------------------------------------------------------


class TestExplicitTimeout:
    def test_timeout_constant_exists(self):
        """_OPTIONS_PROXY_TIMEOUT_S must be a named constant in the module."""
        mod = _import_module()
        assert hasattr(mod, "_OPTIONS_PROXY_TIMEOUT_S"), (
            "_OPTIONS_PROXY_TIMEOUT_S must be a named constant. "
            "Every HTTP request must have an explicit timeout."
        )
        assert isinstance(mod._OPTIONS_PROXY_TIMEOUT_S, (int, float))
        assert mod._OPTIONS_PROXY_TIMEOUT_S > 0

    def test_requests_get_called_with_timeout(self, vixcls_fixture, vxvcls_fixture):
        """requests.get must be called with an explicit timeout argument."""
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                mod._fetch_options_proxy()

        for c in mock_get.call_args_list:
            kwargs = c.kwargs if hasattr(c, "kwargs") else c[1]
            args = c.args if hasattr(c, "args") else c[0]
            # timeout can be positional or keyword
            has_timeout = "timeout" in kwargs or (len(args) >= 2)
            assert has_timeout, (
                f"requests.get call missing timeout: {c}. "
                "All HTTP requests must have an explicit timeout."
            )


# ---------------------------------------------------------------------------
# AC-10: FRED_API_KEY from os.environ
# ---------------------------------------------------------------------------


class TestApiKeyFromEnv:
    def test_api_key_not_hardcoded_in_source(self):
        """FRED_API_KEY must not appear as a literal string in the module source."""
        mod = _import_module()
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        # The real key from .env should never be in source
        # Check there's no 32-hex-char string that looks like a FRED key literal
        import re
        # FRED keys are 32-char hex strings; look for them as string literals
        pattern = re.compile(r'["\'][0-9a-f]{32}["\']')
        matches = pattern.findall(source)
        assert not matches, (
            f"Potential hardcoded API key found in source: {matches}. "
            "FRED_API_KEY must be read from os.environ."
        )

    def test_url_contains_api_key_from_env(self, vixcls_fixture, vxvcls_fixture):
        """The FRED request URL must include the API key read from os.environ."""
        mod = _import_module()
        sentinel = "FRED_TEST_KEY_XYZ999"
        with patch.dict("os.environ", {"FRED_API_KEY": sentinel}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                mod._fetch_options_proxy()

        # At least one call should carry the sentinel key in URL or params
        found = False
        for c in mock_get.call_args_list:
            args = c.args if hasattr(c, "args") else c[0]
            kwargs = c.kwargs if hasattr(c, "kwargs") else c[1]
            url = args[0] if args else ""
            params = kwargs.get("params", {}) or {}
            if sentinel in url or sentinel in str(params):
                found = True
                break
        assert found, (
            "FRED_API_KEY sentinel not found in any requests.get call. "
            "The module must read the key from os.environ and pass it to FRED."
        )


# ---------------------------------------------------------------------------
# AC-11: Observation parsing — skip "." values
# ---------------------------------------------------------------------------


class TestObservationParsing:
    def test_latest_non_dot_observation_used(self):
        """Latest observation that is not '.' must be used as the current value."""
        mod = _import_module()
        # Most recent is "." — should fall back to the previous valid value
        spot_val = 18.75
        vix_body = _make_fred_body("VIXCLS", [
            ("2026-06-10", str(spot_val)),
            ("2026-06-11", "17.00"),
            ("2026-06-12", "."),   # latest = not available → skip
        ])
        vxv_body = _make_fred_body("VXVCLS", [
            ("2026-06-10", "21.0"),
            ("2026-06-11", "20.5"),
            ("2026-06-12", "."),
        ])
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vix_body),
                    _make_mock_response(vxv_body),
                ]
                result = mod._fetch_options_proxy()
        assert result["available"] is True
        # The as_of_date should be 2026-06-11 (the last non-"." date)
        assert result["as_of_date"] == "2026-06-11"
        # vix_level must be the 2026-06-11 value, NOT 18.75
        assert abs(result["vix_level"] - 17.00) < 1e-9

    def test_as_of_date_is_iso_string(self, vixcls_fixture, vxvcls_fixture):
        """as_of_date must be an ISO date string (YYYY-MM-DD)."""
        import re
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()
        if result["available"]:
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["as_of_date"]), (
                f"as_of_date {result['as_of_date']!r} is not an ISO date string."
            )


# ---------------------------------------------------------------------------
# Integration: fixture round-trip
# ---------------------------------------------------------------------------


class TestFixtureRoundTrip:
    def test_fixture_schema_has_required_fred_keys(self, vixcls_fixture):
        """The fixture must carry all FRED API schema keys (runtime validator)."""
        required_keys = {
            "realtime_start", "realtime_end", "observation_start",
            "observation_end", "units", "output_type", "file_type",
            "order_by", "sort_order", "count", "offset", "limit",
            "observations",
        }
        for key in required_keys:
            assert key in vixcls_fixture, (
                f"FRED fixture missing required key: {key!r}. "
                "Fixture must match FRED API schema."
            )

    def test_fixture_observations_have_required_keys(self, vixcls_fixture):
        """Each observation in the fixture must have: realtime_start, realtime_end, date, value."""
        for obs in vixcls_fixture["observations"]:
            for key in ("realtime_start", "realtime_end", "date", "value"):
                assert key in obs, (
                    f"Observation missing key {key!r}: {obs}"
                )

    def test_full_pipeline_with_both_fixtures(self, vixcls_fixture, vxvcls_fixture):
        """Full _fetch_options_proxy run with both fixtures returns valid result."""
        mod = _import_module()
        with patch.dict("os.environ", {"FRED_API_KEY": "test_key_sentinel"}):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _make_mock_response(vixcls_fixture),
                    _make_mock_response(vxvcls_fixture),
                ]
                result = mod._fetch_options_proxy()

        # The fixture has non-"." observations — must be available
        assert result["available"] is True
        # Source must identify FRED
        assert "FRED" in result["source"] or "fred" in result["source"].lower()
        # vix_level must be a positive float
        assert isinstance(result["vix_level"], float)
        assert result["vix_level"] > 0
        # term structure values must be floats
        ts = result["vix_term_structure"]
        assert isinstance(ts["spot"], float)
        assert isinstance(ts["term_3m"], float)
        assert isinstance(ts["ratio"], float)
        assert isinstance(ts["spread"], float)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_fred_body(series_id: str, obs_pairs: list[tuple[str, str]]) -> dict:
    """Build a minimal FRED observations response body matching the schema fixture."""
    observations = [
        {
            "realtime_start": "2026-06-13",
            "realtime_end": "2026-06-13",
            "date": date_str,
            "value": value_str,
        }
        for date_str, value_str in obs_pairs
    ]
    return {
        "realtime_start": "2026-06-13",
        "realtime_end": "2026-06-13",
        "observation_start": "1990-01-02",
        "observation_end": "9999-12-31",
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "asc",
        "count": len(observations),
        "offset": 0,
        "limit": 100000,
        "observations": observations,
    }
