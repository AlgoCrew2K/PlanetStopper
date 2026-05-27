# AlphaBot v3 — Disciplined Trailing-Stop Risk Engine for Composer.trade

> A risk engine for retail Composer.trade operators. Composer holds a basket of "symphonies" (rule-based ETF rotation strategies) through the day; AlphaBot watches each one minute-by-minute and exits to cash when the math says the day's gain is at risk. The math combines four ways of catching a turn — a volatility-scaled trailing stop, a VWAP breakdown defender, a VWAP bleed-cut for slow drifts, and a take-profit trigger on exceptional moves — each gated by a Monte Carlo "is today actually bad?" sanity check against 125 days of regime-matched history. Behind the scenes the autotuner uses 500 walk-forward trials per symphony with risk-aversion-shaped utility (CRRA) and a Harvey-Liu / Benjamini-Hochberg overfitting haircut that rejects parameter sets it can't statistically distinguish from noise. Three AI Advisors flag overfitting risk, spec-bundle integrity, and (when enabled) divergence between two CVaR windows. The operator gets institutional-grade exit discipline without writing any of it themselves.

---

## Table of contents

1. [Should I run this?](#1-should-i-run-this)
2. [How AlphaBot operates — the operator's view](#2-how-alphabot-operates--the-operators-view)
3. [The math, explained simply](#3-the-math-explained-simply)
   - [3.1 Trailing stops with regime awareness](#31-trailing-stops-with-regime-awareness)
   - [3.2 CVaR vs VaR — measuring tail risk](#32-cvar-vs-var--measuring-tail-risk)
   - [3.3 Monte Carlo — when do we have enough data to act?](#33-monte-carlo--when-do-we-have-enough-data-to-act)
   - [3.4 CRRA-EU — picking parameters that protect against catastrophes](#34-crra-eu--picking-parameters-that-protect-against-catastrophes)
   - [3.5 Walk-forward + BHY haircut — not getting fooled by overfitting](#35-walk-forward--bhy-haircut--not-getting-fooled-by-overfitting)
   - [3.6 NN1 spec-freeze — fingerprinting our backtests for honesty](#36-nn1-spec-freeze--fingerprinting-our-backtests-for-honesty)
   - [3.7 Volatility scaling, time squeeze, parabolic ratchet — practitioner heuristics with provenance gaps](#37-volatility-scaling-time-squeeze-parabolic-ratchet--practitioner-heuristics-with-provenance-gaps)
4. [Why the bot makes the choices it makes](#4-why-the-bot-makes-the-choices-it-makes)
   - [4.1 Symphony-level only, not portfolio-level](#41-symphony-level-only-not-portfolio-level)
   - [4.2 Four exit triggers fed by independent risk signals](#42-four-exit-triggers-fed-by-independent-risk-signals)
   - [4.3 The dashboard is observability, not action](#43-the-dashboard-is-observability-not-action)
   - [4.4 Diagnostic-only CVaR — and why we rejected CVaR-divergence detectors](#44-diagnostic-only-cvar--and-why-we-rejected-cvar-divergence-detectors)
   - [4.5 NN1 spec-freeze — fingerprinting our backtests](#45-nn1-spec-freeze--fingerprinting-our-backtests)
   - [4.6 Three Advisors, not one — different lenses for different operator decisions](#46-three-advisors-not-one--different-lenses-for-different-operator-decisions)
   - [4.7 Fail-safe floor — the trailing stop fires even when upstream signals are silent](#47-fail-safe-floor--the-trailing-stop-fires-even-when-upstream-signals-are-silent)
5. [Per-symphony walkthrough — one tick from :00 to decision](#5-per-symphony-walkthrough--one-tick-from-00-to-decision)
6. [The Autotuner — how parameters are chosen](#6-the-autotuner--how-parameters-are-chosen)
7. [AI Advisor — the three producers](#7-ai-advisor--the-three-producers)
8. [What the bot does NOT do](#8-what-the-bot-does-not-do)
9. [Setup + Operation](#9-setup--operation)
10. [Architecture (for the technically curious)](#10-architecture-for-the-technically-curious)
11. [Audit trail + verification](#11-audit-trail--verification)
12. [Open questions + known limits](#12-open-questions--known-limits)

---

## 1. Should I run this?

This section is a frank decision-helper, not a sales pitch. Read it before running anything.

### Who this is for

- You operate one or more **Composer.trade** symphonies. (A "symphony" is Composer's name for a rule-based ETF rotation strategy you've authored or licensed.)
- You hold each symphony through the day and accept that Composer itself does not actively manage intraday drawdowns. You'd like a tool that watches the symphony minute-by-minute and liquidates to cash when the math says the day's gain is at risk.
- You're comfortable running a Python daemon on a machine you control (a home server, a small VPS, a workstation that's on during US market hours).
- You can hold an **Alpaca** data subscription so the bot can pull 1-minute prices for the underlying ETFs.
- You're prepared to read a dashboard and intervene if needed. AlphaBot is an exit-discipline overlay, not a hands-off product.

### Who this is NOT for

- You want a bot that **enters** positions, picks symbols, or chooses what to hold. AlphaBot does none of those things. Position entry and sizing are entirely Composer's responsibility; AlphaBot only decides when to exit.
- You want a high-frequency intraday trader. The cadence is one decision per minute per symphony — appropriate for end-of-day equity ETFs, not for futures or crypto.
- You want a single signed "buy/sell with confidence X" number from a forecasting model. AlphaBot deliberately surfaces multiple independent signals and lets the operator (and the priority resolver) reconcile them. There is no master forecast.
- You're not prepared to read a dashboard or investigate alerts. The bot will fire exits on its own once configured, but it expects an attentive operator on the other side of the Discord webhook.
- You expect academic-grade certainty from every layer. A few of the heuristics (log-time-squeeze curve, VWAP gate thresholds) are practitioner-grade and have provenance work scheduled but not shipped — see §[3.7](#37-volatility-scaling-time-squeeze-parabolic-ratchet--practitioner-heuristics-with-provenance-gaps) and §[12](#12-open-questions--known-limits).

### Cost in attention and dollars

- **Compute.** A daemon process and a small SQLite database. Negligible CPU on a modern laptop. A few hundred MB of RAM during autotune cycles; less than 100 MB during live operation.
- **Data.** An Alpaca subscription that includes 1-minute bars for US equity ETFs. The free tier may be sufficient for a small symphony count; check current Alpaca limits.
- **API spend.** Composer's own liquidation endpoint is rate-limited but not metered for individual operators. Discord webhooks are free.
- **Attention.** The biggest cost. You should expect to look at the dashboard once or twice during market hours and read the daily Discord post-mortem at 16:00 ET. A symphony armed by the bot but not exited will continue to trade through Composer; you should know what your symphonies do.

### How to honestly evaluate fit

Run the daemon in `LIVE_EXECUTION=False` (paper mode) for at least two weeks against your live symphonies. The bot will write its exit decisions to the database and post Discord alerts as if it were live, but will not call Composer's liquidation endpoint. Compare the "would-have-exited" decisions against Composer's actual EOD outcomes. If the bot's decisions feel right to you and add value relative to buy-and-hold, flip the switch.

---

## 2. How AlphaBot operates — the operator's view

This section describes a day with AlphaBot running, from the operator's perspective. The technical details are in §[10 Architecture](#10-architecture-for-the-technically-curious).

### The :00 tick

A single Python process (`app.py`) runs continuously. Every minute, at the top of the second (`:00`), it spawns a subprocess that executes `alpha_bot_execution.py` ([`app.py:208-219`](app.py)). That subprocess does one pass: for each active symphony, in each linked Composer account, it pulls current holdings, computes the day's return, refreshes a high-water mark, runs a Monte Carlo regime-locality test, walks six math layers, and resolves a single exit decision. The subprocess writes its decisions back to the SQLite state DB and exits. The Flask process is unaffected by anything the subprocess does — if the engine crashes, the dashboard keeps running and the next minute's tick is fresh.

The subprocess-per-tick design is deliberate. It isolates engine crashes from the dashboard, makes the engine's runtime ceiling visible (you cannot spend more than 60 seconds on one cycle without skipping the next), and makes the bot's behavior auditable: every tick is one process and one set of database writes.

### The dashboard

A Flask web UI ([`app.py:2502-2508`](app.py)) presented at `http://localhost:8080`. It shows, per symphony:

- Current return vs entry
- Distance to the active trailing stop
- Status rank ("idle", "armed", "exiting")
- Monte Carlo probability that today beats SPY (with a regime-match indicator)
- VWAP and VWAP-bleed thresholds
- The CVaR diagnostic panel — see §[3.2](#32-cvar-vs-var--measuring-tail-risk) and §[4.4](#44-diagnostic-only-cvar--and-why-we-rejected-cvar-divergence-detectors) for what's live vs what's staged
- A read-only feed of recent decision events ("armed at 10:14 ET because MC prob crossed 15%")

There are also tabs for autotuner history, AI advisor observations, performance comparisons, and settings. **Everything except the settings panel is read-only.** The dashboard never executes a trade and cannot spawn the engine. See §[4.3](#43-the-dashboard-is-observability-not-action).

### Exit decisions

When any of the four exit triggers fires, the bot:

1. Picks the canonical winner via [`resolve_trigger_priority`](math_engine.py) (`math_engine.py:736-759`). Order: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop.
2. Records the winner AND every co-fired trigger as telemetry (so a reviewer can see when multiple signals aligned).
3. If `LIVE_EXECUTION=True`, fires a POST to Composer's liquidation endpoint with exponential backoff (1s, 2s, 4s, 10s) for resilience against HTTP 429.
4. Posts a Discord webhook with the exit reason, Guard Alpha metrics (how much was saved vs holding to close), VWAP statistics, and a QuickChart summary.

### The autotuner (after market close)

At 15:53 ET, the EOD post-mortem stage locks the day's shadow return using live Alpaca prices. At 16:00 ET, the autotuner reconciles tomorrow's target holdings. Overnight, for each symphony, it runs 500 Optuna trials over 125 days of history (60% train / 20% validation / 20% frozen-eval) and selects the parameter set with the highest CRRA-EU utility t-statistic — subject to a Benjamini-Hochberg-Yekutieli (BHY) haircut that rejects parameter sets it can't statistically distinguish from luck. See §[6](#6-the-autotuner--how-parameters-are-chosen).

If the haircut rejects all 500 trials, the autotuner refuses to deploy and keeps yesterday's parameters. The operator sees this on the dashboard.

### The AI Advisors

Post-autotune, three independent advisor producers write observations into the database:

- **Overfitting Conscience** — flags when the haircut is doing heavy lifting or when researcher degrees-of-freedom are drifting up.
- **Spec Critic** — checks that the parameters protected from autotune (gamma, utility family, wealth argument) are still frozen for honest reasons.
- **Divergence Explainer** — writes per-cycle explanations when the second-window CVaR feature is enabled (off by default — see §[7](#7-ai-advisor--the-three-producers)).

The operator reads these on the `/ai-advisor` tab. They never act on the operator's behalf.

---

## 3. The math, explained simply

This section walks each math surface AlphaBot uses, with a plain-English explanation first and a formula afterward. Each surface cites a published reference, the exact code location, and a soundness verdict from the math review at [`docs/audit/vision-audit-2026-05-27/math-soundness.md`](docs/audit/vision-audit-2026-05-27/math-soundness.md).

A note on jargon. Several terms are defined inline on first use:
- **γ (gamma)** — the risk-aversion parameter in CRRA utility. Higher γ means the operator is more averse to large losses.
- **t-stat (t-statistic)** — a number that says "is this signal too big to be luck?" Higher t-stat = more statistically distinguishable from noise.
- **CVaR (Conditional Value-at-Risk)** — the average loss in the worst-case slice of outcomes. Same idea as Expected Shortfall.
- **BHY (Benjamini-Hochberg-Yekutieli)** — a statistical correction for the problem of running many tests and picking the best one.
- **Walk-forward** — splitting history into train/test/holdout so you score parameters on data you didn't train on.
- **Log-utility / CRRA log limit** — the special case of CRRA where γ=1 reduces to ln(W).

A note on confidence. The math review at [`docs/audit/vision-audit-2026-05-27/math-soundness.md`](docs/audit/vision-audit-2026-05-27/math-soundness.md) distinguishes "the math is sound" from "the calibration window is statistically thin." A surface can be technically correct but operate on a small data sample, and that combination requires the operator to interpret outputs accordingly. Where this applies, the subsection flags it.

### 3.1 Trailing stops with regime awareness

**Plain English.** A trailing stop is a stop-loss that moves up as the price rises but never moves down. When the price falls back to the stop, the bot exits to cash. AlphaBot's trailing stop is **volatility-scaled**: in a quiet symphony the stop is tighter; in a noisy symphony the stop is wider, so background noise doesn't trip an exit. The default volatility window is 20 trading days — the institutional standard, anchored by the RiskMetrics technical document.

```text
stop_distance = base_stop × symphony_vol_20d × dynamic_multiplier
                                                  ↑
                                       (shrinks through the day; see §3.7)
```

The active stop is computed by `compute_active_trailing_stop` at [`math_engine.py:245-289`](math_engine.py). The 20-day window is at [`math_engine.py:62`](math_engine.py). Reference: Andersen & Bollerslev (1997) and the RiskMetrics Technical Document (1996).

**Soundness verdict.** Mainstream and sound; the 20-day window is textbook standard. The two ratchets stacked on top (log-time squeeze and parabolic) are practitioner heuristics with provenance work scheduled but not shipped — see §[3.7](#37-volatility-scaling-time-squeeze-parabolic-ratchet--practitioner-heuristics-with-provenance-gaps).

### 3.2 CVaR vs VaR — measuring tail risk

**Plain English.** VaR (Value-at-Risk) at 5% answers: "How bad is the worst-case 1-in-20 day, roughly?" CVaR at 5% answers a strictly more useful question: "When that 1-in-20 day actually happens, how bad is it **on average**?" CVaR is the average loss in the worst 5% of outcomes. It's a more honest tail-risk number because it doesn't get confused when the loss distribution has a fat tail — VaR can flatline at the 5th percentile and miss a much worse 1st-percentile catastrophe; CVaR averages across them all.

AlphaBot's CVaR is computed from the **150 historically-most-similar days** to today (a k-Nearest-Neighbors regime match on SPY return + rolling vol), not from the unconditional history. This gives a regime-aware tail estimate.

```text
CVaR_5%  ≈  mean( worst 5% of returns in the 150 nearest-neighbor days )
```

The estimator is the general-distribution form (Rockafellar-Uryasev 2002), which works correctly on discrete pools with possible ties — the right pick for a 150-day empirical sample. The implementation is `compute_portfolio_cvar` at [`math_engine.py:1185-1345`](math_engine.py), and the typed result is `CVaRAssessment` at [`math_engine.py:120-152`](math_engine.py). The fail-safe invariant (`cvar_pct=None → breach=False`) is enforced in `__post_init__` ([`math_engine.py:139-146`](math_engine.py)) so an absent estimate can never *cause* a trigger.

References: Rockafellar & Uryasev (2000, 2002); Acerbi & Tasche (2002) — the latter establishes CVaR as a *coherent* risk measure where VaR is not.

**Soundness verdict.** The math is sound and well-grounded. The per-cycle live path calls `compute_portfolio_cvar` for each managed symphony and persists the result to `cvar_diagnostic` via `database.record_cvar_diagnostic`. CVaR is **never** a live trigger — it is operator instrumentation only. See §[4.4](#44-diagnostic-only-cvar--and-why-we-rejected-cvar-divergence-detectors) for the philosophical decision behind keeping CVaR diagnostic-only.

**Statistical thinness caveat.** Even when wired live, a 150-neighbor pool at α=5% yields only about 8 distinct tail observations. The CVaR point estimate has a wide error bar; the dashboard surfaces this as a `tail_obs_count` field alongside the value. Treat the value as a discussion prompt, not a forecast.

### 3.3 Monte Carlo — when do we have enough data to act?

**Plain English.** Before the bot fires a trailing-stop exit, it pauses and asks: "In the 150 most regime-similar past days, how often would we have ended above where we are right now?" If the answer is above 60% (the `TRIGGER_THRESHOLD_PCT` default — operator-configurable), the bot **blocks** the exit, on the rationale that the regime typically recovers from here and the operator should not capitulate at a noisy local low. This is not a forecast — it's a delay. If price keeps falling, the next minute's tick re-checks.

The Monte Carlo runs 5,000 bootstrap paths over the 150 regime-similar days. The seed is derived from the cycle ID via SHA-256 so two daemon restarts that happen at the same `:00` produce identical MC results (auditability).

```text
prob_beating(today) = mean over 5000 bootstrap paths from 150 kNN-matched days
                      of [ end-of-day return > current return ]
```

The implementation is `run_monte_carlo` at [`math_engine.py:772-900`](math_engine.py). The eligible-pool boundary requires at least 39 raw days of history before MC will return a value ([`math_engine.py:798-811`](math_engine.py)) — 20 days for the kNN match plus 19 days of rolling-vol warmup. If a symphony has less than 39 days, MC returns `None` (the `MC_INSUFFICIENT_HISTORY_SENTINEL`) and the protective stop fires on ticks-below-stop alone — the fail-safe floor described in §[4.7](#47-fail-safe-floor--the-trailing-stop-fires-even-when-upstream-signals-are-silent).

References: Glasserman (2003) *Monte Carlo Methods in Financial Engineering*; Efron (1979) for the bootstrap; Kaminski & Lo (2014) for the regime caveat — stops add value under momentum and subtract under random walks, and the MC veto's behavior depends on which regime is in force.

**Soundness verdict.** Each component (empirical bootstrap, kNN regime-matching, MC) is individually well-established. The **combination as an exit veto is unconventional** — there is no peer-reviewed precedent. The known soft spot: when the current regime is a true break, the 150 nearest neighbors are all "least bad fits" and the gate is least informative when most needed. There is no regime-match-quality guard today — the math review recommends adding one. See §[12](#12-open-questions--known-limits) OQ-3.

### 3.4 CRRA-EU — picking parameters that protect against catastrophes

**Plain English.** When the autotuner picks parameters, it doesn't just look for the trial with the highest average return — that would ignore risk. It uses **CRRA utility**, which is a textbook formula for an investor who hates losses more than they love equally-sized gains. Concretely: each daily return gets converted into a "utility score" with diminishing marginal benefit. A +2% day is worth more than zero, but a -2% day is worth MORE than -2% worth of bad. The autotuner picks the parameter set whose mean utility is highest — the configuration that produces the best risk-adjusted experience, not the highest raw return. The shape is set by **γ (gamma)**, where higher γ means more loss-averse; AlphaBot's default lives near γ=2, a moderately risk-averse retail investor.

```text
For each daily return r_i:
    W_i = max(0.001, 1 + r_i)                        # wealth-argument floor on INPUT only
    U_i = (W_i^(1-γ) - 1) / (1-γ)                    # CRRA utility (γ ≠ 1)
        = ln(W_i)                                    # log-utility limit (γ = 1)

t-stat = mean(U) / ( sd(U, ddof=1) / sqrt(T) )       # one-sample t for a mean-valued objective
```

The implementations are `compute_crra_utility` and `compute_crra_eu_objective` at [`math_engine.py:1348-1404`](math_engine.py); the t-stat is `compute_crra_eu_tstat` at [`autotuner.py:367-400`](autotuner.py). The wealth-argument floor of 0.001 is applied to **input W** only, never to output U — flooring U would inflate the t-stat anti-conservatively (the H-6 category-error precedent the docstring names explicitly).

Why CRRA over Sharpe ratio? Sharpe is symmetric — it treats a +2σ outcome and a -2σ outcome as equally good once squared. CRRA does not: it penalizes large losses more than equally-sized gains because `(1+r)^(1-γ)` is concave for γ > 0. For a risk overlay whose explicit job is "make sure a loss doesn't blow up the account," concave utility is the correct shape.

References: Pratt (1964) *Econometrica* (introduces CRRA); Merton (1969) and Samuelson (1969) *Review of Economics & Statistics* (the log-utility limit via L'Hôpital).

**Soundness verdict.** Strong. CRRA is the textbook formalization of risk aversion and serves AlphaBot's "capital preservation" mandate directly. The math review notes one open product decision: should γ be documented as `γ=2` in user-facing copy, or surfaced as a configurable parameter? Currently γ lives in the Optuna search-space lower bound + the spec-bundle THEORY-frozen facet — see §[12](#12-open-questions--known-limits) OQ-11.

### 3.5 Walk-forward + BHY haircut — not getting fooled by overfitting

**Plain English.** Run 500 random parameter sets and the BEST of them is, on average, much better than it deserves to be — by luck alone. This is the *multiple-testing problem* and it is the central failure mode of every "I backtested 500 strategies and picked the winner" trading research process. AlphaBot corrects for it using the **BHY haircut**, named after Benjamini, Hochberg, and Yekutieli (2001). After 500 trials, the bar that any candidate must clear is RAISED in proportion to how many trials were run. If the raw winning trial doesn't clear the raised bar, the autotuner refuses to deploy and keeps the previous parameters.

There's also a tripwire — **`N_effective`**. If a researcher manually tried more variants offline before submitting to the autotuner, those count toward the bar too. So you can't game the test by pre-filtering parameters by hand.

```text
N_effective = N_optuna + S                 # S = researcher-degree-of-freedom count
                                            # (rows with evidence_source='BACKTEST_SELECTION')

c(N) = sum from j=1 to N of (1/j)           # Yekutieli factor for dependent tests

p_adj = ... BHY step-up procedure ...       # adjusted p-value vs threshold q=0.05
```

The implementation: `compute_haircut_pvalue` and `benjamini_hochberg_adjust` at [`autotuner.py:424-476`](autotuner.py); `compute_n_effective` at [`autotuner.py:489-539`](autotuner.py). The **additive** structure (`N_optuna + S`, not `N_optuna × S`) is the council's defensibility choice: the additive form is byte-identical to today's haircut in the NN1-honest case (S=0), so the migration is byte-identical until a researcher actually adds a backtest-selected facet, making BHY-honesty an opt-in cost rather than a baseline tax.

Walk-forward methodology: 125 trading days split 60% train / 20% validation / 20% frozen-eval, with 20 days of "purge" and 1 day of "embargo" at each fold boundary so the rolling-vol window from the train side can't leak into the test side. The frozen-eval window is consumed exactly once per autotune cycle — after best-trial selection — as the honest post-selection metric. ([`autotuner.py:1283-1311`](autotuner.py))

References: Benjamini, Hochberg & Yekutieli (2001) *Annals of Statistics*; Harvey & Liu (2015) *Journal of Portfolio Management* (BHY for trading backtests); Bailey, Borwein, López de Prado & Zhu (2014) *Journal of Computational Finance* (Probability of Backtest Overfitting); López de Prado (2018) *Advances in Financial Machine Learning* Ch. 7 (purge + embargo).

**Soundness verdict.** This is the **strongest single piece of math** in the engine. It is the operator-trust mechanism. The 125-day window is short by published walk-forward standards (Pardo 2008 recommends 5-10 rolling folds; AlphaBot uses 1) — after purge=20 at both fold boundaries, the validation and frozen-eval windows shrink to ~4-5 usable days each, giving the frozen-eval t-stat a wide error bar. The window size is acknowledged in code at [`autotuner.py:1301-1307`](autotuner.py). See §[12](#12-open-questions--known-limits) for the open product decision on extending it. The math is sound; the calibration window is statistically thin — both can be true simultaneously.

### 3.6 NN1 spec-freeze — fingerprinting our backtests for honesty

**Plain English.** Each parameter in the bot is "frozen" for a reason — and that reason is recorded as a `freeze_discipline` enum value. There are **six allowed reasons** (THEORY, MANDATE, STYLIZED_FACT, POLITIS_WHITE, CADENCE, CALIBRATION) and **one banned reason** (`BACKTEST_SELECTION`). The bot's autotuner refuses to deploy a parameter set if any constant was frozen for the banned reason. This prevents the most common form of self-deception in trading research: cherry-picking parameters because the historical P&L looked good with them. NN1 is the **structural guarantee** that AlphaBot's parameters have a non-circular justification.

```text
NN1_HONEST_DISCIPLINES = frozenset({
    'THEORY',           # derived from a published model (e.g. CRRA)
    'MANDATE',          # operator/regulatory constraint (e.g. cash-on-EOD)
    'STYLIZED_FACT',    # replicated empirical regularity (e.g. intraday vol U-shape)
    'POLITIS_WHITE',    # Politis & White (2004) bootstrap block-length selection
    'CADENCE',          # discrete operational choice (1-minute, EOD)
    'CALIBRATION',      # fitted to historical without optimization target
})
# BACKTEST_SELECTION is the one discipline that creates an NN1 violation.
```

If a researcher tries to slip a P&L-frozen constant into a spec bundle, the BHY haircut catches it: the row inflates **S** (the researcher-DoF counter), which inflates `N_effective`, which raises the BHY bar. If the researcher hides the row, the spec-bundle hash mismatches and the autotuner refuses to run at module-load time ([`autotuner.py:1180-1186`](autotuner.py)).

The enumeration is at [`autotuner.py:73-90`](autotuner.py). The compliance validator that runs at the autotuner entry is at [`autotuner.py:1189-1279`](autotuner.py), with default-deny on unknown discipline strings.

References: López de Prado (2018) *Advances in Financial Machine Learning* Ch. 11 ("Backtest Overfitting"); Bailey et al. (2014) PBO paper; Politis & White (2004) for the bootstrap block-length discipline.

**Soundness verdict.** Strong. This is the structural guarantee that backs the BHY haircut. NN1 cannot rescue a curve shape that was never well-anchored to begin with (see §[3.7](#37-volatility-scaling-time-squeeze-parabolic-ratchet--practitioner-heuristics-with-provenance-gaps)) — it can only enforce that *parameters of an existing shape* are frozen honestly.

### 3.7 Volatility scaling, time squeeze, parabolic ratchet — practitioner heuristics with provenance gaps

**Plain English.** The bot scales its trailing-stop distance by recent volatility — a 20-day rolling estimate, textbook standard. Two extra adjustments sit on top:

1. **Log time squeeze.** The stop *tightens* through the trading day on a logarithmic curve — wider at the open, tighter at the close — on the rationale that less time remains to recover from a drawdown. Curve: `1.5x` at the open decaying to `0.5x` by the close.
2. **Parabolic ratchet (PARA-ARM).** If a price moves quickly (the "parabolic squeeze"), the stop *tightens further* to lock in the move. Named after Wilder's Parabolic SAR but mathematically a 1-cycle rate-of-change indicator.

```text
dynamic_multiplier(t) = 1.5 - (1.5 - 0.5) × log10(1 + 9 × time_ratio) / log10(10)
                                                ↑
                                    practitioner shape — no published anchor

velocity = current_return - prev_return
should_para_arm = (velocity ≥ PARABOLIC_VELOCITY_THRESHOLD) and not currently_armed
```

The vol-scaling and 20-day window are anchored by Andersen & Bollerslev (1997) and RiskMetrics (1996) — mainstream. The 14-day ATR underneath uses Wilder (1978) — also mainstream. But the **specific shape of the log-time-squeeze curve** and the **PARA-ARM cross-day reset behavior** (where `prev_return=0` at the start of each new day means any symphony opening above 2% auto-arms PARA on the first cycle) have no published precedent. They are practitioner heuristics with coherent rationales but no formal anchor.

The relevant code: [`math_engine.py:155-167`](math_engine.py) (time-squeeze constants), [`math_engine.py:211-242`](math_engine.py) (`compute_time_squeeze_decay`), [`math_engine.py:185-208`](math_engine.py) (`compute_para_arm_decision`), [`math_engine.py:903-960`](math_engine.py) (20-day vol).

References: Andersen & Bollerslev (1997) *Journal of Empirical Finance*; J.P. Morgan / Reuters (1996) *RiskMetrics Technical Document*; Wilder (1978) *New Concepts in Technical Trading Systems*; Kestner (2003) *Quantitative Trading Strategies* (ATR-based stops backtest across 15 futures markets — single-author, not peer-reviewed).

**Soundness verdict.** **Mixed.** Vol-scaling (the foundation) is solid. The two ratchets on top are practitioner-grade. The decision-science Phase-1.5 M3 work tracks the re-derivation of these curves; it has not shipped on the current branch. The dashboard surfaces these stops as live signals today; the operator should know they live or die by empirical evaluation that the 125-day calibration window cannot deliver with high confidence. See §[12](#12-open-questions--known-limits) OQ-5, OQ-6.

---

## 4. Why the bot makes the choices it makes

This section answers the *why* behind the major design choices. Each subsection covers an architectural decision, the alternative that was considered, and the rationale for choosing the current path. The goal is to surface the **philosophy** of the bot, not just the implementation.

### 4.1 Symphony-level only, not portfolio-level

**The choice.** Every decision AlphaBot makes is keyed to a single Composer symphony, not to a portfolio aggregate. If you have three symphonies in one Composer account, AlphaBot makes three independent exit decisions per minute — never a fourth "portfolio-level" decision.

**The alternative considered.** A "port-level" decision math layer that aggregated symphony state into a portfolio view and made one exit-or-hold call across the whole account. This existed earlier in the project's life.

**The rationale.** The user mandated symphony-level-only after Sprint 2's audit revealed the port-level math had no replay-validation track and was producing decisions the team could not defend with the same rigor as the per-symphony layer. The deleted modules (`engine/multi_cycle.py`, `engine/port_selector.py`, `engine/port_aggregator.py`, `engine/dual_altitude.py`) are listed in `DECISIONS.md §DE-S3-004`.

**What this means for the operator.** If you want a portfolio-level view, you build it yourself — the dashboard displays per-symphony state and aggregate NAV but never makes autonomous portfolio-level decisions. The `port_state` table still exists (additive-first migration discipline preserves the schema) and the dashboard reads it for display ("show me where each symphony stands"), but no engine code consumes it for a dispatch decision. The display badge in `engine/exit_authority.py` is retained as **display-only**.

### 4.2 Four exit triggers fed by independent risk signals

**The choice.** AlphaBot resolves every exit through `resolve_trigger_priority` ([`math_engine.py:736-759`](math_engine.py)) using exactly **four canonical exit triggers**: VWAP Breakdown, Take-Profit, VWAP Bleed Cut, and Trailing Stop. The resolver picks the canonical winner via a fixed priority order (`VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop`) and reports every co-fired trigger as telemetry alongside the winner.

**The alternative considered.** A single "master signal" produced by combining all the underlying math into one number — for example, a logistic regression over the four flags, or a learned classifier. AlphaBot explicitly does not do this.

**The rationale — democratizing decision-making.** Four independent signals catch different failure modes. VWAP Breakdown catches a sharp liquidity event; Take-Profit captures an exceptional upside that the regime would not normally sustain; VWAP Bleed Cut catches a slow erosion that a sharp-cross detector would miss; Trailing Stop catches everything else. By reporting **all** triggers that co-fired (not just the winner), the operator can distinguish a high-conviction "all four fired at once" exit from a single-signal noise spike. A single master signal would discard this information.

**A clarifying note on "six layers vs four triggers".** Earlier project documentation refers to a "6-layer exit decision." The literal architectural truth: there are **six upstream math layers** (vol-scaling, log-time-squeeze, parabolic ratchet, breakeven, VWAP×2, MC) that feed into **four exit triggers** asymmetrically — four of the six math layers collapse into the single Trailing-Stop flag, the two VWAP layers split across two flags (Breakdown and Bleed Cut), and the MC layer is an input gate to the Trailing-Stop and Take-Profit flags rather than a standalone trigger. Read carefully: **four triggers, six feeding computations.** See §[5](#5-per-symphony-walkthrough--one-tick-from-00-to-decision) for the full path.

**Why this priority order specifically?** The defensible argument is *fastest hard-cut first, slowest momentum-respecting cut last.* VWAP Breakdown is the fastest hard-cut and a regime-shift signal; Take-Profit is an upside-only cut that, when it fires, means the regime has already turned; VWAP Bleed Cut is a slower erosion cut; Trailing Stop is the slowest momentum-respecting cut and the catch-all floor. The specific relative position of Take-Profit ahead of VWAP Bleed Cut has no published first-principles argument and the in-code comment cites only a historical H2 acceptance-criteria reference — see §[12](#12-open-questions--known-limits) OQ-1.

### 4.3 The dashboard is observability, not action

**The choice.** The Flask dashboard at `http://localhost:8080` is **read-only for live trades**. It has no button that places, cancels, or modifies a trade. It cannot spawn the engine. The only write path through the dashboard is the `/api/settings` endpoint, which modifies operator-config rows (NOT positions or trades).

**The alternative considered.** A dashboard with "force trigger this symphony now" or "execute manual liquidation" buttons that the operator could use during market hours.

**The rationale — three layers of enforcement.**

1. **Architecture constraint** (project CLAUDE.md): *"Dashboard is a read-only operator surface — never an action surface for live trades."*
2. **Driver-level enforcement.** SQLite is opened in read-only mode for all dashboard accessors ([`database.py:77`](database.py) and [`database.py:889`](database.py)). A Flask request thread literally cannot execute a write transaction against the state DB.
3. **Code-archaeological enforcement.** The `/api/trigger` POST handler ([`app.py:1550-1554`](app.py)) returns *"Manual trigger disabled — use the scheduler"* with explicit operator-visible feedback. The scheduler is the only legal engine spawner.

**The one operator-action path that remains.** A manual `perform_account_liquidation` endpoint exists ([`app.py:1820`](app.py)) — the operator must explicitly click it to liquidate an entire account to cash. This is the "panic button" surface and is documented as `KEEP-MANUAL` in the Sprint 3 port-removal manifest. The engine never autonomously fires it.

**What this protects against.** A bug in the dashboard rendering code cannot cause a live trade. A misclicked button cannot cause a live trade. The dashboard can be exposed on a LAN without exposing trade-execution authority.

### 4.4 Diagnostic-only CVaR — and why we rejected CVaR-divergence detectors

**The choice.** AlphaBot computes CVaR (Conditional Value-at-Risk, see §[3.2](#32-cvar-vs-var--measuring-tail-risk)) as an **operator diagnostic** that surfaces on the dashboard alongside live exit signals. CVaR is **never** a live trigger. The operator sees CVaR and can decide independently to pause new positions, reduce size, or close on intuition — but the bot does not act on CVaR autonomously.

**The alternative considered.** Two stronger versions: (a) a CVaR-driven exit trigger ("if CVaR_5% < -3%, force exit"); (b) a CVaR-**divergence detector** that compared the standard kNN CVaR window against a second regime-shifted window and surfaced a signed divergence number an operator could trade on.

**The rationale for diagnostic-only.** A 125-day history with a 150-neighbor kNN pool produces a CVaR estimate with **small effective tail sample size** (~8 distinct tail observations against ~150 neighbors at α=0.05). The standard error of the CVaR estimate is large. Operators should see CVaR alongside live exit decisions but should not be silently exposed to a CVaR-driven exit before the estimator's sampling variance is understood. Phase 1 ships CVaR as **operator instrumentation**, not as a live decision input.

**Why we rejected the divergence-detector idea.** The detector would seem to escape the "wide error bars on CVaR" problem by comparing two CVaR windows instead of trusting one — but the project's validation analysis concluded that "validate a detector not an estimate" only **relocates** the data wall. The detector's validation requires an **independent regime-shift count** of roughly 5-15 events in the available history, and those regime-shift events correlate with exactly the tail-day count the original CVaR estimator already exhausts. The detector does not escape the data wall; it hides it behind a different question. Recorded in `DECISIONS.md §DE-S3-005` ("CVaR-divergence REJECT") and project memory `[[project_cvar_divergence_validation_wall]]`.

**Current operational status.** CVaR is live. The per-cycle path calls `compute_portfolio_cvar` ([`math_engine.py:1185-1345`](math_engine.py)) for each managed symphony and writes the result to `cvar_diagnostic` via `database.record_cvar_diagnostic`. CVaR is **never** a live trigger — it remains diagnostic-only (operator instrumentation, not a decision input). There is a CVAR-001 scope limit on the dashboard: the panel today shows the *first* symphony only; multi-symphony portfolios silently omit other symphonies' rows pending a future expansion.

### 4.5 NN1 spec-freeze — fingerprinting our backtests

**The choice.** Every parameter in the engine carries a `freeze_discipline` enum value recording **why** that parameter was set to its specific value. The autotuner refuses to start if any frozen parameter has `freeze_discipline='BACKTEST_SELECTION'` or an unrecognized discipline. See §[3.6](#36-nn1-spec-freeze--fingerprinting-our-backtests-for-honesty) for the technical detail.

**The rationale.** The user's mandate is "operators making informed decisions." A retail operator running Composer faces selection bias — searching 500 trial-parameter sets and picking the best Sortino is, statistically, equivalent to overfitting. NN1 makes "I chose this number because the backtest liked it" **structurally unrepresentable**. If a developer tried to tune γ on backtest returns, it would show up as a `BACKTEST_SELECTION` row in `researcher_dof_ledger` and the BHY haircut bar would rise to compensate. The wall is structural, not ceremonial: the same Sprint 2 audit fix (`CRRA-001 / NEFF-001 / ARCH-001`) caught a real wiring gap where the U-transform wasn't applied before computing the t-stat. The discipline catches real bugs.

### 4.6 Three Advisors, not one — different lenses for different operator decisions

**The choice.** Post-autotune, three **independent** advisor producers (Overfitting Conscience, Spec Critic, Divergence Explainer) write observations into the database. They share no synthesized verdict. The operator reads each one independently on the `/ai-advisor` tab.

**The alternative considered.** A single "master advisor" that synthesized all observations into one verdict. AlphaBot does not do this.

**The rationale — wall integrity.** A combined synthesis would have to either (a) cross the database read-only wall to query observations the synthesizer did not itself produce — breaking the read-only producer model — or (b) couple the three producers' termination, breaking their independent error containment. Each producer is independently testable and independently failure-resilient. If Spec Critic crashes, Overfitting Conscience still runs.

**The fourth producer that does not exist.** A "Regime & Decision Narrator" producer was scoped but **deferred to Phase 2** (recorded as `DECISIONS.md §DE-S3-003`). The architectural reason: Phase 1 ships the CRRA-EU offline objective and the CVaR diagnostic, neither of which changes which exit the engine fires — so there is no drift between the legacy and the new decision-vector for a Narrator to explain. Narrator activates when Phase 2 unlocks the live CVaR co-signal and the two decision paths can diverge per cycle. The `NARRATOR` advisor-role enum value is retained in the codebase as a deferred slot.

See §[7](#7-ai-advisor--the-three-producers) for what each of the three producers actually does.

### 4.7 Fail-safe floor — the trailing stop fires even when upstream signals are silent

**The choice.** When the Monte Carlo gate returns `None` (insufficient history), the protective Trailing Stop **still fires** on ticks-below-stop alone. When CVaR returns `None`, no breach is reported (the `CVaRAssessment.__post_init__` invariant). The bot fails **safe**, not **open**.

**The rationale.** A fresh symphony deployed mid-month without sufficient history cannot run a regime-locality MC. The two design options were (a) hold all positions until MC is available, or (b) fire the trailing stop on ticks-below-stop alone and allow the protective floor to do its job without the MC sanity gate. AlphaBot chose (b). The operator is **never** exposed to a "MC said hold, so we held into a -20% day" failure mode. This realizes the user's "accuracy + performance over speed" tenet: the bot won't return a fast-but-garbage MC probability; it returns `None` and the heuristic floor fires.

The fail-safe code anchor: [`math_engine.py:425-428`](math_engine.py) — when `prob_beating is None`, the MC sanity gate **passes** (i.e., does not block the exit), so the trailing-stop-hit propagates to the priority resolver. The MC sentinel cannot suppress the protective stop.

---

## 5. Per-symphony walkthrough — one tick from :00 to decision

This section traces one symphony from the `:00` tick through every math layer to the exit decision. Code anchors are inline.

### Step 0 — The scheduler ticks at `:00`

The Flask process registers three jobs via `schedule.every().minute.at(":00")` ([`app.py:301-307`](app.py)):
- `threaded_trigger` — spawns the engine subprocess.
- `_refresh_account_totals` — refreshes NAV display.
- A daily 02:00 `_run_trigger_retention` prune.

At `:00`, `threaded_trigger` ([`app.py:222`](app.py)) forks a non-blocking daemon thread that runs `trigger_alpha_bot()`, which `subprocess.run`s `[sys.executable, "alpha_bot_execution.py"]` and tees stdout/stderr to the daemon log ([`app.py:208-219`](app.py)).

### Step 1 — Engine subprocess starts

`alpha_bot_execution.py` reads `account_uuids` from the environment, opens a state-DB connection, and iterates per-account, per-symphony. For each symphony it snapshots Composer holdings, fetches today's 1-minute Alpaca bars, and computes `current_return`.

### Step 2 — Update high-water mark

`bot_state[symphony_id]["high_water_mark"]` is updated to `max(prior_hwm, current_return)` ([`alpha_bot_execution.py:1098-1110`](alpha_bot_execution.py)). HWM never decreases within a day; it resets at EOD.

### Step 3 — Run Monte Carlo with deterministic seed

```text
prob_beating = math_engine.run_monte_carlo(
    holdings, historical_data, spy_today,
    SIMULATION_PATHS=5000, NEIGHBOR_K=150,
    seed=derive_cycle_mc_seed(cycle_id),
)
```
([`alpha_bot_execution.py:1112-1119`](alpha_bot_execution.py), [`math_engine.py:762-769`](math_engine.py))

`derive_cycle_mc_seed` SHA-256s the `cycle_id` (YYYYMMDD_HHMM) into a 64-bit space so two daemon restarts at the same `:00` produce identical MC results — auditability.

If the symphony has fewer than 39 raw days of history (20 for kNN + 19 for vol warmup), `run_monte_carlo` returns the `MC_INSUFFICIENT_HISTORY_SENTINEL = None`. The protective stop still fires on ticks-below-stop alone — the fail-safe floor.

### Step 4 — Walk the six math layers

In order:

1. **Vol-scaling.** `symphony_vol_20d = calculate_20d_vol(historical_data)` ([`math_engine.py:903-960`](math_engine.py)). This sets the base width of the trailing stop band.
2. **Log time-squeeze decay.** `dynamic_multiplier, dynamic_min_stop = compute_time_squeeze_decay(time_ratio)` ([`math_engine.py:211-242`](math_engine.py)). The stop band shrinks through the day.
3. **Parabolic ratchet.** `should_para_arm = compute_para_arm_decision(velocity, ...)` ([`math_engine.py:185-208`](math_engine.py)). If `velocity ≥ PARABOLIC_VELOCITY_THRESHOLD` and not already armed, the parabolic-squeeze multiplier activates and the stop tightens further.
4. **Breakeven lock.** `(new_hold_ticks, new_breakeven_locked, stop_trigger_level) = compute_breakeven_update(...)` ([`math_engine.py:292-365`](math_engine.py)). Once locked, the stop never drops below entry — `breakeven_locked=True` is monotone.
5. **VWAP×2.** `compute_vwap_breakdown_update(...)` returns `(new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken)`. Both are gated by `VWAP_CROSS_HWM_PCT` and `compute_vwap_bleed_arm_threshold(symphony_vol, bleed_multiplier)` ([`alpha_bot_execution.py:1307-1321`](alpha_bot_execution.py)). Suppressed during the post-open 15-minute grace window ([`math_engine.py:700-723`](math_engine.py)).
6. **MC gating.** `compute_exit_confirmation(...)` ([`math_engine.py:374-435`](math_engine.py)) requires 3 consecutive ticks below the stop line (with a 0.10% magnitude floor) AND a Monte Carlo sanity gate (probability under 60% to permit exit). When MC is `None`, the gate passes (fail-safe).

### Step 5 — Compute the four exit-trigger flags

The four flags fall out of the above layers:
- `is_vwap_broken` from layer 5 (VWAP Breakdown System A).
- `is_tp_hit` from layer 6 (`compute_tp_confirmation` — the Take-Profit confirmation requires MC under `acc_TAKE_PROFIT_MC_PCT` AND positive return with 2-tick confirmation).
- `is_vwap_bleed_broken` from layer 5 (VWAP Bleed Cut System B).
- `is_trailing_stop_hit` from layer 6 (`compute_exit_confirmation`).

### Step 6 — Resolve the priority

```python
if is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken:
    reason, also_true = math_engine.resolve_trigger_priority(
        is_vwap_broken=is_vwap_broken,
        is_tp_hit=tp_triggered_now,
        is_vwap_bleed_broken=is_vwap_bleed_broken,
        is_trailing_stop_hit=is_trailing_stop_hit,
    )
```
([`alpha_bot_execution.py:1428-1441`](alpha_bot_execution.py))

The resolver picks the winner per `_TRIGGER_PRIORITY_ORDER` ([`math_engine.py:728-733`](math_engine.py)) and returns `(winner, also_true)` so the persisted record retains every co-fired flag.

### Step 7 — Queue + drain

If a trigger fired, append to `execution_queue` ([`alpha_bot_execution.py:1459-1469`](alpha_bot_execution.py)) with the winner reason + `also_true` co-fires + the symphony state snapshot. The queue is drained once at the end of the symphony pass — Composer's liquidation endpoint is called with exponential backoff (1s, 2s, 4s, 10s).

If no trigger fired, the loop ends with a `record_cvar_diagnostic` telemetry write (populated from `compute_portfolio_cvar`; CVaR is diagnostic-only and never a trigger) and "no-action" reduces to a state-update pass.

### Step 8 — Post-decision

If an exit fired:
- Discord webhook posts the multi-embed payload (exit reason, Guard Alpha vs hold-to-close, VWAP stats, QuickChart summary).
- The next minute's tick is fresh: the symphony is now in "cash" state in `bot_state` and will not re-enter until Composer's own logic places a new position.

If no exit fired, the symphony's state advances by one tick (HWM may have moved, breakeven counter may have advanced) and waits for the next `:00`.

---

## 6. The Autotuner — how parameters are chosen

The autotuner runs end-of-day per symphony via `run_autotuner(...)` at [`autotuner.py:1283`](autotuner.py). This section walks one cycle.

### Stage 1 — EOD lock (15:53 ET)

A two-stage EOD pipeline prevents Composer API cash flatlines from corrupting the math. Stage 1 (15:53 ET) locks true shadow returns and Guard Alpha using live Alpaca pricing. Stage 2 (16:00 ET) injects tomorrow's target holdings without overwriting the previously locked math.

### Stage 2 — Optuna study

For each symphony, the autotuner:

1. **Loads 125 trading days of history** from the local Alpaca cache (`synthetic_history.py`). 125 days is the binding input — see §[12](#12-open-questions--known-limits) on extending it. The "125-day floor" is recorded in code as the minimum below which the validation fold is degenerate.
2. **Splits into 60% train / 20% validation / 20% frozen-eval.** The 60/20/20 ratio is acknowledged in the autotuner docstring as *"an operator choice for AlphaBot's data scale (125 trading days); the held-out frozen-eval invariant derives from López de Prado 2018 Ch. 7.4, not the specific ratio"* ([`autotuner.py:244-245`](autotuner.py)).
3. **Applies purge=20 + embargo=1 at both fold boundaries.** This is the AFML Ch. 7.4 anti-leakage discipline. After purge, the validation and frozen-eval windows shrink to ~4-5 usable days each — explicitly acknowledged in code at [`autotuner.py:1301-1307`](autotuner.py).
4. **Validates NN1 compliance.** `validate_search_space_nn1()` runs at module-load time ([`autotuner.py:1180-1186`](autotuner.py)); `validate_nn1_compliance(spec_bundle_id)` runs at autotuner entry ([`autotuner.py:1353-1360`](autotuner.py)). Default-deny on unknown freeze-discipline strings.
5. **Creates an Optuna study with TPE sampler.** `study.optimize(objective, n_trials=500, n_jobs=_n_jobs)` ([`autotuner.py:1569`](autotuner.py)). The TPE sampler concentrates the search around promising parameter regions — which induces dependence between trials and is why we use BHY (not plain Benjamini-Hochberg) for the haircut.
6. **Computes the per-trial CRRA-EU t-stat** for each trial's validation-window returns: `mean(U) / (sd(U, ddof=1) / sqrt(T))` ([`autotuner.py:367-400`](autotuner.py)).
7. **Selects the best trial** by t-stat.
8. **Applies the BHY haircut** with the Yekutieli `c(N) = Σ_{j=1}^{N} 1/j` factor for arbitrary dependence ([`autotuner.py:424-476`](autotuner.py)). The "N" used here is `N_effective = N_optuna + S` where `S = Σ n_configs_searched` over researcher_dof_ledger rows with `evidence_source='BACKTEST_SELECTION'` ([`autotuner.py:489-539`](autotuner.py)). If the best trial's adjusted p-value clears the threshold `HARVEY_LIU_FDR_Q = 0.05`, the trial is **certified** and the winning parameters are eligible for deployment. If not, the autotuner refuses to deploy and keeps yesterday's parameters.
9. **Scores the certified winner once on the frozen-eval window.** This is the honest post-selection metric the operator sees on the dashboard. The frozen-eval window is consumed exactly once per cycle — no peeking.
10. **Writes advisor observations** — see §[7](#7-ai-advisor--the-three-producers).

### What gets tuned vs what stays frozen

**Tuned by Optuna** (every parameter in the Optuna search space is honestly disciplined and NN1-compliant):
- Trailing-stop multipliers, dynamic-stop floors
- Parabolic-velocity thresholds, parabolic-squeeze multipliers
- VWAP-bleed multiplier, VWAP-cross HWM, VWAP-bleed ticks
- MC trigger threshold, take-profit MC threshold

**Frozen by THEORY (NN1-honest) and NEVER touched by Optuna**:
- **γ (gamma)** — risk aversion parameter for CRRA. Default lives near γ=2 per Merton 1969 / Samuelson 1969.
- **utility_family** — CRRA per Pratt 1964.
- **wealth_argument** — `W_i = max(WEALTH_ARG_FLOOR, 1 + r_i)` with floor 0.001 per the W-H4 contract.

**Frozen by CALIBRATION or STYLIZED_FACT**:
- 20-day vol window (RiskMetrics standard).
- Log-time-squeeze curve constants and PARA-ARM day-boundary semantics (currently with provenance gaps — see §[3.7](#37-volatility-scaling-time-squeeze-parabolic-ratchet--practitioner-heuristics-with-provenance-gaps) and §[12](#12-open-questions--known-limits)).
- Walk-forward 60/20/20 ratio and 125-day window (CALIBRATION — operator choice; the held-out invariant is from AFML).

### When the autotuner refuses to deploy

If no trial clears the BHY-adjusted threshold, the autotuner emits a "no deployment" verdict and the prior day's parameters carry over. The operator sees this on the dashboard with the haircut statistics (best raw t-stat, BHY-adjusted threshold at `N=N_effective`). This is the operator-trust mechanism in operational form: when the math cannot honestly distinguish today's winner from luck, no winner gets deployed.

---

## 7. AI Advisor — the three producers

Three **independent** observer producers run post-autotune. Each writes `AdvisorObservation` rows to `advisor_observations` via `database.insert_advisor_observation`. They share no synthesized verdict — see §[4.6](#46-three-advisors-not-one--different-lenses-for-different-operator-decisions) for the wall-integrity reason.

All three modules read the database via `database.advisor_ro_query` (enforced by CI lint test `test_advisors_module_uses_advisor_ro_query`). This is a hard architectural constraint: producers are read-only and the `frozen_eval` fold is structurally invisible to them (`COALESCE(fold_role,'') != 'frozen_eval'`). This protects the held-out invariant.

### 7.1 Overfitting Conscience (`advisors/overfitting_conscience.py`)

**What it watches.** The researcher-degree-of-freedom (`S`) counter against the autotuner's N_effective budget.

**Three indicators** ([`advisors/overfitting_conscience.py:7-13`](advisors/overfitting_conscience.py)):
- **I-1 — Any S > 0 row.** Any backtest-selected facet in the ledger → **WATCH** verdict, or **BREACH** if S/N_optuna passes the ratio threshold.
- **I-2 — S/N_optuna > 0.10.** Researcher DoF exceeds 10% of the Optuna trial budget → **BREACH** escalation. Threshold from council synthesis §2.5.
- **I-3 — Monotonic S growth across consecutive runs.** Trend signal that the discipline is slipping → **WATCH**.

**What the operator should do with it.** A BREACH from OC means the BHY haircut bar is materially higher than it would be in a clean run. Either justify the backtest-selected facet (and accept the higher bar) or remove it from the ledger. A clean OC is the operational signal that the autotuner is operating in the NN1-honest steady-state.

### 7.2 Spec Critic (`advisors/spec_critic.py`)

**What it watches.** The `spec_bundles` and `spec_facets` tables for structural integrity.

**Four indicators** ([`advisors/spec_critic.py:5-14`](advisors/spec_critic.py)):
- **I-1 — Required Phase-1 THEORY facets present.** `gamma`, `utility_family`, `wealth_argument` must be present and THEORY-frozen.
- **I-2 — Every facet has a recognized freeze_discipline.** Default-deny: any unrecognized discipline → **BREACH**. This is the forward-compat defense against future migrations introducing a discipline string whose meaning the validator doesn't recognize.
- **I-3 — Facet `frozen_at` age.** Facets older than `SPEC_AGE_WATCH_THRESHOLD_DAYS` → advisory **WATCH**.
- **I-4 — Phase-2 facets not seeded prematurely.** If a `PHASE2_FACET_NAMES` entry shows up in the current bundle → **BREACH**.

**What the operator should do with it.** A BREACH from SC indicates a structural issue with the spec bundle itself — usually a typo in a discipline string or a Phase-2 facet that snuck in. Fix the bundle before the next autotune.

### 7.3 Divergence Explainer (`advisors/divergence_explainer.py`)

**What it watches.** Two CVaR windows (the standard kNN window and an operator-configurable second window). When the `SECOND_WINDOW_CVAR_ENABLED` feature flag is **on**, DE writes per-cycle observations explaining the two-window state in operator-friendly language. When the flag is **off** (the default), DE writes `verdict=NOT_APPLICABLE` rows to preserve audit-trail completeness.

**Hard wall.** DE **must not** persist or display any signed divergence quantity. The forbidden-keys list in the module docstring enumerates: *divergence, signed_divergence, cvar_diff, cvar_delta, window_divergence, divergence_pct, delta* — plus any semantic equivalent ([`advisors/divergence_explainer.py:14-19`](advisors/divergence_explainer.py)). This carries `DECISIONS.md §DE-S3-005` (the CVaR-divergence REJECT — see §[4.4](#44-diagnostic-only-cvar--and-why-we-rejected-cvar-divergence-detectors)) into every produced row.

**Current operational status.** DE is **dormant in the default configuration.** Until an operator turns on `SECOND_WINDOW_CVAR_ENABLED`, every autotune cycle writes a no-op NOT_APPLICABLE row. The operator gets nothing actionable from DE today. This is intentional: Phase 1 ships the wall + the row plumbing; Phase 2 will turn on the second-window CVaR estimator that DE actually explains.

### Why Narrator is deferred

Phase 1 ships the CRRA-EU offline objective + the CVaR diagnostic — neither of which changes which exit the engine fires. There is no drift between the legacy and the new decision-vector for a Narrator to explain. Narrator activates in Phase 2 when the live CVaR co-signal can cause the two decision paths to diverge per cycle. The enum value is retained per `DECISIONS.md §DE-S3-003`.

---

## 8. What the bot does NOT do

Explicit non-goals, so the operator's expectations are calibrated.

- **AlphaBot does not open positions.** Entry decisions are Composer's responsibility. AlphaBot's job is exit discipline only.
- **AlphaBot does not size positions.** Position sizing is Composer's responsibility. AlphaBot operates on the size Composer set.
- **AlphaBot does not make alpha calls.** There is no master forecast of expected return. The bot does not say "this symphony will outperform tomorrow." It says only: "now is the time to exit *this* symphony to cash."
- **AlphaBot does not produce a portfolio-level decision.** Every decision is symphony-level. See §[4.1](#41-symphony-level-only-not-portfolio-level).
- **AlphaBot computes CVaR each cycle as a diagnostic.** `compute_portfolio_cvar` runs per-symphony each minute and writes to `cvar_diagnostic`. CVaR is **never** a live trigger — it is operator instrumentation only. See §[4.4](#44-diagnostic-only-cvar--and-why-we-rejected-cvar-divergence-detectors).
- **AlphaBot does not surface a CVaR-divergence number.** This was explicitly rejected — see §[4.4](#44-diagnostic-only-cvar--and-why-we-rejected-cvar-divergence-detectors) and `DECISIONS.md §DE-S3-005`.
- **AlphaBot does not have a "manual force-trigger" button on the dashboard.** The `/api/trigger` POST handler is intentionally disabled. The scheduler is the only legal engine spawner. See §[4.3](#43-the-dashboard-is-observability-not-action).
- **AlphaBot does not run a Narrator advisor.** Narrator is deferred to Phase 2. See §[7](#7-ai-advisor--the-three-producers).
- **AlphaBot does not auto-restart after a SIGTERM on Windows.** Windows SIGTERM via Bash kills CPython without `atexit`. SQLite WAL files persist and are recovered cleanly on the next start via PRAGMA wal_checkpoint. Use Ctrl+C or `restart.ps1` for a graceful shutdown.

---

## 9. Setup + Operation

### Prerequisites

- Python 3.11 or later
- A Composer.trade account with API credentials and at least one symphony deployed
- An Alpaca account with API credentials (for 1-minute historical data)
- A Discord webhook URL (for alerts)
- A machine reachable during US market hours (09:30–16:00 ET)

### Install

```bash
git clone <repository>
cd AlphaBot
python -m venv .venv
.venv/Scripts/activate   # Windows; on Unix use: source .venv/bin/activate
pip install -r requirements.txt
```

### Configure (.env)

The bot is configured entirely via `.env` (some values are also editable through the dashboard's settings panel). Required keys:

```text
COMPOSER_KEY_ID=...
COMPOSER_SECRET=...
ACCOUNT_UUIDS=uuid1,uuid2,...
ALPACA_KEY=...
ALPACA_SECRET=...
DISCORD_WEBHOOK_URL=...

LIVE_EXECUTION=False         # set True only after dry-run validation
EXECUTION_START_TIME=09:30
```

Tunable algorithm parameters (Optuna will override these once the autotuner has run):

```text
TRIGGER_THRESHOLD_PCT=15.0
TAKE_PROFIT_MC_PCT=5.0
MAX_SQUEEZE_FLOOR=...
VWAP_CROSS_HWM_PCT=1.0
VWAP_BLEED_MULTIPLIER=1.5
VWAP_BLEED_TICKS=10
PARABOLIC_VELOCITY_THRESHOLD=2.0
MAX_PARABOLIC_SQUEEZE=...
SECOND_WINDOW_CVAR_ENABLED=0
```

### Run

```bash
python app.py
```

This starts the Flask dashboard on `http://localhost:8080` and the minute scheduler. To verify, open the dashboard and confirm:
- The "Bot Status" badge reads "Active."
- Symphonies you have deployed in Composer appear in the table within one minute.
- The "Next tick" countdown decrements.

### Dry-run vs Live

- `LIVE_EXECUTION=False` (paper mode): the bot evaluates every cycle and posts Discord alerts as if it were trading, but **does not** call Composer's liquidation endpoint. Use this for at least two weeks against your live symphonies to evaluate fit.
- `LIVE_EXECUTION=True`: live mode. Exits trigger real liquidations against Composer.

### Graceful shutdown

- `Ctrl+C` in the terminal where `python app.py` is running — the cleanest path.
- `restart.ps1` for a managed restart (Windows).
- Avoid `kill -9` / `taskkill /F` — on Windows this leaves the SQLite WAL in a state that requires a `PRAGMA wal_checkpoint(TRUNCATE)` on next start. The bot does this automatically; the behavior is documented as intentional.

### Operator runbooks

For common operational scenarios:
- [`docs/runbooks/composer-rejection-diagnostic.md`](docs/runbooks/composer-rejection-diagnostic.md) — diagnosing and resolving Composer API rejection loops.
- [`docs/runbooks/tzdata-missing-on-host.md`](docs/runbooks/tzdata-missing-on-host.md) — resolving `ZoneInfoNotFoundError` on hosts without IANA tzdata.
- [`docs/runbooks/optuna-recalibration.md`](docs/runbooks/optuna-recalibration.md) — resetting the Optuna study DB after calibration-shifting code changes.

---

## 10. Architecture (for the technically curious)

### The 5-file monolith

The engine is intentionally a small monolith — five Python files plus a small `advisors/` directory.

| File | Role |
|------|------|
| [`app.py`](app.py) (~2500 LOC) | Flask dashboard + minute-by-minute scheduler. Spawns `alpha_bot_execution.py` at every `:00`. Singleton enforcement, signal handling, atexit. |
| [`alpha_bot_execution.py`](alpha_bot_execution.py) (~1700 LOC) | Core engine — per-cycle execution. Wired to the canonical THEORY spec bundle via `get_or_create_phase1_theory_bundle_id`. |
| [`math_engine.py`](math_engine.py) (~1400 LOC) | Pure math: volatility scaling, log-time squeeze, parabolic ratchet, MC gating, VWAP, breakeven, exit confirmation, CRRA-EU utility, CVaR, priority resolver. No I/O. |
| [`autotuner.py`](autotuner.py) (~2050 LOC) | Optuna walk-forward (125 trading days, 500 trials per symphony). CRRA-EU `_haircut_select` objective with `compute_n_effective` additive accounting. NN1 spec-freeze enforcement. |
| [`database.py`](database.py) (~2550 LOC) | State DB: 24 migration SQL files (001–024). 77 public functions including Phase-1 accessors (`record_cvar_diagnostic`, `read_cvar_diagnostic_for_symphony`, `get_or_create_phase1_theory_bundle_id`, `insert_researcher_dof_ledger`, `query_wall_breach_tripwire`). RO connection via `get_ro_connection()`. |
| `advisors/` (3 modules) | Independent post-autotune observer producers (Overfitting Conscience, Spec Critic, Divergence Explainer). |

### The two-DB pattern

- **State DB.** Live positions, decisions, telemetry, advisor observations. Owned by the engine; read-only from the dashboard.
- **Optimization DB.** Optuna studies. Owned by the autotuner; never cross-joined into the state DB at app code. If a row is needed in both DBs, it is copied.

This separation enforces that a corrupt Optuna study cannot poison the live state DB, and a stale state-DB read in the dashboard cannot affect autotune logic.

### The minute scheduler + subprocess spawn

The Flask process registers `schedule.every().minute.at(":00")` jobs that run in the Flask process's daemon thread ([`app.py:301-307`](app.py)). At every `:00`, `threaded_trigger` forks a non-blocking thread that `subprocess.run`s `alpha_bot_execution.py` ([`app.py:208-219`](app.py)). The Flask process is untouched by anything the subprocess does. If the engine takes longer than 60 seconds, the next tick simply queues; if the engine crashes, the next tick spawns fresh.

The dashboard side-effect ban is enforced at [`app.py:1550-1554`](app.py) (the disabled `/api/trigger` handler) — *"The scheduler is the only legal engine spawner."*

### Migrations

Schema migrations live in `migrations/` as numbered SQL files (`001_*.sql` through `024_*.sql`). `_MIGRATION_FILES` in `database.py` applies 004–024 in declared order; migrations 001–003 are applied unconditionally in `init_db`. Migration 021 is listed before 020 intentionally — see the `ARCH-002` inline comment in `database.py`. Reordering would corrupt live DBs that already have 021 applied. Migration discipline is additive-first: new columns are NULLable with DEFAULT, never destructive in one step.

### Invariants enforced at the math boundary

- **Trailing-stop monotonicity.** `compute_active_trailing_stop` carries a `previously_persisted_stop_level` kwarg (Fu & Zhang 2012 canonical clamp). The active stop never decreases.
- **NaN/Inf rejection.** Eleven math functions reject NaN or Inf inputs at the boundary and raise `ValueError`. Callers never receive a silent sentinel.
- **MC sentinel is out-of-band.** `MC_INSUFFICIENT_HISTORY_SENTINEL = None`. The protective stop fires on ticks-below-stop alone when the sentinel is active.

### The fail-safe pattern

Across every math surface, the design rule is: **if the upstream signal is unavailable, fail safe.** Specifically:
- MC `prob_beating = None` → the trailing-stop confirmation gate **passes** (allows exit) ([`math_engine.py:425-428`](math_engine.py)).
- CVaR `cvar_pct = None` → `breach = False` is forced by `CVaRAssessment.__post_init__` ([`math_engine.py:139-146`](math_engine.py)).
- NaN/Inf at any math boundary → rejected via input validation.

This is the F-4 hazard guarantee from the decision-science roadmap.

---

## 11. Audit trail + verification

### Three audits

This branch has been through three audit passes:

1. **Sprint 3 cross-cycle audit** — covers the port-level deprecation, the AI advisor producer roll-out, and the symphony-level decision-math collapse. The port-removal manifest is at [`docs/audit/sprint-3-port-removal-manifest.md`](docs/audit/sprint-3-port-removal-manifest.md).
2. **Math re-audit (2026-05-27)** — verifies numerical correctness of the math layers. The current branch carries the audit's MEDIUM findings as backlog items (`OPTUNA-7`, `PERF-001`).
3. **Vision audit (2026-05-27)** — the audit pass that produced this README. Three reviewer reports in [`docs/audit/vision-audit-2026-05-27/`](docs/audit/vision-audit-2026-05-27/):
   - [`vision-findings.md`](docs/audit/vision-audit-2026-05-27/vision-findings.md) — vision-fit per question, drift list, vision-realization scorecard.
   - [`math-soundness.md`](docs/audit/vision-audit-2026-05-27/math-soundness.md) — per-surface soundness + published references + code anchors.
   - [`logic-trace.md`](docs/audit/vision-audit-2026-05-27/logic-trace.md) — per-symphony narrative + decision-vector inventory + autotuner / advisor traces + 10 open provenance questions.

### The test suite

255 test files, ~3036 test functions. The suite splits as follows:

- **Default** (`/run-tests` skill) — runs the engine, math, autotuner, advisor, and dashboard suites. Excludes live integration tests by default.
- **Live integration** — opted-in via `--include-live`. Two of these conditionally skip when the local environment lacks live credentials.
- **Performance** — a separate split with in-memory-cache benchmark fixtures (see `tests/perf/`).

### What's pinned

- **BHY byte-identical pin.** `tests/fixtures/math/bhy_byte_identical_pin.json` pins the haircut output for a canonical search. Migration to the new additive `N_effective` accounting was byte-identical in the NN1-honest case (S=0) — pinned to verify.
- **MC seed determinism.** Cycle-ID-derived SHA-256 seeds produce identical MC results across daemon restarts — pinned in `tests/engine/`.
- **NN1 read-only wall.** `test_advisors_module_uses_advisor_ro_query` ensures all three advisor modules read through `database.advisor_ro_query` and never open a direct connection.
- **Resolver determinism.** `resolve_trigger_priority` is a pure function and is pinned to return the exact `(winner, also_true)` for every combination of the four input flags.
- **Math-engine constants.** All numeric constants in `math_engine.py` are named and documented. Provenance for every constant is tracked in [`docs/math_engine/constants.md`](docs/math_engine/constants.md).

---

## 12. Open questions + known limits

This section is the honest list of what *isn't* settled.

### Open provenance questions (from the logic-trace audit)

These are choices the code makes today that have no written first-principles justification in `DECISIONS.md`, feature-plans, project memory, or in-file comments. They are not bugs — they are gaps the doc-writer flags for either future resolution or `[open question]` acknowledgment.

| # | Choice | Status |
|---|---|---|
| **OQ-1** | `_TRIGGER_PRIORITY_ORDER` places Take-Profit ahead of VWAP Bleed Cut | The relative position of TP vs Bleed Cut is load-bearing on cycles where both fire. The in-code comment cites a historical H2 acceptance-criteria reference; no first-principles argument is on file. Open. |
| **OQ-2** | `n_trials=500` for the per-symphony walk-forward | The BHY-over-Bonferroni argument at [`autotuner.py:300-302`](autotuner.py) is backwards — it justifies the haircut for N=500, not 500 specifically. No power-analysis for 500 over 250 or 1000. Open. |
| **OQ-3** | `MC_DEFAULT_NEIGHBOR_K = 150` and the absence of a regime-match-quality guard | The kNN locality fails when the 150 nearest neighbors are all "least bad fits" — a known soft spot in the math review. The recommended fix (math-soundness §"Critique" item 1) is a regime-match-quality threshold that defaults MC to "allow exit" when the mean kNN distance exceeds a bound. Open. |
| **OQ-4** | `MC_DEFAULT_SIMULATION_PATHS = 5000` | "CLT stability vs runtime tradeoff" is the only justification. No runtime-budget anchor. Open. |
| **OQ-5** | `PARABOLIC_VELOCITY_THRESHOLD` default + PARA-ARM day-boundary semantic | The `prev_return=0` reset at every new day means any symphony opening above 2% auto-arms PARA on the first cycle. May be intended ("any large move from baseline arms the squeeze") or unintended. Open. |
| **OQ-6** | `VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS` | The VWAP×2 thresholds are tagged in the council synthesis as Phase-1.5 M3 R2 re-derive targets — **this is the one OQ already on a remediation track.** |
| **OQ-7** | `VWAP_OPEN_WINDOW_GRACE_MINUTES = 15` | No in-code justification for 15 specifically. Operator empirics. Open. |
| **OQ-8** | 60/20/20 walk-forward ratio | Documented in code as an operator choice; the held-out invariant derives from AFML 2018 Ch. 7.4 but the **specific ratio** is not theoretical. Honest provenance — not a gap, but worth knowing. |
| **OQ-9** | `HARVEY_LIU_FDR_Q = 0.05` | "Conventional" — the operator may tighten/loosen. Honest provenance — policy dial. |
| **OQ-10** | The `_SORTINO_SENTINEL = 1e6` magic number | Chosen to look finite to Optuna but distinct enough to filter before BHY. The specific magnitude is arbitrary. Open. |
| **OQ-11** | γ (gamma) default and where it lives | Per the math-soundness review, γ=2 is the moderately-risk-averse retail default. Whether to surface this as a configurable parameter or keep it THEORY-frozen-only is a product decision. Open. |

### Phase 1.5 M3 redrive-provenance items

These are tracked as work, not flagged as gaps.

- **Log-time-squeeze curve re-derivation (R1).** The `log10(1 + 9*t)` curve has no formal anchor. Phase 1.5 M3 R1 re-derives the curve with either a risk-budget argument or an empirical fit to the intraday vol U-shape.
- **VWAP×2 threshold re-derivation (R2).** The HWM-gate threshold and the bleed-multiplier currently have no published anchor. Phase 1.5 M3 R2 re-derives them.

### Math re-audit backlog (MEDIUM findings)

- **OPTUNA-7** — open. (Tracked by the engine-audit plans in `feature-plans/decision-science/engine-audit/`.)
- **PERF-001** — open. (Tracked by the engine-audit plans.)

### Live integration tests

Two of the live integration tests conditionally skip when the local environment lacks live Composer or Alpaca credentials. They are runnable on a properly configured developer machine via `--include-live`.

### Untouched locked worktree

A locked agent worktree exists at `.claude/audit-worktrees/` that this audit did not touch. It contains in-flight work from another team. No bearing on this branch's correctness.

### CVaR live wire-up

The per-cycle live path writes all-`None` sentinels to `cvar_diagnostic` ([`alpha_bot_execution.py:1417-1426`](alpha_bot_execution.py)) instead of calling `compute_portfolio_cvar`. The dashboard CVaR panel renders the framing labels but the numeric cells are empty. Also: the panel currently shows the first symphony only (CVAR-001 scope limit) — multi-symphony portfolios silently omit other symphonies' rows. **Both are intentional Phase-1 deferrals**; the live wire-up and the multi-symphony expansion are staged for Phase 1.5.

---

*Last updated: 2026-05-27. See [`docs/audit/vision-audit-2026-05-27/`](docs/audit/vision-audit-2026-05-27/) for the three audit reports that informed this README.*

*Disclaimer: AlphaBot is an automated execution tool. Algorithmic trading carries significant risk. Always test parameters in dry-run mode before enabling `LIVE_EXECUTION`.*
