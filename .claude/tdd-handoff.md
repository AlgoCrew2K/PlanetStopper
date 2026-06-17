# TDD Handoff
Plan: feature-plans/lens-fundamentals-portfolio-fanout-fix.md
Branch: fix/fundamentals-portfolio-fanout
Phase: done

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
| AC-1 | portfolio call fans out and returns available=True | test_lens_fundamentals_fanout.py | TestPortfolioFanout::test_portfolio_call_fans_out_and_is_available | GREEN |
| AC-2a | empty holdings → proxy floor → available=True (no hollow at 03:00) | test_lens_fundamentals_fanout.py | TestUniverseDerivation::test_empty_holdings_uses_proxy_floor | GREEN |
| AC-2b | holdings tickers merged with proxy floor | test_lens_fundamentals_fanout.py | TestUniverseDerivation::test_holdings_merged_with_proxy_floor | GREEN |
| AC-3a | single-ticker path shape unchanged | test_lens_fundamentals_fanout.py | TestSingleTickerPathPreserved::test_single_ticker_path_shape_unchanged | GREEN |
| AC-3b | single-ticker path still fetches SEC | test_lens_fundamentals_fanout.py | TestSingleTickerPathPreserved::test_single_ticker_path_still_fetches_sec | GREEN |
| AC-4 | per-ticker failure degrades only that ticker | test_lens_fundamentals_fanout.py | TestPerTickerDegradation::test_per_ticker_failure_degrades_not_whole_lens | GREEN |
| AC-5a | portfolio payload has per-ticker facts + coverage count | test_lens_fundamentals_fanout.py | TestPortfolioFanout::test_portfolio_payload_has_per_ticker_facts_and_coverage | GREEN |
| AC-5b | all tickers fail → available=False with genuine reason | test_lens_fundamentals_fanout.py | TestAllTickersFailPath::test_all_tickers_fail_available_false | GREEN |
| AC-5c | no composite ratios invented in portfolio payload | test_lens_fundamentals_fanout.py | TestPortfolioFanout::test_no_composite_ratios_invented | GREEN |
| AC-6a | fan-out is bounded | test_lens_fundamentals_fanout.py | TestBoundedFanout::test_fanout_is_bounded | GREEN |
| AC-6b | _FUNDAMENTALS_PROXY_UNIVERSE constant present at module scope | test_lens_fundamentals_fanout.py | TestNamedProxyConstant::test_named_proxy_constant_present | GREEN |
| AC-6c | proxy constant contains company tickers, not ETFs | test_lens_fundamentals_fanout.py | TestNamedProxyConstant::test_named_proxy_constant_contains_company_tickers_not_etfs | GREEN |
| STALE-REPOINT | cycle-2 no-ticker test re-pointed to portfolio path semantics | test_cycle2_lens_producers.py + test_lens_fundamentals_fanout.py | test_fundamentals_no_ticker_routes_to_portfolio_path + TestNoTickerPortfolioPathRegression | GREEN |

**Cascade:** Once AC-6b (constant present) and AC-1 (fan-out implemented) are GREEN,
all 6 SKIP tests will become runnable and are expected to either pass or reveal new
assertion failures.

## Import Stubs Created
None — the test file imports only `ai_advisor` (exists) and patches `database.load_state`
and `requests.get`. No new modules to stub.

## Stale Test — Requires Re-Point Before Merge

`tests/ai_advisor/test_cycle2_lens_producers.py::TestSecEdgarFundamentalsProducer::test_fundamentals_no_ticker_returns_available_false`

**Root cause: stale-by-intent.** Pre-fix, `_build_fundamentals_section()` (no ticker) deterministically returned `available=False` via a no-ticker guard. Post-fix, `ticker=None` is the portfolio fan-out path — the premise of this test is now WRONG. The test currently passes accidentally because there is no `requests.get` mock, so real HTTP calls to SEC EDGAR fail in the test environment (network-blocked / rate-limited) and return `available=False` via the all-fail path. This is a flaky, network-dependent pass.

**Required action (test-writer re-points, NOT deleted/weakened):** Re-point to assert a meaningful property of the portfolio path (e.g., assert that `_build_fundamentals_section()` without a ticker does NOT return reason="ticker symbol required" — because that would be the pre-fix short-circuit — and instead confirm it routes to the fan-out). The test should use a mock so it is deterministic.

I am writing a replacement test now to pin this correctly:

## Questions for User / PM
None — all design decisions covered by plan or [PM-ASSUMED] annotations.

## Test File Issues (for test-writer to fix)
None — all 12 tests passed against the implementation as written.

## Disputed Tests
None.

## Implementation Notes
- Added `_FUNDAMENTALS_PROXY_UNIVERSE: frozenset[str]` constant at module scope (after `_SEC_KEY_CONCEPTS`). 8 company tickers: AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ. No ETFs. Source comment documents S&P 500 cross-sector selection rationale.
- Extracted `_fetch_fundamentals_for_ticker(ticker: str) -> dict` from the original single-ticker body of `_build_fundamentals_section`. The helper returns a partial block (no `lens` key — callers set it). All existing logic (CIK cache lookup, `_fetch_with_backoff`, `_SEC_KEY_CONCEPTS` extraction, citation building, deduplication) moved verbatim into this helper.
- `_build_fundamentals_section` now has two paths:
  - **Single-ticker path** (AC-3 preserved): `ticker is not None` → delegates to `_fetch_fundamentals_for_ticker`, wraps with `lens` key. Shape unchanged.
  - **Portfolio fan-out path** (AC-1 fix): `ticker=None` → derives universe via `database.load_state()` (CC-2 lazy) ∪ `_FUNDAMENTALS_PROXY_UNIVERSE`, iterates per-ticker with per-ticker exception isolation, aggregates `{tickers: {TICKER: payload}, coverage: {available: N, universe: M}}`.
- All-fail path returns `reason="no fundamentals available: all tickers failed SEC EDGAR fetch"` — does NOT say "ticker symbol required" (AC-5 distinguisher).
- Proxy tickers (AAPL, MSFT, etc.) are all in `_SEC_TICKER_CIK_CACHE` → zero extra CIK lookup HTTP calls for the proxy basket. Total calls = 1 companyfacts per ticker → well within the 2× upper bound tested by AC-6.

## Status Log
- [2026-06-16] fan-test-writer (quant-test-writer, LEAD): Starting RED phase for fundamentals portfolio fan-out fix
- [2026-06-16] fan-test-writer: RED complete — 12 tests collected: 4 failing (correct RED on assertions), 2 passing (AC-3 regression guards — existing behavior verified correct), 6 skipped (cascade-gated on upstream RED failures). 0 import/syntax errors. 0 stubs created. HEAD committed to fix/fundamentals-portfolio-fanout.
- [2026-06-16] fan-implementer (composer-alpaca-integration): GREEN complete — 12/12 tests passing, 0 test bugs documented. All 6 previously-skipped cascade tests now run and pass. Lint: 2 pre-existing errors (I001 import sort, E501 long-line) — not introduced by this change (confirmed via git show HEAD:ai_advisor.py). Changes: ai_advisor.py only.
- [2026-06-16] fan-test-writer: REVIEW cycle — adversarial review found 1 stale test (test_fundamentals_no_ticker_returns_available_false in test_cycle2_lens_producers.py — was passing accidentally via network failure, not logic; premise now wrong after AC-1 fix). Re-pointed deterministically with a proper mock. Added 2 regression tests to test_lens_fundamentals_fanout.py (TestNoTickerPortfolioPathRegression). All 14+9=23 relevant tests GREEN. Stale test section resolved. Phase: done (all A/C GREEN, no unresolved gaps). Routing to fan-reviewer for final APPROVE.
