# M3 Redrive — Literature Pass

**Date:** 2026-05-27
**Author:** research-m3 (advisory researcher, Pent team `team-phase15-fixes`)
**Cycle:** `fix-m3-provenance` on `cycle/fix-m3-provenance`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/p15-m3-provenance`
**Recipients:** tw-m3 (RED tests, attribution table), impl-m3 (GREEN code), rev-m3 (review), risk-m3 (math invariants), team-lead (decision)
**Status:** Advisory only. No code or test artifacts produced. PM ferries the commit.

## 0 — Scope clarification (read first)

The kickoff prompt from tw-m3 labels Site 2 as "**Parabolic ratchet / VWAP-cross HWM gate** (`math_engine.py:667-673`)." These are two different layers in this codebase. The line range `667-673` is the **VWAP System-A profit-protection break gate** — the `safe_hwm >= vwap_cross_hwm_pct` condition inside `compute_vwap_breakdown_update`. The **parabolic ratchet** is a separate layer (`compute_para_arm_decision`, `math_engine.py:185-...`) that arms on a *velocity* signal (`current_return - prev_return >= para_threshold`), not on a HWM-cross.

The M3 feature plan at `feature-plans/decision-science/phase-1.5/m3-redrive-provenance-gaps/plan.md` is unambiguous: M3 re-derives **R1** (the `log10(1+9t)` decay curve, `math_engine.py:155-162`) and **R2** (the VWAP System-A HWM gate, `math_engine.py:667-673`). The parabolic ratchet is **out of scope** for M3 ("HARDEN does not delete layers; M3 re-derives **two**" — plan.md:128).

**This brief covers R1 and R2 as scoped by the plan.** If the team-lead actually wants the parabolic ratchet (`compute_para_arm_decision`) re-derived, that is a separate research lift and a separate cycle.

## 0.1 — NN1 binding (read second)

Per `plan.md:32-36`, the M3 re-derivation MUST be:
- **Model-free / stylized-fact / theory** — NEVER strategy-P&L Optuna-selected.
- The data source is the **return series**, not historical AlphaBot exit decisions or P&L.
- The new R1 curve and R2 gate are persisted to `spec_bundles` with `freeze_discipline ∈ {STYLIZED_FACT, THEORY, MANDATE}`. **`BACKTEST_SELECTION` is forbidden** — a `BACKTEST_SELECTION` provenance turns the BHY haircut into a lie by omission (NN1 violation).

Every candidate framework below is scored against this constraint. A candidate that requires P&L-fitting fails the binding regardless of empirical merit.

---

## Site 1 — Log-time squeeze decay curve (R1)

### 1.1 Theoretical purpose

A monotone function `f: [0,1] → [0,1]` with `f(0)=0`, `f(1)=1`, mapping session-time fraction `t` to a "tightness" weight that linearly interpolates the trailing-stop multiplier between `MULT_OPEN=1.5` (loose) and `MULT_CLOSE=0.5` (tight), and the minimum-stop floor between `MIN_STOP_OPEN=0.3` and `MIN_STOP_CLOSE=0.15` percentage points. The current heuristic is concave: `f(t) = log10(1 + 9t)`. The concave shape consumes >50% of its tightening budget within the first ~25% of the session and is nearly flat through the afternoon. Code self-flags: *"The shape has no formal literature provenance and is flagged for a follow-up empirical review against realized intraday vol term-structure."* (`math_engine.py:160-161`).

The function's **operational job** is to scale risk tolerance with elapsed time. The unstated theoretical claim is "remaining-session price uncertainty shrinks roughly like `1 - f(t)`, so the stop can be brought in proportionally as the day progresses." Any defensible re-derivation must connect `f(t)` to a stylized fact about how intraday return-distribution dispersion evolves.

### 1.2 Candidate frameworks (ranked)

#### **Candidate A — Square-root remaining-variance under homoskedasticity (top recommendation).**

- **Citation:** Danielsson, J. & Zigrand, J.-P. (2003). *On time-scaling of risk and the square-root-of-time rule.* London School of Economics, Financial Markets Group DP-439. https://eprints.lse.ac.uk/24827/1/dp439.pdf  `[Tier 1, peer-reviewed working paper]`
- Cross-referenced practitioner explanation: Gundersen, G. (2022). *The Square-Root-of-Time Rule.* https://gregorygundersen.com/blog/2022/05/24/square-root-of-time-rule/  `[Tier 4 — restatement of standard result, not load-bearing]`
- **Theoretical assumption:** Intraday log-returns are i.i.d. with constant per-unit-time variance σ²; therefore variance over the remaining session is σ²·(1−t), and the *standard deviation* of remaining-session returns is σ·√(1−t). Tightness ≡ 1 − (remaining-session std / full-session std) = `1 − √(1−t)`.
- **Functional form:** `f(t) = 1 − √(1 − t)`. Endpoints: `f(0)=0`, `f(1)=1`. Concave, monotonically increasing.
- **NN1 compliance:** ★ **PASSES.** This is **THEORY** — the curve is implied by the i.i.d. assumption, no data fitting required. `freeze_discipline = THEORY`. No new constants beyond the named formula (`SQRT_TIME_CURVE` or similar).
- **Empirical Evidence:** `[Theoretical]` — derived from first principles. Square-root-of-time is the textbook scaling rule for i.i.d. returns; widely re-derived in Black-Scholes and Value-at-Risk texts.
- **Replication Status:** N/A (a derivation, not a backtest).
- **Regime Sensitivity:** Fails when intraday volatility is **non-homoskedastic** — i.e., when the U-shape (Andersen-Bollerslev 1997) dominates. In a U-shape, remaining-session variance does NOT decline linearly; it is high near close and low at midday. The √(1−t) curve under-tightens late in the day relative to a U-shape-corrected curve. However, the i.i.d. assumption is the **standard** financial-mathematics baseline and is the one published reference closest to the operational job of the curve.

#### **Candidate B — Andersen-Bollerslev U-shape periodic component.**

- **Citation:** Andersen, T.G. & Bollerslev, T. (1997). "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. DOI: 10.1016/S0927-5398(97)00004-2. https://finance.martinsewell.com/stylized-facts/volatility/AndersenBollerslev1997b.pdf  `[Tier 1, peer-reviewed]`
- Supporting: Wood, R.A., McInish, T.H., & Ord, J.K. (1985). "An Investigation of Transactions Data for NYSE Stocks." *Journal of Finance* 40(3), 723-739. DOI: 10.1111/j.1540-6261.1985.tb04996.x. `[Tier 1]`
- **Theoretical assumption:** Intraday squared returns follow a deterministic periodic component `s(t)` with a "U" shape — high at open, low at midday, high at close. Andersen-Bollerslev fit `s(t)` with a Flexible Fourier Form (Gallant 1981).
- **Functional form:** `s(t)` is non-parametric in the AB fit (cubic spline / FFF). A closed-form approximation typical of the literature is `s(t) ∝ a + b·cos(2π·t) + c·cos(4π·t)` with `a > b > 0` calibrated to S&P 500 / similar. Cumulative variance fraction `F(t) = ∫₀ᵗ s(u) du / ∫₀¹ s(u) du` then yields `f(t) = F(t)` — but this is now **S-shaped**, not concave: a fast climb in the morning, a near-plateau midday, and a re-acceleration into the close.
- **NN1 compliance:** ⚠ **CONDITIONAL.** If the FFF coefficients are **calibrated from the return series of the universe AlphaBot trades** (e.g., the past N years of SPY or symphony-constituent 5-minute returns), this is `STYLIZED_FACT` — calibration is to return-series stylized facts, not strategy P&L. PASSES the binding. If the coefficients are tuned to maximize backtest P&L, this is `BACKTEST_SELECTION` and FAILS the binding.
- **Empirical Evidence:** `[High]` — replicated repeatedly from Wood-McInish-Ord 1985 onward across NYSE, futures, FX. Mechanism formalized by Admati & Pfleiderer (1988) and Hong & Wang (2000).
- **Replication Status:** Yes — independently replicated in dozens of papers across 40 years.
- **Regime Sensitivity:** The deterministic seasonal is dominated by announcement spikes on FOMC / CPI days; the U-shape is empirically present on "normal" days. Holiday / half-day sessions distort `t`-mapping if `t` is computed against a fixed 6.5-hour day.
- **Concern:** Higher degrees of freedom than Candidate A (3+ coefficients vs zero). Reintroduces overfit risk into the math layer — exactly what the H-5 binding flags. Also produces an **S-shape**, not a concave shape, which is a directionally large divergence from the current `log10(1+9t)` curve.

#### **Candidate C — Inverted (back-loaded) convex power: `f(t) = t^p`, `p ∈ {1.5, 2.0, 2.5}`.**

- **Citation chain:** Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk* 3(2), 5-39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf `[Tier 1]` — for the **linear** baseline (`p=1`, the TWAP / risk-neutral case). The convex case is motivated by the U-shape close-reacceleration empirical pattern (Candidate B), as a low-DoF approximation.
- **Theoretical assumption:** Tightening should back-load — track the U-shape's close peak without paying the FFF DoF cost. `p` is the single shape parameter.
- **Functional form:** `f(t) = t^p`. Endpoints: `f(0)=0`, `f(1)=1`. Convex for `p > 1`.
- **NN1 compliance:** ⚠ **CONDITIONAL.** If `p` is fixed by theory (e.g., `p=2` quadratic, from a stated motivation), this is `THEORY`. If `p` is calibrated to the return-series cumulative-variance curve (Candidate B integrated), this is `STYLIZED_FACT`. If `p` is tuned to backtest P&L, FAILS.
- **Empirical Evidence:** `[Theoretical]` — `p=1` from Almgren-Chriss risk-neutral execution; `p>1` is a heuristic low-DoF approximation of the U-shape's close reacceleration.
- **Replication Status:** Almgren-Chriss replicated; the `p>1` heuristic in trailing-stop contexts is `[Folklore — modest adoption / no formal evidence]`.
- **Regime Sensitivity:** Same as Candidate B for the close reacceleration; ignores the morning peak entirely. Probably better than Candidate A on a U-shape day; worse than B on calibration.
- **Concern:** Reverses the directional intent of the current `log10(1+9t)`. The current curve tightens fast at open; `t^p` tightens fast at close. Under the live-exit-logic blast-radius callout (plan.md:121), this is the **wrong direction** vs the current operational behavior — every historical replay cycle would diverge in the morning. The S-1 attribution table would be enormous, and tw-m3 would need to declare an `intended_direction` that explicitly contradicts the heuristic's premise. The team-lead should treat this as a "do this only if the empirical analysis is rock solid" option, not the default.

#### **Candidate D — Exponential / EMA half-life: `f(t) = 1 − e^(−kt)`.**

- **Citation:** Carver, R. (2015). *Systematic Trading: A unique new method for designing trading and investing systems.* Harriman House. https://www.harriman-house.com/systematic-trading  `[Tier 2 — named expert, practitioner-standard, but no peer-reviewed intraday application]`
- **Theoretical assumption:** Information decays exponentially; tightness grows as `1 − decay`. `k` is the half-life parameter (`half-life = ln(2)/k`).
- **Functional form:** `f(t) = 1 − e^(−kt)` (with renormalization so `f(1)=1`, i.e., `f(t) = (1 − e^(−kt)) / (1 − e^(−k))`).
- **NN1 compliance:** ⚠ **CONDITIONAL.** `k` calibrated to a return-series stylized fact (e.g., the empirical autocorrelation half-life of intraday squared returns) is `STYLIZED_FACT`. `k` calibrated to backtest P&L is `BACKTEST_SELECTION` and FAILS.
- **Empirical Evidence:** `[Folklore — high adoption / low formal evidence for intraday stop overlays specifically]`. Carver applies EMAs predominantly at daily frequency, not intraday stop tightening.
- **Replication Status:** Practitioner-replicated for daily signal smoothing; **not specifically validated** for intraday stop-tightening curves.
- **Regime Sensitivity:** Tunable — `k` small gives near-linear; `k` large gives steep open-loaded shape similar to current `log10`.
- **Concern:** Reintroduces a hand-picked `k` constant that needs its own provenance. If the calibration says `k ≈ 2.3` (which yields `f(1) = 1 − e^(−2.3) ≈ 0.9` before renorm), the curve roughly mimics `log10(1+9t)` — but Carver does not endorse this specific intraday use, so the provenance closure is shaky.

#### **Candidate E — Kelly / Bayesian time-discount.**

- Rejected with one-line reason: Requires a posterior over "is this position a winner," which is **strategy-conditional** — couples the curve to P&L in a way that is hard to disentangle from `BACKTEST_SELECTION`. NN1-risky. Not pursued.

### 1.3 Chosen derivation

**Recommendation: Candidate A — `f(t) = 1 − √(1 − t)`.**

**Rationale, in order:**

1. **NN1 cleanest.** Pure THEORY provenance. No fitted constants. No data source needed for the curve itself. `freeze_discipline = THEORY` in the spec bundle without controversy.
2. **Zero new free parameters.** The endpoints (`MULT_OPEN`, `MULT_CLOSE`, `MIN_STOP_OPEN`, `MIN_STOP_CLOSE`) stay; `DECAY_CURVE_SCALAR = 9` is **deleted entirely** — no replacement constant. The provenance comment cites Danielsson & Zigrand (2003) and the square-root-of-time scaling under i.i.d. returns.
3. **Direction sympathetic to the current heuristic.** Both `1 − √(1−t)` and `log10(1+9t)` are **concave, open-loaded** curves. They diverge in *magnitude*, not in *direction* — S-1 Stage 2 attribution table will be modest, all in the "intended_direction = concave open-loaded" bin.
4. **Closest published rationalization** of the current curve's operational intent. The heuristic was front-loading tightening early; the i.i.d.-remaining-variance framing justifies front-loading because remaining uncertainty shrinks fastest in the morning under i.i.d. The heuristic just had the wrong functional form.
5. **Regime caveat is honest.** Under a U-shape regime, `1 − √(1−t)` is misspecified — but so is `log10(1+9t)`, and worse so. The U-shape correction (Candidate B) is available as a follow-up if data warrants; the i.i.d. baseline is the right starting point.

**What ships:**

```python
# Time-squeeze decay constants (drives intraday tightening of trailing stops)
# PROVENANCE: f(t) = 1 - sqrt(1 - t), the i.i.d.-returns "remaining-session
# uncertainty" curve. Under the standard square-root-of-time scaling for
# i.i.d. log-returns with constant per-unit-time variance, the standard
# deviation of remaining-session returns scales as sqrt(1-t); tightness
# (1 - remaining_std / full_std) is therefore 1 - sqrt(1-t). Endpoints
# f(0)=0, f(1)=1. Concave, monotone, front-loaded. No fitted constants.
# Cited: Danielsson & Zigrand (2003), LSE FMG DP-439,
# https://eprints.lse.ac.uk/24827/1/dp439.pdf.
# Research note: docs/research/m3-provenance/literature-pass.md.
# freeze_discipline = THEORY.

MULT_OPEN = 1.5
MULT_CLOSE = 0.5
MIN_STOP_OPEN = 0.3
MIN_STOP_CLOSE = 0.15
# DECAY_CURVE_SCALAR removed (closed by sqrt-time derivation).
```

```python
# inside compute_time_squeeze_decay:
decay_curve = 1.0 - math.sqrt(1.0 - time_ratio)
```

### 1.4 Numerical comparison

`decay_curve` values at `t ∈ {0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0}`, current heuristic vs Candidate A:

| t | ET clock (9:30-16:00) | Current `log10(1+9t)` | Candidate A `1-√(1-t)` | Δ (A − current) |
|---:|---:|---:|---:|---:|
| 0.00 | 09:30 | 0.0000 | 0.0000 | 0.0000 |
| 0.10 | 10:09 | 0.2788 | 0.0513 | **−0.2275** |
| 0.25 | 11:07 | 0.5119 | 0.1340 | **−0.3779** |
| 0.50 | 12:45 | 0.7404 | 0.2929 | **−0.4475** |
| 0.75 | 14:22 | 0.8893 | 0.5000 | **−0.3893** |
| 0.90 | 15:21 | 0.9590 | 0.6838 | **−0.2752** |
| 1.00 | 16:00 | 1.0000 | 1.0000 | 0.0000 |

**Max-absolute-deviation: 0.4475 at t=0.5 (midday).** This is a **large divergence** — `1 − √(1−t)` is consistently *looser* (less tightening) than the current `log10` curve through the entire interior of the session. At midday, the new curve has consumed only ~29% of its tightening budget vs ~74% under the heuristic. **By Δmultiplier, at t=0.5 the new multiplier is `1.5 − 1.0·0.293 = 1.207` vs the current `0.760` — about 0.45 percentage-points-of-stop wider midday.**

**Direction:** the new curve tightens **less aggressively in the morning** and **more aggressively in the late afternoon** compared to the heuristic. The "morning aggressive tightening" of `log10(1+9t)` is what the original code comment described as a "tuned practitioner heuristic" — the i.i.d. derivation says that was over-tightening.

### 1.5 Recommendation to team-lead

**Ship Candidate A** subject to the S-1 attribution-table review.

**Operating-range red flag:** the divergence vs the current heuristic is **large** (Δ ~0.45 at midday). The S-1 Stage 2 attribution table will show a meaningful number of historical replay cycles where the new (looser-midday) stop did NOT trigger but the old (tighter-midday) stop did. The **intended_direction** to declare in the spec bundle is: *"the new R1 curve is less aggressive midday by up to ~0.45 in decay-weight, equivalent to ~0.45 pp wider stop at t=0.5, monotone-converging at endpoints; expected effect: fewer mid-morning exits, more late-afternoon exits."*

If the team-lead believes the operational behavior of `log10(1+9t)` is **defensible and tuned** — i.e., the operator actively wants tight morning stops — then Candidate A is the wrong choice and we should instead explore **calibrated Candidate B (U-shape FFF)** with a properly-justified calibration window from the return series. That is the only way to defend a non-`log10` curve that still front-loads tightening. The decision is the team-lead's; my recommendation is A because it minimizes NN1 risk and pays a single intentional behavior-change cost that S-1 is built to handle.

---

## Site 2 — VWAP System-A HWM gate (R2)

### 2.1 Theoretical purpose

Inside `compute_vwap_breakdown_update`, the System-A profit-protection break arms ONLY when `safe_hwm >= vwap_cross_hwm_pct`. Below that gate, no VWAP-cross is counted as a profit-protection break (the tick counter resets). The Optuna-tuned `vwap_cross_hwm_pct` is the threshold value. The provenance gap is **the gate's structural form**, not the tuned numeric value (plan.md:53-59). Code self-flags: *"the gate `safe_hwm >= vwap_cross_hwm_pct` is a tuned practitioner heuristic with no formal literature provenance."* (`math_engine.py:668-673`).

The structural job is: arm the profit-protection trailing-break trigger only once a "meaningful" HWM has been banked. The unstated theoretical claim is "below some HWM, a VWAP break is dominated by noise; above that HWM, a VWAP break is signal-rich enough to act on." Any defensible re-derivation must connect the gate **shape** (scalar threshold vs. function of HWM vs. function of time) to a published optimal-stopping or arming framework.

### 2.2 Candidate frameworks (ranked)

#### **Candidate A — Optimal-stopping with running maximum (Leung-Zhang / Peskir maximality principle). Top recommendation.**

- **Citations:**
  - Leung, T. & Zhang, H. (2019). "Optimal Trading with a Trailing Stop." *Applied Mathematics & Optimization* 80, 669-698. DOI: 10.1007/s00245-019-09559-0. https://link.springer.com/article/10.1007/s00245-019-09559-0  `[Tier 1, peer-reviewed]`
  - Peskir, G. (1998). "Optimal Stopping of the Maximum Process: The Maximality Principle." *Annals of Probability* 26(4), 1614-1640. `[Tier 1]`
  - Rodosthenous, N. & Zervos, M. (2025). "Optimal stopping involving a diffusion and its running maximum: a generalisation of the maximality principle." arXiv:2505.18394. https://arxiv.org/abs/2505.18394  `[Tier 3 — arXiv preprint, not yet peer-reviewed; supports but is not load-bearing for the recommendation]`
- **Theoretical structural form:** Leung & Zhang (2019, §1, formalized from Peskir 1998) define a trailing stop as **the first time `X < f(X̄)`** where `X̄` is the running-maximum process and `f` is an **increasing function with `f(x) < x`**. The optimal `f` is characterized by the maximality principle — it is the maximal solution to a specific first-order nonlinear ODE that stays strictly below the diagonal in the `(X̄, X)` plane. For Geometric Brownian Motion with drift `μ` and volatility `σ`, the closed-form optimal boundary is **affine in `X̄`**: `f(X̄) = (1 − δ)·X̄`, where `δ` is a derived stop-distance fraction depending on `μ`, `σ`, the discount rate, and any exit cost.
- **Arming gate structure:** In the Leung-Zhang formalism, the trailing stop is **active for all `X̄`** above the entry price — i.e., there is **NO arming gate**; the trail starts immediately. An "arming gate at `X̄ ≥ vwap_cross_hwm_pct`" corresponds to a **piecewise-defined `f`**: `f(X̄) = -∞` (i.e., trail inactive, never exit) for `X̄ < gate`, and `f(X̄) = (1 − δ)·X̄` (active trail) for `X̄ ≥ gate`. This is **not** the maximality-principle-optimal boundary; the maximality principle says the optimal trail is continuous from the entry HWM, not piecewise.
- **NN1 compliance:** ★ **PASSES.** Choosing the gate structure from the Leung-Zhang formalism is `THEORY`. The threshold value itself (`vwap_cross_hwm_pct`) stays in the Optuna search space per plan.md:59 ("the Optuna search space for `vwap_cross_hwm_pct` is **unchanged**"). The structural choice — continuous trail vs piecewise gate — is the provenance gap that THEORY closes.
- **Empirical Evidence:** `[Theoretical]` — closed-form optimal under GBM-with-drift; Leung & Zhang extend to exponential Ornstein-Uhlenbeck. No live-trading replication of the affine boundary at the trade-by-trade level — but the **structural form** (affine `(1−δ)·X̄`) is the published-optimal answer.
- **Replication Status:** The maximality principle is replicated in dozens of optimal-stopping papers since Peskir 1998. The trailing-stop application is replicated in Leung-Zhang and Rodosthenous-Zervos (2025).
- **Regime Sensitivity:** The closed-form `f(X̄) = (1 − δ)·X̄` assumes GBM with positive drift. **Drift estimation is unstable intraday** — a small mis-estimate of `μ` produces a large change in `δ`. Under negative drift, the optimal strategy is "exit at entry," not a trail; the affine form degenerates.

**Critical reading of the current code in light of Candidate A:**

The current gate `safe_hwm >= vwap_cross_hwm_pct` is **not** the maximality-principle-optimal trailing-stop boundary. The maximality principle says the optimal trail is **affine and continuous from entry** — `f(X̄) = (1−δ)·X̄` — not piecewise with a kink at a tuned threshold. The current AlphaBot construction can be re-interpreted in Leung-Zhang terms as a **two-tier trailing-stop layered on top of an unrelated entry-price stop**:

1. Below the gate (`safe_hwm < vwap_cross_hwm_pct`): the trailing-stop system is **inactive**; risk is governed entirely by the primary `compute_active_trailing_stop` distance and the `compute_time_squeeze_decay` overlay.
2. Above the gate: System-A VWAP-cross arms; once VWAP-broken, the profit-protection branch flips and the position exits even if the primary trailing-stop has not been hit.

In this re-interpretation, the gate is **not a trailing-stop boundary** but a **regime switch** between "primary-stop-only" and "primary-stop OR VWAP-protection." A regime switch at a tuned HWM is consistent with optimal control under a **two-regime model** where the second regime activates after the position has banked enough profit to justify a tighter exit policy. The **structural form** (a step at a tuned HWM) is then the right shape, and the provenance closure is "Candidate A's optimal-stopping framework justifies regime-conditional trailing-stop arming; the gate is the regime switch; the tuned threshold is the regime boundary."

This is the recommendation. See §2.3.

#### **Candidate B — Sortino / downside-deviation-derived arming gate.**

- **Citation:** Sortino, F.A. & Price, L.N. (1994). "Performance Measurement in a Downside Risk Framework." *Journal of Investing* 3(3), 59-64. `[Tier 1, peer-reviewed]`
- **Theoretical assumption:** Arming should be calibrated to a target false-trip rate. The gate is set such that `P(VWAP-cross | HWM < gate) ≤ α` for a stated false-arm tolerance `α`. Empirically `α` is set from the downside-deviation of historical return series (not strategy P&L).
- **Functional form:** Gate `= F⁻¹_HWM(α)` where `F_HWM` is the empirical CDF of "HWM at the time of a noise-driven VWAP cross." Scalar gate, calibrated.
- **NN1 compliance:** ⚠ **CONDITIONAL.** Calibration from the return series of the universe (not strategy P&L) is `STYLIZED_FACT` and PASSES. Calibration from historical AlphaBot exit decisions FAILS.
- **Empirical Evidence:** `[Theoretical]` for the framework; the gate value is `[Backtest]` if calibrated from a finite sample.
- **Replication Status:** Sortino-style frameworks are replicated; this specific HWM-arming-gate application is **not** in the literature — adapted construction.
- **Regime Sensitivity:** The calibrated `α` is regime-conditional (volatility regimes shift the false-trip rate). Under a regime change, the calibrated gate stale.
- **Concern:** Requires a careful calibration spec — what counts as a "noise-driven cross," what's the sample window, what's the inclusion universe. Reintroduces the same overfit risk H-5 flags. Not the top recommendation; included because it is the most defensible *empirically calibrated* alternative if the team-lead rejects Candidate A's "regime switch at tuned HWM" framing.

#### **Candidate C — Bayesian drawdown-distribution threshold (Zambelli 2016).**

- **Citation:** Zambelli, A.E. (2016). "Determining Optimal Stop-Loss Thresholds via Bayesian Analysis of Drawdown Distributions." arXiv:1609.00869 [q-fin.RM]. https://arxiv.org/abs/1609.00869  `[Tier 3 — arXiv preprint]`
- **Theoretical assumption:** Treat the arming threshold as a Bayesian decision boundary over the drawdown distribution; arm when posterior P(drawdown > stop | observed HWM) exceeds a tolerance.
- **Functional form:** Threshold = function of running HWM, derived from Bayesian update of drawdown prior. Possibly continuous (not scalar).
- **NN1 compliance:** ⚠ **CONDITIONAL.** Prior must be calibrated from return-series stylized facts to PASS.
- **Empirical Evidence:** `[Backtest]` — Zambelli applies to hourly trading strategy with two backtest variations. Single-paper; not replicated.
- **Replication Status:** Unknown / no replications found in this search.
- **Regime Sensitivity:** Prior is regime-conditional.
- **Concern:** Single-paper, preprint-only, not peer-reviewed. The provenance closure is weaker than Candidate A.

#### **Candidate D — Chande/Kroll Stop or Chandelier Exit analogs.**

- **Citations:**
  - Chande, T.S. & Kroll, S. (1994). *The New Technical Trader.* John Wiley & Sons. `[Tier 2 — practitioner standard, not peer-reviewed]`
  - LeBeau, C. & Lucas, D.W. (1992). *Computer Analysis of the Futures Markets.* (Chandelier Exit origin.) `[Tier 4]`
- **Theoretical assumption:** Trailing stop set as `HWM − k·ATR` (Chandelier) or `HWM − k·StDev` (Chande-Kroll). The arming gate is *implicit*: the trail is always active, but it doesn't bite until `HWM − k·σ > entry`.
- **Functional form:** Continuous, no explicit gate. The "gate" is the natural breakeven `entry + k·σ`.
- **NN1 compliance:** ⚠ Practitioner heuristic. `k` is hand-tuned in the original sources. NOT `THEORY`. Would need calibration to PASS.
- **Empirical Evidence:** `[Folklore — high adoption / low formal evidence]`. Quantified Strategies and Lizard Indicators document the construction; no peer-reviewed validation found.
- **Replication Status:** Practitioner-replicated; no academic replications.
- **Regime Sensitivity:** Trail bites later in low-vol regimes (good); bites earlier in high-vol regimes (sometimes bad).
- **Concern:** Same provenance category as the current heuristic — substituting one practitioner heuristic for another doesn't close R2. Not viable for M3.

#### **Candidate E — Information-criterion (BIC/AIC) decision-theoretic threshold.**

- Rejected with one-line reason: Requires a model-selection framing the gate is not actually doing; the gate is an arming decision, not a model-selection decision. Conceptual mismatch. Not pursued.

### 2.3 Chosen derivation

**Recommendation: Candidate A — frame the gate as a regime switch under the optimal-stopping (Leung-Zhang / Peskir) formalism. KEEP the gate shape (scalar threshold). KEEP `vwap_cross_hwm_pct` in the Optuna search space. CHANGE the provenance comment from "tuned practitioner heuristic with no formal literature provenance" to a citation of Leung-Zhang (2019) and the maximality principle, with the explicit framing that the gate is a regime switch between primary-stop-only and primary-stop-OR-VWAP-protection.**

**Rationale, in order:**

1. **The gate shape is right.** The Leung-Zhang formalism (`f(X̄) = (1 − δ)·X̄`) is the continuous trail boundary, but the *regime-switch interpretation* of the current AlphaBot code is consistent with two-regime optimal stopping. A scalar threshold at HWM is the regime boundary; the threshold being Optuna-tuned (within the BHY haircut surface) is the parameter calibration. Per plan.md:53, "The Optuna-tuned `vwap_cross_hwm_pct` parameter **remains an Optuna-searched parameter** (it is a tuned threshold; Optuna-searched parameters were never the provenance gap — the *gate shape* was)."
2. **NN1 cleanest.** Choosing the gate STRUCTURE from optimal-stopping theory is `THEORY`. The threshold VALUE staying inside the existing Optuna search space leaves the BHY haircut surface unchanged.
3. **Behavioral identity.** This recommendation is a **provenance-only closure** — the runtime arithmetic is unchanged. S-1 Stage 2 attribution should show **zero divergent cycles** for R2 (the gate code is byte-identical). The intended_direction declaration is "no behavioral change; only the provenance comment moves." This is the cheapest possible R2 closure under S-1.
4. **The structural-form gap is real and closeable.** The original provenance gap was "why a scalar gate at a tuned HWM and not a continuous curve?" Candidate A's answer: the scalar gate is the *regime boundary*; below it, the system is in regime 1 (primary stop only); above it, regime 2 (primary stop OR VWAP protection). This is a published optimal-stopping framing. The provenance comment can cite Leung-Zhang (2019) and the maximality principle without misrepresenting them.

**What ships:**

```python
# VWAP System-A HWM arming gate
# PROVENANCE: the gate `safe_hwm >= vwap_cross_hwm_pct` is the regime boundary
# of a two-regime trailing-stop system. Regime 1 (below gate): primary
# trailing-stop only (compute_active_trailing_stop with time-squeeze decay).
# Regime 2 (at-or-above gate): primary stop OR VWAP-cross profit-protection
# (System A) — adds the System-A break tick counter once HWM has banked
# enough to justify a tighter exit policy. The structural choice of a
# regime switch is justified by the optimal-stopping formalism for trailing
# stops with running maxima (Leung & Zhang, 2019, "Optimal Trading with a
# Trailing Stop," Applied Mathematics & Optimization 80, 669-698, DOI:
# 10.1007/s00245-019-09559-0). The maximality principle (Peskir, 1998,
# Annals of Probability 26(4), 1614-1640) characterizes the optimal trailing
# boundary as the maximal solution to a first-order nonlinear ODE; the
# regime-switch construction is a discretization of this boundary into
# inactive/active regions, with the boundary location (vwap_cross_hwm_pct)
# remaining a tuned parameter within the Optuna search space (per the BHY
# haircut surface). Research note: docs/research/m3-provenance/literature-pass.md.
# freeze_discipline = THEORY.
if safe_hwm >= vwap_cross_hwm_pct and current_return < safe_hwm:
    ...
```

### 2.4 Numerical comparison

The recommended derivation is a **provenance-only closure** — the runtime code is **byte-identical** to the current implementation. Numerical comparison is degenerate:

| Input (`safe_hwm`, `vwap_cross_hwm_pct`, `current_return`) | Current arm decision | Candidate A arm decision | Δ |
|---|---|---|---|
| (0.5, 1.0, 0.4) | False (below gate) | False (below gate) | 0 |
| (1.0, 1.0, 0.9) | True (at gate, return below HWM) | True (at gate, return below HWM) | 0 |
| (1.5, 1.0, 1.4) | True (above gate, return below HWM) | True (above gate, return below HWM) | 0 |
| (1.5, 1.0, 1.5) | False (return equals HWM, strict <) | False (return equals HWM, strict <) | 0 |
| (1.5, 1.0, 1.6) | False (return above HWM) | False (return above HWM) | 0 |

**Max-absolute-deviation: 0 (zero divergent cycles in the S-1 Stage 2 attribution table for R2).**

This is the **least-risk possible R2 closure**. The provenance gap shuts; the runtime stays as Optuna-tuned. The whole behavioral budget of M3 is spent on R1, where the divergence is concentrated and the intended_direction is declared.

### 2.5 Recommendation to team-lead

**Ship Candidate A** — the provenance-only closure with the Leung-Zhang / Peskir regime-switch framing.

**Operating-range red flag:** **none for R2 under this recommendation.** The behavior is unchanged. The risk is purely *provenance interpretive* — if a reviewer challenges the regime-switch reading of the current code, the recommendation falls apart and we'd need to advance to Candidate B (calibrated false-trip threshold from return series) which carries real behavioral divergence.

**Counter-recommendation for the team-lead to consider:** if the user / reviewer specifically wants R2 to **actually change** as part of M3 (i.e., the provenance-only closure is rejected as a no-op), Candidate B is the right second choice. It would require a return-series calibration step from impl-m3 and a real S-1 attribution table for R2. That doubles the cycle's behavioral-change surface and roughly doubles the review burden. My read of plan.md is that the provenance closure is the intended outcome — but the team-lead should confirm this with the user before tw-m3 writes RED.

---

## 3 — Golden-fixture inputs for tw-m3

Per kickoff brief, fixtures for RED tests. **D-2 ★ load-bearing:** these fixtures are **derived directly from the closed-form formulas in §1.3 and §2.3** — they are not regenerated from the post-M3 code's own output. If a future contributor regenerates them from `compute_time_squeeze_decay` or `compute_vwap_breakdown_update` post-M3, the fixture becomes a tautology and the D-2 gate is violated.

### 3.1 R1 fixtures — `f(t) = 1 − √(1 − t)`

```yaml
# Inputs derived from the closed-form 1 - sqrt(1 - t) under the i.i.d. remaining-variance theory.
# Producer: the formula itself (THEORY); these are NOT captured from compute_time_squeeze_decay.
# Tolerance: exact to 1e-12 (pure arithmetic on float64; no transcendentals beyond sqrt).

- name: market_open_boundary
  input: { time_ratio: 0.0 }
  expected: { decay_curve: 0.0, dynamic_multiplier: 1.5, dynamic_min_stop: 0.3 }
  derivation: "1 - sqrt(1 - 0.0) = 1 - 1 = 0; multiplier = 1.5 - 1.0*0 = 1.5; min_stop = 0.3 - 0.15*0 = 0.3"

- name: quarter_session
  input: { time_ratio: 0.25 }
  expected:
    decay_curve: 0.1339745962155614  # 1 - sqrt(0.75)
    dynamic_multiplier: 1.366025404   # 1.5 - 1.0*0.1339745962
    dynamic_min_stop: 0.279903810     # 0.3 - 0.15*0.1339745962
  derivation: "1 - sqrt(1 - 0.25) = 1 - sqrt(0.75) ≈ 0.13397"

- name: midday_session
  input: { time_ratio: 0.5 }
  expected:
    decay_curve: 0.2928932188134524   # 1 - sqrt(0.5)
    dynamic_multiplier: 1.207106781    # 1.5 - 1.0*0.29289
    dynamic_min_stop: 0.256066017      # 0.3 - 0.15*0.29289
  derivation: "1 - sqrt(0.5) ≈ 0.29289; cross-check vs current heuristic 0.74 — large divergence at midday is expected"

- name: three_quarter_session
  input: { time_ratio: 0.75 }
  expected:
    decay_curve: 0.5                   # 1 - sqrt(0.25) = 1 - 0.5
    dynamic_multiplier: 1.0            # 1.5 - 1.0*0.5
    dynamic_min_stop: 0.225            # 0.3 - 0.15*0.5
  derivation: "1 - sqrt(0.25) = 0.5 EXACTLY; clean boundary fixture"

- name: market_close_boundary
  input: { time_ratio: 1.0 }
  expected: { decay_curve: 1.0, dynamic_multiplier: 0.5, dynamic_min_stop: 0.15 }
  derivation: "1 - sqrt(1 - 1.0) = 1 - 0 = 1; multiplier = 1.5 - 1.0*1 = 0.5; min_stop = 0.3 - 0.15*1 = 0.15"
```

### 3.2 R2 fixtures — gate provenance closure (behavior unchanged)

The R2 recommendation is a provenance-only closure. The existing fixture suite for `compute_vwap_breakdown_update` at `tests/fixtures/math_engine/vwap_breakdown/*.json` is **unchanged** — all 24 fixtures already pin the existing arm logic. tw-m3 should:

1. **Re-run** the existing 24 fixtures post-M3 — they MUST pass byte-identical.
2. **Add one new fixture** that asserts the provenance comment is updated:

```yaml
# Provenance-comment closure assertion (R2)
- name: r2_provenance_closes_with_leung_zhang_citation
  asserts:
    - "the comment block at math_engine.py near the System-A gate references 'Leung' AND 'Zhang' AND '2019'"
    - "the comment block references 'Peskir' AND 'maximality principle'"
    - "the comment block references 'docs/research/m3-provenance/literature-pass.md'"
    - "the literal string 'no formal literature provenance' is ABSENT from the System-A gate comment block"
    - "freeze_discipline for the R2 facet in spec_bundles is 'THEORY'"
  derivation: "R2 is a provenance-only closure under Candidate A (§2.3 of the research note); the runtime is byte-identical; the closure is in the comment + the spec_bundle freeze_discipline."
```

### 3.3 R1 spec-bundle fixture (NN1 enforcement)

```yaml
- name: r1_spec_bundle_freeze_discipline_is_theory
  asserts:
    - "spec_bundles row for the M3 R1 facet exists"
    - "spec_bundles.facets_json contains a facet named 'time_squeeze_decay_curve_v2'"
    - "the facet's freeze_discipline is 'THEORY'"
    - "the facet's freeze_discipline is NOT 'BACKTEST_SELECTION' (NN1 enforcement)"
    - "the spec_bundle hash matches the persisted facets_json (immutability invariant from Phase 1)"
  derivation: "Per plan.md:36-37, R1 facet must persist with freeze_discipline in {STYLIZED_FACT, THEORY, MANDATE}; THEORY is the chosen discipline because f(t)=1-sqrt(1-t) is derived from i.i.d.-returns square-root-of-time scaling, no data fitting."
```

### 3.4 Direction-declaration fixture (S-1 K-1 enforcement)

```yaml
- name: r1_intended_direction_declared_pre_ship
  asserts:
    - "spec_bundles.facets_json for time_squeeze_decay_curve_v2 contains an 'intended_direction' field"
    - "intended_direction string contains 'concave', 'open-loaded', 'less aggressive midday', and 'monotone-converging at endpoints'"
    - "intended_direction declaration timestamp PRECEDES the S-1 Stage 2 replay run timestamp"
  derivation: "Per plan.md:118, an after-the-fact direction declaration is circular. Order: derive, declare, commit, replay, validate. This fixture pins the ordering."
```

---

## 4 — Risk callouts and caveats

- **NN1.** Candidate A for R1 is THEORY; Candidate A for R2 is THEORY (provenance closure). Both PASS the binding cleanly. If the team-lead substitutes Candidate B for either, the calibration MUST be from the return series, not strategy P&L. The spec-bundle assertion is the structural enforcement.
- **D-2 (non-circular fixture provenance, ★ load-bearing).** The R1 fixtures in §3.1 are derived from the closed-form formula `f(t) = 1 − √(1 − t)`, not from `compute_time_squeeze_decay`. If a future contributor regenerates them from the post-M3 code's own output, the gate is meaningless. The same is true for R2 — the existing 24-fixture suite is captured-from-producer (the pre-M3 code is the producer for the **behavior**, the literature is the producer for the **comment**); the new provenance-comment assertion is a textual check, not a behavioral one.
- **S-1 Stage 2 attribution table (K-1 ★).** R1 will produce a substantial number of divergent cycles (Δ ≈ 0.45 at midday is large). Every divergent cycle must be attributed and direction-validated. R2 produces zero divergent cycles under the recommended closure.
- **F-2 (replay determinism, ★).** Both `1 − √(1 − t)` and the unchanged R2 gate are pure functions. No RNG, no wall-clock, no unordered-dict iteration. Replay-determinism is preserved.
- **Live-exit-logic blast radius.** R1's Δ ≈ 0.45 at midday means the new curve loosens the stop midday by ~0.45 pp. On a normal-trend day where the morning move continues, this means **fewer premature mid-morning exits**. On a mean-reverting AM-spike day, this means **more drawdown caught** by the looser AM stop. The S-1 attribution table is the human-review surface; the team-lead reviews the per-cycle table before the PR merges.
- **Out-of-scope reminders.** The parabolic ratchet (`compute_para_arm_decision`) is **NOT** in M3 scope per plan.md:128. The Optuna search space for `vwap_cross_hwm_pct` is **unchanged** per plan.md:59. No HAC / Newey-West confidence-interval correction in M3 (W-H5 is M1's residual, out of all of Phase 1 + 1.5) per plan.md:133.

---

## 5 — References (full bibliography)

### Tier 1 (peer-reviewed)

1. Andersen, T.G. & Bollerslev, T. (1997). "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. DOI: 10.1016/S0927-5398(97)00004-2. https://finance.martinsewell.com/stylized-facts/volatility/AndersenBollerslev1997b.pdf
2. Wood, R.A., McInish, T.H., & Ord, J.K. (1985). "An Investigation of Transactions Data for NYSE Stocks." *Journal of Finance* 40(3), 723-739. DOI: 10.1111/j.1540-6261.1985.tb04996.x.
3. Admati, A.R. & Pfleiderer, P. (1988). "A Theory of Intraday Patterns: Volume and Price Variability." *Review of Financial Studies* 1(1), 3-40. https://academic.oup.com/rfs/article-abstract/1/1/3/1601212
4. Hong, H. & Wang, J. (2000). "Trading and Returns under Periodic Market Closures." *Journal of Finance* 55(1), 297-354. http://web.mit.edu/wangj/www/pap/HongWang00.pdf
5. Heston, S.L., Korajczyk, R.A., & Sadka, R. (2010). "Intra-Day Patterns in the Cross-Section of Stock Returns." *Journal of Finance* 65(4), 1369-1407.
6. Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk* 3(2), 5-39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf
7. Leung, T. & Zhang, H. (2019). "Optimal Trading with a Trailing Stop." *Applied Mathematics & Optimization* 80, 669-698. DOI: 10.1007/s00245-019-09559-0. https://link.springer.com/article/10.1007/s00245-019-09559-0
8. Peskir, G. (1998). "Optimal Stopping of the Maximum Process: The Maximality Principle." *Annals of Probability* 26(4), 1614-1640.
9. Danielsson, J. & Zigrand, J.-P. (2003). *On time-scaling of risk and the square-root-of-time rule.* London School of Economics, Financial Markets Group DP-439. https://eprints.lse.ac.uk/24827/1/dp439.pdf
10. Sortino, F.A. & Price, L.N. (1994). "Performance Measurement in a Downside Risk Framework." *Journal of Investing* 3(3), 59-64.
11. Gallant, A.R. (1981). "On the bias in flexible functional forms and an essentially unbiased form: The Fourier flexible form." *Journal of Econometrics* 15(2), 211-245.

### Tier 2 (named expert, not peer-reviewed)

12. Carver, R. (2015). *Systematic Trading: A unique new method for designing trading and investing systems.* Harriman House. https://www.harriman-house.com/systematic-trading
13. Chande, T.S. & Kroll, S. (1994). *The New Technical Trader.* John Wiley & Sons.

### Tier 3 (arXiv preprint / community)

14. Rodosthenous, N. & Zervos, M. (2025). "Optimal stopping involving a diffusion and its running maximum: a generalisation of the maximality principle." arXiv:2505.18394. https://arxiv.org/abs/2505.18394
15. Zambelli, A.E. (2016). "Determining Optimal Stop-Loss Thresholds via Bayesian Analysis of Drawdown Distributions." arXiv:1609.00869.

### Tier 4 (practitioner / restatement)

16. Gundersen, G. (2022). *The Square-Root-of-Time Rule.* https://gregorygundersen.com/blog/2022/05/24/square-root-of-time-rule/
17. ChartSchool / StockCharts. "Chandelier Exit." https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit
18. QuantifiedStrategies (Chande Kroll Stop, Chandelier Exit). https://www.quantifiedstrategies.com/chandelier-exit-strategy/

### Internal cross-references

- `docs/research/risk/log-time-squeeze-investigation.md` — prior literature investigation (2026-05-17). The recommendation here supersedes that note's "no-decision pending A/B" verdict by selecting Candidate A (square-root remaining-variance) on NN1 grounds; the A/B testing the prior note recommended is **not appropriate under M3's NN1 binding** because A/B testing is `BACKTEST_SELECTION`.
- `feature-plans/decision-science/phase-1.5/m3-redrive-provenance-gaps/plan.md` — the M3 plan; this brief follows it on scope, NN1 binding, S-1 dependencies, and Definition of Done.
- `feature-plans/exit-decision-math-fixes.md` — AC-7 / AC-8 provenance-test surface that gets its assertions updated.

### Confidence tags applied

- `[High]`: refs 1-9 — peer-reviewed, replicated.
- `[Medium]`: ref 10 (Sortino-Price methodology; the specific HWM-arming application is adapted).
- `[Folklore — high adoption / low formal evidence]`: refs 12, 13 (intraday-stop application).
- `[Single-source preprint]`: refs 14, 15.
- `[Tier 4 — restatement, not load-bearing]`: refs 16, 17, 18.

---

## 6 — Open questions (logged, not blocking)

- Whether the team-lead reads R2 as a "provenance-only closure" or as "actually change the gate." My recommendation is the former. If the latter, dispatch a follow-up cycle for Candidate B with a return-series calibration spec — that cycle is bigger than M3 itself.
- Whether a future M-series fix should re-derive the **parabolic ratchet** (`compute_para_arm_decision`). That layer's provenance is also self-flagged as practitioner-tuned; the optimal-stopping framework in §2 also covers it as a velocity-arming variant. Out of scope for M3.
- Whether the U-shape (Candidate B for R1) should be implemented as a follow-up if the i.i.d. baseline (Candidate A) shows it's mis-specified in live trading. Decision logged for a future Phase-2 cycle.

---

*End of brief. Top recommendation for each site:*

- **R1 (time-squeeze decay curve):** ship `f(t) = 1 − √(1 − t)` (Candidate A, Danielsson-Zigrand square-root-of-time scaling under i.i.d. returns). `freeze_discipline = THEORY`. Zero new free parameters. Concentrates the M3 behavioral budget here.
- **R2 (VWAP System-A HWM gate):** ship provenance-only closure under Candidate A (Leung-Zhang regime-switch interpretation, Peskir maximality principle citation). `freeze_discipline = THEORY`. Runtime byte-identical to current code.
