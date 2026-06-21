"""RED tests for advisors/universe_provider.py (AC-1..AC-6).

Module under test: advisors.universe_provider  (net-new; does not exist yet)

Expected public surface
-----------------------
  ALPACA_TRADING_BASE_URL : str  — paper trading host constant, DISTINCT from
                                   synthetic_history.ALPACA_BASE_URL (data host)
  fetch_universe(*, force_refresh=False, db_path=None) -> dict
      Returns {available, symbols, reason?} where `symbols` is a frozenset/set.
      Never raises. On failure: available=False + reason=ExcClassName only.
  is_tradeable(symbol: str, *, force_refresh=False) -> bool
      Membership check served from cache after first fetch. Never raises.
  get_tradeable_set(*, force_refresh=False) -> frozenset[str] | set[str]
      Returns the full membership set. Never raises.

ADVERSARIAL FOCUS
-----------------
  AC-1: HTTP call hits paper-api.alpaca.markets (NOT api.alpaca.markets);
        correct auth headers; single-array consumption (one request, no loop).
  AC-2: Filter is exactly tradable==True AND exchange in {NASDAQ,NYSE,ARCA,BATS,AMEX}.
        ETFs and leveraged/inverse ETFs are first-class — never excluded by class.
        No ranking, no top-N cap, no ordering surface.
  AC-3: Cache via atlas_cache pattern; second call within TTL fires zero HTTP;
        force_refresh bypasses; TTL capped at <= 7 days (bill-protection).
  AC-4: is_tradeable returns bool True/False from cache; get_tradeable_set returns
        the full unordered set; no additional HTTP after cache warm.
  AC-5: Every D-1 failure mode (401, timeout, empty array [], malformed JSON)
        → available=False + reason string; never raises; never leaks ALPACA_KEY
        or ALPACA_SECRET in the reason string.
  AC-6: A warehouse snapshot row is written on a fresh fetch (lens='universe_provider');
        the row's raw_json does not contain the ALPACA_KEY value; the module does NOT
        import database.py or the optimization-DB module at the AST level.

Strategy for HTTP mocking
--------------------------
  All tests monkeypatch `requests.get` (or the seam the implementer chooses —
  the fixture-driven tests spy on `requests.get` at the module level).
  ATLAS_CACHE_DB_PATH is isolated to a per-test tmp_path via monkeypatch.

No live network calls. No production DB writes.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "advisors" / "universe_provider_assets.json"


def _load_fixture() -> dict:
    """Load the adversarial assets fixture; skip if absent (pre-commit guard)."""
    if not _FIXTURE_PATH.exists():
        pytest.skip(f"Universe provider fixture missing: {_FIXTURE_PATH}")
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status_code: int = 200, json_body=None, text_body: str = "") -> MagicMock:
    """Build a mock requests.Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400

    if json_body is not None:
        resp.json.return_value = json_body
        resp.text = json.dumps(json_body)
    else:
        resp.text = text_body
        resp.json.side_effect = ValueError(f"No JSON: {text_body!r}")

    return resp


def _fixture_asset_array(fixture: dict) -> list:
    """Return just the raw asset list from the fixture dict."""
    return fixture["assets"]


def _expected_symbols(fixture: dict) -> frozenset:
    """Return the pre-computed expected survivor symbols from the fixture metadata."""
    return frozenset(fixture["expected_survivor_symbols"])


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def asset_fixture():
    """Load and return the adversarial assets fixture."""
    return _load_fixture()


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    """Redirect ATLAS_CACHE_DB_PATH to a per-test temp file for cache isolation.

    This prevents cross-test cache pollution and keeps all writes off disk.
    Returns the db_path string so tests can inspect cache state directly.

    No module reload is needed (and reloading per-test is a memory-leak anti-pattern —
    importlib.reload installs a NEW module object while other already-imported modules
    keep references to the OLD one, leaking a module per test across a multi-file run):
    atlas_cache._db_path() reads ATLAS_CACHE_DB_PATH from os.environ at CALL time
    (atlas_cache.py), so monkeypatch.setenv alone makes the new path take effect; and
    universe_provider has no module-level mutable cache (it routes through atlas_cache's
    per-test SQLite DB), so the tmp_path env var fully isolates each test.
    """
    db_path = str(tmp_path / "test_atlas_cache.db")
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", db_path)
    yield db_path


@pytest.fixture()
def alpaca_env(monkeypatch):
    """Set detectable ALPACA_KEY / ALPACA_SECRET env vars for secret-leak tests."""
    monkeypatch.setenv("ALPACA_KEY", "DETECTABLE_TEST_KEY_ALPACA_123")
    monkeypatch.setenv("ALPACA_SECRET", "DETECTABLE_TEST_SECRET_ALPACA_456")


# ---------------------------------------------------------------------------
# AC-1: HTTP target + auth headers + single-array consumption
# ---------------------------------------------------------------------------


class TestFetchTarget:
    """AC-1: the universe fetch must hit the Alpaca PAPER trading host."""

    def test_fetch_targets_paper_trading_host(
        self, asset_fixture, isolated_cache, alpaca_env, monkeypatch
    ):
        """The HTTP call must go to paper-api.alpaca.markets, NOT api.alpaca.markets.

        A lazy implementation that copies synthetic_history.ALPACA_BASE_URL
        (data.alpaca.markets) would 401 on paper-only keys — this test catches that.
        """
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            universe_provider.fetch_universe()

        assert mock_get.called, "requests.get was never called — no HTTP fetch issued"
        called_url = mock_get.call_args[0][0]  # first positional arg
        assert "paper-api.alpaca.markets" in called_url, (
            f"Expected paper host in URL; got {called_url!r}. "
            "Our keys are PAPER keys — the live host api.alpaca.markets 401s."
        )
        assert "api.alpaca.markets" in called_url  # paper host contains this string
        # Reject any URL that targets the LIVE host directly.
        assert not called_url.startswith("https://api.alpaca.markets"), (
            f"URL targets the LIVE host (api.alpaca.markets), not the paper host: {called_url!r}"
        )

    def test_fetch_sends_correct_query_params(
        self, asset_fixture, isolated_cache, alpaca_env, monkeypatch
    ):
        """The HTTP call must include status=active and asset_class=us_equity as params."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            universe_provider.fetch_universe()

        assert mock_get.called
        kwargs = mock_get.call_args[1] if mock_get.call_args[1] else {}
        # params may be in kwargs or baked into the URL string — check both paths.
        params = kwargs.get("params", {})
        called_url = mock_get.call_args[0][0]

        # Accept either explicit params dict OR URL-embedded query string.
        has_status_active = params.get("status") == "active" or "status=active" in called_url
        has_us_equity = (
            params.get("asset_class") == "us_equity" or "asset_class=us_equity" in called_url
        )
        assert has_status_active, (
            f"Missing status=active in params={params!r} or URL={called_url!r}"
        )
        assert has_us_equity, (
            f"Missing asset_class=us_equity in params={params!r} or URL={called_url!r}"
        )

    def test_fetch_sends_alpaca_auth_headers(
        self, asset_fixture, isolated_cache, alpaca_env, monkeypatch
    ):
        """The HTTP call must include APCA-API-KEY-ID and APCA-API-SECRET-KEY headers."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            universe_provider.fetch_universe()

        assert mock_get.called
        kwargs = mock_get.call_args[1] if mock_get.call_args[1] else {}
        headers = kwargs.get("headers", {})

        assert "APCA-API-KEY-ID" in headers, (
            f"Missing APCA-API-KEY-ID in request headers={headers!r}"
        )
        assert "APCA-API-SECRET-KEY" in headers, (
            f"Missing APCA-API-SECRET-KEY in request headers={headers!r}"
        )
        # The values must come from ALPACA_KEY / ALPACA_SECRET env, not hardcoded.
        assert headers["APCA-API-KEY-ID"] == os.environ["ALPACA_KEY"], (
            "APCA-API-KEY-ID value does not match ALPACA_KEY env var"
        )
        assert headers["APCA-API-SECRET-KEY"] == os.environ["ALPACA_SECRET"], (
            "APCA-API-SECRET-KEY value does not match ALPACA_SECRET env var"
        )

    def test_fetch_consumes_single_array_no_pagination(
        self, asset_fixture, isolated_cache, alpaca_env
    ):
        """The response is a flat JSON array — no pagination loop; exactly one HTTP call."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = universe_provider.fetch_universe()

        assert mock_get.call_count == 1, (
            f"Expected exactly 1 HTTP call (no pagination); got {mock_get.call_count}. "
            "The Alpaca /v2/assets us_equity response is a single flat array."
        )
        assert result.get("available") is True, (
            f"Expected available=True on successful fetch; got {result!r}"
        )

    def test_trading_host_constant_distinct_from_data_host(self):
        """ALPACA_TRADING_BASE_URL must exist and must differ from ALPACA_BASE_URL.

        Conflating the two hosts is the most dangerous implementation mistake —
        paper keys 401 on the live data host path (api.alpaca.markets/v2/assets).
        """
        import synthetic_history
        from advisors import universe_provider

        assert hasattr(universe_provider, "ALPACA_TRADING_BASE_URL"), (
            "universe_provider must define ALPACA_TRADING_BASE_URL as a named constant"
        )
        assert hasattr(synthetic_history, "ALPACA_BASE_URL"), (
            "Precondition: synthetic_history.ALPACA_BASE_URL must exist"
        )
        assert universe_provider.ALPACA_TRADING_BASE_URL != synthetic_history.ALPACA_BASE_URL, (
            f"ALPACA_TRADING_BASE_URL ({universe_provider.ALPACA_TRADING_BASE_URL!r}) "
            f"must be DISTINCT from synthetic_history.ALPACA_BASE_URL "
            f"({synthetic_history.ALPACA_BASE_URL!r}). "
            "The trading host is paper-api.alpaca.markets; the data host is data.alpaca.markets."
        )
        assert "paper-api.alpaca.markets" in universe_provider.ALPACA_TRADING_BASE_URL, (
            f"ALPACA_TRADING_BASE_URL should point to the paper host; got "
            f"{universe_provider.ALPACA_TRADING_BASE_URL!r}"
        )


# ---------------------------------------------------------------------------
# AC-2: Filter correctness — tradable + exchange, ETFs kept, NO ranking
# ---------------------------------------------------------------------------


class TestFilterCorrectness:
    """AC-2: membership filter is exactly tradable==True AND exchange in allowed set."""

    def test_filter_keeps_tradable_listed_exchanges_drops_otc_and_untradable(
        self, asset_fixture, isolated_cache, alpaca_env
    ):
        """Survivors == expected set: OTC, untradable, unknown-exchange all dropped."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        expected = _expected_symbols(asset_fixture)

        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            result = universe_provider.fetch_universe()

        assert result.get("available") is True, f"Expected available=True; got {result!r}"
        symbols: set = result["symbols"]

        assert symbols == expected, (
            f"Survivor set mismatch.\n"
            f"  Expected ({len(expected)}): {sorted(expected)}\n"
            f"  Got      ({len(symbols)}): {sorted(symbols)}\n"
            f"  Extra (should not survive): {sorted(symbols - expected)}\n"
            f"  Missing (should survive):   {sorted(expected - symbols)}"
        )

    def test_filter_drops_otc_symbol(self, asset_fixture, isolated_cache, alpaca_env):
        """OTC_TRADABLE (tradable=True but exchange=OTC) must not survive."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            result = universe_provider.fetch_universe()

        assert "OTC_TRADABLE" not in result["symbols"], (
            "OTC_TRADABLE slipped through the exchange filter — OTC must be excluded"
        )

    def test_filter_drops_untradable_nasdaq_symbol(self, asset_fixture, isolated_cache, alpaca_env):
        """UNTRADABLE_TICK (tradable=False, exchange=NASDAQ) must not survive."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            result = universe_provider.fetch_universe()

        assert "UNTRADABLE_TICK" not in result["symbols"], (
            "UNTRADABLE_TICK slipped through — tradable=False must be excluded"
        )

    def test_filter_drops_nyse_arca_alias(self, asset_fixture, isolated_cache, alpaca_env):
        """NYSE_ARCA_TICK has exchange='NYSE ARCA' (alias) — must NOT survive.

        The allowed set is {NASDAQ, NYSE, ARCA, BATS, AMEX} with exact string
        matching. 'NYSE ARCA' is NOT 'ARCA'. A lazy implementation using 'in'
        substring matching would incorrectly admit this.
        """
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            result = universe_provider.fetch_universe()

        assert "NYSE_ARCA_TICK" not in result["symbols"], (
            "NYSE_ARCA_TICK slipped through — exchange='NYSE ARCA' is NOT in the "
            "allowed set {NASDAQ, NYSE, ARCA, BATS, AMEX}. Exact string match required."
        )

    def test_filter_keeps_all_etfs_including_leveraged(
        self, asset_fixture, isolated_cache, alpaca_env
    ):
        """SPY (NYSE ETF), TLT (ARCA ETF), and SPXL (NASDAQ leveraged ETF) must all survive.

        ETFs and leveraged/inverse ETFs are first-class on Composer — no class-based
        exclusion. Only tradable==False or OTC/unknown exchange drops a record.
        """
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            result = universe_provider.fetch_universe()

        symbols = result["symbols"]
        assert "SPY" in symbols, "SPY (NYSE ETF) must survive the filter"
        assert "TLT" in symbols, "TLT (ARCA ETF) must survive the filter"
        assert "SPXL" in symbols, (
            "SPXL (NASDAQ leveraged ETF) must survive — leveraged/inverse ETFs are kept"
        )

    def test_result_is_unordered_set_no_ranking_keys(
        self, asset_fixture, isolated_cache, alpaca_env
    ):
        """symbols must be a set/frozenset (unordered); result must carry no ranking keys.

        Ranking, dollar-volume, and top-N are explicitly OUT OF SCOPE (AC-2 / design
        correction 2026-06-20). Any 'rank', 'score', 'dollar_volume', 'top_n', or
        'palette' key in the result is a scope violation.
        """
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            result = universe_provider.fetch_universe()

        symbols = result["symbols"]
        assert isinstance(symbols, (set, frozenset)), (
            f"symbols must be a set or frozenset (unordered); got {type(symbols)}"
        )

        forbidden_keys = {"rank", "score", "dollar_volume", "top_n", "palette", "sorted"}
        present_forbidden = forbidden_keys & set(result.keys())
        assert not present_forbidden, (
            f"Result contains forbidden ranking/ordering keys: {present_forbidden}. "
            "Component 1 is membership-only — no ranking, no dollar-volume, no top-N."
        )

    def test_no_top_n_cap_all_records_survive(self, isolated_cache, alpaca_env):
        """A 100-record batch (all tradable, all NASDAQ) must yield exactly 100 survivors.

        Catches any hardcoded top-N that doesn't appear on the small 12-record fixture
        (e.g. universe[:10] from the old template stamper).
        """
        from advisors import universe_provider

        large_batch = [
            {
                "id": f"fake-id-{i:04d}",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "symbol": f"SYM{i:04d}",
                "name": f"Test Ticker {i}",
                "status": "active",
                "tradable": True,
                "marginable": True,
                "maintenance_margin_requirement": 30.0,
                "margin_requirement_long": "0%",
                "margin_requirement_short": "0%",
                "shortable": True,
                "easy_to_borrow": True,
                "borrow_status": "Easy To Borrow",
                "fractionable": False,
                "attributes": [],
            }
            for i in range(100)
        ]

        with patch("requests.get", return_value=_make_mock_response(json_body=large_batch)):
            result = universe_provider.fetch_universe()

        assert result.get("available") is True
        assert len(result["symbols"]) == 100, (
            f"Expected all 100 tradable NASDAQ records to survive; got {len(result['symbols'])}. "
            "There must be NO top-N cap in the filter — the old universe[:10] pattern is banned."
        )


# ---------------------------------------------------------------------------
# AC-3: Weekly cache via atlas_cache pattern
# ---------------------------------------------------------------------------


class TestWeeklyCache:
    """AC-3: fetch routes through atlas_cache; second call within TTL skips HTTP."""

    def test_second_call_within_ttl_does_not_refetch(
        self, asset_fixture, isolated_cache, alpaca_env
    ):
        """Two consecutive fetch_universe() calls must fire exactly one HTTP request."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            universe_provider.fetch_universe()
            universe_provider.fetch_universe()  # second call — should be a cache HIT

        assert mock_get.call_count == 1, (
            f"Expected exactly 1 HTTP call across two fetch_universe() invocations; "
            f"got {mock_get.call_count}. "
            "The second call must be served from the atlas_cache without a new HTTP request."
        )

    def test_force_refresh_bypasses_cache(self, asset_fixture, isolated_cache, alpaca_env):
        """force_refresh=True must cause a live HTTP call even when a fresh cache row exists."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            universe_provider.fetch_universe()  # populates cache
            universe_provider.fetch_universe(force_refresh=True)  # must bypass cache

        assert mock_get.call_count == 2, (
            f"Expected 2 HTTP calls (initial + force_refresh); got {mock_get.call_count}. "
            "force_refresh=True must bypass the cache and issue a new request."
        )

    def test_cache_ttl_is_at_most_7_days(self, asset_fixture, isolated_cache, alpaca_env):
        """The cache TTL passed to atlas_cache.cached_pull must be <= 7 days.

        Bill-protection directive: the Alpaca rate limit is 200 req/window, and
        operators must not be surprised by re-fetches more often than weekly.
        """
        from advisors import atlas_cache

        raw_assets = _fixture_asset_array(asset_fixture)
        mock_resp = _make_mock_response(json_body=raw_assets)

        with patch("requests.get", return_value=mock_resp):
            with patch.object(atlas_cache, "cached_pull", wraps=atlas_cache.cached_pull) as spy:
                from advisors import universe_provider

                universe_provider.fetch_universe()

        assert spy.called, "fetch_universe must route through atlas_cache.cached_pull"
        # Extract ttl_days kwarg from the spy call.
        call_kwargs = spy.call_args[1] if spy.call_args[1] else {}
        ttl_days = call_kwargs.get("ttl_days", 7)  # default 7 if not passed — also acceptable
        assert ttl_days <= 7, (
            f"TTL {ttl_days} days exceeds the 7-day bill-protection cap. "
            "Universe fetches must not occur more often than weekly (ttl_days <= 7)."
        )


# ---------------------------------------------------------------------------
# AC-4: Membership lookup — is_tradeable + get_tradeable_set
# ---------------------------------------------------------------------------


class TestMembershipLookup:
    """AC-4: is_tradeable and get_tradeable_set are the only query surfaces."""

    @pytest.fixture()
    def warm_cache(self, asset_fixture, isolated_cache, alpaca_env):
        """Populate the cache once and return the expected survivor set."""
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            universe_provider.fetch_universe()

        return _expected_symbols(asset_fixture)

    def test_in_set_ticker_returns_true(self, warm_cache, asset_fixture, alpaca_env):
        """is_tradeable returns True for a known survivor ticker."""
        from advisors import universe_provider

        # Pick any known survivor from the fixture metadata.
        a_survivor = next(iter(warm_cache))
        with patch("requests.get") as mock_get:
            result = universe_provider.is_tradeable(a_survivor)

        assert result is True, f"Expected is_tradeable({a_survivor!r}) == True; got {result!r}"
        # No extra HTTP call after cache warm.
        assert mock_get.call_count == 0, (
            f"is_tradeable fired an HTTP request after cache was already warm "
            f"({mock_get.call_count} calls). Membership must be served from cache."
        )

    def test_off_set_ticker_returns_false(self, warm_cache, alpaca_env):
        """is_tradeable returns False for a ticker not in the membership set."""
        from advisors import universe_provider

        with patch("requests.get") as mock_get:
            result = universe_provider.is_tradeable("DOESNOTEXIST_XYZ")

        assert result is False, "Expected is_tradeable('DOESNOTEXIST_XYZ') == False; got {result!r}"
        assert mock_get.call_count == 0, (
            "is_tradeable fired an HTTP request for a known-absent ticker — cache not used"
        )

    def test_is_tradeable_returns_bool_not_truthy(self, warm_cache, alpaca_env):
        """is_tradeable must return a Python bool, not a truthy/falsy container."""
        from advisors import universe_provider

        a_survivor = next(iter(warm_cache))
        result_true = universe_provider.is_tradeable(a_survivor)
        result_false = universe_provider.is_tradeable("DOESNOTEXIST_XYZ")

        assert result_true is True, (
            f"is_tradeable({a_survivor!r}) must return the bool True, got {result_true!r}"
        )
        assert result_false is False, (
            f"is_tradeable('DOESNOTEXIST_XYZ') must return the bool False, got {result_false!r}"
        )

    def test_membership_served_from_cache_no_new_http(self, warm_cache, alpaca_env):
        """10 is_tradeable calls after cache warm must not trigger any HTTP request."""
        from advisors import universe_provider

        symbols_to_check = list(warm_cache) + ["NOTREAL_1", "NOTREAL_2", "NOTREAL_3"]
        with patch("requests.get") as mock_get:
            for sym in symbols_to_check[:10]:
                universe_provider.is_tradeable(sym)

        assert mock_get.call_count == 0, (
            f"Expected 0 HTTP calls for 10 is_tradeable() calls after cache warm; "
            f"got {mock_get.call_count}. Membership must be served entirely from cache."
        )

    def test_get_tradeable_set_returns_full_set(self, warm_cache, alpaca_env, asset_fixture):
        """get_tradeable_set() returns a set/frozenset equal to the fixture survivor set."""
        from advisors import universe_provider

        with patch("requests.get"):
            result_set = universe_provider.get_tradeable_set()

        expected = _expected_symbols(asset_fixture)
        assert isinstance(result_set, (set, frozenset)), (
            f"get_tradeable_set() must return a set or frozenset; got {type(result_set)}"
        )
        assert result_set == expected, (
            f"get_tradeable_set() mismatch.\n"
            f"  Expected: {sorted(expected)}\n"
            f"  Got:      {sorted(result_set)}"
        )

    def test_is_tradeable_force_refresh_triggers_new_http_call(
        self, asset_fixture, isolated_cache, alpaca_env
    ):
        """is_tradeable(..., force_refresh=True) must fire a new HTTP call, not serve stale cache.

        is_tradeable delegates to fetch_universe(force_refresh=...) — this test verifies
        that force_refresh is wired through, not silently dropped.  A lazy implementation
        that ignores force_refresh in is_tradeable would pass the cold-cache tests but
        serve a stale membership set even when the caller explicitly demands a refresh.
        """
        from advisors import universe_provider

        raw_assets = _fixture_asset_array(asset_fixture)
        expected = _expected_symbols(asset_fixture)
        a_survivor = next(iter(expected))

        mock_resp = _make_mock_response(json_body=raw_assets)
        with patch("requests.get", return_value=mock_resp) as mock_get:
            # First call: populate cache.
            universe_provider.fetch_universe()
            assert mock_get.call_count == 1

            # Second call via is_tradeable with force_refresh=True: must hit HTTP again.
            result = universe_provider.is_tradeable(a_survivor, force_refresh=True)

        assert mock_get.call_count == 2, (
            f"Expected 2 HTTP calls (initial + is_tradeable force_refresh); "
            f"got {mock_get.call_count}. "
            "force_refresh=True must be threaded through is_tradeable -> fetch_universe."
        )
        # Result must still be the correct bool.
        assert result is True, (
            f"is_tradeable({a_survivor!r}, force_refresh=True) returned {result!r}; expected True"
        )

    def test_is_tradeable_never_raises_on_fetch_failure(self, isolated_cache, alpaca_env):
        """is_tradeable must return False (not raise) when the underlying fetch fails.

        is_tradeable is documented as 'never raises'.  A Timeout inside fetch_universe
        must degrade to False, not propagate an exception to the caller.  This closes
        the gap where fetch_universe returns available=False but is_tradeable might
        naively try to access result['symbols'] on a failure dict that has an empty set.
        """
        import requests

        from advisors import universe_provider

        with patch("requests.get", side_effect=requests.Timeout("simulated timeout")):
            try:
                result = universe_provider.is_tradeable("AAPL")
            except Exception as exc:
                pytest.fail(
                    f"is_tradeable raised {type(exc).__name__} on fetch failure — "
                    "violates 'never raises' contract (AC-4 + D-1)"
                )

        assert result is False, f"is_tradeable must return False when fetch fails; got {result!r}"


# ---------------------------------------------------------------------------
# AC-5: D-1 / never-raises — every failure mode degrades gracefully
# ---------------------------------------------------------------------------


class TestD1NeverRaises:
    """AC-5: all failure modes → available=False + reason, never raises, never leaks keys."""

    def _assert_graceful_degradation(self, result: dict, context: str) -> None:
        """Shared assertion: available=False + reason present, no exception propagation."""
        assert isinstance(result, dict), f"{context}: expected dict result; got {type(result)}"
        assert result.get("available") is False, (
            f"{context}: expected available=False; got available={result.get('available')!r}"
        )
        assert "reason" in result, (
            f"{context}: expected 'reason' key in result; got keys={list(result.keys())}"
        )
        assert isinstance(result["reason"], str), (
            f"{context}: reason must be a string; got {type(result['reason'])}"
        )
        assert result["reason"], f"{context}: reason must be a non-empty string"

    def test_wrong_host_401_returns_available_false(self, isolated_cache, alpaca_env):
        """A 401 HTTP response (paper keys hitting wrong host) → available=False + reason."""
        from advisors import universe_provider

        mock_resp = _make_mock_response(status_code=401, text_body="Unauthorized")
        with patch("requests.get", return_value=mock_resp):
            try:
                result = universe_provider.fetch_universe()
            except Exception as exc:
                pytest.fail(
                    f"fetch_universe raised {type(exc).__name__} on 401 — "
                    "violates D-1 never-raises contract"
                )

        self._assert_graceful_degradation(result, "401 wrong-host")

    def test_timeout_returns_available_false(self, isolated_cache, alpaca_env):
        """requests.Timeout → available=False; reason must be the exception class name only."""
        import requests

        from advisors import universe_provider

        with patch("requests.get", side_effect=requests.Timeout("connection timed out")):
            try:
                result = universe_provider.fetch_universe()
            except Exception as exc:
                pytest.fail(
                    f"fetch_universe raised {type(exc).__name__} on Timeout — "
                    "violates D-1 never-raises contract"
                )

        self._assert_graceful_degradation(result, "Timeout")
        # D-1: reason must be exception class name only (no message, no path, no key).
        assert result["reason"] == "Timeout", (
            f"D-1: reason on Timeout must be 'Timeout' (class name only); "
            f"got {result['reason']!r}. Full exception message must not leak."
        )

    def test_empty_array_response_returns_available_false(self, isolated_cache, alpaca_env):
        """HTTP 200 with an empty JSON array [] → available=False (honest empty-state).

        A naive implementation might return available=True with an empty set — the spec
        requires honest degradation: an empty universe is not a valid state.
        """
        from advisors import universe_provider

        mock_resp = _make_mock_response(json_body=[])
        with patch("requests.get", return_value=mock_resp):
            try:
                result = universe_provider.fetch_universe()
            except Exception as exc:
                pytest.fail(
                    f"fetch_universe raised {type(exc).__name__} on empty array — "
                    "violates D-1 never-raises contract"
                )

        self._assert_graceful_degradation(result, "empty array []")

    def test_malformed_json_returns_available_false(self, isolated_cache, alpaca_env):
        """HTTP 200 with non-JSON body → available=False + reason class name; no raise."""
        from advisors import universe_provider

        mock_resp = _make_mock_response(status_code=200, text_body="not-json-at-all{{{")
        # Make json() raise so the module must handle the parse error.
        mock_resp.json.side_effect = ValueError("No JSON object could be decoded")

        with patch("requests.get", return_value=mock_resp):
            try:
                result = universe_provider.fetch_universe()
            except Exception as exc:
                pytest.fail(
                    f"fetch_universe raised {type(exc).__name__} on malformed JSON — "
                    "violates D-1 never-raises contract"
                )

        self._assert_graceful_degradation(result, "malformed JSON")

    def test_connection_error_returns_available_false(self, isolated_cache, alpaca_env):
        """requests.ConnectionError → available=False; never raises."""
        import requests

        from advisors import universe_provider

        with patch(
            "requests.get",
            side_effect=requests.ConnectionError("Failed to establish connection"),
        ):
            try:
                result = universe_provider.fetch_universe()
            except Exception as exc:
                pytest.fail(
                    f"fetch_universe raised {type(exc).__name__} on ConnectionError — "
                    "violates D-1 never-raises contract"
                )

        self._assert_graceful_degradation(result, "ConnectionError")

    @pytest.mark.parametrize(
        "failure_description,side_effect_or_resp",
        [
            ("401_wrong_host", ("status_code", 401)),
            ("timeout", ("raises", "Timeout")),
            ("empty_array", ("json_body", [])),
            ("malformed_json", ("bad_json", None)),
        ],
    )
    def test_no_api_key_leaked_in_failure_reason(
        self, isolated_cache, alpaca_env, failure_description, side_effect_or_resp
    ):
        """For every failure mode, ALPACA_KEY and ALPACA_SECRET must not appear in reason.

        D-1: the reason string contains ONLY the exception class name — no message,
        no path, no credential value.
        """
        import requests

        from advisors import universe_provider

        key_value = os.environ["ALPACA_KEY"]
        secret_value = os.environ["ALPACA_SECRET"]

        kind, value = side_effect_or_resp
        if kind == "status_code":
            mock_resp = _make_mock_response(status_code=value, text_body="error")
            patch_ctx = patch("requests.get", return_value=mock_resp)
        elif kind == "raises":
            exc_cls = getattr(requests, value, Exception)
            patch_ctx = patch("requests.get", side_effect=exc_cls("secret: " + key_value))
        elif kind == "json_body":
            mock_resp = _make_mock_response(json_body=value)
            patch_ctx = patch("requests.get", return_value=mock_resp)
        else:  # bad_json
            mock_resp = _make_mock_response(status_code=200, text_body="bad")
            mock_resp.json.side_effect = ValueError("decode error: " + key_value)
            patch_ctx = patch("requests.get", return_value=mock_resp)

        with patch_ctx:
            try:
                result = universe_provider.fetch_universe()
            except Exception:
                return  # Already caught by other tests; here we only care about the reason

        reason = result.get("reason", "")
        assert key_value not in reason, (
            f"[{failure_description}] ALPACA_KEY value found in reason={reason!r}. "
            "D-1: reason must contain only the exception class name."
        )
        assert secret_value not in reason, (
            f"[{failure_description}] ALPACA_SECRET value found in reason={reason!r}. "
            "D-1: reason must contain only the exception class name."
        )


# ---------------------------------------------------------------------------
# AC-6: Warehouse persistence + no cross-DB imports
# ---------------------------------------------------------------------------


class TestWarehousePersistence:
    """AC-6: fresh fetch writes a warehouse snapshot; no state/optimization DB import."""

    def test_fresh_fetch_persists_snapshot_to_warehouse(
        self, asset_fixture, isolated_cache, alpaca_env, tmp_path
    ):
        """A successful fetch must write a row to the warehouse third-DB (lens_snapshots)."""
        from advisors import lens_warehouse, universe_provider

        warehouse_path = str(tmp_path / "test_warehouse.db")

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            universe_provider.fetch_universe(db_path=warehouse_path)

        rows = lens_warehouse.get_lens_snapshots(lens="universe_provider", db_path=warehouse_path)
        assert len(rows) >= 1, (
            f"Expected >= 1 warehouse snapshot row after a fresh fetch; got {len(rows)}. "
            "Every fresh universe fetch must persist a snapshot to the warehouse DB "
            "(third-DB pattern, lens_warehouse.py style)."
        )
        assert rows[0]["available"] == 1, (
            f"Warehouse row available should be 1 (True) after a successful fetch; "
            f"got {rows[0]['available']!r}"
        )

    def test_warehouse_snapshot_does_not_contain_api_key(
        self, asset_fixture, isolated_cache, alpaca_env, tmp_path
    ):
        """The warehouse raw_json must not contain the ALPACA_KEY value (_strip_secrets)."""
        from advisors import lens_warehouse, universe_provider

        warehouse_path = str(tmp_path / "test_warehouse_secrets.db")
        key_value = os.environ["ALPACA_KEY"]

        raw_assets = _fixture_asset_array(asset_fixture)
        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            universe_provider.fetch_universe(db_path=warehouse_path)

        rows = lens_warehouse.get_lens_snapshots(lens="universe_provider", db_path=warehouse_path)
        for row in rows:
            raw_json_str = row.get("raw_json", "")
            assert key_value not in raw_json_str, (
                "ALPACA_KEY value found in warehouse raw_json — credential leak! "
                "The warehouse write must apply _strip_secrets before persisting."
            )

    def test_warehouse_snapshot_contains_symbol_set_for_drift_reconstruction(
        self, asset_fixture, isolated_cache, alpaca_env, tmp_path
    ):
        """The warehouse raw_json must carry the full symbol set, not just a count.

        Week-over-week drift (tickers added/removed) is only reconstructable if the
        persisted payload contains the actual symbols.  A payload of {"symbol_count": N}
        alone is insufficient — an operator cannot tell which symbols were present.

        Contract (per PM directive): raw_payload must include at minimum:
          {"symbols": <sorted list of symbol strings>, "symbol_count": <int>}

        This test fetches the fixture universe, reads back the snapshot row, parses
        raw_json, and asserts every expected survivor symbol is present in the stored
        "symbols" list.  It is deliberately blind to list ordering (sorted or not) —
        only membership matters for drift reconstruction.
        """
        from advisors import lens_warehouse, universe_provider

        warehouse_path = str(tmp_path / "test_warehouse_drift.db")
        expected = _expected_symbols(asset_fixture)
        raw_assets = _fixture_asset_array(asset_fixture)

        with patch("requests.get", return_value=_make_mock_response(json_body=raw_assets)):
            universe_provider.fetch_universe(db_path=warehouse_path)

        rows = lens_warehouse.get_lens_snapshots(lens="universe_provider", db_path=warehouse_path)
        assert len(rows) >= 1, (
            "No warehouse row written — cannot check drift-reconstruction payload"
        )

        raw_json_str = rows[0]["raw_json"]
        try:
            payload = json.loads(raw_json_str)
        except json.JSONDecodeError as exc:
            pytest.fail(f"Warehouse raw_json is not valid JSON: {exc}\nraw_json={raw_json_str!r}")

        assert "symbols" in payload, (
            f"Warehouse payload is missing the 'symbols' key — drift reconstruction "
            f"requires the full symbol set, not just a count. Got keys: {list(payload.keys())}"
        )

        stored_symbols = set(payload["symbols"])
        missing_from_store = expected - stored_symbols
        assert not missing_from_store, (
            f"The following expected survivor symbols are absent from the warehouse "
            f"snapshot's 'symbols' list — drift reconstruction is incomplete:\n"
            f"  Missing: {sorted(missing_from_store)}\n"
            f"  Stored:  {sorted(stored_symbols)}"
        )

        # symbol_count must agree with the stored list length (self-consistency).
        assert "symbol_count" in payload, (
            f"Warehouse payload is missing 'symbol_count'. Got keys: {list(payload.keys())}"
        )
        assert payload["symbol_count"] == len(stored_symbols), (
            f"symbol_count={payload['symbol_count']} does not match "
            f"len(symbols)={len(stored_symbols)} — payload is internally inconsistent"
        )

    def test_module_does_not_import_database_at_ast_level(self):
        """advisors/universe_provider.py must NOT import database.py (no state-DB join).

        The third-DB pattern (lens_warehouse) is strictly isolated from the state DB.
        An AST-level import of 'database' in universe_provider.py is a hard violation
        of Architecture Constraint #3: no cross-DB joins.
        """
        module_path = _REPO_ROOT / "advisors" / "universe_provider.py"
        if not module_path.exists():
            pytest.skip("advisors/universe_provider.py stub not yet written")

        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    # 'database' alone (the state DB module) is forbidden.
                    # 'lens_warehouse' and 'atlas_cache' are explicitly permitted.
                    if name == "database" or name.startswith("database."):
                        violations.append(
                            f"Line {node.lineno}: forbidden import '{name}' "
                            f"(state DB — no cross-DB join)"
                        )

        assert not violations, (
            "universe_provider.py contains forbidden state-DB imports (AC-6):\n"
            + "\n".join(violations)
        )

    def test_module_does_not_import_optimization_db_at_ast_level(self):
        """advisors/universe_provider.py must NOT import autotuner or the optimization DB.

        The universe provider is a standalone fetch-and-cache module; it has no
        business dependency on the Optuna optimization DB.
        """
        module_path = _REPO_ROOT / "advisors" / "universe_provider.py"
        if not module_path.exists():
            pytest.skip("advisors/universe_provider.py stub not yet written")

        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_prefixes = ("autotuner", "optuna")
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    name_lower = (name or "").lower()
                    for prefix in forbidden_prefixes:
                        if name_lower.startswith(prefix):
                            violations.append(
                                f"Line {node.lineno}: forbidden import '{name}' "
                                f"(optimization DB — no cross-DB join)"
                            )

        assert not violations, (
            "universe_provider.py contains forbidden optimization-DB imports (AC-6):\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Anti-recurrence guard: importlib.reload-per-test is a memory-leak anti-pattern
# (it installs a NEW module object each call while other already-imported modules
# keep references to the OLD one — leaking a whole module per test across a
# multi-file run, which OOMs single-process full-tree verification). This file
# was the first to leak it; this guard keeps it from creeping back into THIS file.
# Isolation here is env-var-only (ATLAS_CACHE_DB_PATH via monkeypatch + tmp_path).
# ---------------------------------------------------------------------------


def test_no_importlib_reload_in_this_test_module():
    """Guard: this test module must not call importlib.reload (memory-leak anti-pattern).

    Detected via AST (a call to an attribute named 'reload' on a name/attr 'importlib'),
    so a docstring or comment mentioning the word does not trip it.
    """
    module_path = pathlib.Path(__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "reload":
                target = node.func.value
                root = (
                    target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                )
                if root == "importlib" or node.func.attr == "reload":
                    offenders.append(node.lineno)

    assert not offenders, (
        "importlib.reload(...) found in test_universe_provider.py at lines "
        f"{offenders} — this is a per-test memory-leak anti-pattern. Isolate via "
        "monkeypatch.setenv(ATLAS_CACHE_DB_PATH) + tmp_path instead (atlas_cache reads "
        "the path at call time; universe_provider has no module-level mutable cache)."
    )
