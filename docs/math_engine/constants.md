# math_engine.py — Constant Provenance Reference

**Purpose:** Documents the origin, rationale, and configuration status of every named
constant in `math_engine.py`. This document is the canonical reference for task #17
(constant-provenance archaeology), filed after the PM overrode a cycle-6 reviewer BLOCK
on the grounds that historical provenance is a separate research scope, not a
refactor-cycle gate.

**Methodology:** Git log archaeology (`git log -p --all`), inline value tracing in the
original `alpha_bot_execution.py` at commit `6c4bad6` ("Add files via upload", 2026-05-09),
Optuna parameter search (autotuner.py `trial.suggest_*` calls), and env-var grep of
`alpha_bot_execution.py`. Standard finance literature cited only where a standard exists
and the codebase period matches it.

**Honesty policy:** "Origin not documented" is a valid, non-stigmatized classification.
The cycle-6 override was specifically against fabricating "walk-forward calibrated"
rationale. Where evidence is absent, this document says so.

**Extraction cycle history:**
- Cycle 1 (vol scaling) — commit `1310a3d`, 2026-05-12
- Cycle 2 (ATR) — commit `23a8fce`, 2026-05-12
- Cycle 4 (log time decay) — commit `e7c3d75`, 2026-05-12
- Cycle 5 (active stop) — commit `31c5062`, 2026-05-12
- Cycle 6 (breakeven) — commit `a0e80de`, 2026-05-12
- Cycle 7 (exit confirmation) — commit `84d5cd2`, 2026-05-12
- Cycle 8 (VWAP signals) — commit `70b62e1`, 2026-05-12 (no new constants)
- Cycle 9 (VWAP bleed arm) — commit `8db8030`, 2026-05-12
- Cycle 10 (VWAP breakdown) — commit `5b90d47`, 2026-05-12
- Cycle 11 (MC gating) — commit `d5e72cd`, 2026-05-13
- Cycle 12 (trailing-stop monotonicity invariant) — 2026-05-13 (no new numeric constants; structural invariant only)

---

## Cycle 1 — Volatility Scaling

### LOOKBACK_DAYS = 20

**Used in:** `calculate_20d_vol` (cycle 1, math_engine.py)
**Introduced:** `1310a3dbdfdacc3500b3b726fc5497e08002a34a` 2026-05-12 — `refactor(math_engine): extract magic numbers in calculate_20d_vol (GREEN)`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): `valid_dates[-20:]` (line 75) and `< 20` guard (line 76). The function `calculate_20d_vol` existed inline with the literal `20` at both sites before constant extraction.
**Origin classification:** Standard finance period
**Rationale:** 20-day realized volatility is the dominant practitioner convention for short-horizon risk sizing; it corresponds to approximately one calendar month of trading days and is the standard window used in VIX-adjacent risk models and momentum-strategy volatility normalization. The inline comment added at extraction time reads "20-day realized-volatility window — AlphaBot risk-sizing standard," confirming intentional alignment with this convention rather than an Optuna-tuned value. This constant is not in the Optuna search space (autotuner.py exposes no `LOOKBACK_DAYS` parameter).
**Config-overridable:** No. Not exposed via `os.getenv` in `alpha_bot_execution.py` and not in the Optuna trial parameter set.
**Related code references:**
- `math_engine.py` (current): `calculate_20d_vol` lines 435–436
- `alpha_bot_execution.py`: `math_engine.calculate_20d_vol` call at line 523
- `autotuner.py`: `math_engine.calculate_20d_vol` called at line 194 (no parameter override)

---

### PCT_SCALAR = 100.0

**Used in:** `calculate_20d_vol`, `calculate_14d_atr_pct`, `run_monte_carlo` (cycles 1, 2, and 11 — math_engine.py)
**Introduced:** `1310a3dbdfdacc3500b3b726fc5497e08002a34a` 2026-05-12 — `refactor(math_engine): extract magic numbers in calculate_20d_vol (GREEN)`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): `* 100.0` literal at line 93 (`calculate_20d_vol`) and line 140 (`calculate_14d_atr_pct`). The inline `100.0` literals in `run_monte_carlo` were extracted later in cycle 11 (commit `d5e72cd`, 2026-05-13) and now reference the same `PCT_SCALAR` — these are unit-conversion sites distinct from the new `MC_INSUFFICIENT_HISTORY_PROB` sentinel.
**Origin classification:** Standard finance unit conversion
**Rationale:** The math layer normalizes decimal returns (e.g., 0.012) to percentage points (1.2) throughout. This is a pure unit-conversion scalar with no free parameter: decimal × 100 = percent. No alternative value is meaningful. Named to eliminate ambiguity between this conversion and any other scalar multiply.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `calculate_20d_vol` line 453, `calculate_14d_atr_pct` line 500, `run_monte_carlo` lines 375, 393, 428, 435

---

## Cycle 2 — ATR

### ATR_LOOKBACK_DAYS = 15

**Used in:** `calculate_14d_atr_pct` (cycle 2, math_engine.py)
**Introduced:** `23a8fce4e4392dfe43eac0c428af569f22caa690` 2026-05-12 — `refactor(math_engine): extract magic numbers in calculate_14d_atr_pct (GREEN)`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): `valid_dates[-15:]` (line 105) and `< 15` guard (line 106). Both literals were 15 in the pre-extraction file.
**Origin classification:** Standard finance period (with off-by-one explanation)
**Rationale:** The function is named `calculate_14d_atr_pct` and computes the canonical 14-day Average True Range (Wilder, 1978). ATR requires the prior close to compute the first True Range, so a 15-day historical slice yields exactly 14 TR intervals. The constant is 15 (not 14) to account for this lookback-window offset; the extracted constant comment documents this explicitly: "14-day true-range window (standard ATR period) + 1 prior close required to compute the first TR." The 14-day ATR period is the Wilder default and the dominant practitioner standard. This constant is not Optuna-tuned.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `calculate_14d_atr_pct` lines 465–467
- `alpha_bot_execution.py`: `math_engine.calculate_14d_atr_pct` not called directly in the current file (vol sizing uses `calculate_20d_vol` with ATR as fallback path only)

---

## Cycle 4 — Log Time Decay

### DECAY_CURVE_SCALAR = 9

**Used in:** `compute_time_squeeze_decay` (cycle 4, math_engine.py)
**Introduced:** `e7c3d757f473f7a3135dd4bc99496cf5c41b5715` 2026-05-12 — `refactor(math_engine): extract compute_time_squeeze_decay from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 572: `decay_curve = math.log10(1 + 9 * time_ratio)` — the literal `9` was inline with no comment.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The scalar 9 is chosen so that `log10(1 + 9 * t)` maps `t ∈ [0,1]` exactly to `[0,1]`: at t=0, result=0; at t=1, log10(10)=1. This is a mathematical boundary condition, not an arbitrary calibration. The base-10 logarithm with scalar 9 produces a curve that is steep early in the session (stop loosens quickly from open) and flat near the close (stop is already near maximum tightness). The cycle-4 commit message describes it as "AlphaBot intraday decay curve definition." No alternative value was present in git history; this value was present at the initial file upload. Origin before git is not documented; no Optuna study tunes this parameter.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_time_squeeze_decay` line 72
- Pre-extraction: `alpha_bot_execution.py` line 572 (commit `6c4bad6`, removed at `e7c3d75`)

---

### MULT_OPEN = 1.5

**Used in:** `compute_time_squeeze_decay` (cycle 4, math_engine.py)
**Introduced:** `e7c3d757f473f7a3135dd4bc99496cf5c41b5715` 2026-05-12 — `refactor(math_engine): extract compute_time_squeeze_decay from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) lines 574–576: comment "Calculate Dynamic Multiplier (Decays from 1.5x to 0.5x)" with `mult_open = 1.5` as a local variable.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The dynamic_multiplier scales the vol-based trailing stop distance. At open, the stop is widest (1.5× vol) to avoid whipsaws on opening volatility. The cycle-4 commit message labels this "loosest stop at open — operational policy." The paired values 1.5 (open) and 0.5 (close) are asymmetric by design: open is 3× tighter at close than open. No Optuna study tunes this; no env-var exposes it. Origin before the initial git commit is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_time_squeeze_decay` lines 73–74
- Pre-extraction: `alpha_bot_execution.py` lines 575–577 (commit `6c4bad6`)

---

### MULT_CLOSE = 0.5

**Used in:** `compute_time_squeeze_decay` (cycle 4, math_engine.py)
**Introduced:** `e7c3d757f473f7a3135dd4bc99496cf5c41b5715` 2026-05-12 — `refactor(math_engine): extract compute_time_squeeze_decay from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 576: `mult_close = 0.5` as a local variable.
**Origin classification:** Hand-tuned heuristic
**Rationale:** Dynamic multiplier at market close — the stop is tightest (0.5× vol) near 16:00 ET to capture gains before the day ends. Paired with MULT_OPEN = 1.5. Cycle-4 commit: "tightest stop at close — operational policy." No Optuna tuning; no env-var.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_time_squeeze_decay` lines 73–74
- Pre-extraction: `alpha_bot_execution.py` line 576 (commit `6c4bad6`)

---

### MIN_STOP_OPEN = 0.3

**Used in:** `compute_time_squeeze_decay` (cycle 4, math_engine.py)
**Introduced:** `e7c3d757f473f7a3135dd4bc99496cf5c41b5715` 2026-05-12 — `refactor(math_engine): extract compute_time_squeeze_decay from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) lines 579–580: comment "Calculate Minimum Floors (Decays from 0.3% to 0.15%)" with `min_stop_open = 0.3`.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The floor on trailing-stop distance (in percentage points) at market open. Ensures the active stop never tightens below 0.3 pp even if vol is very low, preventing immediate false exits. The comment in the pre-extraction code names the range explicitly (0.3% to 0.15%). Cycle-4 commit: "floor at open in pct pts — operational policy." No Optuna tuning; no env-var. Origin before the initial git upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_time_squeeze_decay` lines 74–75
- Pre-extraction: `alpha_bot_execution.py` line 580 (commit `6c4bad6`)

---

### MIN_STOP_CLOSE = 0.15

**Used in:** `compute_time_squeeze_decay` (cycle 4, math_engine.py)
**Introduced:** `e7c3d757f473f7a3135dd4bc99496cf5c41b5715` 2026-05-12 — `refactor(math_engine): extract compute_time_squeeze_decay from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 581: `min_stop_close = 0.15`.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The floor on trailing-stop distance at market close: 0.15 pp. Half of MIN_STOP_OPEN, consistent with the 2:1 ratio between open and close multipliers. Prevents the stop from being so tight at close that it fires on normal tick noise. Cycle-4 commit: "floor at close in pct pts — operational policy." No Optuna tuning; no env-var.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_time_squeeze_decay` lines 74–75
- Pre-extraction: `alpha_bot_execution.py` line 581 (commit `6c4bad6`)

---

## Cycle 5 — Active Stop

### VOL_FALLBACK = 1.0

**Used in:** `compute_active_trailing_stop` (cycle 5, math_engine.py)
**Introduced:** `31c506235088322b92d0deb41c0740deb57fa32d` 2026-05-12 — `refactor(math_engine): extract compute_active_trailing_stop from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 586: `safe_vol = symphony_vol if symphony_vol > 0 else 1.0` — the literal `1.0` was inline.
**Origin classification:** Hand-tuned heuristic (degenerate-input sentinel)
**Rationale:** When `symphony_vol <= 0` (degenerate case: missing data, first-tick cold start, or zero-variance portfolio), the vol-based trailing-stop arithmetic would produce a zero or negative stop distance. VOL_FALLBACK = 1.0 substitutes a 1 percentage point vol, keeping the arithmetic valid and producing a conservative (non-zero) stop distance. The value 1.0 is a "neutral" mid-range assumption; it is not derived from historical data. Cycle-5 commit: "neutral fallback for safe_vol when symphony_vol <= 0; preserves vol-scale arithmetic in degenerate-vol case." No Optuna tuning; no env-var.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_active_trailing_stop` line 100
- Pre-extraction: `alpha_bot_execution.py` line 586 (commit `6c4bad6`)

---

## Cycle 6 — Breakeven Lock

### BREAKEVEN_ACTIVATION_MIN = 0.4

**Used in:** `compute_breakeven_update` (cycle 6, math_engine.py)
**Introduced:** `a0e80dea8f153039e9cfa129deaf461b779138dc` 2026-05-12 — `refactor(math_engine): extract compute_breakeven_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 595: `dynamic_activation = max(0.4, min(3.0, symphony_vol))` — the literal `0.4` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** Lower clamp for the dynamic breakeven activation threshold. The activation threshold is vol-proportional (clamped to symphony_vol), so this floor prevents the threshold from falling below 0.4 pp on low-vol days. Below 0.4 pp, a position gain is too small to lock breakeven meaningfully. Cycle-6 commit: "lower clamp, operational policy (AlphaBot risk spec)." No git history predates the initial upload; no Optuna study tunes this. Origin before initial upload is not documented.
**Config-overridable:** No. Not exposed via `os.getenv` and not in Optuna trial parameters.
**Related code references:**
- `math_engine.py` (current): `compute_breakeven_update` line 141
- Pre-extraction: `alpha_bot_execution.py` line 595 (commit `6c4bad6`)

---

### BREAKEVEN_ACTIVATION_MAX = 3.0

**Used in:** `compute_breakeven_update` (cycle 6, math_engine.py)
**Introduced:** `a0e80dea8f153039e9cfa129deaf461b779138dc` 2026-05-12 — `refactor(math_engine): extract compute_breakeven_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 595: `dynamic_activation = max(0.4, min(3.0, symphony_vol))` — the literal `3.0` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** Upper clamp for the dynamic breakeven activation threshold. Caps the threshold at 3.0 pp even on high-vol days, preventing the breakeven lock from becoming unreachable during volatile markets. Cycle-6 commit: "upper clamp, operational policy." 3.0 pp is a meaningful intraday gain level where protecting breakeven is operationally desirable. No Optuna tuning; no env-var. Origin before initial upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_breakeven_update` line 141
- Pre-extraction: `alpha_bot_execution.py` line 595 (commit `6c4bad6`)

---

### BREAKEVEN_ACTIVATION_DEADBAND = 0.2

**Used in:** `compute_breakeven_update` (cycle 6, math_engine.py)
**Introduced:** `a0e80dea8f153039e9cfa129deaf461b779138dc` 2026-05-12 — `refactor(math_engine): extract compute_breakeven_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 596: `if current_return >= (dynamic_activation - 0.2):` — the literal `0.2` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The deadband below the dynamic activation threshold within which a tick still counts toward the HWM-hold counter. Without a deadband, the counter would reset every time the return dipped even 0.001 pp below the threshold due to tick noise. The 0.2 pp window provides noise tolerance. Cycle-6 commit: "proximity window, operational policy." No Optuna tuning; no env-var. Origin before initial upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_breakeven_update` line 142
- Pre-extraction: `alpha_bot_execution.py` line 596 (commit `6c4bad6`)

---

### HWM_HOLD_TICKS_THRESHOLD = 5

**Used in:** `compute_breakeven_update` (cycle 6, math_engine.py)
**Introduced:** `a0e80dea8f153039e9cfa129deaf461b779138dc` 2026-05-12 — `refactor(math_engine): extract compute_breakeven_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 601: `if bot_state[symphony_id]["hwm_hold_ticks"] >= 5:` — the literal `5` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The number of consecutive qualifying ticks (each 1 minute) required to trigger the one-way breakeven lock transition. Five minutes of sustained near-threshold returns is interpreted as "this gain is real, not transient." The comment in the pre-extraction code at line 601 had no explanation for the choice of 5. Cycle-6 commit: "consecutive-tick gate, operational policy." No Optuna study tunes this value. Origin before initial upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_breakeven_update` line 146
- Pre-extraction: `alpha_bot_execution.py` line 601 (commit `6c4bad6`)

---

### TRIGGERED_OVERRIDE_LEVEL = -999.0

**Used in:** `compute_breakeven_update` (cycle 6, math_engine.py)
**Introduced:** `a0e80dea8f153039e9cfa129deaf461b779138dc` 2026-05-12 — `refactor(math_engine): extract compute_breakeven_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 606: `stop_trigger_level = -999.0` — the literal `-999.0` was inline.
**Origin classification:** Sentinel value
**Rationale:** When a position is already triggered (exited), `stop_trigger_level` is forced to -999.0 — a value so far below any real return that the exit-confirmation condition (`current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT`) can never fire, suppressing re-exit attempts on an already-closed position. The value -999 is a conventional "impossible" sentinel in financial return space where normal values are bounded by ±100%. Cycle-6 commit: "sentinel, operational policy (suppresses re-exit)." This is a protocol sentinel, not a tunable parameter.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_breakeven_update` line 152
- Pre-extraction: `alpha_bot_execution.py` line 606 (commit `6c4bad6`)
- Also appears in `alpha_bot_execution.py` as the guard in `safe_hwm` computation: `high_water_mark if high_water_mark != -999.0 else current_return` (checks against the same sentinel value)

---

## Cycle 7 — Exit Confirmation

### MAGNITUDE_FLOOR_PCT = 0.10

**Used in:** `compute_exit_confirmation` (cycle 7, math_engine.py)
**Introduced:** `84d5cd233c255d50c5c29488d187a16a0c097263` 2026-05-12 — `refactor(math_engine): extract compute_exit_confirmation from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 616: `if current_return <= (stop_trigger_level - 0.10) and prob_beating < 60.0:` — the literal `0.10` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The trailing stop fires only when the return is at least 0.10 pp below the stop trigger level (not merely at or just below). This buffer absorbs MC noise and single-tick price spikes that would otherwise cause premature exits at exactly the stop boundary. Cycle-7 commit: "return must fall at least this far below stop_trigger_level to count toward confirmation; buffers MC noise / single-tick spikes (AlphaBot operational policy, extracted from inline)." No Optuna tuning; no env-var. Origin before initial upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_exit_confirmation` line 199
- Pre-extraction: `alpha_bot_execution.py` line 616 (commit `6c4bad6`)

---

### MC_SANITY_THRESHOLD = 60.0

**Used in:** `compute_exit_confirmation` (cycle 7, math_engine.py)
**Introduced:** `84d5cd233c255d50c5c29488d187a16a0c097263` 2026-05-12 — `refactor(math_engine): extract compute_exit_confirmation from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 616: `... and prob_beating < 60.0` — the literal `60.0` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** When the Monte Carlo engine estimates >= 60% probability that the portfolio will beat the benchmark, an exit signal is suppressed ("if we still think we beat the benchmark, don't capitulate"). The threshold is asymmetric: it does not need to reach 50% (random) to suppress exit; the 60% threshold requires a meaningful MC advantage before blocking the stop. Cycle-7 commit: "MC probability at or above which exit is suppressed; 'if we still think we beat the benchmark, don't capitulate' (AlphaBot operational policy, extracted from inline)." No Optuna tuning (Optuna tunes `TAKE_PROFIT_MC_PCT` and `TRIGGER_THRESHOLD_PCT`, not this gate). No env-var. Origin before initial upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_exit_confirmation` line 200
- Pre-extraction: `alpha_bot_execution.py` line 616 (commit `6c4bad6`)

---

### EXIT_CONFIRM_TICKS = 3

**Used in:** `compute_exit_confirmation` (cycle 7, math_engine.py)
**Introduced:** `84d5cd233c255d50c5c29488d187a16a0c097263` 2026-05-12 — `refactor(math_engine): extract compute_exit_confirmation from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 620: `elif bot_state[symphony_id]["below_stop_count"] >= 3:` — the literal `3` was inline. The comment at line 619 read "Hardcoded exit threshold: 3 consecutive ticks."
**Origin classification:** Hand-tuned heuristic
**Rationale:** Three consecutive qualifying minutes (ticks) below the stop trigger level are required before `is_trailing_stop_hit` flips True. The pre-extraction code even self-labelled this as "Hardcoded exit threshold: 3 consecutive ticks," signalling awareness that the value was not derived from an optimization. Three ticks (three minutes) is long enough to confirm a genuine breach, short enough to exit before a gap-down accelerates. Cycle-7 commit: "consecutive qualifying ticks required to flip is_trailing_stop_hit (AlphaBot operational policy, extracted from inline)." No Optuna tuning; no env-var.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_exit_confirmation` line 204
- Pre-extraction: `alpha_bot_execution.py` lines 619–620 (commit `6c4bad6`)

---

## Cycle 9 — VWAP Bleed Arm

### VWAP_BLEED_ARM_MIN = -3.0

**Used in:** `compute_vwap_bleed_arm_threshold` (cycle 9, math_engine.py)
**Introduced:** `8db80305d191c9c367f724450b39e890e3a90248` 2026-05-12 — `refactor(math_engine): extract compute_vwap_bleed_arm_threshold from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 532: `acc_VWAP_BLEED_ARM_PCT = max(-3.0, min(-0.5, raw_dynamic_bleed))` — the literal `-3.0` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The most-negative clamp for the VWAP-bleed arm threshold (in percentage points; always negative). When the computed dynamic threshold would fall below -3.0 pp (high vol × high bleed multiplier), it is clamped at -3.0, preventing the bleed counter from requiring an unrealistically deep drop to arm. The cycle-9 commit documents the clamp semantics but does not explain why -3.0 specifically was chosen over -2.5 or -4.0. Origin before initial upload is not documented; this value was not Optuna-tuned (Optuna tunes `VWAP_BLEED_MULTIPLIER` and `VWAP_BLEED_TICKS` but not the arm clamps).
**Config-overridable:** No. The arm clamps are not in `os.getenv` or Optuna trial parameters.
**Related code references:**
- `math_engine.py` (current): `compute_vwap_bleed_arm_threshold` line 278
- Pre-extraction: `alpha_bot_execution.py` line 532 (commit `6c4bad6`)

---

### VWAP_BLEED_ARM_MAX = -0.5

**Used in:** `compute_vwap_bleed_arm_threshold` (cycle 9, math_engine.py)
**Introduced:** `8db80305d191c9c367f724450b39e890e3a90248` 2026-05-12 — `refactor(math_engine): extract compute_vwap_bleed_arm_threshold from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 532: `acc_VWAP_BLEED_ARM_PCT = max(-3.0, min(-0.5, raw_dynamic_bleed))` — the literal `-0.5` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The least-negative clamp for the VWAP-bleed arm threshold. Even on very low-vol days, the portfolio must drop at least -0.5 pp below VWAP before the bleed counter arms. This prevents the bleed system from firing on trivial intraday noise. The cycle-9 commit describes it as "least-negative clamp; arm threshold must be at least this deep (shallower drops never arm)." Why -0.5 specifically was not documented; origin before initial upload is unknown. Not Optuna-tuned.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_vwap_bleed_arm_threshold` line 278
- Pre-extraction: `alpha_bot_execution.py` line 532 (commit `6c4bad6`)

---

## Cycle 10 — VWAP Breakdown

### VWAP_WEIGHT_THRESHOLD = 0.5

**Used in:** `compute_vwap_breakdown_update` (cycle 10, math_engine.py)
**Introduced:** `5b90d478ae643700bf705fb4ba3d9ea6bec79528` 2026-05-12 — `refactor(math_engine): extract compute_vwap_breakdown_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 659: `if valid_vwap_weight > 0.5 and weighted_vwap_diff < 0:` — the literal `0.5` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** Minimum total allocation weight that must have valid VWAP data before the weighted VWAP signal is evaluated. If less than 50% of the portfolio (by allocation weight) has live VWAP prices, the signal is considered too sparse to be reliable and both VWAP exit systems are bypassed. The 0.5 threshold represents a majority-coverage requirement. Cycle-10 commit: "minimum allocation coverage for reliable weighted diff signal." The boundary is a strict `>` (exactly 0.5 does NOT pass), which is pinned by a golden fixture. Not Optuna-tuned; no env-var. Origin before initial upload is not documented.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `compute_vwap_breakdown_update` line 341
- Pre-extraction: `alpha_bot_execution.py` line 659 (commit `6c4bad6`)

---

### VWAP_BREAK_CONFIRM_TICKS = 3

**Used in:** `compute_vwap_breakdown_update` (cycle 10, math_engine.py)
**Introduced:** `5b90d478ae643700bf705fb4ba3d9ea6bec79528` 2026-05-12 — `refactor(math_engine): extract compute_vwap_breakdown_update from alpha_bot_execution.py (GREEN)`
**Pre-extraction location:** `alpha_bot_execution.py` (original upload `6c4bad6`) line 665: `if bot_state[symphony_id]['vwap_ticks'] >= 3:` — the literal `3` was inline.
**Origin classification:** Hand-tuned heuristic
**Rationale:** Three consecutive qualifying ticks (minutes) where the portfolio return is below its high-water mark while the VWAP signal is negative are required before System A (profit-protection VWAP break) signals an exit. Matches the confirmation philosophy of EXIT_CONFIRM_TICKS = 3. Cycle-10 commit: "consecutive qualifying ticks for System A profit-protection break." Note: System B (VWAP bleed ticks) uses a different, account-tunable threshold (`VWAP_BLEED_TICKS`, Optuna-tuned, intentionally NOT promoted to a module constant per cycle-10 commit: "VWAP_BLEED_TICKS intentionally NOT promoted to module constant — it is account-tunable and lives as a function parameter"). Origin of the value 3 before initial upload is not documented.
**Config-overridable:** No (for System A). System B's equivalent is `VWAP_BLEED_TICKS` in `acc_params`, which IS Optuna-tuned.
**Related code references:**
- `math_engine.py` (current): `compute_vwap_breakdown_update` line 347
- Pre-extraction: `alpha_bot_execution.py` line 665 (commit `6c4bad6`)
- Contrast with Optuna-tuned `VWAP_BLEED_TICKS`: `autotuner.py` line 298 `trial.suggest_int("VWAP_BLEED_TICKS", 3, 30)`

---

## Cycle 11 — MC Gating

### MC_INSUFFICIENT_HISTORY_PROB = 100.0

**Used in:** `run_monte_carlo` (cycle 11, math_engine.py)
**Introduced:** `d5e72cd` 2026-05-13 — `feat(math_engine): extract MC-gating magic numbers to named constants`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): literal `100.0` returned from the early-exit branch when insufficient history is available for MC simulation.
**Origin classification:** Sentinel value
**Rationale:** When the MC simulation cannot run (fewer than `MC_MIN_HISTORY_DAYS` rows), the function returns 100.0% probability — meaning "we are certain to beat the benchmark" — which ensures the MC exit gate is never armed due to a data absence. This is semantically distinct from `PCT_SCALAR = 100.0`: `PCT_SCALAR` is a unit-conversion multiplier (decimal → percent); `MC_INSUFFICIENT_HISTORY_PROB` is a protocol sentinel that carries deliberate exit-suppression semantics. The commit `d5e72cd` explicitly documents this distinction as a named precedent for future shared-value/distinct-role constants. Not Optuna-tuned; not env-var configurable.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `run_monte_carlo` early-exit branch

---

### MC_MIN_HISTORY_DAYS = 20

**Used in:** `run_monte_carlo` (cycle 11, math_engine.py)
**Introduced:** `d5e72cd` 2026-05-13 — `feat(math_engine): extract MC-gating magic numbers to named constants`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): guard literal `20` checking minimum row count before running MC simulation.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The minimum number of historical daily rows required for the MC simulation to produce statistically meaningful paths. Below 20 rows the nearest-neighbor regime filter has too few candidates for stable percentile estimates. 20 days corresponds to approximately one calendar month of trading history — the same convention as `LOOKBACK_DAYS` (20-day realized vol). Whether this value was independently chosen or deliberately mirrors `LOOKBACK_DAYS` is not documented; origin before the initial upload is unknown. Not Optuna-tuned.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `run_monte_carlo` history-guard

---

### MC_VOL_WINDOW_DAYS = 20

**Used in:** `run_monte_carlo` (cycle 11, math_engine.py)
**Introduced:** `d5e72cd` 2026-05-13 — `feat(math_engine): extract MC-gating magic numbers to named constants`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): literal `20` in the rolling SPY volatility window computation inside `run_monte_carlo`. Use sites apply `(MC_VOL_WINDOW_DAYS - 1)` arithmetic for inclusive-endpoint rolling windows, as documented in the inline constant comment.
**Origin classification:** Standard finance period
**Rationale:** 20-day rolling volatility window for the SPY regime-conditioning step inside MC simulation. Consistent with `LOOKBACK_DAYS = 20` (portfolio vol) — both use the 20-day practitioner convention. The `-1` arithmetic at use sites is an inclusive-endpoint offset. Not Optuna-tuned.
**Config-overridable:** No.
**Related code references:**
- `math_engine.py` (current): `run_monte_carlo` SPY regime filter

---

### MC_DEFAULT_SIMULATION_PATHS = 5000

**Used in:** `run_monte_carlo` (cycle 11, math_engine.py)
**Introduced:** `d5e72cd` 2026-05-13 — `feat(math_engine): extract MC-gating magic numbers to named constants`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): literal `5000` as the default number of simulation paths.
**Origin classification:** Hand-tuned heuristic
**Rationale:** Number of vectorized Monte Carlo simulation paths. 5000 paths gives CLT-stable percentile estimates (standard error on a 10th-percentile estimate ≈ 0.13%) at acceptable runtime on a single Python process. Inline comment at extraction: "CLT stability vs runtime tradeoff." No Optuna study tunes this; it is a runtime-performance parameter, not a risk parameter. Origin before initial upload is not documented.
**Config-overridable:** No (default; callers may pass an override but none currently do).
**Related code references:**
- `math_engine.py` (current): `run_monte_carlo` signature default

---

### MC_DEFAULT_NEIGHBOR_K = 150

**Used in:** `run_monte_carlo` (cycle 11, math_engine.py)
**Introduced:** `d5e72cd` 2026-05-13 — `feat(math_engine): extract MC-gating magic numbers to named constants`
**Pre-extraction location:** `math_engine.py` (original upload `6c4bad6`): literal `150` as the default kNN neighbor count for regime-locality filtering.
**Origin classification:** Hand-tuned heuristic
**Rationale:** The kNN filter selects the 150 historical days most similar to today's SPY regime (by return and rolling vol) as the sampling pool for simulation paths. Smaller k means tighter regime match (less data, higher variance); larger k means smoother estimates (more data, less specificity). 150 days is approximately 7 months of trading history — a pragmatic balance. Inline comment at extraction: "smaller=tighter regime match, larger=smoother estimate." No Optuna study tunes this. Origin before initial upload is not documented.
**Config-overridable:** No (default; callers may pass an override but none currently do).
**Related code references:**
- `math_engine.py` (current): `run_monte_carlo` signature default

---

## Cycle 12 — Trailing-Stop Monotonicity Invariant

**No new numeric constants were introduced in this cycle.** Cycle 12 enforces a structural invariant inside `compute_active_trailing_stop` via a new optional kwarg rather than a named constant.

### `previously_persisted_stop_level` kwarg (structural invariant)

**Used in:** `compute_active_trailing_stop` (cycle 12, math_engine.py)
**Introduced:** 2026-05-13 — verification arc remediation cycle A2 (trailing-stop monotonicity)
**Nature:** Optional keyword argument (`float | None`, default `None`). When provided, `compute_active_trailing_stop` clamps its output so the returned stop level is never lower than `previously_persisted_stop_level`. This is a one-way ratchet: the stop can only move up or stay flat between ticks.
**Invariant source:** Fu & Zhang canonical trailing-stop formulation. A trailing stop that can retreat to a lower level than previously persisted violates the monotonicity property that makes a trailing stop meaningful as a risk control. Without this clamp, a sequence of declining vol readings could silently lower an already-armed stop, creating a real-money exposure gap.
**Why at the math layer, not the caller:** The monotonicity invariant is a correctness property of the computation itself, not a caller-side policy. Placing the clamp inside `compute_active_trailing_stop` ensures every future caller gets the correct behavior by default; no caller can accidentally omit it.
**Config-overridable:** No. The invariant is unconditional when `previously_persisted_stop_level` is supplied. Callers that genuinely need to reset (e.g., new position, post-execution cold start) pass `None`, which disables the clamp for that tick only.
**Related code references:**
- `math_engine.py` (current): `compute_active_trailing_stop` function signature and clamp site
- `alpha_bot_execution.py`: call site passes the DB-persisted stop level on each tick; passes `None` on first tick after position open

---

## Coverage Gaps

The following constants have confirmed inline origins (git-traceable to the initial upload
commit `6c4bad6`, 2026-05-09) but no documentation of pre-upload provenance. In every
case the value was present as an unnamed literal in `alpha_bot_execution.py` at the time
of the first git commit. Git history does not extend further. No issue tracker, design
document, or comment in the original code explains the choice of these specific values:

| Constant | Value | Classification | Gap |
|---|---|---|---|
| DECAY_CURVE_SCALAR | 9 | Hand-tuned heuristic | Why 9 and not 8 or 10 — the mathematical boundary property (log10(10)=1) is documented but whether this drove the original design choice is unknown |
| MULT_OPEN | 1.5 | Hand-tuned heuristic | No design document for the 1.5x open-stop width |
| MULT_CLOSE | 0.5 | Hand-tuned heuristic | No design document for the 0.5x close-stop width |
| MIN_STOP_OPEN | 0.3 | Hand-tuned heuristic | No calibration record |
| MIN_STOP_CLOSE | 0.15 | Hand-tuned heuristic | No calibration record |
| VOL_FALLBACK | 1.0 | Sentinel / degenerate-input | 1.0 pp is a reasonable neutral vol but no formal justification exists |
| BREAKEVEN_ACTIVATION_MIN | 0.4 | Hand-tuned heuristic | No calibration record |
| BREAKEVEN_ACTIVATION_MAX | 3.0 | Hand-tuned heuristic | No calibration record |
| BREAKEVEN_ACTIVATION_DEADBAND | 0.2 | Hand-tuned heuristic | No calibration record |
| HWM_HOLD_TICKS_THRESHOLD | 5 | Hand-tuned heuristic | No calibration record |
| MAGNITUDE_FLOOR_PCT | 0.10 | Hand-tuned heuristic | No calibration record |
| MC_SANITY_THRESHOLD | 60.0 | Hand-tuned heuristic | No design document explaining 60% vs alternatives |
| EXIT_CONFIRM_TICKS | 3 | Hand-tuned heuristic | Pre-extraction code self-labelled "Hardcoded" — no design record |
| VWAP_BLEED_ARM_MIN | -3.0 | Hand-tuned heuristic | No calibration record |
| VWAP_BLEED_ARM_MAX | -0.5 | Hand-tuned heuristic | No calibration record |
| VWAP_WEIGHT_THRESHOLD | 0.5 | Hand-tuned heuristic | 50% majority-coverage rule is intuitive but not formally documented |
| VWAP_BREAK_CONFIRM_TICKS | 3 | Hand-tuned heuristic | No calibration record; mirrors EXIT_CONFIRM_TICKS by coincidence or design — unknown |

**Constants with confident, documented origin (no gap):**
- LOOKBACK_DAYS = 20 — standard finance period (20-day realized vol convention)
- PCT_SCALAR = 100.0 — unit conversion (decimal to percent; no alternative possible)
- ATR_LOOKBACK_DAYS = 15 — standard finance period (Wilder 14-day ATR + 1 prior close)
- TRIGGERED_OVERRIDE_LEVEL = -999.0 — sentinel value with clear functional specification
- MC_INSUFFICIENT_HISTORY_PROB = 100.0 — sentinel value; exit-suppression semantics explicitly distinguished from PCT_SCALAR in commit `d5e72cd` (2026-05-13)
- MC_MIN_HISTORY_DAYS = 20 — hand-tuned heuristic; provenance documented at extraction time in commit `d5e72cd`
- MC_VOL_WINDOW_DAYS = 20 — standard finance period (20-day vol convention, consistent with LOOKBACK_DAYS); documented in commit `d5e72cd`
- MC_DEFAULT_SIMULATION_PATHS = 5000 — hand-tuned heuristic; CLT-stability rationale documented in commit `d5e72cd`
- MC_DEFAULT_NEIGHBOR_K = 150 — hand-tuned heuristic; kNN locality rationale documented in commit `d5e72cd`

**Coverage summary: 9 of 27 named constants have confident documented origin; 18 have confirmed
inline pre-extraction location but no pre-upload design rationale. The 5 MC-gating constants
(Cycle 11, introduced 2026-05-13) were added directly as named constants with full provenance
documented at introduction — they carry no pre-upload gap. Cycle 12 introduced no named
constants; its contribution is a structural kwarg invariant documented above.**
