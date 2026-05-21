# Feature: Exit-Decision Math Fixes — Remediation Cluster 1
Status: ready
Created: 2026-05-21

## Summary

Cluster 1 of the AlphaBot v3 math-audit remediation. Fixes the exit-decision math and exit-state-lifecycle defects on **both** exit altitudes — per-symphony and port-level. The audit verified the per-tick exit primitives (breakeven latch, 3-tick exit-confirmation, trigger-priority resolver, NaN/Inf rejection) are derivationally correct; the defects in this cluster are: the trailing-stop ratchet is constructed as a frozen give-back floor instead of a high-water-mark-anchored trailing stop; per-position and port-level exit state is never reset across position/composition turnover; and several input-validation and provenance gaps.

Audit findings covered: C-2 (per-symphony cross-position reset), H-1 (ratchet construction + citations), M-1 (time-squeeze range), M-2 (squeeze lower bound), M-4 (decay-curve provenance), VWAP System A provenance, two LOW items — plus two port-level lifecycle findings surfaced by post-audit verification (port-level C-2 analogue, port-level daily reset).

Audited at `main @ 53ef340`; this cycle branches from current `main`. Line numbers below reference the audit SHA — the team re-locates against current code.

## Acceptance Criteria

### Per-symphony exit math & state
- [ ] AC-1 (C-2): when a `symphony_id` closes a position and later re-opens a new one, the new position starts with fully reset per-position state — `triggered=False`, `breakeven_locked=False`, `hwm_hold_ticks`/`below_stop_count`/`vwap_ticks`/`vwap_bleed_ticks`=0, and no persisted `stop_trigger` (the first `compute_breakeven_update` receives `previously_persisted_stop_level=None`). The new position's first computed stop level is neither the `-999.0` sentinel nor clamped by the prior position's level.
- [ ] AC-2 (C-2): position-open is detected reliably (keyed on `position_open_date` / composition identity); the reset fires exactly once per new position, never mid-position.
- [ ] AC-3 (H-1): the trailing-stop ratchet anchors on the **high-water mark** — each tick `stop = HWM − vol_scaled_distance` is recomputed; the stop may move down when the vol-scaled distance widens and up as the HWM rises. Invariants preserved: HWM is monotonic non-decreasing within a position; once the breakeven latch fires the stop floors at breakeven. (Shared `math_engine` math — this fix covers both altitudes.)
- [ ] AC-4 (H-1): the ratchet function/variable naming and docstring accurately describe an HWM-anchored trailing stop; the Glynn & Iglehart citation is removed; the Fu & Zhang citation is corrected (Fu & Zhang 2012, *Int. J. Operations Research* 9(3), 129-140) or removed; the same correction is propagated to `test_stop_monotonicity.py`.
- [ ] AC-5 (M-2): `compute_active_trailing_stop` rejects `parabolic_squeeze_multiplier <= 0` at entry with an explicit `ValueError`; for `multiplier ∈ (0,1]` the stop distance stays strictly positive and is never collapsed to ~0 or placed above the HWM.
- [ ] AC-6 (M-1): `compute_time_squeeze_decay` raises an explicit `ValueError` on `time_ratio` outside `[0,1]` rather than crashing with an opaque `log10` domain error or silently over-tightening.
- [ ] AC-7 (M-4): the `log10(1+9·t)` decay curve carries a sourced inline rationale for its concave shape, or is explicitly flagged for a follow-up empirical review.
- [ ] AC-8 (VWAP provenance): the VWAP System A gate (`safe_hwm >= vwap_cross_hwm_pct`) is documented in-code as a tuned practitioner heuristic with no formal literature provenance.
- [ ] AC-9 (LOW): `is_in_open_window_grace` requires/asserts a timezone-aware ET datetime or performs tz-aware arithmetic so a UTC caller cannot shift the grace window; the dead local `exec_start` is removed.

### Port-level exit state lifecycle
- [ ] AC-10 (port-level C-2 analogue): `rebase_port_state_on_composition_change` resets `triggered` **and** `triggered_reason` (alongside the fields it already resets), so a portfolio composition change cannot leave a stale `triggered=True` that makes `build_port_signal` emit a spurious port-wide exit signal on the first cycle of the new composition.
- [ ] AC-11 (port-level daily reset): a port-level daily-reset path wipes the `port_state` transient exit-guard fields (`triggered`, `triggered_reason`, `armed`, `para_armed`, `port_breakeven_active`) at the start of a new trading day — the `port_state` analogue of `wipe_transient_state`.

### Regression
- [ ] AC-12: every changed math/state layer ships a golden-fixture or property-based test; all existing math tests (705+) and portmode tests (196) pass; new tests are RED-verified against pre-fix code. Golden fixtures for `compute_breakeven_update` and the replay-ratchet-parity test are updated to the new HWM-anchored behavior — an intentional behavior change, not a regression.

## Architecture

| Finding | File / function | Change |
|---|---|---|
| C-2 | `alpha_bot_execution.py` per-symphony loop + `bot_state` lifecycle; cross-ref `database.py` `wipe_transient_state` | detect position-open; clear the six transient fields + delete `stop_trigger` |
| H-1 | `math_engine.py` `compute_breakeven_update` (~283-292) | re-anchor ratchet to HWM; recompute `stop = HWM − distance` each tick; preserve HWM monotonicity + breakeven floor |
| H-1 | `math_engine.py` ratchet docstring + `test_stop_monotonicity.py` | fix naming, remove Glynn & Iglehart, correct Fu & Zhang to 2012 |
| M-1 | `math_engine.py` `compute_time_squeeze_decay` (~177-181) | raise `ValueError` on `time_ratio ∉ [0,1]` |
| M-2 | `math_engine.py` `compute_active_trailing_stop` (~213-216) | reject `parabolic_squeeze_multiplier <= 0` |
| M-4 | `math_engine.py` decay curve + `DECAY_CURVE_SCALAR` | sourced rationale comment |
| VWAP | `math_engine.py` (~516) | provenance comment on the System A gate |
| AC-9 | `math_engine.py` `is_in_open_window_grace` (~545-554) | tz-aware handling; delete dead local `exec_start` |
| AC-10 | `database.py` `rebase_port_state_on_composition_change` (~997-1011) | add `triggered` / `triggered_reason` to the reset payload |
| AC-11 | `database.py` `new_day_reset_port_state` (~973-982) or a new path | wipe `port_state` transient exit-guard fields at new day |

**Shared-math note:** the port-level altitude has **no separate exit math** — it runs the same `math_engine` functions via the per-symphony loop and consumes the aggregated result. AC-3/4/5/6 therefore fix both altitudes with one change. The port-level-specific work is purely the `port_state` lifecycle (AC-10, AC-11).

## Edge Cases
- Same `symphony_id` re-allocated to a new position after a trigger (the C-2 path).
- Port composition change with a stale `triggered=True` in `port_state` (AC-10).
- New trading day with stale `port_state` exit-guard fields (AC-11).
- `parabolic_squeeze_multiplier` = 0, < 0, > 1.
- `time_ratio` < 0, > 1, and exactly 0.0 / 1.0 (endpoints must still yield the documented MULT_OPEN/CLOSE and MIN_STOP values).
- `symphony_vol <= 0` (the existing `VOL_FALLBACK` path must still hold).
- The HWM re-anchor changes `compute_breakeven_update` output — the autotuner replay (`autotuner.py` calls it) and `test_e2_replay_ratchet_parity.py` shift; their fixtures update to the new correct behavior. Full replay-parity remediation is Cluster 3 — this cluster only updates fixtures to keep the suite green.

## Security Considerations
Internal risk-engine change — no new user input, no injection surface, no new external API calls, no new auth boundary. The only persistence touched is `port_state` SQLite reads/writes (parameterized queries, already in place). Safety-relevant invariant: no change may introduce a path that reaches `execute_sell_to_cash()` / the broker `go-to-cash` call outside the existing `LIVE_EXECUTION` guard — `quant-code-reviewer`'s live-trade-boundary gate enforces this.

## Testing Strategy
- **Golden-fixture tests** (per changed `math_engine` layer): `compute_breakeven_update` HWM-anchored behavior, `compute_active_trailing_stop` squeeze rejection, `compute_time_squeeze_decay` range rejection. Fixtures under `tests/fixtures/math/`.
- **Property-based tests**: HWM monotonic non-decreasing within a position; stop ratchets up with the HWM; stop may widen when the vol-scaled distance widens; breakeven floor holds once latched.
- **Lifecycle tests** (the audit-specified RED tests): per-symphony open→trigger→re-open for the same `symphony_id` (AC-1/2); port-level composition-change with stale `triggered` (AC-10); port-level new-day reset (AC-11).
- **Rejection tests**: `multiplier <= 0` raises; `time_ratio ∉ [0,1]` raises.
- **Full suite**: `tests/math_engine/` and `tests/portmode/` both green; new tests RED-verified against pre-fix code.
- `quant-test-writer` authors the adversarial RED tests; no hardcoded producer values — derive from fixtures or assert shape/property.

## Decisions
| Decision | Choice | Rationale |
|---|---|---|
| D1 — ratchet construction | Re-anchor to the high-water mark (true trailing stop) | Literature standard (Fu & Zhang, Imkeller & Leung); makes the volatility-scaling AlphaBot already computes actually function; resolves the compound premature-exit risk without a coupled decay-curve reshape; the breakeven latch remains the "lock gains hard" mechanism |
| M-1 handling | Raise `ValueError` on out-of-range `time_ratio` | Consistent with `math_engine`'s reject-don't-coerce policy for non-finite inputs |
| M-2 handling | Reject `multiplier <= 0` at entry | `exit-math`'s policy ruling; clamping would mask a mistuned Optuna parameter |

## Scope Boundaries
- **IN**: exit-decision math in `math_engine.py` (ratchet, breakeven, time-squeeze, squeeze, VWAP provenance, grace tz); per-symphony exit-state lifecycle in `alpha_bot_execution.py`; port-level exit-state lifecycle in `database.py` (`rebase_port_state_on_composition_change`, `new_day_reset_port_state`); the citation fix in `test_stop_monotonicity.py`.
- **OUT**: Monte Carlo kNN (Cluster 2); autotuner replay & statistics, including the autotuner port-level blind spot (Clusters 3-4); synthetic history (Cluster 5); portfolio aggregation / analytics integrity (Cluster 6). The DSR/Sortino constants in `math_engine.py:9-17` get a clarifying comment only; relocation is deferred to Cluster 4.
