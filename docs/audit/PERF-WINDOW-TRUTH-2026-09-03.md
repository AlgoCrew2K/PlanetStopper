# Performance-Tab & Main-Dashboard Window-Truth — Audit Verdict

**Run date:** 2026-09-03
**Branch / base SHA:** `audit/perf-window-truth` @ `731cb778`
**Team:** `perf-trace` (code path) · `perf-data` (live droplet) · `perf-doc` (docs + cross-surface sweep) · `perf-composer-api` (external API research) · `perf-synth` (synthesis lead) · `team-lead` (arbiter)
**Type:** READ-ONLY diagnostic. **Zero production-code diff** on this branch.

**Revision history** — recorded because this audit reversed itself repeatedly and the corrections are part of the evidence:

| Rev | Basis | Status |
|---|---|---|
| v1 | Synthesis lead's solo code trace, teammate reports outstanding | superseded |
| v2 | Partial integration (`perf-data` genesis + Composer endpoint existence) | superseded |
| **v3 (this)** | **Full team integration + team-lead rulings.** Adds F3/F7, reverses the "no computation bug" headline's scope, retracts one quantification, promotes E-17 to fact | **current** |

---

## 0. The reported defect

Operator, verbatim:

> "I just checked my drawdown in performance and swapped to ytd from 60d and nothing changed. even if the bot numbers don't go back that far I would've expected my live numbers to. the displays need to be fucking accurate and informational of restrictions to data length."

---

## 1. Executive verdict

**He reported a display defect on the Performance tab. That defect is real. But the investigation it triggered found something worse on the dashboard he did not report — and that is the headline.**

### ⭐ The headline: the dashboard's Max DD row is 98.7% artifact, and it declares a winner on it

The main dashboard renders a **Max DD** comparison of `Bot 5.79%` against `Held 23.44%`, stamps an **`α +17.66%`** delta badge on it, and applies a **`winner` CSS class to the Bot bar**. Every one of those three numbers is unsound:

| Quantity | Value |
|---|---|
| Rendered gap (`if_held 23.4446` − `dry_run 5.7874`) | **17.6572 points** — badge reads `α +17.66%` |
| Honest, matched-basis gap (same 53 days, same metric, same units) | **0.2253 points** (held 10.5875% vs bot 10.3622%) |
| **Artifact** | **17.43 points = 98.7% of the rendered figure** |

**Three defects stack to produce it** — period, subject, and units:

1. **Period mismatch.** `if_held` is Composer's **lifetime** `max_drawdown` scalar (reaching back to `invested_since`, as early as **2024-07-12**); `dry_run` can only span `shadow_history` (from 2026-06-22, 53 days).
2. **Subject / metric-definition mismatch — the one that makes re-basing insufficient.** `dry_run` is **not the bot's drawdown at all.** `analytics.py:1067-1069` adds `if_held` as a *constant* to every point of `bot_equity[t]`, and peak-to-trough is translation-invariant under a constant offset. `perf-data` proved this by force-injection: setting `max_drawdown` to `0.1805 / 0.0 / 9.99 / −5.0` left `dry_run` identical to 10 decimals (`2.0328304291537336 / …32 / …05 / …62`). **`dry_run` is the peak-to-trough of the guard-alpha *divergence residual*** — a categorically different and structurally small quantity, identically zero on every untriggered day, and small on *any* amount of data.
3. **Units mismatch.** `dry_run` is an un-normalized percentage-point peak-to-trough; Composer's scalar is a normalized fraction. (Consistent with the translation-invariance result — a normalized drawdown would not be translation-invariant.)

**Aggravating: the UI does not merely display this, it adjudicates on it** (`templates/index.html:885-909` computes `mdd_bot_wins`/`mdd_alpha`; `:985-992` renders them). Live at **3 sites** — the hero vs-row plus both per-symphony card blocks.

**And the codebase already knew.** A prior cycle diagnosed this exact mismatch — the comment at `templates/index.html:887-890` says verbatim that "the bot MDD is computed from the shadow trajectory… while the held MDD is from Composer's full lifetime. A winner bar on a 8-day bot vs lifetime held comparison would be misleading." **The correct diagnosis was given the wrong remedy:** a *data-depth* guard (`mdd_insufficient`, `len(hist_dates) < 30`, `app.py:1702`). Because the mismatch is **structural, not depth-related**, that guard **expired silently the moment history crossed 30 trading days — on or about 2026-08-04.** The dashboard has been adjudicating on a 98.7% artifact, unqualified, for roughly a month. *(Two further scope gaps: the guard only ever covered the hero row — both per-symphony card sites compute `mdd_alpha` with no guard at all, `templates/index.html:1230-1237` and `:1315-1322`.)*

### The reported defect: Performance tab — display-honesty, arithmetic correct

**Scoped statement (this wording is load-bearing):** there is **no computation bug in `/api/performance`.** MaxDD is genuinely computed over the already-sliced series (`app.py:4890-4891`; `analytics.py:348-427` is pure over its argument). Independently confirmed by `perf-trace` and `perf-doc`. **That exoneration applies to this route ONLY** — F3 and F7 are live on the dashboard.

What the operator hit: with 53 trading days retained, `all_days[-60:]` returns all 53 and the YTD cutoff removes nothing, so **six of seven buttons return byte-identical data** — while the caption asserts `"· 1260d window"` for a 5Y click, silently drops the label entirely on YTD, and the banner stays hidden because its threshold is a *stability* floor (`< 30`), not a coverage check.

**Verdict on his stated requirement — "accurate and informational of restrictions to data length": he is right on both surfaces**, and the codebase already ships the correct pattern in three places it simply never applied here (§6).

**Honest business reading, stated without spin:** over the 53 days that can be measured, Guard Alpha shows **no meaningful drawdown advantage (0.23pp)**. That is a measurement, not a verdict on the strategy — 53 days is short and MDD is noisy. Nothing here supports "the bot works" *or* "the bot doesn't."

---

## 2. Surface-by-surface matrix

Two statements in this audit look contradictory side by side and are both true of *different surfaces*. A careless reader will get this exactly backwards.

| | Performance tab (`/api/performance`) | Main dashboard (`/api/state`, `/api/strip`) |
|---|---|---|
| **Held / `if_held` source** | `current_return` **column of the same `shadow_history` rows** as bot (E-1) | Composer's **lifetime** `max_drawdown` scalar (E-21) |
| **Can held outlive bot?** | **No** — same rows, one query | **Yes** — and that is precisely the defect |
| **Basis-mismatch defect?** | **CLEAN** ✅ | **PRESENT — F7, #1** ❌ |
| **MaxDD windowed correctly?** | **Yes** ✅ | **No — F3**, never receives `window` ❌ |
| **Primary defect** | Silent window collapse + false window label | Mismatched-basis, winner-badged comparison |

**Genuinely good news, worth stating plainly: the tab the operator actually reported is clean of the basis defect. The dashboard he didn't report is not.**

### Per-dimension verdicts

| # | Dimension | Verdict | Confidence |
|---|---|---|---|
| D1 | Window token reaches the MaxDD computation (`/api/performance`)? | **YES — correctly windowed** | HIGH |
| D2 | Computation bug in `/api/performance`? | **NO — actively hunted, absent** | HIGH |
| D3 | Held sourced from a longer record than bot (Performance)? | **NO — same rows, same query** | HIGH |
| D4 | Oversized windows collapse silently? | **YES — both mechanisms** | HIGH (live-reproduced) |
| D5 | Does the UI disclose actual coverage? | **NO — and it misstates it** | HIGH |
| D6 | Established in-repo honesty pattern not applied? | **YES — three of them** | HIGH |
| D7 | Retention prune or genesis? | **GENESIS — 2026-06-22 go-live floor** | HIGH |
| D8 | Longer live series available? | **YES — Composer daily series, live-probed back to 2024-07-12** | HIGH |
| D9 | Computation defect anywhere in scope? | **YES — F3 (dormant) and F7 (#1, live)** | HIGH |

---

## 3. The mechanism — and two DIFFERENT collapse boundaries

**Do not merge these into one curve.** Performance and the hero-chart/strip degrade at *different window sizes* because they use *different mechanisms*, even though both bottom out on the same 53-day dataset.

**Performance tab — positional trading-day slice** (`all_days[-days:]`, `analytics.py:1654`, `:1759-1761`). Boundary sits **between 30d and 60d**:

```
days=30    n=30  MDD_bot=-0.017943  MDD_held=-0.019888   DIFFERS
days=60    n=53  MDD_bot=-0.051671  MDD_held=-0.055012   collapsed
days=90/125/252/1260/ytd   n=53     identical to 60d
```

**Hero-chart / strip — calendar cutoff** (`_window_cutoff_date`). Boundary sits **between 60d and 90d**:

```
30d   cutoff 2026-08-04  n=23  DIFFERENT (real subset)
60d   cutoff 2026-07-05  n=44  DIFFERENT  MDD_bot=-0.027645  MDD_held=-0.029558
90d   cutoff 2026-06-05  n=53  IDENTICAL  MDD_bot=-0.051661  MDD_held=-0.054991
125d / 1y / ytd / all    n=53  IDENTICAL
```

**Consequence:** the operator's exact "60d→YTD, nothing changed" click reproduces **on Performance** (both already at the 53-day ceiling). It would **not** have reproduced on the hero-chart picker, where 60d is a genuine 44-day subset.

**Therefore — immunity is window-and-data dependent, never structural.** A calendar cutoff is non-degenerate exactly for cutoffs falling *inside* the retained span and degenerate beyond it. Both the original "structurally immune" claim and the sweeping counter-challenge were wrong; this is the accurate middle. Beyond the span, what saves the calendar surfaces is **disclosure**, not immunity — and that is precisely what Performance lacks.

---

## 4. Evidence table

| ID | Claim | Evidence | Type |
|----|-------|----------|------|
| E-1 | Held and Bot come from ONE query over the SAME rows (Performance) | `analytics.py:1627-1634`; per-symphony `:1734-1743`; unpacked `app.py:4820`/`4827` | FACT |
| E-4 | MaxDD computed over the **sliced** series; no unwindowed source | `app.py:4890-4891`; `analytics.py:348-427` | FACT |
| E-6 | Numeric windows collapse silently when short | `analytics.py:1654`, `:1759-1761` | FACT |
| E-7 | YTD token resolves correctly to Jan 1 (not itself a bug) | `analytics.py:1859-1860` | FACT |
| E-8 | Caption **drops** the window label on YTD | `static/performance.js:450-452` — `typeof win === 'number'` false for the string `"ytd"` (`app.py:4786`, `4907`) | FACT |
| E-9 | Caption **asserts** an unbacked window on numeric clicks | same lines, no coverage check | FACT |
| E-10 | Banner suppressed in exactly this regime | `app.py:44` (`=30`), `app.py:4884`, `static/performance.js:388` | FACT |
| E-11 | That banner is a *stability* warning, not a *coverage* disclosure | `templates/performance.html:430-431` | FACT |
| E-12 | Response body has no coverage field | `app.py:4898-4917` | FACT |
| E-13 | In-repo honesty precedent exists and is tested | `database.py:3624-3676`; rationale `:3616-3621`; `app.py:4329-4332`; `tests/database/test_exit_turnover_stats.py` | FACT |
| E-14 | `window_days` has **zero** test assertions; `obs-caption` presence-tested only | grep of `tests/`; `tests/dashboard/test_risk_adjusted_display.py:200` | FACT |
| E-15 | Retention default 180; **unset** on the droplet | `app.py:794`; `grep -i retention /opt/planetstopper/.env` → exit 1 | FACT |
| **E-17** | **Six of seven buttons return byte-identical data; only 30d differs** | `perf-data` numeric table (§3); MaxDD identical to 15 dp; `perf-trace` reproduced dates-list `==` True | **FACT** *(promoted from interpretation in v1/v2)* |
| E-18 | Retention is **not** the cause — prune cutoff (~2026-03-07) predates the data | `app.py:794`, `database.py:3320-3334` vs the 2026-06-22 floor | FACT |
| E-19 | Composer publishes 3 account-scoped dated-series endpoints; same auth we already send | `perf-composer-api` report §Q1/Q4 (`portfolio-history`, `symphonies/{id}`, `symphony-historical-holdings`) | FACT |
| E-20 | `shadow_history` begins **2026-06-22**; 53 trading days / 73 calendar days | `MIN/MAX/COUNT(DISTINCT trading_day)` = `2026-06-22 / 2026-09-03 / 53`, identical across all 11 symphonies; `exit_triggers` + all 52 `post_mortem_*.json` share the date | FACT |
| E-21 | Dashboard Max DD pairs a lifetime scalar against a 53-day divergence residual | `analytics.py:1042` (`if_held`), `:1049` (`dry_run`); `docs/research/dashboard/composer-per-symphony-stats.md:93` | FACT |
| E-22 | A longer live series extends the **live** leg only | Composer returns realized performance; the counterfactual exists only in `shadow_history` | FACT (structural) |
| **E-23** | **`dry_run` is mathematically invariant to `if_held`** | Force-injection `0.1805/0.0/9.99/−5.0` → `dry_run` identical to 10 dp; cause `analytics.py:1067-1069` (constant offset; peak-to-trough translation-invariant) | **FACT** (empirical) |
| **E-24** | **Composer daily series live-probed back to 2024-07-12, zero truncation** | S-1/S-2 return 538–539 daily points, weekend-gap spacing confirmed; both raw `series` and cash-flow-clean `deposit_adjusted_series`/`cumulative_twr_series`; 4 read-only GETs, zero 429s | **FACT** — supersedes the earlier "no per-day series exists" reading, which was based on `symphony-stats-meta` alone |
| **E-25** | **The mismatch was previously diagnosed and mis-remedied** | `templates/index.html:887-890` names it verbatim; remedy was a depth guard (`app.py:1702`, `<30`) that cannot fire on a structural mismatch — stopped firing ~2026-08-04 | **FACT** |
| E-26 | `invested_since` spans 2024-07-12 → 2026-04-01 | `symphony-stats-meta`, all 11 symphonies | FACT |

---

## 5. The operator's exact expectation — answered directly

> "even if the bot numbers don't go back that far I would've expected my live numbers to"

**He is right, and the system fails him in opposite directions on the two surfaces.**

- **On Performance:** live *should* reach further and **doesn't** — held is the `current_return` column of the same rows as bot (E-1). Expectation unmet.
- **On the dashboard:** live *does* reach further — and it is being subtracted from a 53-day divergence residual as though commensurable (E-21). Expectation met, catastrophically.

**And real live history genuinely exists**: `invested_since` reaches 2024-07-12 (E-26), and Composer serves genuine daily series back to that exact date with zero truncation (E-24). So the expectation is not merely reasonable — it is **satisfiable**, which was not clear earlier in this audit.

**Two structural constraints must survive into any fix:**

1. **A longer window extends the live leg only (E-22).** Pre-2026-06-22, Guard Alpha did not exist and executed no trades, so **bot and held are identical by definition** over that period. There is no missing counterfactual to reconstruct — the counterfactual *equals* the realized series. A long-window Bot-vs-Held comparison is therefore honestly constructible: it correctly shows **zero divergence until 2026-06-22, then real divergence after**.
2. **But note the uncomfortable consequence:** on a full-history basis both legs show ~23.4% max drawdown, dominated by 2024–2025 drawdowns the bot was never present for — making Guard Alpha's effect invisible at that scale. **There are exactly two honest presentations:** (a) the matched short window (~10.59% held vs ~10.36% bot — roughly neutral), or (b) full history (both legs identical, because the bot existed for ~4% of the period). **Neither supports the rendered "23.44 → 5.79 improvement." That number is artifact under every honest framing** — an independent corroboration of F7 at highest severity.

---

## 6. What the display SHOULD say — three existing precedents, none applied

The codebase has already solved this **three times**, and the honesty data is in some cases *already computed and simply never rendered*:

1. **`coverage_days`** — `get_exit_turnover_stats` emits `min(window, actual_days)` per window (`database.py:3624-3676`) precisely so "a retention-pruned 365-day window never silently claims a full year" (`database.py:3616-3621`, `app.py:4329-4332`). Tested.
2. **`basis_label`** — `/api/guard-alpha-summary` emits operator-facing **prose** (`"snapshot-time basis, since <earliest> · through <latest>"`, `app.py:3847-3861`) and `static/index.js:1532,1544` writes it to the screen. **This is the only surface in the entire app where computed coverage-honesty data actually reaches the operator.**
3. **`available_days`** — `/api/hero-chart` already computes exactly the field the Performance fix needs (`app.py:3386`) and **nothing renders it** (repo-wide grep: one hit, the producer line).

**The dominant failure mode across every windowed surface in this app is the `DE-AUDIT-BL4-001` anti-pattern: compute the honesty signal, never render it.** `available_days` (`app.py:3386`) and `/api/history`'s `window_days` (`app.py:4628`) are both fully-computed with zero consumers.

**Target contract:** every response declares requested window **and** actual covered span; when actual < requested the caption says so (*"53 observations · 60d requested · only 53 trading days available (history begins 2026-06-22)"*); windows that cannot be backed are annotated or disabled; the YTD path renders a label at all; and the `<30` stability banner stays **separate** from coverage — conflating the two is what let this hide.

---

## 7. Findings ledger

| ID | Finding | Severity | Class |
|----|---------|----------|-------|
| **F7** | **Dashboard Bot-vs-Held Max DD: period + subject + units mismatch, winner-badged, 98.7% artifact.** 3 live render sites. Prior depth guard expired ~2026-08-04 (E-25) | **CRITICAL — #1** | Computation |
| **F3** | `/api/strip/<window>`'s `max_drawdown` never receives `window` (`analytics.py:2016` → `get_portfolio_max_drawdown`, `:1251-1258`, has no such param) — byte-identical across all 6 tokens while `cumulative_return`/`guard_alpha`/`vol_*` in the **same response** vary correctly. **Dormant**: sole consumer `updateComparisonRows` is gated on a `data_as_of` field this payload never carries (`static/index.js:1492-1505`, `DE-CLOSED-BOUNCE-001`) | HIGH (dormant) | Computation |
| F1 | Silent window collapse: 6 of 7 Performance buttons byte-identical (E-17) | HIGH | Data + display |
| F2 | `window_days` echoes the *request*, not delivered coverage — an affirmative false claim. Present on `/api/performance` **and** `/api/history` | HIGH | Display |
| F4 | Caption drops the window label entirely on YTD (E-8) | MEDIUM | Render bug |
| F5 | **Cross-tab semantic mismatch — elevated to its own finding, NOT "documented-deliberate."** Performance's `1Y`=252 **trading** days; History's `1Y`=365 **calendar** days. Two separately-timed fixes landing on different semantics is not a deliberate joint decision merely because each was individually documented — and same-label-different-span is squarely the display-truth class reported | MEDIUM | Semantic |
| F6 | **"YTD" has four independent implementations.** Three surfaces use the shared server-side `_window_cutoff_date`; History reimplements it **client-side** in local-tz `Date` arithmetic (`static/history.js:389-395`) — the same bespoke-YTD-trim pattern deleted server-side under AC-5. Can diverge by a day at TZ/DST boundaries | MEDIUM | Semantic |
| F8 | Coverage fields computed with **zero render consumers**: `available_days` (`app.py:3386`), `/api/history`'s `window_days` (`app.py:4628`) | MEDIUM | Display |
| F9 | Three different validation behaviours across window-token routes: 404 (`/api/strip`), silent-lifetime-fallback (`/api/hero-chart`), fail-open-to-`all` (`/api/guard-alpha-summary`) | LOW | Consistency |
| F10 | `/api/settings/flush-resync`'s `_REAL_POST_MORTEM_DATES` allowlist is stale ("verified 2026-05-21"). POSTed today it deletes every post-mortem since 2026-05-20 — destroying the very data depth this audit concerns. Latent (needs an operator POST) | MEDIUM (latent) | Data-destructive |
| F11 | `analytics.load_post_mortem_history`'s dormant positional slice — same class as F1 | LOW | Document only |
| F12 | YTD calendar slice has no post-slice `<2` guard, where the numeric path returns `None` (`app.py:3332-3333` vs `analytics.py:1655-1656`) | LOW | Robustness |

**Cleared negative findings** (recorded so a future cycle does not re-litigate):

- **`DE-MATH-R0-001` AC-5's shared-calendar-cutoff claim in `.claude/CLAUDE.md` / `docs/generated/app.md:309` is accurate, not overstated.** Verified by `perf-doc`.
- **No `/api/performance` computation bug exists.** The `"(c) a genuine computation bug"` line in `HANDOFF.md` was a generic dispatch-time placeholder written before any findings existed — never a claim about this route.
- **CLAUDE.md's gotcha "Composer's API is poorly documented" is out of date** — an official ~35-endpoint reference exists (E-19). Flagged as a doc correction.

---

## 8. Remediation option set

**Ranked per the team-lead's rulings. Correctness outranks disclosure; a semantic change must not ship wearing a bug-fix label.**

### 1. F7 — fix ALL THREE defects (period + metric definition + units). **RULED: both/all, not partial**

*Not a disclosure fix.* Everywhere else in this audit a `title=`/caption disclosure is correct because the numbers are honest and merely under-explained. **F7 is the exception**: the row exists to answer *"is the bot helping on drawdown,"* and a tooltip on this comparison converts a confidently-wrong answer into an admittedly-useless one without answering it.

**Required shape:**
1. **Both legs recomputed as genuine peak-to-trough of the real compounded return path over the SAME window** — held from `current_return`, bot from `shadow_return`. Already computed: **held 10.5875%, bot 10.3622%** (53-day window, value-weighted with the strip's own `current_value` scheme).
2. Composer's lifetime scalar retained as a **separate, clearly-labelled** figure — never a leg of this subtraction.
3. One normalization convention, adopted explicitly and stated.
4. Disclosure applies only to the residual short-horizon caveat **after** (1)–(3).

**Partial fix explicitly considered and REJECTED.** Fixing only `if_held`'s period would move the display from 23.44 to ~10.59 and still show the bot winning by ~4.80 points — roughly **21× the truth (0.23)** — while the UI *looks* repaired. Replacing a 77× overstatement with a 21× one is worse than leaving it visibly broken: **it launders the error**, and it still compares two different quantities. *(Note: the "12.86pt / 72.8% artifact" figure originally quoted for this scenario has been **RETRACTED** — it mixed a normalized `if_held_matched` against the code's un-normalized `dry_run`. Do not quote it. The clean, fully apples-to-apples figures are 0.2253pt true gap / 98.7% artifact.)*

> **MANDATORY PRE-CONDITION — the one thing that could make this ruling wrong.** `dry_run`/`mdd_bot` may have consumers beyond these 3 render sites. **Before redefining it, enumerate every consumer across the repo** (routes, templates, JS, post-mortems, Discord embeds, advisors) and state the blast radius. If any surface depends on the current divergence-residual semantics, introduce a **new correctly-shaped value alongside** rather than mutating a shared quantity in place. Letting an implementer redefine a shared quantity without that enumeration is how a display fix becomes an engine-adjacent regression.

### 2. Display-honesty fix (F1/F2/F4/F8) — **cheap, correct regardless of everything else**

Add a coverage field (requested window, actual covered days, earliest date), render requested-vs-actual, fix the YTD `typeof` gap, annotate or disable unbackable windows. Mirror `coverage_days`/`basis_label`; **`available_days` already exists and merely needs rendering** (`app.py:3386`). Low risk: `window_days` has zero test assertions, `obs-caption` is presence-tested only (E-14). New codepath ⇒ Toxic Pair TDD. **Per `DE-AUDIT-BL4-001`, this must be RENDERED — never a JSON-field-only fix.**

### 3. F3 — fix or delete the dormant unwindowed strip field

⚠️ **Threading `window` into F3 WITHOUT re-basing F7 is actively worse than nothing** — it makes a mismatched-basis comparison *look* responsive and trustworthy while remaining incomparable. Sequence after F7, or delete the field.

### 4. Semantic-unification decision (F5/F6) — **a PRODUCT call, not a defect fix**

> **Correcting a framing that propagated through this audit:** "port Performance onto the shared seam" is a **SEMANTIC CHANGE, not a bug fix.** Performance's `60d` means 60 **trading** days; returning all 53 is *arithmetically correct for that semantic* — undisclosed, but not wrong math. The shared seam's `60d` means 60 **calendar** days (~44 trading days). Porting **redefines what the button means**; the number changes as a consequence.

The defensible argument for doing it: "60d" reads to a human as sixty calendar days, History already uses calendar, `_WINDOW_TRAILING_DAYS` is calendar — **Performance is the outlier.** That is a good argument for a product decision with a visible consequence (his 60d figure changes), and **he is entitled to know which he is getting.** It would genuinely fix his 60d→YTD click (60d becomes a real 44-day subset); 90d/125d/1y/YTD stay identical until data depth grows, so **disclosure remains mandatory and neither substitutes for the other.**

**Open design question — do not resolve in a diagnosis document:** the shared vocabulary has **no `5y` token at all**, so porting requires deciding what 252 and 1260 become. Tradeoff: calendar days match how an operator reads "1 year"; trading days match how the math is computed.

### 5. Data depth (F-none — capability, not defect)

- **❌ `SHADOW_HISTORY_RETENTION_DAYS` — RULED OUT.** Genesis, not pruning (E-18/E-20). Retention has never deleted a row; raising it recovers **nothing**.
- **✅ Sourcing longer live history is now UNLOCKED** — daily series confirmed to 2024-07-12 (E-24), existing credentials, no ToS delta. This also closes the retirement-recommender's 181-day gate immediately rather than waiting until 2027-03-10. Still a **feature cycle**: basis decision (TWR vs `simple_return` — mixing un-fixes `DE-AUDIT-BL5-12-001`/BL-12) plus hard provenance separation per E-22.
- 🚨 **The `/backtest` trap, loudly.** `/backtest` accepts `start_date`/`end_date` and emits a dated 2024→today series that **looks exactly like realized history and is not** — counterfactual replay of today's tree, caller-supplied capital, modelled fills, and it accepts **no `account-id`** (the structural discriminator). Any implementation MUST provenance-tag the source, same discipline as `if_held_source` under `DE-POSTMORTEM-INTEGRITY-001`. Getting this wrong would inject fabricated history into the operator's real performance display — **a worse defect than anything in this audit.**

### 6. Documentation corrections

Annotate — **never rewrite** — `DECISIONS.md:10973` and `feature-plans/retirement-approval-polish-2d.md:45` with a dated `[correction, 2026-09-03]` pointer: both use "retention" framing for what is actually the go-live floor. Also correct CLAUDE.md's "Composer API is poorly documented" gotcha.

---

## 9. Audit provenance — where this audit was wrong

Recorded at the team-lead's explicit direction: *"a verdict that hides where its authors were wrong is less trustworthy, not more."*

| # | Claim | Corrected to | Falsified by |
|---|-------|-------------|--------------|
| 1 | Short history caused by **retention pruning** (carried in from a 2026-08-28 finding and stated to the operator twice) | **Go-live floor 2026-06-22.** Right counts, wrong cause — and it invited a remediation that recovers nothing | `perf-data` (`.env` + MIN/MAX query) |
| 2 | Other surfaces are **"structurally immune"** to collapse | Immunity is **window-and-data dependent**, never structural | team-lead challenge |
| 3 | The counter-challenge: the calendar seam **collapses too, so it buys only disclosure** | Over-broadened. Non-degenerate *inside* the span (60d genuinely subsets), degenerate beyond | `perf-trace` + `perf-data` boundary counts |
| 4 | Seam port framed as a **bug fix** | **Semantic change** (trading→calendar redefinition) | team-lead self-correction |
| 5 | **"No computation bug found"** (this document, v1) | True for `/api/performance` **only** — F3 and F7 are live on the dashboard | `perf-doc` / `perf-trace` |
| 6 | **"No per-day Composer series exists anywhere"** | False — based on `symphony-stats-meta` alone; S-1/S-2 serve daily data to 2024-07-12 | `perf-data` live probe |
| 7 | F7 remedy = **re-base the period** | Insufficient — also a metric-definition **and** units defect | `perf-data` force-injection (E-23) |
| 8 | F7 partial-fix artifact = **12.86pt / 72.8%** | **Retracted** — mixed normalized against un-normalized units | team-lead, confirmed by `perf-data` |
| 9 | F5 filed as **"documented-deliberate, not a bug"** (this document, v1/v2) | **Elevated to its own finding** — individually-documented ≠ deliberate joint decision | team-lead ruling |

**On the adjacent-evidence trap specifically:** the prior "~49-day retention" observation was treated as a hypothesis, not a cause, and that discipline paid off twice — the count was approximately right, the *cause* was wrong (reversal 1), and the mechanism established here holds independently of the number anyway. **Causes genuinely stack**: truncated data, silent collapse, a false window label, *and* an invalid winner-badged comparison. The audit did not stop at the first sufficient explanation — and the finding it would have missed by doing so (F7) is the most severe one it found.

---

## 10. Open items

| ID | Item | Status |
|----|------|--------|
| U-1 | Retention vs genesis | ✅ **CLOSED** — genesis (E-20) |
| U-2 | Longer live series | ✅ **CLOSED** — exists, probed to 2024-07-12 (E-24) |
| U-3 | Numeric confirmation of window collapse | ✅ **CLOSED** — E-17 promoted to FACT |
| U-5 | **`dry_run` consumer enumeration** — blocking pre-condition on the F7 fix | ❌ **OPEN — assign before the fix cycle** |
| U-6 | Units convention (`dry_run` un-normalized pp vs Composer normalized fraction) | ⚠️ **Flagged, non-blocking.** The two matched figures (10.5875 / 10.3622) share a convention, so the 0.23pt gap is internally consistent regardless. Resolve **in the fix plan**, not the diagnosis |
| U-7 | Composer rate limits — two Tier-1 sources conflict (25/min reference vs 1/sec help-centre) | ⚠️ Unverified; let 429-handling be the authority |
| U-8 | `tests/analytics/test_live_m1_helpers.py:66` hits an undocumented `/api/v2/…` path with bearer-only auth | Separate ticket, out of scope |
| U-9 | **Minor unreconciled discrepancy:** the 53-day aggregate MDD_bot reads `-0.051671` via the positional path and `-0.051661` via the calendar path — same days, ~1e-5 apart | Trivial; recorded rather than smoothed. Likely an aggregation-path difference worth a glance during the fix |

---

## 11. Bottom line

1. **⭐ Fix F7 first — all three defects, not one.** 98.7% of a winner-badged number on the operator's main dashboard is artifact. It has been unqualified for ~a month since the depth guard silently expired. **Enumerate `dry_run`'s consumers before redefining it.**
2. **Ship the display-honesty fix.** Cheap, low-risk, and the literal answer to what he asked for. `available_days` is already computed — render it.
3. **F3: fix or delete — but never before F7.** Threading the window without re-basing launders the error.
4. **The semantic port is a product decision, not a fix.** It will change his 60d number. He is entitled to be told that, not to have it smuggled in.
5. **❌ Do not touch retention.** Genesis, not pruning. It recovers nothing.
6. **✅ Longer live history is real and reachable** (2024-07-12, existing credentials) — a feature cycle, with the `/backtest` trap called out loudly.
7. **The honest bottom line on the strategy: over 53 measurable days, Guard Alpha shows a 0.23pp drawdown edge — effectively neutral.** Short window, noisy metric. State it; don't spin it either way.
