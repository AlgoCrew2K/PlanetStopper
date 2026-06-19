"""RED tests — VIX freshness fix for advisors/lens_options_proxy.py.

Feature plan: feature-plans/lens-derivatives-vix-freshness-fix.md
Acceptance criteria: AC-1 through AC-6

Defect being pinned:
    The shipped producer requests sort_order="asc", limit=100,
    observation_start="2020-01-01" — returning the OLDEST 100 observations
    (~Jan-May 2020). _parse_latest_observation walks in reverse and returns
    ~May-2020 as "latest", reporting available=True with a 6-year-stale VIX.
    The honest-availability (D-1) contract only covers fetch FAILURE, not
    STALENESS.

Tests assert CONTRACT and SHAPE, never hardcoded producer-computed VIX values.
All expected values are DERIVED from the fixture or assert format/property only.

Fixture provenance:
    tests/fixtures/math/fred_observations_fresh.json — schema-derived from the
        real FRED observations response. Latest valid observation is "2026-06-10"
        (sentinel), clearly within the staleness window when _today() is patched
        to "2026-06-16".
    tests/fixtures/math/fred_observations_stale.json — same FRED schema shape.
        Latest valid observation is "2020-05-15" (sentinel reproducing the live
        defect), clearly stale when _today() is patched to "2026-06-16" (~6 years).

Mocking strategy:
    - ALL HTTP calls: mock `requests.get` at the call site in lens_options_proxy.
      No live FRED calls. Fixture JSON is returned directly.
    - Run-date: monkeypatch `advisors.lens_options_proxy._today` with a fixed
      callable returning a deterministic datetime.date sentinel.
    - time.sleep: patched to a no-op so retry backoff does not slow tests.
    - FRED_API_KEY: set via monkeypatch.setenv to a non-empty sentinel string
      (value is irrelevant since requests.get is mocked).
    - No DB interaction. No ai_advisor import needed (testing the producer directly).

Test run command:
    pytest tests/ai_advisor/test_lens_options_proxy_freshness.py \\
        -p no:xdist -o addopts= -m "not live and not slow and not perf" -v
"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).parents[1] / "fixtures" / "math"

_FRESH_FIXTURE_PATH = _FIXTURES / "fred_observations_fresh.json"
_STALE_FIXTURE_PATH = _FIXTURES / "fred_observations_stale.json"

# Sentinel "today" date used across all freshness tests.
# All test assertions that depend on staleness comparisons use this date.
_TODAY_SENTINEL = datetime.date(2026, 6, 16)

# Sentinel FRED API key — any non-empty string; never used for real HTTP calls.
_SENTINEL_API_KEY = "TEST_FRED_KEY_NOT_REAL"


# ---------------------------------------------------------------------------
# FRED response shape validator (schema-derived-with-validator pattern, AC-5).
# Validates that the golden fixture matches the documented FRED observations shape.
# This guard means if FRED changes its schema the fixture needs updating — it
# is NOT a test of the production code, it is a test of the fixture itself.
# ---------------------------------------------------------------------------


def _validate_fred_observations_shape(data: dict, fixture_path: pathlib.Path) -> None:
    """Assert the fixture matches the FRED observations API response schema.

    Required top-level fields from the FRED docs:
        realtime_start, realtime_end, observation_start, observation_end,
        units, output_type, file_type, order_by, sort_order,
        count, offset, limit, observations

    Each observation must carry: realtime_start, realtime_end, date, value.
    """
    top_level_required = {
        "realtime_start",
        "realtime_end",
        "observation_start",
        "observation_end",
        "units",
        "output_type",
        "file_type",
        "order_by",
        "sort_order",
        "count",
        "offset",
        "limit",
        "observations",
    }
    for field in top_level_required:
        assert field in data, (
            f"Fixture {fixture_path.name} missing top-level FRED field '{field}'. "
            f"Re-capture from real FRED API or fix the fixture schema."
        )
    assert isinstance(data["observations"], list), (
        f"Fixture {fixture_path.name}: 'observations' must be a list"
    )
    assert len(data["observations"]) > 0, (
        f"Fixture {fixture_path.name}: 'observations' list must not be empty"
    )
    obs_required = {"realtime_start", "realtime_end", "date", "value"}
    for i, obs in enumerate(data["observations"]):
        for field in obs_required:
            assert field in obs, (
                f"Fixture {fixture_path.name} observation[{i}] missing field '{field}'"
            )


@pytest.fixture(scope="session")
def fresh_fred_fixture() -> dict[str, Any]:
    """Load and validate the fresh FRED observations fixture.

    Latest valid observation: "2026-06-10" (sentinel).
    Stale gap from _TODAY_SENTINEL (2026-06-16): 6 days — within any reasonable
    _OPTIONS_PROXY_MAX_STALENESS_DAYS threshold (~10 days, PM-ASSUMED).
    """
    data = json.loads(_FRESH_FIXTURE_PATH.read_text(encoding="utf-8"))
    # Strip _fixture_meta before returning to callers (not part of FRED schema).
    data_without_meta = {k: v for k, v in data.items() if k != "_fixture_meta"}
    _validate_fred_observations_shape(data_without_meta, _FRESH_FIXTURE_PATH)
    return data_without_meta


@pytest.fixture(scope="session")
def stale_fred_fixture() -> dict[str, Any]:
    """Load and validate the stale FRED observations fixture.

    Latest valid observation: "2020-05-15" (sentinel reproducing the live bug).
    Stale gap from _TODAY_SENTINEL (2026-06-16): ~2223 days — vastly exceeds any
    reasonable _OPTIONS_PROXY_MAX_STALENESS_DAYS threshold.
    """
    data = json.loads(_STALE_FIXTURE_PATH.read_text(encoding="utf-8"))
    data_without_meta = {k: v for k, v in data.items() if k != "_fixture_meta"}
    _validate_fred_observations_shape(data_without_meta, _STALE_FIXTURE_PATH)
    return data_without_meta


@pytest.fixture(autouse=True)
def _patch_fred_api_key(monkeypatch):
    """Ensure FRED_API_KEY is always set to a non-empty sentinel for every test.

    The producer checks for an empty key and returns available=False early.
    We want to test the actual fetch and freshness logic in all cases, so we
    always provide a non-empty key and mock requests.get to prevent real HTTP.
    """
    monkeypatch.setenv("FRED_API_KEY", _SENTINEL_API_KEY)


@pytest.fixture(autouse=True)
def _patch_today(monkeypatch):
    """Monkeypatch _today() to return _TODAY_SENTINEL for deterministic staleness checks.

    AC-5: the run-date used by the freshness comparison must be injectable.
    This fixture exercises that contract by verifying the patched _today() is
    what the producer actually uses (rather than datetime.date.today()).

    raising=False: _today does not yet exist on the shipped module (it is the new
    injectable seam the implementer must add). The autouse fixture creates it if
    absent so that tests which do NOT test for _today's existence can still run
    and fail on their own assertions rather than cascading from a fixture error.
    Tests in TestRunDateInjectable explicitly verify the attribute exists.
    """
    import advisors.lens_options_proxy as _proxy

    monkeypatch.setattr(_proxy, "_today", lambda: _TODAY_SENTINEL, raising=False)


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch):
    """Patch time.sleep to a no-op so retry backoff does not slow tests."""
    import advisors.lens_options_proxy as _proxy

    monkeypatch.setattr(_proxy.time, "sleep", lambda _s: None)


def _make_requests_get_mock(responses: list[dict]) -> MagicMock:
    """Return a mock for requests.get that yields successive FRED response dicts.

    Each dict in ``responses`` is returned as the .json() of successive calls.
    Every mock response has status_code=200 and does not raise raise_for_status.
    """
    mocks = []
    for resp_data in responses:
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = MagicMock()
        m.json.return_value = resp_data
        mocks.append(m)
    mock_get = MagicMock(side_effect=mocks)
    return mock_get


# ---------------------------------------------------------------------------
# Fixture shape validator test — ensures our fixtures are correctly formed.
# (Meta-test: if this fails, fix the fixture, not the producer.)
# ---------------------------------------------------------------------------


class TestFixtureSchema:
    """Verify the golden fixtures match the FRED observations API schema.

    These tests are invariant guards on the fixtures themselves — they are
    NOT testing the producer. A failure here means the fixture needs repair.
    They should be GREEN from the very first RED run.
    """

    def test_fresh_fixture_has_valid_fred_schema(self, fresh_fred_fixture):
        """fresh_fred_fixture passes the FRED schema validator (meta-test)."""
        # Validator already ran in the fixture; reaching here means it passed.
        assert "observations" in fresh_fred_fixture
        assert isinstance(fresh_fred_fixture["observations"], list)
        assert len(fresh_fred_fixture["observations"]) > 0

    def test_stale_fixture_has_valid_fred_schema(self, stale_fred_fixture):
        """stale_fred_fixture passes the FRED schema validator (meta-test)."""
        assert "observations" in stale_fred_fixture
        assert isinstance(stale_fred_fixture["observations"], list)
        assert len(stale_fred_fixture["observations"]) > 0

    def test_fresh_fixture_latest_valid_obs_is_2026_06_10(self, fresh_fred_fixture):
        """The latest non-'.' observation in the fresh fixture is dated '2026-06-10'.

        This pins the sentinel design: gap from _TODAY_SENTINEL (2026-06-16) is 6 days,
        expected to be within _OPTIONS_PROXY_MAX_STALENESS_DAYS (~10 days, PM-ASSUMED).
        """
        latest = None
        for obs in reversed(fresh_fred_fixture["observations"]):
            if obs["value"] != ".":
                latest = obs
                break
        assert latest is not None, "fresh fixture must have at least one valid observation"
        assert latest["date"] == "2026-06-10", (
            f"fresh fixture sentinel latest-valid-obs must be '2026-06-10', "
            f"got {latest['date']!r}. Fix the fixture file."
        )

    def test_stale_fixture_latest_valid_obs_is_2020_05_15(self, stale_fred_fixture):
        """The latest non-'.' observation in the stale fixture is dated '2020-05-15'.

        This pins the defect reproduction: gap from _TODAY_SENTINEL (2026-06-16)
        is ~2223 days, massively exceeding any reasonable staleness threshold.
        """
        latest = None
        for obs in reversed(stale_fred_fixture["observations"]):
            if obs["value"] != ".":
                latest = obs
                break
        assert latest is not None, "stale fixture must have at least one valid observation"
        assert latest["date"] == "2020-05-15", (
            f"stale fixture sentinel latest-valid-obs must be '2020-05-15', "
            f"got {latest['date']!r}. Fix the fixture file."
        )


# ---------------------------------------------------------------------------
# AC-1: Recent-window fetch — most-recent valid observation selected
# ---------------------------------------------------------------------------


class TestRecentWindowFetch:
    """AC-1: The producer selects the most-recent valid observation, not the
    oldest-from-2020 batch produced by the shipped asc+2020-start query.
    """

    def test_most_recent_valid_observation_is_selected(self, fresh_fred_fixture):
        """With a fresh FRED response, _parse_latest_observation selects the
        LAST non-'.' observation in the list (most-recent when asc-sorted).

        Derived: the expected value comes from reading the fixture itself —
        walk reversed observations, take the first non-'.' value.
        """
        import advisors.lens_options_proxy as _proxy

        # Derive expected from the fixture (never hardcode a producer value).
        expected_value = None
        expected_date = None
        for obs in reversed(fresh_fred_fixture["observations"]):
            if obs["value"] != ".":
                expected_value = float(obs["value"])
                expected_date = obs["date"]
                break
        assert expected_value is not None, "fresh fixture has no valid observation"

        result = _proxy._parse_latest_observation(fresh_fred_fixture)

        assert result is not None, (
            "_parse_latest_observation must return (value, date) for a fixture "
            "with valid observations, got None."
        )
        returned_value, returned_date = result
        assert returned_date == expected_date, (
            f"_parse_latest_observation must select the most-recent valid observation "
            f"(fixture date {expected_date!r}), got {returned_date!r}. "
            f"RED: the shipped code selects the right date when observations are asc-sorted "
            f"— this test pins that the latest (not the oldest) is returned."
        )
        # Assert value is a positive float (property), not the hardcoded number.
        assert isinstance(returned_value, float), (
            f"_parse_latest_observation must return a float value, got {type(returned_value)}"
        )
        assert returned_value > 0, f"VIX values are strictly positive, got {returned_value!r}"

    def test_skips_dot_value_observations(self, fresh_fred_fixture):
        """_parse_latest_observation skips observations with value='.'.

        The fixture has trailing '.' entries before the last valid one.
        Verifies the reverse-walk properly skips them.
        """
        import advisors.lens_options_proxy as _proxy

        result = _proxy._parse_latest_observation(fresh_fred_fixture)
        assert result is not None
        _value, date = result

        # Find the last observation that is a '.'.
        last_dot_date = None
        for obs in fresh_fred_fixture["observations"]:
            if obs["value"] == ".":
                last_dot_date = obs["date"]

        if last_dot_date is not None:
            # The returned date must not be a '.' observation's date.
            returned_valid_obs = None
            for obs in fresh_fred_fixture["observations"]:
                if obs["date"] == date:
                    returned_valid_obs = obs
                    break
            assert returned_valid_obs is not None, (
                f"Returned date {date!r} not found in fixture observations"
            )
            assert returned_valid_obs["value"] != ".", (
                f"_parse_latest_observation returned a '.' observation date {date!r}. "
                f"Must skip '.' values and return the latest VALID observation."
            )

    def test_fetch_does_not_use_stale_2020_start(self):
        """_fetch_fred_series must NOT request observation_start='2020-01-01'
        with sort_order='asc' and limit=100.

        This is the root-cause fix: the shipped code passes these three params
        together, returning the oldest 100 observations. The fixed code must
        use a rolling recent window (asc + recent start) or desc order.

        Verified by capturing the actual params passed to requests.get and
        asserting the combination that produces the stale batch is not present.

        RED: fails against the shipped code because it still uses 2020-01-01.
        GREEN: passes after implementer changes the window strategy.
        """
        import advisors.lens_options_proxy as _proxy

        captured_params: list[dict] = []

        def _capture_params(*args, **kwargs):
            # requests.get(url, params=..., timeout=...) — params come as a kwarg.
            captured_params.append(kwargs.get("params", {}))
            m = MagicMock()
            m.status_code = 200
            m.raise_for_status = MagicMock()
            m.json.return_value = {
                "observations": [
                    {
                        "realtime_start": "2026-06-16",
                        "realtime_end": "2026-06-16",
                        "date": "2026-06-10",
                        "value": "15.0",
                    }
                ]
            }
            return m

        with patch("advisors.lens_options_proxy.requests.get", side_effect=_capture_params):
            try:
                _proxy._fetch_fred_series("VIXCLS", _SENTINEL_API_KEY)
            except Exception:
                pass  # We only care about the params that were sent.

        assert len(captured_params) > 0, "_fetch_fred_series must call requests.get"

        for params in captured_params:
            obs_start = params.get("observation_start", "")
            sort_order = params.get("sort_order", "")
            limit = params.get("limit", None)

            # The stale-bug combination: asc + 2020-01-01 start + limit=100.
            # Any one of these constraints changing breaks the stale-batch pattern.
            stale_bug_present = obs_start == "2020-01-01" and sort_order == "asc" and limit == 100
            assert not stale_bug_present, (
                f"_fetch_fred_series uses the stale-batch query combination "
                f"(sort_order='asc', limit=100, observation_start='2020-01-01'). "
                f"This returns the OLDEST 100 observations from 2020, not recent data. "
                f"Params sent: {params!r}. "
                f"RED: this assertion fails until the implementer fixes the window strategy."
            )

    def test_all_none_observations_returns_none(self):
        """_parse_latest_observation returns None when all values are '.'.

        Existing behavior preserved (part of AC-4 regression guard).
        """
        import advisors.lens_options_proxy as _proxy

        all_dot_data = {
            "observations": [
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": "2026-06-10",
                    "value": ".",
                },
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": "2026-06-11",
                    "value": ".",
                },
            ]
        }
        result = _proxy._parse_latest_observation(all_dot_data)
        assert result is None, (
            "_parse_latest_observation must return None when all observations are '.'. "
            f"Got {result!r}."
        )

    def test_empty_observations_list_returns_none(self):
        """_parse_latest_observation returns None on an empty observations list."""
        import advisors.lens_options_proxy as _proxy

        result = _proxy._parse_latest_observation({"observations": []})
        assert result is None, (
            f"_parse_latest_observation must return None on empty observations list, got {result!r}"
        )


# ---------------------------------------------------------------------------
# AC-2: Freshness guard — stale data → available=False, no fabricated values
# ---------------------------------------------------------------------------


class TestFreshnessGuard:
    """AC-2: Core fix — when the latest valid observation is older than
    _OPTIONS_PROXY_MAX_STALENESS_DAYS, the producer returns available=False
    with reason='stale_data' and no fabricated vix/regime/risk values.
    """

    def test_stale_latest_observation_available_false(self, stale_fred_fixture):
        """PINNING RED: fixture with latest valid obs ~6 years before _today()
        must return available=False.

        This is the primary defect reproducer. On origin/main, this test FAILS
        because the producer returns available=True with a 2020 VIX value.

        The stale fixture mimics exactly what origin/main returns: the latest
        valid observation is "2020-05-15" (the May 2020 data from the asc-batch).
        With _today() = 2026-06-16, the gap is ~2223 days.
        """
        import advisors.lens_options_proxy as _proxy

        # Both VIXCLS and VXVCLS return the stale fixture (two calls per
        # _fetch_options_proxy call).
        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False, (
            f"When the latest valid FRED observation is ~6 years old (2020-05-15), "
            f"_fetch_options_proxy MUST return available=False. "
            f"Got available={result.get('available')!r}. "
            f"RED: the shipped code returns available=True with a stale 2020 VIX — "
            f"honest-availability must cover staleness, not only fetch failure."
        )

    def test_stale_obs_reason_is_stale_data(self, stale_fred_fixture):
        """Stale data returns reason='stale_data' (not a generic exception name).

        The reason must be the staleness sentinel string, not type(exc).__name__,
        because staleness is not an exception — it is a data quality decision.
        """
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("reason") == "stale_data", (
            f"Stale data must return reason='stale_data', "
            f"got {result.get('reason')!r}. "
            f"RED: the shipped code does not implement the freshness guard."
        )

    def test_stale_obs_returns_no_vix_level(self, stale_fred_fixture):
        """Stale data result must NOT contain vix_level.

        Fabricating a 2020 VIX value as if it were current is the core defect.
        An unavailable stale result must not carry any VIX payload.
        """
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert "vix_level" not in result, (
            f"Stale data result must NOT carry vix_level (would be a fabricated 2020 value). "
            f"Got result keys: {list(result.keys())!r}. "
            f"RED: shipped code includes vix_level from the stale observation."
        )

    def test_stale_obs_returns_no_vix_term_structure(self, stale_fred_fixture):
        """Stale data result must NOT contain vix_term_structure."""
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert "vix_term_structure" not in result, (
            f"Stale data result must NOT carry vix_term_structure. "
            f"Got result keys: {list(result.keys())!r}."
        )

    def test_stale_obs_returns_no_risk_read(self, stale_fred_fixture):
        """Stale data result must NOT contain risk_read."""
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert "risk_read" not in result, (
            f"Stale data result must NOT carry risk_read. Got result keys: {list(result.keys())!r}."
        )

    def test_stale_obs_returns_no_as_of_date(self, stale_fred_fixture):
        """Stale data result must NOT contain as_of_date.

        Returning as_of_date='2020-05-15' with available=False would be less
        harmful than available=True with a 2020 date, but the contract says
        as_of_date is an available=True field only (per module docstring).
        """
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert "as_of_date" not in result, (
            f"Stale data result must NOT carry as_of_date (available=False field). "
            f"Got result keys: {list(result.keys())!r}."
        )

    def test_fresh_latest_observation_available_true(self, fresh_fred_fixture):
        """Fresh data (latest obs within staleness window) returns available=True.

        Positive guard: the freshness fix must not false-positive and make
        valid recent data unavailable.

        Fresh fixture: latest valid obs = "2026-06-10", _today() = "2026-06-16",
        gap = 6 days. Expected to be within _OPTIONS_PROXY_MAX_STALENESS_DAYS
        (PM-ASSUMED ~10 days).
        """
        import advisors.lens_options_proxy as _proxy

        # Provide fresh fixture for BOTH VIXCLS and VXVCLS fetches.
        mock_get = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, (
            f"When the latest FRED observation is 6 days old (fresh fixture), "
            f"_fetch_options_proxy must return available=True. "
            f"Got available={result.get('available')!r}. "
            f"RED: the freshness guard has a false-positive or the module lacks _today()."
        )

    def test_fresh_result_has_required_payload_keys(self, fresh_fred_fixture):
        """Fresh result must carry all required available=True payload fields.

        Keys per the module docstring: vix_level, vix_term_structure, risk_read,
        as_of_date, source. Values are property-asserted (not hardcoded).
        """
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, "precondition"
        for key in ("vix_level", "vix_term_structure", "risk_read", "as_of_date", "source"):
            assert key in result, (
                f"Fresh result must carry key '{key}', got keys: {list(result.keys())!r}"
            )

    def test_named_staleness_constant_exists(self):
        """_OPTIONS_PROXY_MAX_STALENESS_DAYS is a named module-level constant.

        AC-2 requires: "The threshold is a NAMED module-level constant with a
        source comment." This test verifies the name exists at the module level.
        The reviewer verifies the source comment manually (not testable in pytest).
        """
        import advisors.lens_options_proxy as _proxy

        assert hasattr(_proxy, "_OPTIONS_PROXY_MAX_STALENESS_DAYS"), (
            "advisors.lens_options_proxy must expose '_OPTIONS_PROXY_MAX_STALENESS_DAYS' "
            "as a named module-level constant. "
            "RED: the shipped code has no such constant — the staleness threshold "
            "is an undeclared magic number that does not exist yet."
        )
        staleness_days = _proxy._OPTIONS_PROXY_MAX_STALENESS_DAYS
        assert isinstance(staleness_days, int), (
            f"_OPTIONS_PROXY_MAX_STALENESS_DAYS must be an int, got {type(staleness_days)}"
        )
        assert staleness_days > 0, (
            f"_OPTIONS_PROXY_MAX_STALENESS_DAYS must be positive, got {staleness_days!r}"
        )
        # Must be large enough to cover weekends/holidays (plan: "above longest normal
        # market closure"). A reasonable lower bound is 5 days.
        assert staleness_days >= 5, (
            f"_OPTIONS_PROXY_MAX_STALENESS_DAYS={staleness_days} is too small to cover "
            f"a long weekend. Plan requires threshold 'comfortably above the longest "
            f"normal market closure' (~4 days). Minimum 5 (recommend ~10, PM-ASSUMED)."
        )
        # Must be small enough to catch the 6-year defect decisively.
        # The stale fixture is 2223 days old. Any threshold < 1000 days would catch it.
        assert staleness_days < 1000, (
            f"_OPTIONS_PROXY_MAX_STALENESS_DAYS={staleness_days} is suspiciously large "
            f"and would fail to protect against long data gaps."
        )

    def test_named_lookback_constant_exists(self):
        """_OPTIONS_PROXY_LOOKBACK_DAYS is a named module-level constant.

        The rolling recent-window start date is computed as:
            _today() - timedelta(days=_OPTIONS_PROXY_LOOKBACK_DAYS)
        This constant must be a named symbol (no magic numbers in the producer).
        """
        import advisors.lens_options_proxy as _proxy

        assert hasattr(_proxy, "_OPTIONS_PROXY_LOOKBACK_DAYS"), (
            "advisors.lens_options_proxy must expose '_OPTIONS_PROXY_LOOKBACK_DAYS' "
            "as a named module-level constant for the rolling fetch window. "
            "RED: the shipped code has no such constant."
        )
        lookback = _proxy._OPTIONS_PROXY_LOOKBACK_DAYS
        assert isinstance(lookback, int), (
            f"_OPTIONS_PROXY_LOOKBACK_DAYS must be an int, got {type(lookback)}"
        )
        # Must be wide enough to contain at least a few valid observations
        # across holidays. Plan says ~90 days.
        assert lookback >= 30, (
            f"_OPTIONS_PROXY_LOOKBACK_DAYS={lookback} is too small to reliably "
            f"contain valid observations across market closures. Minimum 30, recommend 90."
        )


# ---------------------------------------------------------------------------
# AC-3: as_of_date truthful — equals selected observation's real date
# ---------------------------------------------------------------------------


class TestAsOfDateTruthful:
    """AC-3: The returned as_of_date equals the date of the selected observation.

    The defect case: as_of_date was "2020-05-15" (stale, from the 2020 batch).
    The fix: as_of_date must equal the most-recent valid observation's real date.
    On fresh data, this should be the fixture's latest valid date, not today.
    """

    def test_as_of_date_equals_latest_fixture_observation_date(self, fresh_fred_fixture):
        """as_of_date in the fresh result equals the fixture's latest valid observation date.

        Derived: the expected date comes from reading the fresh fixture itself —
        the last non-'.' observation's date string.
        """
        import advisors.lens_options_proxy as _proxy

        # Derive expected from the fixture (never hardcode).
        expected_date = None
        for obs in reversed(fresh_fred_fixture["observations"]):
            if obs["value"] != ".":
                expected_date = obs["date"]
                break
        assert expected_date is not None, "fresh fixture must have a valid observation"

        mock_get = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, "precondition"
        returned_as_of = result.get("as_of_date")
        assert returned_as_of == expected_date, (
            f"as_of_date must equal the selected observation's real date from the "
            f"fixture ({expected_date!r}), got {returned_as_of!r}. "
            f"AC-3: a fabricated today-date or wrong date in as_of_date is a defect."
        )

    def test_as_of_date_is_not_todays_date_when_data_is_older(self, fresh_fred_fixture):
        """as_of_date must NOT be _today() when the latest observation is older.

        The producer must use the observation's actual date, not the run date.
        With fresh fixture latest = "2026-06-10" and _today() = "2026-06-16",
        they differ — as_of_date must be "2026-06-10", not "2026-06-16".
        """
        import advisors.lens_options_proxy as _proxy

        today_str = _TODAY_SENTINEL.isoformat()  # "2026-06-16"

        # Derive the expected date (not today) from the fixture.
        expected_date = None
        for obs in reversed(fresh_fred_fixture["observations"]):
            if obs["value"] != ".":
                expected_date = obs["date"]
                break

        # Guard: ensure the fixture sentinel date differs from _TODAY_SENTINEL.
        assert expected_date != today_str, (
            f"Fixture sentinel latest date must differ from _TODAY_SENTINEL "
            f"for this test to be meaningful. Got expected_date={expected_date!r}, "
            f"today={today_str!r}. Fix the fixture."
        )

        mock_get = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, "precondition"
        as_of = result.get("as_of_date")
        assert as_of != today_str, (
            f"as_of_date must not be today's date when the latest observation is "
            f"from {expected_date!r}. Got as_of_date={as_of!r}, today={today_str!r}. "
            f"The producer must use the observation's real date, not the run date."
        )

    def test_as_of_date_is_valid_iso_date_string(self, fresh_fred_fixture):
        """as_of_date is a valid ISO date string (YYYY-MM-DD).

        The date must be parseable by datetime.date.fromisoformat — the freshness
        guard itself requires this (it parses the date for comparison).
        """
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, "precondition"
        as_of = result.get("as_of_date", "")
        assert isinstance(as_of, str) and len(as_of) == 10, (
            f"as_of_date must be a 10-char ISO date string, got {as_of!r}"
        )
        # Must parse without raising.
        try:
            parsed = datetime.date.fromisoformat(as_of)
        except (ValueError, TypeError) as exc:
            pytest.fail(f"as_of_date={as_of!r} is not a valid ISO date: {exc!r}")
        # Must be in the past (or present) relative to _TODAY_SENTINEL.
        assert parsed <= _TODAY_SENTINEL, (
            f"as_of_date {parsed} must be <= _today() sentinel {_TODAY_SENTINEL} "
            f"(FRED doesn't publish future observations)."
        )


# ---------------------------------------------------------------------------
# AC-4: Honest-availability preserved — existing failure paths unchanged
# ---------------------------------------------------------------------------


class TestHonestAvailabilityPreserved:
    """AC-4: The fix must not break existing honest-availability paths.

    Regression guard for:
    - Transport / connection failure → available=False, reason=exc type
    - HTTP 429 exhausted → available=False, reason=exc type
    - No valid observations (all '.') → available=False
    - Regime classification + risk_read unchanged when data is fresh and valid
    """

    def test_connection_error_returns_unavailable(self):
        """ConnectionError raises → available=False, reason='ConnectionError'.

        D-1 contract preserved: reason is type(exc).__name__ only.
        """
        import advisors.lens_options_proxy as _proxy

        with patch(
            "advisors.lens_options_proxy.requests.get",
            side_effect=requests.exceptions.ConnectionError("simulated"),
        ):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False, "ConnectionError must return available=False"
        assert result.get("reason") == "ConnectionError", (
            f"D-1: reason must be 'ConnectionError', got {result.get('reason')!r}"
        )
        assert "vix_level" not in result
        assert "source" in result, "source citation must always be present"

    def test_timeout_error_returns_unavailable(self):
        """Timeout → available=False, reason='Timeout'."""
        import advisors.lens_options_proxy as _proxy

        with patch(
            "advisors.lens_options_proxy.requests.get",
            side_effect=requests.exceptions.Timeout("simulated"),
        ):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False
        assert result.get("reason") == "Timeout", (
            f"D-1: reason must be 'Timeout', got {result.get('reason')!r}"
        )

    def test_429_exhausted_returns_unavailable(self):
        """HTTP 429 on every attempt → available=False after bounded retries.

        The bounded retry loop (AC-6 preserved) must exhaust and return
        available=False, not raise and not loop infinitely.
        """
        import advisors.lens_options_proxy as _proxy

        retryable_resp = MagicMock()
        retryable_resp.status_code = 429
        retryable_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=retryable_resp
        )

        # Return 429 on every attempt (max_attempts = 3, so 3 calls for VIXCLS,
        # then potentially 3 for VXVCLS if VIXCLS somehow succeeds — but it won't).
        with patch(
            "advisors.lens_options_proxy.requests.get",
            return_value=retryable_resp,
        ):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False, "Exhausted 429 retries must return available=False"
        reason = result.get("reason", "")
        assert isinstance(reason, str) and reason, "reason must be a non-empty string"
        # D-1: reason is type(exc).__name__ — HTTPError
        assert "HTTPError" in reason or "Error" in reason, (
            f"D-1: reason must be the exception type name on 429, got {reason!r}"
        )

    def test_no_valid_observations_returns_unavailable(self):
        """All-dot VIXCLS response → available=False, no fabricated VIX.

        Existing no-valid-observations path is preserved.
        """
        import advisors.lens_options_proxy as _proxy

        all_dots = {
            "observations": [
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": "2026-06-10",
                    "value": ".",
                },
            ]
        }
        # Second call (VXVCLS) won't be reached because VIXCLS fails first.
        mock_get = _make_requests_get_mock([all_dots])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False, (
            "All-dot VIXCLS observations must return available=False"
        )
        assert "vix_level" not in result

    def test_regime_classification_backwardation_contango_preserved_when_fresh(
        self, fresh_fred_fixture
    ):
        """Regime classification logic (_classify_regime) is unchanged.

        Spot > term_3m → backwardation (stress).
        Spot < term_3m → contango (calm).
        The freshness guard only gates BEFORE regime computation; after a fresh
        fetch, the existing regime logic runs unchanged.
        """
        import advisors.lens_options_proxy as _proxy

        # Test _classify_regime directly (not gated by freshness).
        # Spot > term_3m (high stress → backwardation).
        regime_back = _proxy._classify_regime(spot=35.0, term_3m=25.0)
        assert regime_back == "backwardation", (
            f"spot=35 > term_3m=25 must classify as 'backwardation', got {regime_back!r}"
        )

        # Spot < term_3m (calm → contango).
        regime_cont = _proxy._classify_regime(spot=15.0, term_3m=20.0)
        assert regime_cont == "contango", (
            f"spot=15 < term_3m=20 must classify as 'contango', got {regime_cont!r}"
        )

    def test_d1_reason_is_exc_type_name_only(self):
        """D-1 contract: reason is type(exc).__name__, never str(exc).

        If str(exc) contained API keys or host names, they must not leak.
        """
        import advisors.lens_options_proxy as _proxy

        secret = "api_key=SUPER_SECRET_12345"

        with patch(
            "advisors.lens_options_proxy.requests.get",
            side_effect=ConnectionError(f"Request failed: {secret}"),
        ):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False
        reason = result.get("reason", "")
        assert secret not in reason, (
            f"D-1 violated: secret string leaked into reason. reason={reason!r}"
        )


# ---------------------------------------------------------------------------
# AC-5: Run-date injectable via _today()
# ---------------------------------------------------------------------------


class TestRunDateInjectable:
    """AC-5: The run-date used by the freshness comparison must be injectable.

    The _today() helper at module scope allows tests to pin the comparison date
    deterministically without depending on the wall clock.
    """

    def test_today_callable_exists_at_module_scope(self):
        """_today is a callable defined IN the module source, not injected by tests.

        AC-5 hard requirement: the freshness comparison uses this injectable
        helper, not a hardcoded call to datetime.date.today().

        The autouse _patch_today fixture creates _today on the module with
        raising=False so that other tests (that need it for their comparisons)
        don't fail in setup. This test uses importlib + AST inspection to verify
        _today is defined IN the module source file — not just present as a
        runtime attribute (which could be the test fixture's synthetic lambda).
        """
        import ast
        import pathlib

        proxy_path = pathlib.Path(__file__).parents[2] / "advisors" / "lens_options_proxy.py"
        source = proxy_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Walk the module body (top-level only) for a FunctionDef named _today.
        today_fn_defined = any(
            isinstance(node, ast.FunctionDef) and node.name == "_today" for node in tree.body
        )
        assert today_fn_defined, (
            "advisors/lens_options_proxy.py must define '_today' as a module-level "
            "function (def _today(): ...) in the source file. "
            "The autouse fixture can monkeypatch it ONLY BECAUSE it's already there. "
            "RED: the shipped module has no such function definition."
        )

    def test_today_function_body_returns_datetime_date_via_ast(self):
        """The _today() function in the module source returns a datetime.date.

        AST inspection: the function body must contain a call to
        datetime.date.today() (or equivalent) — not a hardcoded date literal.
        This ensures the default (unpatched) _today() will always return a
        real live date at production runtime.

        RED: the shipped module has no _today function at all — AST finds nothing.
        """
        import ast
        import pathlib

        proxy_path = pathlib.Path(__file__).parents[2] / "advisors" / "lens_options_proxy.py"
        source = proxy_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find the _today function definition.
        today_fn = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_today":
                today_fn = node
                break

        if today_fn is None:
            pytest.fail(
                "advisors/lens_options_proxy.py has no '_today' function definition. "
                "RED: the shipped code has no injectable run-date seam."
            )

        # Verify it contains some reference to datetime — either datetime.date.today()
        # or similar. We look for a Return node containing an Attribute access to 'today'.
        fn_source = ast.unparse(today_fn)
        assert "datetime" in fn_source or "date" in fn_source, (
            f"_today() function body does not reference 'datetime' or 'date'. "
            f"The default implementation must return datetime.date.today(). "
            f"Function source: {fn_source!r}"
        )

    def test_freshness_comparison_uses_injectable_today(self, stale_fred_fixture):
        """The freshness comparison uses _today(), not datetime.date.today().

        Verified by patching _today to return a HISTORICAL date (2020-05-20)
        close to the stale fixture's latest observation (2020-05-15). With only
        5 days of gap, the observation is FRESH relative to the patched _today
        — so the result must be available=True, NOT available=False.

        If the producer used the real datetime.date.today() instead of _today(),
        the observation would be ~6 years old → available=False.
        This test will FAIL if the producer ignores the injectable _today() and
        uses datetime.date.today() directly.
        """
        import advisors.lens_options_proxy as _proxy

        # Override the autouse fixture's _today patch for this specific test.
        # We patch _today to return 2020-05-20 (5 days after the stale fixture's
        # latest obs of 2020-05-15 — should be within the staleness window).
        historical_today = datetime.date(2020, 5, 20)
        with patch.object(_proxy, "_today", return_value=historical_today):
            mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])
            with patch("advisors.lens_options_proxy.requests.get", mock_get):
                result = _proxy._fetch_options_proxy()

        # Gap: 2020-05-20 - 2020-05-15 = 5 days. This should be WITHIN the
        # staleness threshold (~10 days, PM-ASSUMED). So result must be available=True.
        assert result.get("available") is True, (
            f"When _today() is patched to 2020-05-20 and the latest obs is 2020-05-15 "
            f"(gap=5 days, within staleness threshold), the result must be available=True. "
            f"Got available={result.get('available')!r}. "
            f"RED: if the producer uses datetime.date.today() instead of _today(), "
            f"it sees a 2023+ date and marks 2020 data as stale — failing this test. "
            f"Reason: {result.get('reason')!r}"
        )

    def test_freshness_comparison_uses_today_to_mark_stale(self, stale_fred_fixture):
        """When _today() is far in the future, 2020 data is stale.

        Positive complement to the above test: with _today() = 2026-06-16 (the
        autouse fixture), the stale fixture (latest obs 2020-05-15) must be
        available=False. Confirms the injectable _today() is used, not a
        hardcoded reference date.
        """
        import advisors.lens_options_proxy as _proxy

        # The autouse _patch_today fixture already sets _today() = 2026-06-16.
        # No additional patching needed.
        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        # Gap: 2026-06-16 - 2020-05-15 = 2223 days >> any threshold.
        assert result.get("available") is False, (
            f"With _today()=2026-06-16 and latest obs=2020-05-15 (gap=2223 days), "
            f"result must be available=False. Got {result.get('available')!r}."
        )
        assert result.get("reason") == "stale_data"


# ---------------------------------------------------------------------------
# AC-6: Bounded retry + off-execution-path preserved
# ---------------------------------------------------------------------------


class TestBoundedRetryPreserved:
    """AC-6: The fix must preserve bounded retry and the never-raises contract.

    The existing _OPTIONS_PROXY_MAX_ATTEMPTS / exponential backoff / cap
    constants and the off-execution-path contract are unchanged by the fix.
    """

    def test_max_attempts_constant_present(self):
        """_OPTIONS_PROXY_MAX_ATTEMPTS still exists and is a positive int."""
        import advisors.lens_options_proxy as _proxy

        assert hasattr(_proxy, "_OPTIONS_PROXY_MAX_ATTEMPTS"), (
            "_OPTIONS_PROXY_MAX_ATTEMPTS must still exist after the fix"
        )
        assert isinstance(_proxy._OPTIONS_PROXY_MAX_ATTEMPTS, int)
        assert _proxy._OPTIONS_PROXY_MAX_ATTEMPTS >= 1, "_OPTIONS_PROXY_MAX_ATTEMPTS must be >= 1"

    def test_backoff_cap_constant_present(self):
        """_OPTIONS_PROXY_BACKOFF_CAP_S still exists and is a positive float."""
        import advisors.lens_options_proxy as _proxy

        assert hasattr(_proxy, "_OPTIONS_PROXY_BACKOFF_CAP_S"), (
            "_OPTIONS_PROXY_BACKOFF_CAP_S must still exist after the fix"
        )
        assert isinstance(_proxy._OPTIONS_PROXY_BACKOFF_CAP_S, float)
        assert _proxy._OPTIONS_PROXY_BACKOFF_CAP_S > 0

    def test_max_retry_wait_constant_present(self):
        """MAX_OPTIONS_RETRY_WAIT_SECONDS still exists and is a positive float."""
        import advisors.lens_options_proxy as _proxy

        assert hasattr(_proxy, "MAX_OPTIONS_RETRY_WAIT_SECONDS"), (
            "MAX_OPTIONS_RETRY_WAIT_SECONDS must still exist after the fix"
        )
        assert isinstance(_proxy._OPTIONS_PROXY_BACKOFF_CAP_S, float)
        assert _proxy.MAX_OPTIONS_RETRY_WAIT_SECONDS > 0

    def test_fetch_options_proxy_never_raises(self, stale_fred_fixture, fresh_fred_fixture):
        """_fetch_options_proxy never raises — always returns a dict.

        CC-3 / off-execution-path contract: never raises under any condition.
        Test with fresh, stale, and exception injection scenarios.
        """
        import advisors.lens_options_proxy as _proxy

        # Scenario 1: fresh data
        mock_get_fresh = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])
        with patch("advisors.lens_options_proxy.requests.get", mock_get_fresh):
            try:
                result = _proxy._fetch_options_proxy()
                assert isinstance(result, dict)
            except Exception as exc:
                pytest.fail(
                    f"_fetch_options_proxy raised on fresh data: {exc!r}. "
                    f"CC-3 violated: must never raise."
                )

        # Scenario 2: stale data
        mock_get_stale = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])
        with patch("advisors.lens_options_proxy.requests.get", mock_get_stale):
            try:
                result = _proxy._fetch_options_proxy()
                assert isinstance(result, dict)
            except Exception as exc:
                pytest.fail(
                    f"_fetch_options_proxy raised on stale data: {exc!r}. "
                    f"CC-3 violated: must never raise."
                )

        # Scenario 3: network failure
        with patch(
            "advisors.lens_options_proxy.requests.get",
            side_effect=ConnectionError("network down"),
        ):
            try:
                result = _proxy._fetch_options_proxy()
                assert isinstance(result, dict)
            except Exception as exc:
                pytest.fail(
                    f"_fetch_options_proxy raised on network failure: {exc!r}. "
                    f"CC-3 violated: must never raise."
                )

    def test_retry_count_bounded_on_connection_error(self):
        """_fetch_fred_series stops retrying after _OPTIONS_PROXY_MAX_ATTEMPTS.

        Verifies the retry loop is bounded — not an infinite retry on failures.
        """
        import advisors.lens_options_proxy as _proxy

        call_count = 0

        def _counting_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise requests.exceptions.ConnectionError("simulated")

        with patch("advisors.lens_options_proxy.requests.get", side_effect=_counting_error):
            try:
                _proxy._fetch_fred_series("VIXCLS", _SENTINEL_API_KEY)
            except Exception:
                pass  # Expected to raise after exhausting attempts.

        assert call_count <= _proxy._OPTIONS_PROXY_MAX_ATTEMPTS, (
            f"_fetch_fred_series made {call_count} attempts but "
            f"_OPTIONS_PROXY_MAX_ATTEMPTS={_proxy._OPTIONS_PROXY_MAX_ATTEMPTS}. "
            f"The retry loop is not bounded correctly."
        )
        assert call_count > 0, "_fetch_fred_series must make at least one attempt"


# ---------------------------------------------------------------------------
# Weekend / holiday edge case: 3-4 day old data must still be available
# ---------------------------------------------------------------------------


class TestWeekendHolidayEdgeCase:
    """The freshness guard must NOT false-positive on normal weekend/holiday closures.

    Plan §Edge Cases: "The latest VIX observation may legitimately be 1-4 calendar
    days old. The staleness threshold MUST NOT flag these as stale — pick a threshold
    comfortably above the longest normal market closure."

    We test with a fresh fixture where the latest observation is 3 days old relative
    to _today() — simulating a Tuesday-morning run after a 3-day holiday weekend.
    """

    def test_observation_three_days_old_is_still_available(self):
        """Latest obs 3 days before _today() → available=True (within threshold).

        Constructs a minimal FRED-shaped response where the latest valid obs is
        3 days before the injected _today(). Gap = 3 days < any sane staleness
        threshold (PM-ASSUMED ~10 days). The guard must NOT fire.
        """
        import advisors.lens_options_proxy as _proxy

        three_days_ago = _TODAY_SENTINEL - datetime.timedelta(days=3)
        obs_date_str = three_days_ago.isoformat()  # e.g. "2026-06-13"

        near_fresh_response = {
            "observations": [
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": "2026-06-10",
                    "value": ".",
                },
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": obs_date_str,
                    "value": "16.45",
                },
            ]
        }

        mock_get = _make_requests_get_mock([near_fresh_response, near_fresh_response])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, (
            f"Latest observation is {obs_date_str} (3 days before _today()="
            f"{_TODAY_SENTINEL}). Gap = 3 days. This is a normal long-weekend "
            f"scenario. The freshness guard must NOT flag it as stale. "
            f"Got available={result.get('available')!r}, reason={result.get('reason')!r}. "
            f"RED: if _OPTIONS_PROXY_MAX_STALENESS_DAYS <= 3 the guard is too tight."
        )

    def test_observation_four_days_old_is_still_available(self):
        """Latest obs 4 days before _today() → available=True (4-day closure guard).

        Longest plausible market closure: long weekend + adjacent holiday = ~4 days.
        The threshold must be > 4 to avoid false-positives on these closures.
        """
        import advisors.lens_options_proxy as _proxy

        four_days_ago = _TODAY_SENTINEL - datetime.timedelta(days=4)
        obs_date_str = four_days_ago.isoformat()

        four_day_old_response = {
            "observations": [
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": obs_date_str,
                    "value": "17.11",
                },
            ]
        }

        mock_get = _make_requests_get_mock([four_day_old_response, four_day_old_response])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is True, (
            f"Latest observation is {obs_date_str} (4 days before _today()). "
            f"Gap = 4 days — normal long-holiday weekend. "
            f"Must be available=True. Got {result.get('available')!r}, "
            f"reason={result.get('reason')!r}."
        )

    def test_observation_older_than_staleness_threshold_is_unavailable(self):
        """Latest obs older than _OPTIONS_PROXY_MAX_STALENESS_DAYS → available=False.

        Positive complement: once the gap genuinely exceeds the threshold, the
        guard fires correctly. We use a gap of (max_staleness + 30) days to ensure
        we're well beyond any reasonable threshold.
        """
        import advisors.lens_options_proxy as _proxy

        # Use the constant if it exists; fall back to the PM-ASSUMED default of 10
        # so the test can assert a meaningful staleness scenario regardless of order
        # (the test_named_staleness_constant_exists test in TestFreshnessGuard pins
        # that the constant MUST exist — this avoids an AttributeError cascade here).
        max_staleness = getattr(_proxy, "_OPTIONS_PROXY_MAX_STALENESS_DAYS", 10)
        stale_days = max_staleness + 30  # Well beyond the threshold.
        stale_date = _TODAY_SENTINEL - datetime.timedelta(days=stale_days)

        stale_response = {
            "observations": [
                {
                    "realtime_start": "2026-06-16",
                    "realtime_end": "2026-06-16",
                    "date": stale_date.isoformat(),
                    "value": "21.00",
                },
            ]
        }

        mock_get = _make_requests_get_mock([stale_response, stale_response])

        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert result.get("available") is False, (
            f"Latest observation is {stale_days} days old (threshold={max_staleness}). "
            f"Must be available=False. Got {result.get('available')!r}."
        )
        assert result.get("reason") == "stale_data", (
            f"reason must be 'stale_data', got {result.get('reason')!r}"
        )


# ---------------------------------------------------------------------------
# Property: source citation always present
# ---------------------------------------------------------------------------


class TestSourceCitationAlwaysPresent:
    """The 'source' key must be present in EVERY returned dict (available or not).

    This is an existing contract from the module docstring — preserved by the fix.
    """

    def test_source_present_on_fresh_result(self, fresh_fred_fixture):
        """source key is present when available=True."""
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([fresh_fred_fixture, fresh_fred_fixture])
        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert "source" in result, "source must always be present in the result dict"
        assert isinstance(result["source"], str) and result["source"].strip(), (
            "source must be a non-empty string"
        )

    def test_source_present_on_stale_result(self, stale_fred_fixture):
        """source key is present when available=False due to staleness."""
        import advisors.lens_options_proxy as _proxy

        mock_get = _make_requests_get_mock([stale_fred_fixture, stale_fred_fixture])
        with patch("advisors.lens_options_proxy.requests.get", mock_get):
            result = _proxy._fetch_options_proxy()

        assert "source" in result, "source must always be present even when stale"
        assert isinstance(result["source"], str) and result["source"].strip()

    def test_source_present_on_network_failure(self):
        """source key is present when available=False due to network failure."""
        import advisors.lens_options_proxy as _proxy

        with patch(
            "advisors.lens_options_proxy.requests.get",
            side_effect=ConnectionError("network down"),
        ):
            result = _proxy._fetch_options_proxy()

        assert "source" in result, "source must always be present even on network failure"
