# Research Report: Composer.trade API Surface for AI Advisor / Programmatic Backtest

**Researcher:** composer-api-researcher
**Date:** 2026-05-31
**Confidence Summary:** High confidence on all four load-bearing questions (docs location, backtest capability, symphony CRUD, auth). The backtest verdict is the critical output: programmatic backtesting of an inline symphony definition IS supported via a confirmed, officially-documented endpoint.

---

## Research Questions

1. Where does the official Composer API documentation live? Is there a machine-readable OpenAPI/Swagger spec?
2. Does the API expose programmatic backtesting — specifically, can you submit a modified symphony definition and receive backtest results? (LOAD-BEARING GATE)
3. Can you fetch a symphony's definition and construct a variant for asset-swap/logic-change? What does the symphony schema look like?
4. What is the auth flow and what are the rate limits?

---

## Findings

### 1. Docs Location and Machine-Readable Spec

**Primary reference:** `https://api.composer.trade/docs/index.html` — a Redocly-rendered OpenAPI reference.
*(Source: api.composer.trade/docs/index.html, accessed 2026-05-31, Tier 1, observation: documented, Confidence: High)*

**Machine-readable spec:** The Redocly renderer embeds a download link labeled "Download OpenAPI specification." The underlying spec is served at `https://api.composer.trade/docs/swagger.json`. Anonymous `GET /openapi.json` and `GET /openapi.yaml` paths return HTTP 404; `/docs/swagger.json` returns the spec contents (confirmed via fetch, 2026-05-31).
*(Source: api.composer.trade/docs/swagger.json, accessed 2026-05-31, Tier 1, observation: documented, Confidence: High)*

**Help center getting-started article:** `https://help.composer.trade/article/236-getting-started-with-your-composer-api` — last updated 2025-07-16. Covers auth, base URL, rate limits with code examples.
*(Source: help.composer.trade/article/236, accessed 2026-05-31, Tier 1, Confidence: High)*

**Official MCP server:** `https://ai.composer.trade/mcp` (hosted); GitHub: `https://github.com/invest-composer/composer-trade-mcp`. The MCP server is an official Composer product that exposes 28 tools wrapping the REST API. Raw source files returned 404 to anonymous fetch, but tool inventory was confirmed via community aggregator at `https://playbooks.com/mcp/invest-composer/composer-trade-mcp` (accessed 2026-05-31, Tier 2).

**Existing project baseline docs (Tier 1 internal):**
- `docs/research/composer/baseline__2026-05-12.md` — comprehensive endpoint baseline, auth, rate limits, schema snapshots.
- `docs/research/composer/reverification__2026-05-13.md` — 24-hour drift check, all UNCHANGED.
- `docs/research/claude-integration/composer-symphony-logic-endpoint.md` — live-validated `/score` endpoint including actual response shape from a real symphony (1,044,312 bytes for "Planet of Hunted Cascades").

These documents are the highest-quality prior art for this project. This report extends them specifically for the backtest + symphony-CRUD surface the AI Advisor requires.

---

### 2. LOAD-BEARING VERDICT: Programmatic Backtesting

**VERDICT: YES — programmatic backtesting of an inline symphony definition IS available via the official API.**

There are TWO distinct backtest endpoints. Both are documented in `api.composer.trade/docs/swagger.json` (accessed 2026-05-31, Tier 1):

#### Endpoint A — Symphony-ID Backtest
```
POST /api/v0.1/symphonies/{symphony-id}/backtest
```
- Runs a backtest against a **saved symphony** by its ID.
- Rate limit: **500 req/sec** (documented exception — all other endpoints are 1 req/sec).
- Does NOT accept an inline symphony definition; requires a pre-saved symphony ID.
- Useful for backtesting the unmodified live symphony (baseline comparison).

#### Endpoint B — Inline Definition Backtest (THE CRITICAL ONE)
```
POST /api/v0.1/backtest
```
- Accepts an **inline symphony definition** (the full logic tree) without requiring a pre-saved symphony.
- This is the endpoint that gates the entire AI Advisor advisor-proposes-variant-and-backtests flow.
- Rate limit: inherits the standard 1 req/sec limit (NOT the 500 req/sec exception — that exception applies only to the `/{symphony-id}/backtest` path). [Medium — rate limit for this endpoint not separately enumerated; 1 req/sec default applies absent explicit exception.]

**Request body schema for `POST /api/v0.1/backtest`:**
*(Source: api.composer.trade/docs/swagger.json, operation `backtest_api_v0_1_backtest_post`, accessed 2026-05-31, Tier 1, Confidence: High)*

```json
{
  "symphony": {
    "raw_value": {
      "id": "string (UUID)",
      "step": "root",
      "name": "string",
      "description": "string",
      "weight": { "num": "string", "den": "string" },
      "rebalance": "none | daily | weekly | monthly | ...",
      "rebalance-corridor-width": <number>,
      "children": [ ... ]
    }
  },
  "capital": <number, required>,
  "apply_reg_fee": <boolean, required>,
  "apply_taf_fee": <boolean, required>,
  "slippage_percent": <number, required — note: 0.01 = 1%, NOT 1.0>,
  "broker": "<enum, required>: ALPACA_OAUTH | ALPACA_WHITE_LABEL | APEX_LEGACY | alpaca | apex",
  "abbreviate_days": <integer, optional>,
  "apply_subscription": "<optional>: none | monthly | yearly",
  "backtest_version": "<optional>: v1 | v2",
  "spread_markup": <number, optional — e.g. 0.001 = 10bps>,
  "start_date": "<string, optional>",
  "end_date": "<string, optional>",
  "benchmark_symphonies": ["<symphony-id>", ...],
  "benchmark_tickers": ["<ticker>", ...],
  "sparkgraph_color": "<string, optional>"
}
```

The `symphony.raw_value` tree is structurally identical to the response from `GET /api/v0.1/symphonies/{id}/score`. This means: **fetch a symphony's score tree → mutate it (asset swap, indicator threshold change, etc.) → POST /api/v0.1/backtest with the mutated tree**. This is exactly the AI Advisor's required workflow.

**Response shape** (confirmed from swagger.json + docs index, accessed 2026-05-31):
```
{
  "stats": {
    "cumulative_return": <number>,
    "sharpe_ratio": <number>,
    "sortino_ratio": <number>,
    "max_drawdown": <number>,
    "annualized_rate_of_return": <number>,
    "win_rate": <number>,
    "tail_ratio": <number>,
    "calmar_ratio": <number>,
    "<trailing_period>_return": <number>   // 1D, 1W, 1M, 3M, 6M, 1Y, 3Y, 5Y
  },
  "holdings_breakdown": { ... },
  "rebalance_days": [ ... ],
  "costs": {
    "reg_fee": <number>,
    "taf_fee": <number>,
    "slippage": <number>,
    "spread_markup": <number>,
    "subscription": <number>
  },
  "data_warnings": [ ... ],
  "benchmark_comparisons": {
    "<benchmark-id>": {
      "alpha": <number>,
      "beta": <number>,
      "correlation": <number>,
      "cumulative_return": <number>
    }
  }
}
```

**Date range / history:**
- Composer uses **daily adjusted closing prices** accounting for corporate actions (splits, dividends). This is confirmed in help.composer.trade/article/67 (accessed 2026-05-31).
- The documented date-range parameters are `start_date` and `end_date` (ISO 8601 strings); both are optional (backtest uses all available history if omitted).
- Hard history floor: not explicitly documented. The `data_warnings` field in the response surfaces ticker-level insufficient-history warnings. [Medium — exact earliest available date not documented; inferred from docs structure.]

**Fidelity vs UI backtest:**
- The API backtest is the same engine that powers the UI backtest — same cost parameters (`apply_reg_fee`, `apply_taf_fee`, `slippage_percent`, `apply_subscription`). The `backtest_version` parameter (`v1` | `v2`) mirrors the version toggle available in the UI.
- My interpretation: the API and UI backtests are equivalent when given identical parameters. [Medium — inferred from identical parameter set; no explicit "API == UI" statement in docs. A live comparison test would confirm.]

**The MCP server corroboration:** The official MCP server (28 tools) exposes both `backtest_symphony` (inline definition → backtest) and `backtest_symphony_by_id` (saved ID → backtest) as separate tools. This is independent corroboration that both pathways exist at the API layer.
*(Source: playbooks.com/mcp/invest-composer/composer-trade-mcp, accessed 2026-05-31, Tier 2, Confidence: High)*

---

### 3. Symphony Read / CRUD / Composition

All endpoints below: Source `api.composer.trade/docs/swagger.json` + `/docs/index.html`, accessed 2026-05-31, Tier 1, Confidence: High unless flagged.

#### Read (fetch definition)
```
GET /api/v0.1/symphonies/{symphony-id}/score
```
Returns the full decision-tree logic. **This is already called by this codebase in `symphony_logic.py:43`.** Confirmed empirically on 2026-05-14 against a real symphony ("Planet of Hunted Cascades", 1,044,312-byte response). See `docs/research/claude-integration/composer-symphony-logic-endpoint.md` for the live-validated schema.

Query param: `score_version` ∈ {`v1`, `v2`}, default `v1`.

**Schema confirmed by empirical validation (2026-05-14):**

Top-level keys: `description`, `name`, `id`, `step` (`"root"`), `rebalance`, `asset_class`, `asset_classes`, `children`.

Node vocabulary (`step` values): `root`, `group`, `if`, `if-child`, `asset`, `filter`, `wt-cash-equal`, `wt-cash-specified`, `wt-inverse-vol`.

Leaf node (`step: "asset"`) shape:
```json
{
  "step": "asset",
  "ticker": "UVXY",
  "name": "ProShares Ultra VIX Short-Term Futures ETF",
  "exchange": "BATS",
  "id": "45cb7da2-..."
}
```

Conditional node (`step: "if-child"`) shape:
```json
{
  "step": "if-child",
  "is-else-condition?": false,
  "lhs-fn": "relative-strength-index",
  "lhs-fn-params": { "window": 10 },
  "lhs-val": "UVXY",
  "comparator": "lt",
  "rhs-fixed-value?": true,
  "rhs-val": "85",
  "id": "d74bd787-...",
  "children": [ ... ]
}
```

Weight node: `"weight": { "num": 20, "den": 100 }` (rational fraction — 20/100 = 20%).

Known indicator functions (`lhs-fn` / `rhs-fn`): `relative-strength-index`, `current-price`, `moving-average-price`, `max-drawdown`, `cumulative-return`, `standard-deviation-return`, `moving-average-return`.

**Size warning:** Real symphony score trees can exceed 1 MB. The `symphony_logic.py` condensation pipeline reduces to < 8 KB for Claude context. For AI Advisor use, the full tree must be passed to `POST /api/v0.1/backtest`, but only the condensed summary should reach the LLM advisor prompt.

#### Additional read endpoints
```
GET /api/v0.1/symphonies/{symphony-id}/versions
GET /api/v0.1/symphonies/{symphony-id}/versions/{version-id}/score
```
Version history and logic tree at a historical version. Empirically confirmed in `composer-symphony-logic-endpoint.md`.

#### Create (save a new symphony)
```
POST /api/v0.1/symphonies
```
Request body: same `raw_value` tree structure as the score response. Confirmed by `api.composer.trade/docs/index.html` operation `create_symphony_api_v0_1_symphonies_post`, accessed 2026-05-31. [Medium — schema confirmed structurally; exact required vs optional sub-fields not exhaustively enumerated in this session.]

#### Update (save changes to existing)
```
PUT /api/v0.1/symphonies/{symphony-id}
```
Same body shape as POST. [Medium — confirmed documented; not tested empirically this session.]

#### Copy (duplicate without modifying)
```
POST /api/v0.1/symphonies/{symphony-id}/copy
```
`is_public` parameter **deprecated** (will be removed in a future version). Flagged in baseline 2026-05-12; still present on 2026-05-31.

#### Delete
```
DELETE /api/v0.1/symphonies/{symphony-id}
```
Documented; no request body.

#### Search public symphonies
```
POST /api/v0.1/search/symphonies
```
Query public strategy database. Relevant to advisor if proposing analogous strategies from the community.

**Implication for AI Advisor workflow (interpretation):**

The complete advisor loop is API-supported:
1. `GET /symphonies/{id}/score` → fetch live symphony tree
2. LLM mutates the tree (asset swap, threshold adjustment, logic tweak)
3. `POST /api/v0.1/backtest` with the mutated `raw_value` → get backtest stats
4. Overfitting-veto gate runs on the stats
5. If passes: `POST /api/v0.1/symphonies` (save as new) or `PUT /api/v0.1/symphonies/{id}` (overwrite)

This loop does NOT require creating a saved symphony before backtesting. Step 3 accepts the inline tree. Only step 5 (persisting the accepted recommendation) requires a write.

ToS risk note: Using the API to programmatically create/modify live symphonies is sanctioned by the API (the endpoints exist and are documented). However, fully automated mutation and deployment of live positions without human review is a compliance and operational risk that Planet Stopper's existing architecture already gates via the advisor surface (read-only dashboard, human approval for any deploy action).

---

### 4. Auth Flow and Rate Limits

This surface is documented in depth in `docs/research/composer/baseline__2026-05-12.md` (Section 2 and 4) and reverified in `reverification__2026-05-13.md` as UNCHANGED. Summary for completeness:

**Auth:**
- Two headers on every request: `x-api-key-id: <key-id>` and `authorization: Bearer <key-secret>`.
- Already implemented in `alpha_bot_execution.py:160-165` as `get_composer_headers()`.
- One active key pair per user. New key immediately revokes prior pair (no overlap window).
- No scope system; key inherits the creating user's full access.
*(Confidence: High — Tier 1, confirmed empirically by `composer-symphony-logic-endpoint.md` live validation 2026-05-14)*

**Rate limits:**
- Standard: **1 req/sec** (HTTP 429 on exceed).
- Exception: `POST /api/v0.1/symphonies/{symphony-id}/backtest` — **500 req/sec**.
- `POST /api/v0.1/backtest` (inline definition backtest) — not explicitly excepted; standard 1 req/sec applies. [Medium — absence-of-explicit-exception; not a confirmed 500/s limit.]
- `Retry-After` header on 429: not documented. AlphaBot already uses exponential backoff (`alpha_bot_execution.py:259-293`).

**Implication for AI Advisor (interpretation):** At 1 req/sec for inline backtests, batching N candidate variants serially will take N seconds. For an advisor that proposes 3-5 variants per symphony, a single advisor run takes 3-5 seconds for backtesting alone — acceptable for an async/background flow, but incompatible with a synchronous user-request path. The 500 req/sec limit on the symphony-ID endpoint is not useful for the advisor (which needs the inline path to avoid polluting the user's saved symphonies library).

---

### 5. Codebase Reconciliation

**Endpoints already called by Planet Stopper** (extracted from `alpha_bot_execution.py` and `symphony_logic.py`, 2026-05-31):

| Endpoint | File | Purpose |
|---|---|---|
| `GET /api/v0.1/portfolio/accounts/{id}/symphony-stats-meta` | `alpha_bot_execution.py:173` | Fetch live symphony stats + holdings |
| `POST /api/v0.1/deploy/accounts/{id}/symphonies/{id}/go-to-cash` | `alpha_bot_execution.py:258` | Execute stop-loss sell-to-cash |
| `GET /api/v0.1/symphonies/{id}/score` | `symphony_logic.py:43` | Fetch symphony decision tree for advisor context |

**Not yet called (new for AI Advisor):**

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /api/v0.1/backtest` | Inline backtest of proposed variant | Load-bearing new surface |
| `POST /api/v0.1/symphonies` | Save an accepted advisor recommendation | Only needed if auto-apply is in scope |
| `PUT /api/v0.1/symphonies/{id}` | Overwrite existing symphony | Only needed if auto-apply is in scope |

**Auth contract:** The new endpoints share the same auth headers already in `get_composer_headers()`. No new credential surface needed.

---

### Schema Snapshot: POST /api/v0.1/backtest (Verbatim)

*(From api.composer.trade/docs/swagger.json, accessed 2026-05-31, Tier 1)*

```
Request:
  symphony.raw_value.step:          "root"  (required, fixed literal)
  symphony.raw_value.id:            string (UUID)
  symphony.raw_value.name:          string
  symphony.raw_value.children:      array of node objects (the logic tree)
  capital:                          number (required)
  apply_reg_fee:                    boolean (required)
  apply_taf_fee:                    boolean (required)
  slippage_percent:                 number (required; 0.01 = 1%)
  broker:                           enum (required): ALPACA_OAUTH | ALPACA_WHITE_LABEL |
                                    APEX_LEGACY | alpaca | apex
  start_date / end_date:            string ISO 8601 (optional)
  backtest_version:                 "v1" | "v2" (optional, default v1)

Response:
  stats.cumulative_return           number
  stats.sharpe_ratio                number
  stats.sortino_ratio               number
  stats.max_drawdown                number
  stats.annualized_rate_of_return   number
  stats.win_rate                    number
  stats.calmar_ratio                number
  stats.<period>_return             number (1D, 1W, 1M, 3M, 6M, 1Y, 3Y, 5Y)
  holdings_breakdown                object
  rebalance_days                    array
  costs.*                           object (fees breakdown)
  data_warnings                     array (ticker-level insufficient-history flags)
  benchmark_comparisons             object (alpha, beta, correlation per benchmark)
```

---

## Analysis

My interpretation of what these findings imply for the AI Advisor design:

**The advisor loop is fully API-supported.** `POST /api/v0.1/backtest` with an inline `raw_value` tree is the load-bearing endpoint, it accepts the same structure returned by `GET /score` (already called by `symphony_logic.py`), and the response includes all metrics needed for the overfitting-veto gate (Sharpe, Sortino, max drawdown, returns series).

**The mutation problem is non-trivial, but not an API problem.** The API will accept any structurally valid tree. The hard work is in the LLM prompt: how do you describe an asset swap (change `ticker` in a leaf node), a threshold change (mutate `rhs-val` in an `if-child`), or a de-correlation rebalance (add/remove branches) in a way that the LLM can output a valid tree diff? The `symphony_logic.py` condensation pipeline handles the read side; the write side (LLM outputs a structured mutation, not a free-text description) is the engineering question the API research cannot answer.

**Scale:** At 1 req/sec for inline backtests, the advisor is a background-async process, not a synchronous one. This is consistent with the existing architecture (auto-tuner runs post-market, not intraday).

**Symphony size:** Score trees can exceed 1 MB. The full tree must be in the backtest request body, but the LLM should only receive the condensed summary (< 8 KB, already handled by `condense_symphony_logic`). The advisor workflow should not ask the LLM to output a modified full tree — it should ask for a structured diff (e.g., "replace ticker X with Y in node UUID Z") and apply the diff programmatically.

**Save vs no-save:** The advisor can backtest variants without creating saved symphonies (using `POST /api/v0.1/backtest` directly). Only if the user approves a recommendation would a `POST /api/v0.1/symphonies` or `PUT` be warranted. This keeps the user's symphony library clean during exploration.

---

## Recommendations

These are options with trade-offs; the PM and user decide direction.

**Option A — Pure advisory (no write-back):** Advisor fetches score, mutates, backtests via `POST /api/v0.1/backtest`, surfaces metrics to the user. No save. Low risk, no ToS ambiguity on auto-modification of live strategies. Limitation: user must manually apply any accepted recommendation in the Composer UI.

**Option B — Advisory + save-as-new:** On user approval, advisor calls `POST /api/v0.1/symphonies` to create a new symphony (not overwrite the live one). User deploys capital via the existing `invest` endpoint or via the UI. Lower risk than overwrite; lets the user compare old vs new live. Limitation: Composer account accumulates saved (but undeployed) symphonies.

**Option C — Advisory + overwrite:** On user approval, advisor calls `PUT /api/v0.1/symphonies/{id}` to update the live symphony. The live strategy changes at next rebalance. Highest risk: a bad suggestion + missed veto gate modifies a live position. Should never be fully automated; requires explicit user confirmation gate.

**On mutation representation:** Recommend asking the LLM for a structured JSON diff (node-UUID → field-change map) rather than a full regenerated tree. Apply the diff programmatically. This is both safer (reduces surface area for LLM hallucination) and more reviewable by the user.

---

## Open Questions

1. **`POST /api/v0.1/backtest` rate limit confirmation:** The 500 req/sec exception is documented only for the `/{symphony-id}/backtest` path. The generic `/backtest` path likely inherits 1 req/sec, but this is an inference — an integrations agent should confirm empirically. [Medium]

2. **Earliest backtest date range:** The API does not document the earliest `start_date` it supports. The `data_warnings` field handles per-ticker gaps. Needs empirical testing with a wide date range. [Medium]

3. **`backtest_version` v1 vs v2 behavioral diff:** Both are documented; no diff is described. Unknown whether v2 uses different return calculations or cost models. [Medium]

4. **Node UUID generation for mutated trees:** When constructing a mutated tree, should modified nodes get new UUIDs or retain originals? The API may reject duplicate UUIDs or require unique IDs. [Low — needs empirical test]

5. **ToS on programmatic symphony modification:** The API supports it mechanically. Whether automated modification of live strategies violates Composer's Terms of Service (particularly for funded accounts) should be verified against the current ToS before Options B/C are built. [Low — flagged, not blocking for advisory-only Option A]

6. **`What's New` changelog inaccessibility:** `www.composer.trade/whats-new` has returned HTTP 403 to anonymous fetchers since 2026-05-12. No resolution. This blocks changelog monitoring for silent endpoint drift. [Medium — ongoing]

---

## Sources

| # | URL | Access Date | Tier | Method | Description |
|---|---|---|---|---|---|
| 1 | https://api.composer.trade/docs/index.html | 2026-05-31 | 1 | Documented (Redocly OpenAPI render) | Primary API reference — endpoint inventory, schemas, rate limits |
| 2 | https://api.composer.trade/docs/swagger.json | 2026-05-31 | 1 | Documented (machine-readable spec) | Underlying OpenAPI spec — backtest request/response schemas |
| 3 | https://help.composer.trade/article/236-getting-started-with-your-composer-api | 2026-05-31 | 1 | Documented | Getting-started guide, last updated 2025-07-16 |
| 4 | https://help.composer.trade/article/235-getting-your-api-key | 2026-05-31 | 1 | Documented | API key guide, last updated 2025-12-31 |
| 5 | https://help.composer.trade/article/67-backtest-basics | 2026-05-31 | 1 | Documented | Backtest mechanics: daily adj. close, fees, slippage |
| 6 | https://playbooks.com/mcp/invest-composer/composer-trade-mcp | 2026-05-31 | 2 | Community aggregator | MCP server tool inventory (28 tools including backtest_symphony + backtest_symphony_by_id) |
| 7 | https://github.com/androslee/compose_symphony_parser | 2026-05-31 | 3 | Community | Symphony EDN/JSON format confirmation; step/ticker/children structure |
| 8 | C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\composer\baseline__2026-05-12.md | 2026-05-31 | 1 (internal) | Documented (empirically validated) | Project baseline: auth, rate limits, endpoint inventory |
| 9 | C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\composer\reverification__2026-05-13.md | 2026-05-31 | 1 (internal) | Documented | 24-hour drift check — all unchanged |
| 10 | C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\claude-integration\composer-symphony-logic-endpoint.md | 2026-05-31 | 1 (internal) | Observed network (live validation 2026-05-14) | `/score` endpoint empirical validation — actual response shape, 1MB+ tree, node vocabulary |
| 11 | C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py:153-165, 173, 258 | 2026-05-31 | 1 (internal) | Codebase | Live base URL, auth headers, and endpoints already called |
| 12 | C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\symphony_logic.py:43 | 2026-05-31 | 1 (internal) | Codebase | Live call to GET /symphonies/{id}/score; EDN tree walk |
| 13 | C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\symphony_logic\test_live_composer_score.py | 2026-05-31 | 1 (internal) | Codebase | Confirmed `step` vocabulary: root, group, if, if-child, asset, filter, wt-cash-equal |
