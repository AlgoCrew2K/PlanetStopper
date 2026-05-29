> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Decision-Science Architecture — Provenance & Institutional-Adoption Review

**Date:** 2026-05-22
**Author:** quant-risk-researcher
**Scope:** Literature-and-practice provenance of a proposed "constrained decision core" for trading-position exits. **Provenance and institutional adoption only** — path-generator mechanics and the horizon convention are covered by a sibling task and are deliberately NOT addressed here.
**Charter constraint:** This report surfaces what the literature proves, what it widely practices without proving, and what is folklore. It makes **no implementation recommendation** and names **no preferred design**.

---

## Architecture Under Review

A four-part exit decision core:

1. Hand-crafted technical heuristics (trailing-stop / VWAP-breakdown / volatility-ratchet style signals) are **not** independent exit triggers; they become **conditioning features** for a forward-return-path simulator (a kNN regime-matching Monte Carlo).
2. The simulated path ensemble feeds a **CVaR / Expected Shortfall hard constraint** — breach the tail budget, exit.
3. ...and an **expected-utility objective** — exit when `E[U(exit)] > E[U(hold)]`.
4. Arbitrated by a **deterministic priority resolver**.

---

## BLUF (Bottom Line Up Front)

- Each of the **four primitives is individually well-grounded** — three of them (CVaR-as-constraint, signal combination into one objective, conditional-density forecasting) are not merely academic but institutionally standard. Expected-utility / optimal-stopping for exit timing is academically deep but **thin in documented practitioner systems**.
- **The full composition as a stack is not, to the evidence found, published or institutionally documented.** It is best described honestly as a **bespoke composition of individually-proven parts**. Closest published analogues are CVaR-constrained safe reinforcement learning and analog/macro-contextual retrieval forecasting — neither is the same object.
- The **highest-risk joint in the composition is primitive (1)→(4)'s dependence on a kNN regime matcher fed many correlated heuristic features.** This is the part with the most adverse literature: distance concentration can degrade nearest-neighbour quality at as few as ~10–15 effective dimensions, and correlated features inflate nominal dimensionality without adding discriminating information. This is a genuine, documented pathology — not folklore.
- A second real risk: CVaR is **harder to estimate and harder to backtest than VaR**, and a tail-budget *trigger* inherits that estimation error directly into a live action.

Confidence on individual primitives: `[High]`. Confidence that the *composition* is novel-but-sound: `[Medium]` — novelty is well-evidenced; soundness of the joint is unproven and partly contra-indicated.

---

## Deliverable 1 — CVaR / Expected Shortfall as a *Constraint*

### Academic grounding

- **Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk," *Journal of Risk* 2(3):21–41** is the foundational result. They proved CVaR can be minimized by linear programming and — directly relevant here — that the same machinery handles **maximizing a reward function subject to a CVaR constraint**, not only minimizing CVaR. CVaR-as-constraint is therefore *original to the founding paper*, not a later bolt-on. `[High]` `[Theoretical]`
- Rockafellar & Uryasev (2002), "Conditional value-at-risk for general loss distributions," *Journal of Banking & Finance* 26(7):1443–1471, extends to general (non-continuous) distributions and establishes CVaR as a **coherent risk measure** in the Artzner–Delbaen–Eber–Heath (1999) sense — VaR is not coherent (it fails subadditivity). This is the standard academic argument for preferring ES/CVaR as a constraint.
- Chance-constrained programming context: a VaR constraint is a chance constraint and is generally non-convex; CVaR (and EVaR) constraints are convex tractable surrogates. This is why CVaR constraints are computationally preferred over literal VaR constraints in optimization.

**Replication status:** The Rockafellar–Uryasev LP formulation is one of the most independently reproduced results in quantitative risk; reproduced across thousands of papers and standard textbooks. **Replicated: Yes.**

### Institutional adoption — specific

- **Basel FRTB is the strongest institutional evidence.** BCBS, *Minimum capital requirements for market risk*, document **d457, 14 January 2019** (the consolidated standard; originally d352, January 2016): the **internal models approach (IMA) replaces VaR with Expected Shortfall**. Verified directly from bis.org/bcbs/publ/d457.htm — confirmed "relies upon the use of expected shortfall models." `[High]` `[Live evidence — regulatory mandate]`
  - The widely-cited specifics — ES at the **97.5% confidence level**, calibrated to a period of stress, with liquidity-horizon scaling — are reported by multiple secondary/expert sources (Bank Policy Institute, AnalystPrep/FRM curriculum, SIFMA). These specifics are `[Medium]` here because I confirmed the VaR→ES replacement from the primary BCBS page but did **not** extract the 97.5% figure from the primary PDF itself. Flagged as an open item — the 97.5% level should be confirmed against d457 §MAR33 before being quoted as primary.
  - **Important nuance:** FRTB uses ES as the *capital measure*, and uses VaR-based **backtesting** at the desk level (P&L attribution + VaR exception counts) *because ES is hard to backtest directly*. So even the flagship institutional adoption keeps VaR in the loop for validation. This is directly relevant to a design that wants to *trigger actions* on ES.
- **Buy-side / insurance:** CVaR-constrained allocation is documented in the (re)insurance and banking-foundation literature (e.g., Sample-Average-Approximation for CVaR-constrained allocation in a (re)insurance context, arXiv 2410.10239; "Robust portfolio optimization for banking foundations: a CVaR approach," *Central European Journal of Operations Research*, 2022). `[Medium]` `[Backtest / applied]`
- **Vendors:** CVaR/ES optimization is a first-class feature in mainstream institutional risk platforms (the literature consistently references this; specific vendor confirmation not independently re-verified here — treat as `[Unverified]` for any specific vendor claim).

### Empirical Evidence

- Coherence and LP-tractability: `[Theoretical]`, replicated, uncontested.
- ES superiority over VaR for *capturing* tail severity: `[Theoretical]` + `[Live evidence]` (regulatory adoption).
- ES superiority as a *trading trigger*: **not established.** No primary source found that demonstrates ES-triggered position exits outperform simpler rules out-of-sample.

### Regime Sensitivity / honest caveats

- **Estimation error.** CVaR averages the tail beyond VaR; with limited data the tail shape is poorly observed. Quant Decoded and A.L. Capital Advisory both note VaR is *more robust to tail misestimation* precisely because it depends on a single quantile — CVaR "pays for" its information content with higher estimation variance. A hard exit trigger inherits this variance.
- **Backtestability.** CVaR is materially harder to backtest than VaR (you must validate average tail magnitude, not just exception frequency) — this is why FRTB itself backtests on VaR. Acerbi & Székely (2014), "Backtesting Expected Shortfall," *Risk* magazine, is the standard reference establishing ES *is* backtestable but with more complex procedures and larger samples.
- **Crypto / regime studies:** "Regime- and Tail-Dependent Performance of CVaR-Based Portfolio Strategies in Cryptocurrencies" (*JRFM* 14(3):53, 2026) finds CVaR-strategy performance is itself **regime-dependent** — it is not a regime-robust universal.

**Verdict:** CVaR-as-constraint is **proven as a primitive and institutionally standard** for capital and allocation. Using it as a *live exit trigger* is a reasonable extrapolation but is **not itself a documented, validated practice** — label that step `[widely-plausible / low direct evidence]`.

---

## Deliverable 2 — Expected-Utility / Optimal-Stopping for *Exit Timing*

### Academic grounding

- **Optimal stopping** is the canonical mathematical frame for exit timing. The double-stopping (entry+exit) formulation is mature:
  - **Leung & Li (2015), "Optimal Mean Reversion Trading with Transaction Costs and Stop-Loss Exit," *International Journal of Theoretical and Applied Finance* 18(3)** (arXiv:1411.5062) — solves optimal liquidation timing under an OU process with transaction costs and a stop-loss; shows the optimal liquidation level is monotone in the stop-loss level. `[High]` `[Theoretical]`
  - Liquidation-of-an-indivisible-asset literature (e.g., arXiv:1312.2754) frames exit explicitly under a **general expected-utility function** — i.e., EU-based exit is an established theoretical object.
  - Henderson & Hobson and related real-options work treats single-asset exit under utility as an optimal-stopping problem; this is a deep, decades-old literature.
- **Prospect-theory variant:** Research shows that for a wide class of value/probability-weighting functions (including Tversky–Kahneman), the optimal liquidation prospect *takes the form of a stop-loss threshold plus a distribution over gains* — i.e., utility-shaped exit rules theoretically *rationalize* observed stop-loss behaviour (ScienceDirect, *Journal of Economic Theory*, "Probability weighting, stop-loss and the disposition effect"). This is a genuine theory→practice bridge for the *shape* of utility-based exits. `[Medium]` `[Theoretical]`

### Institutional / practitioner surfacing

- **Optimal execution is the clearest institutional cousin.** Almgren & Chriss (2000/2001), "Optimal execution of portfolio transactions," *Journal of Risk* 3(2):5–39, frame liquidation as risk-aversion-parameterized (mean–variance) optimization. The risk-aversion λ is the practical knob — institutional execution algos (IS/VWAP/TWAP families) are direct descendants. `[High]` `[Live evidence]` — but note this is *execution scheduling*, not discretionary position-exit timing.
- Risk-sensitive execution with an explicit **CVaR objective** is published (arXiv:2201.11962, "Risk-Sensitive Optimal Execution via a Conditional Value-at-Risk Objective") — this is the nearest published object combining EU/risk-objective + execution.
- **Honest gap:** I found **no primary or expert source documenting an institutional discretionary-exit system that explicitly compares `E[U(exit)]` vs `E[U(hold)]` per position per bar.** Optimal-stopping and EU exit are academically rich but practitioner adoption is concentrated in (a) execution scheduling and (b) pairs/stat-arb entry-exit bands. The specific "EU crossover as the exit objective" framing is `[academically grounded / practitioner-adoption thin]`.

### Empirical Evidence

- Optimal-stopping exit theory: `[Theoretical]`, replicated mathematically.
- Almgren–Chriss execution: `[Live evidence]` for *execution*, not for *whether-to-exit*.
- EU-crossover exit as a live discretionary trigger: **no empirical evidence found** — `[Folklore / unproven]` if presented as standard practice.

### Regime Sensitivity

- Optimal-stopping solutions are **model-class-specific** (OU mean reversion, GBM, etc.). A stopping rule derived under one process is not optimal — and can be badly wrong — under another. A simulator-driven EU comparison sidesteps a closed-form process assumption but *relocates* the model risk into the path generator.
- Utility-based exits are sensitive to the **reference point and curvature assumptions** — prospect-theory results show the *shape* of the optimal rule changes qualitatively with the value function. The utility function is a modelling choice, not an observable.

**Verdict:** EU/optimal-stopping for exit is **theoretically proven and deep**, but its institutional footprint is in execution scheduling and stat-arb bands. The specific `E[U(exit)] vs E[U(hold)]` exit objective is a **defensible application of proven theory, not a documented standard practice.**

---

## Deliverable 3 — Signal Combination into ONE Unified Objective

### This is genuinely standard systematic-investing practice. `[High]`

- **Grinold & Kahn, *Active Portfolio Management* (1995/2000, McGraw-Hill)** — the **Fundamental Law of Active Management**: `IR ≈ IC × √Breadth`. The entire managed-quant industry is built on combining many independent forecasts into one objective; the FLAM is the formal justification that *more independent signals raise the information ratio*. `[High]` `[Theoretical, near-universally adopted]`
- Grinold & Kahn explicitly describe combining a vector of signals with positive predictive power into residual-return forecasts — i.e., **one unified alpha**, then one optimization. Letting each signal trade independently is *not* the textbook approach; the textbook approach is exactly "blend, then act once."
- **Clarke, de Silva & Thorley (2002), "Portfolio Constraints and the Fundamental Law of Active Management," *Financial Analysts Journal* 58(5):48–66**, and their (2006) generalization extend the law to a full covariance matrix and an explicit **transfer coefficient** — formalizing how constraints and signal correlation degrade the realized IR. This is directly on-point: it is the canonical treatment of *blending signals into one constrained objective*.
- López de Prado, *Advances in Financial Machine Learning* (2018, Wiley), Ch. 8 (feature importance) and the meta-labeling construct: the modern ML restatement of "combine many features/signals into one decision."

So: **using heuristics as features feeding one objective, rather than as N independent triggers, is the orthodox systematic-investing pattern.** The architecture's choice here is the *conservative, well-supported* one — not the novel part.

### The important caveat the same literature raises

- **Breadth is not the count of signals — it is the count of *independent* signals.** Clarke, de Silva & Thorley (2006) and "Strategy design and the fallacies of breadth" (*Journal of Asset Management*, 2020) show that correlated signals **inflate nominal breadth while contributing little real breadth**; naïvely counting correlated signals overstates the achievable IR.
- Alpha Architect ("Backtesting strategies based on multiple signals — Beware of Overfitting Bias!") documents that multi-signal strategies built on **many weak signals** have lower out-of-sample replication ratios than a few strong ones; the replication ratio decreases in the number of model parameters.

**This caveat connects directly to Deliverable 6** — feeding *many correlated heuristics* into the matcher is exactly the failure mode the breadth literature warns about.

### Empirical Evidence

- Signal combination into one objective: `[Live evidence]` — this is what quant managers do.
- "More signals always better": **contra-indicated** — `[Backtest evidence against]` when signals are weak/correlated.

**Verdict:** The "blend into one objective" choice is **proven and standard.** The risk is not the pattern — it is the *number and correlation* of inputs.

---

## Deliverable 4 — Signals Conditioning a *Return Distribution* (not a point forecast)

### Established. `[High]`

- **Conditional density / regime-switching forecasting is a mature, peer-reviewed field.**
  - **Hamilton (1989), "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle," *Econometrica* 57(2):357–384** — the foundational Markov regime-switching paper. Hamilton's own survey ("Regime-Switching Models," prepared for the *New Palgrave Dictionary of Economics*, 2005) is a clean primary reference. `[High]`
  - **Hamilton & Susmel (1994), "Autoregressive conditional heteroskedasticity and changes in regime," *Journal of Econometrics* 64:307–333** — first combination of regime-switching with ARCH; the ancestor of regime-switching GARCH.
  - Regime-switching models produce **mixture distributions** — non-Gaussian even when each component is Gaussian. Conditioning on a regime *is* conditional-density forecasting. This is precisely the "distribution, not point forecast" object.
- **GARCH-X / conditional CVaR with covariates:** GARCH-X (GARCH with exogenous covariates in the variance equation) is standard; conditional/dynamic CVaR with covariates (CAViaR-style and its ES extensions — Engle & Manganelli 2004, "CAViaR," *JBES* 22(4):367–381; and dynamic-ES extensions) are established peer-reviewed methods. Conditioning a *tail* measure on covariates is therefore not novel.
- **Analog / nearest-neighbour conditional forecasting** has a real pedigree: Lorenz (1969) introduced analog forecasting; the dynamics-adapted-kernel analog work (Zhao & Giannakis, arXiv:1412.3831) is the rigorous modern treatment. Recent finance applications: "History Rhymes: Macro-Contextual Retrieval for Robust Financial Forecasting" (arXiv:2511.09754, Nov 2025) and "Regime-aware financial volatility forecasting via in-context learning" (arXiv:2603.10299). **Caveat: these recent finance applications are arXiv preprints — not peer-reviewed — and < 12 months old. Treat as `[Unverified — preprint]`.**

### Empirical Evidence

- Regime-switching conditional density (Hamilton lineage): `[Out-of-sample backtest]`, extensively replicated, peer-reviewed.
- GARCH-X / conditional-ES: `[Out-of-sample backtest]`, peer-reviewed.
- kNN/analog conditional density *in finance specifically*: `[Backtest]` only, and the most relevant sources are unreviewed preprints.

### Regime Sensitivity

- Regime-switching models suffer **regime-identification lag** — the filter knows the regime *probabilistically and with delay*; you act on a smoothed/filtered estimate, not the true state.
- Markov-switching MLE has known **small-sample and label-switching pathologies** (Cappé–Moulines–Rydén; arXiv:1705.10445 on asymptotics).
- **kNN/analog forecasting is distinct from parametric regime-switching** and carries the curse-of-dimensionality risk discussed in Deliverable 6 — the "regime matching" in the architecture is the analog branch, which is the *less* peer-validated of the two.

**Verdict:** Conditioning a return *distribution* on signals is **well-established** in the parametric regime-switching / GARCH-X form. The specific *kNN-analog* realization is established as a concept but, for finance, rests on recent unreviewed preprints — `[Established as concept / weak peer-reviewed evidence in the kNN-finance form]`.

---

## Deliverable 5 — THE COMPOSITION

### Has anyone published or institutionally documented this exact full stack?

**No evidence found that the full stack — heuristic-features → regime-conditioned path simulation → CVaR-constrained, EU-objective exit, arbitrated by a deterministic resolver — has been published as a unit or institutionally documented as a unit.** Searches across SSRN-style queries, arXiv, and practitioner literature returned the *components* abundantly and the *composition* not at all.

**Honest characterization: this is a bespoke composition of individually-proven (or individually-plausible) parts.** That is not a criticism in itself — most production quant systems are bespoke compositions — but it must not be presented as "an established architecture." It is not.

### Closest published analogues (in descending similarity)

1. **CVaR-constrained safe reinforcement learning.** "CVaR-Constrained Policy Optimization for Safe RL" (IEEE TNNLS, 2024); "Policy Gradients for CVaR-Constrained MDPs" (Prashanth & Ghavamzadeh, arXiv:1405.2690); "Risk-Averse Bayes-Adaptive RL" (arXiv:2102.05762, uses Monte-Carlo tree search for CVaR). These pair a **CVaR hard/soft constraint** with an **expected-return objective** over **Monte-Carlo-simulated futures** — structurally the closest published object to primitives (2)+(3)+the simulator. **Difference:** the policy is *learned*, the simulator is an MDP transition model (not a kNN analog retriever), and the application is not position exit. `[Medium]` analogy strength.
2. **"Adaptive Insurance Reserving with CVaR-Constrained RL under Macroeconomic Regimes" (arXiv:2504.09396)** — combines CVaR constraints + regime conditioning + a sequential decision. Closest published object that has *both* CVaR-constraint and regime-conditioning together. **Difference:** insurance reserving, not trading exit; regimes are macro states, not kNN-retrieved analogs. `[Medium]`.
3. **Risk-sensitive optimal execution with a CVaR objective** (arXiv:2201.11962) — CVaR + sequential trading decision. **Difference:** CVaR is the *objective*, not a *constraint*; execution scheduling, not discretionary exit.
4. **Macro-contextual / analog retrieval forecasting** (arXiv:2511.09754; 2603.10299) — the regime-matching-conditions-a-distribution branch. **Difference:** forecasting only; no CVaR constraint, no EU exit objective, no decision arbitration.
5. **Scenario-based / chance-constrained programming** (Rockafellar–Uryasev lineage) — provides the "simulate scenarios → enforce a CVaR constraint" template. **Difference:** static portfolio optimization, not a per-position sequential exit with a path simulator.

### What this means

- The **constraint+objective decision pattern** (a hard CVaR constraint plus an EU/expected-return objective) is a recognizable, published pattern — it appears verbatim in safe-RL. The architecture's items (2)+(3) are a *re-skinning* of that pattern for exits.
- The **simulator** branch — using **kNN analog retrieval** as the path generator that the CVaR/EU stack consumes — is where the composition departs from published analogues. Safe-RL uses learned transition models or MDP simulators; analog-retrieval forecasting does not bolt a CVaR-constrained decision onto its output. **The joint of "kNN analog path ensemble" → "CVaR-constrained EU exit" is the genuinely novel seam.**
- The **deterministic priority resolver** arbitrating a hard constraint vs a soft objective is standard constrained-optimization hygiene (a feasibility-first lexicographic ordering) — not novel, but also not the interesting part.

**Verdict:** `[Medium]` confidence that the composition is **novel** (well-evidenced by absence). `[Low→Medium]` confidence on whether the novel seam is *sound* — the safe-RL analogues suggest the constraint+objective half is reasonable; the kNN-path-generator half is the unproven joint and is contra-indicated by Deliverable 6.

---

## Deliverable 6 — Evidence AGAINST

### 6a. Curse of dimensionality / correlated features into a kNN regime matcher — REAL and documented

This is the strongest body of adverse evidence and it lands directly on primitive (1)→ the matcher.

- **Distance concentration.** In high dimensions, all pairwise distances converge toward a common value; the ratio (max distance − min distance)/min distance → 0. The classic references: **Beyer, Goldstein, Ramakrishnan & Shaft (1999), "When Is 'Nearest Neighbor' Meaningful?," *ICDT 1999*** — proves nearest-neighbour becomes ill-defined as dimensionality grows; and **Aggarwal, Hinneburg & Keim (2001), "On the Surprising Behavior of Distance Metrics in High Dimensional Space," *ICDT 2001***. `[High]` `[Theoretical, replicated]`
- **How few dimensions before it bites?** Multiple sources (Cornell CS4780 lecture notes; arXiv:2401.00422 "Interpreting the Curse of Dimensionality from Distance Concentration") indicate meaningful distance concentration can appear with **as few as ~10–15 dimensions** for many data distributions. There is **no universal hard number** — it depends on the intrinsic dimensionality and correlation structure of the data — but the practitioner rule of thumb that kNN degrades "somewhere in the low tens of features" is consistent with the literature. `[Medium]` (rule-of-thumb; data-dependent).
- **Correlated conditioning features are doubly bad.** Correlated features (a) inflate *nominal* dimensionality without adding *intrinsic* dimensionality — so you pay the distance-concentration cost without buying discriminating power; and (b) implicitly **re-weight the distance metric** toward whatever underlying factor the correlated cluster proxies, so the "nearest neighbour" is dominated by a redundant axis. Hand-crafted trailing-stop / VWAP-breakdown / volatility-ratchet signals are **strongly mutually correlated** (all are functions of recent price, trend, and volatility) — this is precisely the redundant-cluster case. The Fundamental-Law breadth literature (Clarke–de Silva–Thorley 2006; "fallacies of breadth," *JAM* 2020) makes the same point in the alpha-combination context: **correlated inputs overstate effective breadth.**
- **Conditioning-feature dilution.** kNN requires a candidate to be close in *every* dimension simultaneously; each added irrelevant or redundant feature makes a genuinely-similar historical analog *harder* to find. The distance becomes "dominated by the large number of irrelevant attributes" (Cornell CS4780; Baeldung CS). So adding heuristic features to "improve" regime matching can **degrade** it past a small number — the opposite of the intuition that more conditioning is better.
- **Sample-size compounding.** A path simulator that retrieves analogs from ~125 trading days of history (per the AlphaBot autotuner horizon) has a **very small reference set**. Distance concentration plus a small N means the "k nearest neighbours" may be barely distinguishable from k random draws once feature count climbs. With ~125 days and low-tens of features, the regime matcher is operating in exactly the regime the literature flags as unreliable. `[High]` concern — this is the single most important adverse finding.

**Mitigations the literature offers (stated as options, not recommendations):** dimensionality reduction before matching (PCA / autoencoder to intrinsic dimensionality); supervised metric learning so the distance is return-relevant; aggressive feature selection / orthogonalization (López de Prado Ch. 8); using few strong features rather than many weak ones (Alpha Architect). The report does **not** recommend any of these — it notes only that the pathology is recognized and that the literature treats unmitigated high-dimensional kNN as a known failure mode.

### 6b. Criticism of CVaR-triggered / utility-based exits in live trading

- **CVaR estimation error feeds straight into the trigger.** As in Deliverable 1: CVaR depends on the *shape* of the tail beyond VaR; with limited data the tail is poorly estimated; VaR is more robust to tail misestimation. A *hard exit trigger* on CVaR converts estimation noise into action noise — spurious exits when the tail estimate is high-variance. `[High]` (well-supported by multiple expert sources).
- **CVaR is hard to backtest** (Acerbi & Székely 2014; FRTB's own choice to backtest on VaR) — so a CVaR-triggered exit rule is itself **hard to validate out-of-sample**, which raises overfitting risk for any parameter (the tail budget, the confidence level) chosen by the autotuner.
- **Backtest overfitting** is the dominant practitioner criticism of complex multi-component trading systems: Bailey, Borwein, López de Prado & Zhu (2014), "Pseudo-mathematics and financial charlatanism: The effects of backtest overfitting on out-of-sample performance," *Notices of the AMS* 61(5); and Bailey & López de Prado's **Probability of Backtest Overfitting (PBO)** and **Deflated Sharpe Ratio**. A four-primitive stack with a kNN matcher, a simulator, a tail budget, a utility curve, and a priority resolver has a **large effective parameter count** — the literature would predict high PBO unless explicitly controlled. `[High]`.
- **Utility-based exits depend on an unobservable utility function.** Prospect-theory work shows the *shape* of the optimal exit rule changes qualitatively with the value function and reference point. The utility curve is a free modelling choice; there is no market-observable utility. An EU-crossover trigger is only as good as that assumed curve. `[Medium]`.
- **Disposition-effect literature is a double-edged citation.** Stop-loss commitment devices demonstrably reduce the disposition effect (Fischbacher, Hoffmann & Schudy; Richards et al., Bayes Business School) — supportive of *rule-based, pre-committed* exits. But "When the disposition effect proves to be rational" (*Frontiers in Psychology*, 2023) shows for professional traders the disposition effect can be rational in mean-reverting markets — i.e., a utility/path-model that recommends holding losers is not automatically a bug. The literature does **not** speak with one voice on whether sophisticated utility-based exit improves on simple rules.
- **Model risk relocation.** Replacing closed-form optimal-stopping with a simulator does not remove model risk — it moves it into the path generator. If the kNN matcher is unreliable (6a), every downstream CVaR and EU number is computed on an unreliable ensemble. **The composition's weakest link sets the ceiling for the whole stack.**

### What the evidence-against does NOT say

- It does **not** say CVaR constraints are bad — they are coherent and regulatorily mandated.
- It does **not** say signal combination is bad — it is standard.
- It does **not** say conditional-density forecasting is bad — it is established.
- The adverse evidence is **specific**: it targets (i) the kNN matcher fed many correlated features on a small history, and (ii) the conversion of hard-to-estimate, hard-to-backtest tail measures into live triggers. Those two are the load-bearing risks.

---

## Conflicts & Open Questions

- **Conflict:** The Fundamental Law says more independent signals raise IR; the breadth-fallacy and multi-signal-overfitting literature says more *correlated* signals overstate breadth and hurt out-of-sample. These are **not actually contradictory** — they hinge on *independence* — but a naïve reading of FLAM ("more signals good") directly conflicts with the kNN dimensionality evidence. Both citations are surfaced; the reconciliation is the word "independent."
- **Conflict:** Disposition-effect literature both supports pre-committed rule-based exits *and* shows utility-rational holding of losers can be correct for professionals in mean-reverting markets. Both surfaced; methodological difference is retail-vs-professional sample and market regime.
- **Open question (non-blocking):** The FRTB ES confidence level (97.5%) and liquidity-horizon mechanics were taken from secondary/expert sources; the VaR→ES replacement was confirmed from the primary BCBS d457 page. Confirm 97.5% against d457 §MAR33 before quoting as primary.
- **Open question:** The most relevant *finance* analog-retrieval papers (arXiv:2511.09754, 2603.10299) are < 12 months old and not peer-reviewed — flagged `[STALE-RISK / preprint]`; re-check for peer-reviewed versions before relying on them.
- **Adjacent (out of scope, logged):** Path-generator mechanics and the horizon convention are the sibling task's domain — not assessed here. The interaction between a 125-day reference window and kNN reliability is noted in 6a only insofar as it bears on *provenance of the matcher choice*.

---

## Source List (by tier)

**Tier 1 — Primary (peer-reviewed / standards / regulator)**
- Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk," *Journal of Risk* 2(3):21–41. https://www.financerisks.com/filedati/WP/paper/CVaR%20Portfolio%20Optimization.pdf
- Rockafellar & Uryasev (2002), "Conditional value-at-risk for general loss distributions," *Journal of Banking & Finance* 26(7):1443–1471.
- Artzner, Delbaen, Eber & Heath (1999), "Coherent Measures of Risk," *Mathematical Finance* 9(3):203–228.
- BCBS d457, *Minimum capital requirements for market risk*, 14 Jan 2019. https://www.bis.org/bcbs/publ/d457.htm
- Hamilton (1989), "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle," *Econometrica* 57(2):357–384.
- Hamilton & Susmel (1994), "ARCH and changes in regime," *Journal of Econometrics* 64:307–333.
- Engle & Manganelli (2004), "CAViaR: Conditional Autoregressive Value at Risk by Regression Quantiles," *JBES* 22(4):367–381.
- Almgren & Chriss (2000/2001), "Optimal execution of portfolio transactions," *Journal of Risk* 3(2):5–39.
- Leung & Li (2015), "Optimal Mean Reversion Trading with Transaction Costs and Stop-Loss Exit," *IJTAF* 18(3). https://arxiv.org/pdf/1411.5062
- Clarke, de Silva & Thorley (2002), "Portfolio Constraints and the Fundamental Law of Active Management," *Financial Analysts Journal* 58(5):48–66. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=934440
- Beyer, Goldstein, Ramakrishnan & Shaft (1999), "When Is 'Nearest Neighbor' Meaningful?," *ICDT 1999*.
- Aggarwal, Hinneburg & Keim (2001), "On the Surprising Behavior of Distance Metrics in High Dimensional Space," *ICDT 2001*.
- Acerbi & Székely (2014), "Backtesting Expected Shortfall," *Risk* magazine.
- Bailey, Borwein, López de Prado & Zhu (2014), "Pseudo-mathematics and financial charlatanism," *Notices of the AMS* 61(5).
- "Strategy design and the fallacies of breadth," *Journal of Asset Management* (2020). https://link.springer.com/article/10.1057/s41260-020-00193-y
- "Regime- and Tail-Dependent Performance of CVaR-Based Portfolio Strategies in Cryptocurrencies," *JRFM* 14(3):53. https://www.mdpi.com/2227-7072/14/3/53
- "Probability weighting, stop-loss and the disposition effect," *Journal of Economic Theory*. https://www.sciencedirect.com/science/article/abs/pii/S0022053118306446
- "When the disposition effect proves to be rational," *Frontiers in Psychology* (2023). https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1091922/full

**Tier 2 — Expert / books / named analysts**
- Grinold & Kahn, *Active Portfolio Management*, 2nd ed. (2000), McGraw-Hill.
- López de Prado, *Advances in Financial Machine Learning* (2018), Wiley.
- Hamilton, "Regime-Switching Models," *New Palgrave Dictionary of Economics* (2005). https://econweb.ucsd.edu/~jhamilto/palgrav1.pdf
- Bank Policy Institute, "Why Is the FRTB Expected Shortfall Calculation Designed as It Is?" https://bpi.com/why-is-the-frtb-expected-shortfall-calculation-designed-as-it-is/
- Alpha Architect, "Backtesting strategies based on multiple signals — Beware of Overfitting Bias!" https://alphaarchitect.com/backtesting-strategies-based-multiple-signals-beware-overfitting-biases/
- Cornell CS4780, "k-Nearest Neighbors / Curse of Dimensionality" lecture notes. https://www.cs.cornell.edu/courses/cs4780/2022fa/lectures/lecturenote02_kNN.html
- Uryasev, "Conditional Value-at-Risk: Algorithms and Applications." https://www2.mathematik.hu-berlin.de/~romisch/SP01/Uryasev.pdf

**Tier 3 — Community / preprints (date-flagged, not peer-reviewed)**
- "History Rhymes: Macro-Contextual Retrieval for Robust Financial Forecasting," arXiv:2511.09754 (Nov 2025) — `[preprint]`.
- "Regime-aware financial volatility forecasting via in-context learning," arXiv:2603.10299 — `[preprint]`.
- "CVaR-Constrained Policy Optimization for Safe RL," IEEE TNNLS (2024).
- Prashanth & Ghavamzadeh, "Policy Gradients for CVaR-Constrained MDPs," arXiv:1405.2690.
- "Risk-Averse Bayes-Adaptive Reinforcement Learning," arXiv:2102.05762.
- "Adaptive Insurance Reserving with CVaR-Constrained RL under Macroeconomic Regimes," arXiv:2504.09396.
- "Risk-Sensitive Optimal Execution via a CVaR Objective," arXiv:2201.11962.
- Zhao & Giannakis, "Analog Forecasting with Dynamics-Adapted Kernels," arXiv:1412.3831.
- "Interpreting the Curse of Dimensionality from Distance Concentration," arXiv:2401.00422.

**Tier 4 — Secondary synthesis**
- Quant Decoded, "VaR vs. CVaR." https://quantdecoded.com/en/var-vs-cvar-choosing-the-right-risk-measure
- A.L. Capital Advisory, "CVaR and Tail Risk." https://alcapitaladvisory.com/research/frameworks/cvar.html
- SIFMA, AnalystPrep — FRTB introductory material.
