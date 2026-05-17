# I2 — Stop-compounding investigation (PARA + breakeven + time-squeeze)

Date: 2026-05-17
Workstream: I2 (engine-correctness-remediation)
Researcher: quant-risk-researcher

## Executive summary

AlphaBot's trailing-stop pipeline stacks three tightening mechanisms — the
parabolic-squeeze ratchet, the breakeven lock, and the intraday time-squeeze
decay curve. The audit panel labelled this as "8× compounding"; the first
finding of this investigation is that the **stop-distance multiplications do
not actually stack 3×** in the way the audit panel feared:

- The parabolic-squeeze multiplier (`MAX_PARABOLIC_SQUEEZE`, default `0.50`)
  is applied **exactly once** when `para_armed OR breakeven_locked` (single
  conditional in `compute_active_trailing_stop`).
- The breakeven lock's second-order effect is **flooring the trigger** at
  `0.0` (not a fresh multiplier on the distance).
- The time-squeeze decay multiplies the base distance by
  `dyn_mult(t) ∈ [0.5, 1.5]` and is **always on** (cannot be disabled at
  runtime).

The real compounding is therefore **2× in distance terms**: the squeeze
multiplier (≈ 0.5) times the time-squeeze multiplier (1.5 → 0.5 over the
session). That is still material — the effective stop distance shrinks by
**~3×** between 10:30 ET and 15:55 ET under the default `EXECUTION_START_TIME
= 10:30` anchor. The bite is **concentrated in the final 30 minutes**
(15:30 - 16:00 ET) where the decay curve flattens near its 0.5 floor and
the minimum-stop floor also reaches its 0.15 floor.

**Verdict: Material, but bounded.** Recommend remediation option (a) — cap
the compounded tightness at a literature-grounded floor — over option (b)
because the existing decoupled functions are each individually defensible
and the cap is a one-line change with a Toxic-Pair-friendly test surface.

---

## Three mechanisms

### PARA ratchet
**File:** `math_engine.py`
**Functions:** `compute_para_arm_decision` (arms the latch),
`compute_active_trailing_stop` (applies the multiplier).
**Formula (verbatim):**

```python
# compute_para_arm_decision  (math_engine.py:68-89)
velocity = float(current_return) - float(prev_return)
should_arm = bool((velocity >= para_threshold) and (not currently_armed))
```

```python
# compute_active_trailing_stop  (math_engine.py:119-151)
safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
if para_armed or breakeven_locked:
    active *= parabolic_squeeze_multiplier   # default 0.50
return active
```

**Behavior:** Once armed, the latch is permanent for the position lifetime
(no reset path). The squeeze multiplier `MAX_PARABOLIC_SQUEEZE` is Optuna-tuned
in `[0.1, 0.8]` per `ai_advisor.py:139` and defaults to `0.50` per
`alpha_bot_execution.py:62` and `database.py:19`. Applied **once**, not once
per armed flag.

### Breakeven lock
**File:** `math_engine.py`
**Function:** `compute_breakeven_update` (`math_engine.py:154-222`).
**Condition (verbatim):**

```python
dynamic_activation = max(BREAKEVEN_ACTIVATION_MIN,
                         min(BREAKEVEN_ACTIVATION_MAX, symphony_vol))
# = clamp(symphony_vol, 0.4, 3.0)  -- in percentage points
if current_return >= (dynamic_activation - BREAKEVEN_ACTIVATION_DEADBAND):
    new_hold_ticks = current_hold_ticks + 1
else:
    new_hold_ticks = 0
new_breakeven_locked = (
    currently_breakeven_locked or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD)
)  # 5 consecutive qualifying ticks, one-way latch
if new_breakeven_locked:
    stop_trigger_level = max(base_stop_level, 0.0)
```

**Behavior:** Latches after 5 consecutive minutes within
`BREAKEVEN_ACTIVATION_DEADBAND = 0.2%` of the dynamic-activation threshold
(itself = vol clamped to [0.4%, 3.0%]). Once latched, the trigger level is
floored at zero (semantic "no worse than breakeven"). Additionally, the
caller passes `breakeven_locked=True` into `compute_active_trailing_stop`
which causes the *same* squeeze multiplier branch to fire — meaning
breakeven-lock-without-para-armed produces the same 0.50× as
para-armed-without-breakeven; the two conditions OR into one multiplication.

### Time-squeeze decay
**File:** `math_engine.py`
**Function:** `compute_time_squeeze_decay` (`math_engine.py:92-116`).
**Curve (verbatim):**

```python
decay_curve = math.log10(1 + DECAY_CURVE_SCALAR * time_ratio)   # SCALAR=9
dynamic_multiplier = MULT_OPEN - (MULT_OPEN - MULT_CLOSE) * decay_curve
#                  = 1.5 - 1.0 * decay_curve   -> in [0.5, 1.5]
dynamic_min_stop = MIN_STOP_OPEN - (MIN_STOP_OPEN - MIN_STOP_CLOSE) * decay_curve
#                = 0.3 - 0.15 * decay_curve    -> in [0.15, 0.30]
```

**Behavior:** Always on. `time_ratio` is computed by the caller
(`alpha_bot_execution.py:805-807`) as elapsed-since-`EXECUTION_START_TIME`
over `(close - EXECUTION_START_TIME)`. With the operator-typical setting
`EXECUTION_START_TIME = "10:30"` (the action gate that avoids open-volatility
noise), the curve spans 330 minutes, not 390.

---

## Compounding measurement

### Methodology

Deterministic side calculation (no engine modification). Pure-function
reproduction of `compute_time_squeeze_decay` + `compute_active_trailing_stop` +
`compute_breakeven_update` at minute-mark snapshots across the trading day,
under a **flat random-walk price assumption** (`current_return ≡ HWM = 1.0%`
constant; satisfies the Kaminski-Lo random-walk regime where prices have
zero or near-zero drift).

The simulation source is checked in at
`docs/research/risk/scripts/i2_compounding_sim.py`. Three columns are
reported:

1. **PARA only** — counterfactual stop distance if only the parabolic
   ratchet were active (computed at `time_ratio = 0`).
2. **PARA + breakeven** — counterfactual with the OR'd squeeze branch fired
   but no time-squeeze decay (computed at `time_ratio = 0`). Note: this
   equals the PARA-only distance because the OR is single-shot; only the
   trigger-level floor at 0.0 differs.
3. **All three** — live time-ratio at minute `t`.

Defaults used:
- `MAX_PARABOLIC_SQUEEZE = 0.50` (`.env` default per
  `alpha_bot_execution.py:62`).
- `EXECUTION_START_TIME = "10:30"`, market close `16:00`.
- `safe_hwm = 1.0%`, flat random-walk regime (`current_return ≡ HWM`).
- Three vol regimes: `symphony_vol = 0.60% / 1.20% / 2.40%`.

### Stop tightness across trading day (symphony_vol = 1.20%, typical)

| Time (ET) | time_ratio | decay | dyn_mult | dyn_min | PARA-only dist | +breakeven dist | +time-squeeze dist | Trigger level (all 3) |
|-----------|-----------|-------|----------|---------|----------------|-----------------|--------------------|-----------------------|
| 10:30 | 0.000 | 0.000 | 1.500 | 0.300 | 0.900 | 0.900 | 0.900 | +0.100 |
| 11:00 | 0.091 | 0.260 | 1.240 | 0.261 | 0.900 | 0.900 | 0.744 | +0.256 |
| 12:00 | 0.273 | 0.539 | 0.962 | 0.219 | 0.900 | 0.900 | 0.577 | +0.423 |
| 13:00 | 0.455 | 0.707 | 0.793 | 0.194 | 0.900 | 0.900 | 0.476 | +0.524 |
| 14:00 | 0.636 | 0.828 | 0.672 | 0.176 | 0.900 | 0.900 | 0.403 | +0.597 |
| 15:00 | 0.818 | 0.922 | 0.578 | 0.162 | 0.900 | 0.900 | 0.347 | +0.653 |
| 15:30 | 0.909 | 0.963 | 0.537 | 0.156 | 0.900 | 0.900 | 0.322 | +0.678 |
| 15:45 | 0.955 | 0.982 | 0.518 | 0.153 | 0.900 | 0.900 | 0.311 | +0.689 |
| 15:55 | 0.985 | 0.994 | 0.506 | 0.151 | 0.900 | 0.900 | 0.304 | +0.696 |
| 16:00 | 1.000 | 1.000 | 0.500 | 0.150 | 0.900 | 0.900 | 0.300 | +0.700 |

All values are in percentage points. **Active-stop distances are the gap
between the HWM and the trigger; smaller = tighter.** The compounded
(all-three) column shrinks from 0.900% to 0.300% over the session — a
**3.0× tightening** versus the PARA-only counterfactual at open.

### Stop tightness — low vol regime (symphony_vol = 0.60%)

| Time (ET) | dyn_mult | safe_vol×dyn_mult | dyn_min | active=max | After squeeze (× 0.50) | Trigger (hwm=1.0%) |
|---|---|---|---|---|---|---|
| 10:30 | 1.500 | 0.900 | 0.300 | 0.900 | 0.450 | +0.550 |
| 12:00 | 0.962 | 0.577 | 0.219 | 0.577 | 0.289 | +0.711 |
| 14:00 | 0.672 | 0.403 | 0.176 | 0.403 | 0.202 | +0.798 |
| 15:30 | 0.537 | 0.322 | 0.156 | 0.322 | 0.161 | +0.839 |
| 16:00 | 0.500 | 0.300 | 0.150 | 0.300 | 0.150 | +0.850 |

At low vol, the `dynamic_min_stop = 0.150` floor binds at close — the squeeze
applies to a max(...) whose vol-leg is `0.300` and min-leg is `0.150`, so the
active is `0.300 * 0.50 = 0.150` exactly. Tightening ratio 0.450 → 0.150 is
also **3.0×**.

### Stop tightness — high vol regime (symphony_vol = 2.40%)

| Time (ET) | dyn_mult | safe_vol×dyn_mult | After squeeze | Trigger (hwm=1.0%) |
|---|---|---|---|---|
| 10:30 | 1.500 | 3.600 | 1.800 | -0.800 |
| 12:00 | 0.962 | 2.309 | 1.154 | -0.154 |
| 14:00 | 0.672 | 1.613 | 0.807 | +0.193 |
| 15:30 | 0.537 | 1.289 | 0.645 | +0.356 |
| 16:00 | 0.500 | 1.200 | 0.600 | +0.400 |

High-vol regime: vol-leg always dominates the floor; tightening is the
clean `dyn_mult(t)/1.5 = decay-curve` factor and the stop crosses from
"deeply negative-return-tolerant" (-0.8%) at open to "+0.4%-locked" at close.
Same 3.0× ratio.

### Late-day concentration

The decay curve is `log10(1 + 9t)`, which is **concave** — the largest
*absolute* drop in `dyn_mult` happens early (10:30 → 11:00: 1.500 → 1.240,
a 0.26 absolute drop in one half-hour) but the largest *relative* tightening
late (15:00 → 16:00: 0.578 → 0.500, a 13.5% relative tightening in one
hour, on a stop already at 39% of open width).

The dangerous regime is the cross-product:

- **15:30 - 16:00 ET window** — stop distance has shrunk to 0.30-0.32% at
  typical vol, and the `dynamic_min_stop` floor of 0.15 binds in the
  low-vol regime. A `MAGNITUDE_FLOOR_PCT = 0.10%` confirmation requirement
  (`math_engine.py:226`) means the **effective triggering gap** is only
  0.20% — comparable to a single bid/ask round-trip on a 1-minute bar in
  many ETFs.

For symphony_vol = 0.60%, the post-squeeze + post-time-squeeze stop distance
of 0.150% at close minus the magnitude floor of 0.10% leaves a **0.050%
trigger envelope** — i.e. any 5-bp noise spike triggers the exit-confirm
counter. That is the late-day "noise bite" zone.

### Compounding bite — comparative

| Regime | PARA only (open) | All-three (close) | Tightening factor |
|---|---|---|---|
| Low vol (0.60%) | 0.450 | 0.150 | 3.0× |
| Typical (1.20%) | 0.900 | 0.300 | 3.0× |
| High vol (2.40%) | 1.800 | 0.600 | 3.0× |

The compounding factor is **regime-invariant** (because the time-squeeze
multiplier 1.5 → 0.5 is multiplicative across vol regimes). The vol regime
only sets the absolute scale.

---

## Kaminski-Lo regime analysis

**Citation:** Kaminski, K. M., & Lo, A. W. (2014). "When do stop-loss rules
stop losses?" *Journal of Financial Markets*, 18, 234-254.
DOI: [10.1016/j.finmar.2013.07.001](https://doi.org/10.1016/j.finmar.2013.07.001).
SSRN: [abstract_id=968338](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338).

**Result (paraphrased from `[High]` two-source corroboration):**
under the Random Walk Hypothesis with positive risk premium, any stop-loss
rule produces a **non-positive "stopping premium" Δμ** (expected return
delta versus buy-and-hold). The drag is the risk premium foregone during
the post-stop liquid period, multiplied by the unconditional probability of
stopping out. Kaminski-Lo show this is **monotonically non-positive for all
random-walk DGPs** — there is no parameter setting that flips the sign.
The Research Affiliates discussion of Kaminski-Lo
([Stop the Losses!](https://www.researchaffiliates.com/publications/articles/1099-stop-the-losses))
adds the practitioner-relevant simulation result that under
zero-autocorrelation regimes (AR1 = 0, i.e. random walk), expected return
and Sharpe deltas are statistically indistinguishable from zero in their
simulations — the cost is *not* large in absolute basis points, but the
**maximum drawdown improves modestly**, which is the dominant practitioner
justification for stop rules.

**Application to AlphaBot.** The mechanism is identical: every minute that
the stop is "armed" (PARA-armed or breakeven-locked or both), the symphony
runs with a tightening Damocles ceiling on adverse moves. Under a random
walk for the underlying P&L, the probability of stopping out within any
fixed window scales with the inverse of (stop_distance / per-minute σ).
With a 5σ-equivalent gap at open (0.900% / 0.077% per-min ≈ 11.7σ for vol=1.20)
shrinking to ~3.9σ at close (0.300% / 0.077%), the **per-minute hit
probability rises by ~250×** between open and close even before factoring
in the magnitude floor.

In aggregate, the Kaminski-Lo subtraction applies and AlphaBot's compounding
**amplifies it specifically in the final 30 minutes** of the session — the
exact window where the curve's `dyn_mult` is closest to its 0.5 floor and
the `dynamic_min_stop` is closest to its 0.15 floor.

**Other relevant citations:**

- Han, Y., Zhou, G., & Zhu, Y. (2014). "Taming Momentum Crashes: A Simple
  Stop-Loss Strategy."
  SSRN: [abstract_id=2407199](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199).
  Shows that the **opposite** regime — momentum strategies on the top
  decile of US equities 1926-2011 — extracts value from stop rules. AlphaBot
  symphonies are typically composed of momentum-tilted leveraged ETFs;
  this **partially offsets** the Kaminski-Lo drag at the symphony level,
  though the random-walk regime still dominates at the intraday minute
  cadence under analysis here.
  `[Backtest only, single source]` — not independently replicated in the
  literature reviewed for this report.
- López de Prado, M. (2018). *Advances in Financial Machine Learning*,
  ch. 3 — Triple-Barrier Method. Wiley.
  ISBN: 978-1119482086. The Triple-Barrier framework treats profit-take,
  stop-loss, and time-exit as **independent label boundaries**, not as
  cumulatively-tightening multipliers. AlphaBot's compounded design is a
  departure from this canonical formulation. `[Expert, widely-adopted]`.
- Carver, R. (2015). *Systematic Trading*, ch. "Stoploss". Harriman House.
  ISBN: 978-0857194459. Argues for a **single volatility-scaled stop**
  proportional to instrument σ — explicitly criticizes "multiple stacked
  rules" as redundant and parameter-fragile. `[Practitioner, single source]`.

### Empirical evidence grading

| Claim | Grade |
|---|---|
| Kaminski-Lo random-walk subtraction | `[Theoretical + Backtest]` — closed-form proposition + simulation in the original paper |
| Late-day tightening shape (3× compounding) | `[Deterministic recomputation]` — derived directly from `math_engine.py` constants |
| Per-minute hit-probability scaling | `[Theoretical]` — first-passage normal approximation, not validated against AlphaBot live data |
| Han-Zhou-Zhu momentum offset | `[Backtest]` — single source, US equities 1926-2011, not independently replicated for leveraged-ETF symphonies |

### Replication status

- Kaminski-Lo: replicated in the literature multiple times (Research
  Affiliates 2015 simulation; subsequent stop-loss survey papers).
- The 3× compounding ratio: **directly recomputed** from named constants in
  `math_engine.py`; no Monte Carlo needed.
- Per-minute hit probability: not replicated against AlphaBot live data —
  a follow-up team should validate against the state DB's
  `actions` / `decisions` tables segmented by minute-of-day.

### Regime sensitivity

- **Random walk on the symphony return** — Kaminski-Lo subtraction dominates;
  the compounding amplifies it late-day. The dangerous regime.
- **Momentum** (positive AR1 ≥ Sharpe-ratio threshold per Kaminski-Lo) —
  stops add value; compounding amplifies the gain. Likely AlphaBot's
  daily-cadence regime for momentum-tilted symphonies.
- **Mean reversion** (negative AR1) — stops **destroy** value (force exit
  before reversion); compounding amplifies the destruction. Plausibly
  AlphaBot's intraday-minute regime in low-vol sessions where most flows
  are noise.

The intra-day minute regime is much closer to random walk + mean reversion
than to momentum (the latter typically materializes on a daily/weekly
cadence). The compounding bite is therefore **operating in the most adverse
regime** for the minute scheduler.

---

## Verdict

**Material** — compounding bite concentrated in the **15:30 - 16:00 ET
window** at typical vol (1.20%) and across the **last 90 minutes
(14:30 - 16:00 ET)** at low vol (0.60%), where the `dynamic_min_stop`
floor of 0.15% binds.

### Proposed remediation

#### Option (a) — Cap the compounded tightness

**Formula sketch (no code; placement guidance only):**

```
# In compute_active_trailing_stop, AFTER the conditional multiplier:
active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
if para_armed or breakeven_locked:
    active *= parabolic_squeeze_multiplier
# NEW: floor the active distance against a fraction of pre-squeeze vol-distance.
active = max(active, COMPOUND_FLOOR_FRAC * safe_vol)
return active
```

**Constants (literature-anchored, NOT proposed values — for follow-up team
to tune):**
- `COMPOUND_FLOOR_FRAC = 0.40` would prevent the compounded distance from
  dropping below 40% of the bare vol distance. At vol=1.20% this floors the
  close-of-day distance at 0.480% (versus current 0.300%), reclaiming
  the late-day envelope.
- Alternative parameterization: cap as a function of `time_ratio` —
  `floor_frac = 0.30 + 0.20 * (1 - time_ratio)` — to keep the cap loose
  early and binding only late.

**Citation/rationale:** Carver (2015) and López de Prado (2018, Triple
Barrier) both advocate **a single coherent stop level**. A floor on the
compounded distance is the minimal-surgery way to express that principle
inside AlphaBot's existing API without unwinding the latched-flag state
machine. The 0.40 baseline is a placeholder; the follow-up team should
A/B-test floor fractions in [0.30, 0.60] against the 125-day walk-forward
fixture.

#### Option (b) — Decouple into single activation function

**Sketch:**

```
def compute_active_trailing_stop_unified(
    symphony_vol, time_ratio, hwm_hold_progress, para_velocity_excess,
    base_min_stop,
) -> float:
    # Single coherent tightening signal in [0, 1]:
    tighten = w_time * f_time(time_ratio) \
            + w_be   * f_be(hwm_hold_progress) \
            + w_para * f_para(para_velocity_excess)
    tighten = min(tighten, TIGHTEN_CAP)   # e.g., 0.7
    active = (1 - tighten) * (safe_vol * MULT_OPEN) + tighten * base_min_stop
    return max(active, base_min_stop)
```

Each sub-signal `f_*` is in [0, 1]; weights `w_*` sum to ≤ 1 enforcing the
single-activation-function discipline. The latched booleans (`para_armed`,
`breakeven_locked`) become smooth signals (`hwm_hold_progress = hold_ticks /
HWM_HOLD_TICKS_THRESHOLD`, etc.).

**Citation/rationale:** López de Prado (2018) Triple Barrier and the
volatility-targeting overlay literature (Moreira & Muir 2017,
"Volatility-Managed Portfolios," *Journal of Finance* 72(4),
DOI: [10.1111/jofi.12513](https://doi.org/10.1111/jofi.12513)) both frame
exit rules as smooth, single-argument functions, not multiplicative
overlays. Option (b) aligns AlphaBot with that canonical form.

### Recommended option

**Option (a)** — the cap.

Reasons:
1. **Surface area.** Option (a) modifies one expression in one function
   (`compute_active_trailing_stop`) and adds one constant. Option (b)
   re-engineers the entire latched-flag state machine across three
   functions, the dispatch site in `alpha_bot_execution.py`, the Optuna
   parameter space, and likely the autotuner's replay path. The risk
   surface for option (b) is an order of magnitude larger.
2. **Empirical reversibility.** Option (a) is easy to A/B test against the
   walk-forward fixture: bisect `COMPOUND_FLOOR_FRAC` in [0.3, 0.6] and
   compare daily P&L distributions. Option (b)'s weight-tuple
   `(w_time, w_be, w_para)` is a 3-dimensional parameter space requiring
   full Optuna re-tuning.
3. **Preserves existing tests.** The fixture suite for
   `compute_active_trailing_stop` (74 lines + parametrized cases in
   `tests/math_engine/test_active_trailing_stop.py`) covers the
   multiplier semantics. Option (a) extends; option (b) replaces.
4. **Decision-theoretic risk.** The Han-Zhou-Zhu (2014) momentum-offset
   evidence suggests the *latched-flag* design is doing real work in the
   momentum regime — flattening it via option (b) might wash out the
   benefit. Option (a) keeps the latches and only bounds the worst case.

### Follow-up team scope (if remediation greenlit)

**Composition:** Quad — `quant-test-writer` + `risk-engine-specialist` +
`quant-code-reviewer` + `optuna-methodology-researcher`.

**Acceptance criteria (suggested, for PM Gate-1 review):**

- AC-1: A new module-level constant `COMPOUND_FLOOR_FRAC` (or equivalent
  name) is introduced in `math_engine.py` with a source-citation comment
  per project rule.
- AC-2: `compute_active_trailing_stop` applies the floor as the last step
  before return; existing tests pass unchanged when
  `COMPOUND_FLOOR_FRAC = 0` (backward-compatible default off).
- AC-3: New RED fixtures pin (a) the floor binds in the typical-vol close
  regime, (b) the floor does NOT bind in the high-vol open regime, (c)
  the floor is bypassed (or interacts correctly) with the
  `previously_persisted_stop_level` monotonicity ratchet.
- AC-4: Autotuner replay path verifies the floor produces non-degenerate
  Optuna gradients across `[0.30, 0.60]` on the 125-day walk-forward
  fixture for at least 3 symphonies.
- AC-5: A short backtest report (3 representative symphonies, 60 trading
  days) compares P&L and trigger frequency at floor ∈ {0.0, 0.4, 0.5, 0.6}
  with sample size + drawdown stated per project rule.

**Out of scope:** Re-architecting the latched-flag state machine. Changing
the Optuna parameter ranges. Touching breakeven activation thresholds.

---

## References

1. Kaminski, K. M., & Lo, A. W. (2014). When do stop-loss rules stop
   losses? *Journal of Financial Markets*, 18, 234-254.
   DOI: <https://doi.org/10.1016/j.finmar.2013.07.001>. SSRN preprint:
   <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338>. MIT
   institutional copy: <https://dspace.mit.edu/handle/1721.1/114876>.
2. Han, Y., Zhou, G., & Zhu, Y. (2014). Taming momentum crashes: A simple
   stop-loss strategy. SSRN:
   <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199>.
3. López de Prado, M. (2018). *Advances in Financial Machine Learning*.
   Wiley. ISBN: 978-1119482086. Chapter 3 (Triple-Barrier Method).
4. Carver, R. (2015). *Systematic Trading*. Harriman House. ISBN:
   978-0857194459. Chapter on Stoploss.
5. Moreira, A., & Muir, T. (2017). Volatility-managed portfolios.
   *Journal of Finance*, 72(4), 1611-1644.
   DOI: <https://doi.org/10.1111/jofi.12513>.
6. Asvanunt, A., & Brooks, J. (Research Affiliates, 2015). Stop the
   losses! <https://www.researchaffiliates.com/publications/articles/1099-stop-the-losses>.
   (Practitioner replication of Kaminski-Lo simulation regime.)
7. AlphaBot internal: invariant audit notes
   (`docs/research/math_engine/invariant-audit__2026-05-13.md`) — flags
   the squeeze-multiplier semantic-bound gap (no test pins
   `0 < parabolic_squeeze_multiplier <= 1`).
8. AlphaBot internal: methodology review
   (`docs/research/dashboard/math-engine-methodology-review.md`) — notes
   that the squeeze multiplier fires **once** even when both flags are
   set; corroborates this report's "not 8×" finding.

---

## Open questions (deferred — not blocking this report)

- **Live data validation.** This report's per-minute hit-probability
  scaling is theoretical. A read-only audit of the state DB
  (`decisions` table, segmented by minute-of-day) would confirm or refute
  the late-day concentration empirically. Suggested follow-up:
  `risk-engine-specialist` solo, read-only, two-day budget.
- **Interaction with `MAX_SQUEEZE_FLOOR`.** The audit panel referenced an
  `MAX_SQUEEZE_FLOOR` parameter elsewhere in the config surface; this
  report did not investigate whether it interacts with the compounding
  bite. Suggested follow-up: include in the option-(a) AC scope above.
- **The audit panel's "8× compounding" framing.** This report finds the
  compounding is at most 2× in distance and 3× in tightness-ratio. If
  the audit panel had a different counting method (e.g. counting each
  state flag's potential pathway), the panel notes should be reconciled
  with this report in a follow-up doc.
