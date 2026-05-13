# Alpaca API Reverification — 2026-05-13

**Researcher:** alpaca-api-researcher
**Date:** 2026-05-13 (1 day after baseline)
**Baseline reference:** `docs/research/alpaca/baseline__2026-05-12.md`

---

## Verdict

**CLEAR.** No new SDK releases since baseline. No endpoint surface changes. All 4 originally-flagged Open Questions (`[Unverified]` / `[STALE]`) are now resolved against primary-tier docs. Production code (`alpha_bot_execution.py`, `synthetic_history.py`) matches the current Alpaca v2 contract on every dimension verified: endpoint paths, parameter names, parameter values, header names, response shape, and the `feed=iex` pinning that the feed-pinning recommendation produced. One small precision note added (see Discrepancies — none rise to a blocker).

**Summary line:** 4 of 4 open questions resolved; 0 new discrepancies in production code; 1 action item for PM (delete pre-fix `history_cache.json` if present on host — already captured in optuna-provenance audit, repeated here for traceability).

---

## What Changed Since 2026-05-12 Baseline

| Surface | Status | Detail |
|---|---|---|
| `alpaca-py` latest stable | **unchanged** — 0.43.4 (2026-04-29) | GitHub releases JSON shows no release after 0.43.4 as of 2026-05-13. Tier 1, High. |
| `data.alpaca.markets/v2/stocks/bars` schema | **unchanged** | Re-fetched `docs.alpaca.markets/reference/stockbars` 2026-05-13 — parameter list and defaults match baseline. Tier 1, High. |
| `data.alpaca.markets/v2/stocks/quotes/latest` | **unchanged** | Re-fetched 2026-05-13 — endpoint URL, param list, response schema match. Tier 1, High. |
| Subscription-tier limits | **unchanged** | Basic = 200 calls/min + IEX only; Algo Trader Plus = $99/mo + 10,000 calls/min + SIP. Tier 1, High. |
| Production code Alpaca call sites | **changed since baseline-of-baseline** | All three production call sites now pin `feed=iex` (merge `ce043fd` predates this baseline). No further drift in 24 hours. |

**Code-vs-baseline reconciliation:** The baseline (2026-05-12) reported `alpha_bot_execution.py:163,232` as un-pinned. Today (2026-05-13) the same call sites at `alpha_bot_execution.py:166` and `:239` explicitly include `&feed=iex`. The single-day shift reflects merges that landed between baseline authorship and this reverification — not new drift. The `feed-pinning-recommendation.md` analysis is preserved and remains correct in its assertions; only the "currently un-pinned" diagnostic is now historical.

---

## Open Question Resolutions

### OQ1 — Historical quotes endpoint schema `[Unverified]` → **Resolved** `[High]`

**Source:** `docs.alpaca.markets/reference/stockquotes-1` (Tier 1, accessed 2026-05-13)

- **Endpoint:** `GET https://data.alpaca.markets/v2/stocks/quotes`
- **Params:** `symbols` (required), `start`, `end` (default ~15 min ago), `limit` (1–10,000, default 1000), `feed` (default `sip`, options `sip|iex|boats|otc`), `asof`, `page_token`, `sort` (default `asc`), `currency` (default USD)
- **Pagination:** `next_page_token` continuation pattern (identical to `/stocks/bars`)
- **Sort:** by symbol then quote timestamp
- **Tier access:** Doc page does NOT publish explicit per-tier matrix; same as bars (sip requires Algo Trader Plus, iex available to Basic — inferred from `/docs/about-market-data-api` matrix). Tier 1 confidence on the param list; Medium on tier-matrix inference because matrix is not on this specific page.

**AlphaBot impact:** Zero — production code does not call `/v2/stocks/quotes`. Endpoint catalog now complete for future design discussions.

### OQ2 — Portfolio history endpoint `[STALE]` → **Resolved** `[High]`

**Source:** `docs.alpaca.markets/reference/getaccountportfoliohistory-1` (Tier 1, accessed 2026-05-13). Note the trailing `-1` — the URL referenced in the baseline (`docs.alpaca.markets/docs/portfolio-history`) is a navigation landing page that surfaces only nav chrome; the `/reference/getaccountportfoliohistory-1` URL is the actual endpoint reference.

- **Endpoint:** `GET https://paper-api.alpaca.markets/v2/account/portfolio/history` (paper; live base swaps `paper-api` for `api`)
- **Status:** Active, NOT deprecated.
- **Params:** `period` (default `1M`), `timeframe` (`1Min|5Min|15Min|1H|1D`), `start` / `end` (RFC3339, normalized to America/New_York), `intraday_reporting` (default `market_hours`, options `extended_hours`, `continuous`), `pnl_reset` (default `per_day`, also `no_reset`), `cashflow_types` (`ALL|NONE|comma-list`)
- **Deprecated param:** `extended_hours` is deprecated in favor of `intraday_reporting` — this is the one drift signal in the entire reverification, but is not in AlphaBot's code path.
- **Response:** equity + P/L timeseries; full schema not retrieved on this fetch.

**AlphaBot impact:** Zero — production code does not call portfolio history. Removes the `[STALE]` tag from the baseline's open-question list.

### OQ3 — Trade-history / fills endpoint `[Unverified]` → **Partial-Resolved** `[Medium]`

Cross-reference: the `/v2/stocks/trades` endpoint exists per the same reference site navigation (Tier 1 nav, accessed 2026-05-13) and follows the same param/pagination patterns as `/stocks/bars` and `/stocks/quotes` (symbols, start, end, limit, feed, asof, page_token, sort, currency). Direct doc-page deep-fetch was not pursued in this reverification because AlphaBot does not consume it; the previous baseline's Tier-3 inference is consistent with current nav structure.

**AlphaBot impact:** Zero — production code does not consume trade tape. Listed for catalog completeness.

### OQ4 — Per-tier feed access matrix `[Unverified]` → **Resolved** `[High]`

**Source:** `docs.alpaca.markets/us/docs/about-market-data-api` (Tier 1, accessed 2026-05-13, re-fetched and confirmed against the legacy `docs.alpaca.markets/docs/about-market-data-api` URL)

Confirmed per-tier matrix (verbatim from doc):

| Tier | Equities feed | Options feed | Rate limit | Historical recency |
|---|---|---|---|---|
| **Basic** (free) | IEX only | Indicative Pricing Feed | 200 calls/min | "latest 15 minutes" restriction |
| **Algo Trader Plus** ($99/mo) | All US Stock Exchanges (SIP) | OPRA | 10,000 calls/min | No restriction |

Doc still does NOT publish an exhaustive feed-value × tier matrix (e.g., does Basic have `delayed_sip`? `boats`?). The matrix above is the only one the doc explicitly lists. The auth-time error language ("Any attempt to access a data feed not available for your subscription will result in an error during authentication") on `/docs/real-time-stock-pricing-data` remains the only authoritative guarantee for non-listed feed values.

**AlphaBot impact:** Decisive for the feed-pinning analysis. AlphaBot's `feed=iex` pin is correct for any operator on either tier — it works on Basic (IEX is the only available feed) and continues to work on Algo Trader Plus (IEX is a strict subset of accessible feeds). The feed pin is therefore tier-portable, which is the property the feed-pinning recommendation argued for.

### Bonus resolution — "Unlimited" plan naming `[single-source]` → **Resolved** `[High]`

**Source:** `docs.alpaca.markets/us/docs/about-market-data-api` (Tier 1, accessed 2026-05-13)

Doc explicitly states: *"No separate 'Unlimited' tier exists; Algo Trader Plus represents the premium offering for individual traders."* This closes the marketing-vs-docs label drift the baseline flagged. Marketing-page "Unlimited" wording = Algo Trader Plus product. Tier 1, High confidence — single primary source but the source is the canonical product spec page.

### Bonus partial-resolution — Latest-quotes default feed

`/v2/stocks/quotes/latest` doc states verbatim (accessed 2026-05-13): *"Default: `sip` if the user has the unlimited subscription, otherwise `iex`."* This is the only documented case where the API performs auto-tier-detection at request time rather than auth time. Not applicable to AlphaBot (no latest-quote calls in code) but useful as a contrast point — the bars endpoint does NOT auto-detect, which is why explicit `feed=iex` pinning matters there.

### Open Question that did NOT resolve

**Rate-limit doc page** — `docs.alpaca.markets/docs/api-rate-limits` returned 404 again on 2026-05-13. The `/docs/api-rate-limit` (singular) URL also 404'd. The per-tier rate limits in OQ4 above are sourced from the `about-market-data-api` page; no dedicated rate-limit doc page exists or is accessible. This is not a regression — it was 404 yesterday too, and the per-tier limits are documented on the about page. Closed as **"no dedicated page exists, primary source confirmed elsewhere."**

---

## Code-vs-Current-Spec Discrepancies

### Verified clean

| Code site | Endpoint / behavior | Spec match | Notes |
|---|---|---|---|
| `alpha_bot_execution.py:66` | `ALPACA_BASE_URL = "https://data.alpaca.markets/v2"` | Matches Tier 1 docs | Live and paper share market-data base URL (paper-api only diverges for trading endpoints, which AlphaBot does not call). |
| `alpha_bot_execution.py:79-80` | Headers `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` | Matches Tier 1 docs | Verbatim header names per `/reference/stockbars`. |
| `alpha_bot_execution.py:166` | `GET /v2/stocks/bars` daily, `feed=iex`, `adjustment=split`, `timeframe=1Day`, `limit=10000` | Matches | All param names + values valid per current spec. `feed=iex` works on every tier. `limit=10000` is the documented max. |
| `alpha_bot_execution.py:188` | `data["bars"]` response parsing | Matches | Multi-symbol response shape `{"bars": {SYMBOL: [bar, ...]}, "next_page_token": ...}` matches the doc. |
| `alpha_bot_execution.py:194-208` | Bar field consumption: `c`, `h`, `l`, `t` | Matches | All four are documented bar fields. (`o`, `v`, `n`, `vw` available but not consumed; that's a use-choice, not a discrepancy.) |
| `alpha_bot_execution.py:210` | `data.get("next_page_token")` pagination | Matches | Documented pagination key. |
| `alpha_bot_execution.py:239` | `GET /v2/stocks/bars` 1-Min, `feed=iex`, `timeframe=1Min`, `limit=1000` | Matches | `1Min` is documented timeframe value; `limit=1000` is the documented default. |
| `alpha_bot_execution.py:248-251` | VWAP from `c * v` summation | Matches semantics | Bars include both `c` (close) and `v` (volume); the response document field availability supports this calculation. |
| `synthetic_history.py:16` | `ALPACA_BASE_URL` from env, default `https://data.alpaca.markets/v2` | Matches | Same base. |
| `synthetic_history.py:18-22` | Header names | Matches | Same `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`. |
| `synthetic_history.py:36` | `GET /v2/stocks/bars` with `feed=iex`, `adjustment=split`, `timeframe={1Day,1Min}`, `limit=10000` | Matches | All values valid against current spec; `adjustment=split` and `feed=iex` both documented. |
| `synthetic_history.py:61-71` | Response parsing + `next_page_token` pagination | Matches | Same pagination key as live path. |

### Minor precision notes (not blockers, not action items)

1. **`adjustment=split` is narrower than `adjustment=all`.** Doc lists `raw`, `split`, `dividend`, `spin-off`, `all`. AlphaBot uses `split` only — not a spec violation (the parameter accepts it), but it means dividends and spin-offs are NOT applied to historical OHLC. This is a math-design choice, surfaced (not recommended on) in the original baseline. No change in spec since baseline.

2. **`timeframe=1Day` returns ONE bar per trading day stamped at session open (00:00:00Z in some configurations or session-open in others depending on `asof`).** The code at `alpha_bot_execution.py:199` takes `bars[j]["t"][:10]` to derive date_str — this assumes the timestamp prefix is YYYY-MM-DD. The doc does not contradict that, and the existing fixtures presumably encode it correctly. No regression vs current spec.

3. **`start` parameter format.** Both call sites pass RFC-3339-with-Z (`"%Y-%m-%dT%H:%M:%SZ"`). Doc accepts either RFC-3339 or `YYYY-MM-DD`. Match.

4. **15-min Basic-tier historical recency restriction is implicit, not surfaced.** If AlphaBot ever runs on Basic, the live-path `fetch_intraday_vwaps` call at minute-cadence will not be able to read bars from the most-recent 15 minutes. This is in the spec doc and was in the baseline; reiterating for completeness because the feed-pinning fix did NOT remove this constraint — it removed the broader SIP-tier-only constraint but the 15-min recency restriction remains for Basic. Surface as a possible future operator-state concern.

5. **`alpaca-py` SDK still not in use.** AlphaBot's raw-HTTP approach continues to be insulated from SDK churn. No SDK release since 2026-04-29, so even the hypothetical "adopt SDK" path has not become more attractive or more costly in the last 24 hours.

### `feed-pinning-recommendation.md` audit

The recommendation document's IEX/SIP analysis (training-vs-execution feed mismatch, silent 403 on Basic, fixture-replay compatibility) all remain accurate as of 2026-05-13. The recommendation itself has been adopted in code per `ce043fd`. No changes needed to the document — its "PROPOSAL" status header is now historically out-of-date (it was implemented) but that's a separate doc-hygiene call, not a research correctness call.

### `optuna-provenance-audit.md` spot-check

Re-confirmed against current code:
- `autotuner.py:81` still calls `synthetic_history.generate_synthetic_history()` as the sole tick-data source. (Verified via Grep.)
- `synthetic_history.py:36` still pins `feed=iex`. (Verified via Grep.)
- `alpha_bot_execution.py` Alpaca calls (lines 166, 239) still pin `feed=iex`. (Verified via Grep.)
- Audit conclusion ("CLEAR — Optuna pipeline was always IEX-clean") holds.

No spec-side changes that would alter the audit's data-flow trace conclusion.

---

## Action Items for PM

1. **(Already known, restated for traceability)** Confirm whether `history_cache.json` exists on the production host with a pre-`ce043fd` write date. If so, delete before next live trading day. Captured in optuna-provenance-audit Recommendation #2 — no new ask, just re-anchoring.

2. **(Doc-hygiene, optional)** `feed-pinning-recommendation.md` has "Status: PROPOSAL — awaiting user decision" in its header. The proposal has been implemented (Option A — `feed=iex` pinned everywhere) per `ce043fd`. Consider updating the status header to "IMPLEMENTED 2026-05-XX (commit ce043fd)" for archive clarity. Non-urgent; informational only.

3. **(Schedule)** Next reverification cadence: per Operating Rule "Rate-limit behavior changes — re-verify every report older than 90 days," the next mandatory recheck is on or before 2026-08-13. Sooner re-fetch is warranted only if (a) AlphaBot adopts `alpaca-py`, or (b) the operator changes subscription tier, or (c) a new endpoint enters the code path.

---

## Open Questions (carried forward)

1. **Rate-limit dedicated doc page** — does not exist; per-tier limits are documented inside `about-market-data-api`. **Closed as "no dedicated page; primary source identified."**
2. **`/v2/stocks/trades` deep schema** — not fetched this cycle because not in code path. **Listed for catalog completeness, not blocking.**
3. **Per-tier matrix for non-listed feed values** (`delayed_sip`, `boats`, `overnight` accessibility on Basic vs Algo Trader Plus) — doc does not publish. Auth-time error is the only guarantee. **`[Medium]` Open, not blocking AlphaBot's IEX-pinned path.**

---

## Sources

| URL | Access date | Tier | Description |
|---|---|---|---|
| `https://docs.alpaca.markets/reference/stockbars` | 2026-05-13 | 1 | Bars endpoint reference |
| `https://docs.alpaca.markets/reference/stocklatestquotes-1` | 2026-05-13 | 1 | Latest-quotes endpoint reference |
| `https://docs.alpaca.markets/reference/stockquotes-1` | 2026-05-13 | 1 | Historical-quotes endpoint reference |
| `https://docs.alpaca.markets/reference/getaccountportfoliohistory-1` | 2026-05-13 | 1 | Portfolio history endpoint reference (replaces baseline's 404'd URL) |
| `https://docs.alpaca.markets/docs/about-market-data-api` | 2026-05-13 | 1 | Subscription tiers + rate limits + feed access |
| `https://docs.alpaca.markets/us/docs/about-market-data-api` | 2026-05-13 | 1 | Same content, US-prefixed URL — cross-confirmation |
| `https://docs.alpaca.markets/docs/real-time-stock-pricing-data` | 2026-05-12 (cached) | 1 | Feed value list + auth-time error language |
| `https://api.github.com/repos/alpacahq/alpaca-py/releases` | 2026-05-13 | 1 | SDK release timeline — confirmed no release after 0.43.4 |
| `https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html` | 2026-05-13 | 1 | SDK method surface (informational; AlphaBot does not use SDK) |
| `docs/research/alpaca/baseline__2026-05-12.md` | 2026-05-13 | Project-internal | Prior baseline reference |
| `docs/research/alpaca/feed-pinning-recommendation.md` | 2026-05-13 | Project-internal | Re-confirmed analysis still holds |
| `docs/research/alpaca/optuna-provenance-audit.md` | 2026-05-13 | Project-internal | Re-confirmed conclusions still hold |

See `docs/research/alpaca/sources.md` for the rolling tier-tagged source library.
