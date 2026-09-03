# Performance-Tab Window Truth — Audit Verdict

**Run date:** 2026-09-03
**Branch / base SHA:** `audit/perf-window-truth` @ `731cb778`
**Team:** `perf-trace` (code path) · `perf-data` (live droplet) · `perf-synth` (synthesis lead)
**Type:** READ-ONLY diagnostic. No production code changed by this audit.

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

**Classification:** this is a **display-honesty defect (severity HIGH)** stacked on a **data-depth limitation (severity MEDIUM, ops)**. **No max-drawdown computation bug was found** — the metric is computed correctly over whatever series it is handed (§4, F-4). The lie is in the label, not in the arithmetic.

**Verdict on the operator's core complaint — "the displays need to be accurate and informational of restrictions to data length": he is right, and the codebase already has the correct pattern implemented elsewhere and simply did not apply it here** (`GET /api/exit-turnover`'s `coverage_days`, §6).

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
| D7 | Why is the underlying history short — retention prune or genesis? | **UNRESOLVED** | — | See §9. Blocked on live droplet evidence. |
| D8 | Does a longer live series exist anywhere to source from? | **Not in this codebase; external availability UNRESOLVED** | MED / — | §5. Code side proven; Composer payload probe outstanding. |

---

## 3. The mechanism, step by step

The route accepts the window two different ways, and **both** degrade silently:

**Numeric buttons (30 / 60 / 90 / 125 / 252 / 1260)** — a *trading-day tail slice*:

```python
# analytics.py:1652-1654
all_days = sorted(day_map.keys())
sorted_days = all_days if days is None else all_days[-days:]
```

`all_days[-60:]` on a 49-element list returns **all 49 elements**. Python slicing does not error, warn, or signal short coverage. `all_days[-1260:]` returns the same 49. Six of the seven buttons therefore produce byte-identical input.

**The YTD button** — a *calendar cutoff* on the full series:

```python
# app.py:4810
_fetch_days = None if is_ytd else days
# app.py:3332
idx = [i for i, d in enumerate(dates) if cutoff_iso is None or str(d) >= cutoff_iso]
```

`analytics._window_cutoff_date("ytd")` correctly resolves to `date(today.year, 1, 1)` (`analytics.py:1859-1860`) — that part is **not** buggy. But if every retained row already post-dates 1 Jan, the filter removes nothing and YTD also returns all 49.

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
| E-17 | With 49 trading days available, six of seven buttons return identical data | Follows deductively from E-6 + E-7 | *interpretation* (deductive; pending numeric confirmation, §9) |
| E-18 | Retention (180d) alone cannot explain a ~49-trading-day table | 180 calendar days ≈ 124 trading days ≫ 49 | *interpretation* (arithmetic; the decisive live check is §9 U-1) |

---

## 5. The operator's exact expectation — answered directly

> "even if the bot numbers don't go back that far I would've expected my live numbers to"

**He is reasoning correctly about the world and incorrectly about this system — and the system, not the operator, is what's wrong here.**

In the real world his Composer account *does* have a longer track record than Planet Stopper's guard has been running. His expectation that "if held" should be able to reach further back than "what the bot did" is a sound intuition.

But on this tab, **"live / if-held" is not a record of his account.** It is a *per-day counterfactual column* (`current_return`) that Planet Stopper writes into `shadow_history` on the same cycle, in the same row, as the bot's `shadow_return`. The two series are siblings inside one table:

- one query returns both columns (E-1),
- the route unpacks both from that one call (E-2),
- so **held cannot outlive bot by a single day.** They are the same rows.

**Consequence for remediation:** the fix "just use the longer live data we already have" **does not exist as stated** — there is no longer live series in the system today (E-16). Making held reach further back is not a display fix; it is a *new data-sourcing feature* (option (b), §7), and its feasibility depends on whether Composer will actually serve a per-day historical series — which this audit did **not** confirm (§9, U-2).

**What can be honestly promised today:** the display can stop claiming windows it cannot back. That is option (a), and it is correct regardless of how (b) resolves.

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
2. When actual < requested, the UI says so **in the caption**, e.g. *"49 observations · 60d window requested · only 49 trading days available (history begins 2026-06-26)"* — never the bare, false *"· 60d window"*.
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
| **Precedent** | Direct: mirror `coverage_days` (E-13) rather than inventing a new shape |
| **Caveat** | Does **not** give him more history. It stops the display lying about how much there is |

### Option (b) — Data-depth fix — **CONDITIONAL, needs §9 resolved first**

Two mutually exclusive sub-cases, and **which one applies is currently unknown** (§9, U-1). Do not authorize this option before that is settled:

- **(b1) If history is retention-pruned** → raising `SHADOW_HISTORY_RETENTION_DAYS` (`app.py:794`) is a cheap `.env` change that grows coverage *going forward only*. It cannot restore already-deleted rows. Cost: disk growth + slower unbounded queries.
- **(b2) If history is genesis-limited** (the table simply starts when it started) → **raising retention buys literally nothing.** The only way to deepen history is to backfill from an external source, i.e. option (b3).
- **(b3) Source a genuinely longer live series** (the operator's literal ask) → requires (i) confirming Composer actually serves a per-day historical series (§9, U-2 — **unconfirmed**), (ii) a new client + ingestion path, (iii) reconciling a Composer-sourced held series against the existing `current_return` basis. This is a **feature cycle, not a fix**, and it re-opens the held-vs-bot basis questions settled in `DE-HELD-BASIS-001`. Do not scope it as a quick win.

### Option (c) — Computation-bug fix — **NOT APPLICABLE**

No max-drawdown computation defect was found (D2/E-4/E-5). The arithmetic is right; the labelling is wrong. **If a computation bug had been found it would outrank both options above** — it was specifically hunted for and not present.

**Recommended sequencing:** ship (a) now (cheap, always correct, directly answers the operator). Resolve §9 U-1/U-2. Only then decide (b), and scope (b3) as a feature if and only if Composer's payload is confirmed to carry the series.

---

## 8. Secondary findings (real, but not the reported symptom)

| ID | Finding | Severity | Evidence |
|----|---------|----------|----------|
| S-1 | **"1Y" means different things on two tabs.** Performance's 1Y sends `252` **trading** days; the History tab's 1Y was changed to `365` **calendar** days under `DE-GAS-COHERENCE-001`. This dual contract is **deliberate and documented**, not a bug — but it does mean the same label spans different periods on two surfaces, which matters for an audit about display truth | INFO (documented-deliberate) | `templates/performance.html:421-422`; design comment `app.py:4781-4783`; ruling `DECISIONS.md:5825-5828` |
| S-2 | **`/api/settings/flush-resync` carries a stale hardcoded allowlist.** `_REAL_POST_MORTEM_DATES` is a frozenset of 11 dates "Verified 2026-05-21" (`app.py:5833-5850`), and Phase 1 deletes every `post_mortem_*.json` **not** in it. POSTed today it would delete every post-mortem produced since 2026-05-20 — i.e. it destroys history depth. Latent (requires an operator POST); **not** the cause of the reported symptom; flagged because this audit is about data depth | MEDIUM (latent, data-destructive) | `app.py:5833-5850`, `app.py:5877-5894` |
| S-3 | The YTD calendar slice has no post-slice `<2` guard, so a 1-element series can reach the metrics layer where the numeric path would have returned `None` | LOW | `app.py:3332-3333` vs `analytics.py:1655-1656` |

---

## 9. What could NOT be determined, and why

Stated plainly rather than papered over. Both open items were pursued; neither is closed by evidence in hand.

| ID | Open question | Why it matters | Why unresolved |
|----|---------------|----------------|----------------|
| **U-1** | Is the short history caused by **retention pruning** or by **genesis** (the table simply starting recently / a reset)? | **Decides option (b) entirely.** If genesis, raising retention is a no-op that would waste a cycle and not help the operator | Requires the droplet's actual `SHADOW_HISTORY_RETENTION_DAYS` and `MIN(trading_day)`. Requested from `perf-data`; **not received at time of writing**. Note the tension: the code default is 180 days ≈ 124 trading days (E-15), which is **inconsistent** with a ~49-trading-day table — so either the `.env` overrides it downward or the cause is genesis. Unresolved either way |
| **U-2** | Does Composer's `symphony-stats-meta` payload actually contain a per-day historical series (e.g. a `dvm_capital`-style map)? | **Decides whether option (b3) is even possible.** Without it, the operator's literal expectation cannot be met at any price | The repo only consumes scalar fields and its live contract test asserts scalars only (E-16) — that proves *this codebase* doesn't use a series, **not** that the payload lacks one. Requires a live payload probe; requested from `perf-data`; **not received at time of writing** |
| **U-3** | Numeric confirmation on production data that 60d/90d/125d/YTD/1Y/5Y return identical series | Would upgrade E-17 from deduction to measurement | Requested from `perf-data`; **not received at time of writing.** The deduction rests on E-6/E-7, which are solid source reads; but a deduction is not a measurement and is labelled as such |
| **U-4** | Independent corroboration of the code trace by `perf-trace` | Cross-verification of D1/D2/D3 | `perf-trace` had not reported at time of writing. **Mitigation:** the synthesis lead performed the full trace independently rather than relying on an unreceived report; every D1–D6 verdict above is backed by a first-hand `file:line` read at `731cb778`, not by relay |

**Honest scoping note on the PM's adjacent-evidence warning.** A prior observation that droplet `shadow_history` retains ~49 trading days was treated as a *hypothesis*, not a cause. What this audit establishes independently of that number is the **mechanism**: oversized windows collapse silently (E-6), held and bot are the same rows (E-1), and the caption misstates the window (E-8/E-9). Those hold at *any* history length. The specific number only determines *which* buttons collapse — not *whether* the display is dishonest when they do. **Multiple causes genuinely stack here** (truncated data **and** silent collapse **and** a false window label); the audit does not stop at the first sufficient explanation.

---

## 10. Bottom line for the PM

- **The arithmetic is fine. The label is a lie.** Ship option (a).
- **The operator's instinct is right, but the plumbing can't honor it:** held is the same rows as bot (E-1). "Use the longer live data" is not a fix that exists today — it's an unscoped feature contingent on U-2.
- **Do not authorize a retention bump until U-1 is answered.** If the cause is genesis, that change helps nobody.
- **Two open questions block the data-depth decision (U-1, U-2).** Neither blocks the display fix.
