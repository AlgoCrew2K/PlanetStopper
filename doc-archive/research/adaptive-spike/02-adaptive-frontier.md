<!-- ARCHIVED from research/adaptive-spike @ 7683c30, original date 2026-05-30. Adaptive frontier research: REACTIVE vs ADAPTIVE-LEARNED tiers; external_data daily-only boundary; ~1-knob budget derived from regime-count side. Direction records in memory/project_adaptive_exit_direction.md. -->
# Adaptive Frontier — The Honest Maximum of Adaptivity (task #2)

**Researcher:** adaptive-frontier-researcher (Agent Team `adaptive-spike`, read-only)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2 (no app code changed)
**Framing mandate:** the user is NOT giving up on adaptive. This document pushes the frontier to its *honest maximum* — what adaptivity this engine + the new `external_data` can actually support — and draws a clean line between achievable-now, theory-specified, learnable-and-validatable, and data-blocked. It does NOT recommend an implementation path (out of charter); it surfaces options + trade-offs and labels every claim by evidence grade.

**Builds on (read in full):**
- `consensus-exit-research/research/00-GATE1-RECOMMENDATION.md` — esp. §4 validatability, the ~5-15 independent-regime count, the safe-by-degeneracy principle.
- `audit-soundness/audit/00-SYNTHESIS.md` + `findings/pillar3-empirical.md` — the live regime read (intraday lag-1 AC ≈ −0.036; day-clustered Guard Alpha t=1.52, NS) and the structural data wall.

**Method discipline:** I separate FACT (cited) / INTERPRETATION (labeled) / OPTIONS (never "do X"). Every empirical claim is graded `[Theoretical] / [Backtest] / [Out-of-sample backtest] / [Live evidence] / [Folklore]`. Confidence tags `[High]/[Medium]/[Low]/[single-source]`. Where reputable sources disagree, both are shown.

---

## 0. Executive summary — the honest maximum, in one screen

The frontier of *honest* adaptivity for this engine has three tiers, and the binding constraint is different in each:

1. **REACTIVE adaptivity (achievable now, theory-specified, ~0 added DoF):** the engine *already* reacts to live conditions — inverse-volatility stop scaling (`compute_active_trailing_stop`, `math_engine.py:328-372`), the √-time intraday squeeze (`compute_time_squeeze_decay`, `:294-325`, *zero free parameters*), the MC gate, and the breakeven latch. A **theory-specified regime→response function** can honestly extend this *one defensible notch* — by making the *existing* knobs condition on a regime label drawn from a **fixed, non-estimated** classifier (e.g. realized-vol terciles or a breadth threshold). This adds adaptivity **without adding validation burden**, because a theory-fixed map estimates nothing. The honest ceiling here is set by *theory quality*, not data: the best-supported move is **scale risk down when volatility is high** (Moreira-Muir 2017 `[Out-of-sample backtest, contested]`) and **let stops fire only where the underlying trends** (Kaminski-Lo 2014 `[Theoretical+Backtest, High]`). The catch: the operator's symphonies measure as the *wrong* (mean-reverting) intraday regime (pillar3 §5a), so the upside leg of any regime→response map is betting against the measured data.

2. **LEARNED-at-daily-resolution (mostly data-blocked, a sliver survives):** can a *learned* regime-response map be validated on the **real** broad daily history? The honest parameter budget is brutal: independent regimes — not rows — are the binding count, and they stay **~5-15** even with 41.2M rows, because the rows are autocorrelated across time and correlated across tickers (Gate-1 §4.2; pillar3 §3). A Markov regime-switching estimator needs **100-200 observations *per regime*** to be stable (`[Medium]`, practitioner+textbook). The real broad daily history *can* supply that for a **2-state classifier** (decades × ~252 days/yr ≫ 200/regime) — so **learning the regime *labels* is feasible**; what is **not** feasible is learning a *rich* regime→**exit-response** mapping, because the response must be validated against the engine's *intraday exit* outcomes, of which there are ~5-15 independent units. **Net: you can honestly learn a coarse (2-3 state) daily regime classifier; you cannot honestly learn more than ≈1 response-parameter hung off it.** That is the same ~1-knob budget the Gate-1 doc reached for consensus weights, arrived at here independently from the regime-count side.

3. **DATA TO EXTEND THE FRONTIER (definitive answer):** the binding gap is **intraday depth** — the engine stops *intraday*, but `external_data` is **daily-only**, and the live record is ~6 days. **Decades-deep, broad-universe *intraday* data does NOT exist as the `external_data` daily series does.** The deepest broad-universe intraday available *anywhere* is ~1993 (tick-by-tick, TAQ/TickData) / ~2004 (one-minute bars) — **2-3 decades, purchasable not free, and still not matched to *these symphonies'* holdings or the engine's minute cadence** (TickData/WRDS, `[High]`). So the daily-depth frontier (1885→2026) **cannot be ported to the intraday horizon the engine actually trades.** This is a hard ceiling, stated plainly.

**The single honest sentence:** *You can make the engine's already-reactive knobs condition on a theory-fixed regime label today (notch 1), and you can learn a coarse daily regime classifier from the real broad history (a piece of notch 2) — but the response that classifier drives must stay ≈1 parameter, and the intraday depth needed to validate a richer exit-response simply does not exist at the daily series' scale (notch 3).* Adaptivity is not dead; it is **bounded to "theory-shaped reactivity + a coarse learned regime label + ≤1 learned response knob,"** and everything past that line is the data wall the project already mapped twice.

---

## 1. REACTIVE adaptivity — what the engine already does, and how far theory can push it

### 1.1 FACT: the engine is *already* reactive (the baseline the frontier extends)
The vision word "adaptive" is partly already realized — the engine reacts to live conditions every minute, with **zero estimated parameters in the reaction curves themselves**:

| Reactive layer | Mechanism | Free params | Code | Grade |
|---|---|---|---|---|
| **Inverse-vol stop scaling** | stop distance `= max(safe_vol × dynamic_multiplier, dynamic_min_stop)` — wider stops when vol is high | multiplier is tuned, *curve* is linear | `math_engine.py:328-372` | reactive, in-form sound |
| **√-time intraday squeeze** | `decay = 1 − √(1 − t)`; multiplier interpolates MULT_OPEN(1.5)→MULT_CLOSE(0.5) over the session | **zero** (closed-form THEORY) | `math_engine.py:231-325` | `[Theoretical, sound]` |
| **MC recovery gate** | suppress exit when a Monte-Carlo "could recover" probability clears a bar | gate params | `math_engine.py` MC layers | reactive veto |
| **Breakeven latch** | one-way lock to breakeven after HWM-hold ticks | clamps | `math_engine.py:375+` | reactive latch |
| **14-day ATR fallback** | vol-of-range proxy when high/low present | — | `math_engine.py:1139+` | reactive |

> **INTERPRETATION (labeled):** "adaptive" in the user's sense likely means *more* than this minute-by-minute reactivity — it means the engine's *parameters* shift with the *regime*. That is the frontier. The reactive layers above are the **floor** the frontier builds on, and crucially they are honest *because the reaction curves are theory-fixed, not fitted* (the √-time curve has literally zero free constants). Any frontier extension that preserves that property inherits its honesty.

### 1.2 Which REGIME INDICATORS can be defined + validated on `external_data` daily breadth?

**Definable now (computable from the daily series), graded by how much each can be *validated*:**

| Candidate regime indicator | Computable from `external_data`? | Validation status | Grade |
|---|---|---|---|
| **Realized-volatility state** (rolling σ of daily returns, terciles/2-state) | YES — `daily_return` column, broad cross-section | Volatility is *highly persistent* and forecastable — the one regime variable with strong support | `[Out-of-sample backtest, High]` that vol is predictable; see §1.3 |
| **Cross-sectional breadth** (% of universe up / advance-decline) | YES — broad cross-section per `date` | Breadth as a *regime label* is largely practitioner folklore; little formal OOS validation surfaced | `[Folklore — high adoption / low evidence]` |
| **Trend / time-series-momentum state** (sign of trailing N-day return on a market proxy) | YES (on a proxy index built from the cross-section) | TS-momentum is robust **at monthly horizon, on futures** (Moskowitz-Ooi-Pedersen 2012); *not* established intraday on equities | `[Out-of-sample backtest, High]` at monthly/futures; **not transferable to intraday equity** |
| **Dispersion / correlation state** (cross-sectional return dispersion) | YES — broad cross-section | Diagnostic; no clean exit-relevant validation | `[Theoretical]` only |

> **Hard constraint carried from recon + Gate-1:** ticker breadth is *large* (12,934 tickers) but tickers are *correlated*, so the count of **independent regime units** stays **~5-15**, NOT thousands. Breadth gives you a *wider cross-section per day*, it does **not** multiply the independent *time* units a regime classifier's validation rests on (same "two n's" caveat as Gate-1 §1/§7). And the **7% synthetic rows must be excluded** from any validation — they are concentrated in leveraged/inverse ETFs (UPRO modeled to 1885, TQQQ to 1995) and are *model output, not observations*; including them would validate the synthesizer's model, not the regime signal.

### 1.3 FACT/THEORY: how far a *theory-specified* regime→response function can honestly go

The literature gives **two** regime→response directions with real academic standing, and **one** that is folklore:

**(A) Volatility-state → risk-scaling. `[Out-of-sample backtest, CONTESTED]`** — and the contest is the whole story:
- **Moreira & Muir (2017), *Journal of Finance* 72(4):1611-1644** showed that scaling exposure *down* when volatility is high raises Sharpe ratios and produces alphas across the market, value, momentum, profitability, ROE, investment, BAB, and carry — because vol changes are *not* offset by proportional expected-return changes. In-sample strong; OOS positive *if you ignore transaction costs*, smaller than in-sample. **Replication status: contested.**
- **Cederburg, O'Doherty, Wang & Yan (2020), *Journal of Financial Economics* 138(1):95-117**, on a broader **103 equity strategies**, found **no systematic OOS Sharpe improvement** — the benefit *disappears out-of-sample for most factors*. **The survivors are specific: momentum (MOM), profitability (ROE), and betting-against-beta (BAB).** **Barroso & Detzel (2021)** add that the gains **do not survive transaction costs**. **DeMiguel, Martín-Utrera & Uppal (2024), *Journal of Finance***, recover an OOS-and-net-of-cost benefit only for a *multifactor* construction, not single-factor vol management.
- **The conflict, surfaced not resolved:** Moreira-Muir says vol-scaling works; Cederburg et al. says it mostly fails OOS except MOM/ROE/BAB; the methodological difference is **breadth of test universe** (8 factors vs 103) and **OOS + transaction-cost discipline**. For Planet Stopper this is doubly important because (i) the engine *already* does inverse-vol scaling (§1.1), so the question is only whether *conditioning it on a coarse regime* adds value, and (ii) the engine's instrument is an *intraday stop*, not a monthly factor, so even the favorable evidence is **off-horizon and off-instrument** — a transfer the literature does not license.

**(B) Trend/momentum-state → permit-stops. `[Theoretical+Backtest, High]` (the cited authority):**
- **Kaminski & Lo (2014), *Journal of Financial Markets* 18:234-254** (the project's own cited authority, verified primary-source): stop-loss value is **regime-conditional on the return-generating process**. Under the **Random Walk Hypothesis, simple 0/1 stop-loss rules *always decrease* expected return**; under **momentum / regime-switching processes, stops can *add* value** (they document +50-100 bps/month during stop-out periods, monthly data 1950-2004). The paper explicitly provides guidelines under **mean-reversion, momentum, and Markov regime-switching**.
- **The honest theory-specified response this licenses:** *make the stop's aggressiveness condition on a trend/momentum regime label* — fire readily where the underlying is in a momentum regime (stops help), and **stand down / widen where it is mean-reverting (stops hurt)**. This is a direct, citable, theory-grounded regime→response map.
- **THE WALL, stated plainly:** pillar3 §5a *measured* the operator's symphonies at the engine's actual horizon — **intraday minute-return lag-1 AC ≈ −0.036, z≈−4.56, only 48.5% of series positive.** That is the **mean-reverting / random-walk** regime — the one Kaminski-Lo says stops are **neutral-to-harmful** in. So a theory-correct trend-regime→response function, applied honestly, would spend *most* of its time telling the engine to *stand down* — which is safe and correct, but is the *opposite* of the "more adaptive, more active" intuition. **The theory is sound; the measured regime points the response toward restraint, not activity.**

**(C) Breadth/ADX-multiplier folklore. `[Folklore — high adoption / low evidence]`:** the widely-practiced "tie your ATR-multiplier to an ADX/breadth-derived regime — widen in volatile/bearish phases, tighten in calm trends" pattern is **practitioner content, not validated research** (TradingView/Medium/blog tier; no peer-reviewed OOS surfaced). High adoption, low formal evidence. It can *inform the shape* of a theory-specified curve but **must not be cited as proof** the curve works.

> **The honest ceiling for notch 1 (REACTIVE, theory-specified):** you can extend the engine's existing reactive knobs to **condition on a fixed regime label** (vol-state and/or trend-state), drawing the *direction* of the response from Kaminski-Lo (stops help in momentum, hurt in mean-reversion) and Moreira-Muir (scale down in high vol). Because the classifier and the response curve are **theory-fixed (estimated nothing)**, this adds **~0 validation burden** — it inherits the √-time layer's "honest because nothing is fitted" property. **The cost is intellectual honesty about direction:** the measured regime (mean-reverting intraday) makes the *upside* leg a bet against the data, so the defensible theory-specified posture is *restraint in the measured regime*, not *more firing*.

---

## 2. LEARNED-at-daily-resolution — can a small regime→response map be genuinely learned and validated?

This is the sharpest question. The answer splits into **classifier** (learnable) and **response** (mostly not).

### 2.1 The honest parameter budget — independent regimes, not rows
- **FACT (carried from Gate-1 §4.2 + pillar3 §3, triangulated):** the binding count for *validation* is **independent regime units ≈ 5-15**, NOT the 41.2M daily rows and NOT the 12,934 tickers. Rows are autocorrelated in time and correlated across tickers; pillar3 demonstrated the collapse empirically (episode-level t=2.25 → day-clustered t=1.52). López de Prado's **≤1-parameter-per-independent-observation** ceiling therefore caps the *response* model at **≈1 learned parameter** — the same number the Gate-1 doc reached for consensus weights, reached here independently from the regime-count side. `[Methodological, High]`

### 2.2 What CAN be learned: a coarse daily regime CLASSIFIER
- **FACT:** a Markov regime-switching estimator needs **~100-200 observations per regime** for stable convergence (`[Medium]`, textbook/practitioner — MetricGate/MDPI tier, corroborated by the standard 2-state-on-1255-daily-obs S&P practice). The **real (non-synthetic) broad daily history** easily clears this for a **2-state (and likely 3-state) classifier**: decades × ~252 trading days/yr ≫ 200/regime even after excluding the 7% synthetic rows and restricting to the ~5,016 tickers with ≥10yr.
- **INTERPRETATION (labeled, grounded):** *learning the regime LABELS at daily resolution is genuinely feasible and validatable* — this is the one place the new data buys real, honest capability. A 2-3 state vol/trend HMM (or even a simple tercile/threshold rule cross-validated on the daily series) is **defensible and replicable** because the validation unit for *the classifier* is daily observations, of which there are plenty.

### 2.3 What CANNOT be honestly learned: a rich regime→EXIT-response map
- **The asymmetry that decides everything:** the classifier validates against **daily price observations** (abundant). The **response** — "in regime R, set exit-knob to value v" — must validate against the **engine's intraday exit outcomes** (Guard Alpha), of which there are **~5-15 independent units** (pillar3). You cannot borrow the classifier's abundant daily n to license the response's parameters; they validate against different, scarce, units. This is the **exact "validate the detector vs validate the estimate" relocation the project already rejected once** (`project_cvar_divergence_validation_wall`) — moving the wall onto the regime count does not escape it.
- **Net honest budget:** **a coarse learned daily classifier (2-3 states, validatable) + ≤1 learned response parameter hung off it (e.g. a single regime-contingent stop-width multiplier or a single regime-on/off gate).** Anything richer — per-regime weight vectors, per-symphony regime responses, a learned response *curve* — exceeds the ~1-parameter ceiling and re-imports overfitting on the live exit path.
- **Survival-of-honest-validation verdict:** **Yes, a sliver survives** — but it is small and it is *mostly the classifier, not the response*. The learned adaptivity that survives honest validation is "**learn the regime label well; let it move at most one theory-anchored response knob, gated by the same FDR/permission discipline the engine already uses.**" That is real, and it is more than zero — but it is **not** the rich learned-adaptive system the word "adaptive" might evoke.

> **Cross-check with acceptance-gate-designer (coordination noted):** I sent the gate-designer the proposal that the **classifier consumes ~0 DoF if theory-fixed and a budgeted amount if learned**, while the **response** consumes the scarce ~5-15-regime budget regardless. Our two deliverables should quote the **same independent-regime count and the same 7%-synthetic-exclusion rule** so the synthesizer sees one budget, not two. (Alignment requested; non-blocking open item — see §4.)

---

## 3. DATA TO EXTEND THE FRONTIER — the definitive intraday-depth answer

**The binding gap is intraday depth.** The engine trades a **minute-cadence intraday stop**; `external_data` is **daily-only** (no OHLC, no volume, no VWAP — recon-confirmed); the live intraday record is ~6 days. So the question that decides whether the frontier can be *pushed by data* is: **does decades-deep, broad-universe *intraday* data exist?**

**DEFINITIVE ANSWER: No — not at the depth or freeness of the daily `external_data` series, and not matched to this engine.** `[High]`, multi-source:

| Source | Intraday depth | Universe | Cost / access | Grade |
|---|---|---|---|---|
| **TickData (US Equities)** | **tick-by-tick since 1993**; one-minute bars built from trades follow the **1993** baseline; *25-field* minute quote bars only back to **2004** | broad (all NYSE/AMEX/NASDAQ/CTA, includes delisted → survivorship-bias-clean) | **commercial, paid** | `[High]` (vendor primary) |
| **NYSE Daily TAQ / WRDS** | monthly TAQ **1993-2014**; millisecond daily product **2001-present** | broad | **WRDS subscription** | `[High]` |
| **ISSM (historical tick)** | NYSE/AMEX **1983-1992**, NASDAQ **1987-1992** | major exchanges | research archive | `[Medium]` |
| **Alpaca minute bars** (engine's own feed) | **only a few years** back (recon-stated) | broad but recent | API | `[High]` (recon) |
| **FirstRate / algoseek / Databento** | ~20yr / 2004+ minute depending on product | broad | **commercial** | `[Medium]` |

**What this means for the frontier, plainly:**
1. **The deepest broad-universe intraday that exists anywhere is ~30 years** (tick, 1993) — **one-minute bars ~20 years** (2004) — versus the daily series' **~140 years (1885)**. The daily-depth frontier **cannot be ported to the intraday horizon.** The order-of-magnitude gap (decades vs ~century) is structural.
2. **Even the ~20-30yr intraday that exists is purchasable, not free**, and — critically — **not matched to these symphonies' holdings, allocations, or the engine's exact minute cadence.** Reconstructing the *symphony-level* intraday Guard-Alpha counterfactual over decades would require not just minute bars but the symphonies' historical compositions, which is a separate and harder reconstruction.
3. **Therefore intraday-depth acquisition does NOT escape the ~5-15-regime wall for the *exit-response* question.** More intraday history would help estimate *intraday volatility/regime structure* (a real, if costly, gain for the classifier side), but the number of **independent regime episodes** the *exit decision* faces is still gated by distinct market regimes over the window, which even 30 years of intraday data lifts to *tens*, not the ~1,000 the tail-risk machinery needs (Yamai-Yoshiba bar, pillar3 §7) and not enough to license a rich learned exit-response.

> **The one honest data move that *would* extend the frontier (option, not a recommendation):** the *daily* `external_data` (real rows, 7% synthetic excluded) **can** be used to fit and OOS-validate the **daily regime classifier** of §2.2 — that is a genuine, in-reach use of the new data. What it **cannot** do is supply the **intraday exit-outcome** observations needed to validate the *response*; for that, the only honest source is **forward live/paper accumulation** (the README's prescribed path), which accrues independent regime units at ~252 trading days/yr — meaning *years*, not a dataset purchase, is what moves the response-validation needle.

---

## 4. Achievable-now vs Data-blocked — the clean split

| Capability | Tier | Status | Binding constraint | Evidence grade |
|---|---|---|---|---|
| Minute-by-minute **reactive** stop scaling (vol, √-time, MC, breakeven) | REACTIVE | **ACHIEVED (in engine today)** | none — theory-fixed curves | `[Theoretical/Backtest, sound in form]` |
| **Theory-specified** regime→response: condition existing knobs on a **fixed** (non-estimated) regime label (vol-state / trend-state), direction per Kaminski-Lo + Moreira-Muir | REACTIVE+ | **ACHIEVABLE NOW** (adds ~0 validation burden) | theory quality + honesty that the *measured* regime is mean-reverting (favors restraint, not more firing) | direction `[High]`; net OOS value `[contested]` |
| **Learn a coarse 2-3 state daily regime CLASSIFIER** on real broad history (7% synthetic excluded) | LEARNED | **ACHIEVABLE NOW & VALIDATABLE** | classifier validates on abundant daily obs — clears the 100-200/regime bar | `[Out-of-sample backtest feasible, Medium-High]` |
| Hang **≤1 learned response parameter** off the classifier, FDR/permission-gated | LEARNED | **ACHIEVABLE but BUDGET-CAPPED at ≈1 knob** | response validates on ~5-15 independent regime units, not on daily rows | `[Methodological, High]` |
| **Rich** learned regime→exit-response (per-regime vectors, per-symphony, learned curves) | LEARNED | **DATA-BLOCKED** | exceeds ~1-param ceiling; re-imports overfitting on the live exit path | `[High]` it is blocked |
| Validate the **upside (Guard-Alpha-positive) leg** of any regime→response OOS | LEARNED/EMPIRICAL | **DATA-BLOCKED & currently contraindicated** | ~5-15 regime units; measured intraday regime is mean-reverting (unfavorable) | pillar3 `[Live evidence, t=1.52 NS]` |
| Port the **daily 1885→2026 depth to the intraday horizon** | DATA | **DATA-BLOCKED (does not exist)** | deepest broad intraday ≈1993 tick / 2004 minute; paid; unmatched to symphonies | `[High]`, vendor-primary |
| Tail-risk-conditioned response (CVaR-driven regime gating) | LEARNED | **DATA-BLOCKED (already rejected twice)** | ~1,000 tail obs needed vs ~6-37 available; do not re-litigate | `[High]` (`project_cvar_divergence_validation_wall`) |

**Regime sensitivity / where this fails (mandatory):**
- The theory-specified regime→response **fails in exactly the regime the operator is measured to be in** (mean-reverting intraday): there, Kaminski-Lo says stops are neutral-to-harmful, so the honest response is restraint, and the "adaptive upside capture" leg has negative expected value (pillar3 §5a).
- A learned daily classifier **fails at regime *transitions*** (the HMM's known weakness) and at **gap/illiquid sessions** — and `external_data` has **no volume/liquidity columns** to detect the low-volume sessions where any stop logic degrades.
- The whole frontier **fails silently if validation is run against the 7% synthetic rows** (leveraged/inverse ETFs modeled back to 1885/1995) — that validates a model, not the market. Exclusion is non-negotiable.

---

## 5. Open questions (logged, not resolved — out of charter to decide)
1. **Classifier learned vs theory-fixed.** A theory-fixed classifier costs ~0 DoF; a learned one costs budget but is validatable on daily obs. Which does the user want? (Trade-off, not a recommendation.) — alignment requested with acceptance-gate-designer.
2. **Direction-of-response honesty.** Given the measured mean-reverting intraday regime, does the user accept that an *honest* regime→response will most often counsel **restraint** (stand down / widen) rather than more-active firing? This is the same "expectation must be confronted up front" issue as Gate-1 OQ-3.
3. **Intraday-depth acquisition appetite.** Is paid ~20-30yr intraday data (TickData/WRDS) worth acquiring **for the classifier side only**, knowing it does *not* unblock the exit-*response* validation? (Cost/benefit, user's call.)
4. **Single response knob identity.** If ≤1 learned response parameter is the budget, *which* knob earns it — a regime-contingent stop-width multiplier, a regime on/off gate, or a regime-conditioned N-confirms? (A WHAT decision; interacts with the Gate-1 consensus design.)
5. **[Unverified / single-source]** the 100-200-obs-per-regime HMM stability threshold is from practitioner/textbook tier (MetricGate/MDPI), not a single canonical peer-reviewed citation — flagged for a statistician's confirmation on the build team. Carried as open, not asserted as settled.

---

## 6. Sources (graded)

**Tier 1 — peer-reviewed primary (verified):**
- Kaminski, K. & Lo, A. W. (2014). *When Do Stop-Loss Rules Stop Losses?* **Journal of Financial Markets 18:234-254.** https://dspace.mit.edu/handle/1721.1/114876 · SSRN abstract 968338. `[Theoretical+Backtest, High]`
- Moreira, A. & Muir, T. (2017). *Volatility-Managed Portfolios.* **Journal of Finance 72(4):1611-1644.** https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513 · NBER w22208. `[Out-of-sample backtest, contested]`
- Cederburg, S., O'Doherty, M. S., Wang, F. & Yan, X. S. (2020). *On the Performance of Volatility-Managed Portfolios.* **Journal of Financial Economics 138(1):95-117.** https://ideas.repec.org/a/eee/jfinec/v138y2020i1p95-117.html · SSRN 3357038. `[Out-of-sample backtest, High]` (the OOS-failure counterweight; MOM/ROE/BAB survive)
- DeMiguel, V., Martín-Utrera, A. & Uppal, R. (2024). *A Multifactor Perspective on Volatility-Managed Portfolios.* **Journal of Finance.** https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13395 · SSRN 3982504. `[Out-of-sample backtest]`
- Moskowitz, T. J., Ooi, Y. H. & Pedersen, L. H. (2012). *Time Series Momentum.* **Journal of Financial Economics.** https://www.sciencedirect.com/science/article/pii/S0304405X11002613 · SSRN 2089463. `[Out-of-sample backtest, High]` (monthly/futures — NOT transferable to intraday equity)
- Barroso, P. & Detzel, A. (2021). volatility-managed portfolios do not survive transaction costs. (cited via Cederburg-line syntheses) `[Out-of-sample backtest]` `[restatement — verify direct]`

**Tier 1 — vendor/data primary (verified):**
- TickData, *U.S. Equities* product page — tick since 1993, minute bars from 1993 baseline, 25-field minute quotes from 2004, survivorship-clean broad universe. https://www.tickdata.com/product/us-equities/ `[High]`
- WRDS / NYSE Daily TAQ — monthly TAQ 1993-2014, ms daily 2001-present. https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/nyse-trade-and-quote-taq/ `[High]`

**Tier 3-4 — practitioner / folklore (labeled, NOT cited for important claims):**
- Market-breadth & ADX-multiplier regime-filter practitioner content (QuantMonitor, TradingView, Medium, LuxAlgo, BuildAlpha). `[Folklore — high adoption / low evidence]`
- Markov regime-switching small-sample / 100-200-obs-per-regime stability (MetricGate docs; MDPI JRFM 13(12):311). `[Medium, textbook/practitioner — single-source for the exact threshold]`

**Internal (read in full):**
- `consensus-exit-research/research/00-GATE1-RECOMMENDATION.md` (§4 validatability, ~5-15 regime count, safe-by-degeneracy).
- `audit-soundness/audit/00-SYNTHESIS.md` + `findings/pillar3-empirical.md` (intraday AC ≈ −0.036; day-clustered Guard Alpha t=1.52 NS; data wall).
- Engine code: `math_engine.py:231-372` (√-time squeeze + vol-scaled stop), `:1104-1136` (`calculate_20d_vol`), `:1139+` (ATR fallback).

---

*Honest-broker note: I did not recommend a build path (out of charter). I pushed the frontier to its honest maximum — a theory-fixed regime→response notch and a learnable coarse daily classifier with a ≤1-knob response — and labeled the rich-learned-response and intraday-depth ambitions as data-blocked, with the conflicting Moreira-Muir vs Cederburg evidence shown rather than adjudicated. The user is not being told to give up on adaptive; they are being told exactly how far adaptive can honestly reach on this engine and this data, and where the wall is.*
