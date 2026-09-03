# Performance-Tab Window Truth — Audit Verdict

**Run date:** 2026-09-03
**Branch / base SHA:** `audit/perf-window-truth` @ `731cb778`
**Team:** `perf-trace` (code path) · `perf-data` (live droplet) · `perf-synth` (synthesis lead) · `composer-api-researcher` · `perf-doc`
**Type:** READ-ONLY diagnostic. No production code changed by this audit.

> **Revision note (same day, post-integration).** §§1–8 of this document were written from the synthesis lead's own first-hand code trace while teammate reports were still outstanding, and §9 originally listed four unresolved questions. Team evidence subsequently landed (`perf-data`'s droplet verification, `composer-api-researcher`'s endpoint report, `perf-doc`'s cross-surface sweep) and **three of the four are now resolved — one of them reversing a conclusion in this document's own §7.** The reversal is called out explicitly at §7(c) and §9 rather than silently edited in. Companion source: `docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md`; consolidated ruling: `DE-PERF-WINDOW-TRUTH-001` in `DECISIONS.md`.

---

## 0. The reported defect

Operator, verbatim:

> "I just checked my drawdown in performance and swapped to ytd from 60d and nothing changed. even if the bot numbers don't go back that far I would've expected my live numbers to. the displays need to be fucking accurate and informational of restrictions to data length."

Two distinct claims are embedded here, and they have different answers:

1. **"Nothing changed when I switched 60d → YTD."** — Reproduced and explained. See §3.
2. **"My live numbers should go back further than the bot numbers."** — **The operator's expectation is reasonable but is not satisfiable from the current data plumbing.** See §5. This is the finding most likely to be mis-summarized, so it is stated at length.

---

## 1. Executive verdict

**The Performance tab is not miscomputing max drawdown. It is telling the operator something untrue about how much history backs the number.**

Three things are true simultaneously; do not stop at the first.

- **(A) Both series are truncated to the same short window — by construction, not by accident.** The "live"/if-held line is *not* an independent longer record of the real account. It is the `current_return` **column of the very same `shadow_history` rows** that produce the bot line, read in a single SQL statement. Bot and Held are therefore *always* exactly the same length. There is no scenario in the current design where live history outlives bot history.
- **(B) When the requested window exceeds the available history, every oversized window silently collapses to "all the data there is."** 60d, 90d, 125d, YTD, 1Y and 5Y all degrade to the identical full series once the table is shorter than 60 trading days. Identical inputs → identical MaxDD. The operator saw exactly this.
- **(C) The UI actively misstates the window while this happens.** The caption renders `"<N> observations · 1260d window"` for a 5Y click backed by far fewer days, and the "insufficient history" banner is *suppressed* in precisely this regime because its threshold is 30 observations, not "does the window have the coverage it claims."

**Classification (this route):** a **display-honesty defect (severity HIGH)** stacked on a **data-depth limitation (severity MEDIUM, ops)**. **No max-drawdown computation bug exists in `/api/performance`** — the metric is computed correctly over whatever series it is handed (§4, E-4). On this route the lie is in the label, not in the arithmetic.

> **(D) But a genuine computation defect DOES exist — on the MAIN DASHBOARD, not this tab — and it outranks everything above.** Found by `perf-doc`/`perf-trace` while this document was being drafted. The dashboard's Bot-vs-Held **Max DD** row pairs Composer's **lifetime** `if_held` scalar (since `invested_since`, e.g. 2024-07-12) against a `dry_run` figure that can only span `shadow_history` (from 2026-06-22) — a lifetime-vs-53-day comparison, rendered at 5+ live sites with **zero disclosure**. It is ranked the **#1 finding of the whole audit** and requires a **computation re-base**, not a disclosure fix. This document's §7 option (c) originally read "NOT APPLICABLE"; that was correct **for this tab only** and is corrected in place at §7(c). Full detail: `DE-PERF-WINDOW-TRUTH-001` §"Verdict 2" in `DECISIONS.md`.

**Verdict on the operator's core complaint — "the displays need to be accurate and informational of restrictions to data length": he is right on both surfaces, and the codebase already has the correct pattern implemented elsewhere and simply did not apply it here** (`GET /api/exit-turnover`'s `coverage_days`, §6).

---

## 2. Per-dimension verdict table

| # | Dimension | Verdict | Confidence | Basis |
|---|-----------|---------|-----------|-------|
| D1 | Does the window token reach the MaxDD computation? | **YES — correctly windowed** | HIGH | `app.py:4890-4891` over the sliced list; `analytics.py:348-427` is pure over its argument |
| D2 | Is MaxDD computed on an unwindowed series (computation bug)? | **NO — no such bug found** | HIGH | Same as D1; no second/unwindowed MaxDD source feeds the tab (`static/performance.js:314-317`) |
| D3 | Is "held/live" sourced from a longer record than "bot"? | **NO — same rows, same query** | HIGH | `analytics.py:1627-1634`, unpacked at `app.py:4827` |
| D4 | Do oversized windows silently collapse to the full series? | **YES** | HIGH | `analytics.py:1654` / `1759-1761` (tail slice); `app.py:3332` (calendar slice) |
| D5 | Does the UI disclose actual coverage? | **NO — and it misstates it** | HIGH | `static/performance.js:450-452`; `app.py:44` + `4884` |
| D6 | Is there an established in-repo honesty pattern that was not applied? | **YES** | HIGH | `database.py:3624-3676` `coverage_days`; rationale `app.py:4329-4332` |
| D7 | Why is the underlying history short — retention prune or genesis? | **GENESIS (go-live floor 2026-06-22), NOT pruning** | HIGH | `perf-data` droplet verification: 53 trading days / 73 calendar days as of 2026-09-03; 180-day prune cutoff (~2026-03-07) sits *before* the floor, so retention has deleted nothing (`app.py:794`, `database.py:3320-3334`) |
| D8 | Does a longer live series exist anywhere to source from? | **Not in this codebase; but Composer DOES document three account-scoped dated series endpoints.** Reach/depth still unverified | MED | `composer-api-researcher` report §Q1/Q4; see E-19 |
| D9 | Is there a computation defect anywhere in this audit's scope? | **YES — on the main dashboard, not this tab. Ranked #1** | HIGH | `analytics.py:1008-1060` (lifetime Composer `if_held`) vs `analytics.py:1049` (`shadow_history`-scoped `dry_run`); rendered `app.py:1558`, `2011-2016`, `2555`, `2741`, `3108` |

---

## 3. The mechanism, step by step

The route accepts the window two different ways, and **both** degrade silently:

**Numeric buttons (30 / 60 / 90 / 125 / 252 / 1260)** — a *trading-day tail slice*:

```python
# analytics.py:1652-1654
all_days = sorted(day_map.keys())
sorted_days = all_days if days is None else all_days[-days:]
```

`all_days[-60:]` on the live 53-element list returns **all 53 elements**. Python slicing does not error, warn, or signal short coverage. `all_days[-1260:]` returns the same 53. Six of the seven buttons therefore produce byte-identical input.

**The YTD button** — a *calendar cutoff* on the full series:

```python
# app.py:4810
_fetch_days = None if is_ytd else days
# app.py:3332
idx = [i for i, d in enumerate(dates) if cutoff_iso is None or str(d) >= cutoff_iso]
```

`analytics._window_cutoff_date("ytd")` correctly resolves to `date(today.year, 1, 1)` (`analytics.py:1859-1860`) — that part is **not** buggy. But if every retained row already post-dates 1 Jan, the filter removes nothing and YTD also returns all 53. (Live data confirms this precondition: history begins 2026-06-22, so **every** retained row post-dates 1 Jan 2026 — E-20.)

**Result:** 60d and YTD hand `compute_quantstats_metrics` the identical list, so MaxDD is identical. Correct arithmetic on an unannounced substitution of the window.

---

## 4. Evidence table

Every row is a direct source read at `731cb778`. Rows marked *interpretation* are labelled as such and are **not** promoted to fact.

| ID | Claim | Evidence | Type |
|----|-------|----------|------|
| E-1 | Held and Bot come from ONE query over the SAME rows | `analytics.py:1627-1634` — `SELECT trading_day, symphony_id, shadow_return, current_return FROM shadow_history …` (single statement, both columns) | FACT |
| E-2 | The route unpacks both series from that one call | `app.py:4820`, `app.py:4827` — `dates, shadow_returns, live_returns = _series` | FACT |
| E-3 | Per-symphony scope has the same property | `analytics.py:1734-1743` (`SELECT trading_day, shadow_return, current_return … WHERE symphony_id = ?`) | FACT |
| E-4 | MaxDD is computed over the **sliced** series (no unwindowed source) | `app.py:4890-4891`; `analytics.py:348-427` takes `returns_series` and never re-reads the DB | FACT |
| E-5 | UI MaxDD reads only those payload metrics | `static/performance.js:314-317`, `:55-56` | FACT |
| E-6 | Numeric windows collapse silently when short | `analytics.py:1654`; per-symphony `analytics.py:1759-1761` | FACT |
| E-7 | YTD token resolves correctly to Jan 1 (not itself a bug) | `analytics.py:1859-1860` | FACT |
| E-8 | The caption **drops** the window label on YTD | `static/performance.js:450-452` — `if (typeof win === 'number')`, but `window_days` is the **string** `"ytd"` (`app.py:4786`, emitted at `app.py:4907`) | FACT |
| E-9 | The caption **asserts** an unbacked window on numeric clicks | same lines — renders `' · ' + win + 'd window'` with no coverage check | FACT |
| E-10 | The "insufficient history" banner is suppressed in this regime | `app.py:44` `_PERFORMANCE_MIN_HISTORY_DAYS = 30`; `app.py:4884` `observation_count < 30`; `static/performance.js:388` hides the banner when false | FACT |
| E-11 | That banner is a *stability* warning, not a *coverage* disclosure | `templates/performance.html:430-431` — "At least 30 trading days … needed for stable quantstats metrics" | FACT |
| E-12 | The response body has no coverage field at all | `app.py:4898-4917` — keys: scope, dates, live_returns, shadow_returns, live_metrics, shadow_metrics, observation_count, insufficient_history, window_days | FACT |
| E-13 | The in-repo honesty precedent exists and is tested | `database.py:3624-3676` (`coverage_days = min(window, actual_days)`); rationale `app.py:4329-4332`; tests `tests/database/test_exit_turnover_stats.py` | FACT |
| E-14 | `window_days` has **zero** test assertions; `obs-caption` is only tested for element presence | grep of `tests/` — `window_days` hits are unrelated (inverse-vol DSL); `tests/dashboard/test_risk_adjusted_display.py:200` asserts the testid exists only | FACT |
| E-15 | Retention default is 180 days | `app.py:794` `SHADOW_HISTORY_RETENTION_DAYS`, default `"180"`; prune at `database.py:3320-3334` | FACT |
| E-16 | No Composer historical-series endpoint is consumed anywhere | only `symphony-stats-meta` is called (`alpha_bot_execution.py:192`, `app.py:4951`); repo consumes scalar snapshot fields only (`analytics.py:507-510` data-source contract; `tests/analytics/test_live_m1_helpers.py:85-99` asserts scalars) | FACT |
| E-17 | With ~53 trading days available, six of seven buttons return identical data | Deduction from E-6 + E-7, on the now-verified 53-day history (E-20) | *interpretation* (deductive; the mechanism is proven, the button-by-button numeric sweep was not separately measured — see §9 U-3) |
| E-18 | Retention is **not** the cause — the prune cutoff predates the data | Verified: 180-day default (`app.py:794`) ⇒ cutoff ~2026-03-07, *before* the 2026-06-22 floor, so `database.py:3320-3334` has deleted nothing | FACT (was *interpretation* in the pre-integration draft; upgraded by `perf-data`) |
| E-19 | Composer documents **three** account-scoped dated series endpoints, none called by this codebase; same credential pair, no new auth | `composer-api-researcher` report §Q1/Q4: `/portfolio-history` (`epoch_ms`/`series`/`cumulative_twr_series`), `/symphonies/{id}` (`epoch_ms`/`series`/`deposit_adjusted_series`), `/symphony-historical-holdings` (explicit `start_date`) | FACT (existence + field names, `[High]`) — **depth/reach remains `[Unverified]`** |
| E-20 | `shadow_history` begins **2026-06-22** (Guard-Alpha go-live); 53 trading days / 73 calendar days as of 2026-09-03 | `perf-data` live droplet verification | FACT |
| E-21 | Dashboard Max DD pairs a **lifetime** Composer scalar against a **53-day** shadow figure, undisclosed | `analytics.get_symphony_max_drawdown` `analytics.py:1008-1060`; `if_held` from Composer's `max_drawdown` ("since `invested_since`, full holding history") read at `alpha_bot_execution.py:275`; `dry_run` from `_get_shadow_divergence_trajectory` `analytics.py:1049`; generic labels `templates/index.html:988-990`, `1251-1254`, `1336-1339`, `static/index.js:1043-1057` | FACT (`perf-doc`) |
| E-22 | A longer live series would **not** extend the guard-alpha comparison | Composer endpoints return *realized* performance only; the Planet-Stopper counterfactual exists nowhere but `shadow_history`, which cannot predate 2026-06-22 | FACT (structural) |

---

## 5. The operator's exact expectation — answered directly

> "even if the bot numbers don't go back that far I would've expected my live numbers to"

**He is reasoning correctly about the world and incorrectly about this system — and the system, not the operator, is what's wrong here.**

In the real world his Composer account *does* have a longer track record than Planet Stopper's guard has been running. His expectation that "if held" should be able to reach further back than "what the bot did" is a sound intuition.

But on this tab, **"live / if-held" is not a record of his account.** It is a *per-day counterfactual column* (`current_return`) that Planet Stopper writes into `shadow_history` on the same cycle, in the same row, as the bot's `shadow_return`. The two series are siblings inside one table:

- one query returns both columns (E-1),
- the route unpacks both from that one call (E-2),
- so **held cannot outlive bot by a single day.** They are the same rows.

### …but on the MAIN DASHBOARD he is literally right — and that is the #1 defect

**Post-integration correction.** He wrote "I just checked my drawdown **in performance**", so §5's analysis above is the direct answer to what he clicked. But his *expectation* almost certainly comes from the main dashboard, where **the Held Max DD genuinely is a lifetime number** — Composer's own `max_drawdown` since `invested_since` (e.g. 2024-07-12), read straight into `bot_state` (E-21).

So across the two surfaces:

| Surface | Held / live basis | Bot basis | Result |
|---|---|---|---|
| Performance tab | `shadow_history.current_return` — 53 days | `shadow_history.shadow_return` — 53 days | Same length. His expectation **unmet** |
| Main dashboard Max DD | Composer lifetime scalar — **since 2024-07-12** | `shadow_history`-scoped — 53 days | His expectation **met, and that is exactly what makes the comparison invalid** (E-21) |

**His instinct was right, and the system is wrong in both directions at once:** where he looked, live *should* reach further and doesn't; where he didn't look, live *does* reach further and is being silently subtracted from a 53-day bot figure as though the two were commensurable. Both are display-truth failures; the second is also a math failure.

**Consequence for remediation:**

- "Just use the longer live data we already have" **is not available on the Performance tab** — there is no longer live series wired into this system (E-16). Composer *does* document three account-scoped dated endpoints on the existing credentials (E-19), so this is now a *scopeable* feature rather than an unknown — but their actual history depth is still **unverified** (§9, U-2).
- **A hard structural ceiling applies regardless (E-22):** a longer live series would extend the **live** leg only. The Planet-Stopper counterfactual exists nowhere but `shadow_history` and cannot predate 2026-06-22, so **a longer guard-alpha comparison is unreachable at any price.** Anyone scoping option (b) must keep "your live performance since 2024" and "what Guard Alpha saved you" as separately-labelled claims — conflating them would be a second, subtler correctness error of exactly the kind this audit exists to catch.

**What can be honestly promised today:** the display can stop claiming windows it cannot back, and the dashboard can stop comparing a lifetime figure to a 53-day one. Those are options (a) and (c), and both are correct regardless of how (b) resolves.

---

## 6. What the display SHOULD say — the in-repo precedent

The codebase already solved this exact problem once, deliberately, and documented why:

```
# database.py:3616-3621
# … a trailing-365-day window sourced from this table cannot structurally back a
# true year of history by default. _TURNOVER_WINDOWS_DAYS names the windows
# this feature reports; coverage_days (below) makes the honesty explicit per
# window instead of silently implying full coverage the table can't back.
```

`get_exit_turnover_stats` emits `coverage_days = min(window, actual_days)` per window (`database.py:3667-3670`), and the route docstring states the intent plainly: *"coverage_days is capped by retained history, so a retention-pruned 365-day window never silently claims a full year"* (`app.py:4329-4332`).

`GET /api/performance` violates that established standard. It reports `observation_count` (a raw count the operator must mentally compare against a trading-day expectation) but **no coverage figure and no requested-vs-actual comparison** (E-12).

**Target behavior, stated as an operator-visible contract:**

1. Every response declares **requested window** and **actual covered span** (a `coverage_days`-shaped field, mirroring E-13).
2. When actual < requested, the UI says so **in the caption**, e.g. *"53 observations · 60d window requested · only 53 trading days available (history begins 2026-06-22)"* — never the bare, false *"· 60d window"*.
3. Windows that cannot be backed at all are **visibly annotated or disabled**, so clicking 5Y cannot silently return the 30d dataset.
4. The YTD path must render a window label too — today it silently renders none (E-8), which makes the two views look gratuitously different for the wrong reason.
5. Keep the existing 30-observation stability banner **separate** — it answers "are these metrics statistically stable?", not "does this window have the data it claims" (E-11). Conflating them is what let this defect hide.

---

## 7. Remediation option set (for the PM)

Options are independent. (a) is always correct; (c) is empty this cycle; (b) is the only one that could satisfy the operator's literal expectation, and it carries real uncertainty.

### Option (a) — Display-honesty fix — **RECOMMENDED, do this regardless**

*Scope:* `app.py` `api_performance` response body + `static/performance.js` caption + `templates/performance.html`.

Add a coverage field to the response (requested window, actual covered trading days, earliest available date), render requested-vs-actual in the caption, fix the `typeof win === 'number'` YTD gap (E-8), and annotate or disable windows exceeding coverage.

| | |
|---|---|
| **Fixes** | The operator's stated complaint ("accurate and informational of restrictions to data length") in full |
| **Cost** | Small. One route response extension + one JS render function + template copy |
| **Risk** | Low. `window_days` currently has **zero** test assertions and `obs-caption` is only presence-tested (E-14) — nothing pins the current wording. New codepath ⇒ Toxic Pair TDD per project rules |
| **Precedent** | Direct: mirror `coverage_days` (E-13) rather than inventing a new shape. **And note S-5** — `/api/hero-chart/<window>` already computes an `available_days` field of exactly this kind (`app.py:3386`) that nothing renders. The honesty signal is partly built already; the gap is a render layer, which makes this cheaper still |
| **Caveat** | Does **not** give him more history. It stops the display lying about how much there is |

### Option (b) — Data-depth fix — **(b1) RULED OUT; (b3) conditional on U-2**

**U-1 is now RESOLVED, and it kills the cheap sub-option:**

- **(b1) Raise `SHADOW_HISTORY_RETENTION_DAYS` — ❌ RULED OUT. Do not do this.** The cause is **genesis, not pruning** (E-18/E-20): data begins at the 2026-06-22 Guard-Alpha go-live floor, and the 180-day prune cutoff (~2026-03-07) sits *before* it, so retention has never deleted a single row. Raising it preserves nothing that isn't already preserved and buys the operator **zero** additional history. *(This also corrects the "retention" framing carried in `DE-RETIRE-CORE-001` and `feature-plans/retirement-approval-polish-2d.md:45` — see `DE-PERF-WINDOW-TRUTH-001` §"Corrected framing".)*
- **(b2) Wait.** The only thing that deepens `shadow_history` is elapsed time. Coverage grows one trading day per trading day, with no engineering required.
- **(b3) Source a genuinely longer LIVE series** (the operator's literal ask) → now *scopeable* rather than speculative: the endpoints exist, are documented, are account-scoped (hence realized, not simulated), and need **no new credential** (E-19). Still requires (i) a read-only probe confirming the series actually reaches back to inception — **`[Unverified]`**, §9 U-2 — (ii) a new client + ingestion path, (iii) a basis decision (`cumulative_twr_series` is TWR; the dashboard shows `simple_return`, and mixing them un-fixes `DE-AUDIT-BL5-12-001`/BL-12), and (iv) hard provenance separation per E-22. This is a **feature cycle, not a fix**. Do not scope it as a quick win.
  - **Cheapest next step:** three read-only GETs on existing credentials to settle U-2 before any scoping. `composer-api-researcher` recommends probing `/symphonies/{symphony-id}` first (highest information per call; directly answers whether per-symphony history reaches the 2024-07-12 `invested_since`).
  - **Explicitly NOT usable:** `/backtest`. It will happily return a dated 2024→today series that *looks* like the answer, on four independent structural grounds (no `account-id`, fictional `capital`, modeled costs, and it replays today's tree counterfactually). Named here so nobody reaches for it under time pressure.

### Option (c) — Computation-bug fix — **CORRECTED: APPLICABLE, and it outranks (a)**

> **This section originally read "NOT APPLICABLE."** That verdict was correct **for `/api/performance`** — actively hunted for and genuinely absent (D2/E-4/E-5) — but it was written before `perf-doc`/`perf-trace` reported two class-(c) findings on the **main dashboard**. The original wording is left visible here rather than quietly replaced, because "no computation bug" is exactly the kind of reassuring conclusion that should not be allowed to slip into the record unqualified.

- **(c-i) `/api/strip/<window>`'s `max_drawdown` is never re-windowed — real, currently DORMANT.** `compute_windowed_portfolio_strip` threads `window=` into `cumulative_return` and `vol_bot`/`vol_held`, but passes `max_drawdown` straight through from `get_portfolio_max_drawdown(...)`, which has **no `window` parameter at all** (`analytics.py:1251-1272`) — contradicting the route's own docstring (`app.py:3414-3417`). Byte-identical across every window token. **Zero operator-visible impact today** (the sole consumer is gated on a `data_as_of` field this payload never carries), so it is a landmine, not a live symptom — but any future change that wires a consumer to it ships a silent lie. Fix or delete the field; do not leave it.
- **(c-ii) Dashboard Bot-vs-Held Max DD compares a lifetime scalar to a 53-day one — LIVE, 5+ render sites, zero disclosure. ⭐ HIGHEST-SEVERITY FINDING OF THIS AUDIT.** (E-21.) **Disclosure alone is NOT sufficient here, and this is the one place in this audit where that is true.** Everywhere else the numbers are honest and merely under-explained, so a `title=`/caption fix is right. Here the row exists to answer *"is the bot helping?"* — and a tooltip on a lifetime-vs-53-day comparison only converts a confidently-wrong answer into an admittedly-useless one. **Required fix is a computation re-base:** recompute `if_held` MaxDD over the *same* `shadow_history`-anchored window as `dry_run`, keep Composer's lifetime scalar as a separate, separately-labelled figure (it is a real and useful number — just not this comparison's `if_held`), and apply the sibling disclosure pattern only to whatever short-horizon caveat *remains after* re-basing. The needed pattern already exists 24 lines away in the same template (`templates/index.html:961-962`, the Cumulative row's basis footnote) and was simply never extended to the MDD row.

**Recommended sequencing:**

1. **(c-ii) first** — it is live, wrong, and drives the operator's core "is the bot helping" judgement. Correctness outranks disclosure.
2. **(a) next** — cheap, low-risk, always correct, and it is the literal answer to what he asked for.
3. **(c-i)** — fix or delete the dormant field before something consumes it.
4. **(b3) only after a read-only Composer probe settles U-2.** **(b1) is ruled out; do not authorize a retention bump.**

---

## 8. Secondary findings (real, but not the reported symptom)

| ID | Finding | Severity | Evidence |
|----|---------|----------|----------|
| S-1 | **"1Y" means different things on two tabs.** Performance's 1Y sends `252` **trading** days; the History tab's 1Y was changed to `365` **calendar** days under `DE-GAS-COHERENCE-001`. This dual contract is **deliberate and documented**, not a bug — but it does mean the same label spans different periods on two surfaces, which matters for an audit about display truth | INFO (documented-deliberate) | `templates/performance.html:421-422`; design comment `app.py:4781-4783`; ruling `DECISIONS.md:5825-5828` |
| S-2 | **`/api/settings/flush-resync` carries a stale hardcoded allowlist.** `_REAL_POST_MORTEM_DATES` is a frozenset of 11 dates "Verified 2026-05-21" (`app.py:5833-5850`), and Phase 1 deletes every `post_mortem_*.json` **not** in it. POSTed today it would delete every post-mortem produced since 2026-05-20 — i.e. it destroys history depth. Latent (requires an operator POST); **not** the cause of the reported symptom; flagged because this audit is about data depth | MEDIUM (latent, data-destructive) | `app.py:5833-5850`, `app.py:5877-5894` |
| S-3 | The YTD calendar slice has no post-slice `<2` guard, so a 1-element series can reach the metrics layer where the numeric path would have returned `None` | LOW | `app.py:3332-3333` vs `analytics.py:1655-1656` |
| S-4 | **A fourth, divergence-risked mechanism for the same "YTD" token.** The History tab reimplements the calendar cutoff **client-side** in local-timezone `Date` arithmetic instead of using the shared server-side `analytics._window_cutoff_date("ytd")` every other surface uses | LOW–MED | `static/history.js:389-395` (`perf-doc`) |
| S-5 | **Two more fully-computed disclosure fields with zero render consumers** — `/api/hero-chart/<window>`'s `available_days` and `/api/history/<int:days>`'s `window_days`. Same defect class as `DE-AUDIT-BL4-001`: the backend already computes the honesty signal and the UI never shows it. Directly relevant here — `available_days` is *exactly* the coverage disclosure §6 asks for, already built | MEDIUM | `app.py:3386`, `app.py:4628`; grep-confirmed zero consumers (`perf-doc`) |

---

## 9. What could NOT be determined, and why

Stated plainly rather than papered over. All four items were pursued; **three closed on team evidence, one remains genuinely open.** Statuses below reflect the post-integration state — the pre-integration draft listed all four as unresolved.

| ID | Open question | Status | Detail |
|----|---------------|--------|--------|
| **U-1** | Retention pruning or genesis? | ✅ **RESOLVED — genesis** | `perf-data` verified against the live droplet: data begins 2026-06-22 (Guard-Alpha go-live floor), 53 trading days / 73 calendar days as of 2026-09-03. The 180-day prune cutoff (~2026-03-07) predates the floor, so retention has deleted nothing. **Option (b1) ruled out** (E-18/E-20) |
| **U-2** | Does a longer live series exist to source from? | ⚠️ **PARTIALLY RESOLVED — existence YES, depth UNVERIFIED** | The premise that Composer exposes no dated series is **false**: three documented, account-scoped, date-indexed endpoints exist on the credentials already in use (E-19). **But whether they reach back to inception, and their true granularity, is `[Unverified]` — undocumented, and settleable only by a live read-only probe that was not performed.** This is now the single blocking unknown for option (b3), and it is a *narrower and more tractable* unknown than when this document was drafted |
| **U-3** | Numeric confirmation that 60d/90d/125d/YTD/1Y/5Y return identical series on production data | ❌ **STILL UNRESOLVED** | The *mechanism* is proven from source (E-6/E-7) and the *history depth* is now measured (E-20), so the conclusion follows deductively — but the button-by-button sweep was never separately executed. **E-17 remains labelled `interpretation`, not fact.** A deduction from two verified facts is strong; it is still not a measurement, and is not promoted to one here |
| **U-4** | Independent corroboration of the code trace | ✅ **RESOLVED — corroborated, and extended** | Teammate evidence landed via `perf-doc`, independently converging on the caption defect (E-8) and confirming D1/D2/D3. **It also went further than this document's original scope**, surfacing the two main-dashboard class-(c) findings that reversed §7(c). Recorded because it is the single most consequential outcome of cross-verification in this audit: the synthesis lead's solo trace was *correct but incomplete*, and would have shipped a reassuring "no computation bug" headline had it stood alone |

**Residual honesty note.** Two things in this document rest on deduction rather than measurement and are labelled as such throughout: **E-17** (the six-of-seven-buttons claim, §U-3) and the `[Unverified]` depth of the Composer endpoints (§U-2). Neither is load-bearing for the recommended sequencing — (c-ii), (a) and (c-i) are all justified without them — but neither should be quoted onward as a measured result.

**Honest scoping note on the PM's adjacent-evidence warning.** The prior observation that droplet `shadow_history` holds only ~49 trading days was treated as a *hypothesis*, not a cause — and that discipline paid off twice. First, the count was approximately right but **the attributed cause was wrong**: it was never retention pruning, it is a go-live floor (E-18/E-20), and "extend retention" — the fix that hypothesis invites — would have bought the operator nothing. Second, the mechanism this audit establishes holds *independently* of the number: oversized windows collapse silently (E-6), held and bot are the same rows (E-1), and the caption misstates the window (E-8/E-9). The history length only determines *which* buttons collapse, not *whether* the display is dishonest when they do. **Causes genuinely stack here** — truncated data **and** silent collapse **and** a false window label **and**, on the dashboard, an invalid lifetime-vs-53-day comparison. The audit did not stop at the first sufficient explanation, and the finding it would have missed by doing so (c-ii) is the most severe one it found.

---

## 10. Bottom line for the PM

- **⭐ Fix the dashboard Max DD comparison first (c-ii).** It is live, it silently compares a lifetime figure to 53 days, and it drives the operator's "is the bot helping" judgement. **A tooltip is not enough here** — it needs a computation re-base. This outranks the tab he actually complained about.
- **On the Performance tab the arithmetic is fine and the label is a lie.** Ship option (a) — cheap, low-risk, nothing pins the current wording (E-14).
- **The operator's instinct was right on both counts, and the system fails it in opposite directions:** on Performance, live *should* reach further and doesn't (E-1); on the dashboard, live *does* reach further and is being subtracted from a 53-day bot figure as if commensurable (E-21).
- **❌ Do NOT authorize a retention bump.** U-1 is settled: the cause is the 2026-06-22 go-live floor, not pruning. Retention has deleted nothing and raising it preserves nothing. *(This also corrects the framing in two prior entries — see `DE-PERF-WINDOW-TRUTH-001` §"Corrected framing".)*
- **A longer live series is now scopeable but not yet justified.** The endpoints exist on existing credentials (E-19); their reach is unverified (U-2). Three read-only GETs settle it. **And even a "yes" extends only the live leg — a longer guard-alpha comparison is structurally unreachable at any price (E-22).**
- **Do not quote E-17 as measured.** It is a sound deduction from two verified facts, and it is still not a measurement (U-3).
