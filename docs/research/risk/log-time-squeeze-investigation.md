# I1 — Log-time squeeze: Literature investigation

Date: 2026-05-17
Workstream: I1 (engine-correctness-remediation)
Researcher: quant-risk-researcher

## Current implementation

File: `math_engine.py`
Function: `compute_time_squeeze_decay(time_ratio)` (lines 92-116)
Constants block: lines 52-58 (`DECAY_CURVE_SCALAR=9`, `MULT_OPEN=1.5`, `MULT_CLOSE=0.5`, `MIN_STOP_OPEN=0.3`, `MIN_STOP_CLOSE=0.15`)

Formula (verbatim):

```python
decay_curve = math.log10(1 + DECAY_CURVE_SCALAR * time_ratio)  # log10(1 + 9 * t)
dynamic_multiplier = MULT_OPEN - (MULT_OPEN - MULT_CLOSE) * decay_curve
dynamic_min_stop   = MIN_STOP_OPEN - (MIN_STOP_OPEN - MIN_STOP_CLOSE) * decay_curve
```

Where `t = time_ratio ∈ [0.0, 1.0]` is the fraction of the trading session elapsed.

**Shape summary.** `log10(1 + 9t)` is a concave function mapping `[0,1] → [0,1]` exactly (boundary-clamped: `log10(1)=0`, `log10(10)=1`). Sampled values:

| t (session fraction) | ET clock (9:30 open, 16:00 close) | decay_curve | dynamic_multiplier | dynamic_min_stop |
|---|---|---|---|---|
| 0.00 | 09:30 | 0.000 | 1.500 | 0.300 |
| 0.10 | 10:09 | 0.279 | 1.221 | 0.258 |
| 0.25 | 11:07 | 0.512 | 0.988 | 0.223 |
| 0.50 | 12:45 | 0.740 | 0.760 | 0.189 |
| 0.75 | 14:22 | 0.889 | 0.611 | 0.167 |
| 0.90 | 15:21 | 0.957 | 0.543 | 0.156 |
| 1.00 | 16:00 | 1.000 | 0.500 | 0.150 |

**Behavior summary.** The multiplier (and the min-stop floor) drops steeply in the first hour of trading and flattens through the afternoon. Roughly half of the open-to-close stop tightening is consumed within the first ~25% of the session (by ~11:07 ET); only ~24% of the remaining tightening occurs after midday. In effect, AlphaBot's trailing stops tighten *fastest at the open* and *least at the close*. This is the inverse shape of a function that would peak its tightening at the close (e.g., an exponential `1 - exp(-kt)` with `k` small, or a power curve `t^p` with `p>1`).

---

## Literature survey

### U-shaped intraday volatility (Wood, McInish & Ord 1985; Admati & Pfleiderer 1988; Andersen & Bollerslev 1997)

- **Citations:**
  - Wood, R.A., McInish, T.H., & Ord, J.K. (1985). "An Investigation of Transactions Data for NYSE Stocks." *Journal of Finance* 40(3), 723-739. DOI: 10.1111/j.1540-6261.1985.tb04996.x. [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1985.tb04996.x)
  - Admati, A.R. & Pfleiderer, P. (1988). "A Theory of Intraday Patterns: Volume and Price Variability." *Review of Financial Studies* 1(1), 3-40. [Oxford Academic](https://academic.oup.com/rfs/article-abstract/1/1/3/1601212)
  - Andersen, T.G. & Bollerslev, T. (1997). "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. DOI: 10.1016/S0927-5398(97)00004-2. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0927539897000042) — PDF at [Martin Sewell mirror](https://finance.martinsewell.com/stylized-facts/volatility/AndersenBollerslev1997b.pdf)
- **Curve form:** Intraday realized variance follows a deterministic seasonal `s(t)` shaped roughly like a "U": high near 09:30, trough near midday, secondary peak into 16:00. Andersen & Bollerslev fit `s(t)` empirically with the Flexible Fourier Form (Gallant 1981) and a cubic spline.
- **Empirical justification:** Confirmed across decades — NYSE transactions 1971-1972 and 1982 (Wood-McInish-Ord); DM/USD 1986-1996 and S&P 500 futures 1986-1996 (Andersen-Bollerslev). Mechanism (Admati-Pfleiderer): strategic clustering of liquidity traders + informed traders during periods of high expected liquidity, which themselves cluster at open and close. `[High]`, replicated repeatedly.
- **Comparison to log10(1+9t):** The U-shape implies vol is BOTH high at open AND high at close. AlphaBot's `log10(1+9t)` curve, used to tighten stops as time progresses, *implicitly assumes vol monotonically decays through the day* — it tightens hardest in the morning. The U-shape literature contradicts a monotonic-decay framing: if anything, stops should *loosen* slightly midday and re-tighten into the close, or at minimum tighten more sharply in the final 30 minutes than in the first 30 minutes.

### Heston-style intraday variance (Heston, Korajczyk & Sadka 2010)

- **Citations:**
  - Heston, S.L., Korajczyk, R.A., & Sadka, R. (2010). "Intra-Day Patterns in the Cross-Section of Stock Returns." *Journal of Finance* 65(4), 1369-1407. [Bauer/UH PDF](https://www.bauer.uh.edu/departments/finance/documents/Heston-Korajczyk-Sadka-jf-2010-01-07.pdf)
  - Heston, S.L. & Nandi, S. (2000). "A Closed-Form GARCH Option Pricing Model." *Review of Financial Studies* 13(3), 585-625. (Discrete-time GARCH option pricing; does NOT itself prescribe an intraday time-of-day curve — frequently miscited.)
- **Curve form:** Heston-Korajczyk-Sadka document periodicity in returns and volatility at a half-hour frequency, modeled as deterministic dummies. No closed-form parametric `s(t)`; the periodic component is left non-parametric.
- **Empirical justification:** Half-hour cross-sectional return periodicity across NYSE 2001-2005. `[High]` within its sample.
- **Comparison to log10(1+9t):** Provides no parametric precedent for `log10(1+9t)`. The Heston-style "deterministic intraday seasonal" formalism would model AlphaBot's decay as `s(t)` estimated from data, not a fixed concave-log form. `[Unverified]` that anyone in this literature uses a log curve specifically.

### EMA half-life decay (Carver 2015, *Systematic Trading*)

- **Citation:** Carver, R. (2015). *Systematic Trading: A unique new method for designing trading and investing systems.* Harriman House. [Harriman House](https://www.harriman-house.com/systematic-trading)
- **Curve form:** Exponentially-weighted moving average with half-life `h`: weight at age `k` is `α(1-α)^k` where `α = 1 - 2^(-1/h)`. Equivalent continuous form: `w(τ) = exp(-λτ)` with `λ = ln(2)/h`. Used by Carver to weight volatility estimates and to slow/speed signal adaptation.
- **Empirical justification:** Practitioner standard; Carver references his 10+ years at AHL/Man Group. Not specifically tied to intraday risk-overlay decay — Carver's book operates predominantly at daily frequency. `[Folklore — high adoption / low formal evidence]` for intraday stop-overlay use.
- **Comparison to log10(1+9t):** Exponential `1 - exp(-kt)` is *convex* in `t` for the first part and concave in the later part with appropriate `k`; choice of `k` determines whether tightening front-loads or back-loads. `log10(1+9t)` is monotonically concave (front-loaded tightening). The two curves are not equivalent. No primary source places either curve as *the* canonical intraday-stop decay shape.

### Linear time-to-close decay (Almgren & Chriss 2000; TWAP execution)

- **Citation:** Almgren, R. & Chriss, N. (2000-2001). "Optimal Execution of Portfolio Transactions." *Journal of Risk* 3(2), 5-39. PDF mirror: [smallake.kr](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf)
- **Curve form:** Risk-neutral limit (`λ → 0`) yields linear holdings reduction `X(t) = X₀(1 - t/T)`; i.e., uniform sell-down — TWAP. Risk-averse case (`λ > 0`) yields hyperbolic-sine front-loading. No log term anywhere.
- **Empirical justification:** Theoretical optimal under linear permanent + temporary market impact and quadratic execution-risk penalty. Foundational `[High]`.
- **Comparison to log10(1+9t):** Almgren-Chriss is an *execution-cost* model, not a *stop-tightening* model. Their linear schedule does not justify a log-shaped stop overlay. If AlphaBot wants a defensible "time-pressure" prior, the natural Almgren-Chriss-adjacent choice would be linear `t` (TWAP-like) or convex `t^p` (front-loaded), not concave-log.

### Optimal liquidation under periodic market closures (Hong & Wang 2000)

- **Citation:** Hong, H. & Wang, J. (2000). "Trading and Returns under Periodic Market Closures." *Journal of Finance* 55(1), 297-354. [MIT PDF](http://web.mit.edu/wangj/www/pap/HongWang00.pdf)
- **Curve form:** Equilibrium model — closures cause volatility clustering at open and close because most informed/allocational trade is not available overnight. No prescriptive intraday curve, but mechanism predicts the U-shape.
- **Comparison to log10(1+9t):** Reinforces the U-shape contradiction. `[High]` for the mechanism.

### Empirical S&P 500 intraday-volatility profile (ICI 2012)

- **Citation:** Investment Company Institute (2012). "Key Data Undercut Critics' Arguments on ETFs and Intraday Volatility." [ICI Viewpoints](https://www.ici.org/viewpoints/view_12_etfs_intraday)
- **Finding:** "Volatility for U.S. large-cap stocks, as measured by the S&P 500 index, in the first thirty minutes of trading (9:30 a.m. to 10:00 a.m.) was substantially higher than the last 30 minutes of trading." Mid-day to last-half-hour delta is small.
- **Comparison to log10(1+9t):** Empirically consistent with a *declining-vol-through-the-day* shape — which is roughly what `log10(1+9t)` produces for the *multiplier*. BUT: the ICI source flags morning-vol >> afternoon-vol with only a modest closing-bar uptick — the curve's directional intent (more multiplier loosening AM, more tightness PM) is broadly defensible; what's NOT defensible is the *rate* — log10(1+9t) consumes >50% of its decay budget by 11:07 ET, leaving the multiplier nearly flat through the second half of the session when the closing-auction risk actually grows. `[Medium]` — single-source within the practitioner-research category, but the underlying empirical pattern is corroborated by the academic U-shape literature.

### Other curves found

- **Power-law / Heston-Korajczyk-Sadka style dummies.** Bucket-by-half-hour deterministic adjustments — flexible but non-parametric. No log specification observed.
- **Flexible Fourier Form (Gallant 1981; applied by Andersen-Bollerslev 1997).** Trigonometric basis; flexibly fits any periodic shape including U. No closed-form intraday-decay log shape.
- **Tradespost / community "decay multiplier" rules.** Practitioner blogs describe linearly-decreasing-stop-by-N-bps-per-N-minutes (e.g., the TradersPost example: 0.085% smaller every 12 minutes). [TradersPost blog](https://blog.traderspost.io/article/stop-loss-strategies-algorithmic-trading). These are *linear* decays. `[Folklore]`.

---

## Comparative table

| Curve | Slope peak (ET) | Late-day steepness | Empirical grounding | Practitioner adoption |
|---|---|---|---|---|
| `log10(1+9t)` (AlphaBot) | 09:30 (concave, derivative max at `t=0`) | Very low (slope ≈ 1/[ln(10)·10] ≈ 0.043 at `t=1` vs ≈ 0.391 at `t=0`; ~9× flatter at close) | None directly cited in the public quant-risk literature `[single-source: AlphaBot codebase]` | Not observed in published quant libraries `[Unverified]` |
| Linear `t` (TWAP) | Constant | Constant | Optimal execution under risk-neutral linear-impact (Almgren-Chriss 2000) | Standard TWAP/VWAP scaffolding |
| Convex `t^p`, `p>1` (e.g., `t²`) | 16:00 (max derivative at end) | Highest | Matches closing-auction surge intuition; aligns with U-shape's late-day reacceleration | Common in execution algos that "rush to the close" |
| Exponential `1 - exp(-kt)` | 09:30 if `k` large; uniform if `k` small | Tunable | Carver-style EWMA precedent (daily) | Widely adopted at daily frequency |
| U-shape (Andersen-Bollerslev FFF / dummies) | 09:30 AND 16:00 | High at close, near-zero mid-day | Strongest — 40+ years of replication | Standard in academic intraday vol modeling |
| `log10(1+9t)` "inverted" — `1 - log10(1+9(1-t))` | 16:00 | Highest | Same empirical basis as convex `t^p` | None observed |

---

## Verdict

**Alternative recommended — but pending an empirical A/B test before code change.**

The current `log10(1 + 9t)` curve has **no identifiable precedent** in the academic intraday-vol literature (Andersen-Bollerslev 1997; Heston-Korajczyk-Sadka 2010; Wood-McInish-Ord 1985; Admati-Pfleiderer 1988; Hong-Wang 2000) or in the principal practitioner reference (Carver 2015). The closest practitioner cousins (TradersPost-style linear decay multipliers, EWMA half-life decays) use *different curve families*. The current shape consumes >50% of its multiplier tightening within the first 25% of the session, leaving the close (where closing-auction risk peaks per the U-shape literature) almost flat.

**Direction of the recommendation:** invert the steepness — tighten *slowly through the morning* and *rapidly into the close*. Two specific candidates worth A/B-testing:

1. **Linear (TWAP-parity):** `decay(t) = t`. Justifiable on Almgren-Chriss risk-neutral grounds. Zero free parameters beyond the existing `MULT_OPEN`/`MULT_CLOSE` endpoints.
2. **Convex power:** `decay(t) = t^p` with `p ∈ {1.5, 2.0, 2.5}`. Justifiable on U-shape-close-reacceleration grounds. One free parameter.

**Why not a full U-shape FFF or Heston-dummy schedule?** Those are higher-degree-of-freedom shapes and reintroduce overfit risk into the math layer — exactly the concern that triggered I1. Linear and low-degree power curves preserve the "single concave/convex decay" intent while gaining a published rationale.

**Citations supporting the alternative:** Almgren & Chriss (2000) for linear-TWAP grounding; Andersen & Bollerslev (1997), Wood-McInish-Ord (1985), Admati-Pfleiderer (1988), Hong-Wang (2000) for the late-day vol reacceleration that motivates a convex (close-weighted) tightening profile.

**Expected behavior difference.** Linear or convex curves will hold a *wider* trailing stop through the morning vs. the current log curve — *fewer premature mid-morning exits* on a normal-trend day where the open-hour move continues. Tightening accelerates into the afternoon, especially the final hour, so the convex variants would *capture more closing-auction reversals* than today's near-flat afternoon multiplier permits. Net effect on PnL is regime-dependent: trend-up sessions likely benefit (wider AM stop = ride the move); mean-reverting AM-spike sessions may suffer (looser AM stop catches less drawdown). Direction is testable.

**Regime sensitivity callout.** All three curves (log, linear, convex) fail equally on (a) regime shifts at the open (overnight gap days), (b) low-volume holiday sessions, (c) FOMC/CPI release days when the FFF deterministic seasonal is dominated by the announcement spike. None of the four cited periodicity papers studied stop-loss overlays specifically — extrapolation to AlphaBot's use case is `[Theoretical]` / `[Folklore]`, not `[Live evidence]`.

### Follow-up team scope (recommended)

Dispatch a Quad team (test-writer + implementer + `quant-code-reviewer` + `optuna-specialist`) for an **A/B walk-forward backtest** comparing four curves: (i) current `log10(1+9t)`, (ii) linear `t`, (iii) `t²`, (iv) `1 - exp(-3t)`. Acceptance criteria:

1. Re-run AlphaBot's existing 125-day walk-forward Optuna study (`autotuner.py`) on at least 3 symphonies covering distinct regime profiles (one trend, one mean-reverting, one mixed).
2. Hold every other math-engine constant fixed at the current production values; vary ONLY the decay-curve formula.
3. Report per-curve: Sharpe, max drawdown, hit rate, average win/loss, AND a histogram of *exit-time-of-day* — to confirm the predicted shift in exit clustering from morning to afternoon.
4. Decision rule: replace `log10(1+9t)` only if one alternative dominates on at least 2 of {Sharpe, max DD, drawdown duration} across all 3 symphonies, OR ties on those metrics while producing a more defensible exit-time histogram (i.e., closer to the U-shape close peak).
5. If no alternative dominates — keep the current curve, document this report as its empirical rationale, and retire I1 with the rationale comment below.

**Fallback rationale comment to add to `math_engine.py` IF A/B finds no improvement:**

```
# DECAY_CURVE_SCALAR = 9 yields log10(1+9t) mapping t∈[0,1] → [0,1] exactly.
# Shape: concave, front-loaded — ~50% of multiplier tightening occurs in
# the first 25% of the session. No direct academic precedent (see
# docs/research/risk/log-time-squeeze-investigation.md). Retained because
# walk-forward A/B vs. linear/convex/exponential alternatives showed no
# Sharpe/drawdown improvement on representative symphonies — workstream I1.
```

If the A/B confirms a better curve, retire the log form entirely.

---

## References

1. Wood, R.A., McInish, T.H., & Ord, J.K. (1985). "An Investigation of Transactions Data for NYSE Stocks." *Journal of Finance* 40(3), 723-739. DOI: 10.1111/j.1540-6261.1985.tb04996.x. https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1985.tb04996.x
2. Admati, A.R. & Pfleiderer, P. (1988). "A Theory of Intraday Patterns: Volume and Price Variability." *Review of Financial Studies* 1(1), 3-40. https://academic.oup.com/rfs/article-abstract/1/1/3/1601212
3. Andersen, T.G. & Bollerslev, T. (1997). "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. DOI: 10.1016/S0927-5398(97)00004-2. https://www.sciencedirect.com/science/article/abs/pii/S0927539897000042 (PDF: https://finance.martinsewell.com/stylized-facts/volatility/AndersenBollerslev1997b.pdf)
4. Andersen, T.G. & Bollerslev, T. (1998). "Deutsche Mark-Dollar Volatility: Intraday Activity Patterns, Macroeconomic Announcements, and Longer Run Dependencies." *Journal of Finance* 53(1), 219-265. https://public.econ.duke.edu/~boller/Published_Papers/jf_98.pdf
5. Hong, H. & Wang, J. (2000). "Trading and Returns under Periodic Market Closures." *Journal of Finance* 55(1), 297-354. http://web.mit.edu/wangj/www/pap/HongWang00.pdf
6. Heston, S.L., Korajczyk, R.A., & Sadka, R. (2010). "Intra-Day Patterns in the Cross-Section of Stock Returns." *Journal of Finance* 65(4), 1369-1407. https://www.bauer.uh.edu/departments/finance/documents/Heston-Korajczyk-Sadka-jf-2010-01-07.pdf
7. Heston, S.L. & Nandi, S. (2000). "A Closed-Form GARCH Option Pricing Model." *Review of Financial Studies* 13(3), 585-625. (Cited only to clarify it does NOT prescribe an intraday curve, despite frequent miscitation.)
8. Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk* 3(2), 5-39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf
9. Gallant, A.R. (1981). "On the bias in flexible functional forms and an essentially unbiased form: The Fourier flexible form." *Journal of Econometrics* 15(2), 211-245. (Source of the Flexible Fourier Form used by ref [3].)
10. Carver, R. (2015). *Systematic Trading: A unique new method for designing trading and investing systems.* Harriman House. https://www.harriman-house.com/systematic-trading
11. Investment Company Institute (2012). "Key Data Undercut Critics' Arguments on ETFs and Intraday Volatility." https://www.ici.org/viewpoints/view_12_etfs_intraday
12. TradersPost (n.d., practitioner blog). "Stop-Loss Strategies for Algorithmic Trading: 4 Methods." https://blog.traderspost.io/article/stop-loss-strategies-algorithmic-trading [Tier 4 — cited only as example of practitioner linear-decay convention.]

### Confidence tags applied

- `[High]`: refs [1]-[6], [8] — peer-reviewed, replicated.
- `[Medium]`: ref [11] — single-source practitioner-research, but the underlying empirical pattern is corroborated by [1]-[5].
- `[Folklore — high adoption / low formal evidence]`: ref [10] for *intraday* application; ref [12].
- `[Unverified]`: no peer-reviewed source uses `log10(1 + 9t)` specifically for intraday risk overlays. Single-source: AlphaBot codebase.

### Open questions (logged, not blocking)

- Whether any proprietary risk system (BlackRock Aladdin, Bloomberg PORT+) uses a log-time decay for intraday risk overlays. `[Unverified]` — proprietary, not in public literature.
- Whether `log10(1+9t)` was originally chosen for AlphaBot from a specific source or as a hand-tuned heuristic — needs DECISIONS.md / git blame archeology, out of scope for this research workstream.
