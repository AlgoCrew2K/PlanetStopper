# Decision-Science Research Report: Expected-Utility Theory + CVaR for Dynamic Position-Exit Decisions

**Date:** 2026-05-22
**Author:** quant-risk-researcher (decision-science specialization)
**Scope:** Literature findings only. This report does NOT recommend an implementation path for AlphaBot. It evaluates the academic and practitioner record behind a proposed migration: replace a heuristic exit stack with a per-minute model that simulates forward return paths, compares expected utility of holding vs exiting, and exits when CVaR breaches a budget (two tuned parameters: risk aversion gamma, tail-risk budget lambda).

**Hard scope note:** Per agent charter, where the literature offers options with trade-offs, both sides are surfaced; no "do X" is issued. Conflicting sources are flagged, not silently resolved.

---

## Executive Digest

- **Utility-based exit comparison is theoretically sound but NOT free of structural pitfalls.** The "compare expected utility of holding vs exiting" framing is a well-posed optimal-stopping problem with a substantial literature (Henderson & Hobson and successors). However, the literature documents a specific, named failure mode — **horizon dependence / time-inconsistency** — that a naive fixed-horizon utility comparison run every minute will exhibit unless the utility function is explicitly horizon-unbiased. This is not folklore; it is a published structural result. `[High]`
- **CVaR is a genuinely coherent risk measure** (Artzner et al. 1999; Rockafellar & Uryasev 2000/2002) and is defensible as a tail trigger in principle. `[High]`
- **The crux — tail estimation — is where the proposal is most exposed.** CVaR at the 5% tail has materially higher estimation error than VaR, the gap *widens* for fat-tailed distributions, and the published practitioner benchmark (Yamai & Yoshiba) is **~1,000 tail-relevant observations** for ES accuracy to converge with VaR. A per-minute MC exit engine inherits this directly: the CVaR estimate is only as trustworthy as the path generator's tail, and a thin tail produces a confidently wrong budget check. `[High]`
- **Path-generator choice dominates everything.** GBM/Gaussian *understates* the tail and will make a CVaR budget systematically permissive. Student-t and bootstrap methods capture fat tails better; IID bootstrap destroys volatility clustering (understating sustained-drawdown risk); block bootstrap preserves it but needs a tuned block length. **No short-horizon equity path generator is "tail-correct" out of the box** — this is the single biggest model-risk item. `[High]`
- **"Mathematically immune to curve-fitting" is not a defensible claim.** A 2-parameter model has lower *parameter* risk than a 7-knob heuristic, but it relocates risk into *specification* risk (utility form, path-generator family, horizon, confidence level — each an unstated degree of freedom). The literature (Bailey, López de Prado et al.) treats specification choices as overfitting surface, not as exempt from it. `[High]`
- **Per-minute MC for live exits is computationally feasible** at modest path counts, and variance-reduction (antithetic variates, QMC, importance sampling) can cut required paths — but importance sampling, the technique most relevant to a 5% tail, adds its own tuning surface and failure modes under tail misspecification. `[Medium]`

---

## 1. Utility-Based Exit Decisions

### 1.1 Is "compare expected utility of holding vs exiting" a sound framework?

**Finding (Fact):** Yes, as a *formal object*. The decision "when to sell a position to maximize expected utility of terminal wealth" is a textbook **optimal stopping / mixed stopping-control problem**. The canonical treatment is Henderson & Hobson, *"Horizon-unbiased utility functions"* (Stochastic Processes and their Applications, 117(11):1621–1641, 2007) and the related asset-sale stopping problem in Evans, Henderson & Hobson, *"An explicit solution for an optimal stopping/optimal control problem which models an asset sale"* (Annals of Applied Probability, 18(5), 2008). In these models a risk-averse agent holding an indivisible asset chooses a stopping time to maximize expected utility; the optimal policy is a **time-dependent price boundary**.
- **Empirical Evidence:** `[Theoretical]` — these are continuous-time stochastic-control results, not backtests.
- **Replication Status:** The mathematical results are standard and have been extended many times (e.g., Leung & Ludkovski on optimal exit; Fabre, *"Liquidation of an indivisible asset with independent investment,"* Mathematical Finance 28, 2018). Independent extension: Yes. Independent *empirical* validation of utility-exit *outperformance*: Unknown / not found.
- **Regime Sensitivity:** The clean results assume a known diffusion (often GBM) for the asset. Drift uncertainty changes the boundary materially (Ekström & Lu, *"Optimal liquidation of an asset under drift uncertainty,"* 2015) — the optimal policy becomes belief-dependent.

**Interpretation:** The framing is sound *as a decision-theoretic object*. What the literature does NOT supply is evidence that a utility-comparison exit *beats a well-tuned heuristic in live equity/ETF trading*. The papers solve for optimality under an assumed price model; they do not claim the assumed model is correct. The soundness is conditional on the path model — see Section 3.

### 1.2 CRRA vs CARA and the gamma parameter

**Finding (Fact):** The two standard one-parameter utility families:
- **CARA** (constant absolute risk aversion): `u(W) = -exp(-a·W)`. Risk aversion `a` is constant in wealth; the dollar amount put at risk does not scale with wealth.
- **CRRA** (constant relative risk aversion): `u(W) = W^(1-gamma)/(1-gamma)` (log utility at `gamma=1`). Relative risk aversion `gamma` is constant; the *fraction* of wealth at risk is wealth-invariant. CRRA exhibits *decreasing* absolute risk aversion.
- Both belong to the HARA (hyperbolic absolute risk aversion) class; both are used primarily for analytical tractability (Stanford CME241 lecture notes, A. Rao; MIT 14.123 notes).

**Known failure modes — directly relevant to the proposal:**

1. **Wealth-normalization sensitivity (CARA).** `[Medium]` Under CARA the decision depends on absolute wealth `W` only through the exponential; if the engine feeds it position P&L rather than total wealth, or an inconsistent wealth proxy, the effective risk aversion drifts. The literature explicitly notes CARA's lack of a wealth effect is often *unrealistic* and patched by making `a` wealth-dependent (Sciubba/ScienceDirect, *"A note on wealth effect under CARA utility,"* 2010). **Implication:** for a position-exit engine, the choice of *what wealth argument to pass* is itself a modeling decision that changes behavior — it is not a neutral normalization.

2. **CRRA scale-invariance can mask the problem — or hide a bug.** `[Medium]` CRRA's appeal is that the optimal *fraction* is wealth-independent, so a per-position decision needn't track total wealth. But this only holds if the return on the *position* is what enters utility. If the engine compares utility of "hold this position" vs "exit to cash" without a consistent wealth base, scale-invariance is broken silently.

3. **Horizon dependence — the most important documented failure mode.** `[High]` Standard fixed-horizon expected-utility maximization requires committing to an investment horizon `T`. Henderson & Hobson (2007) show that for the *asset-sale* problem, fixing an arbitrary prior horizon is "artificial," and a naive choice produces **time-inconsistent** decisions: the optimal action computed at minute *t* for horizon *T* can contradict the action computed at minute *t+1* for the same calendar target, generating churn and regret. Their fix is the class of **horizon-unbiased utility functions** — utilities `u(W,t)` constructed so the optimal stopping policy does not depend on an arbitrary horizon. A per-minute exit engine that re-runs a *fixed-horizon* utility comparison every minute is exactly the configuration this literature flags as ill-posed. **This is a specification choice the proposal must make explicit; "expected utility of holding" is undefined without a stated horizon convention.**
   - Conflicting framings to surface: Henderson & Hobson treat horizon-unbiasedness as a *requirement* for the sale problem; much of applied portfolio practice (and most CRRA textbook treatment) simply fixes `T` and accepts horizon dependence. These are not reconciled in the literature — they are different problem formulations. The proposal sits in the asset-sale formulation, where the horizon-unbiased critique applies.

4. **Empirical descriptive failure.** `[Medium]` Experimental work (Brocas et al., *"Risk Aversion in a Dynamic Asset Allocation Experiment"*) finds EUT "cannot explain contestants' decisions well," with "no evidence of CARA and only some evidence of CRRA." This is a descriptive (behavioral) finding — it does not invalidate EUT as a *normative* exit rule, but it removes any claim that the chosen utility form is empirically the "true" one.

- **Empirical Evidence for §1.2:** Utility forms — `[Theoretical]`. Descriptive failure — `[Out-of-sample experimental evidence]`. Horizon-dependence failure — `[Theoretical]`, but a hard mathematical result.
- **Regime Sensitivity:** Gamma calibrated in one volatility regime implies a different *dollar* risk tolerance in another (CRRA scales with wealth, not with volatility). Gamma is not regime-free.

### 1.3 Academic vs practitioner grounding

- **Academic:** Strong and deep for the *stopping-problem* formulation (Henderson, Hobson, Evans, Leung, Ludkovski, Fabre, Ekström). `[High]`
- **Practitioner:** Utility-based *position exit* (as opposed to utility-based *portfolio allocation*, which is mainstream) is **not a widely documented retail/practitioner pattern**. Practitioner exit literature is dominated by volatility-scaled stops, trailing stops, and time stops (Carver, *Systematic Trading*; Chan). No Tier-2 practitioner source was found endorsing a per-minute utility-comparison exit. Treat the practitioner adoption of this specific design as **`[Folklore-adjacent — low documented adoption]`**: not disproven, but not a beaten path.

---

## 2. CVaR / Expected Shortfall as a Position-Management Trigger

### 2.1 Coherence properties

**Finding (Fact):** `[High]` CVaR is a **coherent risk measure** in the sense of Artzner, Delbaen, Eber & Heath, *"Coherent Measures of Risk"* (Mathematical Finance, 9(3):203–228, 1999) — it satisfies monotonicity, sub-additivity, positive homogeneity, and translation invariance. VaR is **not** coherent: it can violate sub-additivity, i.e., penalize diversification. Rockafellar & Uryasev, *"Optimization of Conditional Value-at-Risk"* (Journal of Risk, 2(3):21–41, 2000) and *"Conditional Value-at-Risk for General Loss Distributions"* (Journal of Banking & Finance, 26(7):1443–1471, 2002) established:
- CVaR is coherent for **general loss distributions, including discrete/empirical** ones (the 2002 paper's contribution — the 2000 paper handled continuous distributions).
- CVaR has a convex optimization representation (the Rockafellar-Uryasev minimization formula), linearizable via LP — VaR optimization is non-convex.
- For continuous loss distributions CVaR equals expected shortfall (expected loss beyond VaR); for distributions with atoms the precise definition requires the "CVaR+/CVaR-" split from the 2002 paper. **Relevant caveat:** an empirical MC sample is discrete, so the engine is in the general-distribution regime — the estimator must use the Rockafellar-Uryasev general definition, not the naive "average of losses beyond the empirical VaR," which is biased on discrete samples.
- **Replication Status:** Foundational and universally cited; Yes.

**One important counter-current to surface (conflict flag):** Coherence has been *criticized* as not obviously the right axiom set for all uses. Some authors argue VaR's non-coherence is overstated in practice, and that ES's own weaknesses (estimation, backtesting) offset its coherence advantage — see Section 2.3. The proposal should not treat "CVaR is coherent" as settling the question of whether it is the *best* trigger.

### 2.2 Choosing the confidence level

**Finding (Fact):** `[High]` The regulatory benchmark is the Basel FRTB move from 99% VaR to **97.5% ES** (Basel Committee on Banking Supervision, *Fundamental Review of the Trading Book*; BCBS Consultative Documents). Rationale stated by the Committee: ES captures tail-loss *severity* VaR ignores, and 97.5% ES ≈ 99% VaR under normality, so the change is roughly capital-neutral for Gaussian books but *more conservative for fat-tailed* ones.

**Trade-off (Interpretation, literature-grounded):**
- A *deeper* tail (e.g., 1%) is more conservative but has *fewer* sample points → far higher estimation error (Section 2.3). The estimation error of ES grows as the confidence level approaches 1 (Kondor / Caccioli et al., *"Estimation Error of Expected Shortfall,"* arXiv:1402.5534).
- A *shallower* tail (e.g., 10%) is more stably estimable but conditions on losses that are not really "tail" events.
- Basel's 97.5% is an explicit compromise between tail-relevance and estimability. There is **no single "correct" level** in the literature — it is a stated trade-off.
- For a per-minute, short-horizon engine, the relevant tail event count per evaluation is `alpha × N_paths` — at 5% and 1,000 paths that is **50 paths**, at 1% it is **10 paths**. The confidence level and the path count are jointly constrained, not independently choosable.

### 2.3 CVaR estimation error at the 5% tail with small samples — how many paths / how much history?

**This is a core risk item. Findings, graded:**

1. **ES needs more data than VaR for equal accuracy, and the gap widens with tail fatness.** `[High]` Yamai & Yoshiba, *"Comparative Analyses of Expected Shortfall and Value-at-Risk: Their Estimation Error, Decomposition, and Optimization"* (Monetary and Economic Studies / Bank of Japan IMES, 2002) and *"Value-at-risk versus expected shortfall: A practical perspective"* (Journal of Banking & Finance, 29(4):997–1015, 2005). Headline practitioner number: **~1,000 observations are needed for ES estimation accuracy to converge with VaR's** for practical purposes. For fat-tailed losses, VaR estimates can be *more* accurate than ES estimates at a given sample size — ES "pays" for its tail-awareness with estimator variance.

2. **ES estimation error scales unfavorably as alpha → 1.** `[High]` Kondor and collaborators (*"Estimation Error of Expected Shortfall,"* arXiv:1402.5534; *"Portfolio Optimization under Expected Shortfall: Contour Maps of Estimation Error,"* arXiv:1510.04943) show a **phase-diagram** in the (N/T, alpha) plane: beyond a critical line the ES optimization/estimation problem becomes effectively unstable — the estimate is dominated by sampling noise. The relative standard deviation of the ES estimator is substantially larger than VaR's, and diverges faster as the tail thins.

3. **Tail data sparsity is intrinsic.** `[High]` ES conditions on the worst `alpha` fraction; with `N` samples only `alpha·N` inform the estimate. At 5% / 1,000 samples → 50 points; at 5% / 250 samples → ~13 points. Estimating a *mean of a fat tail* from ~13 points is high-variance and biased low (the empirical tail rarely contains the rare extreme).

**Synthesis for the proposal (Interpretation — stated as such):**
- **Per-minute MC path count:** To get a CVaR estimate at the 5% tail whose error is comparable to a VaR estimate, the Yamai-Yoshiba benchmark points to an **order of ~1,000+ paths minimum**, and the Kondor phase-diagram work implies the requirement *grows* if the path generator is genuinely fat-tailed (the more relevant case). A few hundred paths will produce a CVaR number, but it will be a *noisy* number — the budget check `CVaR > lambda` will then itself be noisy, producing false exits and missed exits. There is no published exact "N paths" answer because it depends on the tail of the path generator (Section 3) — but **"a few hundred paths is insufficient for a stable 5% CVaR" is well-supported.**
- **History feeding the path generator:** If forward paths are calibrated from historical returns (bootstrap, or fitted t/GARCH), the *historical* sample carries its own tail-estimation error. AlphaBot's 125-trading-day autotuner window (~125 daily observations) contains only ~6 observations in the worst 5% — far below the regime where a *daily-frequency* tail is reliably characterized. Intraday (minute) history supplies many more points but a *different* return process (intraday returns are not scaled-down daily returns; microstructure, intraday vol seasonality).
- **Estimation bias direction:** Small-sample empirical CVaR is typically **biased toward understating the tail** (the rare extreme is usually absent from a small sample). A budget check built on a downward-biased CVaR is **systematically too permissive** — it will let the engine hold through risk it has under-measured. This bias direction matters: it fails toward *not exiting*.

### 2.4 Backtesting / validating the CVaR trigger

**Finding (Fact):** `[High]` ES is **not elicitable** as a standalone functional (Weber 2006; Gneiting, *"Making and Evaluating Point Forecasts,"* JASA 106, 2011) — there is no scoring rule that ranks ES forecasts in isolation. **However**, ES is **jointly elicitable with VaR** (Fissler & Ziegel, *"Higher order elicitability and Osband's principle,"* Annals of Statistics, 2016; Acerbi & Székely, *"Backtesting Expected Shortfall,"* Risk, 2014). Practical ES backtests exist and are sensitive to systematic ES underestimation.
- **Implication for the proposal:** the CVaR budget trigger *can* be backtested, but the validation design must use a joint VaR-ES backtest (or Acerbi-Székely-style tests), not a naive "did CVaR predict the loss" check. A claim that the trigger "works" requires this machinery; a raw P&L backtest does not validate the CVaR estimate itself.
- **Regime Sensitivity:** ES backtests have low power in small samples — exactly the regime a short live track record sits in.

---

## 3. Forward-Path Generation for Short-Horizon Equity/ETF Simulation — THE CRUX

The CVaR budget is **only as good as the tail of the simulated path distribution.** If the generator understates the tail, the CVaR estimate is biased low and the budget is permissive in precisely the scenarios it exists to catch. This section grades each method on **tail fidelity**.

### 3.1 GBM / Gaussian increments

**Finding (Fact):** `[High]` GBM models log-returns as Gaussian. Empirical equity/ETF returns — at daily *and* intraday frequency — exhibit **excess kurtosis and heavy tails** that Gaussian increments cannot reproduce (well-established; Cont, *"Empirical properties of asset returns: stylized facts and statistical issues,"* Quantitative Finance 1, 2001; reaffirmed across the GBM-limitation literature, e.g., the cryptocurrency-VaR GBM critique arXiv:2601.14272).
- **Tail-fidelity verdict:** GBM **understates the tail**. A CVaR computed from GBM paths is **systematically too small** — the worst 5% of Gaussian paths are far milder than the worst 5% of real returns. **GBM cannot credibly support a CVaR budget**; it will produce a comfortable-looking number that is wrong in the dangerous direction.
- **Additional flaw:** GBM has no volatility clustering and no mean reversion — it cannot represent a *sustained* adverse drift, which is the multi-bar drawdown a trailing-exit engine most needs to anticipate.
- **Empirical Evidence:** `[Backtest / well-replicated stylized fact]`.

### 3.2 Student-t increments (t-GBM)

**Finding (Fact):** `[Medium-High]` Replacing Gaussian increments with Student-t (calibrated degrees of freedom `nu`) reproduces excess kurtosis and is "highly accurate compared to normal GBM" in empirical studies (ASA proceedings 2016, *"Estimation of Geometric Brownian Motion Model with a t-distribution"*).
- **Tail-fidelity verdict:** Better than GBM. Captures *unconditional* heavy tails.
- **Caveats to surface:** (a) The tail index is now a *fitted parameter* `nu` — with small history, `nu` is itself poorly estimated, and a mis-estimated `nu` mis-states the CVaR. (b) IID t-increments still have **no volatility clustering** — they get the *marginal* tail fatness but not the *temporal clustering* of large moves, so they understate the probability of *consecutive* bad bars. (c) Student-t is symmetric unless skew-t is used; equity downside tails are typically fatter than upside.
- **Empirical Evidence:** `[Backtest]`. **Replication:** the t-improvement over Gaussian is replicated; the *adequacy* of t for a 5% CVaR budget is not established.

### 3.3 Historical (IID) bootstrap

**Finding (Fact):** `[High]` Resampling actual historical returns with replacement produces a fat-tailed marginal "for free" — no distributional assumption (medium / Analytics Vidhya bootstrap-GBM treatments; bootstrap-VaR literature, MPRA 68842).
- **Tail-fidelity verdict (marginal):** Good for the *marginal* tail — the simulated returns are real returns, so kurtosis/skew are inherited. **Hard ceiling:** the bootstrap **cannot generate a loss larger than the worst loss in the sample.** If the worst historical day is the worst the simulator can produce, CVaR is **capped by history** — a structural understatement of tail risk for any position whose true risk exceeds the sample's realized worst case.
- **Critical flaw for a path simulator:** IID bootstrap **destroys serial dependence** — autocorrelation and, crucially, **volatility clustering**. The literature is explicit: "if you shuffle days randomly, you destroy autocorrelation and underestimate risk of sustained drawdown" (block-bootstrap sources, MDPI Risks 13(9):166, 2025). For a *multi-bar* forward path, IID bootstrap therefore **understates the probability of a sustained adverse run** — again failing in the dangerous direction.
- **Empirical Evidence:** `[Backtest]`.

### 3.4 Block bootstrap (moving / circular / stationary)

**Finding (Fact):** `[High]` Block bootstrap resamples *contiguous blocks* of returns, preserving within-block serial dependence and volatility clustering. Variants: moving block bootstrap (MBB; Künsch 1989), circular block bootstrap (Politis & Romano 1992), stationary bootstrap (Politis & Romano 1994, random block length). MBB "more reliable in preserving auto-correlation, fat-tail, and positive skewness" of stock returns; block bootstrap produces "wider, more conservative confidence intervals, particularly in extreme tails," and accounting for volatility clustering raised a 99% VaR by ~20% vs IID in one study (MDPI Risks 13(9):166, 2025).
- **Tail-fidelity verdict:** **Best of the four for a CVaR budget** on short multi-bar horizons — it is the only listed method that captures *both* fat marginals *and* clustering of large moves.
- **Caveats to surface:** (a) Block length is a **tuning parameter** — too short re-introduces the IID flaw, too long reduces effective sample diversity. There is no universally optimal block-length rule; data-driven selectors exist (Politis-White) but are themselves estimates. **The block length is an additional, often unstated, model degree of freedom.** (b) The worst-case ceiling from §3.3 still applies — block bootstrap cannot exceed historically realized extremes. (c) Quality depends entirely on the history fed in; a calm-regime history yields a calm-regime CVaR.
- **Empirical Evidence:** `[Backtest]`, with independent replication across asset classes (equities, crypto). **Replication:** Yes for the clustering-preservation claim.

### 3.5 Methods not in the prompt but flagged for completeness

- **GARCH / filtered historical simulation (FHS):** Bootstrap GARCH-standardized residuals, then re-inject the GARCH volatility path. This is the practitioner standard for *conditional* tail risk and explicitly handles volatility clustering with a fat-tailed residual. The block-bootstrap crypto paper (MDPI 2025) frames block bootstrap partly as a *robustness alternative when GARCH is misspecified* — i.e., GARCH/FHS and block bootstrap are the two serious contenders, with a documented trade-off (model-based-but-misspecifiable vs model-free-but-block-length-dependent). `[Medium-High]`
- **Extreme Value Theory (EVT) / peaks-over-threshold:** fits a Generalized Pareto tail; the reference method when the tail itself must be extrapolated *beyond* the historical worst case — the one way to escape the §3.3/§3.4 ceiling. Cost: another fitted tail parameter on sparse data. `[Medium]`

### 3.6 Crux summary table (tail fidelity for a 5% CVaR budget)

| Method | Fat marginal tail | Volatility clustering | Can exceed historical worst | CVaR-budget verdict |
|---|---|---|---|---|
| GBM / Gaussian | No | No | Yes (but tail too thin) | **Understates tail — unsafe for a budget** |
| Student-t GBM (IID) | Yes (fitted `nu`) | No | Yes | Better marginals; misses clustered drawdowns |
| Historical IID bootstrap | Yes (inherited) | **No** | **No (capped by history)** | Misses sustained drawdowns; tail-capped |
| Block bootstrap | Yes (inherited) | **Yes** | No (capped by history) | **Best listed option; block length is a tuned DoF** |
| GARCH-FHS (not in prompt) | Yes (residuals) | Yes (explicit) | Yes | Practitioner standard; misspecifiable |
| EVT tail (not in prompt) | Yes (extrapolated) | depends on coupling | **Yes** | Only way past the historical-worst ceiling |

**Bottom line for Section 3 (Interpretation):** The literature is unambiguous that **GBM and IID bootstrap understate the tail in the dangerous direction** and cannot, on their own, support a CVaR budget that is meant to catch sustained adverse runs. Block bootstrap (or GARCH-FHS) is the credible class — but each introduces its *own* tuning surface (block length; GARCH spec). There is **no path generator that is simultaneously assumption-free and tail-correct.** Whatever generator is chosen *is itself the dominant model-risk decision* — larger than the choice of gamma or lambda.

---

## 4. The Overfitting Question

**Claim under examination:** a 2-parameter (gamma, lambda) EUT+CVaR model is genuinely *less* prone to overfitting than a ~7-knob heuristic stack tuned by walk-forward — and possibly "mathematically immune to curve-fitting."

### 4.1 Parameter risk vs specification risk

**Finding (Fact):** `[High]` The overfitting literature (Bailey, Borwein, López de Prado & Zhu, *"The Probability of Backtest Overfitting,"* Journal of Computational Finance, 2017; Bailey, Ger, López de Prado, Sim & Wu, *"Statistical Overfitting and Backtest Performance,"* SSRN 2507040; López de Prado, *Advances in Financial Machine Learning*, 2018) frames overfitting as a function of the **number of distinct configurations searched and selected on**, not merely the count of free parameters in the final model. The Deflated Sharpe Ratio penalizes performance *for the number of trials*. Combinatorial Purged Cross-Validation exists because in-sample selection of *any* design choice inflates expected performance.

**Two distinct risk channels (Interpretation, standard taxonomy):**
- **Parameter risk:** error from fitting the *values* of free parameters (gamma, lambda; or the 7 heuristic knobs). Scales with parameter count and with how aggressively they are tuned. **A 2-parameter model genuinely has lower parameter risk than a 7-parameter one** — this part of the claim is defensible.
- **Specification (model) risk:** error from the *choice of model structure* — utility family (CARA vs CRRA vs skew-aware), the horizon convention, the path generator (GBM/t/IID-bootstrap/block/GARCH/EVT), the block length, the confidence level alpha, the wealth normalization. **Each of these is a degree of freedom.** The EUT+CVaR proposal has *at least 5–7 such structural choices* before gamma and lambda are ever tuned.

**Key point:** the 2-parameter framing **counts only the parameter-risk channel and hides the specification-risk channel.** If the path generator, utility form, horizon, and alpha are themselves selected by looking at backtest performance, they are *de facto* tuned parameters — and the literature (López de Prado et al.) is explicit that such selection inflates expected backtest performance just as numeric parameter tuning does. The honest parameter count of the EUT+CVaR model is **not 2**; it is 2 *visible* knobs plus a stack of structural choices, several of which are continuous (block length, nu, alpha).

### 4.2 Is the EUT+CVaR model less prone to overfitting?

**Honest verdict (Interpretation, literature-grounded):**
- **On parameter risk alone: plausibly yes.** Two tuned scalars, walk-forward, is less curve-fitting surface than seven.
- **On total overfitting risk: not established, and quite possibly no.** The EUT+CVaR model *relocates* risk from parameter risk into specification risk. Specification risk is **more dangerous** in one specific way: parameter risk is at least *visible and quantifiable* (you can deflate the Sharpe for trials, you can CPCV the parameter search). Specification risk from "we chose GBM" or "we fixed horizon = 30 minutes" is often **unstated, untested, and not penalized** — it does not show up in a walk-forward parameter sweep at all. A model can have 2 parameters and still be badly overfit *to a structural assumption that happens to flatter the backtest period*.
- The heuristic stack's 7 knobs are at least *honest about being knobs.* The EUT+CVaR model's structural choices can masquerade as "principled" while doing the same job as knobs.
- **López de Prado's own guidance** cuts both ways: he favors *parsimony and slight underfit* for robustness ("a slightly underfit model may still generalize... its backtested performance is less likely to be a mirage") — which *supports* fewer parameters. But he equally warns that **shared specification errors stack hidden risk** — which *warns against* treating a structural choice as exempt from overfitting scrutiny.

### 4.3 Is "mathematically immune to curve-fitting" ever defensible?

**Finding (Interpretation, strongly literature-grounded):** **No.** This claim is not defensible for the EUT+CVaR exit model, for three reasons:
1. **The model has free parameters** (gamma, lambda) that *will* be tuned on data — by definition not immune to parameter overfitting. "Two parameters" is *less* exposure, not *zero* exposure.
2. **The structural choices are data-influenced.** Choosing the path generator, utility form, horizon, and alpha — if any of these is informed by what performed well historically — is curve-fitting by the López de Prado definition (selection among configurations).
3. **Mathematical coherence (CVaR) and decision-theoretic optimality (EUT) are properties of the *model given its assumptions* — they say nothing about whether the assumptions match the future market.** A GBM-based CVaR engine is "mathematically rigorous" and *also* systematically wrong about the tail (Section 3.1). Rigor of derivation is orthogonal to freedom from overfitting.

The defensible statement is narrower: *"a 2-parameter model has a smaller parameter-overfitting surface than a 7-parameter model, holding specification risk constant"* — and that last clause is exactly what does not hold here.

- **Empirical Evidence for §4:** `[Theoretical]` + `[Backtest-methodology literature]`. **Replication:** the overfitting-from-trials result is replicated and standard.
- **Anti-pattern check:** No single backtest can settle whether EUT+CVaR beats the heuristic — even an impressive Sharpe would require Deflated-Sharpe / CPCV treatment and out-of-sample confirmation before being credited.

---

## 5. Compute Cost and Variance Reduction for Per-Minute MC Exit Evaluation

### 5.1 Feasibility of per-minute Monte Carlo

**Finding (Fact / practitioner):** `[Medium]` Per-minute MC for a *single position's* exit decision is computationally light by industry standards — pricing desks run far larger MC books intraday. The cost is `N_paths × horizon_steps × N_positions` per minute. The binding constraint is **not raw compute** but the architectural rule in AlphaBot's own CLAUDE.md: *no blocking I/O on the 1-minute execution path.* MC itself is CPU-bound (non-blocking); the risk is path-generator *calibration* that touches history fetches.
- **Tension to surface:** Section 2.3 says a *stable* 5% CVaR wants ~1,000+ paths; Section 5.2 variance reduction can lower that. There is a real trade-off between CVaR stability and per-minute latency budget — both must be quantified together, not separately.

### 5.2 Variance-reduction techniques

Standard families (MIT 15.450 lecture notes; survey literature on variance reduction):

1. **Antithetic variates.** `[High]` Pair each path with its mirror (negate the driving Gaussians). Cheap, near-universal, reduces variance when the estimator is monotone in the inputs. **Caveat for CVaR:** antithetic variates are most effective for *smooth, symmetric* estimators (means); for a **tail** functional like CVaR the variance reduction is **weaker and not guaranteed** — the literature notes antithetics can even *increase* variance for non-monotone payoffs. Useful but not a tail-specific tool.
2. **Quasi-Monte Carlo (QMC) / low-discrepancy sequences (Sobol, Halton).** `[Medium-High]` Replaces pseudo-random draws with low-discrepancy points; can improve convergence from `O(N^-1/2)` toward `~O(N^-1)` for low effective dimension. **Caveat:** the advantage **degrades with dimension** (a multi-step path is moderately high-dimensional) and for **discontinuous / tail** integrands QMC's superiority is reduced. Brownian-bridge / PCA path construction is the standard fix to keep effective dimension low.
3. **Importance sampling (IS).** `[High]` The technique **specifically designed for tail / rare-event estimation** (Glasserman, Heidelberger & Shahabuddin — exponential tilting; Glasserman & Li, *"Importance Sampling for Portfolio Credit Risk"*). Tilt the sampling distribution toward the loss tail so more paths land in the 5% region, then re-weight. For a 5% (and especially a 1%) CVaR this is the highest-leverage method — it directly attacks the "only 50 of 1,000 paths inform the estimate" problem.
   - **Caveats — important:** (a) IS requires choosing a **good tilt**; a poor tilt can *increase* variance, sometimes catastrophically. (b) Recent work (arXiv:2601.09927, *"Efficiency versus Robustness under Tail Misspecification"*) shows IS efficiency is **fragile under tail misspecification** — if the assumed tail is wrong, the IS estimator's variance guarantees fail. This couples IS directly to the Section 3 crux: **IS does not fix a wrong path generator; it makes a wrong generator's CVaR converge faster to the wrong answer.** (c) IS adds a tuning surface (the tilt) — another implicit parameter (see Section 4).
4. **Control variates / stratified sampling.** `[Medium]` Stratifying on the terminal return (or a tail indicator) guarantees tail coverage and is a robust complement to IS; control variates need a correlated closed-form quantity.

### 5.3 How much can variance reduction cut the path count?

**Finding (Interpretation, literature-grounded):** For tail estimation, well-tuned **importance sampling can reduce required paths by one to two orders of magnitude** for a target accuracy — this is the central result of the rare-event simulation literature. Antithetics typically give a modest constant-factor reduction; QMC gives a convergence-rate improvement that is strongest in low effective dimension. **However:** the literature gives **no universal "use N paths" number** — the reduction depends on the estimator, the tilt quality, and the path-generator tail. The honest statement is: *variance reduction can make a per-minute 5% CVaR tractable at hundreds rather than thousands of paths, but only if (a) the path generator's tail is correct and (b) the IS tilt is well-chosen — and both are themselves modeling problems.*

- **Empirical Evidence for §5:** `[Theoretical]` + `[practitioner-method literature]`. **Replication:** IS for rare events is heavily replicated. **Regime Sensitivity:** IS tilts calibrated in one volatility regime can become inefficient in another; the tilt is not regime-free.

---

## Cross-Cutting Honest Verdict (no recommendation — findings only)

1. **The framework is theoretically legitimate.** EUT optimal-stopping for asset exit and CVaR as a coherent tail measure are both well-founded, well-cited bodies of work. Nothing in the proposal is pseudoscience.
2. **"2 parameters" undersells the true model complexity.** The honest degrees of freedom include utility family, gamma, the horizon convention, the path-generator family, its sub-parameters (nu / block length / GARCH spec), alpha, the wealth-normalization choice, and any IS tilt. The migration trades *visible* parameter risk for *less visible* specification risk — and the literature does not support the conclusion that this lowers *total* overfitting risk.
3. **"Mathematically immune to curve-fitting" is not a defensible claim** and conflicts directly with the Bailey / López de Prado overfitting literature.
4. **The path generator is the dominant risk, not gamma/lambda.** A CVaR budget on GBM or IID-bootstrap paths is biased toward *understating* the tail — i.e., it fails toward not exiting. Block bootstrap or GARCH-FHS are the credible generators, each with its own tuned degree of freedom and a hard ceiling at the historically realized worst case (EVT is the only escape from that ceiling).
5. **Estimation error at the 5% tail is real and quantified.** Yamai-Yoshiba's ~1,000-observation benchmark and Kondor's phase-diagram instability mean a per-minute CVaR computed from a few hundred plain-MC paths is a *noisy* trigger; variance reduction (especially importance sampling) can help, but cannot rescue a mis-specified tail.
6. **Validation is harder than a P&L backtest.** ES is not standalone-elicitable; crediting the CVaR trigger requires joint VaR-ES backtests (Acerbi-Székely / Fissler-Ziegel), and any head-to-head vs the heuristic needs Deflated-Sharpe / CPCV treatment.

---

## Open Questions (logged, not resolved — adjacent to scope)

- What return frequency feeds the path generator (daily-calibrated vs intraday-calibrated)? The two imply different tail processes and different sample sizes; this was raised in §2.3 but is a design question, not a literature question.
- Does AlphaBot's existing `run_monte_carlo` in `math_engine.py` already use a particular path family? A grounding read of the codebase would be needed to state whether the proposal *extends* or *replaces* an existing simulator — out of scope for this literature report.
- Empirical head-to-head studies of *utility-based exits vs trailing-stop heuristics on live equity/ETF data* were searched for and **not found**. If they exist they are not prominent; absence of evidence is logged, not treated as evidence of absence.

---

## Sources

**Tier 1 — Primary (peer-reviewed / standards / foundational):**
- Artzner, Delbaen, Eber & Heath (1999), "Coherent Measures of Risk," *Mathematical Finance* 9(3):203–228.
- Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk," *Journal of Risk* 2(3):21–41. https://www.financerisks.com/filedati/WP/paper/CVaR%20Portfolio%20Optimization.pdf
- Rockafellar & Uryasev (2002), "Conditional Value-at-Risk for General Loss Distributions," *Journal of Banking & Finance* 26(7):1443–1471. https://sites.math.washington.edu/~rtr/papers/rtr187-CVaR2.pdf · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=267256
- Henderson & Hobson (2007), "Horizon-unbiased utility functions," *Stochastic Processes and their Applications* 117(11):1621–1641. https://warwick.ac.uk/fac/sci/statistics/staff/academic-research/hobson/publications/horizonu.pdf
- Evans, Henderson & Hobson (2008), "An explicit solution for an optimal stopping/optimal control problem which models an asset sale," *Annals of Applied Probability* 18(5). https://projecteuclid.org/journals/annals-of-applied-probability/volume-18/issue-5/An-explicit-solution-for-an-optimal-stopping-optimal-control-problem/10.1214/07-AAP511.full
- Fabre (2018), "Liquidation of an indivisible asset with independent investment," *Mathematical Finance* 28. https://onlinelibrary.wiley.com/doi/10.1111/mafi.12127
- Yamai & Yoshiba (2002), "Comparative Analyses of Expected Shortfall and Value-at-Risk: Their Estimation Error, Decomposition, and Optimization," *Monetary and Economic Studies* (Bank of Japan IMES). https://www.imes.boj.or.jp/research/papers/english/me20-1-4.pdf · BIS version: https://www.bis.org/cgfs/Yamai-Yoshiba.pdf
- Yamai & Yoshiba (2005), "Value-at-risk versus expected shortfall: A practical perspective," *Journal of Banking & Finance* 29(4):997–1015. https://www.sciencedirect.com/science/article/abs/pii/S0378426604001499
- Gneiting (2011), "Making and Evaluating Point Forecasts," *Journal of the American Statistical Association* 106(494):746–762.
- Fissler & Ziegel (2016), "Higher order elicitability and Osband's principle," *Annals of Statistics* 44(4):1680–1707.
- Basel Committee on Banking Supervision, *Fundamental Review of the Trading Book* (consultative documents). https://www.bis.org/publ/bcbs265.pdf
- Cont (2001), "Empirical properties of asset returns: stylized facts and statistical issues," *Quantitative Finance* 1(2):223–236.

**Tier 1/2 — Working papers and preprints (date-flagged; arXiv not peer-reviewed):**
- Kondor (2014), "Estimation Error of Expected Shortfall," arXiv:1402.5534. https://arxiv.org/abs/1402.5534
- Caccioli, Kondor et al. (2015), "Portfolio Optimization under Expected Shortfall: Contour Maps of Estimation Error," arXiv:1510.04943. https://arxiv.org/pdf/1510.04943
- Acerbi & Székely (2014), "Backtesting Expected Shortfall," *Risk*.
- Fissler, Ziegel & Gneiting (2015), "Expected Shortfall is jointly elicitable with Value at Risk — Implications for backtesting," arXiv:1507.00244. https://arxiv.org/abs/1507.00244
- "Robust Tail Risk Estimation in Cryptocurrency Markets: Addressing GARCH Misspecification with Block Bootstrapping," *Risks* 13(9):166 (2025). https://www.mdpi.com/2227-9091/13/9/166
- "Efficiency versus Robustness under Tail Misspecification: Importance Sampling and Moment-Based VaR Bracketing," arXiv:2601.09927. https://arxiv.org/html/2601.09927
- "Estimation of Geometric Brownian Motion Model with a t-distribution," ASA Proceedings (2016). https://ww2.amstat.org/meetings/proceedings/2016/data/assets/pdf/389551.pdf

**Tier 2 — Expert / textbook / named-author:**
- López de Prado (2018), *Advances in Financial Machine Learning*, Wiley.
- Bailey, Borwein, López de Prado & Zhu (2017), "The Probability of Backtest Overfitting," *Journal of Computational Finance*. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Bailey, Ger, López de Prado, Sim & Wu, "Statistical Overfitting and Backtest Performance," SSRN 2507040. https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf
- Glasserman & Li, "Importance Sampling for Portfolio Credit Risk." https://business.columbia.edu/sites/default/files-efs/pubfiles/1368/Glasserman_importance_sampling.pdf
- Glasserman, "Importance Sampling and Stratification for Value-at-Risk." https://business.columbia.edu/sites/default/files-efs/pubfiles/1394/Glasserman_importance_sampling_and_strat.pdf
- A. Rao, "Understanding Risk-Aversion through Utility Theory," Stanford CME241 lecture notes. https://web.stanford.edu/class/cme241/lecture_slides/UtilityTheoryForRisk.pdf
- MIT 15.450, "Variance Reduction / Quasi-Monte Carlo" lecture notes. https://ocw.mit.edu/courses/15-450-analytics-of-finance-fall-2010/4fa033082ff5ee58722a67fe81f0dce7_MIT15_450F10_lec03.pdf
- Bank Policy Institute, "Why is the FRTB Expected Shortfall Calculation Designed as It Is?" https://bpi.com/why-is-the-frtb-expected-shortfall-calculation-designed-as-it-is/
- Brocas et al., "Risk Aversion in a Dynamic Asset Allocation Experiment." https://cear.gsu.edu/files/2015/12/Asset_Allocation_Experiment.pdf

**Tier 3/4 — Community / secondary (used only for orientation, corroborated above):**
- QuantInsti, "Conditional Value at Risk (CVaR) or Expected Shortfall." https://blog.quantinsti.com/cvar-expected-shortfall/
- arXiv:2601.14272, GBM lognormal-limits / crypto VaR critique (preprint, not peer-reviewed). https://arxiv.org/pdf/2601.14272

**Verification note:** Two foundational PDFs (Rockafellar-Uryasev 2002; Kondor arXiv:1402.5534) could not be machine-extracted in this session; their results are reported from multiple independent corroborating secondary sources and the papers' abstracts, and are tagged `[High]` only where ≥2 independent sources agree. The specific numerical claims (Yamai-Yoshiba ~1,000 observations; 97.5% ES ≈ 99% VaR under normality) are each corroborated by ≥2 sources. Any claim resting on a single un-extracted primary source is tagged `[Medium]` in-text.
