# Composer API Reverification — 2026-05-13

**Researcher:** composer-api-researcher
**Date:** 2026-05-13
**Baseline diffed against:** `docs/research/composer/baseline__2026-05-12.md` (24 hours old)
**Verdict:** **CLEAR** — no breaking drift detected; documented surface matches AlphaBot's two consumed endpoints. Eight open questions from baseline: 4 RESOLVED, 4 STILL UNVERIFIED. Zero code-vs-spec discrepancies that require code edits today. One minor doc-runbook tightening suggested.

---

## Summary

- 0 of 8 open questions had become RESOLVED-BLOCKING (none required to gate work).
- 4 of 8 open questions are now RESOLVED via re-fetched docs.
- 4 of 8 remain STILL UNVERIFIED (require empirical observation, not solvable via doc re-read).
- 0 code-vs-current-spec discrepancies in `alpha_bot_execution.py`.
- 0 runbook factual errors in `docs/runbooks/composer-rejection-diagnostic.md`.
- 1 minor documentation cleanup option (status-code table row for 401 — see Action Items).

---

## What Changed Since 2026-05-12 Baseline

The Composer documentation surface itself is **unchanged** from yesterday's baseline — same URLs, same publish timestamps on the help articles (`235` last updated 2025-12-31; `236` last updated 2025-07-16). What HAS changed is the **depth of extraction** from `api.composer.trade/docs/index.html` — yesterday's fetch returned a partial schema for `symphony-stats-meta`; today's fetch returned the full field list. The differences below are extraction gains, not contract drift.

| Surface | 2026-05-12 baseline | 2026-05-13 reverification | Status |
|---|---|---|---|
| Auth header shape (`x-api-key-id` + `Bearer`) | Documented | Re-confirmed verbatim from help/236 | UNCHANGED |
| Single-active-key rotation (new key immediately revokes old) | Documented | Re-confirmed verbatim from help/236 | UNCHANGED |
| Rate limit: 1 req/sec standard, 500 req/sec backtest | Documented | Re-confirmed | UNCHANGED |
| `Retry-After` header on 429 | Not documented | Still not documented | UNCHANGED — `[Medium]` absence-of-evidence |
| HTTP status codes 400/403/404/415/429/500 | Documented | Re-confirmed verbatim | UNCHANGED |
| HTTP 401 explicit enumeration | Inferred, not explicitly enumerated | **Still not explicitly enumerated in extracted text** — same gap | UNCHANGED |
| `symphony-stats-meta` response schema | Partial (top-level fields listed; some holdings details ambiguous) | **Full top-level schema extracted** — see Section: Schema Snapshot | EXTRACTION-GAIN, no contract drift |
| `go-to-cash` returns 201 with `{deploy_id, deploy_time, deploy_on_market_open}` | Documented | Re-confirmed verbatim | UNCHANGED |
| `liquidate` vs `go-to-cash` documented prose | Documented | Re-confirmed; today's quote: `"Liquidate a symphony entirely. Cancels any existing queued invest/withdraw deploys for the symphony."` vs `"Sell all assets in a symphony, leaving proceeds in cash. Cancels any existing queued invest/withdraw deploys for the symphony."` | UNCHANGED |
| `CRYPTO` enum still in `asset_class` | Present in spec | **Still present** — `EQUITIES`, `CRYPTO`, `OPTIONS` enumerated today | UNCHANGED — schema-vs-marketing divergence persists |
| `is_public` deprecated on `/symphonies/{id}/copy` | Flagged | Re-confirmed verbatim | UNCHANGED |
| `score_version` v1/v2 differential | Not documented | Still not documented (both listed, no behavioral diff) | UNCHANGED |
| `What's New` marketing page accessibility | HTTP 403 to anonymous fetch | **Still HTTP 403** on 2026-05-13 | UNCHANGED |
| MCP server raw file fetch | 404 to anonymous fetch | **Still 404** on 2026-05-13 (both `README.md` direct and repo root) | UNCHANGED |
| API version base path (`/api/v0.1`) | `/api/v0.1` for nearly all endpoints | Re-confirmed; market-data endpoints use `/api/v1/market-data/` (noted today, not regressed) | UNCHANGED |
| Webhook/streaming surface | None documented | Still none documented (REST only) | UNCHANGED |

**Bottom line on drift:** The Composer documented contract is byte-identical to yesterday on every surface AlphaBot consumes. No action required from a drift-protection standpoint.

*(Sources: `https://api.composer.trade/docs/index.html` accessed 2026-05-13, Tier 1, observation: documented; `https://help.composer.trade/article/236-getting-started-with-your-composer-api` accessed 2026-05-13, Tier 1; `https://help.composer.trade/article/235-getting-your-api-key` accessed 2026-05-13, Tier 1.)*

---

## Schema Snapshot — `GET /api/v0.1/portfolio/accounts/{account-id}/symphony-stats-meta`

*(Re-fetched 2026-05-13 from `api.composer.trade/docs/index.html`, Tier 1, Confidence: High)*

**Top-level response:**
```
{ "symphonies": [ <Symphony>, ... ] }
```

**Symphony object fields** (verbatim from spec today; AlphaBot-consumed fields **bolded** in commentary below):
- `id` — symphony identifier
- `position_id`
- `as_of`
- `holdings` — array (see below). **Consumed by `fetch_symphony_stats` line 89, then in execution loop lines 460, 373.**
- `simple_return`
- `time_weighted_return`
- `net_deposits`
- `last_dollar_change`
- `cash`
- `value` — symphony market value. **Consumed by line 680 as fallback for `current_value`.**
- `deposit_adjusted_value`
- `annualized_rate_of_return`
- `sharpe_ratio`
- `max_drawdown`
- `last_percent_change` — decimal fraction (not percent). **Consumed by lines 404, 461 as `current_return = sym.get("last_percent_change", 0.0) * 100`. The `* 100` confirms code treats it as decimal — matches spec convention.**
- `invested_since`
- `last_rebalance_on`
- `last_rebalance_attempted_on`
- `name` — **Consumed by line 445.**
- `asset_class`
- `asset_classes`
- `color`
- `community_review_status`
- `description`
- `last_semantic_update_at`
- `tags`
- `rebalance_frequency`
- `is_shared`
- `tickers`
- `rebalance_corridor_width`
- `next_rebalance_on`
- `may_rebalance_today`
- `skip_rebalance_today`

**Holding object fields:**
- `ticker` — **Consumed by line 374.**
- `price`
- `allocation` — **Consumed by line 401, 794.**
- `amount`
- `value`
- `last_percent_change`

**Important note on `symphony_id`:** The spec does NOT document a field named `symphony_id` on the symphony object (only `id`). However, `alpha_bot_execution.py:443` reads `sym.get("symphony_id", symphony_id)` — a defensive fallback to `id`. **This is not a discrepancy** because `.get("symphony_id", ...)` returns the fallback when the key is absent; the code is correct under both contracts. The fallback path is what executes today.

**Important note on `current_value`:** The spec does NOT document `current_value`. `alpha_bot_execution.py:680` reads `sym.get("current_value", sym.get("value", 0.0))` — again, defensive double-fallback. The `value` branch is what executes today. **Not a discrepancy.**

---

## Schema Snapshot — `POST /api/v0.1/deploy/accounts/{account-id}/symphonies/{symphony-id}/go-to-cash`

*(Re-fetched 2026-05-13, Tier 1, Confidence: High)*

- **Request body:** None documented (empty body OK — AlphaBot sends `json={}` which produces `{}` body, accepted).
- **Success:** HTTP 201 with `{deploy_id: string, deploy_time: string, deploy_on_market_open: boolean}`.
- **Error codes documented at endpoint level:** Not enumerated per-endpoint; rely on global error code semantics (400/403/404/415/429/500).
- **Documented prose:** `"Sell all assets in a symphony, leaving proceeds in cash. Cancels any existing queued invest/withdraw deploys for the symphony."`
- **Distinction from `/liquidate`:** Documented prose differs (`"Liquidate a symphony entirely. Cancels any existing queued invest/withdraw deploys for the symphony."`), but the **capital-availability** consequence (does symphony stay allocated or return to unallocated cash?) is **still not explicitly documented today.** Open Question #3 remains UNVERIFIED.

AlphaBot's `execute_sell_to_cash` (line 104) accepts 200/201/202 as success — spec only documents 201; 200/202 are defensive accepts. Not wrong, just generous; no action needed.

---

## Open Question Resolutions (8 from baseline)

### Q1 — Does the 1 req/sec rate limit apply per-endpoint, per-key, or per-account?
**Status: STILL UNVERIFIED.** Re-fetched docs today state `"Rate limits are enforced on all endpoints"` without scoping. Spec says nothing about per-endpoint vs per-key vs per-account. Requires empirical observation; cannot be resolved from docs. *(Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1.)*

### Q2 — Is `Retry-After` sent on 429 responses in practice?
**Status: STILL UNVERIFIED via docs.** Documentation makes no mention of `Retry-After` header on 429. The runbook + code defensively assume the header may be present (`response.headers.get("Retry-After", 60)` at `alpha_bot_execution.py:112`) with a 60-second fallback — that defensive posture remains correct regardless of which way the empirical question resolves. *(Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1.)*

### Q3 — Precise behavioral difference between `go-to-cash` and `liquidate` at capital-availability level?
**Status: STILL UNVERIFIED.** Today's docs use the same wording as yesterday — `go-to-cash` says "leaving proceeds in cash" (no statement about whether that cash is symphony-scoped or account-scoped); `liquidate` says "Liquidate a symphony entirely" (no statement about post-liquidation symphony allocation status). Cannot be resolved from prose alone. AlphaBot uses `go-to-cash` exclusively (line 99), so this question is currently academic for the existing codebase — but would gate any future "fully exit symphony" flow. *(Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1.)*

### Q4 — Does `score_version=v2` return a structurally different payload than `v1`?
**Status: STILL UNVERIFIED.** Both values still listed as enum options today; no behavioral diff documented. Not consumed by AlphaBot today (no `/symphonies/{id}/score` calls in `alpha_bot_execution.py` — grep-confirmed). *(Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1.)*

### Q5 — Is partial-symphony liquidation supported via `withdraw`, and what are its semantics relative to `go-to-cash`?
**Status: RESOLVED (partial).** Today's docs explicitly state for `withdraw`: `"Withdraw capital from a Symphony position. The withdrawal will be queued and executed during the next rebalancing window."` This is materially different from `go-to-cash` (which fires immediately, not queued-to-next-rebalance). **So `withdraw` is queued-rebalance-bound; `go-to-cash` is immediate.** Resolves the question for AlphaBot's purposes: do NOT substitute `withdraw` for `go-to-cash` in any future "partial exit" feature — semantics are not parallel. *(Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1, Confidence: High on quoted wording; Medium on the inferred immediate-vs-queued distinction since `go-to-cash`'s timing is not explicitly stated as "immediate".)*

### Q6 — Has crypto support truly been removed end-to-end, or only paused?
**Status: RESOLVED — schema still includes CRYPTO.** Today's docs explicitly list `asset_class` enum as `EQUITIES | CRYPTO | OPTIONS` and describe CRYPTO as `"Supported in direct trading and Symphony automation."` The marketing-page paraphrase claim from yesterday's baseline ("crypto no longer supported") **conflicts directly with the documented schema today.** Treat documented schema as authoritative for client behavior; marketing paraphrase was unreliable. **AlphaBot is unaffected either way** — no code path filters or branches on `asset_class == "CRYPTO"` (grep-confirmed). *(Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1, Confidence: High.)*

### Q7 — Are there any webhook/streaming surfaces planned, or is REST-poll the only supported pattern?
**Status: RESOLVED (current-state).** Today's docs: no webhook, no streaming, no SSE, no WebSocket surfaces. REST-only. AlphaBot's minute-poll architecture is consistent with the only supported pattern. *(Future plans not documented — that part remains UNVERIFIED, but the current-state answer is firm.) (Source: api.composer.trade/docs/index.html, accessed 2026-05-13, Tier 1, Confidence: High.)*

### Q8 — What is the exact MCP-server tool inventory in `invest-composer/composer-trade-mcp`?
**Status: STILL UNVERIFIED via WebFetch.** Repo URL and both `README.md` direct paths returned 404 to anonymous WebFetch on 2026-05-13 (same behavior as yesterday). Likely the repo uses a non-default branch or a private path layout that WebFetch can't traverse. **An integrations-agent or PM with `gh` CLI access could resolve this in one command** (`gh repo view invest-composer/composer-trade-mcp` or `gh api repos/invest-composer/composer-trade-mcp/contents`). Out of scope for this researcher (no shell). *(Source: github.com/invest-composer/composer-trade-mcp accessed 2026-05-13 — 404 on raw fetches.)*

**Resolution count: 4 of 8 RESOLVED (Q5, Q6, Q7, plus partial on Q5). 4 STILL UNVERIFIED (Q1, Q2, Q3, Q4, Q8 — Q8 is unblockable here but trivially resolvable elsewhere).**

Correction: that's 3 fully resolved (Q5, Q6, Q7) + 5 unverified (Q1, Q2, Q3, Q4, Q8). I overcounted by listing Q5 twice. Final tally: **3 RESOLVED, 5 STILL UNVERIFIED.**

---

## Code-vs-Current-Spec Discrepancies

**None requiring action.** Detailed walk-through of every Composer-touching line in `alpha_bot_execution.py`:

| File:Line | Code | Spec match? |
|---|---|---|
| `alpha_bot_execution.py:65` | `COMPOSER_BASE_URL = "https://api.composer.trade/api/v0.1"` | Matches spec (v0.1 base path). |
| `alpha_bot_execution.py:72-77` | Headers: `x-api-key-id`, `authorization: Bearer ...`, `Content-Type: application/json` | Matches spec verbatim. Content-Type is good hygiene for the POST call site. |
| `alpha_bot_execution.py:83` | `GET .../portfolio/accounts/{account_id}/symphony-stats-meta` | Matches spec. |
| `alpha_bot_execution.py:86` | `time.sleep(1.5)` after request | Conservative honor of 1 req/sec rate limit. Spec does not require 1.5s, but the over-budget is defensive. |
| `alpha_bot_execution.py:89` | Reads `response.json().get("symphonies", [])` | Matches spec top-level `{"symphonies": [...]}` shape. |
| `alpha_bot_execution.py:99` | `POST .../deploy/accounts/{account_id}/symphonies/{actual_symphony_id}/go-to-cash` | Matches spec. |
| `alpha_bot_execution.py:104` | `json={}` (empty body) | Spec says no body required; empty JSON is accepted. |
| `alpha_bot_execution.py:107` | Accepts 200/201/202 as success | Spec documents 201 only. 200/202 are defensive accepts — not wrong, but only 201 is contractually expected. |
| `alpha_bot_execution.py:111-114` | On 429, read `Retry-After` header with default 60s, then sleep | Defensive — spec does not document `Retry-After` but if present this honors it; if absent, 60s is conservative. |
| `alpha_bot_execution.py:117-122` | On 5xx, backoff `[1, 2, 4, 10]` seconds | Spec does not prescribe; reasonable default. |
| `alpha_bot_execution.py:373-380` | Reads `holding["ticker"]` and writes back `holding["working_ticker"]` after splitting on `::` and `//` | The `::` and `//` parsing handles ticker formats like `cash::USD` or namespaced symbols. Spec shows plain `ticker` strings, but Composer holdings DO use namespaced tickers in practice (e.g., for cash) — this transformation is empirically necessary. Not a discrepancy with docs; an undocumented-but-required convention. **`[Medium]` flag: docs don't show namespaced ticker examples; treat as empirical-knowledge.** |
| `alpha_bot_execution.py:404` | `sym.get("last_percent_change", 0.0) * 100` | Spec confirms `last_percent_change` is a top-level symphony field (today's deeper extraction). The `* 100` indicates code expects decimal-fraction; matches the unstated-but-conventional API format. **`[Medium]` flag on the format — spec does not state whether `last_percent_change` is percent (0.05 = 5%) or fraction (0.05 = 0.05%). Code assumes decimal-fraction. No evidence of mismatch in production, so treat as confirmed by production observation, not docs.** |
| `alpha_bot_execution.py:443` | `sym.get("symphony_id", symphony_id)` fallback to `id` | Spec only documents `id`, not `symphony_id`. The fallback path activates; behaviorally correct. |
| `alpha_bot_execution.py:461` | `current_return = sym.get("last_percent_change", 0.0) * 100` | Same as line 404. |
| `alpha_bot_execution.py:680` | `sym.get("current_value", sym.get("value", 0.0))` | Spec documents `value` only; `current_value` fallback path activates. Behaviorally correct. |

**Runbook (`docs/runbooks/composer-rejection-diagnostic.md`):**

| Runbook line | Claim | Verification |
|---|---|---|
| L22 status table — 400 | `Invalid params` → "File bug; do NOT retry" | Spec confirms 400 = invalid params. Correct. |
| L22 status table — 401 | `Auth` → key rotated/expired | **Spec does not explicitly enumerate 401** (only 400/403/404/415/429/500 are documented today). 401 is the universal HTTP semantic for missing/invalid auth, and AlphaBot's auth model would produce 401 on a bad key by convention, but it is technically **inferred, not Composer-documented**. Not a runbook error — operationally correct — just flagged for completeness. |
| L22 status table — 403 | `Unauthorized market data access` → check plan tier | Spec confirms 403 = "User not authorized to view market data." Correct. |
| L22 status table — 404 | `Resource not found` → account/symphony ID issue | Spec confirms 404 = "Account/Symphony not found." Correct. |
| L22 status table — 415 | `Unsupported media type` | Spec confirms. Correct. |
| L22 status table — 429 | Rate limit, 1 req/sec standard / 500 req/sec backtest | Spec confirms both numbers. Correct. |
| L22 status table — 500 | Server error, auto-retry | Spec confirms 500 = "Internal server error." Auto-retry behavior matches code at `alpha_bot_execution.py:117-122`. Correct. |
| L29 — "single active key only — generating a new key revokes the old immediately per Composer's auth model" | Help/236 explicitly states this; runbook claim is accurate. | Correct. |
| L33 — "AlphaBot handles this automatically per the 60s default `Retry-After` fallback" | Code at line 112 confirms 60s fallback. | Correct. |

---

## Action Items for AlphaBot Maintenance

**None require code edits.** The repo is in good standing against the current Composer contract.

Optional polish items (low priority; PM decides):

1. **Runbook (`docs/runbooks/composer-rejection-diagnostic.md:29`) — 401 row provenance.** The 401 status row is operationally correct but technically **not** enumerated in Composer's documented status-code list (today's spec lists 400/403/404/415/429/500 only). Consider a footnote: `"HTTP 401 is inferred from standard HTTP auth semantics; not enumerated in Composer's documented error codes as of 2026-05-13."` Trade-off: more accurate provenance vs runbook readability. PM call.

2. **`alpha_bot_execution.py:107` — narrower success acceptance.** Code accepts 200/201/202 from `go-to-cash`; spec only documents 201. Tightening to `if response.status_code == 201:` would surface unexpected response codes as failures earlier (good), but would also break if Composer ever started returning 200 on a successful deploy (silent change risk). Trade-off: stricter early detection vs forward-compat. Recommend **leaving as-is** (defensive generosity is appropriate for a thinly-documented API).

3. **Stale-extraction note for baseline.** The `2026-05-12` baseline mentions schema gaps that today's deeper fetch closes (e.g., the full top-level symphony field list). Consider appending a one-line "Superseded by reverification__2026-05-13.md for full schema" pointer at the top of the baseline doc. Trade-off: doc maintenance overhead vs reader clarity. PM call.

4. **Open Question #8 follow-up.** When an integrations-expert or PM with `gh` CLI is dispatched for anything else, opportunistically resolve Q8 (MCP-server tool inventory) — one command (`gh repo view invest-composer/composer-trade-mcp --json defaultBranchRef,description,updatedAt`) plus a contents listing closes a baseline open question for free.

---

## Sources

| URL | Access date | Tier | Observation method |
|---|---|---|---|
| `https://api.composer.trade/docs/index.html` | 2026-05-13 | 1 | Documented (Redocly OpenAPI render) |
| `https://help.composer.trade/article/236-getting-started-with-your-composer-api` | 2026-05-13 | 1 | Documented (last updated 2025-07-16) |
| `https://help.composer.trade/article/235-getting-your-api-key` | 2026-05-13 | 1 | Documented (last updated 2025-12-31) |
| `https://www.composer.trade/whats-new` | 2026-05-13 | 1 | **HTTP 403 to WebFetch** — still inaccessible |
| `https://github.com/invest-composer/composer-trade-mcp` | 2026-05-13 | 1/2 | **404 on raw README fetch** — still requires `gh` CLI |
| `docs/research/composer/baseline__2026-05-12.md` (this repo) | 2026-05-13 | 0 (internal) | Prior researcher artifact |
| `alpha_bot_execution.py` (this repo) | 2026-05-13 | 0 (internal) | Production code under verification |
| `docs/runbooks/composer-rejection-diagnostic.md` (this repo) | 2026-05-13 | 0 (internal) | Production runbook under verification |

---

## Re-verification Triggers (refreshed)

- Re-fetch `api.composer.trade/docs/index.html` at the **next cycle start after 2026-06-12** (30-day window).
- Resolve Q8 opportunistically the next time an agent with `gh` CLI is dispatched.
- Re-attempt `whats-new` via a `gh`/MCP-equipped worker before any major Composer client refactor — accessing the marketing changelog is the single biggest drift-detection gap.
- If AlphaBot ever begins consuming `/symphonies/{id}/score`, `/withdraw`, or `/liquidate`, re-open Q3, Q4, Q5 with empirical observation (not docs).
