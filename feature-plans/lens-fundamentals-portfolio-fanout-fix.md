# Feature: Fundamentals Lens — Portfolio→Ticker Fan-Out Fix
Status: ready
Created: 2026-06-16

## Summary

The SEC EDGAR fundamentals lens (`ai_advisor._build_fundamentals_section`) is **permanently
unavailable** in the nightly Market Prism. Root cause (verified on origin/main `7083147`): the
function requires a `ticker` and short-circuits `available=False, reason="ticker symbol required..."`
when `ticker=None` — but BOTH production callers invoke it with no ticker:
`advisors/lens_pipeline.py:73` (`builder()`, the 03:00 nightly) and `ai_advisor.py:1420`
(`_build_fundamentals_section()` in `assemble_advisor_context`). So SEC EDGAR is never reached and
the fundamentals lens is a dead block (REAL BUG 2 from the Phase-3 proof-run).

This fix makes the portfolio-level call (`ticker=None`) derive a company-ticker universe internally
and fan out the existing single-ticker SEC companyfacts logic across it, aggregating into a
portfolio-level fundamentals block with honest-availability — mirroring how the technicals lens
derives its universe internally (`_PROXY_UNIVERSE` floor, DE-TECH-002). The single-ticker path
(`ticker="AAPL"`) is preserved byte-for-byte for any per-symphony caller.

## Acceptance Criteria

- [ ] **AC-1 (internal fan-out on portfolio call):** `_build_fundamentals_section()` (no ticker)
  derives a universe of company tickers and fetches SEC fundamentals for each, returning an
  aggregated `available=True` block when at least one ticker resolves with key facts — instead of
  the `"ticker symbol required"` short-circuit.
- [ ] **AC-2 (universe = holdings ∪ company proxy floor):** the universe is the live
  `logic_holdings` tickers (from `database.load_state()`) unioned with a NAMED module-level
  `_FUNDAMENTALS_PROXY_UNIVERSE` floor of large-cap **company** tickers (NOT ETFs — ETFs have no
  companyfacts). The proxy is an unconditional floor so the 03:00 nightly Prism always has a real
  fundamentals universe even when holdings are empty (consistency with DE-TECH-002).
- [ ] **AC-3 (single-ticker path preserved):** `_build_fundamentals_section(ticker="AAPL")` returns
  the SAME shape/behavior as today (existing per-symphony callers unaffected). Refactor the existing
  single-ticker body into a helper (e.g. `_fetch_fundamentals_for_ticker(ticker) -> dict`) that both
  paths call.
- [ ] **AC-4 (per-ticker honest degradation):** a ticker that fails to resolve (ETF / unknown /
  HTTP error / no key facts) is excluded from the aggregate WITHOUT failing the whole lens; only its
  own per-ticker entry is omitted or marked unavailable. No fabricated facts.
- [ ] **AC-5 (aggregate availability + shape):** the portfolio payload exposes per-ticker key facts
  and a coverage count (e.g. `{tickers: {AAPL: {...}}, coverage: {available: N, universe: M}}`
  [PM-ASSUMED — confirm exact keys]) plus citations aggregated across the resolved tickers. If the
  universe is empty OR every ticker fails → `available=False` with a real reason (honest, not
  fabricated). NO composite ratios invented from the facts.
- [ ] **AC-6 (bounded SEC fan-out):** the fan-out respects SEC EDGAR limits — descriptive
  `User-Agent` (already `_SEC_USER_AGENT`), bounded retry via the existing `_fetch_with_backoff`, a
  small proxy basket (~8 tickers) and a NAMED per-request pacing/cap so the nightly fetch stays light
  (SEC allows ~10 req/s; companyfacts payloads are large). Off-execution-path; no blocking I/O on the
  engine path.

## Architecture

Surface: `ai_advisor.py` only (the fundamentals producer + a new named proxy constant) plus its test
file. No schema change, no new route, no engine-path change. Advisory-only.

1. **Extract** the current single-ticker body (CIK resolve → companyfacts fetch → `_SEC_KEY_CONCEPTS`
   extraction → citation build) into `_fetch_fundamentals_for_ticker(ticker: str) -> dict` returning
   the existing single-ticker block shape. `_build_fundamentals_section(ticker="X")` delegates to it
   (AC-3 — behavior preserved).
2. **Fan-out path** (`ticker=None`): derive `universe = sorted(holdings_tickers ∪
   _FUNDAMENTALS_PROXY_UNIVERSE)`; for each ticker call `_fetch_fundamentals_for_ticker`; collect the
   `available=True` results; build the aggregated payload + merged citations; honest-availability if
   none succeed. Mirror `_build_technicals_section`'s universe derivation (holdings from
   `database.load_state()`, lazy/CC-2-safe).
3. `_FUNDAMENTALS_PROXY_UNIVERSE` — named module-level constant, ~8 large-cap company tickers across
   sectors (e.g. AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ) [PM-ASSUMED] with a source comment.

**Data flow (nightly):** `lens_pipeline._call_lens_section("fundamentals")` → `builder()` →
`_build_fundamentals_section(ticker=None)` → universe → per-ticker
`_fetch_fundamentals_for_ticker` → aggregate → block consumed by the Prism fundamentals analyst.

## Design-System Mapping

N/A — backend producer, no UI surface. The Overview tab renders whatever the lens reports; an honest
`available=False` empty state already exists.

## Edge Cases

- **Empty holdings (off-hours/weekend/flat):** the proxy floor guarantees a non-empty universe →
  lens still available (the technicals hollow-at-03:00 lesson).
- **All proxy tickers fail (SEC outage / 403 from missing UA):** `available=False` with a real
  reason; no fabricated facts.
- **Holdings contain ETFs / non-companies:** those tickers degrade per-ticker (CIK not found / no key
  facts) and are excluded from the aggregate; the lens stays available via the rest.
- **Large companyfacts payloads:** keep the basket small (~8); bound the fan-out; do not retain raw
  multi-MB JSON beyond extracting key facts.
- **Duplicate tickers (holdings ∩ proxy):** deduplicated by the set union.
- **SEC rate limit / 429:** existing `_fetch_with_backoff` bounded retry; after exhaustion that
  ticker degrades, lens stays available via others.

## Security Considerations

- **No new external surface.** Same SEC EDGAR companyfacts endpoint, same mandatory `User-Agent`
  (`_SEC_USER_AGENT`). No new credential (SEC is keyless/free).
- **Input validation:** facts parsed only for the known `_SEC_KEY_CONCEPTS` (numeric vals);
  never eval/template response content. CIK lookups from the SEC bulk file only.
- **Data exposure:** advisory facts only; D-1 `type(exc).__name__` on errors; no raw response
  persisted.
- **Advisory-only / off-execution-path:** never touches `LIVE_EXECUTION`; cannot place a trade.
  CC-2 lazy-import boundary preserved (`database.load_state` lazy inside the function).

## Testing Strategy

New/updated tests in `tests/ai_advisor/test_lens_fundamentals_fanout.py` (or the existing fundamentals
test file — test-writer confirms the canonical path). All SEC HTTP mocked; NO live SEC in the suite.
Fixtures schema-derived from the real companyfacts shape with a runtime validator; assertions derive
from fixture shape/presence — NEVER hardcoded financial literals.

- `test_portfolio_call_fans_out_and_is_available` — `_build_fundamentals_section()` (no ticker) with
  mocked SEC responses for the proxy basket → `available=True`, per-ticker facts present, coverage
  count correct. **The RED that pins the dead-lens defect.**
- `test_empty_holdings_uses_proxy_floor` — `database.load_state()` mocked empty → universe is the
  proxy floor → `available=True` (no hollow at 03:00).
- `test_single_ticker_path_unchanged` — `_build_fundamentals_section(ticker="AAPL")` returns the same
  shape as the pre-fix single-ticker behavior (regression guard for AC-3).
- `test_per_ticker_failure_degrades_not_whole_lens` — one ticker 404/ETF, others OK → lens
  `available=True`, failed ticker excluded, no fabricated facts.
- `test_all_tickers_fail_available_false` — every ticker fails → `available=False` with a real
  reason; no fabricated payload.
- `test_named_proxy_constant_present` — `_FUNDAMENTALS_PROXY_UNIVERSE` is a named module-level
  constant of company tickers (reviewer verifies the source comment).
- `test_fanout_is_bounded` — assert the fan-out makes a bounded number of SEC calls (no unbounded
  loop) for a given universe size.

**Run protocol:** `pytest tests/ai_advisor -p no:xdist -o addopts= -m "not live and not slow and not perf"`
(the `-m` filter is MANDATORY). `DB_PATH` via `tests/conftest.py`. SEC mocked — no real network.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Internal fan-out (not a caller signature change) | Both callers invoke `builder()` with no args via the uniform lens map; the universe must be derived inside the producer, exactly as technicals does. Avoids touching the pipeline contract. |
| [PM-ASSUMED] Company proxy floor (~8 large-caps, NOT ETFs) | The nightly Prism must get a real fundamentals read even when flat; ETFs have no SEC companyfacts, so the floor must be individual companies. Small basket respects SEC rate limits + payload weight. |
| Per-ticker honest degradation, aggregate stays available | Mirrors the breadth pattern; a single 404/ETF must not kill the whole lens. No fabricated facts (D-1 / honest-availability). |
| No invented composite ratios | Aggregating raw XBRL facts into a synthetic score would fabricate a producer value; expose per-ticker facts + coverage, assert shape not values (no-hardcoded-test-values rule). |
| Preserve single-ticker path | Defensive: a per-symphony caller may pass a ticker; AC-3 keeps it byte-identical via the extracted helper. |
| No fundamentals cache (scope OUT) | SEC is free (no provider bill); nightly fan-out over ~8 tickers is acceptable. A quarterly-TTL cache is a documented fast-follow if payload weight becomes an issue. |

## Scope Boundaries

- **IN:** `ai_advisor._build_fundamentals_section` portfolio fan-out + `_fetch_fundamentals_for_ticker`
  helper extraction + `_FUNDAMENTALS_PROXY_UNIVERSE` named floor; per-ticker honest degradation;
  bounded fan-out; tests; doc-gen (module doc + DECISIONS `DE-FUND-001`). Already wired (the pipeline
  + `assemble_advisor_context` already call it) — no new wiring, but the PM live functional test must
  prove the nightly path now returns `available=True` with real SEC facts.
- **OUT:** the derivatives freshness fix (shipped #37); any new fundamentals data source beyond SEC
  EDGAR companyfacts; a fundamentals cache layer; composite fundamental scores; schema/migration/UI
  changes; changing the lens-pipeline contract.

**Team note:** Toxic-Pair TDD — test-writer (quant-test-writer, LEAD) + implementer
(composer-alpaca-integration — best external-API/fan-out/fixture-first fit for SEC EDGAR HTTP;
candidates checked: risk-engine-specialist [math-core, weaker for HTTP fan-out], flask/sqlite [N/A])
+ reviewer (quant-code-reviewer) + doc-writer (doc-gen). PM live-functional gate: real SEC fan-out
(keyless) proving the nightly portfolio path returns `available=True` with real key facts for the
proxy basket, plus per-ticker degradation honesty. **doc-writer: COMMIT your docs before going idle
(two prior cycles' doc-writers abandoned uncommitted — do not repeat).**
