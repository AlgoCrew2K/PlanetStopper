# TDD Handoff
Plan: feature-plans/lens-fundamentals-portfolio-fanout-fix.md
Branch: fix/fundamentals-portfolio-fanout
Phase: red

## Test Files
- `tests/ai_advisor/test_lens_fundamentals_fanout.py` — 12 tests collected
  (4 failing RED, 2 passing regression guards, 6 skip-gated on upstream RED tests)

## Behavioral Test Plan
N/A — backend producer, no UI surface (plan §Design-System Mapping).

## Implementation Contract for fan-implementer

These are the exact symbols the tests import/monkeypatch from `ai_advisor`.
Coordinate on them BEFORE writing production code:

1. `_FUNDAMENTALS_PROXY_UNIVERSE: frozenset[str]` (or `set[str]`) — named
   module-level constant of company ticker strings. MUST have a source comment.
   The proxy must NOT include ETFs (SPY, QQQ, IWM etc.) — ETFs have no SEC
   companyfacts. Tests inspect this attribute directly.
   Recommended ~8 tickers: AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ
   (or similar large-cap cross-sector companies).

2. `_build_fundamentals_section(_data=None, *, ticker=None)` — existing signature
   preserved. When `ticker=None`, the function now fans out over the internal
   universe (holdings ∪ proxy). When `ticker="AAPL"`, it returns the SAME shape
   as today (AC-3 regression guard).

3. `_fetch_fundamentals_for_ticker(ticker: str) -> dict` — new internal helper
   extracted from the current single-ticker body. Tests check AC-3 behavior
   via _build_fundamentals_section(ticker="AAPL") — if that function works
   correctly via the helper, the contract is satisfied.

4. Portfolio payload shape (AC-5): `{tickers: {TICKER: {...per-ticker block...}},
   coverage: {available: N, universe: M}}`. Tests assert shape/presence only —
   never hardcoded financial values.

5. Fan-out must be BOUNDED (AC-6): the number of requests.get calls must be <=
   2 * len(universe) (CIK lookup cached for proxy tickers → 1 companyfacts call
   each; at most 2 calls per ticker total). Tests count call_count on requests.get.

6. Per-ticker failures degrade ONLY that ticker (AC-4): if one ticker's companyfacts
   fetch returns 404 or raises, the remaining tickers resolve normally and the lens
   returns available=True.

7. All-fail reason must NOT be "ticker symbol required..." (AC-5): that is the
   pre-fix short-circuit reason. After the fix, the all-fail reason must reflect
   the genuine outcome (e.g. "no fundamentals available: all tickers failed",
   "SEC EDGAR returned no key facts for any ticker", etc.).

8. `database.load_state` is called lazily inside `_build_fundamentals_section`
   (CC-2: no module-level DB access). Tests mock `database.load_state` directly.

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | portfolio call fans out and returns available=True | test_lens_fundamentals_fanout.py | TestPortfolioFanout::test_portfolio_call_fans_out_and_is_available | RED |
| AC-2a | empty holdings → proxy floor → available=True (no hollow at 03:00) | test_lens_fundamentals_fanout.py | TestUniverseDerivation::test_empty_holdings_uses_proxy_floor | RED |
| AC-2b | holdings tickers merged with proxy floor | test_lens_fundamentals_fanout.py | TestUniverseDerivation::test_holdings_merged_with_proxy_floor | SKIP (gated on AC-6b) |
| AC-3a | single-ticker path shape unchanged | test_lens_fundamentals_fanout.py | TestSingleTickerPathPreserved::test_single_ticker_path_shape_unchanged | GREEN (regression guard — correct pre-fix behavior) |
| AC-3b | single-ticker path still fetches SEC | test_lens_fundamentals_fanout.py | TestSingleTickerPathPreserved::test_single_ticker_path_still_fetches_sec | GREEN (regression guard — correct pre-fix behavior) |
| AC-4 | per-ticker failure degrades only that ticker | test_lens_fundamentals_fanout.py | TestPerTickerDegradation::test_per_ticker_failure_degrades_not_whole_lens | SKIP (gated on AC-6b) |
| AC-5a | portfolio payload has per-ticker facts + coverage count | test_lens_fundamentals_fanout.py | TestPortfolioFanout::test_portfolio_payload_has_per_ticker_facts_and_coverage | SKIP (gated on AC-1) |
| AC-5b | all tickers fail → available=False with genuine reason (not pre-fix short-circuit) | test_lens_fundamentals_fanout.py | TestAllTickersFailPath::test_all_tickers_fail_available_false | RED |
| AC-5c | no composite ratios invented in portfolio payload | test_lens_fundamentals_fanout.py | TestPortfolioFanout::test_no_composite_ratios_invented | SKIP (gated on AC-1) |
| AC-6a | fan-out is bounded | test_lens_fundamentals_fanout.py | TestBoundedFanout::test_fanout_is_bounded | SKIP (gated on AC-6b) |
| AC-6b | _FUNDAMENTALS_PROXY_UNIVERSE constant present at module scope | test_lens_fundamentals_fanout.py | TestNamedProxyConstant::test_named_proxy_constant_present | RED |
| AC-6c | proxy constant contains company tickers, not ETFs | test_lens_fundamentals_fanout.py | TestNamedProxyConstant::test_named_proxy_constant_contains_company_tickers_not_etfs | SKIP (gated on AC-6b) |

**Cascade:** Once AC-6b (constant present) and AC-1 (fan-out implemented) are GREEN,
all 6 SKIP tests will become runnable and are expected to either pass or reveal new
assertion failures.

## Import Stubs Created
None — the test file imports only `ai_advisor` (exists) and patches `database.load_state`
and `requests.get`. No new modules to stub.

## Questions for User / PM
None — all design decisions covered by plan or [PM-ASSUMED] annotations.

## Status Log
- [2026-06-16] fan-test-writer (quant-test-writer, LEAD): Starting RED phase for fundamentals portfolio fan-out fix
- [2026-06-16] fan-test-writer: RED complete — 12 tests collected: 4 failing (correct RED on assertions), 2 passing (AC-3 regression guards — existing behavior verified correct), 6 skipped (cascade-gated on upstream RED failures). 0 import/syntax errors. 0 stubs created. HEAD committed to fix/fundamentals-portfolio-fanout.
