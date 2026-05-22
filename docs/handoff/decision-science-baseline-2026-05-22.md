# AlphaBot v3 — Exit-Decision Math Baseline (Factual)

**Date:** 2026-05-22
**Scope:** READ-ONLY factual account of the *current* exit-decision stack.
**Purpose:** Counter-evidence base for the proposed EU+CVaR migration. No
recommendation is made here; this documents only what exists, with `file:line`
citations.

---

## 0. TL;DR — the two pitch claims, checked

| Pitch claim | Verdict | Evidence |
|---|---|---|
| "7+ conflicting heuristic variables" | **Partially false.** There are 6 exit-decision *layers* and **8 tunable parameters** (not "7+ variables" — the count conflates the two). The layers do **not** conflict: they are resolved by a single deterministic priority order. | `math_engine.py:661-692`, `alpha_bot_execution.py:1447-1467` |
| "Relies on a one-size-fits-all 3-year static history" | **Mostly false.** The 3-year Alpaca history feeds *only* the live Monte Carlo kNN pool and is a **rolling** window re-fetched every trading day (cache keyed on `current_date_str`). The autotuner does **not** use it at all — it tunes on a **rolling 125-trading-day** walk-forward window. Parameters are tuned **per symphony** (and optionally per account in port mode), i.e. not one-size-fits-all. | `alpha_bot_execution.py:261-272`, `synthetic_history.py:234,281`, `autotuner.py:963-1000` |

Detail follows.

---

## 1. `math_engine.py` — Decision Layers Enumerated

The engine is a stack of **pure functions**. Each is a self-contained primitive;
composition happens in `alpha_bot_execution.py` (§2). All named constants live at
module scope in `math_engine.py` (project no-magic-numbers rule).

### 1.1 Volatility scaling — `compute_active_trailing_stop` (`math_engine.py:178-222`)
- **Computes:** the active trailing-stop *distance* in percentage points.
  `safe_vol = symphony_vol if >0 else VOL_FALLBACK`;
  `active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)`; then if
  `para_armed or breakeven_locked`, `active *= parabolic_squeeze_multiplier`.
- **Trigger:** evaluated **every cycle** for every armed/tracked symphony.
- **Inputs / constants:**
  - `VOL_FALLBACK = 1.0` — `math_engine.py:100`
  - `symphony_vol` ← `calculate_20d_vol` (`math_engine.py:836-868`), 20-day realized vol.
  - `LOOKBACK_DAYS = 20` — `math_engine.py:61` (vol window)
  - `parabolic_squeeze_multiplier` ← tunable `MAX_PARABOLIC_SQUEEZE` (see §3).
  - `dynamic_multiplier`, `dynamic_min_stop` ← from the time-squeeze layer (§1.2).
- There is also `calculate_14d_atr_pct` (`math_engine.py:871-925`, `ATR_LOOKBACK_DAYS = 15` at line 62) — an ATR variant that **falls back to `calculate_20d_vol`** if high/low data is missing. Note: production's per-cycle path calls `calculate_20d_vol` (`alpha_bot_execution.py:1123`), not the ATR function — ATR is not on the live exit path.

### 1.2 Log time squeeze — `compute_time_squeeze_decay` (`math_engine.py:144-175`)
- **Computes:** `(dynamic_multiplier, dynamic_min_stop)`.
  `decay_curve = log10(1 + 9*time_ratio)` (0.0 at open → 1.0 at close);
  `dynamic_multiplier` interpolates `MULT_OPEN→MULT_CLOSE`;
  `dynamic_min_stop` interpolates `MIN_STOP_OPEN→MIN_STOP_CLOSE`.
- **Trigger:** every cycle. `time_ratio` is fraction of session elapsed
  (`alpha_bot_execution.py:1201-1208`).
- **Constants:**
  - `DECAY_CURVE_SCALAR = 9` — `math_engine.py:95`
  - `MULT_OPEN = 1.5`, `MULT_CLOSE = 0.5` — `math_engine.py:96-97`
  - `MIN_STOP_OPEN = 0.3`, `MIN_STOP_CLOSE = 0.15` — `math_engine.py:98-99`
- **Self-documented provenance note:** the comment at `math_engine.py:88-94`
  explicitly states the concave curve "has no formal literature provenance and
  is flagged for a follow-up empirical review." This is a tuned heuristic — the
  pitch's "heuristic" framing is accurate *for this layer*.

### 1.3 Parabolic ratchet — `compute_para_arm_decision` (`math_engine.py:118-141`)
- **Computes:** `(velocity, should_arm_transition)`.
  `velocity = current_return - prev_return`;
  `should_arm = (velocity >= para_threshold) AND (not currently_armed)`.
- **Trigger:** every cycle. Once `para_armed` flips True it **never re-arms**
  (one-way latch).
- **Effect:** when `para_armed`, `compute_active_trailing_stop` multiplies the
  stop distance by `parabolic_squeeze_multiplier` (tightens the stop).
- **Constant:** `para_threshold` ← tunable `PARABOLIC_VELOCITY_THRESHOLD` (§3).

### 1.4 MC gating — `run_monte_carlo` (`math_engine.py:705-833`)
See §5 for full detail. In the layer taxonomy: the MC probability `prob_beating`
**gates three other layers** rather than emitting an exit itself:
- Arms / disarms the trailing stop (`alpha_bot_execution.py:1141-1164`).
- Arms / confirms / disarms take-profit (`compute_tp_confirmation`).
- Vetoes the trailing stop ("MC sanity gate", `MC_SANITY_THRESHOLD = 60.0`,
  `math_engine.py:303`, applied in `compute_exit_confirmation:358-361`).

### 1.5 VWAP — two independent sub-systems
- **Signal:** `compute_vwap_signals` (`math_engine.py:440-492`) — allocation-
  weighted `(last_price - vwap)/vwap` across holdings → `(weighted_vwap_diff,
  valid_vwap_weight)`.
- **Bleed arm threshold:** `compute_vwap_bleed_arm_threshold` (`:500-526`) —
  `raw = -(symphony_vol * bleed_multiplier)`, clamped to
  `[VWAP_BLEED_ARM_MIN=-3.0, VWAP_BLEED_ARM_MAX=-0.5]` (`:496-497`).
- **State machine:** `compute_vwap_breakdown_update` (`:534-622`) — two
  *independent* systems:
  - **System A (profit-protection break):** arms once `safe_hwm >=
    vwap_cross_hwm_pct`; counts ticks where `current_return < safe_hwm`;
    fires `is_vwap_broken` after `VWAP_BREAK_CONFIRM_TICKS = 3` (`:531`).
  - **System B (bleed):** counts ticks where `current_return <=
    vwap_bleed_arm_pct`; fires `is_vwap_bleed_broken` after
    `vwap_bleed_ticks_threshold` (tunable `VWAP_BLEED_TICKS`).
  - Gate to evaluate either: `valid_vwap_weight > VWAP_WEIGHT_THRESHOLD = 0.5`
    (`:530`) AND `weighted_vwap_diff < 0`.
- **Constants:** `VWAP_WEIGHT_THRESHOLD = 0.5`, `VWAP_BREAK_CONFIRM_TICKS = 3`
  (`:530-531`), `VWAP_BLEED_ARM_MIN/MAX` (`:496-497`).
- **Self-documented provenance note:** `math_engine.py:601-606` flags System A's
  `safe_hwm >= vwap_cross_hwm_pct` gate as "a tuned practitioner heuristic with
  no formal literature provenance."

### 1.6 Breakeven — `compute_breakeven_update` (`math_engine.py:225-298`)
- **Computes:** `(new_hold_ticks, new_breakeven_locked, stop_trigger_level)`.
  `dynamic_activation = clamp(symphony_vol, 0.4, 3.0)`; a tick counts if
  `current_return >= dynamic_activation - 0.2`; after
  `HWM_HOLD_TICKS_THRESHOLD = 5` qualifying ticks `breakeven_locked` latches
  True (one-way). When locked, `stop_trigger_level = max(base_stop_level, 0.0)`
  — the 0.0 breakeven floor.
- **Constants:** `BREAKEVEN_ACTIVATION_MIN = 0.4`, `_MAX = 3.0`,
  `_DEADBAND = 0.2`, `HWM_HOLD_TICKS_THRESHOLD = 5`,
  `TRIGGERED_OVERRIDE_LEVEL = -999.0` — `math_engine.py:103-115`.
- **Reference cited in code:** Fu & Zhang (2012), *Int. J. Operations Research*
  9(3) — `math_engine.py:273-274`.

### 1.7 Exit confirm — `compute_exit_confirmation` (`math_engine.py:307-368`)
- **Computes:** `(new_below_stop_count, is_trailing_stop_hit)`.
  Guard: if `not armed or is_triggered` → return unchanged, no exit.
  `below_stop_condition = (current_return <= stop_trigger_level -
  MAGNITUDE_FLOOR_PCT) AND mc_sanity_ok`. After `EXIT_CONFIRM_TICKS = 3`
  consecutive qualifying ticks, `is_trailing_stop_hit = True`. A miss resets the
  count to 0.
- **MC sanity gate:** `mc_sanity_ok = prob_beating is None or prob_beating <
  MC_SANITY_THRESHOLD`. None (MC unavailable) → passes (fail-safe).
- **Constants:** `MAGNITUDE_FLOOR_PCT = 0.10`, `MC_SANITY_THRESHOLD = 60.0`,
  `EXIT_CONFIRM_TICKS = 3` — `math_engine.py:302-304`.

### 1.8 Take-profit confirm — `compute_tp_confirmation` (`math_engine.py:375-437`)
- **Computes:** `(new_tp_armed, new_above_tp_count, is_tp_hit)`. Arms when
  `mc_available and prob_beating < take_profit_mc_pct`; once armed, counts ticks
  where MC rises back `>= take_profit_mc_pct`; fires after
  `TP_CONFIRM_TICKS = 2` *and* `current_return > 0`; if MC confirms but return
  ≤ 0 it disarms.
- **Constant:** `TP_CONFIRM_TICKS = 2` — `math_engine.py:372`.

**Layer count:** 6 distinct exit-decision layers (vol-scaling, time-squeeze,
parabolic ratchet, breakeven, VWAP {2 sub-systems}, MC). They emit **4 exit
signals**: `is_trailing_stop_hit`, `is_tp_hit`, `is_vwap_broken`,
`is_vwap_bleed_broken`.

---

## 2. `alpha_bot_execution.py` — Composition Into One Per-Cycle Decision

All within the per-symphony loop body (`alpha_bot_execution.py:~1063-1496`).
Execution **order** per cycle:

1. **HWM update** — `:1101-1113`.
2. **Monte Carlo** — `prob_beating = run_monte_carlo(...)`, `:1115-1122`;
   `mc_available = prob_beating is not None`, `:1139`.
3. **20-day vol** — `:1123`.
4. **Trailing-stop arm/disarm** (MC-gated) — `:1141-1164`.
5. **Parabolic arm** — `:1173-1194`.
6. **Time-squeeze decay** — `:1196-1211`.
7. **Active stop distance** — `:1213-1223`.
8. **Breakeven update → `stop_trigger_level`** — `:1225-1238`.
9. **Check 1 — Trailing Stop** → `is_trailing_stop_hit` — `:1240-1250`.
10. **Check 2 — Take-Profit** → `tp_triggered_now` — `:1263-1286`.
11. **Check 3 — VWAP Breakdown** → `is_vwap_broken`, `is_vwap_bleed_broken` —
    `:1310-1326`.
12. **Open-window grace** — if within `VWAP_OPEN_WINDOW_GRACE_MINUTES` (default
    15) of open, **both** VWAP signals are forced False — `:1328-1333`.
13. **Trigger resolution** — `:1447-1467`.

### 2.1 Precedence — single deterministic resolver
When any of the 4 signals is True (`:1447-1452`), the decision routes through
**one** function, `math_engine.resolve_trigger_priority` (`:1455-1460`,
defined `math_engine.py:669-692`). The canonical priority order is a single
named constant `_TRIGGER_PRIORITY_ORDER` (`math_engine.py:661-666`):

> **VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop**

`resolve_trigger_priority` returns `(winner, also_true[])` — it explicitly
records which lower-priority signals **co-fired**. The winner alone drives the
exit (`reason`, `attempted_level` via `_level_map`, `:1461-1467`).

### 2.2 Do the layers contradict each other?
**The honest answer: no — not in the sense the pitch implies.** Evidence:

- **The four exit signals are pure booleans resolved by one priority list.**
  Two signals firing on the same tick is *expected and handled* — that is
  exactly what `also_true` captures (`math_engine.py:679-692`,
  `alpha_bot_execution.py:1455-1467`). There is no code path where the engine
  is "stuck" between two layers; the priority order is total and deterministic.
- **All four exit signals point the *same direction* — they only ever say
  "exit".** None of the four exit signals can say "hold". There is no
  layer-vs-layer "one says hold, one says exit" conflict because no layer emits
  a hold signal.
- **The one place a layer *suppresses* another is explicit and one-directional:**
  - The **MC sanity gate** can veto the trailing stop: a high `prob_beating`
    (≥ 60.0) resets `below_stop_count` and blocks `is_trailing_stop_hit`
    (`math_engine.py:358-361`). This is a *designed* gate ("if we still think we
    beat the benchmark, don't capitulate", `:332-337`), not an emergent
    conflict — and it is documented as fail-safe: MC `None` → gate passes.
  - The **open-window grace** suppresses both VWAP signals for the first 15
    minutes (`alpha_bot_execution.py:1328-1333`) — again a designed gate.
- **Genuine inter-layer interaction that *could* be called "tension"** —
  honest caveat for the migration debate: the parabolic ratchet and breakeven
  lock both feed `compute_active_trailing_stop` and both *tighten* the stop
  (`math_engine.py:220-221`). They never disagree (both tighten), but they can
  compound. That is a coupling, not a contradiction.

**Conclusion:** the layers are **cleanly ordered and prioritized**, not
conflicting. The "7+ conflicting heuristic variables" framing is not supported
by the code. What *is* fair to say: several layers are explicitly self-flagged
in-code as "tuned practitioner heuristics with no formal literature provenance"
(`math_engine.py:88-94`, `:601-606`) — so the *heuristic* characterization is
accurate; the *conflicting* characterization is not.

---

## 3. Actual Tunable-Parameter Count

The real count of operator/Optuna-tunable parameters that affect exit
decisions: **8.**

| # | Parameter | Default | Set / overridden where | Optuna-tuned? |
|---|---|---|---|---|
| 1 | `TRIGGER_THRESHOLD_PCT` | 15.0 | env `alpha_bot_execution.py:83`; `DEFAULT_STRATEGY` `database.py:29` | **No** — in `DEFAULT_LOCKED_VARS` (`database.py:40`) |
| 2 | `TAKE_PROFIT_MC_PCT` | 5.0 | env `:85`; `DEFAULT_STRATEGY:30` | **Yes** — `autotuner.py:972` |
| 3 | `VWAP_CROSS_HWM_PCT` | 1.0 | env `:88`; `DEFAULT_STRATEGY:32` | **Yes** — `autotuner.py:973` |
| 4 | `PARABOLIC_VELOCITY_THRESHOLD` | 2.0 | env `:98`; `DEFAULT_STRATEGY:33` | **Yes** — `autotuner.py:976` |
| 5 | `MAX_PARABOLIC_SQUEEZE` | 0.50 | env `:99`; `DEFAULT_STRATEGY:34` | **Yes** — `autotuner.py:977` |
| 6 | `VWAP_BLEED_MULTIPLIER` | 1.5 | `acc_params.get(..., 1.5)` `alpha_bot_execution.py:1031`; `DEFAULT_STRATEGY:35` | **Yes** — `autotuner.py:974` |
| 7 | `VWAP_BLEED_TICKS` | 10 | `acc_params.get(..., 10)` `:1032`; `DEFAULT_STRATEGY:36` | **Yes** — `autotuner.py:975` |
| 8 | `MAX_SQUEEZE_FLOOR` | 0.20 | env `:84`; `DEFAULT_STRATEGY:31` | **No** — in `DEFAULT_STRATEGY` but never in the Optuna search space |

**6 of the 8** are in the Optuna search space (`OPTUNA_SEARCH_SPACE_KEYS`,
`autotuner.py:52-56`). Per-symphony override path:
`acc_params = symphony_strat.get("params", {})` (`alpha_bot_execution.py:1022`)
→ each `acc_*` falls back to the env/module default if absent (`:1024-1032`).

**Additional fixed config dials** (not "decision parameters" but worth listing
so the count is honest): `SIMULATION_PATHS` (5000), `NEIGHBOR_K` (150),
`VWAP_OPEN_WINDOW_GRACE_MINUTES` (15), `EXECUTION_START_TIME` — all env-set in
`alpha_bot_execution.py:77-102`, none Optuna-tuned.

**~25 named constants** in `math_engine.py` (`:61-115`, `:302-304`, `:372`,
`:496-497`, `:530-531`) are *hard-coded*, not tunable — confirm-tick counts,
clamp bounds, decay-curve shape, etc.

So: "7+" understates the *named-constant* surface and overstates the *tunable*
surface — the precise figure is **8 tunable parameters, 6 of them Optuna-searched**.

---

## 4. `autotuner.py` — What It Actually Searches

### 4.1 Search space
6-dimensional (`autotuner.py:970-977`), bounds named at `:60-71`:
| Param | Bounds | Type |
|---|---|---|
| `TAKE_PROFIT_MC_PCT` | [2.0, 10.0] | float |
| `VWAP_CROSS_HWM_PCT` | [0.5, 2.5] | float |
| `VWAP_BLEED_MULTIPLIER` | [0.5, 3.0] | float |
| `VWAP_BLEED_TICKS` | [3, 30] | int |
| `PARABOLIC_VELOCITY_THRESHOLD` | [1.0, 4.0] | float |
| `MAX_PARABOLIC_SQUEEZE` | [0.1, 0.8] | float |

The V1 *calibration sweep* (`run_calibration_sweep`, `:1186-1369`) is a **2-D**
sub-search: only `PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT`
(narrowed bounds `[0.3, 2.0]` for the latter, `:78-79`). It is **read-only** —
persists nothing to the DB (`:1198`).

### 4.2 Window, trials, sampler
- **History window:** rolling **125 trading days** (`synthetic_history.py:281`,
  `generate_synthetic_history`), regenerated each autotuner run
  (`autotuner.py:890`).
- **Trial count:** `n_trials=500`, `n_jobs=-1` for `run_autotuner`
  (`autotuner.py:1000`); `n_trials=100`, `n_jobs=1` for the calibration sweep
  (`:1278`).
- **Sampler:** Optuna default **TPE** for `run_autotuner` (no explicit sampler
  passed to `create_study`, `:999`); explicit `TPESampler(seed=random_state)`
  for the calibration sweep (`:1271`).
- **Objective:** Sortino ratio on per-day guard-alpha (`compute_sortino_ratio`,
  `:231-259`; objective closure `:970-988`). The deployment/OOS objective
  `run_simulation` (`:735-802`) is an explicit **loss-averse** utility:
  asymmetric penalties on missed upside (`×1.5`), drawdown-from-peak (`×0.75`),
  and negative guard-alpha (`×2.0`) — constants `:94-114`.

### 4.3 CONFIRM/REFUTE: rolling walk-forward?
**CONFIRMED — the system uses walk-forward, but not a *rolling-fold* one.**
- It is a **three-fold walk-forward split**: 60% train / 20% validation / 20%
  frozen-eval (`TRAIN_RATIO/VALIDATION_RATIO/FROZEN_EVAL_RATIO`,
  `autotuner.py:154-159`; split logic `:906-916`). Port mode uses 50/20/30
  (`:166-171`).
- **Purge + embargo** at both fold boundaries: `PURGE_DAYS = 20`
  (`:129`, sized to `max(vol=20, ATR=15)`), `EMBARGO_DAYS = 1` (`:147`).
  Cited: López de Prado 2018, *AFML* Ch. 7.4.
- **Selection on validation fold only**; frozen-eval consumed exactly once
  post-selection (`:982-988`, `:1105-1111`).
- **Important nuance for the migration debate:** the code itself
  (`autotuner.py:862-868`, "OOS-fold-collapse v2") **acknowledges** this is a
  *single* train/val/frozen split, not rolling/purged k-fold CV — and flags
  expanding to rolling folds as a "future workstream." So "rolling walk-forward"
  is **half-true**: the *window* rolls day-to-day, the *fold structure* does not.

### 4.4 CONFIRM/REFUTE: multiple-testing / DSR / BHY haircut?
**CONFIRMED — a multiple-testing haircut already exists and is live.**
- **Harvey & Liu 2015 selection-bias haircut**, `_haircut_select`
  (`autotuner.py:698-732`), applied to the AI branch in `run_autotuner`
  (`:1008-1049`) and in the calibration sweep (`:1284-1318`).
- Mechanics: per-trial t-stat `Sortino·√T` (`compute_sortino_tstat`, `:289-301`)
  → one-sided p-value `1-Φ(t)` clamped (`compute_haircut_pvalue`, `:304-316`)
  → **Benjamini-Hochberg-Yekutieli** step-up adjustment with the Yekutieli
  arbitrary-dependence factor `c(N) = Σ 1/j` (`benjamini_hochberg_adjust`,
  `:319-356`) → deploy the BHY-winner only if `p_adj <= HARVEY_LIU_FDR_Q = 0.05`
  (`:277`). If no trial clears the gate the AI proposal is rejected wholesale
  and the cascade falls to fallback/default (`:1042-1049`, `:1064-1074`).
- **Re: "DSR" / Deflated Sharpe Ratio** — the code comment at `:266-271`
  explicitly states it **does NOT** use a Sharpe-derived deflation formula and
  calls that the "H-6 category error this replaces" (a Sortino's sampling
  distribution ≠ a Sharpe's). The recent `selection_tstat` re-pin commit
  (`9444dbb`, "autotune-panel tests use selection_tstat, not DSR") is precisely
  the surface that removed DSR terminology. **So: no DSR; yes BHY.**
- The recent commits the brief listed all map to live code:
  `calibration sweep` = `run_calibration_sweep`; `BHY dependency factor c(N)` =
  `benjamini_hochberg_adjust` line 345; `haircut_outcome` = the explicit
  per-row FDR-gate verdict field (`:1300-1318`, `:1360`).

---

## 5. `run_monte_carlo` — Already On The Execution Path Today

**CONFIRMED: Monte Carlo is live, on the per-cycle execution path, today.**
Called every cycle for every symphony at `alpha_bot_execution.py:1115-1122`.

- **Definition:** `math_engine.py:705-833`.
- **Method:** k-Nearest-Neighbours regime matching, **not** a parametric MC.
  Distance is Euclidean over 2 z-standardized features — SPY daily return and
  rolling 20-day SPY vol (`:746-796`). Picks the `neighbor_k` nearest historical
  days, builds a K×N returns matrix, dot-products with portfolio weights, then
  resamples `simulation_paths` draws with replacement and returns the percentile
  of `current_symphony_return` in the simulated distribution (`:798-833`).
- **What it computes / returns:** `prob_beating` — the probability (0–100, in
  percentage points, `:833`) the symphony's current return beats its
  benchmark-implied path. **Or `None`** when history is insufficient
  (`MC_INSUFFICIENT_HISTORY_SENTINEL`, `:71`, `:744`).
- **Path count:** `MC_DEFAULT_SIMULATION_PATHS = 5000` (`:76`); production passes
  `SIMULATION_PATHS` env, default 5000 (`alpha_bot_execution.py:101`).
- **Neighbour count:** `MC_DEFAULT_NEIGHBOR_K = 150` (`:77`); production env
  `NEIGHBOR_K`, default 150 (`alpha_bot_execution.py:102`).
- **Minimum-history requirement:** the **eligible-pool** count must be
  `>= MC_MIN_HISTORY_DAYS = 20` (`:72`, `:739`). Because the first
  `MC_VOL_WINDOW_DAYS - 1 = 19` days are excluded from the kNN pool
  (short-sample vol bias, `:765-771`), the **raw** minimum history is
  `20 + 19 = 39` days (`:738`, confirmed in project memory
  `project_mc_eligible_pool_vs_raw_day_boundary`).
- **Determinism:** seeded per cycle via `derive_cycle_mc_seed`
  (`math_engine.py:695-702`) — SHA-256 of the cycle_id into a 64-bit space;
  uses an isolated `np.random.default_rng`, not the numpy global RNG.
- **Consumers of the return value** (7+ sites, per project memory
  `project_mc_sentinel_consumer_blast_radius`): trailing-stop arm/disarm
  (`alpha_bot_execution.py:1141-1164`), MC sanity veto inside
  `compute_exit_confirmation`, take-profit arm/confirm in
  `compute_tp_confirmation`, the rolling `mc_history` buffer (`:1168-1171`),
  port-level `mc_sanity_gate_would_block` snapshot (`:1385-1387`), chart
  history, and the autotuner replay (`mc_prob` field in tick data).
- **The 3-year history feeding MC:** `fetch_alpaca_history`
  (`alpha_bot_execution.py:256-...`) pulls `365*3+30` days (`:272`). The cache
  is keyed on `current_date_str` (`:265`) — it is **re-fetched every trading
  day**, i.e. a **rolling** 3-year window, not a frozen one. The log string
  "static 3-year history" (`:266`) refers to it being a *static within-day
  cache*, not a static-across-time dataset.

---

## 6. Honest Assessment — Do The Heuristic Layers Genuinely Conflict?

**No.** The layers are cleanly ordered and prioritized. Concrete evidence:

1. **One total, named priority order.** `_TRIGGER_PRIORITY_ORDER`
   (`math_engine.py:661-666`) and the single resolver `resolve_trigger_priority`
   (`:669-692`). There is exactly one exit decision per cycle; co-firing signals
   are recorded in `also_true`, never left ambiguous.
2. **All exit signals are unidirectional.** Every one of the 4 signals
   (`is_trailing_stop_hit`, `is_tp_hit`, `is_vwap_broken`,
   `is_vwap_bleed_broken`) can only say "exit". **No layer emits a "hold"
   signal**, so the pitch's "one says hold, another says exit" scenario has no
   code analogue.
3. **Suppression gates are explicit and one-directional, not emergent
   conflicts:** the MC sanity gate (`math_engine.py:358-361`) and the
   open-window grace (`alpha_bot_execution.py:1328-1333`) are *designed*
   vetoes — documented, fail-safe (`None` → pass), and one-way.
4. **The only genuine coupling:** parabolic ratchet and breakeven lock both
   tighten the same stop distance (`math_engine.py:220-221`) — they *compound*,
   they never *contradict*.

**Where the pitch has a fair point:** several layers are **explicitly
self-flagged in the code** as tuned heuristics with no formal provenance —
the time-squeeze decay curve (`math_engine.py:88-94`) and the VWAP System-A
gate (`:601-606`). The decision objective is Sortino-based with ad-hoc
loss-aversion multipliers (`autotuner.py:94-114`). So calling the stack
"heuristic" is defensible; calling it "conflicting" is not — and calling the
parameter surface "7+ variables" / the history "one-size-fits-all 3-year
static" misstates the facts:
- **8** tunable parameters (6 Optuna-searched), tuned **per symphony**.
- The autotuner runs on a **rolling 125-trading-day walk-forward** with
  purge+embargo and a **live BHY multiple-testing haircut** — it already does
  the statistical-rigor work the migration pitch implies is absent.
- Monte Carlo (kNN-regime, 5000 paths) is **already on the live execution
  path** and already gates three of the exit layers.

---

## Appendix — Files & Key Line Anchors
- `math_engine.py` — layers `:118-622`, MC `:705-833`, priority `:661-692`,
  constants `:61-115` / `:302-304` / `:372` / `:496-497` / `:530-531`.
- `alpha_bot_execution.py` — per-cycle composition `:1063-1496`, MC call
  `:1115-1122`, checks 1/2/3 `:1240-1326`, grace `:1328-1333`, resolution
  `:1447-1467`, param defaults `:77-102`, `acc_*` overrides `:1022-1032`,
  3-year history `:256-272`.
- `autotuner.py` — search space `:52-71` / `:970-977`, WFA split `:129-171` /
  `:906-916`, haircut `:262-356` / `:698-732`, calibration sweep `:1186-1369`,
  objectives `:231-259` / `:735-802`.
- `database.py` — `DEFAULT_STRATEGY` `:28-37`, `DEFAULT_LOCKED_VARS` `:40`.
- `synthetic_history.py` — 125-day window `:234`, `:281`.
