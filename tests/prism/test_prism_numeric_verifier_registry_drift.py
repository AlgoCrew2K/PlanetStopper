"""
Registry-drift contract test — /review PR #90 Finding F2 (DE-PRISM-NUMERIC-VERIFY-001).

Highest-value finding of the fix cycle: protects the anti-fabrication integrity of the
numeric verifier. _INDICATOR_REGISTRY's payload_path for each indicator is a static
string baked in at authoring time (e.g. "vix_level", "series.DGS10.value") that must
match the REAL shape each ai_advisor._build_<lens>_section() builder actually returns.
If a future refactor of a builder's payload shape drifts from what the registry
expects, the verifier would silently go blind for that indicator — _resolve_dotted_path
returns None -> classification "unverifiable" — with ZERO CI failure, because no
existing test calls the REAL builder against the registry's payload_path (verifier unit
tests mock the builder entirely; builder producer tests never touch the registry).

This closes that gap: for each _INDICATOR_REGISTRY entry, call the REAL
ai_advisor._build_<lens>_section() builder with ONLY the external network mocked
(reusing this codebase's OWN established network-mocking boundaries — see
tests/ai_advisor/test_derivatives_section.py, test_cycle2_lens_producers.py, and
test_lens_technicals.py; the builder function itself is never mocked here), then
resolve the registry's payload_path against the REAL returned payload using the
verifier's OWN _resolve_dotted_path / _resolve_payload_path / _lookup_registry_entry
(never a test reimplementation) and assert the result is not None.

Expected to PASS today (no known drift exists) — its value is as a permanent
regression guard that fails the moment a builder's payload shape changes without a
corresponding registry update.

Network hermeticity (hard gate, PM condition on this finding): every sub-test mocks
the FULL network surface for its lens, not just the primary call:
  - derivatives: the entire advisors.lens_options_proxy._fetch_options_proxy() is
    mocked (not a lower-level primitive) — nothing inside it can leak regardless of
    its internal implementation.
  - sentiment: the entire advisors.news_corpus.build_news_corpus() is mocked, same
    reasoning.
  - macro / fundamentals: confirmed by reading ai_advisor.py directly that
    `requests.get` (ai_advisor.py:458, inside _fetch_with_backoff) is the ONLY direct
    network call site in the entire module — used by nothing except these two
    builders' FRED/SEC fetches. Mocking requests.get is a complete guard.
  - technicals: advisors/lens_technicals.py's own docstring names _get_bars as the
    "test-mockable seam" and it is the only network-touching call in the file
    (confirmed via source read — no other requests/urllib/httpx call exists there).
  - Belt-and-suspenders: derivatives/sentiment/technicals sub-tests ALSO patch
    requests.get to raise loudly if called — insurance against a future refactor
    introducing a direct requests.get call in one of those lenses without this test
    being updated. (macro/fundamentals cannot carry this guard — requests.get IS
    their legitimate mocked call there.)

DB isolation: conftest._isolate_db autouse fixture (technicals' builder calls
database.load_state()). Run protocol: targeted -n0 only.
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

_FIXTURES_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "math"

_NO_REAL_HTTP_MSG = (
    "F2 registry-drift test: no real HTTP call expected for this lens — "
    "requests.get must not be invoked (belt-and-suspenders network guard)."
)


def _import_verifier():
    """Import advisors.prism_numeric_verifier; reload for module-identity safety."""
    if "advisors.prism_numeric_verifier" in sys.modules:
        importlib.reload(sys.modules["advisors.prism_numeric_verifier"])
    return importlib.import_module("advisors.prism_numeric_verifier")


def _make_mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Mirrors tests/ai_advisor/test_cycle2_lens_producers.py::_make_mock_response
    (same network-boundary mocking convention, not reinvented)."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.text = json.dumps(json_data)
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _make_daily_bar(close: float, index: int = 0) -> dict:
    """Mirrors tests/ai_advisor/test_lens_technicals.py::_make_daily_bar."""
    return {
        "t": f"2025-{1 + index // 20:02d}-{1 + index % 20:02d}T00:00:00Z",
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": 1_000_000.0,
        "vw": close,
    }


def _make_bar_sequence(closes: list[float]) -> list[dict]:
    return [_make_daily_bar(c, i) for i, c in enumerate(closes)]


def _resolve_registry_path(mod, indicator: str):
    """Resolve indicator's registry entry -> (payload_path, ...) via the verifier's
    OWN production functions — never a test reimplementation."""
    registry_entry = mod._lookup_registry_entry(indicator)
    assert registry_entry is not None, (
        f"Precondition: {indicator!r} must resolve to a registry entry"
    )
    _lens, path_template, _cmp, _tol = registry_entry
    return mod._resolve_payload_path(path_template, indicator)


class TestIndicatorRegistryMatchesRealBuilderShapes:
    """F2: every _INDICATOR_REGISTRY payload_path must resolve against the REAL
    ai_advisor._build_<lens>_section() output — not a test-authored stand-in."""

    def test_derivatives_indicators_resolve_against_real_builder(self):
        """VIX, VIXCLS, VXVCLS, VIX3M all resolve against the real
        _build_derivatives_section() output (network: lens_options_proxy, entirely
        mocked — nothing inside it can leak)."""
        mod = _import_verifier()
        import ai_advisor

        proxy_success_result = {
            "available": True,
            "vix_level": 18.5,
            "vix_term_structure": {
                "spot": 18.5,
                "term_3m": 20.1,
                "ratio": 18.5 / 20.1,
                "spread": 18.5 - 20.1,
                "regime": "contango",
            },
            "risk_read": "neutral",
            "as_of_date": "2026-06-14",
            "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
        }
        with (
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
            patch("requests.get", side_effect=AssertionError(_NO_REAL_HTTP_MSG)),
        ):
            section = ai_advisor._build_derivatives_section()

        assert section.get("available") is True, (
            f"Precondition: the real builder must report available=True with the "
            f"network mocked; got {section!r}"
        )
        payload = section.get("payload") or {}
        for indicator in ("VIX", "VIXCLS", "VXVCLS", "VIX3M"):
            path = _resolve_registry_path(mod, indicator)
            resolved = mod._resolve_dotted_path(payload, path)
            assert resolved is not None, (
                f"F2 registry-drift guard: indicator {indicator!r}'s registry path "
                f"{path!r} did not resolve against the REAL _build_derivatives_section "
                f"payload — the registry and the real builder shape have drifted. "
                f"Real payload: {payload!r}"
            )

    def test_macro_indicators_resolve_against_real_builder(self):
        """DGS10, 10Y, UNRATE, CPIAUCSL, CPI, FEDFUNDS all resolve against the real
        _build_macro_section() output (network: requests.get — the ONLY direct
        network call site in ai_advisor.py, confirmed by source read)."""
        mod = _import_verifier()
        import ai_advisor

        fred_fixture = json.loads(
            (_FIXTURES_DIR / "fred_macro_api_shape.json").read_text(encoding="utf-8")
        )
        mock_resp = _make_mock_response(fred_fixture["series_observations_shape"])

        with (
            patch.dict(os.environ, {"FRED_API_KEY": "test-key-sentinel"}, clear=False),
            patch("requests.get", return_value=mock_resp),
        ):
            section = ai_advisor._build_macro_section()

        assert section.get("available") is True, (
            f"Precondition: the real builder must report available=True with the "
            f"network mocked; got {section!r}"
        )
        payload = section.get("payload") or {}
        for indicator in ("DGS10", "10Y", "UNRATE", "CPIAUCSL", "CPI", "FEDFUNDS"):
            path = _resolve_registry_path(mod, indicator)
            resolved = mod._resolve_dotted_path(payload, path)
            assert resolved is not None, (
                f"F2 registry-drift guard: indicator {indicator!r}'s registry path "
                f"{path!r} did not resolve against the REAL _build_macro_section "
                f"payload. Real payload: {payload!r}"
            )

    def test_sentiment_tone_resolves_against_real_builder(self):
        """tone resolves against the real _build_sentiment_section() output
        (network: advisors.news_corpus.build_news_corpus, entirely mocked)."""
        mod = _import_verifier()
        import ai_advisor

        corpus_result = {
            "available": True,
            "tone": 0.12,
            "corpus": [
                {
                    "url": "https://www.example-news-site.com/article-1",
                    "title": "Markets Signal Risk Shift",
                    "published": "20260610T120000Z",
                    "domain": "example-news-site.com",
                    "source_feed": "gdelt_artlist",
                    "topics": ["macro"],
                    "score": 0.72,
                },
            ],
            "reason": None,
        }
        with (
            patch("advisors.news_corpus.build_news_corpus", return_value=corpus_result),
            patch("requests.get", side_effect=AssertionError(_NO_REAL_HTTP_MSG)),
        ):
            section = ai_advisor._build_sentiment_section()

        assert section.get("available") is True, (
            f"Precondition: the real builder must report available=True with the "
            f"network mocked; got {section!r}"
        )
        payload = section.get("payload") or {}
        path = _resolve_registry_path(mod, "tone")
        resolved = mod._resolve_dotted_path(payload, path)
        assert resolved is not None, (
            f"F2 registry-drift guard: indicator 'tone''s registry path {path!r} did "
            f"not resolve against the REAL _build_sentiment_section payload. "
            f"Real payload: {payload!r}"
        )

    def test_technicals_breadth_resolves_against_real_builder(self):
        """breadth resolves against the real _build_technicals_section() output
        (network: advisors.lens_technicals._get_bars — the module's own documented
        "test-mockable seam" and its only network-touching call, confirmed by
        source read)."""
        mod = _import_verifier()
        import ai_advisor
        from advisors import lens_technicals

        spy_bars = _make_bar_sequence([100.0] * 250 + [110.0])
        mock_bars = {ticker: spy_bars for ticker in lens_technicals._PROXY_UNIVERSE}

        with (
            patch.object(lens_technicals, "_get_bars", return_value=mock_bars),
            patch("requests.get", side_effect=AssertionError(_NO_REAL_HTTP_MSG)),
        ):
            section = ai_advisor._build_technicals_section()

        assert section.get("available") is True, (
            f"Precondition: the real builder must report available=True with the "
            f"network mocked; got {section!r}"
        )
        payload = section.get("payload") or {}
        path = _resolve_registry_path(mod, "breadth")
        resolved = mod._resolve_dotted_path(payload, path)
        assert resolved is not None, (
            f"F2 registry-drift guard: indicator 'breadth''s registry path {path!r} "
            f"did not resolve against the REAL _build_technicals_section payload. "
            f"Real payload: {payload!r}"
        )

    def test_fundamentals_wildcard_resolves_against_real_builder(self):
        """The <TICKER>.<CONCEPT> wildcard (e.g. AAPL.Revenues) resolves against
        the real _build_fundamentals_section() output — called WITHOUT a ticker
        kwarg, matching the REAL production call site in
        prism_scheduler._fetch_lens_sections() (`ai_advisor._build_fundamentals_section`
        is invoked with no args there).

        Caught during authoring: _build_fundamentals_section(ticker="AAPL") (the
        single-ticker AC-3-regression-guard path) returns a DIFFERENT, flatter
        payload shape ({entity_name, cik, key_facts} with no "tickers" wrapper) than
        the no-ticker portfolio fan-out path ({tickers: {T: {...}}, coverage: {...}}),
        which is the shape the registry's "tickers.<T>.key_facts.<C>.value" path
        actually expects and the shape production always produces (the scheduler
        never passes a ticker). Calling with ticker="AAPL" would have made this test
        pass for the WRONG reason (a payload shape production never emits) — this is
        exactly the kind of drift F2 exists to catch.

        Network: requests.get is the sole call site in ai_advisor.py. The no-ticker
        path fans out _fetch_fundamentals_for_ticker() across
        _FUNDAMENTALS_PROXY_UNIVERSE (AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ) —
        AAPL/MSFT/GOOGL/AMZN/NVDA/JPM hit the in-process CIK fast-path cache (no
        secondary lookup); XOM/JNJ fall through to the SEC-bulk-tickers-JSON slow
        path, which is STILL fully covered by the same requests.get mock (returns
        the companyfacts fixture instead of a real ticker list — harmless, since
        per-ticker failures degrade gracefully and AAPL's own resolution never
        depends on it). No real network call is possible either way.
        """
        mod = _import_verifier()
        import ai_advisor

        sec_fixture = json.loads(
            (_FIXTURES_DIR / "sec_edgar_fundamentals_api_shape.json").read_text(encoding="utf-8")
        )
        mock_resp = _make_mock_response(sec_fixture["companyfacts_shape"])

        with patch("requests.get", return_value=mock_resp):
            section = ai_advisor._build_fundamentals_section()

        assert section.get("available") is True, (
            f"Precondition: the real builder must report available=True with the "
            f"network mocked; got {section!r}"
        )
        indicator = "AAPL.Revenues"
        path = _resolve_registry_path(mod, indicator)
        payload = section.get("payload") or {}
        resolved = mod._resolve_dotted_path(payload, path)
        assert resolved is not None, (
            f"F2 registry-drift guard: the fundamentals wildcard's constructed path "
            f"{path!r} (for {indicator!r}) did not resolve against the REAL "
            f"_build_fundamentals_section payload — the SEC key_facts shape and the "
            f"registry's dynamic path construction have drifted. "
            f"Real payload: {payload!r}"
        )
