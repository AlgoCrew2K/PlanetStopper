<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. Math-implementation findings: zero Critical/High/Medium defects; L-1 (test gap for compute_tp_confirmation). Conclusion feeds docs/audit/final-audit-2026-05-29/math-soundness.md. -->
# Pillar 2 — Math Implementation Correctness Audit

**Auditor:** mathimpl-auditor  
**Worktree HEAD:** 8586ab2  
**Scope file:** `math_engine.py` (1727 lines)  
**Audit date:** 2026-05-30

---

## (a) Numerical Correctness — Layer-by-Layer

### Layer 1: Volatility Scaling (`calculate_20d_vol`, `calculate_14d_atr_pct`)

**`calculate_20d_vol` — PASS**

- Formula: `np.std(portfolio_daily_returns, ddof=0)` over the last `LOOKBACK_DAYS=20` dates. `math_engine.py:1113–1136`.
- `ddof=0` (population std) is intentional and consistent: constant throughout the file. No mismatch.
- Insufficient history short-circuit (`len < LOOKBACK_DAYS → 0.0`) at `math_engine.py:1114` is safe: downstream `compute_active_trailing_stop` falls back to `VOL_FALLBACK=1.0` when `symphony_vol <= 0`, so a 0.0 return triggers the fallback rather than collapsing the stop to zero.
- Returns `float(np.std(...))` — correctly casts numpy scalar to Python float.

**`calculate_14d_atr_pct` — PASS with minor observation**

- Formula: mean True Range / last_close * PCT_SCALAR, then dot-product with allocation weights. `math_engine.py:1139–1193`.
- True Range is correctly computed as `max(H-L, |H-prev_close|, |L-prev_close|)` at `math_engine.py:1178`.
- Uses `ATR_LOOKBACK_DAYS=15` (14 TRs + 1 prior close), consistent with the naming comment at `math_engine.py:65`.
- Falls back to `calculate_20d_vol` on missing data (correct defensive path).
- **Minor observation (not a defect):** `atr_pct_array.dot(weights)` at `math_engine.py:1192` takes an unweighted-sum-then-weight-averaged ATR across tickers, which gives a holdings-weighted blended ATR. This is the documented intent ("allocation-weighted ATR") and the production caller uses only one of vol vs ATR per cycle; no correctness issue.

### Layer 2: Time-Squeeze Decay (`compute_time_squeeze_decay`)

**PASS**

- Formula: `decay_curve = 1 - sqrt(1 - time_ratio)`, then linear interpolation from MULT_OPEN→MULT_CLOSE and MIN_STOP_OPEN→MIN_STOP_CLOSE. `math_engine.py:322–325`.
- Endpoints are exact: `sqrt(1)=1` and `sqrt(0)=0` in IEEE-754. No free parameters. THEORY provenance cited (Danielsson & Zigrand 2003).
- Reject-don't-coerce policy enforced: `time_ratio` outside `[0, 1]` raises `ValueError` at `math_engine.py:318–321`.
- `DECAY_CURVE_SCALAR` is absent (correctly removed per M3 closure).

### Layer 3: Parabolic Ratchet (`compute_para_arm_decision`)

**PASS with documented provenance gap (not a new finding — pre-existing)**

- Formula: `velocity = current_return - prev_return`; `should_arm = (velocity >= para_threshold) and (not currently_armed)`. `math_engine.py:289–290`.
- Pure function. No clamping, no scaling. Caller is responsible for state mutation.
- `_reject_non_finite` applied at entry. Velocity is Python float throughout.
- **Pre-existing provenance gap (vision-audit OQ-5, not a code defect):** `para_threshold` default (2.0 pp) and the squeeze multiplier in `compute_active_trailing_stop` have no published calibration source. This is flagged in the vision-audit and MEMORY.md; not re-litigated here.

### Layer 4: Breakeven Lock (`compute_breakeven_update`)

**PASS**

- Formula: `dynamic_activation = clamp(symphony_vol, BREAKEVEN_ACTIVATION_MIN, BREAKEVEN_ACTIVATION_MAX)`; `new_hold_ticks += 1 if current_return >= (dynamic_activation - BREAKEVEN_ACTIVATION_DEADBAND) else 0`; latch fires at `new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD=5`. `math_engine.py:434–447`.
- Latching invariant correct: `new_breakeven_locked = bool(currently_breakeven_locked OR (new_hold_ticks >= threshold))`. Once `True`, always `True`.
- Breakeven floor: `stop_trigger_level = max(base_stop_level, 0.0)` when locked. `math_engine.py:443`. Semantically anchored to "no worse than zero loss."
- `TRIGGERED_OVERRIDE_LEVEL = -999.0` sentinel applied last when `is_triggered=True`. `math_engine.py:447`. This correctly suppresses re-exit.
- Integer `new_hold_ticks` correctly reset to 0 on miss (never accumulates spuriously).

### Layer 5: Active Trailing Stop (`compute_active_trailing_stop`)

**PASS**

- Formula: `safe_vol = vol if vol > 0 else VOL_FALLBACK=1.0`; `active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)`; if armed or locked: `active *= parabolic_squeeze_multiplier`. `math_engine.py:368–372`.
- `parabolic_squeeze_multiplier <= 0` raises `ValueError` (reject-don't-coerce). Correct — a zero or negative multiplier would produce a degenerate stop.
- `VOL_FALLBACK = 1.0` at `math_engine.py:250` is named with provenance comment.

### Layer 6: Exit Confirmation (`compute_exit_confirmation`)

**PASS**

- MC gate: `mc_sanity_ok = prob_beating is None OR prob_beating < MC_SANITY_THRESHOLD`. `math_engine.py:508`. `None` (insufficient MC) passes the gate — fail-safe preserved.
- Magnitude floor: `current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT`. `math_engine.py:510`. `MAGNITUDE_FLOOR_PCT=0.10` prevents exit on a hairline breach.
- Guard invariant: when `(not armed) or is_triggered`, returns `(current_below_stop_count, False)` — state is NOT reset. `math_engine.py:503–504`. Correct per the docstring.
- `EXIT_CONFIRM_TICKS=3` at `math_engine.py:454`. Named and commented.

### Layer 7: Take-Profit Confirmation (`compute_tp_confirmation`)

**PASS with one edge-case observation**

- Logic: arm when `mc_available AND prob_beating < take_profit_mc_pct`; confirm when above-threshold ticks reach `TP_CONFIRM_TICKS=2` AND `current_return > 0`. `math_engine.py:569–584`.
- Fail-safe: `mc_available=False` cannot arm or confirm TP. Counter resets on MC-unavailable ticks while armed. `math_engine.py:583–584`.
- **Edge-case observation (LOW severity, not a blocking defect):** when `mc_available=True AND prob_beating < take_profit_mc_pct AND tp_armed=True AND is_triggered=True`, the function returns `(tp_armed, above_tp_count, False)` at `math_engine.py:572` — it does NOT reset the armed state. This is correct per the docstring ("state unchanged"). The `is_triggered` path of the outer code should suppress this branch entirely. The behavior is internally consistent but warrants a comment clarifying the `tp_armed=True, is_triggered=True` state combination is never reachable in practice.

### Layer 8: VWAP Signals (`compute_vwap_signals`)

**PASS**

- Formula: `weighted_vwap_diff += allocation * (p - v) / v`; `valid_vwap_weight += allocation`. `math_engine.py:638–641`.
- Zero-volume guard at `math_engine.py:632–634` correctly excludes tickers with explicitly present non-positive volume. Missing volume key qualifies (correct: missing = no data, not zero volume).
- Degenerate VWAP (`v <= 0`) correctly skipped at `math_engine.py:637`.

### Layer 9: VWAP Breakdown State Machine (`compute_vwap_breakdown_update`)

**PASS**

- Boundary semantics documented and tested: `>` for weight, `<` for diff (not `>=`/`<=`). `math_engine.py:747`. Consistent with docstring and pinned by named fixture tests.
- System A and System B are independent (both accumulate within the gate-pass path). `math_engine.py:774–787`.
- `is_triggered` guard at `math_engine.py:744–745` preserves state unchanged and returns `(False, False)` — correct for a triggered position.

### Layer 10: Monte Carlo (`run_monte_carlo`)

**PASS**

- Eligible-pool boundary correctly computed: `eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)`. `math_engine.py:1012`. This implements the 39-raw-day minimum correctly (MC_MIN_HISTORY_DAYS=20 + (MC_VOL_WINDOW_DAYS-1)=19 = 39).
- kNN z-scoring uses candidate-pool statistics (not global), transforming today's query with the same parameters. `math_engine.py:1056–1059`. Correct: prevents unit-artifact domination.
- `np.argpartition` at `math_engine.py:1071` correctly selects the K-nearest but does NOT sort them. The final bootstrap `rng.choice` is order-independent (sampling with replacement), so the lack of sort is not a defect.
- Deterministic seed: `np.random.default_rng(seed)` at `math_engine.py:1096`. Isolated generator — does NOT touch global numpy RNG.
- `below_count = np.searchsorted(sim_results, current_symphony_return)` on a sorted array at `math_engine.py:1100`. Returns the index of the first value >= current_symphony_return, so `simulation_paths - below_count` = count of values that beat the current return. **PASS: semantically correct for "probability of beating current return."**

### Layer 11: CVaR (`compute_cvar_5pct_general_distribution`, `compute_portfolio_cvar`)

**PASS**

- R-U formula: `cvar = (1/alpha) * (1/N) * (sum_below + fractional_weight * var_atom)`. `math_engine.py:1380`. Correct atom-contribution handling (Acerbi-Tasche discipline).
- `k = int(alpha * n)` = `floor(alpha * N)`. `math_engine.py:1321`. Correct discrete quantile.
- `fractional_weight = alpha * n - k`. `math_engine.py:1322`. Correct.
- `tail_obs_count = k + (1 if fractional_weight > 0 else 0)`. `math_engine.py:1342`. H-2 binding on distinct tail count, NOT resample count.
- `compute_portfolio_cvar` delegates to `compute_cvar_5pct_general_distribution` on `nearest_day_returns` (pool, not resampled paths). `math_engine.py:1523`. Correct per spec-m2 FINDING 1.
- `CVaRAssessment` dataclass enforces fail-safe invariants in `__post_init__`: `cvar_pct=None` forces `breach=False`. `math_engine.py:162–169`.

### Layer 12: CRRA Utility (`compute_crra_utility`, `compute_crra_eu_objective`)

**PASS**

- CRRA formula: `(W^(1-gamma) - 1) / (1-gamma)` with `-1` term present. `math_engine.py:1580`. Correct — the `-1` term is load-bearing for mean(U) computation.
- Log-utility branch at `|gamma - 1| < CRRA_LOG_UTILITY_GAMMA_TOL=1e-9`: returns `math.log(W)`. `math_engine.py:1577–1579`. Correct L'Hopital limit.
- `WEALTH_ARG_FLOOR=0.001` applied by caller before entry; this function validates at entry via `_reject_non_finite` but does NOT apply the floor. `math_engine.py:1576`. Correct per docstring (floor must be applied by caller, not here).
- `compute_crra_eu_objective`: `W = max(WEALTH_ARG_FLOOR, 1.0 + r)` applied inline before calling `compute_crra_utility`. `math_engine.py:1606`. Correct — floor on input W.

### Layer 13: Regime Match Quality (`compute_regime_match_quality`)

**PASS**

- Mean squared Mahalanobis distance over K nearest neighbors compared against `MC_REGIME_MATCH_CHI2_THRESHOLD=9.21` (chi2(2)_{0.99}). `math_engine.py:1718`.
- The comment at `math_engine.py:108–119` correctly explains why using the single-draw 0.99-quantile against a MEAN-of-K statistic is intentionally conservative.
- Fail-safe: insufficient pool returns `(None, False)` — `is_unprecedented=False` means absence of a diagnostic does NOT suppress MC. `math_engine.py:1659–1669`.
- Env-var override at `math_engine.py:1647–1648` allows operator threshold adjustment without code change.

---

## (b) Constant Provenance — Magic Numbers Audit

**All module-level constants: PASS**

Every numeric literal at the module level carries both a name and a source comment. Verified by inspection:

| Constant | Line | Source Comment | Verdict |
|---|---|---|---|
| `LOOKBACK_DAYS=20` | 64 | "20-day realized-volatility window — Planet Stopper risk-sizing standard" | PASS |
| `ATR_LOOKBACK_DAYS=15` | 65 | "14-day true-range window ... + 1 prior close" | PASS |
| `PCT_SCALAR=100.0` | 66 | "decimal return -> percentage points" | PASS |
| `CRRA_LOG_UTILITY_GAMMA_TOL=1e-9` | 79 | "Tolerance around gamma == 1 for log-utility branch" + Merton/Samuelson cite | PASS |
| `WEALTH_ARG_FLOOR=0.001` | 84 | "Prevents log(0) / blow-up" + decision-science-council-synthesis cite | PASS |
| `MC_MIN_HISTORY_DAYS=20` | 87–89 | Inline comment | PASS |
| `MC_VOL_WINDOW_DAYS=20` | 90 | "Rolling SPY vol window" | PASS |
| `MC_DEFAULT_SIMULATION_PATHS=5000` | 91 | "CLT stability vs runtime tradeoff" | PASS |
| `MC_DEFAULT_NEIGHBOR_K=150` | 92–94 | "kNN regime locality" | PASS |
| `MC_SEED_MODULUS=2**64` | 101 | "birthday-bound" comment with collision rate math | PASS |
| `MC_REGIME_MATCH_CHI2_THRESHOLD=9.21...` | 119 | Full chi2 derivation with citations | PASS |
| `CVAR_TAIL_PCT=0.05` | 124 | "5th-percentile tail" | PASS |
| `CVAR_ALPHA_DEFAULT=0.05` | 129 | "5th-percentile expected shortfall" + council cite | PASS |
| `CVAR_MIN_TAIL_OBS=1` | 132 | "require at least 1 genuine below-VaR" | PASS |
| `MULT_OPEN=1.5` | 246 | "dynamic_multiplier at market open (loosest stop)" | PASS |
| `MULT_CLOSE=0.5` | 247 | "dynamic_multiplier at market close (tightest)" | PASS |
| `MIN_STOP_OPEN=0.3` | 248 | "min stop floor at market open, in percentage points" | PASS |
| `MIN_STOP_CLOSE=0.15` | 249 | "min stop floor at market close" | PASS |
| `VOL_FALLBACK=1.0` | 250 | "neutral fallback for safe_vol when symphony_vol <= 0" | PASS |
| `BREAKEVEN_ACTIVATION_MIN=0.4` | 253–255 | "lower clamp for dynamic activation threshold (in percentage points)" | PASS |
| `BREAKEVEN_ACTIVATION_MAX=3.0` | 256 | "upper clamp for dynamic activation threshold" | PASS |
| `BREAKEVEN_ACTIVATION_DEADBAND=0.2` | 257–259 | "current_return must be within this distance below dynamic_activation" | PASS |
| `HWM_HOLD_TICKS_THRESHOLD=5` | 260–262 | "consecutive qualifying ticks needed to lock breakeven" | PASS |
| `TRIGGERED_OVERRIDE_LEVEL=-999.0` | 263–265 | "sentinel stop level when position is already triggered" | PASS |
| `MAGNITUDE_FLOOR_PCT=0.10` | 452 | "return must drop at least this far BELOW stop_trigger_level" | PASS |
| `MC_SANITY_THRESHOLD=60.0` | 453 | "MC probability >= this value blocks exit" + rationale | PASS |
| `EXIT_CONFIRM_TICKS=3` | 454 | "consecutive qualifying ticks needed to flip is_trailing_stop_hit" | PASS |
| `TP_CONFIRM_TICKS=2` | 522 | "consecutive above-threshold ticks ... needed to confirm a take-profit exit" | PASS |
| `VWAP_BLEED_ARM_MIN=-3.0` | 646 | "most-negative clamp; deepest bleed threshold allowed" | PASS |
| `VWAP_BLEED_ARM_MAX=-0.5` | 647 | "least-negative clamp; arm threshold must be at least this deep" | PASS |
| `VWAP_WEIGHT_THRESHOLD=0.5` | 680 | "minimum allocation coverage to evaluate VWAP signals" | PASS |
| `VWAP_BREAK_CONFIRM_TICKS=3` | 681 | "consecutive qualifying ticks for System A" | PASS |
| `_SORTED_DATES_CACHE_MAXSIZE=32` | 883 | ">= 8 (per-cycle hot path), << 64 (memory ceiling)" | PASS |
| `_SORTINO_SENTINEL=1e6` | 15 | Module-level comment citing autotuner.py source | PASS |

**One bare literal requiring attention:**

At `math_engine.py:620`: `NO_VOLUME = 0` is a LOCAL variable inside `compute_vwap_signals`. This follows the naming convention (local name + comment at `math_engine.py:618–619`: "Zero-volume tickers have no economically meaningful VWAP..."), which satisfies the "named + comment" project rule in spirit. However, it is a function-local named constant, not a module-level constant. **INFORMATIONAL (I-1):** The project rule says "no magic numbers in math_engine.py — every constant named + source comment." The local assignment with comment satisfies the intent. No action required.

---

## (c) Edge Cases

### Division by Zero

- `compute_vwap_signals`: `v > 0` guard at `math_engine.py:637` prevents `(p-v)/v` zero-division. PASS.
- `compute_cvar_5pct_general_distribution`: pool sort + `k < CVAR_MIN_TAIL_OBS` guard prevents division by zero in `(1/alpha)*(1/n)`. PASS.
- `compute_crra_utility`: `W^(1-gamma)` with `W >= WEALTH_ARG_FLOOR=0.001 > 0`. PASS.
- `_compute_rolling_spy_vol`: `np.maximum(var, 0.0)` at `math_engine.py:974` prevents negative-variance sqrt in growing-window phase. PASS.
- `compute_vwap_bleed_arm_threshold`: double-clamp prevents any output exceeding `[-3.0, -0.5]`. No divide. PASS.

### Empty History / Insufficient Data

- `calculate_20d_vol`: returns `0.0` at `math_engine.py:1114–1115` when `len(valid_dates) < LOOKBACK_DAYS`. PASS.
- `run_monte_carlo`: returns `MC_INSUFFICIENT_HISTORY_SENTINEL=None` when `eligible_days < MC_MIN_HISTORY_DAYS`. `math_engine.py:1013–1018`. PASS.
- `compute_portfolio_cvar`: same boundary, returns `CVaRAssessment(cvar_pct=None, breach=False, ...)`. `math_engine.py:1436–1447`. PASS.
- `compute_regime_match_quality`: same boundary, returns `(None, is_unprecedented=False)`. `math_engine.py:1658–1669`. PASS.
- `compute_crra_eu_objective`: returns `0.0` for empty series. `math_engine.py:1601–1602`. PASS.

### NaN/Inf

- `_reject_non_finite` applied at every public entry point that takes float inputs. Validated via grepping all `_reject_non_finite` call sites.
- `_reject_non_finite_in_records` applied for list-of-dicts inputs (`holdings`, `historical_data`). PASS.
- `compute_cvar_5pct_general_distribution`: explicit non-finite loop at `math_engine.py:1312–1317`. PASS.
- Growing-window phase of `_compute_rolling_spy_vol`: `np.maximum(var, 0.0)` prevents NaN from slight negative floating-point rounding. PASS.

### MC Eligible-Day vs Raw-Day Boundary

The `eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)` computation at `math_engine.py:1012` (run_monte_carlo) and `math_engine.py:1435` (compute_portfolio_cvar) and `math_engine.py:1657` (compute_regime_match_quality) is consistent across all three functions. Minimum raw history required = `MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) = 20 + 19 = 39` days. This matches the project memory `project_mc_eligible_pool_vs_raw_day_boundary`. **All three sites consistent. PASS.**

### `argpartition` vs `sort` Correctness in MC

`np.argpartition(distances, neighbor_k)[:neighbor_k]` at `math_engine.py:1071` returns the `neighbor_k` smallest indices but NOT sorted. This is correct for bootstrap sampling (order does not matter for `rng.choice`). PASS.

---

## (d) Golden-Fixture Test Coverage

Coverage was assessed by grepping test files and examining fixture directories.

| Math Layer | Golden Fixtures Present | Test File | Verdict |
|---|---|---|---|
| `calculate_20d_vol` | YES — 10 fixtures in `tests/fixtures/math_engine/volatility_scaling/` | `tests/math_engine/test_volatility_scaling.py` | PASS |
| `calculate_14d_atr_pct` | YES — 10 fixtures in `tests/fixtures/math_engine/atr/` | `tests/math_engine/test_atr.py` | PASS |
| `compute_time_squeeze_decay` | YES — 6 fixtures in `tests/fixtures/math_engine/time_squeeze_decay/` | `tests/math_engine/test_time_squeeze_decay.py` | PASS |
| `compute_para_arm_decision` | YES — 9 fixtures in `tests/fixtures/math_engine/parabolic_squeeze/` | `tests/math_engine/test_parabolic_squeeze.py` | PASS |
| `compute_active_trailing_stop` | YES — 12 fixtures in `tests/fixtures/math_engine/active_trailing_stop/` | `tests/math_engine/test_active_trailing_stop.py` | PASS |
| `compute_breakeven_update` | YES — 17 fixtures in `tests/fixtures/math_engine/breakeven_update/` | `tests/math_engine/test_breakeven_update.py` | PASS |
| `compute_exit_confirmation` | YES — 17 fixtures in `tests/fixtures/math_engine/exit_confirmation/` | `tests/math_engine/test_exit_confirmation.py` | PASS |
| `compute_vwap_signals` | YES — 13 fixtures in `tests/fixtures/math_engine/vwap_signals/` + zero-volume tests | `tests/math_engine/test_vwap_signals.py`, `test_vwap_signals_zero_volume.py` | PASS |
| `compute_vwap_bleed_arm_threshold` | YES — 10 fixtures in `tests/fixtures/math_engine/vwap_bleed_arm/` | `tests/math_engine/test_vwap_bleed_arm.py` | PASS |
| `compute_vwap_breakdown_update` | YES — fixtures in `tests/fixtures/math_engine/vwap_breakdown/` | `tests/math_engine/test_vwap_breakdown.py` | PASS |
| `run_monte_carlo` | YES — multiple tests covering: seeding, early-window exclusion, fallbacks, sentinel blast-radius, standardization, gating | `tests/math_engine/test_mc_*.py` (7 files) | PASS |
| `compute_cvar_5pct_general_distribution` + `compute_portfolio_cvar` | YES — `tests/fixtures/math/m2_cvar_known_pool.json` (closed-form derived) | `tests/engine/test_m2_cvar_known_pool.py` | PASS |
| `compute_crra_utility` + `compute_crra_eu_objective` | YES — `tests/fixtures/m1-wealth-argument/derivation-fixture.json` (hand-derived) | `tests/autotuner/test_m1_crra_eu_objective.py` | PASS |
| `compute_regime_match_quality` | YES — property tests in `tests/math_engine/test_mc_regime_match_quality.py` | `test_mc_regime_match_quality.py` | PASS |
| `_compute_rolling_spy_vol` | YES — curated scenario tests with reference loop comparison | `tests/math_engine/test_perf001_rolling_vol_vectorization.py` | PASS |
| `filter_sortino_sentinels` | YES — covered in `tests/autotuner/test_c4_haircut_gates_selection.py` | `test_c4_haircut_gates_selection.py` | PASS |
| `derive_cycle_mc_seed` | YES — seeding tests | `tests/math_engine/test_mc_rng_seeding.py` | PASS |

**FINDING F-COV-1 (LOW severity): `compute_tp_confirmation` lacks a dedicated golden-fixture test file.**

Grep confirmed:  
- No `tests/fixtures/math_engine/tp_confirmation/` directory exists.
- No `tests/math_engine/test_tp_confirmation.py` file exists.
- Coverage exists only indirectly via blast-radius tests (`test_mc_sentinel_blast_radius_coverage.py` covers `mc_available=False` paths) and integration-level tests in `tests/autotuner/test_c3_replay_tp_rearm.py` (structural/API contract tests).

The project rule is: "Every change to math layers requires a golden-fixture test." The `compute_tp_confirmation` function was extracted from `alpha_bot_execution.py` and lives in `math_engine.py`. It has not been given dedicated golden-fixture coverage at the unit level for its core state-machine branches (arm → count → hit with return > 0; arm → count → miss on return <= 0; already-triggered bypass; sub-threshold-while-armed reset).

**This is a genuine gap against the project rule.**

---

## (e) `resolve_trigger_priority` — Coherence, Totality, Safety

**`resolve_trigger_priority` — PASS**

Location: `math_engine.py:836–859`.

**Priority order declared:** `["VWAP Breakdown", "Take-Profit", "VWAP Bleed Cut", "Trailing Stop"]` at `math_engine.py:828–833`.

**Totality:** The function iterates `_TRIGGER_PRIORITY_ORDER` (a fixed 4-element list) and collects all `True` entries in priority order. `fired[0]` is the winner, `fired[1:]` are co-fired. If `fired` is empty, returns `(None, [])`. The function handles:
- 0 triggers → `(None, [])`. PASS.
- 1 trigger → `(winner, [])`. PASS.
- 2–4 triggers → `(winner, [co-fired...])`. PASS.
- All 4^4 = 16 combinations are reachable by construction; no branch is unreachable.

**No contradictory branches:** The if/else uses a simple `fired = [name for name in _TRIGGER_PRIORITY_ORDER if flag_map[name]]` list comprehension. There are no conditional logic errors or unreachable code paths.

**Safety:** The function is pure — no I/O, no side effects. It cannot trigger a live trade; it only returns a label and a list. Live-trade side effects are dispatched by the caller (`alpha_bot_execution.py`) and are outside this function's scope.

**Priority order justification gap (pre-existing, not a new finding):** The placement of "Take-Profit" ahead of "VWAP Bleed Cut" has no on-file first-principles justification (vision-audit OQ-1; `math_engine.py:826–833` comment cites "H2 acceptance criteria" document). This is a documented design gap, not a code correctness issue.

---

## Summary of Findings

| Finding | Severity | Description |
|---|---|---|
| F-COV-1 | LOW | `compute_tp_confirmation` lacks a dedicated golden-fixture test file. Project rule ("every math layer requires golden-fixture test") is not satisfied. |
| I-1 | INFORMATIONAL | `NO_VOLUME = 0` at `math_engine.py:620` is a function-local named constant, not module-level. Satisfies spirit of the project rule. No action required. |

No CRITICAL, HIGH, or MEDIUM findings. All eight math layers are numerically correct, all non-structural numeric literals are named with source comments, all edge cases are guarded, and the exit-decision function is total and safe.

The sole genuine gap is **F-COV-1**: `compute_tp_confirmation` is under-covered at the golden-fixture unit test level.
