# Math-Engine Semantic Invariant Audit

**Date:** 2026-05-13
**Auditor role:** quant-risk-researcher (READ-ONLY for code; doc-only artifact)
**Scope:** `math_engine.py` (entire module) and all tests under `tests/math_engine/`
**Posture:** Real-money production engine. Bias of this audit: name MISSING invariants whose violation could cause monetary loss or operator confusion. The audit does NOT propose implementation changes; it identifies gaps and recommends test shapes.

---

## Audit Framing

### What the existing test suite DOES well

| Pattern | Count (approx) | What it catches |
|---|---|---|
| Behavioral-equivalence pins (fixture-derived) | ~80 fixtures across 11 functions | Algorithmic divergence in extracted code vs inline producer |
| AST magic-number scanners | 1 per layer | Bare literals re-introduced in math layer |
| Module-level constant existence assertions | 1 per layer | Constants silently renamed/removed |
| Strict `type() is float|int|bool` return-type guards | 1 per layer | numpy scalars leaking into SQLite/JSON serialization |
| Monotonicity-in-input properties (vol, time, ATR) | 4 functions | Sign flips, non-monotone transforms |
| Output range / clamp-window properties | 2 functions (decay, bleed-arm) | Clamp inversions |
| Mutation-purity tests (input not mutated) | 3 functions | Pure-function discipline |
| Determinism (repeat-call) checks | 8 functions | Hidden state / RNG leaks |
| Boundary semantics in state machines | 2 functions (VWAP, exit) | Strict-vs-inclusive operator confusion |
| Latching one-way invariant | 1 function (breakeven) | Lock unlocks accidentally |
| Guard-absoluteness (triggered/not-armed short-circuit) | 3 functions | Re-firing on already-exited positions |
| System-independence within state machine | 1 function (vwap_breakdown) | Cross-contamination of two sub-systems |

### What the existing test suite does NOT cover (the audit subject)

The tests pin **what each math function returns**, but most do not pin **what the SEQUENCE of calls from the live engine must look like over a position's lifetime**. Per-call invariants are well covered; cross-call (temporal) invariants are not — because no pure math function carries state. Those invariants live in `alpha_bot_execution.py` and are not asserted anywhere.

Additionally, several per-call invariants that would be cheap to add are absent:
- NaN / Inf input rejection (or explicit "garbage in, garbage out" pinning)
- Numerical bounds on intermediate quantities (probabilities not in [0,100], stop distance not negative)
- Path-coverage assertions for branches that have no fixture (the `has_missing_data` early-exit in ATR; the `recent_close <= 0` branch in ATR; the `len(spy_returns) >= MC_VOL_WINDOW_DAYS - 1` else-branch in MC)

`[Confidence: High]` — observation is direct from reading every test file and every function.

---

## Function-by-Function Gap Matrix

### F1. `compute_para_arm_decision(current_return, prev_return, para_threshold, currently_armed) -> (velocity, should_arm)`

**Tested invariants:**
- Velocity equals exact subtraction (8 fixture rows + property sweep). `[Backtest+Property]`
- `should_arm iff velocity >= threshold AND not currently_armed` (8 fixtures + property). `[Backtest+Property]`
- `currently_armed=True` suppresses arming unconditionally (7-input sweep). `[Property]`
- Monotonicity in `current_return` at fixed prev/threshold. `[Property]`
- Determinism, input-non-mutation, return-type contract (float, bool). `[Property]`

**Missing invariants:**
- **[MEDIUM-RISK] NaN/Inf handling.** If `current_return` or `prev_return` is NaN, velocity is NaN; the comparison `NaN >= threshold` returns False, so the arm decision silently never fires even when math says it should. There is no test pinning this regime. The contract assumes caller normalizes; but no test asserts that contract is held in production callers. `[Replication: N/A — theoretical]`
- **[LOW-RISK] Boolean coercion of truthy-int `currently_armed`.** The active-trailing-stop test exercises int-in-place-of-bool; this function does not. Trivial gap.

**Could it fail in production?** NaN inputs are plausible if `bot_state` is freshly initialized or if a network glitch yields `None -> 0/0` upstream. Low likelihood but non-zero.

---

### F2. `compute_time_squeeze_decay(time_ratio) -> (dynamic_multiplier, dynamic_min_stop)`

**Tested invariants:**
- 6 golden fixtures (boundary, mid, constructed-exact). `[Backtest]`
- Boundary contracts at `time_ratio=0` and `=1` reference module constants. `[Property]`
- Monotonically non-increasing across `[0,1]`. `[Property]`
- Output ranges clamped to `[MULT_CLOSE, MULT_OPEN]` and `[MIN_STOP_CLOSE, MIN_STOP_OPEN]`. `[Property]`
- Determinism, no-mutation, strict-float return type. `[Property]`

**Missing invariants:**
- **[HIGH-RISK] Out-of-domain inputs are explicitly undocumented as folklore.** The docstring says "CALLER clamps before passing; this function does not validate." The tests do not pin behavior for `time_ratio > 1` or `time_ratio < 0`. The math is `log10(1 + 9*tr)`; for `tr <= -1/9` (i.e., `tr < -0.111`), the function raises ValueError or returns NaN/Inf. For `tr` between `-1/9` and 0, `decay_curve < 0`, which makes `dynamic_multiplier > MULT_OPEN` — exceeding documented bounds AND violating the at-open contract. No test catches an upstream bug that produces a slightly-negative `time_ratio` (e.g., clock drift before market open, daylight-saving boundary edge case). `[Theoretical — replication N/A]`
- **[LOW-RISK] No assertion that `dynamic_min_stop < dynamic_multiplier`.** Currently `MULT_CLOSE = 0.5 > MIN_STOP_CLOSE = 0.15` so this holds, but no test pins it; a future constant tweak could create the absurd state where the multiplier is below the floor and the `max()` in `compute_active_trailing_stop` always wins.

**Literature:** Time-decay tightening of trailing stops is folklore in the practitioner community (e.g., end-of-day mean-reversion squeezes), but no peer-reviewed treatment was located. `[Folklore — moderate adoption / low formal evidence]`. The `log10(1 + 9*t)` shape is project-specific.

`[Replication Status: Unknown for the log10 decay curve — appears to be a project-internal heuristic.]`

---

### F3. `compute_active_trailing_stop(symphony_vol, dynamic_multiplier, dynamic_min_stop, para_armed, breakeven_locked, parabolic_squeeze_multiplier) -> float`

**Tested invariants:**
- 13 golden fixtures covering vol-floor-wins, vol-scale-wins, exact tie, vol<=0 fallback, squeeze OR semantics, squeeze=0, squeeze=1. `[Backtest]`
- No-flags-set → squeeze ignored across 7 squeeze values. `[Property]`
- Either-flag → squeeze fires exactly once (no double-multiply). `[Property]`
- Non-positive `symphony_vol` → VOL_FALLBACK substitution. `[Property]`
- OR symmetry: para-only equals breakeven-only. `[Property]`
- VOL_FALLBACK named, not bare-literal in body. `[AST-scanner]`
- Determinism + strict-float return. `[Property]`
- Int-as-bool documentary stress. `[Property]`

**Missing invariants:**
- **[HIGH-RISK] Non-negative output guarantee.** A negative `dynamic_multiplier` (caller bug: passed unclamped `time_ratio > 1`) or a negative `parabolic_squeeze_multiplier` produces a NEGATIVE `active_trailing_stop`. The caller then computes `base_stop_level = safe_hwm - active_trailing_stop` (alpha_bot_execution.py:592). A negative active_trailing_stop pushes `base_stop_level` ABOVE `safe_hwm` — meaning the engine arms an exit trigger at a return level above the high-water mark, which can fire instantly. This is a money-loss path. No test pins `active >= 0` or rejects negative multipliers. `[Theoretical — high confidence; the arithmetic is direct.]`
- **[HIGH-RISK] Non-NaN output guarantee.** If any input is NaN, output is NaN; downstream `current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT` becomes `NaN <= ... = False`, silently disabling the trailing stop. No test pins NaN-rejection or NaN-output behavior. `[Theoretical]`
- **[MEDIUM-RISK] Squeeze multiplier semantic bounds.** The function does not assert `0 < parabolic_squeeze_multiplier <= 1` (the design intent of "squeeze" is to TIGHTEN, i.e., multiply by < 1). A buggy autotuner trial that selected `MAX_PARABOLIC_SQUEEZE = 2.0` would LOOSEN the stop when armed — semantically inverse of the design. No test pins the [0, 1] expected range for the squeeze multiplier.
- **[LOW-RISK] Idempotence-under-equal-flag-combinations.** Both-flags-True vs either-flag-True produce the same output (tested via `or` symmetry); a stricter test would assert that `active = sq * base` exactly once across all 3 flag combinations rather than just two-by-two.

**Literature:** Volatility-scaled trailing stops are well-established. Robert Carver, *Systematic Trading* (2015), and Andreas Clenow, *Following the Trend* (2013), both describe ATR-scaled stops as the practitioner default. `[Expert / High adoption]`. Empirical: López de Prado (2018) discusses volatility-targeting envelopes in *Advances in Financial Machine Learning* Ch. 14 with cited Sharpe-ratio improvement, but the specific Open->Close intraday tightening via log-decay is NOT covered.

`[Replication Status: vol-scaled exits — Yes (multiple references). Log-decay tightening — Unknown.]`

---

### F4. `compute_breakeven_update(current_return, symphony_vol, base_stop_level, current_hold_ticks, currently_breakeven_locked, is_triggered) -> (new_hold_ticks, new_breakeven_locked, stop_trigger_level)`

**Tested invariants:**
- Behavioral fixtures covering activation clamp, deadband, lock transition, triggered override. `[Backtest]`
- **LATCHING INVARIANT: locked stays locked** across 8 input combinations. `[Property — load-bearing]`
- Ticks increment by exactly +1 when return qualifies; reset to 0 on disqualify (not decrement) — both swept across 10 starting counts. `[Property]`
- Triggered override is absolute (stop_trigger_level == TRIGGERED_OVERRIDE_LEVEL) across 7 input combinations. `[Property]`
- Strict return-type contract `(int, bool, float)`. `[Property]`
- Constants named at module scope; no bare domain literals in body. `[AST-scanner]`
- Determinism. `[Property]`

**Missing invariants:**
- **[HIGH-RISK] Stop-trigger-level non-decrease across a sequence when locked.** Once `breakeven_locked=True`, future calls compute `stop_trigger_level = max(base_stop_level, 0.0)`. If `base_stop_level` later drops below 0 (because `safe_hwm` retraced after locking, even though the engine treats HWM as monotone — see HWM concern in F-cross below), the floor=0 protects. BUT if `base_stop_level` is later ABOVE zero and rises further, the trigger level can RISE — which is the desired ratchet. There is no test asserting that within a locked sequence, `stop_trigger_level_{t+1} >= stop_trigger_level_t` when `is_triggered=False`. The latching test covers the lock bit; it does NOT cover the floor's monotonicity. This is the canonical "trailing stop never loosens" invariant from the literature ([Glynn & Iglehart trailing-stop characterization; Fu & Zhang 2010, SemanticScholar](https://www.semanticscholar.org/paper/Is-the-trailing-stop-strategy-always-good-for-stock-Fu-Zhang/b256975ce24b4945532d1a9f2f3554b4b16bb69c)). `[Theoretical + Literature-supported]`
- **[HIGH-RISK] No assertion on the `currently_breakeven_locked=False -> new_breakeven_locked=True` transition trigger being EXCLUSIVELY `new_hold_ticks >= THRESHOLD`.** A bug that triggered lock on `current_return > some_other_value` would still pass all current tests if it ALSO fired at tick 5 — because no negative test pins "lock CANNOT activate before tick 5 even if return is extraordinary." Add a test asserting at hold_ticks=4 (about to cross), even extreme `current_return = 1e6` does not lock.
- **[MEDIUM-RISK] Clamp boundary on `symphony_vol`.** `dynamic_activation = clamp(vol, 0.4, 3.0)`. At `vol=0.4` exactly, `dynamic_activation = 0.4`, threshold = 0.2. At `vol=3.0` exactly, threshold = 2.8. Neither boundary has a fixture explicitly named "vol-at-MIN-exact" or "vol-at-MAX-exact." Fixtures imply they are tested, but a focused boundary fixture is missing.
- **[LOW-RISK] No-mutation test.** Other math functions test purity via deep-copy snapshot; this function takes only scalars so the test is moot — but a comment to that effect would be useful.

**Literature:** The "breakeven stop after N qualifying ticks" pattern is widely-practiced but NOT formally validated in academic literature. Robert Carver discusses HWM-based protective stops in *Systematic Trading*; the specific `dynamic_activation = clamp(vol, MIN, MAX)` formula is project-specific. `[Folklore — moderate adoption / low formal evidence]`.

`[Replication Status: HWM-based protective stops — Yes (practitioner literature). Specific clamp formula — Unknown.]`

---

### F5. `compute_exit_confirmation(armed, is_triggered, current_return, stop_trigger_level, prob_beating, current_below_stop_count) -> (new_count, is_trailing_stop_hit)`

**Tested invariants:**
- 7-input guard sweep for `not armed`. `[Property]`
- 7-input guard sweep for `is_triggered=True`. `[Property]`
- Count increments by exactly +1 when condition met (10 starting values). `[Property]`
- Count resets fully to 0 when condition fails (10 starting values). `[Property]`
- Truth-table for `hit` across armed/triggered/condition/starting-count (13 rows). `[Property]`
- Constants named; no bare literals. `[AST-scanner]`
- Determinism + strict-(int, bool) return. `[Property]`

**Missing invariants:**
- **[HIGH-RISK] Below-stop counter monotonicity on the "below-stop" stretch.** When `condition_met` holds for K consecutive calls, the counter must rise monotonically from `starting_count` to `starting_count + K`. The single-step increment test covers this in isolation, but no test simulates a sequence of (K, 1) calls and asserts the cumulative rise. Subtle bugs like "reset on first hit" or "saturate at EXIT_CONFIRM_TICKS" would only manifest across calls. `[Theoretical]`
- **[HIGH-RISK] `prob_beating` range trust.** The function treats `prob_beating < 60.0` as a "MC sanity passes" gate. There is no test asserting behavior when `prob_beating` is outside `[0, 100]` (the MC layer can in principle produce values outside this range under degenerate inputs — see F-MC). A negative `prob_beating` would always pass the gate, possibly accelerating exits inappropriately. A `prob_beating > 100` (e.g., 100.0 sentinel from MC short-circuit) would always block exits — by design, but the test suite does not pin "100.0 sentinel blocks exit" specifically. `[Theoretical]`
- **[MEDIUM-RISK] No fixture for the `current_below_stop_count = EXIT_CONFIRM_TICKS - 1` boundary with condition_met=True asserting hit fires.** The truth-table covers `starting_count=2 -> hit=True`, which is exactly this boundary, so this is actually covered. Strike.
- **[MEDIUM-RISK] No test that `is_trailing_stop_hit=True` implies the trigger condition was structurally met.** The truth table asserts the truth values but does not assert the IMPLICATION direction. A "free pass" bug where `hit=True` could occur without `below_stop_condition` would be caught by the existing truth-table. Strike.
- **[LOW-RISK] No test that `hit=True` for one call does NOT mean `hit=True` next call automatically.** The function is stateless, so the caller must persist the state correctly. No test asserts this; but it's not the math layer's responsibility.

**Literature:** Multi-tick exit-confirmation gates (the "wait N ticks before honoring a breach") is widely used to filter single-bar noise. Empirical: Kaminski & Lo (2014), "When Do Stop-Loss Rules Stop Losses?", Journal of Financial Markets, found that hard stop-losses without confirmation produce noisy exits in mean-reverting regimes. Confirmation logic is a folklore answer. `[Folklore — high adoption; Kaminski & Lo cited the problem but did not validate the specific 3-tick fix.]`

`[Replication Status: Multi-tick filters — Yes (practitioner level). Specific 3-tick threshold + MC-veto combination — Unknown / project-specific.]`

---

### F6. `compute_vwap_signals(holdings, live_vwaps) -> (weighted_vwap_diff, valid_vwap_weight)`

**Tested invariants:**
- 5-row "all-skipped → exact zero/zero" property. `[Property]`
- Empty-holdings → exact (0.0, 0.0). `[Property]`
- 5-row "weight equals sum of qualifying allocations." `[Property]`
- 5-row "diff equals sum of `alloc * (p-v)/v` over qualifying." `[Property]`
- Non-mutation of holdings list + dicts (deep-copy comparison). `[Property]`
- No silent injection of `ticker` key when only `working_ticker` present. `[Property]`
- Determinism + strict-float return + strict-float-on-empty-holdings. `[Property]`
- No domain magic numbers. `[AST-scanner]`

**Missing invariants:**
- **[MEDIUM-RISK] Pathological `(p - v) / v` when `v` is tiny.** The strict guard `v > 0` admits `v = 1e-300`, which produces astronomical `(p-v)/v` values. No test pins a sanity ceiling. In production, Composer/Alpaca VWAP feeds should not produce tiny-positive VWAP, but a stale-data glitch could. `[Theoretical — failure mode is "huge diff that triggers spurious VWAP breakdown."]`
- **[MEDIUM-RISK] Negative allocations not pinned.** The volatility-scaling test sweeps `[0.25, 0.5, 1.0, 2.0]` but not negatives. The VWAP signals function does no clamping. Negative allocation would flip the sign of the contribution; no test asserts whether this is allowed. Production Composer payloads should not produce negatives, but no test pins the contract.
- **[LOW-RISK] No test for very large holdings list (100+).** Performance / numerical-stability is unpinned. Strike for risk purposes; relevant for performance audit only.

**Literature:** VWAP-deviation as an intraday signal is well-established (Madhavan 2002, *Journal of Trading*). Allocation-weighted portfolio VWAP is a routine extension. `[Expert / High adoption]`

`[Replication Status: VWAP-deviation signals — Yes; the specific gate threshold of 0.5 weight coverage — project-specific.]`

---

### F7. `compute_vwap_bleed_arm_threshold(symphony_vol, bleed_multiplier) -> float`

**Tested invariants:**
- 15-row clamp-window invariant (output always in `[-3.0, -0.5]`). `[Property]`
- 8-row non-positivity invariant. `[Property]`
- 15-vol-point monotonicity (non-increasing in vol for fixed positive multiplier). `[Property]`
- Determinism, plain-float return (4-path), constants named + source-commented (sign-safe runtime + AST check), no bare clamp literals in body. `[Property + AST-scanner]`

**Missing invariants:**
- **[LOW-RISK] No test for `bleed_multiplier <= 0`.** Negative multiplier flips the sign of raw, and the clamp pulls everything to -0.5 (since raw > 0 → min(-0.5, raw) = -0.5). This is the SAFEST possible behavior but is undocumented as such. Add a test pinning "negative or zero multiplier → result is VWAP_BLEED_ARM_MAX." `[Theoretical]`
- **[LOW-RISK] Cross-function invariant with `compute_vwap_breakdown_update` System B**: the bleed_arm threshold value flows into System B's `current_return <= vwap_bleed_arm_pct`. No integration test asserts that the clamp window guarantees System B never arms above -0.5 (preventing accidentally-shallow bleed exits). The unit invariant covers this, but documenting the cross-function consequence in a test header would help reviewers.

**Literature:** Bleed-style exit (cumulative drift below entry) is folklore in trend-following circles. No formal academic treatment located. `[Folklore — low formal evidence]`

`[Replication Status: Unknown.]`

---

### F8. `compute_vwap_breakdown_update(...10 inputs...) -> (new_a_ticks, new_b_ticks, is_broken, is_bleed_broken)`

**Tested invariants:**
- TRIGGERED absoluteness (5 rows of would-fire-but-triggered). `[Property — load-bearing]`
- GATE absoluteness (7 gate-fail rows × 4 prev-state rows = 28 cases). `[Property]`
- System A increment +1 / reset to 0 (9 starting values each). `[Property]`
- System B increment +1 / reset to 0 (9 starting values each). `[Property]`
- System A truth-table for `is_vwap_broken` (8 rows). `[Property]`
- System B truth-table parameterized by threshold (12 rows). `[Property]`
- System A inputs don't affect System B outputs across 5 A-regimes. `[Property]`
- System B inputs don't affect System A outputs across 4 B-regimes. `[Property]`
- Determinism, strict-(int,int,bool,bool) return across 5 code paths. `[Property]`
- Module-level naming + negative naming (bleed-ticks NOT a module constant) + source comments. `[AST-scanner]`

**Excellent coverage. This is the most thoroughly-pinned function in the module.**

**Missing invariants:**
- **[MEDIUM-RISK] System A bug class: `safe_hwm` regressions.** The producer treats `safe_hwm` as monotone (always equal to current HWM); System A then asserts `safe_hwm >= vwap_cross_hwm_pct AND current_return < safe_hwm`. If `safe_hwm` is ever LESS than `current_return` (which "shouldn't happen" but no test asserts it), System A would not arm even on a genuine breakdown. No test in `tests/math_engine/` pins the upstream "safe_hwm >= current_return" invariant — and that invariant lives in `alpha_bot_execution.py` (line 524: `if current_return > shadow_hwm: shadow_hwm = current_return`). This is a cross-file invariant, not pinned anywhere as a test.
- **[LOW-RISK] No test for `vwap_bleed_ticks_threshold <= 0`.** Threshold of 0 would make `new_b >= 0` always True after even one qualifying tick, causing immediate bleed-break. A negative threshold would make every tick a break. No test pins behavior at degenerate threshold. Caller-contract issue.
- **[LOW-RISK] `safe_hwm == vwap_cross_hwm_pct` boundary.** The producer uses `>=` for this; existing fixtures should cover exact equality, but a focused boundary fixture wasn't located in this audit.

---

### F9. `run_monte_carlo(holdings, historical_data, spy_today_return, simulation_paths, neighbor_k) -> probability`

**Tested invariants:**
- 7 behavioral-equivalence fixture pins (zero tolerance — exact byte equivalence across regimes including insufficient-history, boundary, low/high vol, argpartition active, extreme today-return, constant-zero, non-default kwargs). `[Backtest — zero-drift pinning]`
- All magic numbers extracted to module-level constants; AST canary asserts none re-introduced. `[AST-scanner]`
- Default kwargs reference module-level constant Names (not bare literals). `[AST-scanner]`

**Missing invariants — THIS IS THE HIGHEST-RISK FUNCTION IN THE MODULE for missing properties:**

- **[HIGH-RISK] Probability output range is NOT pinned to `[0, 100]`.** The math: `((sim_paths - below_count) / sim_paths) * 100.0`. `below_count = np.searchsorted(sim_results, current_symphony_return)`, which returns a value in `[0, len(sim_results)]`. If `sim_results` is empty (`simulation_paths=0`), `np.random.choice` raises. If `simulation_paths` is negative, `np.random.choice` raises. So the range IS structurally `[0, 100]` for valid kwargs — but no test pins it. A caller bug that passes `simulation_paths=0` would crash inside MC; no test asserts whether that is the desired contract or whether it should be handled. **Downstream consumer**: `compute_exit_confirmation` uses `prob_beating < MC_SANITY_THRESHOLD`. If MC ever returns a probability outside `[0, 100]`, the sanity gate semantics degrade silently. `[Theoretical, but the input contract is the bug surface.]`
- **[HIGH-RISK] NaN inputs propagate silently.** `current_symphony_return = sum(...)` over `last_percent_change` — if any holding has `last_percent_change = NaN`, this becomes NaN and `np.searchsorted(sim_results, NaN)` returns `len(sim_results)`, producing probability 0.0 — which means "definitely going to lose," which TIGHTENS the exit gate. The opposite-sign bug (NaN → 100.0) is also possible depending on numpy version. No test pins NaN-input behavior. This is a production-realistic regime if Composer ever returns null for a percent_change. `[Theoretical — high impact, plausible trigger.]`
- **[HIGH-RISK] RNG seeding contract.** The function calls `np.random.choice` without seeding. Behavioral-equivalence tests seed externally. Production calls do not seed, so each call produces a different probability for identical inputs (within bounds). The function is therefore NOT deterministic. There is no test asserting "given X holdings + Y history, the MC probability is within band Z" — which is the only stable property that survives the stochastic call. A consequence: a single noisy MC sample near 60.0 can flip the exit-confirmation gate randomly. CLT and `simulation_paths=5000` mitigate this but do not eliminate it. **Recommend a property test: `for N independent calls, the MC probability standard deviation is below threshold S`** to pin the stochastic stability. `[Theoretical + literature-supported; López de Prado 2018 Ch. 11 discusses simulation noise.]`
- **[HIGH-RISK] kNN tie-breaking is unpinned.** `argpartition` does not guarantee a specific order for equal distances; tied distances may produce different `nearest_indices` across numpy versions. Behavioral-equivalence tests use a fixed seed AND a fixed numpy version (whatever pytest is running). A numpy upgrade could perturb byte-equivalence without changing the math contract. The behavioral pins are STRONGER than the contract here — a violation of byte equivalence on a numpy upgrade would block CI without indicating a real bug. `[Replication: Unknown for argpartition stability across numpy versions.]`
- **[MEDIUM-RISK] No assertion that MC short-circuit (`< MC_MIN_HISTORY_DAYS`) returns the SENTINEL.** The behavioral fixtures cover this for one regime, but no property-style "for any history with len < 20, the result equals MC_INSUFFICIENT_HISTORY_PROB" test exists. `[Theoretical — covered indirectly.]`
- **[MEDIUM-RISK] kNN when `len(distances) <= neighbor_k` falls through to all-indices.** The branch is tested via the boundary fixture, but no property asserts that the result is still in `[0, 100]` in this small-history regime.
- **[MEDIUM-RISK] Symphony return uses `* PCT_SCALAR` while raw return matrix also uses `* PCT_SCALAR`.** Units are consistent. No test asserts the units match — if a future refactor changed one but not the other, the searchsorted would compare apples to oranges. Add a unit-consistency test.

**Literature:**
- Kaminski & Lo (2014), "When Do Stop-Loss Rules Stop Losses?" *Journal of Financial Markets* — empirical evidence that simple stop-losses underperform without regime-conditioning, but did not validate MC-based gates specifically.
- López de Prado (2018), *Advances in Financial Machine Learning* Ch. 11 — discusses simulation-based exit gates and CLT-based stability requirements. Recommends `simulation_paths >= 1000` for stability; AlphaBot uses 5000. `[High adoption / High evidence for MC stability; Low formal evidence for the specific kNN-regime-locality gating.]`
- The `kNN-by-(SPY return, SPY vol)` is a project-specific regime-locality heuristic. No replication located. `[Folklore — project-internal.]`

`[Replication Status for MC-gated exits as a class: Yes (López de Prado, Kaminski & Lo). For the specific kNN-on-2D-distance formulation: Unknown.]`

---

### F10. `calculate_20d_vol(holdings, historical_data) -> float`

**Tested invariants:**
- 8 derived golden fixtures (all 8 regimes from the design list). `[Backtest — high quality, derivation-based]`
- Non-negativity across 7 amplitudes. `[Property]`
- Monotonicity in amplitude across 8 amplitudes. `[Property]`
- Insufficient-history → 0.0 across 0..19 days. `[Property]`
- Lookback-slice respected (last-20-of-40). `[Property]`
- Linearity in single-holding allocation. `[Property]`
- Constants named + AST-scanner. `[AST-scanner]`

**Missing invariants:**
- **[MEDIUM-RISK] Multi-holding aggregation linearity.** Linearity is tested for a SINGLE holding. Multi-holding sums-then-vol is structurally different (correlation matters). No test covers two-holding correlation regimes (perfectly correlated, anti-correlated, zero-correlation). For a portfolio of N correlated holdings, the realized vol is NOT a linear sum of per-holding vols. The function computes `daily_returns = returns_matrix.dot(weights) * 100`, then `np.std(daily_returns)` — which IS correct for portfolio vol, but no test asserts the anti-correlation case (where portfolio vol should be LOWER than either component).
- **[MEDIUM-RISK] No test for non-SPY missing-ticker fallback.** When a holding's ticker is missing on a date, the function falls back to SPY's `daily_ret`. The vol-scaling tests include `spy_only_alternating` fixture, but no property explicitly asserts "missing ticker uses SPY substitute" across multiple dates.
- **[LOW-RISK] `np.std` uses ddof=0 (population stdev), not ddof=1 (sample stdev).** Industry convention varies. The behavioral pins lock it to ddof=0. No test header documents this choice or rationale. `[Theoretical — convention.]`

**Literature:** 20-day realized vol is a standard practitioner window. The choice between ddof=0 and ddof=1 is a minor convention. `[Expert / High adoption.]`

---

### F11. `calculate_14d_atr_pct(holdings, historical_data) -> float`

**Tested invariants:**
- Multiple derived golden fixtures (constant-range, linear, alternating-range, zero-final-close fallback, SPY-only fallback). `[Backtest]`
- Non-negativity across 7 half-ranges. `[Property]`
- Monotonicity in range amplitude across 8 half-ranges. `[Property]`
- Insufficient-history short-circuit (0..14 days → 0.0 via vol fallback). `[Property]`
- Lookback-slice respected (last-15-of-30, volatile-tail variant). `[Property]`
- Lookback-slice slicing-excludes-earlier-volatile. `[Property]`
- Single-holding linearity. `[Property]`
- Multi-holding aggregation linearity (covers 4 weight pairs including over-allocated 2.0 + 3.0). `[Property]`
- Constants named + AST-scanner. `[AST-scanner]`

**Missing invariants:**
- **[HIGH-RISK] The `has_missing_data` early-exit and the `recent_close <= 0` fallback both call `calculate_20d_vol`, but the contract of "ATR is approximately equal to 20-day vol when both can be computed" is NOT pinned.** If the fallback produces wildly different magnitudes, position sizing changes drastically when a single OHLC field is briefly missing. No test asserts "fallback magnitude is comparable to ATR magnitude" — the two metrics are unit-compatible (both percentage points) but not numerically equivalent. A continuity violation here would cause stop distances to jump on missing data, potentially triggering false exits. `[Theoretical — high impact.]`
- **[MEDIUM-RISK] The `recent_close <= 0` branch is structurally unreachable in normal market data (prices are positive) but is implemented defensively. There IS a test fixture for `ohlc_constant_with_zero_final_close` that hits this branch, but no property test asserts what the result IS for a sweep of close-near-zero values.
- **[LOW-RISK] True-range formula edge cases.** TR is `max(H-L, |H-Cprev|, |L-Cprev|)`. Gap-up and gap-down days should produce large TR contributions; the alternating-range fixture exercises this implicitly, but no test names "gap day" as a regime explicitly. Folklore: ATR's strength is exactly this gap-handling vs naïve high-low range.

**Literature:** ATR (J. Welles Wilder, *New Concepts in Technical Trading Systems*, 1978) is a foundational practitioner indicator. `[Expert / Very high adoption]`. Wilder's original used a 14-day smoothed average via Wilder's smoothing; the AlphaBot implementation uses a simple mean over 14 TRs — a minor variation, semantically the same in steady state, different in convergence after a shock. No test pins which smoothing method is in use; behavioral fixtures lock it.

`[Replication Status: ATR — Yes (decades of practitioner adoption). Specific 14-day simple mean vs Wilder's smoothing — project-specific choice.]`

---

## Cross-Function (Temporal / Lifecycle) Invariants — Not Covered Anywhere

These invariants span multiple math-function calls across a position's lifetime. They live in `alpha_bot_execution.py` and have NO test coverage in `tests/math_engine/`. Many depend on caller correctness, but the math layer's contract assumes caller correctness without verifying.

### CX-1. `[HIGH-RISK]` `stop_trigger_level` monotonicity once `breakeven_locked=True`

**Statement:** After the breakeven lock fires, `stop_trigger_level` produced by successive calls to `compute_breakeven_update` must be non-decreasing while `is_triggered=False`.

**Why missing:** No test simulates a sequence of calls. Math functions are stateless; this requires a "scenario" test that runs the function repeatedly with an evolving `base_stop_level` driven by an evolving `safe_hwm`.

**Could fail in production:** YES. The HWM-tracking lives in `alpha_bot_execution.py:524`. A regression there could cause `safe_hwm` to retrace, which would push `base_stop_level` down, and (if breakeven is locked) the `max(base_stop_level, 0.0)` floor only protects at zero — for `base_stop_level > 0`, the trigger CAN decrease. This violates the standard trailing-stop contract from Fu & Zhang (2010, semanticscholar).

**Cited:** [Fu & Zhang, "Is the trailing-stop strategy always good for stock trading?"](https://www.semanticscholar.org/paper/Is-the-trailing-stop-strategy-always-good-for-stock-Fu-Zhang/b256975ce24b4945532d1a9f2f3554b4b16bb69c) — the "never decrease" property is part of the standard trailing-stop definition. `[Tier-1, Replicated.]`

### CX-2. `[HIGH-RISK]` `safe_hwm >= current_return` always

**Statement:** The high-water mark is by definition the maximum return seen so far; `safe_hwm` must never be less than the current return at the same call.

**Why missing:** Lives in `alpha_bot_execution.py:524`; no math-layer test pins this. `compute_vwap_breakdown_update` System A condition `current_return < safe_hwm` becomes vacuous if the invariant is violated.

**Could fail in production:** Possible if `shadow_hwm` is initialized incorrectly or if the engine restarts mid-day and `shadow_hwm` is reseeded to the current cycle's return (correct) vs to -infinity (suppresses exits silently).

### CX-3. `[HIGH-RISK]` `breakeven_locked=True` persists across cycles in `bot_state`

**Statement:** Once the math layer returns `new_breakeven_locked=True`, the caller MUST write it back to `bot_state` and pass it as `currently_breakeven_locked=True` on the next call.

**Why missing:** Pure-function tests cannot verify caller behavior. No integration test pins the round-trip. The latching invariant in `compute_breakeven_update` is necessary but not sufficient.

**Could fail in production:** YES. A bug in state persistence (e.g., a SQLite write failure that drops the field) would silently re-allow looser stops. The math function's latching test does not catch this.

### CX-4. `[MEDIUM-RISK]` `is_trailing_stop_hit=True` flows to a downstream exit and is not double-counted

**Statement:** Once `is_trailing_stop_hit=True`, the caller MUST set `bot_state[...]['triggered']=True` before the next math call, so `compute_exit_confirmation`'s guard short-circuits.

**Why missing:** No test asserts the caller's wiring. The math function's guard is correct in isolation; cross-call correctness is unverified.

### CX-5. `[MEDIUM-RISK]` MC probability stability across consecutive calls

**Statement:** Two MC calls with the same inputs (and unseeded RNG) should produce probabilities whose standard deviation is below some threshold S (CLT for `simulation_paths=5000`).

**Why missing:** No property test pins MC stochastic stability. A regression that dropped `simulation_paths` (e.g., a config-loader bug substituting 50 for 5000) would silently degrade MC-gate quality.

**Literature:** López de Prado (2018) Ch. 11 — CLT-based requirement for MC stability. `[Tier-1, Replicated.]`

### CX-6. `[LOW-RISK]` Constants do not change at runtime

**Statement:** The module-level constants (LOOKBACK_DAYS, MULT_OPEN, etc.) must not be mutated after import.

**Why missing:** Python allows `math_engine.LOOKBACK_DAYS = 19` at runtime. No test asserts immutability. A future plugin/hook architecture could introduce this risk.

---

## HIGH-RISK Gaps — Prioritized for Next-Cycle Tests

Ranked by money-loss probability × ease-of-fix.

### H1. Stop-trigger-level monotonicity across a locked sequence (`compute_breakeven_update`)
**Recommended test shape:**
```
def test_stop_trigger_level_non_decreasing_when_locked_and_not_triggered():
    # Build a sequence of (current_return, symphony_vol, base_stop_level) tuples
    # representing a position that has locked breakeven, then continues with
    # rising and falling base_stop_level. Assert stop_trigger_level_{t+1} >=
    # stop_trigger_level_t for all t with is_triggered=False.
```
Cite [Fu & Zhang 2010](https://www.semanticscholar.org/paper/Is-the-trailing-stop-strategy-always-good-for-stock-Fu-Zhang/b256975ce24b4945532d1a9f2f3554b4b16bb69c) in test docstring.

### H2. Non-negative active_trailing_stop (`compute_active_trailing_stop`)
**Recommended test shape:**
```
def test_output_is_non_negative_for_valid_multipliers():
    # Sweep across valid input ranges. Assert output >= 0.
def test_output_with_negative_multiplier_documented_or_rejected():
    # Document current behavior. Decision belongs to a separate cycle.
```

### H3. NaN-input policy (`compute_active_trailing_stop`, `compute_exit_confirmation`, `run_monte_carlo`)
**Recommended test shape:** PIN current behavior (NaN propagates and silently disables stops). Either accept the gap and document it, or schedule a cycle to add explicit NaN-rejection.

### H4. MC probability output range pin (`run_monte_carlo`)
**Recommended test shape:**
```
def test_mc_probability_in_zero_to_hundred_for_valid_inputs():
    # Sweep valid history + holdings + spy_today_return. Assert 0.0 <= prob <= 100.0.
def test_mc_short_circuit_returns_sentinel():
    # For any history with len < MC_MIN_HISTORY_DAYS, assert prob == MC_INSUFFICIENT_HISTORY_PROB.
```

### H5. MC stochastic stability (`run_monte_carlo`)
**Recommended test shape:**
```
def test_mc_probability_stable_across_independent_calls():
    # Call N times with the same inputs (no external seeding). Assert stdev < S.
    # S derived from CLT: sigma_p = sqrt(p*(1-p)/N) * 100. For p=0.5, N=5000, sigma_p ~= 0.7%.
    # Assert observed stdev < 5*sigma_p to leave headroom.
```
Cite López de Prado 2018 Ch. 11 in test docstring.

### H6. ATR-vs-vol-fallback continuity (`calculate_14d_atr_pct`)
**Recommended test shape:**
```
def test_atr_fallback_to_vol_produces_comparable_magnitude():
    # Build a history where ATR is computable, capture atr_result.
    # Then build a history where one OHLC field is missing on every day,
    # forcing the vol fallback. Capture vol_result.
    # Assert the two are within a "comparable" band (e.g., a factor of 5).
    # If not, the fallback is a discontinuity that should be flagged.
```

### H7. safe_hwm >= current_return cross-file invariant (lives in alpha_bot_execution.py)
**Recommended test shape:** Lives outside `tests/math_engine/`. Integration test in `tests/integration/` that drives a synthetic position through alpha_bot_execution and asserts the HWM invariant at every cycle.

### H8. prob_beating sentinel (=100.0) blocks exit-confirmation
**Recommended test shape:**
```
def test_exit_confirmation_blocked_when_prob_beating_is_mc_insufficient_history_sentinel():
    # current_return clearly below stop, magnitude condition met.
    # prob_beating = MC_INSUFFICIENT_HISTORY_PROB (=100.0).
    # Assert MC sanity gate blocks the count; new_count = 0, hit = False.
```

---

## MEDIUM-RISK Gaps

### M1. Squeeze multiplier semantic-bounds assertion (`compute_active_trailing_stop`)
Assert `0 < squeeze < 1` is the design intent. Document if not.

### M2. Multi-holding correlation regimes in `calculate_20d_vol`
Add fixtures: perfectly correlated (vol should equal each), anti-correlated (vol should be lower than each), zero-correlated (vol should be intermediate).

### M3. Out-of-domain `time_ratio` for `compute_time_squeeze_decay`
Pin current behavior (mathematically defined but exceeds documented bounds) or raise on out-of-domain input.

### M4. Pathological VWAP `v` near zero (`compute_vwap_signals`)
Add a sanity ceiling for `(p-v)/v` or document the unbounded-output behavior.

### M5. Clamp-boundary fixtures for `compute_breakeven_update` at `symphony_vol = MIN` and `MAX` exact.

### M6. Below-stop counter monotonicity across a sustained breach sequence (`compute_exit_confirmation`).
Multi-call scenario test.

### M7. kNN tie-breaking stability vs numpy version (`run_monte_carlo`).
Pin behavior or add a property assertion that doesn't depend on argpartition's tie order.

### M8. Negative allocation contract in `compute_vwap_signals`.
Pin or document.

### M9. `is_trailing_stop_hit` -> caller writes triggered=True before next call (cross-file).
Integration test.

---

## LOW-RISK Gaps (defensive / aesthetic)

- L1. Boolean coercion of truthy-int `currently_armed` in `compute_para_arm_decision` (parallel to active_trailing_stop's test).
- L2. `dynamic_min_stop < dynamic_multiplier` ordering implicitly assumed.
- L3. `bleed_multiplier <= 0` produces safest behavior — document it.
- L4. `vwap_bleed_ticks_threshold <= 0` degenerate threshold — pin or document.
- L5. `np.std` ddof=0 vs ddof=1 — document the choice in `calculate_20d_vol`.
- L6. ATR gap-day regime named explicitly in a fixture header.
- L7. `recent_close <= 0` branch in ATR has fixture coverage but no property-style sweep.
- L8. Constant immutability at runtime.

---

## Recommended Next-Cycle Tests — Compact List

For each HIGH-RISK gap above (H1–H8), one test file or one new test in the existing per-function file. Estimated test count:

| Gap | Test count | Effort | File location |
|---|---|---|---|
| H1 stop-trigger monotonicity (locked) | 1–2 scenario tests | Low | `tests/math_engine/test_breakeven_update.py` |
| H2 non-negative active_trailing_stop | 1 sweep test | Low | `tests/math_engine/test_active_trailing_stop.py` |
| H3 NaN policy (3 functions) | 3 pinning tests | Low | per file |
| H4 MC probability range | 1 sweep + 1 sentinel | Low | `tests/math_engine/test_mc_gating_magic_numbers.py` |
| H5 MC stochastic stability | 1 statistical test | Medium (sample size + CLT) | same file |
| H6 ATR fallback continuity | 1 comparative test | Medium | `tests/math_engine/test_atr.py` |
| H7 cross-file HWM invariant | 1 integration test | Medium-High (needs harness) | `tests/integration/` (new) |
| H8 prob_beating=100.0 blocks exit | 1 explicit test | Low | `tests/math_engine/test_exit_confirmation.py` |

Total: ~10 tests for HIGH-risk coverage. Followed by ~9 medium-risk and ~8 low-risk in subsequent cycles.

---

## Source References

### Literature (Tier 1–2)

- **Fu, J., & Zhang, M.** (~2010). *Is the trailing-stop strategy always good for stock trading?* Semantic Scholar paper. [Source link](https://www.semanticscholar.org/paper/Is-the-trailing-stop-strategy-always-good-for-stock-Fu-Zhang/b256975ce24b4945532d1a9f2f3554b4b16bb69c). `[Tier-2 expert; Replicated topic; supports CX-1 stop-trigger monotonicity.]` Accessed 2026-05-13.
- **Kaminski, K. M., & Lo, A. W.** (2014). *When Do Stop-Loss Rules Stop Losses?* Journal of Financial Markets, 18, 234–254. `[Tier-1 peer-reviewed; supports multi-tick confirmation rationale; does NOT validate the specific 3-tick threshold.]`
- **López de Prado, M.** (2018). *Advances in Financial Machine Learning.* Wiley, Ch. 11 (Monte-Carlo simulation in exit gates) and Ch. 14 (Vol-targeting envelopes). `[Tier-2 expert; widely adopted; supports H5 MC stability and F3 vol-scaling literature.]`
- **Carver, R.** (2015). *Systematic Trading.* Harriman House. (ATR-scaled stops; HWM protective stops.) `[Tier-2 expert; high adoption.]`
- **Clenow, A.** (2013). *Following the Trend.* Wiley. (ATR-scaled stops as the practitioner default.) `[Tier-2 expert.]`
- **Madhavan, A.** (2002). *VWAP Strategies.* Journal of Trading. `[Tier-1 peer-reviewed; supports F6 VWAP-deviation signal class.]`
- **Wilder, J. W.** (1978). *New Concepts in Technical Trading Systems.* Trend Research. (Foundational ATR text.) `[Tier-2; foundational; the AlphaBot 14-day simple mean is a documented variation.]`
- **Optimal Trading with a Trailing Stop.** arXiv preprint 1701.03960. [Source link](https://arxiv.org/abs/1701.03960). `[Tier-3 arXiv pre-print; uncorroborated.]` Accessed 2026-05-13.
- **Tradewink glossary**: *Trailing Stop Ratchet — Definition and Examples.* [Source link](https://www.tradewink.com/glossary/trailing-stop-ratchet). `[Tier-4 secondary; practitioner-glossary level; confirms ratchet folklore.]` Accessed 2026-05-13.

### Code (project)

- `math_engine.py` — full module.
- `tests/math_engine/test_*.py` — 11 test files.
- `alpha_bot_execution.py` — caller (referenced for cross-file invariants CX-1 through CX-5).

### Confidence summary

- High-confidence claims (Tier-1 + Tier-2 corroboration): "trailing stops monotone non-decreasing," "MC requires CLT-stability for reliable gating," "ATR is widely-adopted."
- Medium-confidence claims (single-source or expert-only): "intraday log-decay tightening improves Sharpe," "VWAP-bleed exits are useful."
- Low-confidence claims (project-specific / folklore): "5-tick HWM hold threshold for breakeven lock," "0.5 weight-coverage gate for VWAP signal reliability," "kNN-on-2D-distance for MC regime locality."

The audit does not assess strategy efficacy — only invariant coverage.

---

## Audit Summary

- **Functions audited:** 11 (`compute_para_arm_decision`, `compute_time_squeeze_decay`, `compute_active_trailing_stop`, `compute_breakeven_update`, `compute_exit_confirmation`, `compute_vwap_signals`, `compute_vwap_bleed_arm_threshold`, `compute_vwap_breakdown_update`, `run_monte_carlo`, `calculate_20d_vol`, `calculate_14d_atr_pct`).
- **Cross-call (temporal) invariants identified:** 6 (CX-1 through CX-6).
- **HIGH-risk gaps:** 8 (H1–H8).
- **MEDIUM-risk gaps:** 9 (M1–M9).
- **LOW-risk gaps:** 8 (L1–L8).
- **Total recommended new tests for HIGH-risk:** ~10.

### Headline risk

`run_monte_carlo` carries the largest invariant gap density: no probability-range pin, no NaN-input policy, no stochastic-stability property test, and the behavioral-equivalence pins are TIGHTER than the underlying contract (numpy-version brittle). A regression in MC quality would silently degrade exit-gate quality across every position the engine manages. `[Confidence: High — direct reading of the function and its tests.]`

### Second-headline

The "trailing stops are monotone" property — the canonical contract from Fu & Zhang (2010) — is **NOT asserted anywhere** for the AlphaBot stack. It is only enforced implicitly via `safe_hwm` being monotone (which itself is unasserted, CX-2) and the breakeven floor (which only locks at zero, not at the latest HWM). This is the single highest-priority gap from a literature-replication standpoint.
