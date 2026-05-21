# Math engine audit — full-layer rigor sweep

**Date:** 2026-05-15
**Author:** risk-engine-specialist (read-only audit; no code changes)
**Scope:** Every math layer in `math_engine.py` (8 layers + cross-cutting) audited for correctness, units, state transitions, edge cases, consumer alignment, test coverage, and constant provenance. Companion to the VWAP audit (`docs/research/dashboard/vwap-audit.md`).

---

## Section 0 — Executive summary

| # | Layer | Verdict | Severity of open issues |
|---|-------|---------|-------------------------|
| 1 | Volatility scaling (`calculate_20d_vol` / `calculate_14d_atr_pct`) | **Correct against spec.** Edge-case missing-ticker SPY-substitution is documented + tested. Magnitude is sensitive to weight-sum (allocations are NOT renormalized). | LOW |
| 2 | Log time squeeze (`compute_time_squeeze_decay`) | **Correct.** log10-based decay is well-pinned by 6 fixtures + monotonicity + endpoint properties. No issues. | NONE |
| 3 | Parabolic ratchet (`compute_para_arm_decision`) | **Math is correct. SEMANTICS at day-1 first cycle are wrong by spec.** `prev_return=0.0` wipe (`database.py:140`) means EVERY trading day's first cycle in the action phase computes `velocity = current_return - 0.0 = current_return`. Any symphony opening with `current_return >= PARABOLIC_VELOCITY_THRESHOLD` (default 2%) auto-PARA-ARMs on the first action-phase tick — by accident, not by velocity. The VWAP audit's open question is **CONFIRMED.** | **HIGH** |
| 4 | MC gating (`run_monte_carlo`) | Correct against spec. **RNG NOT seeded inside `run_monte_carlo` — uses global numpy state.** This is acceptable in production (sample is 5000 paths → CLT stable) but means cycle-to-cycle determinism depends on outer seed state. Sample size is principled. Edge cases pinned by 4 fallback fixtures + 7 gating fixtures. | LOW (with docs gap) |
| 5 | Breakeven (`compute_breakeven_update`) | **Correct against spec including the monotonicity-ratchet extension.** Latching invariant + post-breakeven floor invariant + lifetime monotonicity invariant all pinned by `test_stop_monotonicity.py`. Caller `alpha_bot_execution.py:698-705` does NOT pass `previously_persisted_stop_level` — **the monotonicity-clamp is dead code in production.** | **HIGH** |
| 6 | Exit confirm (`compute_exit_confirmation` + trigger-priority loop) | Math correct. Order-of-priority in `alpha_bot_execution.py:819-831` is `tp > vwap_bleed > vwap_breakdown > trailing_stop` BY ATTRIBUTION ONLY — all four signals are computed and **all four side-effects fire if all four are True simultaneously** (only the displayed reason differs). | MEDIUM |
| 7 | HWM tracking (3 fields: `high_water_mark`, `shadow_hwm`, `safe_hwm`) | Three semantically distinct fields. `high_water_mark` is the live trailing-stop anchor, zeroed by sentinel `-999.0` on trigger; `shadow_hwm` is post-trigger peak for shadow accounting; `safe_hwm` is a per-cycle computed safe view. **Naming is unclear and `-999.0` sentinel is a global gotcha.** | MEDIUM |
| 8 | Constants + provenance | All 25 module-level constants in `math_engine.py` are named with source comments (project rule satisfied). 9 constants are Optuna-tuned per-symphony (autotuner sweeps lines 306-312); the rest are hand-set engineering choices. | LOW |

**Top-3 risk-ranked findings to escalate to the operator:**

1. **PARA-ARM-at-open bug (HIGH).** The day's first action-phase cycle always sees `velocity = current_return`. Defaults arm at 2%. Every symphony opening ≥ 2% gets PARA-ARMED for the wrong reason. Today's VWAP cascade (all 11 PARA-ARMED at 10:30) is the operational symptom of this.
2. **Monotonicity-ratchet is unused (HIGH).** `compute_breakeven_update` accepts `previously_persisted_stop_level` but `alpha_bot_execution.py:698` never passes it. The math layer is ready to enforce the canonical Fu & Zhang 2010 trailing-stop ratchet; the caller still ignores it. Trailing stops can drop tick-to-tick today.
3. **Concurrent trigger fire (MEDIUM).** When System A (VWAP profit-protection break) and trailing-stop both fire on the same cycle, both `bot_state[sym]['triggered']=True` paths execute identically — only the Discord embed's reason string changes. The data-vs-action split is intact, but the priority logic at `alpha_bot_execution.py:819-831` is a labeling decision, not a gating decision.

---

## Section 1 — Per-layer audit

### 1.1 Volatility scaling (`math_engine.py:523-555`)

**Function:** `calculate_20d_vol(holdings, historical_data) -> float`

**Formula (line 550):** `daily_returns = returns_matrix.dot(weights) * PCT_SCALAR` then `np.std(daily_returns)` (line 555).

- **Numerical correctness:** `np.std` is population stdev (ddof=0). For a portfolio of correlated assets this is conventional; the producer was the inline code at `alpha_bot_execution.py` cycle-1 and the extraction is byte-equivalent (pinned by 8 fixtures in `tests/fixtures/math_engine/volatility_scaling/*.json`).
- **Units:** Input `daily_ret` is decimal (e.g., 0.025 for 2.5%). Output is "percentage points of daily-return stdev" because of `* PCT_SCALAR` (line 550). A `symphony_vol = 1.55` from yesterday's post-mortem means "1.55 pp daily return stdev." This unit is consumed by 4 downstream sites:
  1. `compute_active_trailing_stop` (line 144): `safe_vol * dynamic_multiplier` — pp × dimensionless = pp ✓
  2. `compute_breakeven_update` (line 202): `max(0.4, min(3.0, symphony_vol))` — pp clamped to [0.4, 3.0] pp ✓
  3. `compute_vwap_bleed_arm_threshold` (line 354): `-(symphony_vol * bleed_multiplier)` — pp × dimensionless = pp ✓
  4. Caller passes to all three above as float, consistent.
- **Edge: insufficient history (line 533-534):** `len(valid_dates) < LOOKBACK_DAYS (20)` → return 0.0. Pinned by `test_volatility_is_zero_when_insufficient_history` (sweeps n=0..19).
- **Edge: missing ticker on a given day (line 547-548):** Falls back to SPY's daily return for that day. This is a **silent substitution** — a holding completely absent from history is treated as if it traded with SPY. Documented in test_atr.py docstring and pinned in MC fallback fixture 03.
- **Edge: NaN propagation:** `_reject_non_finite_in_records` (line 528) rejects NaN in `allocation` and `daily_ret` at the entry; pinned by `tests/math_engine/test_nan_policy_list_inputs.py`.
- **Allocation renormalization:** `weights` is taken as-is from the holdings list. **If allocations sum to < 1.0 (e.g., a holding was dropped from the basket but its weight was not re-spread), the vol output is DILUTED proportionally.** Not a bug in `calculate_20d_vol` — it's a caller-data contract — but documented hazard.
- **Test coverage:** 8 golden fixtures + 6 properties (non-negative, monotone-in-amplitude, insufficient-history, last-20-of-many, linearity-in-allocation, magic-number scan + canonical-constants pin).
- **NOT pinned:** behavior when `historical_data` contains a date key with `{}` (no ticker entries at all). The function would call `day_data.get("SPY", {}).get("daily_ret", 0.0)` → 0.0 silently. If this regime ever fires in production it would understate vol.

**`calculate_14d_atr_pct` (lines 557-606):** Same patterns. Falls back to `calculate_20d_vol` on any missing-OHLC condition. Pinned by `test_atr.py` fixtures.

**Verdict:** CORRECT. The dilution-by-coverage hazard is documented but not currently exercised.

### 1.2 Log time squeeze (`math_engine.py:88-112`)

**Function:** `compute_time_squeeze_decay(time_ratio) -> (multiplier, min_stop)`

**Formula (lines 109-111):**
```
decay_curve = math.log10(1 + 9 * time_ratio)         # in [0, 1] when time_ratio in [0, 1]
dynamic_multiplier = MULT_OPEN - (MULT_OPEN - MULT_CLOSE) * decay_curve   # 1.5 → 0.5
dynamic_min_stop   = MIN_STOP_OPEN - (MIN_STOP_OPEN - MIN_STOP_CLOSE) * decay_curve   # 0.3 → 0.15
```

- **Math correctness:** `log10(1 + 9*1) = log10(10) = 1` exactly; the scalar 9 (named `DECAY_CURVE_SCALAR`) is chosen so the boundary lands on exactly 1. Pinned by `test_at_time_ratio_zero_returns_open_constants` and `test_at_time_ratio_one_returns_close_constants`. 6 fixtures pin midday, early, late, and constructed-clean (`decay=0.5 exactly`) cases.
- **Sign convention:** decay_curve is monotone non-decreasing; multiplier and min_stop decay from open-values to close-values. Pinned by `test_dynamic_multiplier_is_monotonically_non_increasing` (dense 19-point sweep across [0, 1]).
- **Caller's clamp contract (`alpha_bot_execution.py:683`):** `time_ratio = max(0.0, min(1.0, (current_et - m_open_dt)/(m_close - m_open)))`. If caller passes `m_close - m_open == 0` the caller crashes BEFORE the math layer is reached. That's pre-action-phase data hygiene.
- **Edge: caller-clamped contract documented in docstring (line 92-93):** "CALLER clamps before passing; this function does not validate." If a future caller passes `time_ratio > 1`, `decay_curve > 1` and `dynamic_multiplier` can go negative. The math is well-defined for `time_ratio > -1/9` but the contract is `[0, 1]`. Not pinned (no fixture for out-of-range input).
- **Consumer:** `compute_active_trailing_stop` (line 687-694). Consumer is the SINGLE consumer.
- **Test coverage:** 6 golden fixtures + 7 properties (zero-time → open, one-time → close, monotonicity ×2, range-bound ×2, determinism, type contract). Magic-number scanner + constant-canonical-value scanner.

**Verdict:** CORRECT. Best-tested layer in the file.

### 1.3 Parabolic ratchet (`math_engine.py:64-85`)

**Function:** `compute_para_arm_decision(current_return, prev_return, para_threshold, currently_armed) -> (velocity, should_arm)`

**Formula:**
```
velocity = current_return - prev_return
should_arm = (velocity >= para_threshold) AND (not currently_armed)
```

- **Math correctness:** Pinned by 10 fixtures + 4 properties + magic-number scanner. `>=` boundary, exact subtraction, latching (once armed never re-arms), pure function. **Math is correct.**
- **State transition:** Once `should_arm=True`, caller sets `bot_state[sym]['para_armed']=True` (line 676). Caller does NOT reset (one-way latch). Latching is correct.
- **Consumer:** `compute_active_trailing_stop` (line 687-694), via `para_armed=bool(bot_state[...].get('para_armed'))` (line 691). If para_armed=True, active_trailing_stop is multiplied by `MAX_PARABOLIC_SQUEEZE` (default 0.50) → tightens the stop. This is the intended ratchet behavior.

#### THE BUG: day-1 first cycle `prev_return` is wiped to 0.0 (CRITICAL)

The audit's open question (VWAP audit §4 item 5) is now CONFIRMED:

- `database.py:140`: `s_data["prev_return"] = 0.0` on every new-day wipe.
- `alpha_bot_execution.py:665`: `prev_return = bot_state[symphony_id].get("prev_return", current_return)` — fallback is `current_return`, but the dict ALREADY has `"prev_return": 0.0` (the wipe set it), so the `.get()` returns 0.0 (NOT the fallback).
- `alpha_bot_execution.py:667-672`: `compute_para_arm_decision(current_return=current_return, prev_return=0.0, ...)` → `velocity = current_return`.
- Caller default `PARABOLIC_VELOCITY_THRESHOLD = 2.0` (`.env` or `alpha_bot_execution.py:57`).
- **Therefore:** Every symphony opening with `current_return >= 2.0` (i.e., position is already up >= 2% from prior day's close at the time of the first action-phase cycle) gets `should_arm=True` on tick 1. This auto-arms the parabolic squeeze (tighter stops) for no velocity reason at all.

**Evidence from today (per VWAP audit):** all 11 symphonies fired `PARA-ARMED` at 10:30 ET — the first action-phase cycle. Today's open spike pushed everything up >= 2% from yesterday's close. The PARA-ARMED log message correctly reports `velocity = current_return` (the bug), but the operator reads it as "the position is in a parabolic move," which is a different claim.

**Why this is wrong:**
- The parabolic-ratchet's domain meaning per the existing comments and the autotuner's `PARABOLIC_VELOCITY_THRESHOLD` sweep range (1.0 to 4.0, autotuner.py:311) is *intra-cycle velocity*: the position moved fast in the last minute. The first-cycle-of-day measurement is *opening-gap*, not *intra-cycle velocity*. These are different signals.
- The autotuner's simulation correctly initializes `prev_return = 0.0` at the START of each simulated day (autotuner.py:94), which seeds the same bug into the replay — so the Optuna-tuned thresholds are calibrated against a misspecified velocity signal. The autotuner's choices for `PARABOLIC_VELOCITY_THRESHOLD` are biased by this.

**Three possible operator-intended semantics — the code matches NONE of them perfectly:**
1. *"Velocity = intra-cycle move"* → first cycle should not arm at all (need 2 cycles to define velocity). Fix: skip arming on first cycle when wipe sentinel still present.
2. *"Velocity = overnight gap + intra-day move"* → current behavior IS this, but it conflates two regimes (open gap is mean-reverting, intra-day velocity is trend-following). Fix: separate thresholds.
3. *"Velocity = since-open move"* → on first cycle, `prev_return` should be set to the value at market-open, not yesterday's close. Today's `last_percent_change` is computed by Composer against yesterday's close. To get since-open velocity the caller needs the open value, which is not currently fetched.

**Test coverage gap:** No test exercises the day-1 first-cycle integration where `wipe_transient_state` runs THEN the first cycle's `compute_para_arm_decision` is called with `prev_return=0.0`. All para-arm tests (`test_parabolic_squeeze.py`) directly call the pure function — the wipe interaction is invisible.

### 1.4 MC gating (`math_engine.py:448-521`)

**Function:** `run_monte_carlo(holdings, historical_data, spy_today_return, simulation_paths=5000, neighbor_k=150) -> float`

**Algorithm:**
1. Compute today's holdings-weighted return (line 461-464).
2. If `len(valid_dates) < MC_MIN_HISTORY_DAYS` (20): short-circuit with `MC_INSUFFICIENT_HISTORY_PROB = 100.0` (line 467).
3. Compute SPY rolling 20-day vols (line 472-478).
4. Compute today's SPY vol over the most recent 19 prior days + today (line 483). NB: `MC_VOL_WINDOW_DAYS - 1 = 19` is documented as inclusive endpoint.
5. Compute Euclidean distance in 2D (SPY return, SPY vol) (line 486).
6. Select K nearest neighbor days (line 488-493). Uses `np.argpartition` (O(N)) for K < N, full set otherwise.
7. Build returns matrix for nearest days × holdings tickers, substituting `spy_ret` for missing tickers (line 502-511).
8. Compute path returns: matrix dot weights × PCT_SCALAR (line 514).
9. Sample `simulation_paths` (default 5000) draws with replacement (line 517).
10. Sort + compute `P(simulated_return > current_return) * 100.0` (line 519-521).

- **Numerical correctness:** Pinned by 7 behavioral-equivalence fixtures (`test_mc_gating_magic_numbers.py`) with ZERO TOLERANCE (`actual == expected`). Refactoring tests show the algorithm survives byte-for-byte under the magic-number rename.
- **Sample size principled:** `MC_DEFAULT_SIMULATION_PATHS = 5000` gives CLT-stable estimate (1/sqrt(5000) ≈ 1.4% standard error on the probability estimate). Within Optuna sweep range. Adequate.
- **Neighbor K principled:** `MC_DEFAULT_NEIGHBOR_K = 150` is roughly the number of trading days in ~7 months — a regime locality choice. Hand-set; not Optuna-tuned.
- **RNG seeding (line 517):** `np.random.choice` uses the **global numpy RNG state**. The function does NOT seed internally. This means:
  - Tests must `np.random.seed(seed)` BEFORE calling (which they do, `test_mc_fallbacks.py:154`, `test_mc_gating_magic_numbers.py:210`).
  - In production, the engine cycle ordering implicitly seeds via wall-clock numpy import — adjacent calls within one cycle are NOT independent draws because they share global RNG state advance. With 5000 paths and ~30 symphonies per cycle, the cross-symphony correlation in `prob_beating` is negligible (each `np.random.choice` advances the state by ~5000 64-bit draws, far beyond any correlation horizon).
  - **Reproducibility hazard:** Two runs of the engine on the same data WILL produce different `prob_beating` values. Today's `mc_history` for symphony X cannot be reproduced byte-for-byte. The autotuner replay must use the same numpy RNG state to get reproducible scoring — autotuner.py does NOT seed (it implicitly inherits global state).
- **Edge: zero variance (constant returns):** If all `nearest_day_returns` are equal, `np.random.choice` returns that constant; `np.searchsorted` returns 0 or `simulation_paths`. Result is 100.0 or 0.0. NOT crashing. Pinned by `test_mc_fallbacks.py` fixture 04.
- **Edge: insufficient history:** Returns `MC_INSUFFICIENT_HISTORY_PROB = 100.0`. Pinned by fixture 01.
- **Dead branch documented (test_mc_fallbacks.py docstring lines 6-18):** Line 463 short-history `else` branch is structurally unreachable when `MC_MIN_HISTORY_DAYS >= MC_VOL_WINDOW_DAYS`. Documented; not removed (defensive).
- **Consumer:** `prob_beating` gates:
  - ARMING (line 645-651): arms position when `acc_TAKE_PROFIT_MC_PCT <= prob_beating < acc_TRIGGER_THRESHOLD_PCT` (typically `5 <= prob < 15`).
  - DISARM (line 655): disarms when `prob_beating > 2 × TRIGGER_THRESHOLD_PCT` and `current_return > 0`.
  - TP-ARM (line 730-735): arms take-profit when `prob_beating < acc_TAKE_PROFIT_MC_PCT` (typically `< 5`).
  - EXIT SANITY (line 716 via `compute_exit_confirmation`): blocks trailing-stop exit when `prob_beating >= 60.0` ("we still think we beat the benchmark — don't capitulate").
- **Boundary `< MC_SANITY_THRESHOLD = 60.0`:** Strict `<`. A `prob_beating == 60.0` exact does NOT count as "MC sanity fails." Pinned in exit-confirmation tests.

**Verdict:** CORRECT against spec. RNG-non-determinism is a documented property, not a bug. Test coverage is strong (7 gating + 4 fallback + magic-number provenance).

### 1.5 Breakeven (`math_engine.py:150-218`)

**Function:** `compute_breakeven_update(current_return, symphony_vol, base_stop_level, current_hold_ticks, currently_breakeven_locked, is_triggered, previously_persisted_stop_level=None) -> (new_hold_ticks, new_breakeven_locked, stop_trigger_level)`

**Algorithm:**
1. `dynamic_activation = max(0.4, min(3.0, symphony_vol))` — vol-tied threshold for "we're far enough into profit to consider locking breakeven."
2. If `current_return >= dynamic_activation - 0.2`: increment `hwm_hold_ticks`. Else reset to 0.
3. `new_breakeven_locked = currently_breakeven_locked OR (new_hold_ticks >= 5)` — one-way latch.
4. If locked: `stop_trigger_level = max(base_stop_level, 0.0)` — breakeven floor anchor.
5. Else: `stop_trigger_level = base_stop_level`.
6. If `is_triggered=True`: override to `TRIGGERED_OVERRIDE_LEVEL = -999.0`.
7. **NEW (added by the monotonicity-ratchet cycle): if `previously_persisted_stop_level is not None` AND not triggered: clamp `stop_trigger_level = max(previous, new)`.**

- **Math correctness:** All branches pinned by `test_breakeven_update.py` (10+ fixtures) and `test_stop_monotonicity.py` (7 multi-call lifecycle tests).
- **Latching invariant:** `currently_breakeven_locked=True → new_breakeven_locked=True` regardless of other inputs. Pinned by `test_latching_invariant_locked_stays_locked` (8 parametrized cases).
- **Breakeven floor invariant:** When locked, `stop_trigger_level >= 0.0`. Pinned by `test_post_breakeven_stop_level_never_below_zero` (10 parametrized base levels including `-1e-12`).
- **Triggered override absolute:** When `is_triggered=True`, output is `-999.0` regardless of locked, hold_ticks, base. Pinned by `test_triggered_override_is_absolute` (7 parametrized cases).
- **Monotonicity-ratchet (lifecycle invariant):** `test_stop_monotonicity.py` parametrizes 7 scenarios; 4 of them (`retrace_above_zero`, `retrace_dip_recover`, `retrace_below_zero`, `oscillating_above_zero`) WOULD FAIL on the old behavior. They PASS with the new `previously_persisted_stop_level` clamp.

**THE BUG: the production caller does NOT thread the new monotonicity parameter (HIGH)**

`alpha_bot_execution.py:698-705`:
```python
new_hold_ticks, new_breakeven_locked, stop_trigger_level = math_engine.compute_breakeven_update(
    current_return=current_return,
    symphony_vol=symphony_vol,
    base_stop_level=base_stop_level,
    current_hold_ticks=bot_state[symphony_id]["hwm_hold_ticks"],
    currently_breakeven_locked=bot_state[symphony_id]["breakeven_locked"],
    is_triggered=bot_state[symphony_id]["triggered"],
)
```

The kwarg `previously_persisted_stop_level` is **absent**. Default is `None`. Therefore:
- The monotonicity-ratchet clamp branch (line 215-217) is **unreached in production code**.
- The trailing-stop CAN drop from one cycle to the next when `safe_hwm` retraces faster than the stop-distance shrinks (post-lock, `base_stop_level = safe_hwm - active_trailing_stop` can fall, and the `max(base, 0.0)` floor only catches drops below zero).
- The math layer was prepared for this (the new kwarg + clamp logic exists and is tested), but the engine never adopted it.

**Evidence:** `bot_state[sym]['stop_trigger']` is persisted (line 781) but never threaded back into the next cycle's call. A simple grep confirms `previously_persisted_stop_level` appears only in `math_engine.py` (signature + docstring + impl) and `tests/math_engine/test_stop_monotonicity.py` (helper) — never in `alpha_bot_execution.py`.

This is the kind of "extracted API has an option that the caller forgot to use" gap the project's risk-engine specialist rules explicitly call out (Verify Before X §1 — never refactor without confirming output equivalence at the caller).

**Test coverage:**
- Unit-level: strong (10+ fixtures, latching + override + floor + 4 properties + magic-number scanner).
- Integration-level: missing. No test in `tests/execution/` asserts the engine threads `previously_persisted_stop_level` correctly. If one were added, it would fire RED today.

### 1.6 Exit confirm (composition of triggers in the action phase)

**Function:** `compute_exit_confirmation(armed, is_triggered, current_return, stop_trigger_level, prob_beating, current_below_stop_count) -> (new_below_stop_count, is_trailing_stop_hit)`

**Math:** Counts consecutive cycles where `current_return <= stop_trigger_level - 0.10` AND `prob_beating < 60.0`. After 3 consecutive ticks, returns `is_trailing_stop_hit = True`. Otherwise resets to 0 (full reset, not decrement). Guard: skipped when `armed=False` or `is_triggered=True` — counter preserved unchanged. Pinned by `test_exit_confirmation.py` (10+ fixtures + 5 properties + 2 magic-number scanners).

**Pipeline composition (`alpha_bot_execution.py:709-854`):**

The action phase runs FOUR trigger evaluations every cycle for every armed symphony:
1. **Check 1 (line 709-718):** `is_trailing_stop_hit` = `compute_exit_confirmation(...)`.
2. **Check 2 (line 728-751):** `tp_triggered_now` = take-profit (2-tick confirm, requires `prob_beating < TAKE_PROFIT_MC_PCT`).
3. **Check 3 (line 753-767):** `is_vwap_broken` + `is_vwap_bleed_broken` = `compute_vwap_breakdown_update(...)`.
4. **Gate (line 819):** `if is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken:` — appends to execution queue.
5. **Priority resolution (line 820-831):** When multiple are True, the displayed `reason` is chosen with priority `tp_triggered_now > is_vwap_bleed_broken > is_vwap_broken > is_trailing_stop_hit`.

**Critical observation:** Priority is **LABEL ONLY** — `triggered=True` is set the same regardless of which signal fired, and the position is sold-to-cash the same regardless. The four counters (`below_stop_count`, `above_tp_count`, `vwap_ticks`, `vwap_bleed_ticks`) all advance independently. If three of them mature on the same cycle, all three reset paths execute, all three event logs emit, but only ONE Discord embed is sent (per execution_queue item).

**Concurrent-trigger scenario (TODAY'S CASE):**
- All 11 symphonies tripped `VWAP Breakdown` at 10:35 ET.
- Some of them likely ALSO had `is_trailing_stop_hit=True` (current_return retraced below stop-trigger level), since the calibration that lit VWAP also tightens the trailing-stop. The priority logic picked `VWAP Breakdown` (line 826-828) before `Trailing Stop` (line 829-831).
- **The "reason" attribution today is partly a label artifact, not a clean root cause.** If the operator dug into post-mortems and saw all-VWAP-Breakdown, that doesn't mean trailing-stop wasn't equally close to firing.

**Action-phase gate placement:** All four trigger computations happen INSIDE the `if current_time >= market_open` block (line 480-481 gate guards earlier return). The DATA phase (lines 393-477) does NOT compute any of these — that's the data-vs-action split. **VERIFIED CLEAN.** No math function with side-effects on triggers can reach pre-EXECUTION_START_TIME.

**EXECUTION_START_TIME gate (line 480):** `if current_time < market_open and not force_run: return`. Triggered AFTER the data phase completed. State-write of HWM, vol, current_return happens in data phase via `database.save_state(bot_state)` (line 477). Action-phase trigger evaluations only run when `current_time >= market_open`. **CORRECT.**

**Test coverage:** Unit-level strong. Integration-level partial: `tests/execution/test_data_action_split.py` pins the data/action boundary, but no test exercises the simultaneous 4-trigger case to assert that priority labeling matches expectation.

### 1.7 HWM tracking — 3 fields explained

Three distinct HWM-class fields exist:

| Field | Lifecycle | Reset on trigger? | Reset on new day? | Purpose |
|-------|-----------|-------------------|-------------------|---------|
| `high_water_mark` | live trailing-stop anchor | YES → `-999.0` (line 942) | YES → `-999.0` (database.py:138) | The live "peak return seen this day" that gates `safe_hwm` |
| `shadow_hwm` | post-trigger peak tracker | NO (stays monotone) | YES → `-999.0` (database.py:139) | Shows what HWM "would have been" if the engine had not exited — used in dashboard column |
| `safe_hwm` | per-cycle computed view | n/a (not persisted) | n/a | Defensive read: `high_water_mark if high_water_mark != -999.0 else current_return` (alpha_bot_execution.py:632) |

- **`high_water_mark` daily reset:** Wipe writes `-999.0`. First data-phase cycle (line 458) re-initializes via `if current_return > bot_state[s_id].get("high_water_mark", current_return)` — but `bot_state[s_id]['high_water_mark'] = -999.0` survives the `.get()`, so the comparison is `current_return > -999.0` (almost always True on the first cycle), so `high_water_mark` becomes today's first `current_return` on cycle 1. **Behaves correctly.**
- **`high_water_mark` post-trigger:** Set to `-999.0` at line 942 when execution succeeds. The `safe_hwm` defense at line 632 kicks in: `safe_hwm = current_return` instead of `-999.0`. Without this defense, the post-trigger cycle would compute `stop_trigger_level = -999 - active_trailing_stop`, a huge negative number that would re-trigger immediately. **The defense at line 632 is load-bearing.**
- **`shadow_hwm` post-trigger:** Stays monotone (line 462-463 / 628-629) so the dashboard can report "the HWM the position WOULD have hit if we'd held." Dashboard sorts on `shadow_hwm` (`app.py:263-264`). **Verified correct in dashboard tests.**
- **`-999.0` sentinel concerns:** This sentinel doubles as (a) "wiped/unset HWM" (database.py:138-139) and (b) "post-trigger HWM" (alpha_bot_execution.py:942) and (c) "no tracked-stop available" (alpha_bot_execution.py:802-804). Three semantic roles, one numeric value. **Operational hazard:** if any future code path accidentally uses `high_water_mark` directly (without `safe_hwm` translation) it gets `-999.0` and produces a nonsensical answer. The `safe_hwm` translation at line 632 is the SOLE defense. There's no test pinning the invariant "no caller ever reads `high_water_mark` without `safe_hwm` translation."
- **Path-dependence:** All three HWMs are path-dependent (depend on the sequence of `current_return` observations). `high_water_mark` is monotone within a day; `shadow_hwm` is monotone across the position's lifetime within a day. Neither is preserved across days (wipe sets both to -999.0).

**Verdict:** Math is correct. **Naming is a maintenance hazard** — `safe_hwm` is computed inline (line 632), not a persistent field; a future developer reading the code may think it's three persistent fields when it's two. Should be documented in a runbook.

### 1.8 Constants + provenance

All 25 module-level constants in `math_engine.py` are named with source comments (project rule satisfied). Magic-number scanners in 5 test files (`test_volatility_scaling.py`, `test_time_squeeze_decay.py`, `test_breakeven_update.py`, `test_exit_confirmation.py`, `test_mc_gating_magic_numbers.py`) verify no bare literals slip in.

| Constant | Value | Line | Source | Tuned? |
|----------|-------|------|--------|--------|
| `LOOKBACK_DAYS` | 20 | 37 | AlphaBot risk-sizing standard (20d rolling vol) | hand-set |
| `ATR_LOOKBACK_DAYS` | 15 | 38 | 14-day ATR + 1 prior close | hand-set |
| `PCT_SCALAR` | 100.0 | 39 | unit conversion | structural |
| `MC_INSUFFICIENT_HISTORY_PROB` | 100.0 | 42 | "if no data, can't reject hypothesis we beat benchmark" | hand-set |
| `MC_MIN_HISTORY_DAYS` | 20 | 43 | min for MC sim | hand-set |
| `MC_VOL_WINDOW_DAYS` | 20 | 44 | rolling SPY vol window | hand-set |
| `MC_DEFAULT_SIMULATION_PATHS` | 5000 | 45 | CLT stability | hand-set |
| `MC_DEFAULT_NEIGHBOR_K` | 150 | 46 | kNN regime locality | hand-set |
| `DECAY_CURVE_SCALAR` | 9 | 49 | `log10(1+9*1)=1` boundary | hand-set (mathematical) |
| `MULT_OPEN` | 1.5 | 50 | open-time stop multiplier | hand-set |
| `MULT_CLOSE` | 0.5 | 51 | close-time stop multiplier | hand-set |
| `MIN_STOP_OPEN` | 0.3 | 52 | open-time min stop floor (pp) | hand-set |
| `MIN_STOP_CLOSE` | 0.15 | 53 | close-time min stop floor (pp) | hand-set |
| `VOL_FALLBACK` | 1.0 | 54 | degenerate-vol substitute | hand-set |
| `BREAKEVEN_ACTIVATION_MIN` | 0.4 | 57 | lower clamp on dynamic activation | hand-set |
| `BREAKEVEN_ACTIVATION_MAX` | 3.0 | 58 | upper clamp on dynamic activation | hand-set |
| `BREAKEVEN_ACTIVATION_DEADBAND` | 0.2 | 59 | qualifying-tick deadband (pp) | hand-set |
| `HWM_HOLD_TICKS_THRESHOLD` | 5 | 60 | consecutive ticks to lock breakeven | hand-set |
| `TRIGGERED_OVERRIDE_LEVEL` | -999.0 | 61 | post-trigger stop sentinel | hand-set (sentinel) |
| `MAGNITUDE_FLOOR_PCT` | 0.10 | 222 | exit-confirm magnitude offset (pp) | hand-set |
| `MC_SANITY_THRESHOLD` | 60.0 | 223 | "if MC > 60, don't exit" | hand-set |
| `EXIT_CONFIRM_TICKS` | 3 | 224 | consecutive ticks for exit confirm | hand-set |
| `VWAP_BLEED_ARM_MIN` | -3.0 | 325 | most-permissive bleed arm clamp (pp) | hand-set |
| `VWAP_BLEED_ARM_MAX` | -0.5 | 326 | most-cautious bleed arm clamp (pp) | hand-set |
| `VWAP_WEIGHT_THRESHOLD` | 0.5 | 359 | min coverage to evaluate VWAP gate | hand-set |
| `VWAP_BREAK_CONFIRM_TICKS` | 3 | 360 | consecutive ticks for System A | hand-set |

**Optuna-tuned (per-symphony) parameters (autotuner.py:306-312):** These are NOT in `math_engine.py` — they live in `database.symphony_strategies` and are passed in by the caller:

| Param | Sweep range | Default | Consumer |
|-------|-------------|---------|----------|
| `TRIGGER_THRESHOLD_PCT` | 5.0 – 25.0 | 15.0 | MC arming gate (line 645) |
| `TAKE_PROFIT_MC_PCT` | 2.0 – 10.0 | 5.0 | MC TP arming gate (line 730) |
| `VWAP_CROSS_HWM_PCT` | 0.5 – 2.5 | 1.0 | System A profit-protection gate (line 760) |
| `VWAP_BLEED_MULTIPLIER` | 0.5 – 3.0 | 1.5 | Bleed arm threshold (line 559) |
| `VWAP_BLEED_TICKS` | 3 – 30 | 10 | System B confirm ticks (line 560) |
| `PARABOLIC_VELOCITY_THRESHOLD` | 1.0 – 4.0 | 2.0 | Para-arm threshold (line 666) |
| `MAX_PARABOLIC_SQUEEZE` | 0.1 – 0.8 | 0.50 | Active trailing-stop squeeze (line 693) |

**The autotuner's `PARABOLIC_VELOCITY_THRESHOLD` sweep is contaminated** by the same prev_return=0.0 bug because the autotuner simulation `autotuner.py:94` initializes `prev_return = 0.0` at each simulated-day start. So the Optuna search produces thresholds that are calibrated against a "day-1 first cycle always sees velocity=current_return" world — exactly the production behavior. Tuning is self-consistent with the bug.

**Verdict:** Constants discipline is good. The interpretation of the PARABOLIC_VELOCITY_THRESHOLD knob is muddled by the day-1 bug.

---

## Section 2 — Cross-cutting findings

### 2.1 Action-phase leakage check

Searched for state-writes that could happen pre-EXECUTION_START_TIME:

- `alpha_bot_execution.py:393-481` (DATA phase): writes `current_return`, `current_value`, `name`, `account`, `symphony_vol`, `high_water_mark`, `shadow_hwm`, `current_holdings`, Composer inception fields. Calls `database.save_state(bot_state)` (line 477) if pre-gate. **No trigger fields written.**
- `alpha_bot_execution.py:485-522` (POST-MORTEM): writes `post_mortem_run`, `current_holdings`, `current_return`. Calls `autotuner.run_autotuner`. **No live-trigger fields written.**
- `alpha_bot_execution.py:524-854` (ACTION phase): writes trigger fields ONLY. Reaches this section only after `current_time >= market_open` gate (line 480) or `--force`. **CORRECT.**

**No leak found.** The data-vs-action split (commit 46fe019) holds. Verified at line 480-481, 525-528.

### 2.2 Trigger-collision risk (analogous to today's VWAP cascade)

The same threshold-sensitivity hazard that produced today's 11-way VWAP cascade exists in MULTIPLE other layers:

| Layer | Cross-symphony correlation risk | Operational symptom |
|-------|---------------------------------|---------------------|
| Para-ARM (`prev_return=0.0` bug) | **HIGH** — any market-wide morning gap-up >= 2% arms every symphony's parabolic squeeze on tick 1 | Tighter stops fleet-wide on day-1 of a strong-open day |
| VWAP System A | HIGH (confirmed today) | All 11 exits at 10:35 |
| Trailing stop | MEDIUM — depends on each symphony's vol, but a fleet-wide sharp drop hits all armed symphonies' 3-tick confirm window concurrently | Mass exits on a single SPY-correlated down-move |
| Trailing stop + monotonicity-ratchet bug | LOW (with current code) — stops can DROP, which de-correlates the firing window | But: a future fix would re-introduce concurrent-fire risk |
| MC sanity gate (`prob_beating < 60`) | HIGH — kNN regime selection picks SIMILAR historical days for all SPY-correlated symphonies, so MC probabilities are positively correlated across symphonies | When the regime tilts toward a bad-history match, multiple symphonies fail MC-sanity together |
| Take-profit (2-tick confirm + `prob > 5`) | LOW — TP arms when MC is OPTIMISTIC, fleet-wide sentiment shifts can trigger this but only after 2 ticks of recovery, smaller blast radius |

**The fleet-level decorrelation circuit-breaker recommendation from the VWAP audit applies HERE too** — the engine has no fleet-aware logic anywhere.

### 2.3 State persistence across new-day / restart

| Field | Survives daemon restart same day? | Wiped on new day? | Wiped on execution-mode toggle? | Hazard |
|-------|-----------------------------------|--------------------|--------------------------------|--------|
| `triggered` | YES | YES | YES | ✓ |
| `high_water_mark` | YES | YES → -999.0 | YES | ✓ |
| `shadow_hwm` | YES | YES → -999.0 | YES | ✓ |
| `prev_return` | YES | **YES → 0.0** | YES | **BUG (Section 1.3)** |
| `armed`, `tp_armed`, `para_armed`, `breakeven_locked` | YES | YES → False | YES | ✓ |
| `below_stop_count`, `above_tp_count`, `vwap_ticks`, `vwap_bleed_ticks`, `hwm_hold_ticks` | YES | YES → 0 | YES | ✓ |
| `mc_history` | YES | YES → [] | YES | ✓ |
| `triggered_at_*`, `trigger_prices`, `triggered_basket_snapshot` | YES | YES → DELETED | YES | ✓ |
| `current_return`, `current_value`, `current_holdings` | YES | NO (overwritten in data phase) | NO | ✓ — refreshed by data phase |
| `symphony_vol` | YES | NO (recomputed in data phase line 453) | NO | ✓ — refreshed |
| `mc_prob`, `stop_trigger`, `active_stop_distance` | YES | NO | NO | These are AGGREGATED into the dashboard, but stale after restart until first action-phase cycle — acceptable |

**The `prev_return = 0.0` wipe is the only wipe set that introduces a load-bearing semantic difference.** All other wipes are "fresh-start" semantics (counters → 0, flags → False, sentinels → -999.0); `prev_return = 0.0` is "fresh-start as if the position was flat at yesterday's close," which is not how velocity is supposed to be measured.

**Operator-facing impact:** Every Monday morning, the wipe happens on the first cycle after 09:30 ET. The first action-phase cycle (default 10:30 ET) sees `prev_return = 0.0`. Velocity at 10:30 is computed as "since 09:30 ET PLUS overnight gap from Friday close" rolled into one number, ARMing parabolic for any symphony up >= 2% — which is the common case after a weekend with positive macro news.

---

## Section 3 — PARA-ARM-at-open deep-dive (the open question from §4.5 of vwap-audit.md)

### 3.1 Mechanism summary

1. New trading day. First cycle (e.g., 09:31 ET).
2. `bot_state["date"] != current_date_str` → `database.wipe_transient_state(bot_state)` runs.
3. For every symphony: `s_data["prev_return"] = 0.0` (database.py:140).
4. Same first cycle, DATA phase runs (line 398). `bot_state[s_id]['current_return']` is refreshed from Composer's `last_percent_change * 100`. THIS IS *yesterday's close to right now* — i.e., it includes the overnight gap.
5. Cycle ends. `bot_state` saved.
6. Cycles 09:32, 09:33, ..., 10:29 — DATA phase only. No `compute_para_arm_decision` is called (it's in the action phase). **`prev_return` STAYS at 0.0** throughout the pre-action window.
7. Cycle 10:30 (first action-phase cycle). Line 665: `prev_return = bot_state[symphony_id].get("prev_return", current_return)` = 0.0.
8. Line 667-672: `compute_para_arm_decision(current_return=X, prev_return=0.0, para_threshold=2.0, currently_armed=False)`.
9. `velocity = X - 0.0 = X`. If `X >= 2.0` → `should_arm = True`. PARA-ARM fires.

### 3.2 Operator-expected behavior (best-guess from docs + tests + autotuner ranges)

The PARABOLIC_VELOCITY_THRESHOLD's semantic intent — per the autotuner sweep range (1.0 to 4.0 pp) and the in-engine name "VELOCITY" — is **intra-cycle move**: "the position moved at least N pp in the last MINUTE." A 2% intra-minute move is genuinely parabolic; a 2% overnight gap is just an opening move.

### 3.3 What actually happens in each scenario

| Scenario | Expected | Actual | Why |
|----------|----------|--------|-----|
| Day-1 cycle 1: position opens at +5%, no further move | should not PARA-ARM | **PARA-ARMS** (velocity=5.0 ≥ 2.0) | prev_return=0.0 bug |
| Day-1 cycle 1: position opens at +0.5%, no further move | should not PARA-ARM | does not arm (velocity=0.5 < 2.0) | ✓ coincidentally |
| Day-1 cycle 2: position moves from +5% to +5.1% | should not PARA-ARM (already armed from cycle 1; latch holds — but this is a no-op) | does not arm (currently_armed=True suppresses) | ✓ |
| Day-1 cycle 2: position moves from +0.5% to +3% in one minute | should PARA-ARM (intra-cycle velocity 2.5%) | **ARMS CORRECTLY** (velocity=3.0-0.5=2.5 ≥ 2.0) | ✓ |
| Day-N cycle K: position dropped to -2%, recovers to +0.5% from prior cycle's -2% in one minute (intra-cycle velocity 2.5%) | should PARA-ARM | **ARMS CORRECTLY** (velocity=2.5 ≥ 2.0) | ✓ |

**Conclusion:** the bug affects ONLY day-1 first cycle. For every subsequent cycle of every day, the math is correct. But the day-1 effect is severe and reproducible.

### 3.4 Today's evidence (cross-referencing VWAP audit Section 1.2)

> All 11 PARA-ARMED at 10:30 cycle (velocity ≥ 2% over 1 cycle — strong open ratchet)

Cross-check: yesterday's post_mortem_2026-05-14.json (per VWAP audit Section 1.4) showed all 11 symphonies triggered yesterday. Today (2026-05-15) the wipe ran on the first cycle, zeroing prev_return for all 11. By the 10:30 cycle, all 11 had `current_return >= 2%` (the open was strong fleet-wide), so all 11 PARA-ARMED on the FIRST action-phase cycle. **Confirmed.**

### 3.5 Cascading effects of incorrect PARA-ARM

PARA-ARM tightens the trailing-stop via `active_trailing_stop *= MAX_PARABOLIC_SQUEEZE` (line 146, default 0.50). So:

- Active stop distance is HALVED on day-1 cycle 1 for every symphony that opened ≥ 2%.
- Half-distance trailing stop is much closer to current return.
- A modest mean-reversion (the open-spike-fade pattern) crosses the tighter stop faster.
- The 3-tick confirmation completes within ~3 minutes.
- This contributes to (but is not the sole cause of) the today's VWAP-cascade pattern — System A was the actual trigger today, not trailing-stop, but System A's gate (HWM ≥ 1%) was crossed faster *because* PARA-ARM had tightened the stop, raising `safe_hwm`'s ratchet speed indirectly via early stops on prior cycles.

Note: the direct VWAP System A path does NOT go through `active_trailing_stop`; it goes through `safe_hwm` and the `weighted_vwap_diff` gate. So the today's primary causation is the VWAP calibration, not the PARA-ARM bug. But the PARA-ARM bug COULD trip trailing-stop on day-1 fleet-wide in a different market scenario (strong-open + late-day fade).

### 3.6 Three operator-options for fixing

**(NOT recommending; presenting trade-offs.)**

1. **Suppress PARA-ARM on day-1 first cycle.** Add a "first-cycle-of-day" sentinel and skip arming when it's True. Cost: adds state, requires migration. Benefit: cleanest semantic.
2. **Initialize `prev_return = current_return` instead of 0.0 in wipe.** Cost: requires both `database.wipe_transient_state` and tests/database/test_wipe_state.py to change. Effect: velocity at first cycle is always 0 → first cycle never arms. Benefit: minimal code change; matches autotuner's likely intent.
3. **Re-tune PARABOLIC_VELOCITY_THRESHOLD assuming the bug is now-default behavior.** Cost: an Optuna sweep with the bug-as-intent baked into the simulator already. The current Optuna-tuned thresholds are effectively this — but operator may not have realized.

The cleanest is option 2. It costs 1 line of code, 1 test update.

---

## Section 4 — FIX recommendations (no implementation; ranked by severity)

### 4.1 [HIGH] Fix `prev_return = 0.0` wipe (Section 3)

`database.py:140`. Three options enumerated in §3.6. **Operator decision required** — semantic intent of "velocity" needs to be re-stated explicitly. Until then, every Monday's first action-phase cycle will continue to PARA-ARM most of the fleet for the wrong reason.

### 4.2 [HIGH] Thread `previously_persisted_stop_level` from caller (Section 1.5)

`alpha_bot_execution.py:698-705`. Add `previously_persisted_stop_level=bot_state[symphony_id].get("stop_trigger")` to the call. The math layer is ready (tested by `test_stop_monotonicity.py`); the engine is not adopting it. Without this fix, trailing stops can drop tick-to-tick, violating the canonical Fu & Zhang 2010 invariant the math layer has already extracted.

### 4.3 [MEDIUM] Document concurrent-trigger semantics

Add to a runbook (and a docstring on the `execution_queue` builder loop): "When multiple trigger conditions fire simultaneously, the engine exits ONCE with the highest-priority reason; all counters advance; all event logs emit." This is current behavior — but the operator may not know it.

### 4.4 [MEDIUM] Audit the `-999.0` sentinel for naming clarity

Three semantic roles (wiped HWM, post-trigger HWM, no-tracked-stop), one value. Recommend either (a) split into three distinct constants `HWM_WIPED_SENTINEL`, `HWM_POST_TRIGGER_SENTINEL`, `STOP_LEVEL_UNAVAILABLE_SENTINEL` even if all three are `-999.0` at runtime, OR (b) document the multi-role usage in a runbook so future authors understand the contract.

### 4.5 [MEDIUM] Add explicit MC RNG seeding policy

In `run_monte_carlo`, either (a) accept a `seed` kwarg and seed locally (deterministic per-cycle) or (b) document explicitly that the function consumes global numpy state and is non-deterministic between runs. Today this is implicit. The autotuner depends on it for reproducible scoring but does not seed.

### 4.6 [LOW] Document allocation-coverage dilution hazard for volatility

A holding completely missing from `historical_data` gets SPY-substituted. A holding listed but with partial coverage gets a diluted vol contribution. The `valid_vwap_weight > 0.5` gate in VWAP is the closest existing defense. Vol and ATR have no such gate. Document in a runbook.

### 4.7 [LOW] Add fleet-decorrelation circuit-breaker (from VWAP audit)

When N >= 5 symphonies all signal `is_vwap_broken` OR `is_trailing_stop_hit` in the same cycle, defer execution by 1-2 cycles. Cross-cuts every exit layer; needs design.

---

## Section 5 — Test coverage gaps (RED tests that should exist)

These are tests that would have caught the bugs flagged above:

1. **`tests/execution/test_day_one_first_cycle_para_arm.py`** — seed bot_state with `prev_return=0.0` (matches wipe state) and `current_return=2.5`, run the action-phase, assert PARA-ARMED is NOT fired (assumes operator picks option 1 or 2 from §3.6). **Would fail RED today.**

2. **`tests/execution/test_stop_monotonicity_threaded_caller.py`** — end-to-end test that drives `main()` across 7 cycles where `base_stop_level` retraces post-lock. Assert `bot_state[sym]['stop_trigger']` is monotone non-decreasing across cycles. **Would fail RED today** because the caller doesn't thread `previously_persisted_stop_level`.

3. **`tests/execution/test_simultaneous_trigger_priority.py`** — synthetic state where all 4 trigger conditions fire on the same cycle. Assert (a) only one Discord embed is sent, (b) the reason is the highest-priority one, (c) `triggered=True` is set exactly once, (d) `bot_state[sym]['triggered_reason']` matches the embed's reason. Pins today's behavior so it doesn't drift.

4. **`tests/execution/test_eleven_symphony_fleet_cascade.py`** (carry-over from VWAP audit §3.2) — 11 synthetic symphonies, all opening up ≥ 2%, replayed through the first few action-phase cycles. Assert ALL 11 PARA-ARM today (current bug) → after fix, NONE PARA-ARM unless intra-cycle velocity is actually parabolic.

5. **`tests/math_engine/test_mc_rng_determinism.py`** — set `np.random.seed(42)` before two consecutive calls to `run_monte_carlo` with identical inputs; assert outputs differ (because state is consumed) AND that seeding before each call yields IDENTICAL outputs. Pins the RNG contract documented in §4.5.

6. **`tests/math_engine/test_hwm_sentinel_translation_invariant.py`** — property test: for every code path that reads `high_water_mark`, assert it either (a) reads via `safe_hwm` translation OR (b) is the wipe/init path. AST-walk style.

7. **`tests/database/test_wipe_state_prev_return_semantics.py`** — document-the-bug-test that pins the current `prev_return=0.0` behavior so any fix to it is a deliberate decision, not an accidental code drift. (Currently `tests/database/test_wipe_state.py:147` asserts this — but as a "must be 0.0" pin, not as a "this is the day-1 first-cycle interaction" pin.)

---

## Section 6 — Open questions (unverifiable read-only)

1. **Was the `previously_persisted_stop_level` kwarg added with intent to thread it, but the caller-side commit was never made?** Git log on the math_engine.py would clarify. The kwarg's docstring (lines 184-192) presents it as a working contract, suggesting yes — but the caller is unaware. Speculation.

2. **Did the autotuner's Optuna sweep validate against bug-baked-in simulator?** Yes by inspection of `autotuner.py:94`: `prev_return = 0.0` at simulated-day start, same bug. Confirmed read-only. But: were the tuned thresholds picked BECAUSE the day-1 PARA-ARM effect was rewarded by the alpha-scoring function, or DESPITE it? Cannot tell without running an autotuner experiment with the fix applied.

3. **Was `shadow_hwm` originally intended to be the canonical HWM and `high_water_mark` a deprecated alias, or vice versa?** Three HWM fields with overlapping semantics suggests evolutionary accretion. Reading the git log would clarify.

4. **Today's 11-symphony VWAP cascade: would the trailing-stop have fired anyway if VWAP had been deferred?** Cannot determine read-only — requires simulating tomorrow's prices against today's stop levels.

5. **Are there hidden non-deterministic side effects from the MC global-RNG-state pattern that affect arming/disarming decisions?** Adjacent symphonies' MC computations share RNG state advances; theoretically a sort-order quirk in `np.searchsorted` at the same boundary across symphonies could produce systematic bias. Unlikely at 5000 paths but unverifiable without empirical test.

6. **Does the `np.argpartition` branch (line 493) at `len(distances) > neighbor_k` produce the same nearest-K set as a full sort?** `argpartition` returns an UNORDERED partition — the K returned indices are the K smallest, but in arbitrary order within. Downstream consumption (line 495: `nearest_days = [valid_dates[i] for i in nearest_indices]`) does not depend on ordering, so this is fine. But if any future change starts treating `nearest_indices` as ordered, the result would silently drift. Worth a docstring note.

---

## Files referenced (absolute paths)

- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\math_engine.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\database.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\autotuner.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\analytics.py` (HWM consumers)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\app.py` (dashboard HWM sort)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\reporting.py` (Discord HWM rendering)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_volatility_scaling.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_atr.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_time_squeeze_decay.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_parabolic_squeeze.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_active_trailing_stop.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_breakeven_update.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_exit_confirmation.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_vwap_signals.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_vwap_bleed_arm.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_vwap_breakdown.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_mc_gating_magic_numbers.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_mc_fallbacks.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_stop_monotonicity.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_nan_policy.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_nan_policy_list_inputs.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\database\test_wipe_state.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\dashboard\vwap-audit.md` (reference)
