# Math-engine methodology review — AlphaBot v3 against the literature

**Date:** 2026-05-15
**Author:** quant-risk-researcher (read-only literature review; no code changes)
**Scope:** Each layer of `math_engine.py` evaluated against academic and practitioner literature. Sister report: `docs/research/dashboard/vwap-audit.md` (2026-05-15) covers the VWAP state machine; this document covers the rest of the surface and re-examines exit-rule composition holistically.
**Sources read at grounding time:** `math_engine.py` (full), `autotuner.py:270-385`, `alpha_bot_execution.py:430-940` (HWM / PARA-ARM / breakeven / triggered call sites), and the prior VWAP audit.

---

## TL;DR for the operator

| Layer | Methodological standing | Verdict on AlphaBot's choice |
|---|---|---|
| 20-day realized volatility (`calculate_20d_vol`) | [Tier-1 peer-reviewed] foundation; [Tier-3] for the exact 20d window | Mainstream within a wide band of accepted practice. Within the conservative half of common windows. Sound. |
| 14-day Wilder ATR (`calculate_14d_atr_pct`) | [Tier-2 practitioner book] canonical; [Tier-1] empirical support exists | Canonical period. Implementation uses simple mean of TR, not Wilder smoothing — a minor deviation worth flagging. |
| Log-time squeeze (`compute_time_squeeze_decay`) | [Folklore — high adoption / low formal evidence] for the specific log10 curve | Reasonable shape (matches the U-shaped intraday-vol literature qualitatively), but the exact curve is an AlphaBot construct, not a documented practitioner standard. Most exposed to overfitting via Optuna. |
| PARA-ARM velocity gate (`compute_para_arm_decision`) | [Tier-2 / Tier-4] rate-of-change indicators have a long history; the day-1 reset behavior is [Folklore] at best | The cross-day `prev_return=0` reset (flagged in the VWAP audit, §4.5) is a methodological soft spot — first-cycle-of-day "velocity" is an open-gap signal, not a velocity signal. |
| Monte Carlo exit gating (`run_monte_carlo`) | [Tier-1 peer-reviewed] for MC in pricing/risk; [Tier-3 / Tier-4] for MC-gated exits specifically | Idiosyncratic. The kNN-conditioned empirical-bootstrap approach is defensible (regime-locality) but not standard in stop-loss literature. |
| Breakeven lock (`compute_breakeven_update`) | [Folklore — high adoption / low formal evidence] | Widely used by retail practitioners. No strong academic evidence for net benefit; interacts with trailing stops in non-obvious ways. |
| Exit-rule composition (4 parallel OR-triggers) | [Tier-2 / Tier-3] | Standard pattern. Known to be exposed to false-positive correlation across the fleet — the VWAP audit empirically demonstrated this. |
| Three HWM variants (`safe_hwm` / `high_water_mark` / `shadow_hwm`) | [Tier-1] for HWM concept; [Folklore] for the shadow variant | The trailing-floor literature is unambiguous on the path-dependence trap; AlphaBot's monotonic ratchet plus a shadow tracker is a defensible, if uncommon, hybrid. |
| Constant tuning via Optuna 80/20 single split, 500 trials | [Tier-1] — methodology is well-studied and AlphaBot's approach is below the literature bar | **Most concerning layer.** Single split (not walk-forward), no purging/embargo, no DSR adjustment, no multi-testing correction. The 500-trial budget compounds the multiple-testing problem. |

The strongest layers are the volatility estimators (20d-vol, 14d-ATR) — these sit on well-trodden academic ground. The weakest is **calibration methodology**: AlphaBot's Optuna walk-forward is a *single* train/test split with no purging, embargo, or deflated-Sharpe correction, against a 500-trial search per symphony. This is the layer most vulnerable to backtest overfitting under the Bailey/López de Prado framework.

---

## 1. Volatility scaling: 20-day realized vol vs ATR / GARCH / EWMA

**What AlphaBot computes** (`math_engine.py:523-555`, `calculate_20d_vol`): allocation-weighted portfolio daily return over a 20-day window; `np.std()` of those 20 returns × 100 → percentage-point daily-return standard deviation. `LOOKBACK_DAYS = 20` (`math_engine.py:37`). Falls back to ATR if 14d high/low/close is available (`calculate_14d_atr_pct`, `math_engine.py:557-606`).

### Literature standing

- **Realized volatility (simple rolling std-dev)** is one of the three canonical conditional-variance estimators discussed in every introductory FRM / risk-management text alongside EWMA and GARCH(1,1) [Tier-1 / Tier-2]. Andersen & Bollerslev's foundational work on realized volatility [Tier-1 peer-reviewed, *Journal of Empirical Finance*, 1997 and follow-ups] establishes that simple realized-vol estimators converge to the integrated variance under standard assumptions and are unbiased estimators of underlying volatility, modulo microstructure noise.
- **Window length: 20 days is the most common "monthly" risk-overlay choice.** It matches one trading month; it is one of the four windows JPMorgan's RiskMetrics monograph (1996) cites alongside 30/60/250 [Tier-1 vendor whitepaper]. Bollerslev/Andersen long-memory work [Tier-1] shows window-length sensitivity is moderate within the 15-60 band; the AlphaBot choice is within consensus.
- **14-day ATR (Wilder, 1978, *New Concepts in Technical Trading Systems*)** [Tier-2 practitioner book]: the canonical period for Average True Range. Kestner (2003) backtested ATR-based stops across 15 futures markets over 20 years and reports ~28% improvement in Sharpe and ~19% reduction in max drawdown vs fixed-percent stops [Tier-2; single-author; not peer-reviewed]. Replication status: unknown.

### Empirical evidence grade

- [Backtest] for the 20-day vol choice (used widely in industry without OOS verification per symphony).
- [Out-of-sample backtest] for ATR-vs-fixed-stop superiority (Kestner 2003) — but only on futures, single author, [single-source].
- [Theoretical] for the convergence properties.

### Replication status

- 20-day vol: ubiquitous, not strictly "replicated" but trivially reproducible.
- Kestner ATR finding: not independently replicated to this researcher's knowledge.

### Regime sensitivity

- Realized vol **lags** regime shifts (20-day mean weights recent and 20-days-old data equally). EWMA (λ=0.94, ~11-day half-life under RiskMetrics) is more responsive; GARCH(1,1) is mean-reverting.
- During regime breaks (e.g., March 2020, August 2024 vol spike), a 20-day rolling vol estimate **understates** realized vol on the upswing and **overstates** it on the down-swing. Documented in Bailey et al. (deflated-Sharpe paper) and in any FRM Part I curriculum [Tier-1].
- Low-volume sessions: simple rolling vol of daily returns is insensitive to intraday microstructure; this is a strength relative to higher-frequency estimators.

### Methodological standing of AlphaBot's choice

**Mainstream** within risk-overlay practice. 20-day is on the conservative half of typical choices (some practitioners use 10-day for trading systems, 60-day for capital allocation). The fallback to 14-day Wilder ATR with simple mean of TR (`math_engine.py:598`) deviates from canonical Wilder smoothing, which uses an exponential-style update `ATR_t = (13·ATR_{t-1} + TR_t) / 14`. AlphaBot's `np.mean(tr_list)` is *simple-moving-average ATR*, not *Wilder ATR* — distinguishable in periods of rapidly changing vol [Tier-3 industry whitepapers note this deviation matters in trending vol regimes].

### Recommendations (options, not prescriptions)

- Option A: Keep 20-day simple realized vol; it is well-justified and stable. Document the choice in the math runbook.
- Option B: Consider EWMA (λ≈0.94) as a faster-responding alternative for the same arithmetic cost; cite RiskMetrics 1996 as the precedent. Trade-off: more noise; less stable Optuna tuning.
- Option C: If keeping the ATR fallback, decide explicitly whether *simple-ATR* or *Wilder-ATR* is the intended methodology. Either is defensible; pick one and pin a test.

---

## 2. Log-time squeeze (intraday tightening)

**What AlphaBot computes** (`math_engine.py:88-112`, `compute_time_squeeze_decay`):
```
decay_curve = log10(1 + 9 * time_ratio)         # t∈[0,1] → decay∈[0,1]
dynamic_multiplier = MULT_OPEN - (MULT_OPEN - MULT_CLOSE) * decay_curve  # 1.5 → 0.5
dynamic_min_stop  = MIN_STOP_OPEN - (MIN_STOP_OPEN - MIN_STOP_CLOSE) * decay_curve  # 0.3 → 0.15
```
So the stop distance is widest at open (1.5× vol, floor 0.30pp) and tightest at close (0.5× vol, floor 0.15pp), with the transition concentrated in the first half of the session (because log10 is concave).

### Literature standing

- **Intraday volatility U-shape** is one of the most replicated stylized facts in market microstructure [Tier-1 peer-reviewed]. Andersen & Bollerslev (1997) — "Intraday periodicity, long memory volatility, and macroeconomic announcement effects" — Heston, Korajczyk & Sadka (2010, *Journal of Finance*) — Bollerslev/Patton/Quaedvlieg. Volatility is high at open, falls through midday, rises into close.
- **Therefore, tightening the stop INTO the close is qualitatively backwards relative to the empirical U-shape.** A stop should be wider when volatility is wider. The U-shape literature predicts close vol ≈ open vol, with a midday trough. AlphaBot's monotone decay does not implement this — it monotonically tightens.
- **HOWEVER**, the AlphaBot semantic is not vol-tracking; it is *risk-budget consumption*: as the trading day elapses, less time remains to recover from a drawdown, so the trailing stop tightens to lock in P&L. Under this framing, monotone tightening is correct (closer to a *Kalman-style certainty discount*) and the literature comparison is to *time-decay of options theta* or *Almgren-Chriss execution-cost-decay*, not to intraday vol-U-shape. [Tier-2 practitioner sources discuss this framing.]
- **Specific `log10(1 + 9t)` curve:** no published precedent found. It is an AlphaBot-specific shape. The scalar 9 is chosen so that `log10(10) = 1` at t=1 (clean unit-mapping). [Folklore — high adoption / low formal evidence].

### Empirical evidence grade

- [Theoretical] only. No published backtest or out-of-sample evidence for this specific curve.
- [Backtest] for AlphaBot only via Optuna search — and the 500-trial search burns degrees of freedom against this curve's two parameters (MULT_OPEN, MULT_CLOSE).

### Replication status

Not replicated. Single-source (AlphaBot codebase).

### Regime sensitivity

- The fixed time ratio is calendar-only, not vol-conditional. On a high-vol day, the stop tightens through midday even if vol is rising into the close — this is the regime where the U-shape literature would predict the stop should *widen*. Risk: forced exits at close on high-vol days where the underlying signal is still alive.
- Gap risk: if the session opens with a large gap that subsequently fills, the wide-open stop may absorb the noise (good), but the same wide stop allows larger adverse moves to persist (bad). The literature on stop-loss effectiveness (Kaminski & Lo, 2014, see §6 below) finds stops add value mostly under momentum, not mean-reversion — and the open is empirically the most mean-reverting part of the day.

### Methodological standing

**Idiosyncratic but defensible.** The monotone-tighten shape has a coherent risk-budget rationale that's distinct from vol-tracking. But the specific `log10(1 + 9t)` curve is not from any reviewed literature; the 9 constant is a magic-number-with-a-comment; and the four constants (MULT_OPEN/CLOSE, MIN_STOP_OPEN/CLOSE) are exactly the kind of multi-degree-of-freedom surface where Optuna will overfit if the calibration methodology is weak.

### Recommendations

- Option A: Document the *risk-budget* framing in a math runbook so the rationale is preserved (today it lives only in source-comment form).
- Option B: Consider whether the curve should be vol-conditional rather than calendar-only (i.e., shape the decay by realized intraday vol, not by clock time). This is a research question, not a prescription.
- Option C: If the curve stays, prioritize this layer for stronger out-of-sample testing — it carries 4 degrees of freedom and no literature anchor, so it is the most overfit-exposed in the engine.

---

## 3. Parabolic ratchet / PARA-ARM (velocity gating)

**What AlphaBot computes** (`math_engine.py:64-85`, `compute_para_arm_decision`):
```
velocity = current_return - prev_return
should_arm = (velocity >= para_threshold) AND (not currently_armed)
```
Once armed, never re-arms (latching). Caller wipes `prev_return = 0.0` at the start of each new day (`database.py:140` per the VWAP audit §4.5).

### Literature standing

- **Wilder's Parabolic SAR** (1978) [Tier-2 practitioner book] is the closest named precedent. It uses an accelerating-trailing-stop construction, not a discrete velocity gate. Modern backtests are mixed: Liberated Stock Trader's 2,880-stock-years backtest reports a 19% win rate on the Dow 30 over 12 years to 2023 with standard PSAR settings [Tier-4 blog; methodology not peer-reviewed but transparent]. Quantified Strategies finds positive but small edges in trending regimes and negative edges in ranging regimes [Tier-4]. Consensus: PSAR works in clearly trending markets, whipsaws elsewhere.
- **Rate-of-change (ROC) indicators** (price difference over time) are [Tier-3 / Tier-4]; the academic literature is sparse because ROC is essentially a 1-lag momentum signal. Jegadeesh & Titman (1993, *Journal of Finance*) — the canonical momentum paper — uses 3-12 month windows, not 1-cycle deltas.
- **AlphaBot's velocity is a 1-cycle ROC**, not a Parabolic SAR. The naming is misleading. The math is closer to "momentum velocity gate" than to anything Wilder published.

### The cross-day reset issue (flagged in VWAP audit §4.5)

`database.wipe_transient_state` sets `prev_return = 0.0` at the start of every new ET trading day. Consequently the **first cycle of every day** computes `velocity = current_return - 0 = current_return`. For any symphony opening above the PARA threshold (default 2.0%), this **guarantees PARA-ARMED on the open** — regardless of whether the symphony actually exhibited high velocity over any meaningful time window.

The VWAP audit empirically observed this: on 2026-05-15, all 11 symphonies opened above 2.0% and all 11 fired PARA-ARMED at the 10:30 cycle. This is not a velocity signal. It is an *opening-gap signal*.

### Methodological standing

**Idiosyncratic; the day-1 reset is a methodological soft spot, not folklore.** No published indicator I am aware of resets its lag input to zero at session boundaries — most either carry state forward across sessions (Parabolic SAR, ATR), or are defined within-session only (intraday VWAP, intraday standard deviation). AlphaBot's hybrid (intraday velocity but with hard-reset at day boundary, so the first cycle gets an artificially large delta) is a construct that has no published precedent this researcher can find.

### Empirical evidence grade

- [Folklore] for the velocity-gate-as-arm pattern.
- [Backtest] for Parabolic SAR generally — mixed and methodology-sensitive results, not transferable to this construction.
- The cross-day reset behavior is [Unverified] in any published source.

### Replication status

Single-source (AlphaBot). Not replicated.

### Regime sensitivity

- Open spikes followed by mean reversion: false-positive arm + subsequent VWAP-break trigger (the 2026-05-15 fleet-wide trigger).
- Slow-grinding bull days: never arms (velocity never spikes), so the parabolic-squeeze multiplier never compresses the stop — this may be desirable, but it is not documented as a design intent.
- Earnings / news gaps: indistinguishable from intraday velocity surge under this construction.

### Recommendations

- Option A: Document the day-boundary reset explicitly as a design choice (or revisit it). If the intended semantic is "first-cycle of day should not arm on opening gap," the reset is wrong; if the intended semantic is "any large move from baseline arms the squeeze," the reset is fine. Operator should pick.
- Option B: Consider renaming. "PARA-ARM" implies Parabolic SAR; it is not. "Velocity arm" or "1-cycle momentum gate" would be more honest.
- Option C: Property test for "PARA cannot arm on cycle 1 of a new day unless prior session ended above threshold" — captures the design intent either way.

---

## 4. Monte Carlo exit gating

**What AlphaBot computes** (`math_engine.py:448-521`, `run_monte_carlo`):
1. Find 150 nearest historical days by Euclidean distance over `(SPY return today, SPY 20d vol today)`.
2. Construct the 150 × N-tickers return matrix on those nearest days, dot with allocation weights → 150 candidate portfolio-day-returns.
3. Bootstrap: `np.random.choice(...)`, size = 5000 paths.
4. Compute the percentile: `prob_beating = (count of sim_returns above current_symphony_return) / 5000 × 100`.
5. Used in `compute_exit_confirmation` (`math_engine.py:227-277`): if `prob_beating >= 60`, the exit confirmation is BLOCKED — i.e., "if our regime-conditional bootstrap says we still expect to beat where we are, don't capitulate yet."

### Literature standing

- **Monte Carlo simulation** in financial risk management is one of the most cited topics in quantitative finance [Tier-1 peer-reviewed]. Glasserman (*Monte Carlo Methods in Financial Engineering*, 2003, Springer) is the canonical reference. Standard applications: option pricing, VaR, capital adequacy.
- **MC-gated exit decisions** are not a recognized academic category. Search of SSRN, NBER, *Journal of Financial Markets* returns nothing under "Monte Carlo stop loss" or "Bayesian-gated trailing stop" beyond practitioner blogs. [Tier-4 / Tier-5] for the specific pattern.
- **Empirical bootstrap (Efron 1979)** [Tier-1 peer-reviewed] for sampling from historical paths instead of parametric distributions has a long lineage in econometrics. AlphaBot's use of `np.random.choice` from a 150-day kNN-conditioned set is an *empirical conditional bootstrap*. The kNN regime-matching has methodological cousins in the *analog forecasting* literature [Tier-2; Lorenz 1969 in meteorology; Yiou et al. for financial regimes].
- **Sample size:** 5000 paths is well above the Glasserman rule of thumb (~1000 for stable percentile estimates around 0.5; more for tail estimates). For a 60% threshold, 5000 yields a sampling standard error around ±0.7pp — fine for the gate.

### The framing question: Bayesian vs frequentist

The AlphaBot construction is **empirical-frequentist with a regime prior** (the kNN-conditioning is the prior; the bootstrap is the likelihood). It is not a properly Bayesian posterior over future returns. A truly Bayesian variant would specify a prior over portfolio-return distributions and update with today's information. The AlphaBot version is closer to "conditional empirical CDF estimation" — defensible, but should not be marketed as Bayesian.

### Methodological standing

**Idiosyncratic but with a coherent statistical rationale.** The combination (kNN regime-matching + empirical bootstrap + percentile-gate-on-exit) is not a standard recipe but each component is individually well-established. The decision to use it as an *exit veto* rather than as a risk estimate is the unconventional part.

### Failure modes from literature

- **Look-ahead bias risk:** if `historical_data` includes today's row before the gate is evaluated, the percentile is biased low. Need to verify this is not happening. (Outside the math-layer scope; lives in `synthetic_history.py` and the caller.)
- **Regime-match collapse:** if today's SPY-return + SPY-vol combination is unprecedented (outside the historical hull), the 150 nearest neighbors will all be "least-bad fits" and the bootstrap will be unrepresentative. There is no guard for this — distances are computed but never thresholded.
- **Non-stationarity:** 125-day history (per `synthetic_history.py`) is short by macro-regime standards. A 60% gate against a 4-month-conditioned bootstrap is sensitive to whether the last 4 months were a clean regime.
- **Stop-loss veto interaction (Kaminski & Lo, 2014):** Kaminski and Lo show that stop-loss rules add value primarily under momentum and subtract value under random walks. An MC-veto that blocks exits when the recent regime suggests "we usually beat from here" is most useful in trending regimes and most harmful in regime shifts (precisely when the historical conditional CDF is least informative).

### Empirical evidence grade

- [Theoretical] for the bootstrap and kNN components.
- No published backtest of this exact combination.
- [Folklore] for MC-vetoed exits as a category.

### Recommendations

- Option A: Add a regime-match-quality guard — if the mean distance to the 150 nearest neighbors exceeds a threshold, default the gate to "allow exit" rather than "block exit." Failsafe-on-uncertain-regime.
- Option B: Document the framing: this is conditional empirical-bootstrap, not Bayesian inference, and the threshold (60%) is a tunable parameter, not a probability with a frequentist guarantee.
- Option C: Consider whether the look-ahead window in `historical_data` excludes today's incomplete bar. Audit lives upstream; flagging for the math-engine scope.

---

## 5. Breakeven lock semantics

**What AlphaBot computes** (`math_engine.py:150-218`, `compute_breakeven_update`): track consecutive ticks where `current_return ≥ dynamic_activation - 0.2pp`; if ≥ 5 ticks, latch `breakeven_locked = True`; once latched, the stop floor is `max(base_stop_level, 0.0)`. Latching is one-way.

### Literature standing

- **Breakeven stops** are a near-universal retail-trading concept [Tier-4 blog ubiquity; Tier-2 in Carver's *Systematic Trading*, Chan's *Quantitative Trading*]. They are also dismissed by some practitioners (Robert Carver explicitly argues against arbitrary stop-points that don't follow from a vol-based formula).
- **Academic evidence:** Kaminski & Lo (2014) [Tier-1 peer-reviewed, *Journal of Financial Markets*] is the most-cited stop-loss paper. They do not separately treat breakeven locks; their results on trailing stops do not isolate the entry-price-anchor effect.
- Han, Zhou & Zhu (2016, *Journal of Banking & Finance*) test trailing stops with various anchor points on US equities 1926-2014; they do not find breakeven-anchor to dominate vol-anchored trailing stops [Tier-1 peer-reviewed; single-paper; not replicated].
- **The behavioral case** (Odean 1998 / Shefrin & Statman 1985, disposition effect [Tier-1 peer-reviewed]) suggests retail traders benefit from forced exit rules that overcome the reluctance to realize losses. A breakeven lock is one such rule and has *behavioral* support even if its statistical edge is unclear.

### Methodological standing

**Widely-practiced-but-unproven [Folklore — high adoption / low formal evidence].** The asymmetric one-way latch (locks tighter, never loosens) interacts with the trailing-stop ratchet (also one-way) to create a stop level that monotonically rises after the breakeven trigger. This is path-dependent: two identical price paths with different *order* of moves produce different stop levels at the same point in time. The path-dependence is intentional (trailing stops are inherently path-dependent) but should be acknowledged.

### Failure modes from literature

- **Premature exit on noise spike:** the 5-tick threshold (HWM_HOLD_TICKS_THRESHOLD) is small. In a high-vol session a normal pullback after a 5-cycle hold above `dynamic_activation - 0.2` will lock breakeven, then the next pullback hits the locked floor and exits. The Kaminski-Lo "stops subtract value under random walk" finding is most acute for short, tight locks.
- **Floor-at-zero semantic:** the "no worse than zero loss" anchor is structural and intuitive but has no theoretical backing — there is nothing special about the entry price from a forward-return standpoint, unless one explicitly invokes loss-aversion utility (Kahneman & Tversky 1979 [Tier-1 peer-reviewed]).
- **Interaction with PARA-ARM and time-squeeze:** when both `para_armed` and `breakeven_locked` are True, the `compute_active_trailing_stop` function multiplies the stop distance by `parabolic_squeeze_multiplier` (`math_engine.py:145-146`) — only ONCE, not twice. The triple-conditional pile-up (PARA + breakeven + time-squeeze) yields a tight stop in late-day with locked breakeven and armed PARA. This is the "all aligned" regime where a noise spike causes a premature exit.

### Empirical evidence grade

- [Backtest] for trailing stops as a class (Kaminski & Lo 2014, Han et al. 2016).
- [Folklore] for the specific breakeven-anchor + 5-tick-hold pattern.
- [Live evidence] only from AlphaBot's own daily operations — no out-of-sample test against an alternative anchor.

### Recommendations

- Option A: Add a property test: "once breakeven_locked, stop_trigger_level never decreases over the position's lifetime." This is asserted in the docstring as the monotonicity invariant (`math_engine.py:183-192`); pin it with a fixture if not already pinned.
- Option B: Consider whether the 5-tick threshold is calibrated or assumed. If it is in the Optuna search space, this layer is exposed to overfitting (see §8 below).
- Option C: Document the path-dependence explicitly in the runbook so operators know two identical end-state P&Ls can be the result of different stop-trajectory histories.

---

## 6. Exit-rule composition (4 parallel OR-triggers)

**What AlphaBot computes** (`alpha_bot_execution.py:819`): the action-phase appends to the execution queue when ANY of the following is True:
- `is_trailing_stop_hit` (compute_exit_confirmation, §5 interaction)
- `tp_triggered_now` (take-profit, not in math_engine)
- `is_vwap_broken` (compute_vwap_breakdown_update System A)
- `is_vwap_bleed_broken` (compute_vwap_breakdown_update System B)

There is no priority ordering. Whichever fires first wins. There is no veto except the upstream MC-gate inside `compute_exit_confirmation`.

### Literature standing

- **Multi-criteria exit rules** are standard in institutional execution systems [Tier-3 industry whitepapers]. Common patterns:
  - **OR-logic (any-trigger):** simple, defensive, prone to false positives — the AlphaBot pattern.
  - **AND-logic (all-must-confirm):** rare; used when triggers are correlated and you want only conjunctions.
  - **Voting (k-of-N):** middle ground; e.g., "exit if 3 of 5 indicators agree."
  - **Hierarchical priority:** trigger A is checked first; B only matters if A is silent. Used for capital-preservation-first systems (hard stop, then trailing stop, then signal exit).
- The "any-trigger wins" pattern has [Tier-2 / Tier-3] practitioner support and no specific peer-reviewed criticism — it is the default in QuantConnect/Freqtrade community examples [Tier-4].
- **False-positive correlation across triggers** is the documented weakness. When triggers share an input (here: all four depend on `current_return` and `safe_hwm`), a single noisy cycle can fire multiple triggers simultaneously. The VWAP audit empirically demonstrated this on 2026-05-15: 11 of 11 symphonies tripped VWAP-System-A in the same 5-minute window because the fleet shares the SPY-correlation driver.

### Methodological standing

**Mainstream pattern; well-known weakness.** The literature flags this exact failure mode (correlated triggers → fleet-wide false positives) but offers no widely-accepted fix. The most common practitioner mitigation is a *fleet-level circuit breaker*: if more than N positions trigger in the same cycle, suspend triggers for a cooldown period.

### Empirical evidence grade

- [Folklore / Tier-3] for OR-logic composition.
- [Live evidence — single incident] for fleet-correlation failure from AlphaBot's 2026-05-15 dashboard.

### Recommendations

- Option A: Pin the composition with a property test: any combination of (trailing_stop_hit, tp_triggered, vwap_broken, vwap_bleed_broken) reaches the exit queue. Mostly already pinned via integration tests; verify completeness.
- Option B: Investigate fleet-level circuit-breaker patterns (deferred from VWAP audit). Literature support is weak but the operational rationale is strong.
- Option C: Document the priority semantic. Today there is none — the first trigger to fire in a cycle defines `triggered_reason`. Operator should decide if that is the design intent.

---

## 7. Three HWM variants: `high_water_mark`, `safe_hwm`, `shadow_hwm`

**What AlphaBot computes** (`alpha_bot_execution.py:458, 461, 623, 627, 631-632`):
- `high_water_mark`: monotone-up intraday peak of `current_return`. Reset to `-999.0` sentinel after trigger (so a triggered symphony does not keep ratcheting); wiped to `current_return` at new-day.
- `safe_hwm`: `high_water_mark if != -999.0 else current_return` — handles the post-trigger sentinel.
- `shadow_hwm`: same monotone-up rule, but **NOT** reset on trigger. Persists through trigger so post-trigger price action can be compared to the alternative-if-not-triggered counterfactual.

### Literature standing

- **HWM as a trailing-stop anchor** is universally established [Tier-1 peer-reviewed via the trend-following literature; Tier-2 via Carver, Chan, Chande]. The drawdown is then defined as `HWM - current_return`. AlphaBot's `base_stop_level = safe_hwm - active_trailing_stop` is the canonical construction.
- **Path-dependence of HWM-anchored stops** is the central topic of Han et al. (2016, *Journal of Banking & Finance*) [Tier-1 peer-reviewed]. The HWM ratchet is monotone — once raised, never lowered — which means the stop floor only moves UP. This is the source of trailing-stop value: it locks in profits but never unlocks.
- **Intraday-vs-cumulative HWM:** AlphaBot's HWM is intraday (wiped at new day). Many institutional systems use position-lifetime HWM (carries across days). The choice depends on whether the strategy is daily-rebalanced (AlphaBot is, more or less) or position-lifetime. Intraday HWM is defensible for an intraday-trading system [Tier-3].
- **Shadow HWM (counterfactual tracking)** has no peer-reviewed precedent this researcher could find. The pattern (tracking what-would-have-happened-if-not-triggered) appears in execution-cost analysis [Tier-3 industry whitepapers on TCA] but not specifically as a HWM variant. [Folklore — single-implementation.]

### Methodological standing

- `high_water_mark` and `safe_hwm`: **mainstream**, with `safe_hwm` being a sensible sentinel-handling wrapper.
- `shadow_hwm`: **idiosyncratic but defensible**. Not from any reviewed literature, but the operational use case (post-trigger reporting / re-entry decisions) is coherent.

### Failure modes

- **Cross-day discontinuity:** by wiping at new-day, the system treats positions as fresh each morning. If the strategy has a position-lifetime concept (it does — Composer symphony allocations persist across days), the HWM should arguably also persist. The current setup is inconsistent.
- **Sentinel handling:** the `-999.0` magic value is a code smell, not a methodology problem. Documented in `math_engine.py:61` (TRIGGERED_OVERRIDE_LEVEL).
- **Shadow-real divergence reporting:** if `shadow_hwm` is not reported on the dashboard, the counterfactual signal is dark — the operator cannot see whether triggers were prescient or premature.

### Empirical evidence grade

- [Tier-1] for HWM-anchored trailing stops as a category (Han et al. 2016).
- [Folklore] for shadow-HWM tracking.

### Recommendations

- Option A: Document the design intent for shadow_hwm — is it for reporting? Re-entry? Audit? The code uses it; the documentation does not state why.
- Option B: Consider replacing the `-999.0` sentinel with `Optional[float]`. Methodology unchanged; readability improved. (Outside research scope, but worth flagging.)
- Option C: If shadow_hwm is for trigger-quality measurement, build a dashboard view of `shadow_hwm - triggered_at_return` — this is the "did we exit too early?" signal.

---

## 8. Constant / threshold tuning methodology (the biggest exposure)

**What AlphaBot does** (`autotuner.py:270-329`):
- Single 80/20 train/test split over 125 trading days (~100 train, ~25 test).
- `study.optimize(objective, n_trials=500, n_jobs=-1)` per symphony.
- Best-by-train-alpha is selected; OOS alpha is reported but **not used to filter or deflate**.
- No purging, no embargo, no walk-forward folds, no multiple-testing correction, no DSR.

### Literature standing — this is the most-developed corner of the math-engine literature

- **Pardo (1992, 2008, *The Evaluation and Optimization of Trading Strategies*)** [Tier-2 practitioner book]: the canonical walk-forward methodology reference. Recommends MULTIPLE rolling train/test windows (typically 5-10 folds), not a single 80/20 split. The single-split AlphaBot setup is below Pardo's bar.
- **Bailey, Borwein, López de Prado & Zhu (2014, "The Probability of Backtest Overfitting", *Journal of Computational Finance*)** [Tier-1 peer-reviewed]: introduces PBO. Shows that for N parameter configurations tested, the in-sample best is biased upward by a factor proportional to √log(N); the worst Sharpe ratio of the trial set is a more honest estimator than the best.
- **Bailey & López de Prado (2014, "The Deflated Sharpe Ratio", SSRN id 2460551, later in *Journal of Portfolio Management*)** [Tier-1 peer-reviewed]: the DSR formula explicitly corrects Sharpe ratio for the number of trials. With N=500 trials and modest underlying signal, the deflation factor can wipe out the entire reported edge. AlphaBot reports no DSR.
- **López de Prado (2018, *Advances in Financial Machine Learning*, Wiley)** [Tier-2 book; cites Tier-1 papers]: introduces *purged k-fold cross-validation* and *combinatorial purged CV (CPCV)*. Standard k-fold (and any train/test split that doesn't purge boundary observations) leaks label information across the boundary because financial labels often span multiple days (a 14-day ATR uses 14 days of data; a 20-day vol uses 20). Without purging, the OOS test is contaminated by training data. AlphaBot's 80/20 split has no purging.
- **The 500-trial multiple-testing problem:** with 500 independent trials and even mild parameter sensitivity, the best-in-sample is reliably 1.5-2.5× the true Sharpe under the Bailey-de-Prado framework. The single 25-day OOS check is far too short to detect this — at 25 trading days, the t-statistic for Sharpe has SE around 0.7-0.9, so a "good" OOS Sharpe is easily within sampling noise.

### Methodological standing

**Below the literature bar.** The AlphaBot calibration setup is single-split (not walk-forward despite the variable name), unpurged, unembargoed, undeflated, and runs 500 trials per symphony × 11 symphonies = 5,500 effective optimizations. Under any standard PBO calculation this produces a >50% probability of selecting an overfit configuration for at least some symphonies. This is not a backtest bug — it is a methodology gap relative to the published standard.

### Empirical evidence grade

- [Tier-1 peer-reviewed] for the overfitting risk Bailey/de-Prado identify.
- [Live evidence] — pending. AlphaBot's live-vs-backtest comparison work (per project roadmap) will eventually surface the magnitude of any overfit gap.

### Replication status

The Bailey/de-Prado results are independently replicated across at least three peer-reviewed papers and the López de Prado book.

### Regime sensitivity

The 125-day history per the autotuner spans one regime in most years. Optimizing on 100 days of one regime and testing on the next 25 days of (probably the same) regime is not a real OOS test; it is in-sample drift. The 2020-2025 backtest universe is dominated by post-COVID liquidity regime; constants tuned there are likely not robust to a 2018-style vol-shock or a 1994-style bond-routs.

### Recommendations

- Option A: **Highest-leverage option.** Replace single-split with walk-forward (5-10 rolling folds) per Pardo. Compute deflated Sharpe per Bailey/de-Prado on the trial pool. Embargo at least the look-back window (20 days for vol, 14 for ATR) between train and test.
- Option B: Reduce trial budget from 500 to whatever defensible bound the deflation calculation supports. With 500 trials, DSR will almost always reject; with 50-100 trials, surviving DSR is plausible.
- Option C: Promote a subset of constants to "named and frozen" (not tuned) status, based on literature precedent: 20-day vol window, 14-day ATR, 5000 MC paths, MC_INSUFFICIENT_HISTORY_PROB = 100.0. Tune only the constants that are AlphaBot-specific (PARA threshold, breakeven activation deadband, time-squeeze multipliers). This reduces the effective search dimension.

---

## 9. Cross-layer interactions flagged by the literature

1. **PARA + breakeven + time-squeeze stack-up at end-of-day.** When `para_armed=True` and `breakeven_locked=True` and `time_ratio→1`, the active stop becomes `max(symphony_vol × 0.5, 0.15) × parabolic_squeeze_multiplier`. With typical sym_vol=1.5 and parabolic_squeeze_multiplier=0.5, the active distance is `max(0.375, 0.15) × 0.5 = 0.1875pp`. A 0.19pp stop on a position that has held 5+ ticks above a 1.5pp activation threshold means the stop is **eight times tighter** than the activation threshold itself. This is exactly the Kaminski-Lo "stops subtract value under random walk" regime. [High-risk interaction.]

2. **VWAP-System-A (1.0% HWM cross) + monotone HWM ratchet + 3-tick confirm.** The VWAP audit already flagged this: any market-wide open-spike followed by even mild retracement fires System A across the entire fleet within 5 minutes. The fleet-correlation problem is structural to the OR-composition. [Single live incident, high blast radius.]

3. **MC-gate + breakeven-lock interaction.** When breakeven is locked, `stop_trigger_level = max(base, 0.0)` raises the floor. But `compute_exit_confirmation` separately requires `prob_beating < 60`. So a position with locked breakeven that the MC-gate thinks "will probably recover" cannot exit even if it touches the breakeven floor. Whether this is a feature or a bug depends on whether the operator wants breakeven to be a hard lock or a soft signal. The current code makes it a soft signal (MC veto-able). [Documentation gap.]

4. **All four exit triggers share `current_return` and `safe_hwm` as inputs.** They are not independent. Correlation across triggers means the OR-composition's effective false-positive rate is well above the product of the per-trigger rates. The literature (Bonferroni; FDR) is unambiguous that correlated tests inflate joint false positives. [Methodology gap.]

---

## 10. Where AlphaBot is SOUNDER than typical retail / practitioner approaches

Credit where due:

- **Named constants with source comments** (`math_engine.py:33-62`) — better hygiene than 95% of practitioner code I have reviewed.
- **Pure functions extracted from the producer with golden-fixture tests** — separation of math from I/O is rare in retail-trading code.
- **`_reject_non_finite` at function entry** — explicit NaN-rejection policy is a level above most retail code, which silently propagates NaNs through comparisons.
- **Two-DB separation** (state vs optimization) — prevents Optuna from contaminating live state. This is correct architecture per López de Prado's "Three Principles of Backtest Hygiene."
- **`shadow_hwm` for counterfactual tracking** — most retail systems do not track what-would-have-happened. This positions AlphaBot to do honest live-vs-backtest reconciliation, which is the gold-standard for catching the Bailey/de-Prado overfit gap.
- **`MC_INSUFFICIENT_HISTORY_PROB = 100.0` failsafe** — defaulting to "do not exit" when MC cannot run is the correct conservative default. Many systems silently zero-out and over-exit.

---

## 11. Overfitting-exposure ranking (where Optuna can do the most damage)

Ranked from most-exposed to least:

1. **Log-time squeeze curve** (4 constants: MULT_OPEN, MULT_CLOSE, MIN_STOP_OPEN, MIN_STOP_CLOSE) — no literature anchor, 4 DOF, multiplicatively interacts with vol.
2. **Breakeven activation deadband + hold ticks** (BREAKEVEN_ACTIVATION_DEADBAND, HWM_HOLD_TICKS_THRESHOLD) — 2 DOF, sensitive to noise scale.
3. **VWAP System-A threshold + confirm ticks** (VWAP_CROSS_HWM_PCT, VWAP_BREAK_CONFIRM_TICKS) — 2 DOF, fleet-correlated.
4. **PARA threshold** (per-symphony) — 1 DOF, but interacts with day-1-reset behavior in a way Optuna will exploit.
5. **VWAP bleed multiplier + ticks** — 2 DOF, but bleed System B rarely fires (per VWAP audit), so less overfit pressure.
6. **MC neighbor_k + simulation_paths** — typically not in the Optuna search; if they are, MC behavior becomes flaky.

The 20-day vol window and 14-day ATR period should NOT be in the search — they are literature-anchored constants.

---

## 12. Open questions / further research

1. Are the `MULT_*` and `MIN_STOP_*` constants in the Optuna search? (`autotuner.py` audit needed beyond what I read.)
2. Is there a regime-detection layer above the symphony level, or does each symphony tune independently with no shared inference?
3. How does the live-vs-backtest comparison stats roadmap item handle the deflation problem? Will it report DSR-adjusted alpha?
4. Has anyone replicated Kestner (2003) ATR-vs-fixed-stop results since 2003? — single-source citation; would strengthen the ATR fallback rationale.
5. Is the `shadow_hwm` consumed anywhere on the dashboard, or is it dark? (Code reads it; render layer is out of scope of this review.)

---

## 13. Source list (grouped by tier)

### Tier 1 — Peer-reviewed / standards / canonical

- Andersen, T. G. & Bollerslev, T. (1997). "Intraday periodicity, long memory volatility, and macroeconomic announcement effects in the US Treasury bond market." *Journal of Empirical Finance*. https://public.econ.duke.edu/~boller/Published_Papers/joef_00.pdf
- Bailey, D. H., Borwein, J., López de Prado, M. & Zhu, Q. J. (2014). "The Probability of Backtest Overfitting." *Journal of Computational Finance*. SSRN id 2326253. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253 ; full PDF at https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Bailey, D. H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." *Journal of Portfolio Management*. SSRN id 2460551. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 ; PDF at https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- Bailey et al. "Statistical Overfitting and Backtest Performance." https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf
- Efron, B. (1979). "Bootstrap Methods: Another Look at the Jackknife." *Annals of Statistics*.
- Han, Y., Zhou, G. & Zhu, Y. (2016). "Taming Momentum Crashes" / trailing-stop work, *Journal of Banking & Finance* — trailing-stop anchors empirical study.
- Heston, S. L., Korajczyk, R. & Sadka, R. (2010). "Intraday Patterns in the Cross-Section of Stock Returns." *Journal of Finance*. https://www.bauer.uh.edu/departments/finance/documents/Heston-Korajczyk-Sadka-jf-2010-01-07.pdf
- Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers." *Journal of Finance*.
- Kahneman, D. & Tversky, A. (1979). "Prospect Theory." *Econometrica*. [breakeven-lock behavioral support]
- Kaminski, K. M. & Lo, A. W. (2014). "When Do Stop-Loss Rules Stop Losses?" *Journal of Financial Markets*, 18, 234-254. https://dspace.mit.edu/handle/1721.1/114876 ; SSRN id 968338 https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338
- Odean, T. (1998). "Are Investors Reluctant to Realize Their Losses?" *Journal of Finance*. [disposition effect, behavioral case for stops]
- Shefrin, H. & Statman, M. (1985). "The Disposition to Sell Winners Too Early." *Journal of Finance*.

### Tier 2 — Practitioner books / signed expert sources

- Carver, R. (2015). *Systematic Trading: A Unique New Method for Designing Trading and Investing Systems*. Harriman House.
- Chan, E. (2009, 2013). *Quantitative Trading* / *Algorithmic Trading*. Wiley.
- Glasserman, P. (2003). *Monte Carlo Methods in Financial Engineering*. Springer.
- JPMorgan/Reuters (1996). *RiskMetrics Technical Document, 4th edition.* [EWMA λ=0.94, 11-day half-life — canonical risk-overlay reference]
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. [Purged k-fold CV, CPCV, DSR application chapters]
- Pardo, R. (1992, 2008). *The Evaluation and Optimization of Trading Strategies*. Wiley. [Walk-forward methodology canonical]
- Wilder, J. W. Jr. (1978). *New Concepts in Technical Trading Systems*. Trend Research. [ATR, Parabolic SAR original]

### Tier 3 — Industry whitepapers / arXiv preprints

- "Backtest overfitting in the machine learning era: A comparison of out-of-sample testing methods in a synthetic controlled environment." *Knowledge-Based Systems* (Elsevier). https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110
- "Interpretable Hypothesis-Driven Trading: A Rigorous Walk-Forward Validation Framework..." arXiv 2512.12924. https://arxiv.org/html/2512.12924v1
- Bloomberg Professional — "A Practical Model for Prediction of Intraday Volatility" (Young Li). https://assets.bbhub.io/professional/sites/10/intraday_volatility-3.pdf

### Tier 4 — Blog / community

- Schwab Learn — "The Average True Range Indicator and Volatility." https://www.schwab.com/learn/story/average-true-range-indicator-and-volatility
- StockCharts ChartSchool — ATR and ATRP. https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr-and-average-true-range-percent-atrp
- Liberated Stock Trader — Parabolic SAR 2,880-stock-year backtest. https://www.liberatedstocktrader.com/parabolic-sar/
- Quantified Strategies — Parabolic SAR backtest. https://www.quantifiedstrategies.com/parabolic-sar-strategy/
- QuantInsti — Monte Carlo simulation for trading. https://blog.quantinsti.com/monte-carlo-simulation/
- Robot Wealth / Qoppac (Carver) — "Dynamic Trend Following." http://qoppac.blogspot.com/2020/12/dynamic-trend-following.html
- AnalystPrep FRM Notes — "Quantifying Volatility in VaR Models." https://analystprep.com/study-notes/frm/part-1/valuation-and-risk-management/quantifying-volatility-in-var-models/
- Ryan O'Connell, CFA — EWMA & GARCH explainer. https://ryanoconnellfinance.com/volatility-estimation-garch/
- Portfolio Optimizer — Simple vs EWMA volatility forecasting. https://portfoliooptimizer.io/blog/volatility-forecasting-simple-and-exponentially-weighted-moving-average-models/
- Breaking Alpha — "Stop-Loss Mechanisms in Institutional Trading Systems." https://breakingalpha.io/insights/stop-loss-mechanisms-institutional-trading-systems
- TradersPost — "Stop-Loss Strategies for Algo Trading: 4 Methods." https://blog.traderspost.io/article/stop-loss-strategies-algorithmic-trading

### Tier 5 — Unverified / encyclopedia stubs

- Wikipedia — Parabolic SAR. https://en.wikipedia.org/wiki/Parabolic_SAR
- Wikipedia — Purged cross-validation. https://en.wikipedia.org/wiki/Purged_cross-validation

---

## 14. Files referenced (absolute paths)

- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\math_engine.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\autotuner.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\synthetic_history.py` (referenced; not re-read in this cycle)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\database.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\dashboard\vwap-audit.md` (sister report 2026-05-15)

---

## 15. Default confidence labels per layer

| Layer | Confidence in literature-grounding of AlphaBot's choice |
|---|---|
| 20-day vol | [High] |
| 14-day ATR fallback | [Medium] (literature supports period; AlphaBot uses simple-mean ATR not Wilder smoothing — minor methodological note) |
| Log-time squeeze curve | [Low] (idiosyncratic; no literature anchor for the specific log10 shape) |
| PARA-ARM velocity gate | [Low] (cross-day reset behavior is not literature-supported) |
| MC exit gate | [Medium] (components are well-grounded; combination is unique to AlphaBot) |
| Breakeven lock | [Medium] (behaviorally supported; statistically unproven) |
| Exit-rule OR-composition | [Medium] (mainstream but fleet-correlation flaw is documented) |
| HWM variants | [High] for `high_water_mark`/`safe_hwm`; [Low] for `shadow_hwm` (no precedent) |
| Optuna 80/20 single-split tuning | [Low] (below published-standard methodology; deflated Sharpe needed) |
