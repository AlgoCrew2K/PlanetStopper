# Feature: 500-name S&P Market-Breadth Producer
Status: ready
Created: 2026-06-14

## Summary
Compute the institutional gold-standard market-breadth indicator — **% of S&P 500 constituents above their 50-day and 200-day SMA** — for free, using the constituent list (datahub CSV primary / Wikipedia fallback) + the existing Alpaca daily-bar path (`synthetic_history.fetch_bars`). Feeds the technicals lens's *breadth* number with a true 500-name read (the 14-ETF basket stays for posture/momentum). Operator greenlit 2026-06-14; feasibility confirmed by alpaca-api-researcher (free, ~13-20 nightly requests, no symbol normalization needed).

## Acceptance Criteria
- [ ] AC-1: `advisors/lens_breadth.py` new module, off-execution-path, advisory-only, never-raising, D-1 error contract (`type(exc).__name__` only).
- [ ] AC-2: `get_sp500_constituents() -> list[str]` — fetches the current S&P 500 list from a free source (datahub `constituents.csv` primary; Wikipedia table fallback), with a **freshness gate** and a **490–510 row sanity band**; on failure of both → returns `[]` (honest-availability, never a stale/fabricated list). Dot-format tickers (BRK.B) passed through as-is (Alpaca + sources already agree).
- [ ] AC-3: `compute_breadth(constituents) -> dict` — fetches daily bars via `synthetic_history.fetch_bars`, computes **pct_above_50sma** and **pct_above_200sma** over the **QUALIFYING sub-universe** (names with ≥200 daily bars AND a current bar); reports `qualifying_count` / `total_count` (e.g. "487/503"); never imputes missing bars, never fabricates.
- [ ] AC-4: Public `fetch_breadth() -> dict` returns `{available, pct_above_50sma, pct_above_200sma, qualifying_count, total_count, source, reason?}`; `available=False` + `reason` on any failure (empty constituents, fetch failure, zero qualifying).
- [ ] AC-5: Persists each run to the **warehouse** via `lens_warehouse.persist_lens_snapshot(lens="breadth", ...)` (secret-stripped, append-only).
- [ ] AC-6: Metadata flags the **IEX-basis caveat** (free Alpaca feed = IEX trades only; acceptable for a 500-name aggregate).
- [ ] AC-7: The technicals lens consumes this breadth number for its `breadth` field while keeping the 14-ETF basket for posture/momentum (wiring may be a fast-follow if it complicates this PR; the producer + warehouse land here).

## Architecture
New `advisors/lens_breadth.py`. Reuses `synthetic_history.fetch_bars` (Alpaca IEX daily bars, batched ≤40 symbols, page-token loop, 429 backoff — already proven). Constituent fetch via `requests` (datahub raw CSV / Wikipedia HTML table parse). Persists to `lens_warehouse`. Bounded retry + explicit timeouts on all HTTP. No Flask route. No `LIVE_EXECUTION`.

## Edge Cases
- Both constituent sources down → `[]` → `available=False, reason` (no stale list).
- Constituent count outside 490–510 → reject (sanity), try fallback.
- Recent IPO / <200 bars → excluded from the 200-SMA denominator (qualifying sub-universe).
- Halted/delisted name → no current bar → excluded; not a job failure.
- Zero qualifying names → `available=False`.
- Large fetch → batched; respects Alpaca rate limit (once-nightly, ~17 requests).

## Security Considerations
- D-1 (`type(exc).__name__` only); never log raw exception/URL/key.
- FRED/Alpaca creds from env only (via synthetic_history); never hardcoded; secret-strip before warehouse persist.
- No SQL injection (parameterized warehouse writes); no eval/subprocess/shell; no new Flask route.

## Testing Strategy
- `tests/ai_advisor/test_lens_breadth.py` (or tests/database/): mock `requests.get` (constituent fetch) + `synthetic_history.fetch_bars` (bars) — NO live network in CI. Cover: constituent parse (datahub + Wikipedia fallback + freshness/sanity reject), breadth math over qualifying sub-universe (assert derivation/shape, not magic numbers — feedback_no_hardcoded_test_values), honest-availability (empty list / fetch fail / zero qualifying → available=False + reason), warehouse persist call, D-1 reasons. Run -n0.
- PM gates: full `tests/ai_advisor/` (or relevant dir) -n0 + LIVE functional (real "X/503 above 200-day" against live Alpaca + a real constituent fetch).

## Scope Boundaries
- IN: `lens_breadth.py` producer + constituent fetch + breadth math + warehouse persist + tests. Technicals consumption (AC-7) if clean.
- OUT: point-in-time/historical breadth (survivorship — live read only); paid SIP feed; cross-asset risk lens (parked).
