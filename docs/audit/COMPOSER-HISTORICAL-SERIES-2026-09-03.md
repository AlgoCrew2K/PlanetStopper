# Research Report: Composer.trade Historical Daily Series Endpoints

**Researcher:** composer-api-researcher
**Date:** 2026-09-03
**Access date for all web citations:** 2026-09-03
**Confidence Summary:** [High] — Composer publishes an official, complete API reference that documents **three date-indexed time-series endpoints**, two of which are account-scoped (i.e. real realized live performance, not backtest). The *existence* and *response field names* are well-corroborated. The two facts that actually gate the decision — **series granularity** and **how far back history goes** — are **entirely undocumented** and must be settled empirically by `perf-data`.

> **Headline answer:** The premise that Composer exposes no dated series is **FALSE**. `symphony-stats-meta` is scalar-only (correctly characterised in the brief), but it is not the only portfolio endpoint. `GET /portfolio/accounts/{account-id}/symphonies/{symphony-id}` returns a **per-symphony value-over-time series**, and `GET /portfolio/accounts/{account-id}/portfolio-history` returns an **account-level equity curve plus a cumulative TWR series**. Both are account-scoped realized data. Whether they reach back to 2024-07-12 is **unverified** and is the single blocking unknown.

---

## Research Questions

1. Enumerate every Composer endpoint returning time-series (not scalar) data, with response shape, date range and granularity.
2. For each: authentication, public/undocumented/reverse-engineered status, drift risk.
3. Does `/backtest` return a dated series usable as *live/invested* history, or is it strictly hypothetical? Can the two be distinguished?
4. Is there an account-level (not just symphony-level) historical endpoint?

**Out of scope (deliberately):** any recommendation on implementation path. This is a reference report; the PM decides scope.

---

## Findings

### Q1 + Q4 — Time-series endpoints that exist

Composer maintains a **complete official API reference** at `https://api.composer.trade/docs/index.html` (Tier 1, observation method: documented, accessed 2026-09-03). Contrary to the "poorly documented" prior, the reference is thorough — it enumerates ~35 endpoints across Accounts / Portfolio / Reports / Market Data / Symphony / Backtest / Deploy / Dry Run / Direct Trading / Search, with parameters and response schemas.

Exactly **three** endpoints return date-indexed series. All three are under `/api/v0.1/portfolio/accounts/{account-id}/…` — i.e. **all are account-scoped**, which is what makes them realized rather than hypothetical.

#### S-1. Account-level portfolio history — **answers Q4**

```
GET /api/v0.1/portfolio/accounts/{account-id}/portfolio-history
```

- **Documented description (verbatim):** "Gets the value of the account portfolio over time"
- **Parameters:** `account-id` (path, UUID). **No query parameters documented — no date range, no window, no pagination.**
- **Response shape (verbatim from docs schema):**
  ```json
  {
    "epoch_ms": [0],
    "series": [0.1],
    "cumulative_twr_series": [0.1]
  }
  ```
  Three parallel arrays: timestamps, portfolio value at each timestamp, and cumulative **time-weighted return** at each timestamp.
- **Confidence:** `[High]` — present in two independent reads of the primary doc, plus an independent search-result snippet reciting the same three field names verbatim, plus corroboration by Composer's own MCP server tool `get_portfolio_daily_performance` (see below).

> **Why `cumulative_twr_series` matters and deserves care:** TWR is cash-flow-neutral by construction. This project already documents (`app.py:841-845`) that Composer's *displayed* "Total return" uses `simple_return`, and that **TWR diverges from it by ~5 pp when net deposits are non-zero** — and `DE-AUDIT-BL5-12-001`/BL-12 added an on-dashboard disclosure of exactly that basis difference. So `portfolio-history` hands you a *different basis* from the `total-stats` scalar the dashboard currently shows. That is arguably the **more** correct basis for windowed performance comparison (it is not distorted by the operator's deposits), but it is **not** the number the Composer UI headline shows. Mixing the two without labelling would reintroduce the exact defect class BL-12 was opened to close. `[High]` on the divergence existing — it is documented in this repo's own source comment; `[Unverified]` on its magnitude for these specific accounts.

#### S-2. Per-symphony position value over time — **the direct answer to the windowing problem**

```
GET /api/v0.1/portfolio/accounts/{account-id}/symphonies/{symphony-id}
```

- **Documented description (verbatim):** "Gets the value of a position over time"
- **Parameters:** `account-id` (path, UUID), `symphony-id` (path, string). **No query parameters documented — no date range.**
- **Response shape (verbatim from docs schema):**
  ```json
  {
    "epoch_ms": [0],
    "series": [0.1],
    "deposit_adjusted_series": [0.1]
  }
  ```
- **Rate limit:** the docs' rate-limit table singles this endpoint out at **500 requests per minute** — by a wide margin the most generous limit in the table. `[High]`
- **Confidence:** `[High]` on existence and field names (primary docs, two independent reads; corroborated by the MCP tool `get_symphony_daily_performance`).

> **`deposit_adjusted_series` is undocumented beyond its name.** The docs give no definition. My interpretation — and it is *only* an interpretation — is that `series` is the raw position market value (which jumps discontinuously when the operator invests or withdraws) and `deposit_adjusted_series` normalises those capital flows out, making it the return-bearing series. **This is inference, not evidence — `[Unverified]`.** It matters a great deal: using the wrong one would attribute a deposit to performance. `perf-data` should discriminate empirically by cross-checking a known invest/withdraw date against both arrays.

#### S-3. Per-day, per-holding quantities and values

```
GET /api/v0.1/portfolio/accounts/{account-id}/symphony-historical-holdings
```

- **Documented description (verbatim):** "Returns the daily quantity and value of each holding of each symphony for the account, grouped first by symphony id and then by day, over the given date range."
- **Parameters:**
  - `account-id` (path, UUID, required)
  - `start_date` (query) — "The inclusive start date"; **defaults to 30 days ago**
  - `end_date` (query) — "The inclusive end date"; **defaults to 30 days after start date**
  - `include_aggregate_account_holdings` (query, **required**, boolean)
- **Response shape:** `{ "by_symphony": { <symphony_id>: { <date>: [holdings] } }, "total_symphony_holdings": { <date>: [holdings] } }`
- **This is the only one of the three with explicit date-range parameters** — and therefore the only one where a long lookback can be *requested* rather than hoped for. No maximum window is stated.
- **Confidence:** `[Medium]` — documented in the primary source (and the description was independently recited in two separate search snippets), but **no MCP tool wraps it** and I found no community usage report. Docs-only, unexercised. Note the default `end_date` semantics are unusual ("30 days after start", not "today"), so omitting it is a trap.

#### Endpoints that are explicitly NOT time-series

For completeness, and to confirm the brief's own finding: `total-stats` and `symphony-stats-meta` (both already used by this codebase, `app.py:826` and `alpha_bot_execution.py:192`) are scalar-only. The docs describe `symphony-stats-meta` as "Returns aggregate statistics for each symphony in account" and `total-stats` as returning `portfolio_value`, `simple_return`, `time_weighted_return`, `net_deposits`, `todays_dollar_change`, `todays_percent_change`, `total_cash`, `metrics`. **No dated arrays.** `[High]` — the brief's full-response inspection and the published schema agree.

#### Independent corroboration — Composer's own MCP server

Composer publishes an official open-source MCP server (`invest-composer/composer-trade-mcp`, PyPI `composer-trade-mcp`). Its tool list is a **separate artifact from the API reference** and therefore genuine triangulation rather than a restatement. Two of its 29 tools map onto S-1 and S-2:

| MCP tool | Verbatim description | Maps to |
|---|---|---|
| `get_symphony_daily_performance` | "Get daily performance for a specific symphony in a brokerage account" | S-2 |
| `get_portfolio_daily_performance` | "Get the daily performance for a brokerage account" | S-1 |

`[High]` — this is Composer's own first-party client, and the word **"daily"** in both descriptions is the strongest available evidence on granularity. It is still second-hand relative to an actual response: the tool description says daily, the API schema says `epoch_ms`. I did not obtain the server source (GitHub raw/blob fetches returned 404 to this tool), so the URL→tool mapping is inferred from name and description, not read from code. **Granularity remains `[Medium]`, not `[High]`.**

Note also: **no MCP tool wraps `symphony-historical-holdings`** — consistent with S-3 being the least-exercised of the three.

---

### Q2 — Authentication, status, and drift risk

**Authentication (all three, and the whole `/api/v0.1` surface):**
```
x-api-key-id: <key-id>
authorization: Bearer <key-secret>
```
`[High]` — stated identically in the API reference and in the help-center article "Getting Started with Your Composer API" (last updated **2025-07-16**). **This is byte-identical to the header pair this codebase already sends** via `alpha_bot_execution.get_composer_headers()` (`alpha_bot_execution.py:179-184`). No new credential, no new auth flow, no OAuth — the existing `COMPOSER_KEY_ID`/`COMPOSER_SECRET` pair is sufficient. This is a meaningful de-risking finding.

**Status — all three are (a) documented and official**, not reverse-engineered. They sit in the same published reference and on the same versioned base path as `/backtest`, `/score` and `go-to-cash`, all four of which this codebase already calls in production. **There is no ToS-risk delta versus current usage** — this is the same public API surface, not a scraped internal endpoint.

**Drift risk — moderate, and I want to be honest about the tension here.** My standing operating rule assumes Composer drifts silently, and the project's CLAUDE.md gotcha table says "Assume drift." The evidence this round *partially* softens that:

- ✅ A complete, structured, current public reference exists.
- ✅ A first-party open-source client exists and is maintained.
- ❌ **No changelog, no release notes, no versioning policy, no deprecation policy, and no "last updated" date** anywhere in the reference. `[High]` — I checked for these explicitly; they are absent.
- ❌ The base path is `v0.1` — a pre-1.0 version string, on a live money-moving API, with no stability guarantee stated.
- ⚠️ One deprecation *is* visible in-band (`is_public` on the copy endpoint is marked "DEPRECATED - This field is deprecated and will be removed in a future version"), which tells us fields do get retired, and that the only notice is inline.

**Verdict:** documented ≠ contractual. Treat these as stable-but-unguaranteed, and pin fixtures. `[Medium]` confidence on 12-month stability.

**Two live conflicts worth recording:**

1. **Rate limits conflict between two Tier-1 Composer sources.**
   - API reference (accessed 2026-09-03): standard "25 requests per minute"; `GET …/symphonies/{symphony-id}` = **500/min**; various symphony endpoints 250/min; `dry-run/trade-preview` 100/min. Exceeding → HTTP 429.
   - Help center "Getting Started" (last updated 2025-07-16): "Most endpoints have a rate limit of **1 req/sec**. The one exception is `/api/v0.1/symphonies/{symphony-id}/backtest`, which has a rate limit of **500 req/sec**."

   These are not reconcilable as written (1/sec = 60/min ≠ 25/min; and 500/**sec** for backtest is implausible on its face and contradicts this repo's own working assumption of 1 req/s for `/backtest`, `composer_backtest_client.py:30`). The help-center figure is ~14 months old. **My read: prefer the API reference's per-endpoint table as the more recent and more specific source, but treat the true limit as `[Unverified]` and let 429-handling — which `composer_backtest_client.py` already implements correctly with `Retry-After` — be the real authority.** Flagging rather than resolving.

2. **A `v2` path exists in this repo that is not in the documented surface.**
   `tests/analytics/test_live_m1_helpers.py:66` hits
   `https://api.composer.trade/api/**v2**/portfolio/accounts/{account_id}/symphony-stats-meta`
   with **bearer-only** auth (`test_live_m1_helpers.py:52` sends `Authorization: Bearer …` and **no** `x-api-key-id`), whereas production code (`alpha_bot_execution.py:192`) uses `v0.1` with the documented header pair. The API reference contains **no v2 path** (only `v0.1` and a `v1` used for market-data/options). `[Medium]` interpretation: this test was written against the web app's own session-authenticated internal API, not the public one — a different, genuinely reverse-engineered surface. It is a live test (excluded by default), so it is not exercised routinely. **Worth a separate ticket; out of scope for this report.**

---

### Q3 — Is `/backtest` usable as live/invested history? **No. Unambiguously no.**

This is the question the brief flagged as a correctness landmine, so I'll be maximally explicit.

**Both backtest endpoints are strictly hypothetical simulation:**

| | Path | Documented description |
|---|---|---|
| Inline (what this repo uses) | `POST /api/v0.1/backtest` | "Simulate performance for custom symphony definition" |
| By id | `POST /api/v0.1/symphonies/{symphony-id}/backtest` | "Simulate symphony performance using historical data" |

**Can they be distinguished from realized performance? Yes — structurally, not just semantically.** Four independent discriminators, in descending order of strength:

1. **Neither backtest endpoint accepts an `account-id`, in the path or the body.** The three series endpoints (S-1/S-2/S-3) are *all* rooted at `/portfolio/accounts/{account-id}/…`. A backtest request **cannot** identify the operator's account, so it structurally *cannot* return his realized results. This alone is dispositive. `[High]`
2. **Starting capital is a caller-supplied parameter.** `capital` is a required body field; this repo hardcodes `_DEFAULT_CAPITAL = 10_000.0` (`composer_backtest_client.py:70`). The returned `dvm_capital` series is denominated in that fictional $10k, not the operator's real position size. `[High]`
3. **Costs are modelled, not actual.** `slippage_percent` (repo default `0.005`), `apply_reg_fee`, `apply_taf_fee`, `apply_cat_fee`, `spread_markup` are all *inputs*. Realized fills, partial fills, and real slippage are not represented. `[High]`
4. **Version/counterfactual mismatch.** A backtest evaluates the tree you post *today* across all history. Composer's own `GET /symphonies/{id}/versions` endpoint exists precisely because symphonies are edited over time — so replaying today's tree over 2024–2026 is counterfactual even for the same symphony id, and diverges further the more the operator has edited it. `[High]`

**Additional trap worth naming:** `POST /backtest` *does* accept `start_date` / `end_date` body params (documented), so it will happily produce a dated series covering 2024-07-12 → today. It will look exactly like the thing we want. **It is not.** Any implementation must never allow a backtest series and a `portfolio-history`/position series to flow into the same field without an explicit provenance tag — this repo already has the right instinct here (`if_held_source` provenance stamps enforced at read time by `analytics.is_valid_post_mortem_entry`, `DE-POSTMORTEM-INTEGRITY-001`), and the same discipline applies.

**One legitimate, non-conflating use** (stated for completeness, not as a recommendation): a backtest series is a valid *counterfactual benchmark* to display **alongside** realized history — which is conceptually what this project already calls "if-held". That is a different claim from "the operator's live performance" and must be labelled as such.

---

## Analysis

*(Explicitly labelled interpretation — may be wrong.)*

The brief's framing was "we can surface a lifetime number but cannot window it." My reading of the evidence is that **this constraint is an artifact of which endpoint the codebase happens to call, not a property of the Composer API.** The codebase currently uses only the two scalar portfolio endpoints (`total-stats`, `symphony-stats-meta`); the three series endpoints sit on the same base path, behind the same credentials, and were apparently never wired up.

If S-2 returns a series from position inception, then pre-2026-06-22 windowed *live* performance becomes possible per symphony, and the operator's 2024-07-12 history is reachable. If S-1 returns account history from account inception, account-level windowing is possible too.

**But I want to be precise about what that would and would not buy, because there is an asymmetry the brief's framing may obscure:** these endpoints return **Composer's realized performance** — what the symphonies actually did *in the operator's account*. They do **not** contain a Planet-Stopper counterfactual. `shadow_history` (2026-06-22 onward) is what encodes "what would have happened without Guard Alpha." So a long window would let you honestly say *"here is your real live performance since 2024"* — but **not** *"here is how much Guard Alpha saved you since 2024,"* because the bot did not exist then and no if-held counterfactual was ever recorded. The guard-alpha delta remains structurally bounded to the shadow-history era regardless of what these endpoints return. Conflating "live history extends to 2024" with "guard-alpha comparison extends to 2024" would be a second, subtler correctness error, adjacent to the one the brief already correctly guarded against.

The operator's expectation that "live outreaches bot becomes achievable" is therefore only partly served: the **live** leg can extend back; the **bot** leg cannot.

---

## Recommendations

*(Options with trade-offs — not directives. PM decides.)*

**On what to probe first (all three are read-only GETs against the existing credential):**

- **Option A — probe S-2 (`/symphonies/{symphony-id}`) first.** Highest information per call: directly answers "does per-symphony history reach `invested_since`?" for the 2024-07-12 symphony, which is the single fact the whole decision turns on. Cheapest to falsify. Trade-off: answers only the symphony-level question.
- **Option B — probe S-1 (`portfolio-history`) first.** Answers the account-level question and hands over a TWR series. Trade-off: introduces the simple-return-vs-TWR basis question (see BL-12), which is a labelling problem the PM would then own.
- **Option C — probe S-3 (`symphony-historical-holdings`) with an explicit 2024 `start_date`.** The only endpoint where lookback is *requested* rather than discovered, so it is the cleanest test of true retention depth. Trade-off: least corroborated of the three, awkward `end_date` default, and returns holdings rather than a return series — more reconstruction work.
- **Option D — probe all three in one pass.** Three GETs, well inside any plausible rate limit. Trade-off: none material; slightly more fixture surface to capture.

**On basis handling, if S-1/S-2 prove usable:** either (i) adopt TWR and relabel, (ii) keep simple-return and use only S-2's `deposit_adjusted_series`, or (iii) surface both with explicit basis labels — the pattern BL-12 already established. Each trades honesty-of-disclosure against UI complexity.

**On the honest-negative branch:** if the probe shows history is truncated (e.g. only 30/90 days), the brief's original conclusion holds for the truncated span and should be stated plainly rather than implied away.

---

## Open Questions

**Blocking — only `perf-data` can settle these (no documentation exists):**

1. **How far back does S-2's series go?** Position inception, or a fixed retention window? *This is the decision.* Probe against the 2024-07-12 symphony.
2. **How far back does S-1's series go?** Account inception or a window?
3. **What is `epoch_ms` granularity?** Daily bars, or intraday? MCP tool descriptions say "daily" (`[Medium]`), the schema says milliseconds. Check consecutive-timestamp deltas.
4. **What exactly is `deposit_adjusted_series`?** My deposit-normalisation reading is inference only. Validate against a known invest/withdraw date.
5. **What is `cumulative_twr_series`' scale and epoch?** Fraction vs percent; cumulative from account inception or from series start.
6. **Does S-3 actually honour a `start_date` in 2024,** or silently clamp?

**Non-blocking:**

7. True rate limits — the two Composer sources conflict (see Q2). Empirical 429 behaviour is the real answer.
8. The undocumented `/api/v2/…` path in `test_live_m1_helpers.py:66` with mismatched auth — likely a legacy internal-API artifact; deserves its own ticket.
9. Do these endpoints paginate on long histories? Nothing documented; a 2024-inception daily series is only ~550 points, so probably moot.
10. Behaviour for a symphony that was liquidated and re-invested (multiple epochs) — relevant given this project's epoch-additive guard-alpha semantics.

**Assumptions I made:** that the MCP tools `get_symphony_daily_performance` / `get_portfolio_daily_performance` wrap S-2 / S-1 respectively — inferred from name and description, **not** read from source (GitHub fetches 404'd for this tool). If that mapping is wrong, the "daily" granularity evidence weakens to inference from field naming alone.

---

## Sources

| # | Source | URL | Accessed | Tier | Method | Notes |
|---|---|---|---|---|---|---|
| 1 | Composer official API reference | `https://api.composer.trade/docs/index.html` | 2026-09-03 | 1 | documented | Primary source. Complete endpoint reference, ~35 endpoints, schemas, rate-limit table. Read 3× with different prompts; consistent each time. **No changelog / no last-updated date / no versioning policy.** |
| 2 | Composer official MCP server (tool list) | `https://glama.ai/mcp/servers/invest-composer/composer-trade-mcp` (mirror of `github.com/invest-composer/composer-trade-mcp`) | 2026-09-03 | 1–2 | documented (mirror) | 29 tools verbatim. Independent artifact from #1 → genuine triangulation for S-1/S-2. Source code **not** retrieved (raw/blob 404). |
| 3 | Composer Knowledge Center — "Getting Started with Your Composer API" | `https://help.composer.trade/article/236-getting-started-with-your-composer-api` | 2026-09-03 | 1 | documented | Last updated **2025-07-16** → `[STALE]` on rate limits; auth header pair still corroborates #1. |
| 4 | Composer Knowledge Center — "Getting Your API Key" | `https://help.composer.trade/article/235-getting-your-api-key` | 2026-09-03 | 1 | documented | Last updated **2025-12-31**. API key requires an account; paid plan referenced for MCP linkage. |
| 5 | Search-result snippet reciting `portfolio-history` response fields | via web search, resolving to #1 | 2026-09-03 | 4 | secondary | Independent recitation of `epoch_ms` / `series` / `cumulative_twr_series`. Corroborative only — ultimately downstream of #1, **not** counted as independent triangulation. |
| 6 | This repo — `advisors/composer_backtest_client.py` | local (`C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/advisors/composer_backtest_client.py`) | 2026-09-03 | 1 | source read | Current `/backtest` usage; `_DEFAULT_CAPITAL=10_000` (:70), slippage `0.005` (:71), 1 req/s note (:30), `dvm_capital` parsing (:131-193). |
| 7 | This repo — `alpha_bot_execution.py` | local | 2026-09-03 | 1 | source read | `COMPOSER_BASE_URL` (:172), `get_composer_headers` (:179-184), `fetch_symphony_stats` → `symphony-stats-meta` (:191-205), `go-to-cash` (:279). |
| 8 | This repo — `app.py` | local | 2026-09-03 | 1 | source read | `total-stats` fetch (:826), simple-return-vs-TWR ~5 pp divergence comment (:841-845), portfolio MDD cache (:851-855). |
| 9 | This repo — `tests/analytics/test_live_m1_helpers.py` | local | 2026-09-03 | 1 | source read | Undocumented `/api/**v2**/…/symphony-stats-meta` (:66) with bearer-only auth (:52). |

**Not used as evidence:** `composer.trade` marketing/strategy pages and third-party review blogs surfaced repeatedly in search. Per operating rules, marketing pages are never capability evidence; none are cited above.

---

## Confidence Ledger (at a glance)

| Claim | Confidence |
|---|---|
| S-1 / S-2 / S-3 exist as documented endpoints | `[High]` |
| S-1 / S-2 response field names as quoted | `[High]` |
| All three are account-scoped → realized, not simulated | `[High]` |
| Auth = existing `x-api-key-id` + `Bearer` pair; no new credential | `[High]` |
| `/backtest` is hypothetical and structurally distinguishable | `[High]` |
| S-3 exists and honours `start_date`/`end_date` | `[Medium]` (docs-only, no MCP wrapper, no community usage found) |
| Granularity is daily | `[Medium]` (MCP tool descriptions say "daily"; schema says `epoch_ms`) |
| MCP tools map to S-1/S-2 | `[Medium]` (inferred from names; source not read) |
| Rate limits | `[Unverified]` — two Tier-1 Composer sources conflict |
| **History depth / lookback reach (incl. back to 2024-07-12)** | **`[Unverified]` — undocumented; the blocking unknown** |
| `deposit_adjusted_series` semantics | `[Unverified]` — name only |
| 12-month endpoint stability | `[Medium]` — documented but `v0.1`, no changelog, no deprecation policy |
