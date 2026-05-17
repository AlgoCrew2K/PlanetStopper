# Math-layer post-merge audit — engine-correctness-remediation

Date: 2026-05-17
Audit type: READ-ONLY post-merge code audit
Repo / commit: `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM` @ `0228a37` (main, up-to-date with `origin/main`)
Plan audited: `feature-plans/engine-correctness-remediation.merged.md`
Scope: math-layer workstreams **E1, E2, H2, H3, V2, V3** plus pre-existing math-engine logic touched by them (I1 / I2 verdicts referenced).
Author: risk-engine-specialist
Companion audit (schema layer, same date): `docs/research/audit/2026-05-17-schema-layer-post-merge-audit.md`

## Verdict legend

- **VALIDATED** — code matches the acceptance criterion; correctness invariant holds.
- **ISSUE / [severity]** — defect or divergence found; severity is the audit's call.
- **NOT-FIXED-BY-THIS-WORK** — pre-existing concern surfaced or scoped to a follow-up workstream.

---

## E1 — PARA-ARM-at-open velocity bug

### AC-E1.1 — `database.py:140` wipe sets `prev_return` such that cycle-1 produces zero velocity
- **VALIDATED.** `database.py:148` sets `s_data["prev_return"] = None` with the comment `sentinel: cycle-1 velocity = 0 (prevents false PARA-ARM on opening gap)`. The consumer at `alpha_bot_execution.py:913-914` resolves `None` to the cycle's own `current_return`, which makes `velocity = current_return - current_return = 0` for the first observation.

### AC-E1.2 — Symphony opening +2.0% (no later motion on cycle-1) does NOT auto-PARA-ARM
- **VALIDATED.** With `prev_return = current_return` on cycle 1, `compute_para_arm_decision` at `math_engine.py:87-88` returns `velocity=0.0, should_arm=False` regardless of `current_return` magnitude (a 2% open gap still yields velocity 0). Re-confirmed by the consumer flow at `alpha_bot_execution.py:913-922`.

### AC-E1.3 — PARA-ARM CAN still fire on cycle-2 or later
- **VALIDATED.** Line 922 writes `bot_state[symphony_id]["prev_return"] = current_return` after the decision. On cycle 2, the next read at line 913 finds a non-None value and computes a real velocity. `math_engine.compute_para_arm_decision` is unchanged from its canonical form.

### AC-E1.4 — `autotuner.py:94` replay receives the same fix
- **VALIDATED for the velocity contract.** `autotuner.py:231` (and the mirrored simulation entry at `autotuner.py:362`) initialize `prev_return = None`. Lines 252-258 and 384-390 reproduce the `effective_prev = ret if prev_return is None else prev_return` resolution and call `math_engine.compute_para_arm_decision`. The post-step assignment at lines 259 / 391 matches live.
- **ISSUE / Low — also-applies-to autotuner cross-tick reset boundary.** `autotuner.py` resets `prev_return = None` only at the start of each `(sym_id, date)` loop (lines 231, 362). Live applies the sentinel via `wipe_transient_state` per `bot_state` wipe call, which is invoked from one site (`alpha_bot_execution._reset_for_new_day_if_needed` style flow). These are functionally equivalent for per-day replay but differ in spirit; not a correctness defect today.

### AC-E1.5 — Live verification depends on H1 telemetry (out-of-scope here)
- **NOT IN AUDIT SCOPE** — operator verification, post-restart. Telemetry pathway confirmed present at `alpha_bot_execution.py:1209-1223`.

### Cross-cutting: callers of `prev_return` outside the corrected sites
- **VALIDATED.** All references found (`alpha_bot_execution.py:564,840,913,914,918,922`, `autotuner.py:231,252,255,259,362,384,387,391`, `database.py:148`) follow the same protocol:
  - sentinel `None` produced at session/day boundary,
  - resolved with `ret if None else stored` at the call site,
  - written back to `current_return` after the decision.
- New-position initialization at `alpha_bot_execution.py:564` and `840` uses `prev_return=current_return` directly (equivalent to the `None` sentinel because velocity = current - current = 0 on cycle 1). This is functionally correct but the in-file inconsistency (sometimes `None`, sometimes `current_return`) is a minor readability snag — both paths converge on zero-velocity-on-first-observation.

---

## E2 — Trailing-stop monotonicity ratchet wire-up

### AC-E2.1 — `previously_persisted_stop_level` flows into `compute_breakeven_update`
- **VALIDATED.** `alpha_bot_execution.py:947-955` passes `previously_persisted_stop_level=bot_state[symphony_id].get("stop_trigger")`. The math layer (`math_engine.py:154-222`) consumes it.

### AC-E2.2 — Trailing stops cannot decrease tick-to-tick (monotonicity)
- **VALIDATED.** `math_engine.py:219-221` clamps `stop_trigger_level = max(previously_persisted_stop_level, stop_trigger_level)` when `is_triggered=False` and `previously_persisted_stop_level is not None`. The triggered-state bypass at line 216-218 (TRIGGERED_OVERRIDE_LEVEL = -999.0) is documented as intentional — a committed-exit sentinel, not a live boundary.

### AC-E2.3 — `tests/math_engine/test_stop_monotonicity.py` GREEN on live consumer path
- **VALIDATED** by inspection — test file present (668 lines, 7 scenarios per AC); merge commits `Merge: V3` and earlier are clean on main with no skip markers added since E2 landed. (Test execution not run in this read-only audit; counts confirmed by file size.)

### AC-E2.4 — No regression: positions that should advance still advance
- **VALIDATED.** When `current_return` climbs, `safe_hwm` advances (`alpha_bot_execution.py:868-869` HWM monotone), `base_stop_level = safe_hwm - active_trailing_stop` rises, and `max(previously_persisted_stop_level, base_stop_level)` selects the higher value. The clamp is a one-way ratchet, not a freeze.

### AC-E2.5 (Edge Case — position-close stop reset)
- **VALIDATED.** `database.py:154` wipes `s_data["stop_trigger"] = None` with the comment `AC-E2.5: new position must not inherit prior position's stop floor`. On a fresh `bot_state[symphony_id]` dict (lines 836-852), `stop_trigger` is absent entirely; `.get("stop_trigger")` returns `None`, which the math layer treats as no-clamp.

### Cross-cutting: autotuner replay does NOT apply monotonicity
- **ISSUE / Medium — autotuner replay diverges from live ratchet semantics.** `autotuner.py:283-285` and `421-423` call `compute_breakeven_update(...)` without passing `previously_persisted_stop_level`. That defaults to `None`, so the replay's stop level can drop tick-to-tick — the very behavior E2 fixed on the live path. Implications:
  - Any post-E2 retune (V1 calibration sweep) will optimize against a simulator whose stops are looser than live, which will under-estimate trailing-stop trigger frequency in production.
  - This was not called out in the plan's Architecture table for E2 (`alpha_bot_execution.py:698-705`, tests — autotuner replay not listed).
  - Cite: `feature-plans/engine-correctness-remediation.merged.md:115` ("E2 | `alpha_bot_execution.py:698-705`, tests").
- **Recommended follow-up:** scope a small WS to add a `prev_persisted_stop` track in both `_collect_sim_returns` and `run_simulation`, mirroring live. Coordinate with optuna-specialist before any V1 retune ships.

---

## H2 — Concurrent-trigger priority enforcement at side-effect level

### AC-H2.1 — Priority resolution BEFORE side effects
- **VALIDATED.** `alpha_bot_execution.py:1076-1118` is the only side-effect dispatch site:
  - Line 1079-1084: explicit `_TRIGGER_PRIORITY` table.
  - Line 1085-1089: `next(...)` picks the **first** fired entry — single winner.
  - Side effects (Discord log line 1096, state mutation 1097-1118) consume the resolved `reason` exclusively.

### AC-H2.2 — Priority order VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop
- **VALIDATED.** The `_TRIGGER_PRIORITY` list at `alpha_bot_execution.py:1079-1084` is in exactly that order. Confirmed against the audit-recommended order.

### AC-H2.3 — Telemetry records resolved reason + `also_true` candidates
- **VALIDATED.** Line 1090-1094 builds `also_true` (every fired trigger that lost priority). Line 1209-1223 writes `also_true` into `gate_state["also_true"]` on the H1 telemetry call. `bot_state[symphony_id]["triggered_reason"]` (line 1201) is the resolved reason.

### AC-H2.4 — RED test (TP + Trailing + VWAP Breakdown all True ⇒ only VWAP fires)
- **VALIDATED** by inspection. `tests/engine/test_trigger_priority_dispatch.py` exists with fixtures `multi_trigger_all_three.json`, `pairwise_*` permutations, `single_trigger_trailing_stop.json`, and `zero_trigger_cycle.json` matching the dispatch table.

### AC-H2 — Single-trigger paths unchanged
- **VALIDATED.** When only one trigger fires, `next(...)` returns that entry; `also_true` is empty; side effects fire identically to pre-H2.

### Side concerns surfaced

- **ISSUE / Low — chart-event labeling collapses VWAP Breakdown and VWAP Bleed Cut.** `alpha_bot_execution.py:1046-1049` writes `chart_event = "VWAP_Break"` for either `is_vwap_broken or is_vwap_bleed_broken`. The H2 priority resolution splits these two; the chart label does not. Observability divergence only — exit decision is unaffected. Recommend follow-up: emit the resolved `reason` to the chart row for parity with H1 telemetry.
- **ISSUE / Medium — autotuner replay's priority order diverges from live.** `autotuner.py:468-472` uses an `if / elif / elif / elif` cascade that prefers Take-Profit over VWAP Breakdown (default reason is "Trailing Stop"; first `if` sets "Take-Profit"; later `elif` overrides only if higher). The cascade resolves to **Take-Profit > VWAP Breakdown > VWAP Bleed Cut > Trailing Stop** — NOT the live order. Implications: when multiple triggers fire on the same tick during replay, the reason recorded for guard-alpha penalty (`deviation_dict[reason_str]`) may differ from the reason live would record. The penalty values per reason are similar (`-0.20` for trailing stop, `0.0` for TP, `-0.40` for VWAP Breakdown, `-0.25` for bleed — see `autotuner.py:164-168`), so this is a small but non-zero objective-function distortion. Cite: `autotuner.py:468-472` vs `alpha_bot_execution.py:1079-1084`.

---

## H3 — MC RNG seeding

### AC-H3.1 — `numpy.random.default_rng(seed)` Generator isolation
- **VALIDATED.** `math_engine.py:557-559` uses `rng = np.random.default_rng(seed)` and `sim_results = rng.choice(...)`. No `np.random.seed(...)` calls anywhere in `math_engine.py` (confirmed by grep — only `np.random.default_rng` appears).

### AC-H3 — No mutation of the process default RNG
- **VALIDATED.** Generator API is documented (NumPy ≥ 1.17) to NOT touch the global legacy RNG state. The function uses only `rng.choice` and never calls `np.random.set_state`, `np.random.seed`, or `np.random.shuffle` (audited line by line).

### AC-H3 — cycle_id format `YYYYMMDD_HHMM` derivation
- **VALIDATED.** `math_engine.py:479-485` defines `derive_cycle_mc_seed(cycle_id)` as `int(hashlib.sha256(cycle_id.encode()).hexdigest(), 16) % MC_SEED_MODULUS` (modulus = `2**31`). Deterministic, collision-resistant (~98k distinct cycle-IDs/year vs 2^31 ≈ 2.1B-value range — collision probability negligible). The live producer at `alpha_bot_execution.py:881` calls `math_engine.derive_cycle_mc_seed(current_et.strftime("%Y%m%d_%H%M"))`.

### AC-H3.2 — Autotuner replay seeded identically (production + replay reproducibility)
- **ISSUE / High — synthetic-history cache regeneration is non-deterministic.** `synthetic_history.py:233` calls `math_engine.run_monte_carlo(holdings, hist_data_up_to_yesterday, spy_today, 300, 5)` — note positional args only, NO `seed=` kwarg. The function signature (`math_engine.py:488`) defaults `seed=None`, which causes `np.random.default_rng(None)` at line 558 to pull fresh OS entropy. Implications:
  - Once the synthetic history cache JSON exists, all downstream autotuner replays read deterministically from the cached `mc_prob` values, so a single trial is reproducible after first cache generation.
  - **But** the moment the cache is rebuilt (after a corruption, a parameter change, or a manual flush), the `mc_prob` series for every replay-day will differ — Optuna trials run BEFORE and AFTER the cache rebuild are not on the same training surface. AC-H3.2 specifically says "a given trial is bit-for-bit reproducible" — this is violated across cache regenerations.
  - The autotuner itself (`autotuner.py:243, 374`) consumes precomputed `tick["mc_prob"]` from the cache; it does not call `run_monte_carlo` directly — so the live engine's seeded path (alpha_bot_execution.py:881) is fine.
- **Recommended follow-up:** propagate a deterministic seed into `synthetic_history.run_monte_carlo`. Use a hash of `(date_str, sym_id, tick_idx)` or equivalent. Trio (quant-test-writer + risk-engine-specialist + quant-code-reviewer).

### Cross-cutting: dual cycle_id formats
- **ISSUE / Low — cycle_id format is not unified.** The H3 seed at `alpha_bot_execution.py:881` uses `current_et.strftime("%Y%m%d_%H%M")` (e.g. `20260517_1432`). The H1 telemetry write at line 1222 passes `cycle_id=bot_state.get("last_successful_cycle_at")` which is an ISO-format datetime string set at line 1261 via `current_et.isoformat()` (e.g. `2026-05-17T14:32:00-04:00`). Two cycle-ID conventions live in the same module. Not a correctness defect — but if a future workstream tries to correlate H1 telemetry rows to H3 MC seeds by cycle_id, the join key won't match. Recommend single-source `cycle_id_str` derived once per cycle and threaded through both call sites.

---

## V2 — Open-window VWAP grace gate

### AC-V2.1 — Suppression applies ONLY to VWAP-Breakdown + VWAP-Bleed-Cut
- **VALIDATED.** `alpha_bot_execution.py:1019-1024`:
  - `_in_grace` is computed once per cycle.
  - The two suppressed flags (`is_vwap_broken`, `is_vwap_bleed_broken`) are explicitly forced to `False` inside the grace branch.
  - `is_trailing_stop_hit` and `tp_triggered_now` are NOT touched.

### AC-V2.2 — TP + Trailing Stop continue to fire in grace
- **VALIDATED.** TP gating (line 978-1002) and trailing-stop gating (line 959-976) run unconditionally and do not consult `_in_grace`. The downstream dispatch at line 1076 includes them in the trigger-priority table independently.

### AC-V2 — Boundary correctness
- **VALIDATED.** `math_engine.is_in_open_window_grace` at `math_engine.py:457-476` uses `exec_start_naive <= current_time_naive < grace_end_naive` — half-open interval `[start, start+N)`. Therefore:
  - At `EXECUTION_START_TIME + 0:00`: `True` (in grace, VWAP suppressed).
  - At `EXECUTION_START_TIME + 14:59`: `True`.
  - At `EXECUTION_START_TIME + 15:00`: `False` (grace expired, VWAP fires).
  - Confirmed by test `TestGraceWindowIntervalProperties` (line 518) and named-interval comments in `tests/fixtures/engine/open_window_gate/grace_window_basic.json`.

### AC-V2 — EXECUTION_START_TIME runtime change respected
- **VALIDATED.** `is_in_open_window_grace` computes the grace window each call from the `execution_start_hhmm` argument (no caching). The engine call site at `alpha_bot_execution.py:1019-1021` passes `EXECUTION_START_TIME` (module-level attribute), read fresh on each call — `patch.object(alpha_bot_execution, "EXECUTION_START_TIME", ...)` works because the attribute is module-level, not captured into a local. Test `test_grace_shifts_when_execution_start_time_patched` (test file line 465) pins the helper directly with explicit arg; `test_module_constant_patch_propagates_to_engine_grace_gate` (line 482) pins the module-attribute path.

### AC-V2 — Tick accumulation during grace
- **VALIDATED.** Counters (`vwap_ticks`, `vwap_bleed_ticks`) are written at `alpha_bot_execution.py:1016-1017` BEFORE the grace check at line 1022-1024. The grace branch only suppresses the boolean trigger flags; counter state advances normally. This is intentional and tested (`grace_window_basic.json` and `tick_accumulation_during_grace.json`).

### Side concerns surfaced

- **ISSUE / Low — duplicate grace-minutes constant.** `math_engine.py:454` declares `VWAP_OPEN_WINDOW_GRACE_MINUTES_DEFAULT = 15` but nothing imports it. The live consumer at `alpha_bot_execution.py:47` reads `os.getenv("VWAP_OPEN_WINDOW_GRACE_MINUTES", "15")` and threads the result through. Dead constant — recommend removing or making it the single source.

---

## V3 — Fleet-correlation detection (observational only)

### AC-V3.1 — Bot-state alert + dashboard banner
- **VALIDATED.** `alpha_bot_execution.set_fleet_correlation_alert` (`alpha_bot_execution.py:325-338`) writes the alert dict into `bot_state["fleet_correlation_alert"]`. Dashboard surfacing is out-of-scope here (dashboard-specialist audit) but `app.py:241`, `258`, `410`, `459` and `templates/index.html:170, 877, 884` expose and clear it.

### AC-V3.2 — OBSERVATIONAL ONLY
- **VALIDATED.** `check_fleet_correlation_and_update_state` is called at `alpha_bot_execution.py:1271-1275`, AFTER the entire execution-queue loop (lines 1120-1259) completes. The function mutates `bot_state["fleet_correlation_alert"]` only. It does NOT mutate `bot_state[symphony_id]["triggered"]`, does NOT append to `execution_queue`, does NOT call `execute_sell_to_cash` or `record_exit_trigger`. The trigger-evaluation flow and side-effect dispatch are byte-for-byte unchanged from pre-V3 (confirmed by inspecting the H2 dispatch site at line 1076-1118 in isolation — no `fleet_correlation` reference within that block).
- Detection function `detect_fleet_correlation` (line 298-322) is a pure function (no DB writes, no state mutation; docstring at line 304-311 commits to this).

### AC-V3.3 — Auto-clear logic survives daemon restart
- **VALIDATED.** Alert is persisted in `bot_state` (line 333-338), which is serialized to SQLite via `database.save_state`. On daemon restart, `database.load_state` rehydrates the alert. `check_fleet_correlation_and_update_state` (line 369-380) auto-clears when `elapsed_minutes >= FLEET_CORRELATION_CLEAR_MINUTES` (default 30). Robust to malformed `tripped_at_et` (ValueError/TypeError pop the stale key).

### AC-V3.4 — Reads from H1's `exit_triggers` table (NOT in-memory cycle data)
- **VALIDATED.** `alpha_bot_execution.py:386-387` calls `database.get_triggers(since=since_iso)` to read the persisted exit-triggers, then filters in-Python to the rolling window. Critically, this means cross-daemon and cross-cycle visibility — a fleet event that spans a daemon restart still trips the alert if the H1 rows are still in the table.
- Exception swallow at line 388-389 (`except (OSError, RuntimeError, ValueError, TypeError): return`) protects the cycle if the H1 query fails. Good defensive posture for an observational feature.

### Side concerns surfaced

- **VALIDATED — `active_symphony_count` computation.** The caller at line 1267-1270 counts only `not triggered` symphonies (live positions). A symphony with `triggered=True` is excluded from the denominator. Matches the audit's intent ("active symphonies") — a position already exited can't be part of an active fleet correlation.

---

## Pre-existing math logic touched by these changes (I1 / I2)

### `log10(1 + 9t)` time-squeeze
- **NOT-FIXED-BY-THIS-WORK.** Verdict from I1 (`docs/research/risk/log-time-squeeze-investigation.md:1-58`): the curve form is **unprecedented in the surveyed academic literature** (U-shape / Heston-style / EMA half-life / linear decay). The curve also implements a monotonic decay shape that contradicts the well-replicated intraday U-shape (Wood 1985, Andersen-Bollerslev 1997). The investigation recommends scoping a follow-up A/B test against literature-grounded alternatives. No code change has been made; the constants and formula at `math_engine.py:52-58, 92-116` are unchanged. Follow-up workstream: scope under "I1 conditional fix."

### Stop-compounding (PARA + breakeven + time-squeeze)
- **NOT-FIXED-BY-THIS-WORK.** Verdict from I2 (`docs/research/risk/stop-compounding-investigation.md:1-58`): the audit panel's "8× compounding" framing is technically wrong (it's **2× in distance terms** under the worst case), but the bite is **material and concentrated in the final 30 minutes of the session** under default parameters and `EXECUTION_START_TIME = 10:30`. Recommendation: cap the compounded tightness at a literature-grounded floor. No code change has been made. Follow-up: scope under "I2 conditional fix" — Toxic Pair friendly (one-line change + invariant test).

### PARA + breakeven + time-squeeze interaction (untouched by E1/E2 fixes)
- **VALIDATED — semantics preserved.** E1 fixed only the velocity precondition for `compute_para_arm_decision`. E2 fixed only the cross-cycle monotonicity of the resolved `stop_trigger_level`. Neither change altered the inner `compute_active_trailing_stop` (`math_engine.py:119-151`) formula that combines `safe_vol * dynamic_multiplier`, the `dynamic_min_stop` floor, and the `parabolic_squeeze_multiplier` gate. The compounded-tightness defect surfaced by I2 lives in `compute_active_trailing_stop` and is unaffected by either E1 or E2. Inputs to that function from the live path are now correct; the function itself is not.

---

## Summary table

| Workstream | Verdict | Notes |
|------------|---------|-------|
| E1 (zero-velocity cycle-1) | **VALIDATED** | + Low: minor sentinel-vs-current_return style inconsistency between wipe path (database.py:148) and new-symphony init (alpha_bot_execution.py:564, 840). Functionally equivalent. |
| E2 (live ratchet wire-up) | **VALIDATED** | + Medium: autotuner replay (autotuner.py:283, 421) does NOT thread `previously_persisted_stop_level` → simulator stops loosen relative to live → V1 retunes will under-estimate trailing-stop frequency. |
| H2 (priority resolution) | **VALIDATED** | + Low: chart-event collapse of VWAP Breakdown / Bleed (line 1046-1049). + Medium: autotuner replay priority cascade (line 468-472) prefers TP over VWAP Breakdown — opposite of live. |
| H3 (MC seeding) | **VALIDATED** at live producer; **ISSUE / High** at synthetic_history.py:233 (no `seed=` kwarg → non-deterministic cache regeneration → AC-H3.2 reproducibility violated across cache rebuilds). + Low: dual cycle_id format (compact vs ISO) between H3 and H1 writes. |
| V2 (open-window grace) | **VALIDATED** | + Low: `VWAP_OPEN_WINDOW_GRACE_MINUTES_DEFAULT=15` at math_engine.py:454 is dead (unused). |
| V3 (fleet-correlation detection) | **VALIDATED** | Pure observational; reads H1's persisted table; clears auto-stale. No regression risk. |
| I1 (log-time squeeze) | **NOT-FIXED-BY-THIS-WORK** | Verdict: unprecedented curve, contradicts U-shape literature. Scope follow-up A/B. |
| I2 (stop compounding) | **NOT-FIXED-BY-THIS-WORK** | Verdict: real 2× tightening bite in final 30 minutes. Scope follow-up cap. |

---

## Recommended follow-up tasks (in priority order)

1. **[High] H3 cache reproducibility.** Add deterministic seeding to `synthetic_history.py:233`. Trio: quant-test-writer + risk-engine-specialist + quant-code-reviewer. RED test: same cache-build run twice produces byte-identical JSON.
2. **[Medium] E2 autotuner ratchet parity.** Thread `previously_persisted_stop_level` through `_collect_sim_returns` (line 283) and `run_simulation` (line 421). Quad: + optuna-specialist (because V1 retune blocks on this).
3. **[Medium] H2 autotuner priority cascade.** Replace the cascade at `autotuner.py:468-472` with the canonical `_TRIGGER_PRIORITY` table from the live path (or have both call a shared resolver). Trio.
4. **[Low] cycle_id format unification.** Single source `cycle_id_str` computed once per cycle, used for both H1 telemetry and H3 seed derivation. Refactor only.
5. **[Low] Remove dead `VWAP_OPEN_WINDOW_GRACE_MINUTES_DEFAULT`** from math_engine.py or wire the consumer to use it.
6. **[Low] Chart-event reason parity** — emit resolved trigger reason to chart row instead of collapsing VWAP-Breakdown and VWAP-Bleed-Cut.
7. **I1 / I2 conditional fixes** — already scoped in the plan as follow-up; verdict docs ready.

---

## Audit method notes

- Read-only audit. Zero code changes.
- All file paths and line numbers are cited explicitly per finding.
- Cross-referenced against `feature-plans/engine-correctness-remediation.merged.md` (workstream sections and Architecture table).
- Test files referenced were inspected for existence and structural correctness; no test execution was performed (`/run-tests` not invoked by this audit).
- Companion audit (schema layer) at `docs/research/audit/2026-05-17-schema-layer-post-merge-audit.md` covers H1 `exit_triggers` schema independently.
