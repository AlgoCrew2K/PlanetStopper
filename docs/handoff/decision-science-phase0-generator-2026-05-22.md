# Decision-Science Phase 0 — Path Generator, CVaR Sampling & Horizon Convention

**Researcher:** quant-risk-researcher
**Date:** 2026-05-22
**Scope:** Literature findings only. No implementation recommendation. Architecture-provenance / institutional-adoption is a sibling task — not covered here.
**Question owner:** EUT+CVaR position-exit engine, Phase 0 specification.

> **HARD SCOPE NOTE.** This report surveys what the literature establishes, widely practices, or treats as folklore. It does **not** recommend a generator, a block length, a horizon, or an alpha. Where the project has a stated preference (reuse the existing kNN MC), the report assesses that option on its merits alongside the others and surfaces trade-offs — it does not endorse it.

---

## 0. Grounding: what AlphaBot's existing `run_monte_carlo` actually is

Read directly from `math_engine.py` (lines 705-833) for accurate grounding, not assumption:

- **It is a single-day return resampler, not a forward-path simulator.** Each "path" is **one i.i.d. draw** (`rng.choice(nearest_day_returns, size=simulation_paths)`) from a pool of kNN-matched **single-day** portfolio returns. There is no time axis inside a path — `simulation_paths` (default ~5000) is the number of one-day outcomes drawn, not the number of multi-step trajectories.
- **The kNN match is a regime conditioner, not a dynamics model.** Neighbours are selected by Euclidean distance on two standardized SPY features (today's return, 20-day rolling vol). The 150-neighbour pool (`MC_DEFAULT_NEIGHBOR_K`) is then sampled with replacement.
- **Its output is a probability, not a distribution of P&L paths.** `run_monte_carlo` returns `P(return > current_symphony_return)` — a scalar exceedance probability used today as an exit *confirmation* gate, with an out-of-band `None` sentinel for insufficient history.
- **Eligible-pool sizing:** sufficiency is judged on `len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)` against `MC_MIN_HISTORY_DAYS`; raw history floor is 39 days (per project memory `project_mc_eligible_pool_vs_raw_day_boundary`).

**Interpretation (labelled):** The exit engine's stated need — "forward-path simulation good enough to support a 5% CVaR budget on intraday-to-days horizons" — is **not** what the current function does. CVaR over a multi-day horizon needs a *distribution of horizon-cumulative P&L*, which requires either (a) multi-step paths or (b) a horizon-aggregated return distribution. The current function produces neither. "Extending the existing kNN regime MC" is therefore not a parameter change; it is adding a path/horizon-aggregation layer the function does not currently have. This distinction governs the entire generator comparison below.

---

## 1. Path-Generator Comparison

Five candidates assessed on six axes. Each empirical claim is graded `[Theoretical] / [Backtest] / [Out-of-sample backtest] / [Live evidence] / [Folklore]`.

### 1.1 Extend the existing kNN-regime MC

**Mechanic to add:** to produce a horizon-H distribution you would either (a) draw H consecutive single-day returns and compound, or (b) match neighbours on a multi-day window and resample H-day blocks. Option (a) reintroduces the i.i.d. assumption *within* a path; option (b) is a regime-conditioned block bootstrap (see 1.2).

| Axis | Assessment |
|---|---|
| Tail fidelity | Bounded by the empirical pool. Cannot produce a loss worse than the worst neighbour-day return. For a 5% CVaR on a 150-neighbour pool, the tail average rests on ~7-8 observations. `[Theoretical]` — this is the standard historical-simulation limitation (McNeil-Frey 2000; EVT-vs-HS literature). |
| Vol-clustering preservation | **Partially, and only across paths — not within them.** The kNN feature set *conditions* the draw pool on a recent-vol regime, so the pool inherits regime-appropriate dispersion. But i.i.d. resampling inside a multi-day path destroys autocorrelation of squared returns (the actual signature of clustering). `[Theoretical]` |
| CVaR-budget support | Yes for a *single-day* CVaR off the pool; for multi-day it depends entirely on the path-construction choice and degrades as H grows. `[Theoretical]` |
| Data requirements | Already satisfied — 3-year rolling pool, 39-day floor. Lowest of all five. |
| Compute cost | Lowest. Vectorized `argpartition` + `rng.choice`; already on the live minute path. |
| Degrees of freedom introduced | Low-to-moderate: neighbour count k, feature set, distance metric, plus (new) horizon-aggregation rule and any multi-day window. |

**Replication status:** The *generic* technique (regime/state-conditioned historical resampling, sometimes "conditional historical simulation") is widely studied; AlphaBot's specific 2-feature kNN variant is bespoke and **not independently replicated**.

### 1.2 Block bootstrap / stationary bootstrap

| Axis | Assessment |
|---|---|
| Tail fidelity | Same hard ceiling as HS — cannot exceed the worst observed block. `[Theoretical]` |
| Vol-clustering preservation | **This is its purpose.** Resampling contiguous blocks preserves short-range serial dependence including ARCH effects, *up to the block length*. Clustering spanning longer than the block is broken at block joins. `[Theoretical]` (Politis-Romano stationary bootstrap; Politis-White / Patton-Politis-White automatic block-length selection). |
| CVaR-budget support | Yes — produces horizon-cumulative P&L distributions natively by concatenating blocks to length H. |
| Data requirements | Moderate — needs a contiguous return series; works on AlphaBot's 3-year pool. |
| Compute cost | Low-moderate; higher than 1.1 because paths are multi-step. |
| Degrees of freedom | Block length (or expected block length for the stationary variant) is the dominant DoF; see §1.6 and §2 of the horizon discussion. |

**Replication status:** Heavily replicated as a general method. `arch` (Sheppard) ships `optimal_block_length`; the Politis-White (2004) + Patton-Politis-White (2009 correction) selector is the standard reference. `[Theoretical]` for the selector's optimality; method itself `[widely-practiced]`.

### 1.3 GARCH-FHS (Filtered Historical Simulation)

**Mechanic:** fit GARCH(1,1) (or asymmetric variant), divide returns by fitted conditional vol to get standardized residuals, **bootstrap the residuals**, then **re-volatize** by recursively substituting drawn residuals back into the GARCH variance equation to build multi-day paths. Origin: Barone-Adesi, Giannopoulos & Vosper (1999), *Journal of Futures Markets* 19(5):583-602; backtest analysis in Barone-Adesi & Giannopoulos (2002).

| Axis | Assessment |
|---|---|
| Tail fidelity | Strong. Standardized-residual bootstrap keeps the *empirical* (fat, skewed) innovation shape; re-volatizing lets a current high-vol state push paths beyond the worst raw historical return. `[Out-of-sample backtest]` — see §1.7. |
| Vol-clustering preservation | **Strongest of the five — it models the dynamics explicitly.** The GARCH recursion reproduces vol persistence within every path. `[Theoretical] + [Backtest]` |
| CVaR-budget support | Yes, natively multi-horizon. McNeil-Frey-style conditional approaches were designed for conditional ES. |
| Data requirements | GARCH(1,1) needs a few hundred observations for stable MLE; AlphaBot's 3-year pool is adequate for daily, marginal-to-thin for intraday bars. |
| Compute cost | **Highest.** Per-symbol GARCH MLE + recursive re-volatization per path. MLE is iterative and is the binding concern against AlphaBot's "no blocking I/O on the 1-minute execution path" constraint (project CLAUDE.md, architecture constraint 1). |
| Degrees of freedom | GARCH order/family (vanilla vs GJR/EGARCH for leverage), innovation-residual window, plus re-fit cadence. Moderate-to-high. |

**Replication status:** Independently replicated many times; FHS is a practitioner standard for clearing-house margin (origin: London Clearing House prototype, late 1990s).

### 1.4 EVT-augmented (conditional EVT / GARCH-EVT)

**Mechanic:** McNeil & Frey (2000), *Journal of Empirical Finance* 7:271-300 — fit GARCH for conditional vol, then fit a **Generalized Pareto Distribution (POT)** to the tail of the standardized residuals instead of bootstrapping them empirically.

| Axis | Assessment |
|---|---|
| Tail fidelity | **Highest in principle.** GPD lets VaR/ES *extrapolate beyond the worst observed loss* — the one thing HS, block bootstrap, and FHS-on-empirical-residuals cannot do. `[Out-of-sample backtest]` — McNeil-Frey report conditional EVT beating unconditional EVT and plain GARCH on ES backtests. |
| Vol-clustering preservation | Same as FHS — the GARCH stage handles it. |
| CVaR-budget support | Designed precisely for conditional ES. |
| Data requirements | Highest. GPD fit needs enough threshold exceedances; with only ~3 years (~750 daily obs) the tail sample for a stable shape parameter is thin. `[Theoretical]` — ES estimation sample size scales ~O(1/(1-α)²) and worsens with tail heaviness. |
| Compute cost | Highest — GARCH MLE + GPD threshold selection + GPD MLE. |
| Degrees of freedom | **Most of any candidate:** GARCH spec + POT threshold + GPD shape/scale + tail-fraction. Threshold choice is itself an unresolved bias/variance trade-off in the EVT literature. |

**Replication status:** McNeil-Frey is one of the most replicated results in market-risk; conditional EVT consistently ranks at/near top on ES backtests across follow-up studies.

### 1.5 Student-t parametric

**Mechanic:** assume horizon returns are Student-t (optionally skewed-t), estimate df/scale, compute or simulate CVaR in closed-ish form.

| Axis | Assessment |
|---|---|
| Tail fidelity | Better than Gaussian (captures kurtosis) but imposes a symmetric, parametric tail; misses skew unless skewed-t. `[Theoretical]` |
| Vol-clustering preservation | **None** unless paired with a GARCH vol model (then it is just GARCH-t, a special case of 1.3 with a parametric rather than bootstrapped innovation). Standalone Student-t is i.i.d. `[Theoretical]` |
| CVaR-budget support | Yes, and CVaR has a closed form for Student-t — cheapest possible CVaR. |
| Data requirements | Lowest — a few hundred points estimate df adequately. |
| Compute cost | Lowest — closed-form or trivial simulation. |
| Degrees of freedom | Lowest — essentially df + scale (+ skew). |

**Replication status:** Long-established; the arXiv 2505.05646 comparison and the broader literature consistently find pure-Gaussian/pure-parametric **underestimates tail risk** vs FHS/EVT. `[Backtest]`

### 1.6 Direct head-to-head: does extending the kNN MC compete with GARCH-FHS?

The project's stated preference is reuse. The literature gives a qualified, honest answer:

- **On volatility-clustering preservation, GARCH-FHS is structurally superior and the gap is not closeable by tuning.** FHS *models* the conditional-variance recursion; kNN resampling *conditions on* a recent-vol snapshot and then samples i.i.d. The two are not the same thing. A regime-conditioned i.i.d. resampler can land paths in roughly the right dispersion regime but cannot reproduce within-path persistence (autocorrelated squared returns). `[Theoretical]` — this is a structural, not empirical, distinction.
- **The gap shrinks as the horizon shrinks.** For a **1-day** CVaR the within-path dynamics are moot — there is no second step — and a regime-conditioned empirical resampler is much closer to FHS in practice. For intraday-to-1-day horizons the kNN approach is genuinely competitive on the *non-clustering* axes. The competitiveness erodes as H grows to multiple days. `[Interpretation]` grounded in the FHS multi-day mechanic (re-volatization is what FHS adds and what i.i.d. compounding lacks).
- **A middle path exists and is in the literature.** "Conditional / regime-switching historical simulation" and **regime-conditioned block bootstrap** (match the regime, then resample contiguous blocks rather than single days) recover *some* clustering without fitting GARCH. This is closer to "extend the kNN MC" than to "adopt FHS" and is a recognised family — but it is **less validated than FHS** and AlphaBot's specific 2-feature kNN variant has no independent backtest record.
- **Tail-fidelity ceiling is shared.** kNN MC, block bootstrap, and FHS-on-empirical-residuals all inherit the empirical worst-case ceiling. Only EVT-augmentation breaks it. Re-volatizing in FHS *partially* breaks it (current high vol scales an old residual up) but cannot invent a residual shape never seen.

**Conflict to surface (do not silently resolve):** The arXiv preprint (2505.05646, **not peer-reviewed**, `[single-source]`) reports HS breach rates of ~91% at 5% VaR — an implausible figure that strongly indicates a sign/window-convention artifact in that specific study, **not** a property of historical simulation. The well-replicated literature (Kuester-Mittnik-Paolella 2006; McNeil-Frey 2000) finds HS *under*-reacts to vol changes (clustered exceedances), not that it breaches 91% of the time. **Treat the 91% figure as unreliable; treat the directional ranking FHS > GARCH-N > naive-HS as the corroborated finding.**

**Net (literature-grounded, no recommendation):** GARCH-FHS is the better-validated multi-day generator on clustering and (with EVT) tail extrapolation. Extending the kNN MC is competitive specifically at the **short end of the horizon range** the exit engine targets, at far lower compute and zero new dependencies, but is **not** a like-for-like substitute for FHS at multi-day horizons and carries no independent validation record. The decision is a genuine trade-off, not a dominated choice.

### 1.7 Regime sensitivity (where each generator fails)

- **All empirical-resampling methods (kNN MC, block bootstrap, FHS):** fail when the *future* tail event has no analogue in the pool — structural breaks, first-of-kind shocks. FHS's re-volatization mitigates magnitude but not novel shape.
- **kNN MC specifically:** degrades when today's regime is itself unprecedented (few/no genuine neighbours → distance match is spurious); thin 39-day floor makes early-life symbols fragile.
- **GARCH-FHS:** GARCH MLE can be unstable in low-vol or very short samples; vol-of-vol spikes and overnight gaps violate the smooth-recursion assumption; intraday seasonality (open/close vol U-shape) is not captured by vanilla GARCH.
- **EVT-augmented:** POT threshold instability with thin tail samples; GPD shape parameter has wide CIs on ~3 years of data.
- **Student-t parametric:** fails in any regime with asymmetric crashes (symmetric tails) and in all clustered-vol regimes if used without a GARCH layer.
- **Universal:** intraday gap risk and low-volume sessions (lunch lull, half-days) break the i.i.d.-bar and stationary-distribution assumptions every method other than an explicitly intraday-seasonal model relies on.

---

## 2. CVaR Tail-Sample Sufficiency

**Question:** how many paths for a stable 5% CVaR, and how much do variance-reduction techniques cut that.

### 2.1 Why CVaR is harder to simulate than VaR

`[High]` — multi-source. CVaR/ES at level α is the *mean of the worst (1-α) fraction* of outcomes. With N paths at α=5%, only ~`(1-α)·N` paths inform the estimate: **5000 paths → ~250 tail paths; the CVaR is an average over those 250.** Estimation difficulty:

- ES is a **rare-event / tail-average** estimation problem; standard MC converges slowly there. (Springer *Comp. Mgmt. Science* 2015, "On variance reduction of mean-CVaR Monte Carlo estimators"; arXiv 2106.10236 on black-box IS for VaR/CVaR.)
- Required sample size scales **~O(1/(1-α)²)** and **worsens with tail heaviness** — the relative standard error of ES is materially larger than that of VaR for fat-tailed losses. `[Theoretical]` (ES asymptotics literature; Zwingmann-Holzmann 2016 on ES asymptotic normality).
- **Implication for AlphaBot:** the current ~5000 single-day draws give ~250 tail observations for a 5% estimate — adequate for a *point* exceedance probability, **marginal** for a *stable* tail-average CVaR, and the stability degrades further for fat-tailed symbols or multi-day horizons where each path is itself noisier.

### 2.2 Rough sufficiency by generator (literature-anchored, not a recommendation)

There is **no universal "X paths" answer** — every source surveyed states required N depends on α, target accuracy, and tail heaviness. What the literature does establish:

- **Empirical-pool methods (kNN MC, block bootstrap):** the binding constraint is **not** the number of draws but the **number of distinct underlying observations in the tail.** Drawing 50,000 times from a 150-neighbour pool does not add tail information beyond the ~7-8 genuine sub-5% neighbour-days — it only reduces *resampling* noise, not *estimation* noise. This is a structural ceiling: more paths ≠ more tail fidelity once the pool is exhausted.
- **Parametric / FHS / EVT:** more paths *do* buy accuracy because the generator can produce novel tail values. Here crude-MC needs **tens of thousands** of paths for a stable 5% CVaR to a few-percent relative error (consistent with O(N^-1/2) convergence and O(1/(1-α)²) scaling), and that is where variance reduction pays off.

### 2.3 Variance-reduction techniques and their effect on path count

| Technique | Effect | Evidence grade | Notes |
|---|---|---|---|
| **Antithetic variates** | Modest, reliable variance cut (commonly cited ~2x effective-sample improvement for smooth, monotone payoffs; less for non-monotone tail functionals). Cheap to add. | `[Theoretical]` + widely practised | Hammersley-Morton; helps less for the indicator/tail-average structure of ES than for smooth payoffs. |
| **Importance sampling (IS)** | **Largest gains for CVaR specifically** — IS is the canonical rare-event accelerator; tilts sampling toward the loss tail so far more paths land below VaR. Order-of-magnitude variance reduction reported. | `[Theoretical]`, multi-source | Springer 2015 (mean-CVaR estimators); arXiv 2106.10236 (black-box IS for VaR/CVaR); arXiv 0812.3381 (stochastic-approx + unconstrained IS). **Cost:** requires a good tilt; a bad tilt can *increase* variance. |
| **Quasi-Monte Carlo (QMC / RQMC)** | Asymptotically better than MC: VaR error O(N^-1/d); ES MSE o(N^-1) under conditions, up to ~O(N^-1-1/(2d-1)+ε) under stronger conditions — vs MC's O(N^-1/2). | `[Theoretical]`, peer-reviewed | arXiv 1706.00540 (He, convergence analysis of QMC for quantile & ES). **Caveat:** the advantage decays as dimension d (here ≈ horizon length × #assets) grows; strongest for short horizons / few assets. |
| **Control variates / conditional MC** | Useful where a correlated cheap-to-price quantity exists. | `[Theoretical]` | Less commonly applied to multi-asset path CVaR. |

**No source surveyed quantifies a universal "VR cuts path count by factor F."** The honest summary: IS gives the biggest, most CVaR-relevant reduction (potentially order-of-magnitude) but needs design effort and can backfire; antithetic variates give a modest, safe, near-free improvement; QMC helps most at low dimension (short horizon, few assets) and degrades as the path dimension rises. `[Medium]` confidence on the *direction* of all three; `[Low]` on any specific multiplier.

**Replication status:** VR techniques themselves are textbook (Glasserman, *Monte Carlo Methods in Financial Engineering*). The specific CVaR-IS schemes (Springer 2015, arXiv 2106.10236) are individual papers — `[Medium]`, single-paper each until cross-replicated.

---

## 3. The Horizon-Convention Problem

**Question:** re-running a fixed-horizon expected-utility comparison every minute is time-inconsistent. Survey the fixes.

### 3.1 The problem, stated precisely

`[High]` — multi-source. A **fixed-horizon** objective ("maximize E[U(W_{t+H})]" for a constant H) re-evaluated every minute is **time-inconsistent**: at 10:00 the engine optimizes for 10:00+H; at 10:01 it optimizes for 10:01+H — a *different* terminal date. The policy chosen at 10:00 is not the policy the 10:01 problem would endorse. The market-risk literature names this directly: "if, for every state, one wants to minimize the CVaR of a quantity at the *end of the planning horizon*, this results in a time-inconsistent optimal policy." (Survey: probability-risk.springeropen.com 2017, LM-measure perspective; Boda & Filar and the dynamic-CVaR literature.)

Three recognised fix families:

### 3.2 Fix family A — Horizon-unbiased / forward performance utilities

**Sources:** Henderson & Hobson (2007), "Horizon-unbiased utility functions," *Stochastic Processes and their Applications* 117(11):1621-1641; Musiela & Zariphopoulou (2007+), "forward performance processes"; Choulli et al. (2007).

- **Idea:** construct a utility/performance process with **no preferred terminal date** — risk preferences evolve *forward* with the market rather than being pinned to a fixed T. Henderson-Hobson derived these precisely to solve optimal-time-to-sell-an-indivisible-asset, which is structurally the exit-timing problem.
- **Trade-off:** mathematically elegant and *directly* removes the horizon artifact — the optimality of an exit decision does not depend on an arbitrary H. But the class of admissible horizon-unbiased utilities is **restrictive** (not every utility shape admits a forward extension), construction is technical, and there is **little-to-no live-trading validation** — the literature is theoretical. `[Theoretical]`, `[Medium]` confidence the class exists and is well-defined; `[Unverified]` for practical exit-engine deployment.

### 3.3 Fix family B — Rolling-horizon convention (accept inconsistency, control it)

- **Idea:** keep a fixed look-ahead H but treat the per-minute re-solve as a **receding-horizon / model-predictive-control** scheme — re-optimize each minute, act only on the immediate decision, slide the window. This is what the engine does *now* implicitly.
- **Trade-off:** simplest, no new theory, transparent. But it **does not fix** time-inconsistency — it *manages* it. Known pathologies: the chosen exit can oscillate as the window slides; the policy is not the solution of any single well-posed dynamic program. The receding-horizon literature shows it is often acceptable in practice if the horizon is long relative to decision cadence and the objective is not pathologically horizon-sensitive. `[Theoretical]` for the inconsistency; `[widely-practiced]` as an engineering convention.

### 3.4 Fix family C — Time-consistent dynamic risk measures (nested / iterated CVaR)

**Sources:** Ruszczyński (2010), "Risk-averse dynamic programming for Markov decision processes"; Ruszczyński & Shapiro (conditional risk mappings); Cheridito-Delbaen-Kupper (2006); Detlefsen-Scandolo (2005); Kovacevic-Pflug (2009).

- **Idea:** replace the single end-of-horizon CVaR with a **nested / iterated (composite) CVaR** — recursively apply one-step conditional CVaR mappings backward. The resulting dynamic risk measure satisfies **time-consistency by construction** and admits a Bellman recursion, so the per-step decision *is* the dynamic-program-optimal one.
- **Trade-off:** this is the theoretically principled fix — it makes per-minute re-solving *coherent* rather than merely tolerable. But **iterated CVaR ≠ end-of-horizon CVaR**: the nested measure is generally **more conservative** and its "effective alpha" over the full horizon is not the nominal one-step alpha. So if the project's mandate is literally "a 5% CVaR budget on the H-horizon P&L," a nested measure satisfies *time-consistency* but **changes what the 5% means** — this is a genuine, unavoidable tension the literature is explicit about (you cannot have a measure that is both end-of-horizon-CVaR *and* time-consistent in general). `[Theoretical]`, well-replicated theory; `[Unverified]` for live exit-engine use.

### 3.5 Summary of the horizon trade-off

| Fix | Removes inconsistency? | Preserves "5% end-of-horizon CVaR" semantics? | Maturity for live use |
|---|---|---|---|
| A. Horizon-unbiased utility | Yes (no horizon at all) | N/A — reframes the objective away from a fixed-horizon CVaR | Theoretical only |
| B. Rolling/receding horizon | No — manages it | Yes (nominal H kept) | Practitioner-standard, pragmatic |
| C. Nested / iterated CVaR | Yes (by construction) | **No** — changes the effective risk level | Mature theory, unproven in this application |

There is **no option that is simultaneously time-consistent, keeps the literal end-of-horizon 5%-CVaR meaning, and is field-proven.** That impossibility is itself the key finding for Phase 0.

---

## 4. Freezing Specification Choices Without Backtest Selection

**Question:** how to fix generator family, block length, horizon, and CVaR alpha by reasoning *independent* of out-of-sample backtest selection.

### 4.1 Why backtest selection is the trap

`[High]` — Bailey & López de Prado, "The Deflated Sharpe Ratio" (SSRN 2460551) and Bailey-Borwein-López de Prado-Zhu, "The Probability of Backtest Overfitting" (SSRN 2326253). Choosing a spec because it produced the best backtest is **selection under multiple testing**: the winning configuration's measured performance is inflated by the search itself, and PBO shows selected configs systematically underperform the trial median out-of-sample. Tuning generator family / block length / horizon / alpha against a backtest score is exactly this failure mode.

### 4.2 Disciplines that exist for spec-freezing by independent reasoning

The literature offers four broad disciplines. None is "run a backtest and pick the winner."

1. **Theory-driven / a-priori parameter selection.** Fix each knob from a *property the data has*, not a score it produces:
   - **Block length** — Politis-White (2004) + Patton-Politis-White (2009) **automatic block-length selection** chooses the block from the *autocorrelation structure* of the series (MSE-optimal for the bootstrap estimator), independent of any strategy P&L. `[Theoretical]`, replicated, shipped in `arch`.
   - **CVaR alpha** — set by *mandate/risk-budget*, not optimization. 5% (or 1%/2.5%) are regulatory/governance conventions (Basel FRTB uses 97.5% ES). Alpha is a *policy* parameter; choosing it to flatter a backtest is a category error.
   - **Horizon** — set by the *decision cadence and position-holding reality* of the engine (intraday-to-days here), not tuned.
2. **Pre-registration of the specification.** Freeze generator family, block length, horizon, alpha **before** seeing any out-of-sample P&L; the OOS period then serves only as *confirmation*, never *selection*. Bailey-López de Prado's whole programme implies this: the OOS set must not be consumed by the search.
3. **Selection-bias correction when a search is unavoidable.** If some data-driven choice cannot be avoided, the **Deflated Sharpe Ratio** and **PBO** *quantify and discount* the inflation from N trials — turning "best backtest" into "best backtest, deflated for having looked at N candidates." This does not make backtest-selection safe, but it makes the residual evidence honest. `[Theoretical]`, peer-reviewed-adjacent (SSRN, widely cited).
4. **Statistical-property targets instead of P&L targets.** Freeze the generator by how well it reproduces *model-free stylized facts* — fat tails, volatility clustering (ACF of squared returns), leverage effect, ES backtest calibration (unconditional + independence coverage tests, e.g. Kupiec, Christoffersen, Acerbi-Szekely for ES) — measured on the return series itself, **not** on strategy returns. A generator chosen because it passes ES coverage tests is selected on a *calibration* property, not on a *performance* score, and is far less prone to overfitting the exit strategy.

### 4.3 The honest caveat

All four disciplines reduce — they do not eliminate — researcher degrees of freedom. Politis-White still has a tuning constant; "stylized-fact fidelity" still involves judgement about which facts matter. The literature's position is that **the source of evidence must be independent of the OOS strategy P&L**, and that any unavoidable search must be *disclosed and deflated*. There is no published method that makes spec-freezing fully assumption-free.

---

## 5. Open Questions (logged, not resolved — outside this task's scope)

- **OQ-1:** Whether AlphaBot's 3-year daily pool yields enough genuine sub-5% tail observations *per regime cluster* for a stable 5% CVaR — needs a data audit, not literature.
- **OQ-2:** Whether the exit engine's horizon is genuinely fixed or itself a decision variable (changes whether fix family A or C is even applicable).
- **OQ-3:** Intraday-bar GARCH stability and intraday vol-seasonality handling — the surveyed FHS/EVT literature is overwhelmingly daily-frequency; intraday FHS is thinner and not assessed here.
- **OQ-4:** Architecture provenance / institutional adoption of EUT+CVaR exit engines — explicitly the sibling task; not covered.

---

## 6. Source List

**Tier 1 — Primary (peer-reviewed / standards):**
- Barone-Adesi, G., Giannopoulos, K., Vosper, L. (1999). "VaR without correlations for portfolios of derivative securities." *Journal of Futures Markets* 19(5):583-602. https://onlinelibrary.wiley.com/doi/abs/10.1002/(SICI)1096-9934(199908)19:5%3C583::AID-FUT5%3E3.0.CO;2-S
- McNeil, A.J., Frey, R. (2000). "Estimation of tail-related risk measures for heteroscedastic financial time series: an extreme value approach." *Journal of Empirical Finance* 7:271-300. https://www.sciencedirect.com/science/article/abs/pii/S0927539800000128
- Henderson, V., Hobson, D. (2007). "Horizon-unbiased utility functions." *Stochastic Processes and their Applications* 117(11):1621-1641. https://warwick.ac.uk/fac/sci/statistics/staff/academic-research/hobson/publications/horizonu.pdf
- He, Z. (2017/2019). "Convergence analysis of quasi-Monte Carlo sampling for quantile and expected shortfall." arXiv:1706.00540 (peer-reviewed version *Mathematics of Computation*). https://arxiv.org/abs/1706.00540
- Ruszczyński, A. (2010). "Risk-averse dynamic programming for Markov decision processes." *Mathematical Programming* 125:235-261. https://www.researchgate.net/publication/292781036
- Survey: "A survey of time consistency of dynamic risk measures and dynamic performance measures in discrete time." *Probability, Uncertainty and Quantitative Risk* (2017). https://probability-risk.springeropen.com/articles/10.1186/s41546-017-0012-9
- Politis, D.N., White, H. (2004) + Patton, A., Politis, D.N., White, H. (2009 correction). "Automatic Block-Length Selection for the Dependent Bootstrap." https://www.researchgate.net/publication/227357033

**Tier 2 — Expert (named authors, working papers, standard tooling):**
- Bailey, D.H., López de Prado, M. "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." SSRN 2460551. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey, D.H., Borwein, J., López de Prado, M., Zhu, Q.J. "The Probability of Backtest Overfitting." SSRN 2326253. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- "On variance reduction of mean-CVaR Monte Carlo estimators." *Computational Management Science* 12(2):221-242 (2015). https://link.springer.com/article/10.1007/s10287-014-0225-7
- arch documentation — `optimal_block_length` (Sheppard). https://arch.readthedocs.io/en/latest/bootstrap/generated/arch.bootstrap.optimal_block_length.html

**Tier 3 — Community / preprint (date-flagged, not peer-reviewed):**
- "Efficient Black-Box Importance Sampling for VaR and CVaR Estimation." arXiv:2106.10236. https://arxiv.org/pdf/2106.10236
- "Computation of VaR and CVaR using stochastic approximations and unconstrained importance sampling." arXiv:0812.3381. https://arxiv.org/pdf/0812.3381
- Zwingmann, T., Holzmann, H. "Asymptotics for the expected shortfall." arXiv:1611.07222. https://arxiv.org/pdf/1611.07222

**Tier 5 — Unverified / flagged (do NOT cite for important claims):**
- "Comparative Evaluation of VaR Models: Historical Simulation, GARCH-Based Monte Carlo, and Filtered Historical Simulation." arXiv:2505.05646 (2025, **not peer-reviewed**). https://arxiv.org/html/2505.05646v1 — **`[single-source]`, contains an implausible 91% HS breach rate that indicates a methodology artifact.** Used only for the *directional* FHS > GARCH-N > naive-HS ranking, which is corroborated by Tier-1 sources.
