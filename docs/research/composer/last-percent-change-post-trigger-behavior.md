# Research Report: `last_percent_change` Post-Trigger Behavior

**Researcher:** composer-api-researcher
**Date:** 2026-05-16
**Confidence Summary:** Evidence ceiling reached at Outcome 3 (Undocumented/Unknown). No official documentation, no community empirical observation, and no code-level evidence addresses whether `last_percent_change` continues updating or freezes after an external go-to-cash trigger. M1F.7 must treat this field's post-trigger validity as unverified until the operator runs empirical verification.

---

## Research Questions

1. Does `last_percent_change` from the `symphony-stats-meta` endpoint continue updating with market moves after AlphaBot fires a sell signal (via `go-to-cash` or Alpaca direct sell)?
2. Does Composer freeze or null that field once it detects the position is in cash?
3. Is the field's computation documented as reflecting actual holdings vs. theoretical allocation?

---

## Verdict

**Outcome 3 — Behavior undocumented / unknown.** `[Low]` confidence that either Outcome 1 or Outcome 2 is correct. The field is confirmed to exist in the response schema, but Composer publishes no behavioral spec for it under post-liquidation conditions. Zero community empirical observations surfaced. M1F's BC-3 resolution is **conditional on operator-run empirical verification before deploy.**

---

## Evidence

### What IS documented

- **Field existence confirmed** `[High]` — `last_percent_change` appears in the `symphony-stats-meta` endpoint response schema at `GET /api/v0.1/portfolio/accounts/{account-id}/symphony-stats-meta`. The field is listed alongside `last_dollar_change`, `simple_return`, `time_weighted_return`, `cash`, and `value`.
  - Source: [api.composer.trade/docs/index.html](https://api.composer.trade/docs/index.html) — fetched 2026-05-16, Tier 1, observation method: documented.

- **`go-to-cash` and `liquidate` endpoints exist** `[High]` — the API exposes `/go-to-cash` and `/liquidate` actions but their documentation describes only the trade mechanics (sell all assets, cancel queued deploys). No documentation covers how the stats fields behave in subsequent `symphony-stats-meta` calls.
  - Source: [api.composer.trade/docs/index.html](https://api.composer.trade/docs/index.html) — fetched 2026-05-16, Tier 1, documented.

- **`last_percent_change` definition** `[Low]` — the docs describe it as "the most recent percentage change" without specifying the computation window (intraday, close-to-close), the reference date, or whether it reflects realized holdings vs. theoretical allocation. Definition is too shallow to infer post-trigger behavior.
  - Source: [api.composer.trade/docs/index.html](https://api.composer.trade/docs/index.html) — fetched 2026-05-16, Tier 1, documented.

### What is NOT documented

- No official doc, help center article, MCP reference, or community thread addresses whether `last_percent_change` reflects actual vs. theoretical allocation under any condition. The following sources were checked and returned no relevant information:
  - [help.composer.trade/article/65](https://help.composer.trade/article/65-how-does-composer-trade) — execution mechanics only; no performance-calc description. Fetched 2026-05-16.
  - [help.composer.trade/article/149](https://help.composer.trade/article/149-why-is-my-symphony-holding-cash) — cash accumulation mechanics only. Fetched 2026-05-16.
  - [help.composer.trade/article/205](https://help.composer.trade/article/205-symphony-swaps-during-liquidations) — swap mechanics during liquidation; no stats-field discussion. Fetched 2026-05-16.
  - GitHub (Hickinvest/composer-quant-tools) — source code not inspectable via anonymous fetch; README silent on API field semantics. Fetched 2026-05-16.
  - Reddit / Discord community search — zero indexed results for `"symphony-stats-meta"` or `"last_percent_change"` on Reddit or Discord as of 2026-05-16.

---

## Methodology

**Phase 1 — Broad sweep:** Searched for Composer API documentation, symphony stats fields, and general liquidation behavior. Identified primary sources: api.composer.trade/docs, help center articles, GitHub org.

**Phase 2 — Targeted deep dive:** Fetched the OpenAPI docs directly to extract the `symphony-stats-meta` schema. Retrieved all help-center articles touching cash, liquidation, and symphony trading mechanics. Attempted to read community client code (Hickinvest/composer-quant-tools).

**Phase 3 — Verification pass:** Ran targeted searches combining `"last_percent_change"` with liquidation, cash, freeze, and update behavior terms across Reddit, Discord, and GitHub code search. Zero corroborating community sources returned.

**Phase 4 — Recency check:** All sources fetched 2026-05-16. The API docs page itself carries no versioning timestamp; rate-limit and schema findings from the 2026-05-12 baseline report are consistent with this session's fetch.

**Known gap:** The MCP server reference implementation (`invest-composer/composer-trade-mcp`) is 404 to anonymous WebFetch and was not inspectable this session. Its source code might contain comments about field semantics. The `gh` CLI could retrieve it.

---

## Recommendation for M1F.7

M1F.7 (`shadow_hwm = max(current_return)`) depends entirely on `last_percent_change` remaining valid and updating post-trigger. Three options:

**Option A — Empirical verification before deploy (recommended path if M1F timeline allows).**
The operator manually triggers `go-to-cash` on a low-value sandbox symphony mid-day (or uses a non-live test account), then polls `symphony-stats-meta` every 60 seconds for the remainder of the trading day and records whether `last_percent_change` moves with the underlying ETF prices. A single 30-minute observation window during active market hours is sufficient to distinguish Outcome 1 from Outcome 2. This is the only way to reach `[High]` confidence.

**Option B — Assume Outcome 2 (freeze) as the conservative default; define an alternate counterfactual.**
If timeline does not allow empirical verification, treat `last_percent_change` as unreliable post-trigger. The alternate counterfactual is a synthetic return computed from the symphony's last-known allocation snapshot (the holding weights at the moment AlphaBot fires) applied to Alpaca market data for those tickers from trigger-time forward. This is constructable from existing AlphaBot data but adds M1F implementation scope.

**Option C — Proceed on Outcome 1 assumption with a runtime sanity check.**
Deploy M1F.7 using `last_percent_change` as-is, but add a runtime assertion: if `current_return` does not change across two consecutive polling cycles after a trigger event (and the underlying market is moving), flag a `STALE_SHADOW_RETURN` warning in the state DB. This surfaces Outcome 2 empirically in production without blocking deploy, but risks polluted `shadow_hwm` data until the flag fires.

Trade-off summary: Option A is lowest risk but requires operator action before deploy. Option B is safe but adds scope. Option C ships fastest but tolerates a window of silent data corruption if Outcome 2 is true.

---

## Open Questions

1. Does the MCP server source code (`invest-composer/composer-trade-mcp`) contain any comments about field computation? Retrievable via `gh api` — low-effort follow-up.
2. Does AlphaBot's `execute_sell_to_cash` (calling Composer's `/go-to-cash` endpoint directly) differ in post-trigger stats behavior from an external Alpaca sell that Composer does not initiate? If AlphaBot routes through Composer's own `/go-to-cash`, Composer is aware of the state change; if it routes purely through Alpaca, Composer may not be.
3. The `fetch_symphony_stats` function in `alpha_bot_execution.py` already polls this endpoint each cycle. Historical logs from production cycles where a sell was fired could answer the empirical question without any new test — if the operator has log retention covering a full day post-trigger.

---

## Sources

| URL | Access date | Tier | Observation method | Notes |
|-----|-------------|------|--------------------|-------|
| [api.composer.trade/docs/index.html](https://api.composer.trade/docs/index.html) | 2026-05-16 | 1 | documented | Primary OpenAPI reference; confirms field existence and endpoint schema |
| [help.composer.trade/article/65](https://help.composer.trade/article/65-how-does-composer-trade) | 2026-05-16 | 1 | documented | Trading mechanics; no stats-field docs |
| [help.composer.trade/article/149](https://help.composer.trade/article/149-why-is-my-symphony-holding-cash) | 2026-05-16 | 1 | documented | Cash mechanics; no post-trigger stats behavior |
| [help.composer.trade/article/205](https://help.composer.trade/article/205-symphony-swaps-during-liquidations) | 2026-05-16 | 1 | documented | Liquidation swap mechanics; no stats-field docs |
| [help.composer.trade/article/236](https://help.composer.trade/article/236-getting-started-with-your-composer-api) | 2026-05-16 | 1 | documented | Auth and rate limit reference; no field-semantic docs |
| [github.com/Hickinvest/composer-quant-tools](https://github.com/Hickinvest/composer-quant-tools) | 2026-05-16 | 3 | repo-listing only | README silent on field semantics; source not inspectable anonymously |
| Reddit / Discord targeted searches | 2026-05-16 | — | search | Zero results for `symphony-stats-meta` or `last_percent_change` |
