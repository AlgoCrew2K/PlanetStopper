# Math Soundness — Sprint 3 Final (2026-05-27)

**Author:** math-reviewer (team-vision-audit)
**Branch:** `audit/vision-audit`
**Frame:** The prior re-audit (`docs/audit/math-reaudit-2026-05-27/`) verified numerical correctness. This review is different: it asks (a) is the math sound for the user's stated purpose, (b) does it serve the user's stated risk philosophy (disciplined trailing-stop overlay on a Composer.trade portfolio), (c) can a smart non-expert follow the rationale, and (d) is there a published reference?

The user's mandate is **accuracy + performance over anything else** and **operators making INFORMED decisions**. Math that is technically correct but un-explainable fails that mandate. The pitches below are written for "someone with a cursory understanding of trading algorithms and quantstats."

Source quality is graded on the standard tiers: `[Tier-1 peer-reviewed]`, `[Tier-2 practitioner book]`, `[Tier-3 industry/community]`, `[Tier-4 blog]`, `[Folklore]`. Evidence is graded: `[Theoretical] / [Backtest] / [Out-of-sample] / [Live] / [Folklore]`.

---

## 0. TL;DR for the operator

| # | Surface | Sound? | Vision-fit | Strongest published anchor |
|---|---------|--------|------------|---------------------------|
| 1 | CRRA-EU utility objective | YES | Strong | Pratt 1964; Merton 1969; Samuelson 1969 |
| 2 | CVaR diagnostic | YES | Strong (as diagnostic) | Rockafellar-Uryasev 2000, 2002; Acerbi-Tasche 2002 |
| 3 | Monte Carlo gating | PARTIAL | Moderate | Glasserman 2003; Efron 1979 (bootstrap); no published precedent for MC-gated exits |
| 4 | 6-layer exit priority | YES | Strong | Wilder 1978 (trailing-stop family); Kaminski & Lo 2014 (composition) |
| 5 | BHY haircut + N-effective additive | YES | Strong | Benjamini-Hochberg-Yekutieli 2001; Harvey & Liu 2015; Bailey & López de Prado 2014 |
| 6 | NN1 spec-freeze | YES | Strong | López de Prado 2018 (AFML Ch. 11); Bailey et al. 2014 PBO |
| 7 | Walk-forward (125d / 500 trials) | PARTIAL | Moderate | Pardo 2008; López de Prado 2018 (AFML Ch. 7.4) |
| 8 | Vol-scaling / log-time squeeze / parabolic ratchet | PARTIAL | Mixed | Wilder 1978; Andersen-Bollerslev 1997; specific shapes are practitioner constructs |

**The strongest layers** are CRRA-EU, BHY haircut, NN1 spec-freeze, and CVaR diagnostic — these sit on well-trodden academic ground and serve the stated philosophy directly. **The weakest** is the log-time squeeze curve (practitioner, no published anchor) and the calibration window (125 days is short by published walk-forward standards even with the 60/20/20 split). Both are already self-flagged in code.

---

## Per-surface evaluation

### Surface 1 — CRRA-EU utility (`compute_crra_eu_objective`, `compute_crra_eu_tstat`)

**Sound? YES.** Constant Relative Risk Aversion (CRRA) utility is the textbook risk-adjusted-return objective in financial economics. The wealth-argument derivation `W_i = max(WEALTH_ARG_FLOOR, 1 + r_i)` is correct: utility is defined over wealth, not return; flooring at 0.001 (W-H4) is a numerical safety floor — the code applies it to *input* W only, never to *output* U, preserving the utility ordering. The gamma==1 log-utility limit (Samuelson 1969 / Merton 1969 by L'Hôpital) is handled explicitly. The t-stat `mean(U) / (sd(U)/sqrt(T))` with `ddof=1` is the standard one-sample t-statistic for a mean-valued functional — the right choice. The H-6 category discipline (don't reuse `sortino * sqrt(T)` for a mean objective) is documented in code at `autotuner.py:367-400` and is correct.

**Vision-fit? YES.** The user's stated philosophy is disciplined, risk-averse capital preservation. CRRA is the canonical formalization of risk aversion. Sharpe ratio (the obvious alternative) is symmetric — it treats a +2σ outcome and a -2σ outcome as equally good once squared. CRRA does not: it penalizes large losses more than equally-sized gains because `(1+r)^(1-γ)` is concave. For a risk overlay whose explicit job is "make sure a loss doesn't blow up the account," concave utility is the correct shape.

**Non-expert pitch (5 sentences):**
> "When the bot tunes its parameters, it doesn't just look for the trial with the highest average return — that would ignore risk. It uses CRRA utility, which is a textbook formula for an investor who hates losses more than they love equally-sized gains. Concretely: each daily return gets converted into a 'utility score' that has diminishing marginal benefit (a +2% day is worth more than zero, but a -2% day is worth MORE than -2% worth of bad). The bot picks the parameter set whose average utility is highest — meaning it picks the configuration that produces the best risk-adjusted experience, not the highest raw return. The shape of the curve is set by a single parameter γ (gamma), where higher γ means more loss-averse — AlphaBot's default lives near γ=2, which matches a moderately risk-averse retail investor."

**Reference:**
- Pratt, J. W. (1964). "Risk Aversion in the Small and in the Large." *Econometrica* 32(1-2), 122-136. (Introduces CRRA / relative risk aversion.)
- Merton, R. C. (1969). "Lifetime Portfolio Selection under Uncertainty: The Continuous-Time Case." *Review of Economics and Statistics* 51(3), 247-257.
- Samuelson, P. A. (1969). "Lifetime Portfolio Selection by Dynamic Stochastic Programming." *Review of Economics and Statistics* 51(3), 239-246.

**Code refs:** `math_engine.py:1348-1404` (`compute_crra_utility`, `compute_crra_eu_objective`), `autotuner.py:367-400` (`compute_crra_eu_tstat`).

**Empirical evidence:** `[Theoretical]` for the functional form (formally proven). `[Live]` only via AlphaBot's own autotuner runs — no published backtest of CRRA-EU vs Sharpe specifically for trailing-stop calibration; this is a methodology choice, not an empirical claim.

**Replication status:** CRRA utility is in every intermediate-level financial-economics textbook. Trivially reproducible.

**Regime sensitivity:** γ matters most under fat-tailed regimes. A γ=2 default is mildly risk-averse; in a regime with frequent large losses (March 2020, August 2024 vol spike) a tuner might prefer γ=3-5 to push the utility further into loss-aversion territory. AlphaBot's `gamma-2d-search-space` plan (Phase-2) anticipates this.

---

### Surface 2 — CVaR diagnostic (`compute_portfolio_cvar`, `CVaRAssessment`)

**Sound? YES.** Conditional Value-at-Risk (CVaR), a.k.a. Expected Shortfall (ES), at 5% tail is the average loss in the worst 5% of outcomes. The Acerbi-Tasche convention (used by AlphaBot per the constant `CVAR_TAIL_PCT = 0.05`) is the post-2002 standard. The Rockafellar-Uryasev (2000, 2002) general-distribution estimator handles the discrete kNN pool correctly — its formula does not require continuity, which is the right pick for a 150-day empirical pool with possible ties. The `CVaRAssessment` typed result with the fail-safe invariant (`cvar_pct=None → breach=False`, enforced in `__post_init__`) is exactly the right defensive-programming pattern: an absent estimate must never *cause* a trigger.

The Phase-1 decision to keep CVaR **diagnostic-only** (no live trigger) is defensible: with a 125-day history and 150-neighbor kNN, the genuine distinct tail observation count is small (~8 at α=0.05 against ~150 neighbors), so the standard error of the CVaR estimate is large. Operators see CVaR alongside live exit decisions but are not silently exposed to a CVaR-driven exit before the estimator's sampling variance is understood.

**Vision-fit? YES.** Trailing stops can MISS a crash: by the time the trailing stop fires, the loss is already realized. CVaR is the ex-ante measure of "how bad could the next 24h be." Showing it as a diagnostic on the dashboard lets the operator *override* — pause new positions, reduce size, or close on intuition — when the bot's tail estimate spikes. That is the user's stated philosophy: informed operator decisions.

**Non-expert pitch:**
> "CVaR at 5% answers: 'in the worst-case 1-in-20 day, how bad is the loss likely to be?' It is NOT a forecast — it is a worst-case rough estimate derived from the 150 historically-most-similar days. AlphaBot computes CVaR every cycle and shows it on the dashboard so the operator can see if the bot's worst-case looks unusually bad today. The bot does NOT act on CVaR automatically; the operator decides. This is intentional — CVaR estimated from only ~150 days has a wide error bar, so it's a discussion-prompt, not a trigger."

**Reference:**
- Rockafellar, R. T. & Uryasev, S. (2000). "Optimization of Conditional Value-at-Risk." *Journal of Risk* 2(3), 21-41. (The general-distribution estimator AlphaBot uses.)
- Rockafellar, R. T. & Uryasev, S. (2002). "Conditional value-at-risk for general loss distributions." *Journal of Banking & Finance* 26(7), 1443-1471. (Extends to discrete distributions.)
- Acerbi, C. & Tasche, D. (2002). "On the coherence of expected shortfall." *Journal of Banking & Finance* 26(7), 1487-1503. (Establishes ES/CVaR as a coherent risk measure where VaR is not.)

**Code refs:** `math_engine.py:120-152` (`CVaRAssessment`), `math_engine.py:1185-1345` (`compute_portfolio_cvar`), `math_engine.py:104-112` (constants).

**Empirical evidence:** `[Theoretical]` (CVaR is mathematically well-defined). `[Out-of-sample]` for CVaR-as-risk-measure broadly; `[Folklore]` for the specific kNN-conditioned-empirical-CVaR construction.

**Replication status:** CVaR is replicated across every published risk-management textbook since 2003 (Jorion *Value at Risk*; McNeil-Frey-Embrechts *Quantitative Risk Management*). The kNN-conditioned variant is AlphaBot-specific.

**Regime sensitivity:** kNN-CVaR fails when the current regime is unprecedented — the 150 nearest neighbors are all "least bad fits" and the tail estimate is unrepresentative. AlphaBot does NOT yet have a regime-match-quality guard. Already flagged in `docs/research/dashboard/math-engine-methodology-review.md` §4. Project memory `project_cvar_divergence_validation_wall` records the team rejecting the CVaR-divergence-detector idea precisely because "validate a detector not an estimate" relocates the data wall — does not escape it. The Phase-1 diagnostic-only decision is the conservative path.

---

### Surface 3 — Monte Carlo gating (`run_monte_carlo`)

**Sound? PARTIAL.** Each component (empirical bootstrap, kNN regime-matching, 5000-path Monte Carlo) is individually well-established. The *combination as an exit veto* is the unconventional part — there is no peer-reviewed precedent. The eligible-pool boundary at `MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) = 39` raw days is correct: the first `MC_VOL_WINDOW_DAYS - 1 = 19` days have rolling-vol estimates computed on a short, downward-biased sample, so the kNN distance feature would be misleading if they were admitted. Excluding them is the right call, and the audit `project_mc_eligible_pool_vs_raw_day_boundary` confirms this distinction. The seed derivation via SHA-256 of `cycle_id` mapped into a 64-bit space is a correct deterministic seeding pattern (the previous 2^31 modulus had birthday-bound collision risk and was corrected — see `math_engine.py:93-99`).

The methodological soft spots (documented in `math-engine-methodology-review.md` §4):
- **No regime-match-quality guard** — if the 150 nearest neighbors have a mean distance above a threshold, the bootstrap is unrepresentative. AlphaBot computes distances but does not threshold them.
- **5000 paths over 150 neighbors with replacement** — yields a sampling SE of ~0.7pp around the 60% gate, fine in absolute terms but the underlying neighbor count is the binding constraint.
- **MC-gated exits is `[Folklore]`** as a category — no peer-reviewed evidence that MC-vetoing a trailing stop adds value.

**Vision-fit? PARTIAL.** The framing makes sense for the user's philosophy — "don't capitulate at a noisy local low if the regime-conditional distribution says we usually recover from here" — but the gating is exposed to Kaminski & Lo (2014)'s well-replicated finding: stops add value under momentum and subtract under random walks. The MC veto blocks exits when the recent regime *was* trending; in a true regime break, the historical conditional CDF is least informative when most needed.

**Non-expert pitch:**
> "When the bot is about to fire a trailing-stop exit, it pauses and asks: 'in the 150 historically-most-similar days, how often would we have ended above where we are right now?' If the answer is above 60%, the bot blocks the exit — the regime says we usually recover from here, so don't capitulate at the noisy local low. This is NOT a forecast; it's a *delay*. If price keeps falling, the next cycle re-checks. The risk is that during a regime shift — when the past 150 days look nothing like today — the gate looks at irrelevant history and over-blocks. The bot does not currently warn the operator when the regime-match quality is poor; that is an acknowledged gap."

**Reference:**
- Glasserman, P. (2003). *Monte Carlo Methods in Financial Engineering.* Springer. (Canonical MC reference; not specific to exit gating.)
- Efron, B. (1979). "Bootstrap Methods: Another Look at the Jackknife." *Annals of Statistics* 7(1), 1-26. (Empirical bootstrap.)
- Kaminski, K. M. & Lo, A. W. (2014). "When do stop-loss rules stop losses?" *Journal of Financial Markets* 18, 234-254. (The most-cited stop-loss paper — relevant because the MC veto's behavior depends on regime type.)
- No peer-reviewed precedent for MC-gated exits as a category; the construction is AlphaBot-specific.

**Code refs:** `math_engine.py:84-99` (constants, eligible-pool boundary), `math_engine.py:772-900` (`run_monte_carlo`), `math_engine.py:762-769` (`derive_cycle_mc_seed`).

**Empirical evidence:** `[Theoretical]` for the bootstrap + kNN components. `[Folklore]` for MC-vetoed exits. No published OOS backtest of this exact combination.

**Replication status:** Each component is independently replicated; the combination is single-source.

**Regime sensitivity:** Fails on regime breaks (the conditional CDF is least informative when most needed). This is the highest-risk component for the user's mandate because the failure mode coincides with the moment the operator most needs accurate information.

---

### Surface 4 — 6-layer exit priority (`resolve_trigger_priority`)

**Sound? YES.** As implemented, the priority resolver is a *deterministic hierarchical resolver* over four trigger flags (VWAP Breakdown → Take-Profit → VWAP Bleed Cut → Trailing Stop), returning `(winner, co-fired)` rather than dropping the co-fired information. (The "6 layers" referenced in the brief and project file-map appears to include two upstream gates — MC confirmation inside `compute_exit_confirmation` and breakeven-lock inside `compute_breakeven_update` — that influence which flag is True, not the resolution order itself.) The implementation is pure, deterministic, and stable in `_TRIGGER_PRIORITY_ORDER`.

**Vision-fit? YES.** Reporting the co-fired triggers (not just the winner) is the right operator-information call. When 4 of 4 triggers fire simultaneously, the operator sees that — distinguishing a genuine deep-conviction exit from a single-signal noise spike. This serves the user's "informed decisions" mandate directly.

**Soft spot (documented):** the priority order itself (VWAP Breakdown highest, Trailing Stop lowest) is a design choice, not a derived result. There is no published prescription for ordering these specific triggers. The order is defensible (VWAP-Breakdown is the fastest hard-cut; Trailing Stop is the slowest momentum-respecting cut) but is `[Folklore]` for the specific 4-way ordering.

**Non-expert pitch:**
> "Four different exit signals can fire in the same minute: a VWAP-line break, a take-profit hit, a slow VWAP-bleed cut, and a trailing-stop hit. AlphaBot picks one as the 'reason for exit' using a fixed priority — VWAP-Breakdown first, then Take-Profit, Bleed Cut, Trailing Stop — but it also REPORTS the other triggers that co-fired. This matters because when all four fire at once, that's high-conviction; when only the lowest-priority one fires, that's a single weak signal. The operator sees both pieces of information."

**Reference:**
- Wilder, J. W. (1978). *New Concepts in Technical Trading Systems.* Trend Research. (Establishes trailing-stop family; not the specific priority order.)
- Kaminski, K. M. & Lo, A. W. (2014). *op. cit.* (Composition of stop-loss rules.)
- The specific priority order is AlphaBot-defined; no peer-reviewed precedent for the 4-way ordering.

**Code refs:** `math_engine.py:720-759` (`resolve_trigger_priority`, `_TRIGGER_PRIORITY_ORDER`).

**Empirical evidence:** `[Theoretical]` for the determinism (proven). `[Folklore]` for the priority order. `[Live]` from AlphaBot's own daily dashboard.

**Replication status:** The resolver pattern is generic and replicable. The specific order is single-source.

**Regime sensitivity:** Order does not change with regime — by design. In a flash-crash, all four triggers fire simultaneously and the operator sees that.

---

### Surface 5 — BHY haircut + N_effective additive accounting

**Sound? YES.** This is the strongest piece of math in the engine. The Benjamini-Hochberg-Yekutieli (BHY, 2001) step-up procedure is the correct false-discovery-rate (FDR) control for *dependent* test statistics — and Optuna's TPE sampler explicitly induces dependence (it concentrates the search). Plain Benjamini-Hochberg (1995) assumes independence/PRDS and would under-correct. The Yekutieli arbitrary-dependence factor `c(N) = sum_{j=1}^{N} 1/j` is correctly implemented (`autotuner.py:439-476`).

The **additive N_effective accounting** `N_effective = N_optuna + S` (where S is the sum of `n_configs_searched` over researcher_dof_ledger rows with `evidence_source='BACKTEST_SELECTION'`) is the right structural choice over a multiplicative formulation. Multiplicative scaling (N_optuna × S) would explode the haircut threshold and reject genuine signals; additive accounting matches the intuition that researcher degrees-of-freedom and Optuna trials are *parallel* search dimensions, both contributing to the multiple-testing problem at the same order of magnitude. The honest-case `S=0 → N_effective = N_optuna → byte-identical to today's haircut` is the correct fail-safe.

**Vision-fit? STRONG.** This is the layer that operationalizes "don't fool yourself with a backtest you ran 500 times." The user's mandate is accuracy; the BHY haircut is the formal admission that 500 Optuna trials inflate the in-sample best, and the haircut deflates accordingly. Without this, the autotuner's "best Sortino" is the standard backtest-overfitting trap.

**Non-expert pitch:**
> "Run 500 random parameter sets and the BEST of them is, on average, much better than it deserves to be — by luck alone. This is the multiple-testing problem. AlphaBot corrects for it using the BHY haircut, named after Benjamini, Hochberg, and Yekutieli. After 500 trials, the bar that any candidate must clear is RAISED — proportional to how many trials were run. If the raw winning trial doesn't clear the raised bar, the autotuner refuses to deploy and keeps the previous parameters. There is also a tripwire (`N_effective`): if a researcher manually tried more variants offline before submitting to the autotuner, those count toward the bar too — so you can't game the test by pre-filtering."

**Reference:**
- Benjamini, Y., Hochberg, Y. & Yekutieli, D. (2001). "The Control of the False Discovery Rate in Multiple Testing under Dependency." *Annals of Statistics* 29(4), 1165-1188.
- Harvey, C. R. & Liu, Y. (2015). "Backtesting." *Journal of Portfolio Management* 42(1), 13-28. DOI: 10.3905/jpm.2015.42.1.013. (Prescribes BHY for trading-strategy backtests.)
- Bailey, D. H., Borwein, J., López de Prado, M. & Zhu, Q. J. (2014). "The Probability of Backtest Overfitting." *Journal of Computational Finance* 20(4), 39-70. (PBO framework — motivates the haircut.)
- Bailey, D. H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio." *Journal of Portfolio Management* 40(5), 94-107. SSRN 2460551. (Closely related; AlphaBot uses BHY-on-Sortino rather than DSR-on-Sharpe — same family.)

**Code refs:** `autotuner.py:424-476` (`compute_haircut_pvalue`, `benjamini_hochberg_adjust`), `autotuner.py:489-539` (`compute_n_effective`).

**Empirical evidence:** `[Tier-1 peer-reviewed]` for BHY and Harvey & Liu. `[Live]` for AlphaBot's own autotuner — pinned by `tests/fixtures/math/bhy_byte_identical_pin.json`.

**Replication status:** BHY is independently replicated across statistics literature; Harvey & Liu have ~2000+ citations.

**Regime sensitivity:** None — the haircut is a structural property of the search, not of the data. This is one of its virtues.

---

### Surface 6 — NN1 spec-freeze (`NN1_HONEST_DISCIPLINES`)

**Sound? YES.** NN1 (the "no NN1 violation" rule) enforces that every facet of a frozen spec bundle was frozen via an *honest* discipline — `THEORY`, `MANDATE`, `STYLIZED_FACT`, `POLITIS_WHITE`, `CADENCE`, or `CALIBRATION` — never `BACKTEST_SELECTION`. The frozenset is immutable; default-deny on unknown discipline strings; the BHY haircut counts `BACKTEST_SELECTION`-tainted rows into `S` so the haircut bar rises *structurally* if NN1 is violated. This is the correct structural defense.

Why these 6 disciplines and not others: each names a *non-overfittable* source of evidence:
- **THEORY** — derived from a published model
- **MANDATE** — operator/regulatory constraint
- **STYLIZED_FACT** — replicated empirical regularity (e.g., intraday vol U-shape)
- **POLITIS_WHITE** — Politis & White (2004) bootstrap block-size selection
- **CADENCE** — discrete operational choice (1-minute, EOD)
- **CALIBRATION** — fitted to historical without optimization target

`BACKTEST_SELECTION` is the *one* discipline that creates an NN1 violation — selecting a parameter because it produced the best historical P&L is exactly the overfitting failure mode.

**Vision-fit? STRONG.** This is the strongest single discipline in the engine for the user's stated mandate. The user wants accuracy and informed decisions — NN1 makes "I chose this number because the backtest liked it" structurally unrepresentable. If a researcher tries to slip a P&L-frozen constant into a spec bundle, the haircut catches it (the row inflates S, which raises N_effective, which raises the BHY bar). If the researcher hides the row, the spec_bundle_hash mismatches and the autotuner refuses to run.

**Non-expert pitch:**
> "Each parameter in the bot is 'frozen' for a reason — and that reason is recorded. There are six ALLOWED reasons (theory, mandate, stylized fact, etc.) and one BANNED reason: 'the backtest liked this number.' The bot's autotuner refuses to deploy a parameter set if any constant was frozen for the banned reason. This prevents the most common form of self-deception in trading research: cherry-picking parameters because the historical P&L looked good with them. NN1 is the structural guarantee that AlphaBot's parameters have a non-circular justification."

**Reference:**
- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley. Chapter 11 ("Backtest Overfitting") and Chapter 13 ("Backtesting on Synthetic Data") establish the "researcher degrees of freedom" frame that NN1 operationalizes.
- Bailey, D. H. et al. (2014). PBO paper, *op. cit.*
- Politis, D. N. & White, H. (2004). "Automatic block-length selection for the dependent bootstrap." *Econometric Reviews* 23(1), 53-70. (Source of the `POLITIS_WHITE` discipline.)

**Code refs:** `autotuner.py:73-90` (constants, `NN1_HONEST_DISCIPLINES`), `autotuner.py:1247+` (validate_search_space_nn1), `autotuner.py:489-539` (the structural enforcement via `compute_n_effective`).

**Empirical evidence:** `[Theoretical]` for the structural argument. `[Live]` from AlphaBot's spec-bundle ledger and the BHY haircut's response.

**Replication status:** The "researcher degrees of freedom" frame is independently established in López de Prado, Bailey, Harvey & Liu. The NN1 enforcement mechanism is AlphaBot-specific but mechanically straightforward.

**Regime sensitivity:** None — NN1 is a discipline on the *research process*, not the data.

---

### Surface 7 — Walk-forward (125 days, 500 trials per symphony)

**Sound? PARTIAL.** The walk-forward methodology is *correct in shape* — three-fold split (60/20/20 → ~75 train / ~25 validation / ~25 frozen-eval), with `PURGE_DAYS=20` and `EMBARGO_DAYS=1` applied at both fold boundaries per AFML Ch. 7.4. The frozen-eval window is consumed exactly once after best-trial selection (the honest post-selection metric). This is methodologically correct.

The **soft spot** is the 125-day total history: after purge=20 at each of two boundaries, the usable validation and frozen-eval windows shrink to ~4-5 days each. This is explicitly acknowledged in `autotuner.py:1301-1307` ("OOS-fold-collapse v2"). At 4-5 usable OOS days, the t-statistic for any performance metric has SE around 1.5-2.0 — too loose to discriminate truly skilled trials from lucky ones except in the extremes. The 500-trial Optuna budget compounds this: with 5,500 effective optimizations (500 × 11 symphonies), even the BHY-corrected haircut is doing heavy lifting.

The **500-trial floor** is well-justified: at N=500, the Yekutieli `c(N)≈6.79`; at the 100-trial floor, `c(N)≈5.19` — reducing trials disproportionately shrinks the selection-bias correction. The `trial-floor-justification` engine-audit plan pins this with a named constant and a tripwire.

**Vision-fit? PARTIAL.** The shape (three-fold with purge+embargo + frozen-eval) is correct for the user's "informed decisions" mandate — the frozen-eval Sharpe is reported honestly. But the *power* is low at 125-day history. An operator looking at "OOS Sharpe = 0.4 ± 1.7" cannot meaningfully distinguish skill from noise without many more such runs across symphonies. The bot's risk-engine philosophy is sound; the calibration window's statistical resolution is short.

**Non-expert pitch:**
> "The bot tunes its parameters on 125 trading days of history — roughly 6 months. It splits that history into three pieces: ~75 days to train, ~25 days to validate trial choices, and ~25 days to score the final winner ONCE on data it never touched during the search. Between each piece, 20 days are 'purged' (thrown away) so the volatility window from the train side doesn't leak into the test side — a methodology choice from López de Prado's textbook on financial machine learning. The tradeoff: 20 days of purge eats most of the test windows, leaving ~4-5 usable days for the final honest score. That's a small sample — the score is the right shape (no peeking, no contamination) but has wide error bars. Future work would extend the history beyond 125 days to give the score more statistical power."

**Reference:**
- López de Prado, M. (2018). *Advances in Financial Machine Learning,* Wiley. Chapter 7 (Cross-Validation in Finance — purging and embargo), Chapter 11 (Backtest Overfitting). The 60/20/20 ratio is an operator choice; AFML prescribes the held-out invariant, not the ratio.
- Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies,* 2nd ed., Wiley. (Walk-forward methodology — typically recommends 5-10 rolling folds; AlphaBot uses 1 fold.)
- Bailey, D. H. & López de Prado, M. (2014). The Deflated Sharpe Ratio paper, *op. cit.*

**Code refs:** `autotuner.py:1283-1311` (walk-forward docstring), `autotuner.py:1569` (`n_trials=500`), `feature-plans/decision-science/engine-audit/trial-floor-justification/plan.md`.

**Empirical evidence:** `[Tier-1 peer-reviewed]` for walk-forward methodology. `[Tier-2 book]` for the 60/20/20 ratio as one operator choice among many. `[Live]` for AlphaBot's own runs.

**Replication status:** Walk-forward with purge+embargo is independently replicated across López de Prado, Pardo, and multiple Bailey papers. The specific 60/20/20 ratio at 125-day scale is operator-chosen.

**Regime sensitivity:** A 125-day window spans one regime in most years. Constants tuned in a post-COVID liquidity regime may not be robust to a 2018-style vol-shock. The auto-tuner re-runs nightly, so the parameters do drift — but each refresh is also constrained by the same 125-day window.

---

### Surface 8 — Volatility scaling, log time squeeze, parabolic ratchet

**Sound? PARTIAL.** Each piece divides:

- **20-day realized volatility** (`calculate_20d_vol`, `LOOKBACK_DAYS = 20`): MAINSTREAM. Foundation in Andersen & Bollerslev (1997); 20-day is one of the four RiskMetrics-canonical windows (alongside 30/60/250). Sound.
- **14-day ATR** (`calculate_14d_atr_pct`): canonical Wilder (1978) period. The implementation uses simple mean of true ranges (not Wilder smoothing) — a minor deviation that matters in trending vol regimes (`math-engine-methodology-review.md` §1).
- **Log time squeeze** (`compute_time_squeeze_decay`): the `log10(1 + 9*t)` curve is **practitioner / folklore** with no published precedent. The risk-budget rationale ("less time remaining → tighter stop") is coherent and distinct from the intraday-vol U-shape literature; the specific curve shape is AlphaBot-defined. Four degrees of freedom in the constants makes this the most overfit-exposed layer.
- **Parabolic ratchet / PARA-ARM** (`compute_para_arm_decision`): named after Wilder's Parabolic SAR but mathematically a 1-cycle rate-of-change indicator. The cross-day `prev_return=0` reset (`database.wipe_transient_state`) is methodologically idiosyncratic: it makes the first cycle of every new day report `velocity = current_return - 0 = current_return`, so any symphony opening above the threshold (default 2.0%) auto-arms on the first cycle. This was empirically observed on 2026-05-15: 11 of 11 symphonies armed PARA on the open above 2.0%. The behavior may be intended ("any large move from baseline arms the squeeze") but is not documented as such.

**Vision-fit? PARTIAL.** Volatility scaling (20d-vol + 14d-ATR) is solid and serves the philosophy well. Log-time squeeze and PARA-ARM are practitioner heuristics with coherent rationales but no formal anchor — they live or die by empirical evaluation, which the 125-day calibration window cannot deliver with high confidence.

**Non-expert pitch:**
> "The bot scales its trailing-stop distance by recent volatility — a 20-day rolling estimate, the most common choice in textbooks. If the portfolio has been volatile, the stop is wider (more room to breathe); if quiet, the stop is tighter. Two extra adjustments sit on top: (1) the stop *tightens* through the trading day on a logarithmic curve — wider at the open, tighter at the close — on the rationale that less time remains to recover from a drawdown; (2) if a price moves quickly (the 'parabolic squeeze'), the stop *tightens further* to lock in the move. The volatility scaling is textbook-standard. The two adjustments are AlphaBot-specific shapes — they have a coherent story but no published study has tested these exact curves; that empirical question is open."

**Reference:**
- Andersen, T. G. & Bollerslev, T. (1997). "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. (20-day vol foundation; also documents intraday vol U-shape.)
- J.P. Morgan / Reuters (1996). *RiskMetrics Technical Document,* 4th ed. (Establishes 20/30/60/250-day vol windows.)
- Wilder, J. W. (1978). *New Concepts in Technical Trading Systems,* Trend Research. (14-day ATR; Parabolic SAR.)
- Kestner, L. (2003). *Quantitative Trading Strategies.* (ATR-based stops backtest across 15 futures markets.)
- No published precedent for the `log10(1 + 9*t)` curve or the day-boundary velocity reset.

**Code refs:** `math_engine.py:903-960` (20d-vol), `math_engine.py:155-167` (time-squeeze constants), `math_engine.py:211-242` (`compute_time_squeeze_decay`), `math_engine.py:185-208` (`compute_para_arm_decision`).

**Empirical evidence:** `[Theoretical / Tier-1]` for 20d-vol. `[Out-of-sample]` for ATR-based stops (Kestner 2003, single-author, not peer-reviewed). `[Folklore]` for the specific log-time-squeeze curve and the PARA-ARM day-boundary reset.

**Replication status:** 20d-vol and 14d-ATR are universally replicated. The log-time-squeeze curve and PARA-ARM day-reset are single-source.

**Regime sensitivity:** 20d-vol *lags* regime shifts (a known property; EWMA λ=0.94 is more responsive). Time-squeeze is calendar-only — on a high-vol day, the stop tightens through midday even if intraday vol is rising into close. PARA-ARM is most vulnerable on gap-open days.

---

## Cross-surface coherence

The math layers are **largely coherent** with each other:

1. **CRRA-EU → BHY haircut chain (Surfaces 1, 5, 6, 7)** is internally consistent. The objective is a mean-valued utility; its t-stat uses the correct mean-form (`mean(U) / (sd(U)/sqrt(T))`); the BHY haircut applies the right multiple-testing correction (Yekutieli for dependent trials); N_effective accounts for researcher DoF additively; NN1 structurally rules out P&L-frozen disciplines. This is the audit-strongest end-to-end pipeline.

2. **CVaR diagnostic ↔ MC gating (Surfaces 2, 3)** share the same kNN regime-matching pool — by design, per `math_engine.py:1226-1296`. This is good: a consistent regime-locality estimator across both outputs avoids the failure mode where MC says "we're fine" while CVaR shows a fat tail. Both ARE limited by the same eligible-pool boundary (39 raw days) and by the same regime-match-quality gap (neither thresholds the distance-to-nearest-neighbor).

3. **Exit priority (Surface 4) ↔ MC gating (Surface 3)** are coherent: `is_trailing_stop_hit` already incorporates the MC confirmation gate via `compute_exit_confirmation`. The priority resolver does not need to know about MC; the input flag is already MC-adjusted upstream. Clean separation.

4. **Vol-scaling + log-time + PARA-ARM (Surface 8) → trailing-stop level (consumed by Surface 4)** is coherent in *direction* but stacks practitioner heuristics. `compute_active_trailing_stop` applies `parabolic_squeeze_multiplier` exactly once (not twice) when both `para_armed` and `breakeven_locked` are True (`math_engine.py:287-288`). That guard is correct but the stacked-heuristic *interaction* (PARA + breakeven + time-squeeze all aligning in late-day) creates a tight-stop regime where a noise spike triggers a premature exit. The math is correct; the *composition* is the most overfit-exposed surface.

5. **NN1 spec-freeze (Surface 6) ↔ everything that feeds the autotuner**: NN1 enforces that the search space's bounds were frozen by an honest discipline. This includes the practitioner constants in Surface 8 — the log-time-squeeze parameters and the PARA threshold default. The NN1 discipline for these is `CALIBRATION` (fitted to historical without optimization target) or `STYLIZED_FACT` — both honest disciplines, but the underlying *shape* of the curve is `[Folklore]`. NN1 cannot rescue a curve shape that was never well-anchored to begin with; it can only enforce that the *parameters* of an existing shape are frozen honestly. This is a structural limitation of NN1, not a bug.

---

## Critique — what I would change if I could

In rough priority order for the user's "accuracy + informed decisions" mandate:

1. **Add a regime-match-quality guard to MC gating and CVaR (highest leverage).** When the mean kNN distance to the 150 nearest neighbors exceeds a threshold, MC defaults to "allow exit" (fail-safe) and CVaR returns `cvar_pct=None` with `insufficient_reason="regime unprecedented"`. This closes the most acute failure mode for the user's mandate: the bot most confidently veto-blocking exits during a regime break, exactly when the historical conditional CDF is least informative.

2. **Document the log-time-squeeze rationale in the README's math section.** Today the risk-budget framing lives only in source comments. An operator looking at the dashboard cannot trace "why does the stop tighten through the day?" to a story they can follow. The non-expert pitch above (Surface 8) is the kind of content that belongs in user-facing docs.

3. **Decide and document the PARA-ARM day-boundary semantic.** The current behavior — `prev_return=0` reset at every new day so first-cycle velocity is `current_return` — auto-arms PARA on any symphony opening above threshold. This may be intended ("large overnight move arms the squeeze") or unintended ("velocity signal should not include the open gap"). The math review cannot decide; the operator must.

4. **Pin the priority resolver's order with a non-circular argument in the README.** Today the order is `VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop` and the justification is implicit. The defensible argument is "fastest hard-cut first, slowest momentum-respecting cut last." That sentence should be in the README's math section.

5. **Surface the BHY haircut's failure mode to the operator dashboard.** When the autotuner refuses to deploy because the haircut rejected all trials, the operator should see "autotuner refused deployment: best raw Sortino 1.4, BHY-corrected threshold 1.7 at N=500" — this is the user's "informed decisions" mandate operationalized. (The decision-science Phase-2 plans `overfitting-conscience` and `divergence-explainer` appear to do this.)

6. **Decide whether the calibration window stays at 125 days.** At 125 days with purge=20 at both fold boundaries, the validation and frozen-eval windows are ~4-5 days each. This is acknowledged in `autotuner.py:1301-1307`. The methodologically-correct fix is more history (250-day or 500-day window); the cost is operator-choice on the staleness tradeoff (more history = older regimes contaminate today's calibration). This is a design question for the user, not a math bug.

### The weakest math link

**The 125-day calibration window combined with practitioner-heuristic curve shapes (log-time-squeeze + PARA-ARM).** The vol-scaling foundation is solid. The BHY haircut + NN1 + CRRA-EU + CVaR pipeline is solid. The MC gating has a known failure mode (regime breaks) that is documented but not yet mitigated with a regime-match-quality guard. But the *composition* of unanchored curve shapes + a small calibration window + the BHY haircut produces a situation where the haircut can correctly reject overfit trials AND simultaneously be unable to certify any trial as skill — i.e., the autotuner can become a system that mostly says "no" without giving the operator a path forward. This is not a bug; it is a structural property of the chosen window size. Surfacing it to the operator (via the autotuner's refusal explanation) is the right mitigation.

### Acknowledging the prior math re-audit

The 2026-05-27 math re-audit (referenced in the brief) verified numerical correctness — the formulas compute what they claim to compute. This soundness review takes that as given and asks the orthogonal question: do those correctly-implemented formulas serve the user's stated purpose? The answer is YES for surfaces 1, 2, 4, 5, 6 unambiguously; PARTIAL for surfaces 3, 7, 8 with the soft spots above. None of these soft spots are flaws that operators should not trust the bot for the user's stated purpose — but each carries an *informed-decision implication* the operator should know about.

---

## Hand-off to doc-writer

For each surface, the non-expert pitch + the published reference + the code anchor below become the README's math section content. Paste-ready blocks:

### Block A — CRRA-EU utility
- **Pitch:** see Surface 1.
- **Reference:** Pratt (1964) *Econometrica*; Merton (1969), Samuelson (1969) *RES*.
- **Code:** `math_engine.py:1348-1404`; `autotuner.py:367-400`.

### Block B — CVaR diagnostic
- **Pitch:** see Surface 2.
- **Reference:** Rockafellar-Uryasev (2000) *J. of Risk*; Acerbi-Tasche (2002) *JBF*.
- **Code:** `math_engine.py:120-152`, `math_engine.py:1185-1345`.

### Block C — Monte Carlo gating
- **Pitch:** see Surface 3.
- **Reference:** Glasserman (2003) *Monte Carlo Methods in Financial Engineering* (Springer); Kaminski & Lo (2014) *JFM* for the regime caveat.
- **Code:** `math_engine.py:772-900`.

### Block D — 6-layer exit priority
- **Pitch:** see Surface 4.
- **Reference:** Wilder (1978) *New Concepts in Technical Trading Systems* (trailing-stop family); priority order is AlphaBot-defined.
- **Code:** `math_engine.py:720-759`.

### Block E — BHY haircut + N_effective
- **Pitch:** see Surface 5.
- **Reference:** Benjamini-Hochberg-Yekutieli (2001) *Annals of Statistics*; Harvey & Liu (2015) *JPM*; Bailey & López de Prado (2014) *JPM*.
- **Code:** `autotuner.py:424-476`, `autotuner.py:489-539`.

### Block F — NN1 spec-freeze discipline
- **Pitch:** see Surface 6.
- **Reference:** López de Prado (2018) *Advances in Financial Machine Learning,* Ch. 11; Bailey et al. (2014) *J. of Computational Finance.*
- **Code:** `autotuner.py:73-90`, `autotuner.py:1247+`.

### Block G — Walk-forward calibration
- **Pitch:** see Surface 7.
- **Reference:** López de Prado (2018) *AFML* Ch. 7; Pardo (2008) *The Evaluation and Optimization of Trading Strategies.*
- **Code:** `autotuner.py:1283-1311`.

### Block H — Volatility scaling / time squeeze / parabolic ratchet
- **Pitch:** see Surface 8.
- **Reference:** Andersen & Bollerslev (1997) *J. of Empirical Finance*; JP Morgan RiskMetrics (1996); Wilder (1978). No reference for the `log10(1 + 9*t)` curve or the PARA day-boundary reset.
- **Code:** `math_engine.py:155-167`, `math_engine.py:185-242`, `math_engine.py:903-960`.

---

## Open questions for the doc-writer

1. Should the README state the gamma default value (typically γ=2) and where it lives (Optuna search space lower bound vs operator-set default)? — math-soundness scope says yes; precise number is a doc-writer + code-reader call.
2. Should the README distinguish "the math is sound" from "the calibration window is statistically thin" in operator-facing language? — recommended yes; the user's mandate is informed decisions, and conflating these undersells the soft spot.
3. Should the priority-resolver order's rationale ("fastest hard-cut first") be code-comment or README-only? — the doc-writer should decide based on consistency with how the engine's other order decisions are documented.

---

## Hard-rule compliance log

- **Read-only.** This document is the only artifact authored; no production code touched.
- **Worktree only.** Written to `docs/audit/vision-audit-2026-05-27/math-soundness.md` in the `audit/vision-audit` worktree.
- **Cited.** Every claim with a non-`[Folklore]` empirical grade names a published source.
- **Call-path verified.** Every code reference cites a specific file + line range that exists in the worktree as of HEAD. Verified via `Grep`/`Read` during this session.
- **Non-expert language.** Pitches avoid unexplained jargon. Where γ, t-stat, CVaR, BHY appear, they are explained inline at first use.
- **PM commits.** Per `feedback_researcher_agents_lack_bash_pm_ferries_commits`, this file is staged but not committed by this agent.
