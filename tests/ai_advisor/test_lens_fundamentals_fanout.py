"""
Fundamentals lens — portfolio fan-out fix (RED tests).

Tests pin the defect and drive the AC-1..6 fix:
  `_build_fundamentals_section()` (no ticker) currently short-circuits
  available=False / "ticker symbol required..." because both production callers
  invoke `builder()` with no ticker. These tests drive the fix: derive a company
  universe internally, fan out per-ticker SEC EDGAR calls, and aggregate.

Mocking strategy
----------------
* `requests.get` patched at ai_advisor level — prevents all live SEC calls.
* `database.load_state` patched to control the live-holdings universe
  (returns empty dict for the "empty-holdings → proxy floor" cases).
* Per-ticker CIK lookup is bypassed by patching requests.get to return a
  plausible CIK-tickers-JSON response for unknown tickers and the fixture
  companyfacts shape for companyfacts fetches.
* `_FUNDAMENTALS_PROXY_UNIVERSE` constant asserted directly — tests check the
  module-level attribute, not a patched value.
* Math engine NEVER mocked — not used by these tests.
* No live SEC calls in ANY test — the -m filter enforces this.

NEVER hardcode financial literal values (Revenues, NetIncome, etc.) as expected
values — all assertions derive from the fixture shape or assert presence/format.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "math"

# Known ETF tickers that MUST NOT appear in the proxy constant (no companyfacts)
_KNOWN_ETFS = frozenset(
    {"SPY", "QQQ", "IWM", "GLD", "TLT", "EFA", "AGG", "XLF", "XLE", "XLV", "XLI", "SHY", "BIL"}
)


@pytest.fixture
def sec_shape_fixture() -> dict:
    """Captured SEC EDGAR companyfacts response shape — schema-derived (Cycle 2).

    Reuses the existing fixture from the cycle-2 test suite to avoid
    fixture proliferation (same source of truth).
    """
    path = _FIXTURES_DIR / "sec_edgar_fundamentals_api_shape.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


def _make_companyfacts_response(
    ticker: str = "AAPL",
    cik: int = 320193,
    entity_name: str | None = None,
    with_revenues: bool = True,
) -> dict:
    """Build a minimal companyfacts-shaped dict for a given ticker.

    Values are test sentinels — NOT hardcoded producer outputs.
    The shape mirrors sec_edgar_fundamentals_api_shape.json.
    """
    name = entity_name or f"{ticker} Corp"
    facts: dict[str, Any] = {"us-gaap": {}}
    if with_revenues:
        facts["us-gaap"]["Revenues"] = {
            "label": "Revenue",
            "units": {
                "USD": [
                    {
                        "accn": f"0000{cik:06d}-24-000001",
                        "end": "2024-09-28",
                        "val": 391035000000,  # sentinel — not asserted literally
                        "form": "10-K",
                        "filed": "2024-11-01",
                    }
                ]
            },
        }
        facts["us-gaap"]["Assets"] = {
            "label": "Assets",
            "units": {
                "USD": [
                    {
                        "accn": f"0000{cik:06d}-24-000001",
                        "end": "2024-09-28",
                        "val": 364980000000,  # sentinel
                        "form": "10-K",
                        "filed": "2024-11-01",
                    }
                ]
            },
        }
    return {"cik": cik, "entityName": name, "facts": facts}


def _make_empty_holdings_state() -> dict:
    """Simulate database.load_state() when symphonies hold nothing (03:00 / flat)."""
    return {}


def _make_holdings_state(tickers: list[str]) -> dict:
    """Simulate database.load_state() with holdings containing given tickers."""
    return {
        "symphony_alpha": {"logic_holdings": {t: {"weight": 1.0 / len(tickers)} for t in tickers}}
    }


# ---------------------------------------------------------------------------
# Runtime validator: verify fixture shape is usable by the producer
# ---------------------------------------------------------------------------


def _validate_fixture_companyfacts_shape(shape: dict) -> None:
    """Assert the fixture has the keys the producer will actually consume.

    This is the runtime validator mandated by the quant-test-writer rules:
    fixture provenance must be validated, not assumed.
    """
    assert "cik" in shape, "fixture companyfacts must have 'cik'"
    assert "entityName" in shape, "fixture companyfacts must have 'entityName'"
    assert "facts" in shape, "fixture companyfacts must have 'facts'"
    assert "us-gaap" in shape["facts"], "fixture 'facts' must have 'us-gaap' key"


# ---------------------------------------------------------------------------
# AC-6b/6c — proxy constant: module-level attribute checks
# (put first so implementer knows the fixture they need to satisfy)
# ---------------------------------------------------------------------------


class TestNamedProxyConstant:
    """_FUNDAMENTALS_PROXY_UNIVERSE must be a named module-level constant."""

    def test_named_proxy_constant_present(self):
        """_FUNDAMENTALS_PROXY_UNIVERSE exists as a module-level attribute in ai_advisor.

        FAILS until the implementer adds `_FUNDAMENTALS_PROXY_UNIVERSE` to ai_advisor.
        The constant is the named floor that guarantees the nightly Prism always has
        a real fundamentals universe even when holdings are empty (AC-2).
        """
        import ai_advisor

        assert hasattr(ai_advisor, "_FUNDAMENTALS_PROXY_UNIVERSE"), (
            "ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE is missing. "
            "AC-2 requires a named module-level company-ticker floor constant "
            "(not computed at call time, not a local variable). "
            "Add: _FUNDAMENTALS_PROXY_UNIVERSE = frozenset({'AAPL', 'MSFT', ...})"
        )
        proxy = ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE
        assert hasattr(proxy, "__iter__"), (
            "_FUNDAMENTALS_PROXY_UNIVERSE must be iterable (frozenset, set, or list). "
            f"Got {type(proxy).__name__!r}."
        )
        assert len(proxy) > 0, (
            "_FUNDAMENTALS_PROXY_UNIVERSE must be non-empty — it is a floor, "
            "not a placeholder. At minimum ~4 large-cap company tickers."
        )

    def test_named_proxy_constant_contains_company_tickers_not_etfs(self):
        """_FUNDAMENTALS_PROXY_UNIVERSE must contain company tickers, NOT ETFs.

        ETFs (SPY, QQQ, IWM, etc.) have no SEC companyfacts entries — they would
        cause every CIK lookup to fail or return no key facts. The proxy must be
        individual company tickers (AAPL, MSFT, GOOGL, etc.).

        FAILS if the constant contains known ETF tickers.
        """
        import ai_advisor

        if not hasattr(ai_advisor, "_FUNDAMENTALS_PROXY_UNIVERSE"):
            pytest.skip("_FUNDAMENTALS_PROXY_UNIVERSE not yet defined — AC-6b fails first")

        proxy = frozenset(ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE)
        etfs_in_proxy = proxy & _KNOWN_ETFS
        assert not etfs_in_proxy, (
            f"_FUNDAMENTALS_PROXY_UNIVERSE contains ETF ticker(s): {sorted(etfs_in_proxy)}. "
            "ETFs have NO SEC companyfacts — they would cause every proxy fetch to fail. "
            "Use individual company tickers (AAPL, MSFT, GOOGL, AMZN, etc.) only."
        )

        # Also assert all entries look like ticker strings (short alphanumeric)
        for ticker in proxy:
            assert isinstance(ticker, str) and ticker.strip(), (
                f"_FUNDAMENTALS_PROXY_UNIVERSE entry {ticker!r} must be a non-empty string."
            )
            assert len(ticker) <= 10, (
                f"_FUNDAMENTALS_PROXY_UNIVERSE entry {ticker!r} looks too long for a ticker. "
                "Proxy entries must be ticker symbols, not company names."
            )


# ---------------------------------------------------------------------------
# AC-1 — the pinning RED test: no-ticker call fans out instead of short-circuiting
# ---------------------------------------------------------------------------


class TestPortfolioFanout:
    """_build_fundamentals_section() with no ticker must fan out across a universe."""

    def test_portfolio_call_fans_out_and_is_available(self, sec_shape_fixture: dict):
        """PINNING RED: _build_fundamentals_section() (no ticker) returns available=True.

        On the CURRENT (unfixed) implementation, this call immediately returns:
          {"available": False, "reason": "ticker symbol required..."}
        because the function checks `if not ticker` and short-circuits.

        The fix must instead:
          1. Derive a universe (holdings ∪ proxy floor).
          2. Fan out _fetch_fundamentals_for_ticker across each ticker.
          3. Aggregate results into available=True when ≥1 ticker resolves.

        This test MUST FAIL on the current implementation and PASS after the fix.
        """
        import ai_advisor

        _validate_fixture_companyfacts_shape(sec_shape_fixture["companyfacts_shape"])

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp),
        ):
            block = ai_advisor._build_fundamentals_section()

        assert block.get("available") is True, (
            "_build_fundamentals_section() (no ticker) returned available=False. "
            f"reason={block.get('reason')!r}. "
            "CURRENT DEFECT: the function short-circuits with 'ticker symbol required' "
            "when called without a ticker — but both production callers pass no ticker. "
            "The fix must derive a universe internally and fan out (AC-1). "
            f"Full block: {block!r}"
        )
        assert block.get("lens") == "fundamentals", (
            f"block['lens'] must equal 'fundamentals', got {block.get('lens')!r}"
        )

    def test_portfolio_payload_has_per_ticker_facts_and_coverage(self, sec_shape_fixture: dict):
        """Portfolio payload has per-ticker facts and a coverage count.

        AC-5: payload shape = {tickers: {TICKER: {...}}, coverage: {available: N, universe: M}}.
        Asserts presence/shape only — NEVER hardcoded financial values.

        FAILS until the fan-out is implemented and the aggregate payload is built.
        """
        import ai_advisor

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp),
        ):
            block = ai_advisor._build_fundamentals_section()

        if not block.get("available"):
            pytest.skip(
                "Portfolio fan-out not yet implemented (available=False) — "
                "AC-5 payload shape test cannot run until AC-1 passes."
            )

        payload = block.get("payload")
        assert isinstance(payload, dict), (
            f"portfolio fundamentals payload must be a dict, got {type(payload)!r}"
        )

        # Per-ticker facts sub-dict
        assert "tickers" in payload, (
            "portfolio payload must have 'tickers' key — "
            "a dict of per-ticker fundamental facts. "
            f"Got keys: {list(payload.keys())!r}"
        )
        assert isinstance(payload["tickers"], dict), (
            f"payload['tickers'] must be a dict, got {type(payload['tickers'])!r}"
        )
        assert len(payload["tickers"]) >= 1, (
            "payload['tickers'] must have at least one entry when available=True."
        )

        # Coverage count sub-dict
        assert "coverage" in payload, (
            "portfolio payload must have 'coverage' key — "
            "a dict with 'available' (count of resolved tickers) and 'universe' (total). "
            f"Got keys: {list(payload.keys())!r}"
        )
        coverage = payload["coverage"]
        assert isinstance(coverage, dict), (
            f"payload['coverage'] must be a dict, got {type(coverage)!r}"
        )
        assert "available" in coverage, (
            "payload['coverage'] must have 'available' (count of tickers that resolved). "
            f"Got keys: {list(coverage.keys())!r}"
        )
        assert "universe" in coverage, (
            "payload['coverage'] must have 'universe' (total tickers attempted). "
            f"Got keys: {list(coverage.keys())!r}"
        )
        assert isinstance(coverage["available"], int) and coverage["available"] >= 1, (
            f"coverage['available'] must be a positive int, got {coverage['available']!r}"
        )
        assert (
            isinstance(coverage["universe"], int) and coverage["universe"] >= coverage["available"]
        ), (
            f"coverage['universe'] ({coverage['universe']!r}) must be >= coverage['available'] "
            f"({coverage['available']!r})"
        )

    def test_no_composite_ratios_invented(self, sec_shape_fixture: dict):
        """Portfolio payload must NOT invent composite financial ratios.

        AC-5 / no-hardcoded-test-values rule: the producer may only expose raw
        XBRL facts per ticker. Synthetic ratios (P/E, debt/equity, revenue growth,
        etc.) would be producer-computed values — explicitly out of scope.

        FAILS if the payload contains ratio keys that aren't raw XBRL concept names.
        """
        import ai_advisor

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp),
        ):
            block = ai_advisor._build_fundamentals_section()

        if not block.get("available"):
            pytest.skip("Fan-out not yet implemented — skipping ratio-invention check.")

        payload = block.get("payload", {})

        # Synthetic/composite ratio keys that must NOT appear at portfolio level
        forbidden_ratio_keys = {
            "pe_ratio",
            "price_to_earnings",
            "debt_to_equity",
            "revenue_growth",
            "return_on_equity",
            "roe",
            "return_on_assets",
            "roa",
            "ebitda_margin",
            "current_ratio",
            "quick_ratio",
        }
        for ticker_block in payload.get("tickers", {}).values():
            if isinstance(ticker_block, dict):
                facts = ticker_block.get("key_facts", {})
                if isinstance(facts, dict):
                    invented = forbidden_ratio_keys & set(k.lower() for k in facts)
                    assert not invented, (
                        f"Portfolio payload invents composite ratio(s): {invented}. "
                        "Only raw XBRL facts (Revenues, Assets, etc.) are permitted — "
                        "no derived/computed values per AC-5 scope boundaries."
                    )


# ---------------------------------------------------------------------------
# AC-2 — universe derivation: holdings ∪ proxy floor
# ---------------------------------------------------------------------------


class TestUniverseDerivation:
    """Universe for the portfolio call must be holdings ∪ _FUNDAMENTALS_PROXY_UNIVERSE."""

    def test_empty_holdings_uses_proxy_floor(self, sec_shape_fixture: dict):
        """When holdings are empty, the proxy floor guarantees a non-empty universe.

        This is the 03:00 nightly Prism scenario (logic_holdings={} off-hours).
        The lens MUST return available=True even with empty holdings.

        FAILS on the current implementation (short-circuits regardless of holdings).
        Fails on a correct fan-out implementation if the proxy floor is absent.
        """
        import ai_advisor

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        # Empty holdings — exactly the 03:00 nightly state
        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp),
        ):
            block = ai_advisor._build_fundamentals_section()

        assert block.get("available") is True, (
            "_build_fundamentals_section() must return available=True even when "
            "holdings are empty (03:00/weekend/flat scenario). "
            "The _FUNDAMENTALS_PROXY_UNIVERSE floor must guarantee a non-empty universe. "
            f"Got: available={block.get('available')!r}, reason={block.get('reason')!r}"
        )

    def test_holdings_merged_with_proxy_floor(self, sec_shape_fixture: dict):
        """Holdings tickers are merged with the proxy floor (union, not replacement).

        AC-2: the universe is holdings ∪ proxy. Holdings tickers should appear in
        the coverage universe count alongside proxy tickers.

        FAILS until the fan-out merges holdings with the proxy floor.
        """
        import ai_advisor

        if not hasattr(ai_advisor, "_FUNDAMENTALS_PROXY_UNIVERSE"):
            pytest.skip("_FUNDAMENTALS_PROXY_UNIVERSE not defined — AC-6b fails first")

        extra_ticker = "NFLX"
        # NFLX is not in the standard proxy basket; add it via holdings
        holdings_state = _make_holdings_state([extra_ticker])
        companyfacts_resp = _make_mock_response(
            _make_companyfacts_response(
                ticker=extra_ticker, cik=1065280, entity_name="Netflix Inc."
            )
        )

        with (
            patch("database.load_state", return_value=holdings_state),
            patch("requests.get", return_value=companyfacts_resp),
        ):
            block = ai_advisor._build_fundamentals_section()

        if not block.get("available"):
            pytest.skip("Fan-out not yet implemented — skipping holdings-merge check.")

        coverage = (block.get("payload") or {}).get("coverage", {})
        proxy_size = len(ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE)
        universe_size = coverage.get("universe", 0)

        # The universe must be at least as large as the proxy (extra_ticker
        # may already be in the proxy; if so universe_size == proxy_size is fine)
        assert universe_size >= proxy_size, (
            f"coverage['universe'] ({universe_size}) must be >= proxy size ({proxy_size}). "
            "Holdings tickers must be merged INTO the proxy, not replacing it."
        )


# ---------------------------------------------------------------------------
# AC-3 — single-ticker path preserved (regression guard)
# ---------------------------------------------------------------------------


class TestSingleTickerPathPreserved:
    """_build_fundamentals_section(ticker='AAPL') must behave exactly as today."""

    def test_single_ticker_path_shape_unchanged(self, sec_shape_fixture: dict):
        """Single-ticker call returns the existing per-symphony shape.

        AC-3: the refactor must not break any per-symphony caller that passes
        a ticker. The returned block must have the same top-level keys as the
        pre-fix response.

        Baseline shape: {lens, available, payload: {entity_name, cik, key_facts}, sources}.
        """
        import ai_advisor

        mock_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp):
            block = ai_advisor._build_fundamentals_section(ticker="AAPL")

        assert block.get("lens") == "fundamentals", (
            f"single-ticker block['lens'] must equal 'fundamentals', got {block.get('lens')!r}"
        )
        assert isinstance(block.get("available"), bool), (
            "single-ticker block['available'] must be bool"
        )
        assert block.get("available") is True, (
            "single-ticker path (ticker='AAPL') with valid SEC mock must return "
            f"available=True. Got: {block!r}"
        )
        payload = block.get("payload", {})
        # Pre-fix payload keys that must still be present after refactor
        assert "entity_name" in payload, (
            "single-ticker payload must still carry 'entity_name' after refactor (AC-3)."
        )
        assert "key_facts" in payload, (
            "single-ticker payload must still carry 'key_facts' after refactor (AC-3)."
        )
        # Must NOT have 'tickers' or 'coverage' — those are portfolio-path keys
        assert "tickers" not in payload, (
            "single-ticker payload must NOT have 'tickers' key — "
            "that is the portfolio-path aggregate shape. "
            "AC-3: single-ticker shape must be preserved unchanged."
        )
        assert "coverage" not in payload, (
            "single-ticker payload must NOT have 'coverage' key — "
            "that is the portfolio-path aggregate shape. "
            "AC-3: single-ticker shape must be preserved unchanged."
        )

    def test_single_ticker_path_still_fetches_sec(self, sec_shape_fixture: dict):
        """Single-ticker path still makes an HTTP request to SEC EDGAR.

        After extracting _fetch_fundamentals_for_ticker, the single-ticker path
        must route through it and still hit SEC EDGAR (no accidental no-op).

        FAILS if the refactor accidentally bypasses the SEC fetch for ticker calls.
        """
        import ai_advisor

        mock_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp) as mock_get:
            ai_advisor._build_fundamentals_section(ticker="AAPL")

        assert mock_get.called, (
            "Single-ticker _build_fundamentals_section(ticker='AAPL') made no HTTP "
            "request. The refactor accidentally bypassed the SEC fetch. "
            "AC-3: the single-ticker body must route through the same SEC EDGAR "
            "fetch path as before."
        )
        # At least one call must have a User-Agent header (SEC requirement)
        found_ua = False
        for c in mock_get.call_args_list:
            headers = (c.kwargs or {}).get("headers", {})
            if headers.get("User-Agent") or headers.get("user-agent"):
                found_ua = True
                break
        assert found_ua, (
            "No SEC EDGAR request included a User-Agent header after refactor. "
            "The mandatory SEC User-Agent must be preserved (AC-3 / existing requirement)."
        )


# ---------------------------------------------------------------------------
# AC-4 — per-ticker honest degradation
# ---------------------------------------------------------------------------


class TestPerTickerDegradation:
    """A failing ticker degrades only itself; the lens stays available via others."""

    def test_per_ticker_failure_degrades_not_whole_lens(self, sec_shape_fixture: dict):
        """One ticker's 404 does not kill the whole lens.

        Scenario: two tickers in the universe. First ticker gets a 404 (ETF-like
        failure / unknown company). Second ticker succeeds. Lens must be
        available=True with the second ticker's facts present and the first absent.

        FAILS until per-ticker exception isolation is implemented in the fan-out.
        """
        import ai_advisor

        if not hasattr(ai_advisor, "_FUNDAMENTALS_PROXY_UNIVERSE"):
            pytest.skip("_FUNDAMENTALS_PROXY_UNIVERSE not defined — AC-6b fails first")

        # Use two known proxy tickers: first will fail, second will succeed.
        proxy_list = sorted(ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE)
        if len(proxy_list) < 2:
            pytest.skip("Proxy universe has < 2 tickers — cannot run 1-fail/1-pass test")

        ticker_fail = proxy_list[0]  # first alphabetically — will get 404
        ticker_pass = proxy_list[1]  # second — will succeed

        # Build a universe with exactly these two tickers by mocking load_state
        # to return empty (proxy floor provides them) — we control via the mock responses.
        pass_response = _make_mock_response(
            _make_companyfacts_response(ticker=ticker_pass, cik=789019, entity_name="Test Corp")
        )
        fail_response = _make_mock_response({}, status_code=404)

        call_count = [0]

        def selective_get(url, **kwargs):
            call_count[0] += 1
            # The CIK cache pre-populates AAPL/MSFT/GOOGL etc. so companyfacts
            # calls go directly. For the mock, alternate fail/pass based on call order.
            # First companyfacts call → fail; remaining → pass.
            if call_count[0] == 1:
                return fail_response
            return pass_response

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", side_effect=selective_get),
        ):
            block = ai_advisor._build_fundamentals_section()

        # At least ONE ticker succeeded, so the lens must be available
        assert block.get("available") is True, (
            "One ticker's 404 must NOT fail the whole lens. "
            "Per-ticker isolation: if any ticker resolves, the lens is available=True. "
            f"Got: available={block.get('available')!r}, reason={block.get('reason')!r}. "
            "AC-4 requires per-ticker honest degradation."
        )

        # No fabricated facts for the failed ticker
        tickers_payload = (block.get("payload") or {}).get("tickers", {})
        # The failed ticker should either be absent or explicitly marked unavailable
        if ticker_fail in tickers_payload:
            failed_entry = tickers_payload[ticker_fail]
            # If present, it must NOT carry 'key_facts' (that would be fabrication)
            assert not failed_entry.get("key_facts"), (
                f"Failed ticker {ticker_fail!r} must not carry 'key_facts' "
                f"(that would be a fabricated fact). Got: {failed_entry!r}"
            )


# ---------------------------------------------------------------------------
# AC-5b — all tickers fail → available=False with real reason
# ---------------------------------------------------------------------------


class TestAllTickersFailPath:
    """When every ticker fails, the lens must return available=False."""

    def test_all_tickers_fail_available_false(self):
        """Every ticker's companyfacts fetch fails → available=False with a reason
        that reflects the fan-out attempt (not the pre-fix "ticker symbol required"
        short-circuit).

        AC-5: if the entire universe fails (SEC outage / 403 / all-ETF holdings),
        the lens must return available=False with a real reason string — NOT an
        empty payload or a fabricated result.

        This test DISTINGUISHES the pre-fix short-circuit from the correct post-fix
        all-fail path:
          - Pre-fix (current): returns available=False, reason="ticker symbol required..."
            WITHOUT ever attempting SEC fetches (mock not called).
          - Post-fix (correct): attempts the fan-out, all fail, returns available=False
            with a reason that does NOT say "ticker symbol required" (that would be the
            old short-circuit).

        FAILS on the current implementation because the reason is "ticker symbol
        required" (pre-fix short-circuit), not a genuine all-fail reason.
        """

        import ai_advisor

        # All fetches return 404 — no ticker resolves
        fail_resp = _make_mock_response({}, status_code=404)

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=fail_resp) as mock_get,
        ):
            block = ai_advisor._build_fundamentals_section()

        assert block.get("available") is False, (
            "When every ticker fails, _build_fundamentals_section() must return "
            f"available=False. Got: available={block.get('available')!r}. "
            "A hollow available=True with no tickers would fabricate a 'working' lens. "
            "AC-5: all-fail → available=False with honest reason."
        )
        reason = block.get("reason", "")
        assert isinstance(reason, str) and reason.strip(), (
            f"available=False must carry a non-empty reason string. "
            f"Got reason={reason!r}. The reason explains WHY (e.g. all tickers failed, "
            "empty universe, etc.) — not the str(exc) detail (D-1)."
        )
        # The pre-fix short-circuit reason must NOT appear — that means the fan-out
        # was never attempted. If the reason still says "ticker symbol required",
        # the fix was NOT applied.
        assert "ticker symbol required" not in reason.lower(), (
            f"available=False reason is the pre-fix short-circuit: {reason!r}. "
            "The fan-out was NEVER attempted (mock.call_count="
            f"{mock_get.call_count}). "
            "AC-5 requires the all-fail path to arise from a genuine fan-out attempt, "
            "not the pre-fix 'no ticker' guard. Fix: implement the fan-out so the "
            "all-fail reason reflects the actual failure (e.g. 'no fundamentals "
            "available: all tickers failed')."
        )
        # Payload must be absent or empty — no fabricated data
        payload = block.get("payload")
        assert payload is None or payload == {} or payload == [], (
            f"available=False payload must be absent/None/empty, got {payload!r}. "
            "No fabricated fundamentals data when all tickers fail."
        )


# ---------------------------------------------------------------------------
# AC-6a — bounded fan-out
# ---------------------------------------------------------------------------


class TestBoundedFanout:
    """The fan-out must make a bounded number of SEC requests."""

    def test_fanout_is_bounded(self, sec_shape_fixture: dict):
        """SEC request count must be bounded, not proportional to an unbounded loop.

        AC-6: the proxy basket is ~8 tickers. CIK lookup is cached for common
        tickers (no extra HTTP call). companyfacts fetch is 1 call per ticker.
        Total bound: <= 2 * universe_size (worst case: 1 CIK lookup + 1 companyfacts
        per ticker, but proxy tickers use the in-process cache → typically 1 per ticker).

        FAILS if the fan-out makes unbounded calls (regression to an infinite loop).
        """
        import ai_advisor

        if not hasattr(ai_advisor, "_FUNDAMENTALS_PROXY_UNIVERSE"):
            pytest.skip("_FUNDAMENTALS_PROXY_UNIVERSE not defined — AC-6b fails first")

        proxy_size = len(ai_advisor._FUNDAMENTALS_PROXY_UNIVERSE)
        # Upper bound: 2 HTTP calls per ticker (CIK lookup + companyfacts)
        # Proxy tickers use _SEC_TICKER_CIK_CACHE → 0 extra CIK calls → 1 per ticker
        upper_bound = 2 * proxy_size  # generous ceiling

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp) as mock_get,
        ):
            ai_advisor._build_fundamentals_section()

        actual_calls = mock_get.call_count
        assert actual_calls <= upper_bound, (
            f"Fan-out made {actual_calls} HTTP requests for a universe of "
            f"{proxy_size} tickers. Upper bound is {upper_bound} "
            f"(2 calls/ticker). An unbounded loop would produce far more. "
            "AC-6: keep the basket small and the retry bounded."
        )
        # Also assert at least 1 call was made (not a no-op)
        assert actual_calls >= 1, (
            "Fan-out made 0 HTTP requests — the implementation is a no-op. "
            "AC-1: the portfolio path must actually contact SEC EDGAR."
        )


# ---------------------------------------------------------------------------
# REVIEW CYCLE: re-point of stale test in test_cycle2_lens_producers.py
#
# test_cycle2_lens_producers.py::TestSecEdgarFundamentalsProducer::
#   test_fundamentals_no_ticker_returns_available_false
#
# That test's premise ("missing ticker is a caller error → available=False")
# is WRONG after the fix. ticker=None is now the intended PORTFOLIO PATH that
# fans out and returns available=True when any ticker resolves.
# The test currently passes accidentally (no mock → real network fails → False).
# This class re-pins the correct behavior with a proper mock.
# ---------------------------------------------------------------------------


class TestNoTickerPortfolioPathRegression:
    """Regression: calling _build_fundamentals_section() without a ticker uses
    the portfolio fan-out path, NOT the old 'ticker symbol required' guard.

    This replaces the semantic premise of the now-stale cycle-2 test
    test_fundamentals_no_ticker_returns_available_false which relied on an
    un-mocked network call.
    """

    def test_no_ticker_does_not_return_ticker_symbol_required_reason(self, sec_shape_fixture: dict):
        """_build_fundamentals_section() (no ticker) must NOT return the pre-fix
        short-circuit reason 'ticker symbol required...'.

        The pre-fix reason was: "ticker symbol required to fetch SEC EDGAR fundamentals"
        After the fix: ticker=None routes to the portfolio fan-out path. The old
        reason must NEVER appear — its presence means the fix was not applied or
        was reverted.

        This is the deterministic re-point of the cycle-2 stale test — uses a mock
        so it does not depend on real network behavior.
        """
        import ai_advisor

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp),
        ):
            block = ai_advisor._build_fundamentals_section()

        reason = block.get("reason", "")
        assert "ticker symbol required" not in reason.lower(), (
            f"_build_fundamentals_section() (no ticker) returned the pre-fix "
            f"short-circuit reason: {reason!r}. "
            "After the fan-out fix, ticker=None routes to the PORTFOLIO PATH — "
            "the 'ticker symbol required' guard must not fire. "
            "If this test fails, the fix was reverted or the guard was re-introduced."
        )

    def test_no_ticker_routes_to_portfolio_path_not_error_path(self, sec_shape_fixture: dict):
        """_build_fundamentals_section() without ticker makes at least 1 SEC call.

        Pre-fix: made 0 HTTP calls (short-circuited before any network access).
        Post-fix: fans out over the proxy universe → at least 1 companyfacts call.

        This verifies the portfolio path is actually entered (not just a better
        error message).
        """
        import ai_advisor

        companyfacts_resp = _make_mock_response(sec_shape_fixture["companyfacts_shape"])

        with (
            patch("database.load_state", return_value=_make_empty_holdings_state()),
            patch("requests.get", return_value=companyfacts_resp) as mock_get,
        ):
            ai_advisor._build_fundamentals_section()

        assert mock_get.call_count >= 1, (
            "_build_fundamentals_section() (no ticker) made 0 HTTP requests. "
            "Pre-fix behavior: 0 requests (short-circuited). "
            "Post-fix behavior: at least 1 companyfacts call per proxy ticker. "
            "If call_count is still 0, the fan-out path is not being entered."
        )
