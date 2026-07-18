# alpha_bot_execution

> Core per-cycle execution engine: fetches live portfolio state from Composer, runs all per-symphony exit decisions, calls autotuner post-market, and writes state back to the DB.

**Source:** `alpha_bot_execution.py`
**Last updated:** 2026-07-18 (Math Remediation R3-b, `DE-MATH-R3B-001`, cycle in progress) — the arm/disarm block is now delegated to a new shared `math_engine.compute_arm_disarm_decision` seam (replaces the prior inline, MA-4-inverted disarm); a new `disarm_confirm_count` bot_state key is added at the applicable init/reset sites; see the new section below. Prior: 2026-07-18 (Math Remediation F7, `DE-MATH-F7-001`) — post-trigger MC display honesty (AC-1) + MAPERF-15 staleness tripwire (AC-4); prior: 2026-06-21 (startup-seed-symphonies) — confirmed ZERO diff for Math Remediation R1 (2026-07-17, `DE-MATH-R1-001`); see the Replay-Fidelity Boundary section below

## Overview

`alpha_bot_execution.py` is the execution engine spawned by `app.py` at each `:00` minute. It is the top-level orchestrator for the live trading decision loop. Key responsibilities:

- Fetches live Composer portfolio state (holdings, returns, VWAP, MC probability).
- For each symphony: runs all exit-decision checks via `math_engine` primitives.
- Post-market: calls `autotuner.run_autotuner` per symphony.
- Writes state to the DB via `database.save_state`.
- Calls `database.get_or_create_phase1_theory_bundle_id` at startup to satisfy the NN1 Phase-1 spec_bundle_id requirement.

**Sprint 3 change (SITE-A1–A5):** The port-level dispatch block was removed. No port-level decision math is reachable. All exit decisions flow through per-symphony paths only.

**Startup seed (feat/startup-seed-symphonies, 2026-06-21):** `ensure_bot_state_seeded()` is called once from `app.py` daemon startup, before the minute scheduler starts. This prevents the dashboard showing 0 symphonies after an off-hours DB wipe or first-ever start. The seed is idempotent, fail-safe, and does not pollute `shadow_history`. See `DE-SEED-STARTUP-001` in `DECISIONS.md`.

## API Reference

### Top-Level Entry Point

#### `main() → None`
Entry point when run as `python alpha_bot_execution.py`. Acquires the execution lock, loads state, runs the per-symphony execution loop, and releases the lock. On `--force`, bypasses the market-hours guard.

---

### Startup Symphony Seeding

#### `ensure_bot_state_seeded() → None`

Called once from `app.py` daemon startup (before the minute scheduler). Seeds `bot_state` with baseline symphony entries when none are present.

**Behavior:**
- Loads `bot_state` from the DB via `database.load_state()`.
- Presence check: counts dict-valued keys that are NOT in `_SEED_RESERVED_KEYS`. If any symphony entry already exists, returns immediately — no network call, no save (AC-2 idempotency).
- When `bot_state` is empty (or contains only reserved metadata keys), calls `seed_symphonies_into_bot_state(bot_state)` and saves via `database.save_state(bot_state)` if `created >= 1`.
- Entire body is wrapped in `try/except` — a Composer error at startup is logged (`[seed] ensure_bot_state_seeded failed (non-fatal): ...`) and ignored; the daemon continues starting (AC-4 fail-safe).

**Must NOT be called from inside `main()`.** Only called from `app.py` startup, never on the per-minute execution path (AC-7 startup-cost bounded).

---

#### `seed_symphonies_into_bot_state(bot_state: dict) → int`

Creates baseline `bot_state` entries for every symphony not already present.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `bot_state` | `dict` | The mutable state dict loaded from `database.load_state()`. Modified in-place. |

**Returns:** `int` — count of NEW entries created (0 if all accounts return 0 symphonies or all fetches fail).

**Behavior per account in `ACCOUNT_UUIDS`:**
- Calls `fetch_symphony_stats(account)`. Per-account exceptions are caught, logged (`[seed] fetch_symphony_stats(...) failed: ExcType: ...`), and skipped — partial success is allowed, the function never re-raises (AC-4).
- For each symphony id returned that is NOT already in `bot_state`, creates the baseline entry:
  - `high_water_mark` / `shadow_hwm` initialized from `(last_percent_change or 0.0) * 100`
  - `armed`, `tp_armed`, `para_armed`, `triggered`, `breakeven_locked` = `False`
  - `mc_history`, `current_holdings` = `[]`
  - `below_stop_count`, `above_tp_count`, `disarm_confirm_count`, `vwap_ticks`, `vwap_bleed_ticks`, `hwm_hold_ticks` = `0`
  - `prev_return` = `None`
  - `position_epoch` = `database.mint_position_epoch()` (fresh epoch per AC-3 — not the shadow_history write path)
  - `name` from `sym.get("name", "")`
- Calls `_persist_composer_fields_to_bot_state(bot_state, s_id, sym)` after each new entry (mirrors the DATA PHASE create-block at `alpha_bot_execution.py:771-790`).
- Does NOT call `database.record_shadow_observation` and does NOT write any `post_mortem_*.json` files (AC-3 — no Guard-Alpha collection pollution).

**Design limitation (DE-SEED-STARTUP-001):** This function mirrors the DATA PHASE create-block but the two blocks are separate implementations. There is no structural enforcement of their alignment — a future refactor should extract a shared `_create_symphony_entry` helper.

---

#### `_SEED_RESERVED_KEYS: frozenset[str]`

Module-level constant. Composition: `frozenset(database._WIPE_RESERVED_KEYS) | frozenset({"fleet_correlation_alert", "last_successful_cycle_at"})` — 5 keys total: `date`, `last_execution_mode`, `last_market_close_snapshot`, `fleet_correlation_alert`, `last_successful_cycle_at`.

The presence check in `ensure_bot_state_seeded` is `isinstance(v, dict) and k not in _SEED_RESERVED_KEYS`. Only **dict-valued** metadata keys can false-positive this check. Two members are load-bearing for that reason:

- **`last_market_close_snapshot`** (from `_WIPE_RESERVED_KEYS`) — dict-valued; written by the EOD path.
- **`fleet_correlation_alert`** (added here) — dict-valued; written by the engine.

The remaining three members (`date`, `last_execution_mode`, `last_successful_cycle_at`) are string-valued and can never trigger the dict check; they are present as defensive inherited members.

---

### Selection Stats Helper

#### `augment_optimization_results_with_selection_stats(optimization_results: dict) → dict`

Injects `_selection_stats` into each symphony's entry in `optimization_results`. Reads `database.get_latest_autotune_run` per symphony and adds `naive_sharpe`, `selection_tstat`, and `frozen_eval_sharpe`. Handles missing DB rows gracefully — skips injection rather than crashing.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `optimization_results` | `dict` | Symphony-keyed optimization output dict |

**Returns:** The same dict, mutated in place.

---

### Composer API Integration

#### `get_composer_headers() → dict`
Builds the Composer API authentication headers from `COMPOSER_KEY_ID` and `COMPOSER_SECRET` environment variables.

#### `fetch_composer_portfolio(account_id: str) → dict | None`
Fetches the live Composer portfolio for one account. Returns the parsed JSON body or `None` on error.

---

### NN1 Phase-1 Spec-Bundle Wiring

At startup (before any execution cycle), `alpha_bot_execution.py` calls `database.get_or_create_phase1_theory_bundle_id()` to ensure the canonical Phase-1 theory bundle row exists. The returned integer id is passed to `autotuner.run_autotuner` as `spec_bundle_id`. This satisfies the NN1 Phase-1 strict requirement without requiring an explicit operator-registered bundle.

---

### Replay-Fidelity Boundary (Math Remediation R1, `DE-MATH-R1-001`, 2026-07-17)

This module carries **literal zero diff** for the R1 replay-fidelity cycle (per-tick lpc, fail-open arm, regime-conditional exit ticks — MA-1/MA-10/F5). This is a deliberate architecture ruling, not an oversight, and is worth documenting explicitly since a future reader tracing MA-1's fix might otherwise expect it here.

`bot_state["current_holdings"]` is written by two live construction sites in this module (`:888-894`, in the DATA-PHASE update block, and `:1557-1560`, a second per-cycle write) — both emit ticker+allocation ONLY, no `last_percent_change`. **This remains true after R1 and is expected to remain true going forward**, by ruling: `current_holdings` is read back at `:1191` (the triggered-symphony shadow override) into the LIVE `run_monte_carlo` call at `:1270`, whose `mc_prob` is persisted to the live dashboard. Stamping lpc onto this dict would be a live-execution-path behavior change and would relocate the MA-1 degeneracy (the live snapshot refreshes once per cycle, not per tick) rather than fix it. R1's actual fix — a real per-tick `last_percent_change` — is stamped entirely inside `synthetic_history.build_replay_day`, onto a fresh, non-mutating, replay-local structure that is never written back into `bot_state`. See [synthetic_history](synthetic_history.md) for the fix itself and `DE-MATH-R1-001` for the full architecture ruling.

This boundary is enforced as a standing test invariant, not just documented: `tests/execution/test_ac8_live_path_zero_diff_lpc_fix.py` adversarially source-scans that this module never imports `synthetic_history`, and pins that both `current_holdings` construction sites continue to emit ticker+allocation only. A future change that reintroduces lpc onto this shared live dict should treat that as a deliberate, explicitly-ruled scope change — not a silent side effect of an unrelated refactor.

---

### Post-Trigger MC Display Honesty (Math Remediation F7, `DE-MATH-F7-001`, 2026-07-18)

Post-trigger, `prob_underperforming` (from `math_engine.run_monte_carlo`, called at `:1324`) is computed against a fictional 0% baseline — not a bug in the MC math itself but a direct consequence of the TRUE SHADOW RETURN OVERRIDE (`:1244-1258`, in the same block described in the Replay-Fidelity Boundary section above): once a symphony is `triggered`, the `holdings` variable feeding `run_monte_carlo` is swapped for `bot_state[symphony_id]["current_holdings"]` — ticker+allocation only, no `last_percent_change` — so every holding's return input collapses to zero and the resulting probability is meaningless. Pre-F7 this fabricated number was persisted and rendered on three genuinely-live operator surfaces (MC dial, detail-view Risk Math, chart fallback — see [static/index.js](static_index_js.md) for the render-side fix), plus the main-table MC Prob column (`templates/table_partial.html`). **That fourth surface is a CONFIRMED orphaned render path, not a live one** (f7-dash finding, independently confirmed by f7-review's own call-path falsification): no live DOM consumer since the card-SPA redesign removed the `morphdom` injector that used to inject this template's output — a discrepancy with the audit's own premise, which counted the main table as live. F7 fixed the tooltip/value there regardless (template-correct, defensively honest if ever re-wired) and did not re-wire anything (scope discipline). Corroborating evidence: the same template's "View Intraday Chart" button (`:170`) calls an `openChartModal(...)` handler that is not defined in any live JS — a second, pre-existing, unrelated defect on the same likely-dead surface, recorded as backlog in `DE-MATH-F7-001`, out of F7 scope.

F7's fix guards the value at the two persist sites rather than touching the MC computation: the calculation still runs harmlessly every cycle, but a triggered symphony never has the fabricated number written or displayed.

**AC-1 — two persist sites, one guard flag.** `is_triggered_now = bot_state[symphony_id]["triggered"]` (`:1613`) gates both `bot_state[symphony_id]["mc_prob"]` (`:1626`) and the `chart_history` per-cycle append's `mc_prob` (`:1675`) via a shared `persisted_mc_prob = None if is_triggered_now else prob_underperforming` (`:1614`). The console `ArmProb` print (`:1613-1618`) gets the same guard as a minor honesty sweep, ruled in during plan approval — it prints `"Exited"` instead of the fabricated percentage post-trigger, rather than leaving the operator-readable journal misleading while every on-screen surface was fixed.

**Timing is deliberate.** `bot_state[symphony_id]["triggered"]` is not set `True` until later in the same pass, in the execution-queue drain (`:1885`) — so on the cycle the exit actually fires, `is_triggered_now` is still `False` and that cycle's real, pre-override `prob_underperforming` is legitimately persisted; only cycle N+1 onward (once the `current_holdings`-based override is active) does the guard suppress the value to `None`. No decision function anywhere in the six math layers ever receives a guard-produced `None` — the guard lives strictly at display/persist time, after every arm/disarm/TP-confirm/exit-confirm/VWAP branch has already consumed the real value for that cycle.

**Left untouched, by design — genuine one-time snapshots, not fabricated numbers:** the `execution_queue` item construction (`:1764`) and the `record_exit_trigger`/`reporting.send_discord_alert` calls (`:1899`/`:1939`) all run on the triggering cycle itself, before the override is active for that symphony — guarding them would have discarded real exit-moment data, not fabricated data.

**Consumer trace (why the sentinel is bare `None`, no numeric fallback).** Every downstream reader of `mc_prob` was traced before choosing the sentinel shape: `app.py`'s poll passthrough (`:2391`), `_FROZEN_SYM_DEFAULTS` (already `None`-shaped), the dashboard's `sentinelToNull` helper (null-safe), and `templates/table_partial.html`'s MC Prob cell (`:98`, already renders `"---"` when `sym.mc_prob is not none` is false) all handle `None` correctly with zero additional code — no consumer chokes on it, so the codebase's numeric-sentinel fallback idiom was not needed here.

**AC-4 — MAPERF-15 passive staleness tripwire.** A triggered symphony's `current_return` (persisted to `shadow_history.current_return`, raw Composer `last_percent_change * 100`) is the if-held basis `reporting.py`'s Guard-Alpha $-saved math depends on (`DE-GUARD-ALPHA-SAVED-001`). `docs/research/composer/maperf15-post-sale-lpc-semantics.md` empirically confirmed this field keeps moving (tracks-logic) after a live sell, but its own Option B flagged that a silent future Composer behavior change would otherwise be undetectable. This tripwire (`:963-989`) is that check: it watches the same raw `current_return` per triggered symphony while `maperf15_market_hours_now` (`:646` — a real-market-hours discriminator that is independent of `--force`, so a forced run on a closed day/pre-open can never fire a false alarm) holds. A streak counter increments each cycle the value is bit-identical to the prior cycle; a single `logging.warning` fires once the streak reaches `MAPERF15_STATIC_LPC_CYCLES` (`= 30`, ~30 minutes at this project's 1-minute cadence — chosen as a conservative floor well above the sub-second liveness the research doc observed).

**Latch semantics (ruled intentional, not a gap):** the warning fires once per continuous stale episode — `_maperf15_warned` suppresses repeats until the streak resets, and the streak resets to 0 the instant the symphony leaves the `triggered`-AND-market-hours state (untriggers, or the market closes/session ends). A fresh session therefore re-accumulates a full `MAPERF15_STATIC_LPC_CYCLES` before it can warn again — the tripwire never carries a partial count across sessions, and never fires only once for the lifetime of the process. Never raises, never gates any decision, no schema change — passive bookkeeping plus one log line.

---

### Trailing-Stop Arm/Disarm Delegation (Math Remediation R3-b, `DE-MATH-R3B-001`, 2026-07-18, cycle in progress)

The arm/disarm block in `main()` (`~:1373-:1411`) — previously an inline conditional that both armed the protective trailing stop on an in-band MC reading and disarmed it — now delegates the whole decision to `math_engine.compute_arm_disarm_decision` (see [math_engine](math_engine.md)). This replaces a disarm condition that had been INVERTED (MA-4): the old code disarmed on `prob_underperforming > 2 * TRIGGER_THRESHOLD_PCT and current_return > 0.0` — a HIGH MC reading, which `run_monte_carlo`'s own convention makes DETERIORATION, not recovery — while printing `"DISARMED (Conditions Recovered)"`. The new disarm requires `prob_underperforming` to fall back below `TAKE_PROFIT_MC_PCT` (the arm-band's own lower edge) for `DISARM_CONFIRM_TICKS` consecutive ticks. See `DE-MATH-R3B-001` in `DECISIONS.md` for the full bug account, the seam contract, and the parity requirement with the autotuner replay (`autotuner.py:_replay_exit_tick`, same seam).

**Caller-side responsibilities (the seam itself is pure and returns no telemetry):**
- A locally-scoped `armed_before_disarm_decision` snapshot is taken immediately before the seam call and diffed against the seam's return to drive the ARM/DISARM console prints and DB event log — deliberately NOT the pre-existing `prev_armed` variable (a cycle-start snapshot consumed later by the unrelated `chart_event="Armed"` diff).
- The AC-7 `below_stop_count=0` reset fires on the same before/after diff, on the transition into disarm.
- A new `disarm_confirm_count` state key (int, the recovery-tick ladder counter) is threaded alongside `armed` at every bot_state init/reset site that already carries `armed`/`below_stop_count`: the DATA-phase create block, the position-recycle fresh-baseline reset, the main-loop init (plus its legacy-backfill key list), and `seed_symphonies_into_bot_state` (see above). It is deliberately NOT added to the post-trigger reset — that reset never touched `below_stop_count` either, preserving byte-for-byte parity there.

---

## Module-Level Configuration Constants

| Constant | Source | Description |
|----------|--------|-------------|
| `LIVE_EXECUTION` | `os.getenv("LIVE_EXECUTION", "False")` | Master safety flag — must be explicit True for live orders |
| `EXECUTION_START_TIME` | `os.getenv("EXECUTION_START_TIME", "09:30")` | Market session start HH:MM. **Read by the replay, not just production, as of Math Remediation R1** — `autotuner._replay_execution_start_offset_minutes` reads this SAME module attribute (never a replay-local mirror constant) so the two can never drift; see `DE-MATH-R1-001` AC-5/F6. |
| `VWAP_OPEN_WINDOW_GRACE_MINUTES` | `os.getenv("...", "15")` | Suppress VWAP exits for this many minutes after open |
| `MAPERF15_STATIC_LPC_CYCLES` | `30` (named constant, not env-configurable) | Consecutive market-hours cycles of a bit-static post-trigger `current_return` before the AC-4 MAPERF-15 tripwire logs one warning; see the Post-Trigger MC Display Honesty section above |
| `TRIGGER_THRESHOLD_PCT` | `os.getenv("...", "15.0")` | MC probability ceiling to arm the risk guard |
| `SIMULATION_PATHS` | `os.getenv("...", "5000")` | MC path count per cycle |
| `NEIGHBOR_K` | `os.getenv("...", "150")` | kNN pool size per cycle |
| `FLEET_CORRELATION_PCT` | `os.getenv("...", "0.50")` | Fleet circuit-breaker threshold |

## Internal Dependencies

- `database` — `acquire_lock`, `release_lock`, `load_state`, `save_state`, `get_or_create_phase1_theory_bundle_id`, `record_exit_trigger`, `record_shadow_observation`, `get_symphony_live_mode` (per-symphony dry-run/live dispatch), `mint_position_epoch` (startup seed), `_WIPE_RESERVED_KEYS` (extended by `_SEED_RESERVED_KEYS`)
- `math_engine` — all per-tick decision primitives, `run_monte_carlo`, `resolve_trigger_priority`, `compute_regime_match_quality`, `apply_regime_exit_adjustment` (regime-exit-adjustment on the live path)
- `autotuner` — `run_autotuner` (post-market)
- `reporting` — `generate_eod_snapshot`, Discord notifications, `send_discord_alert` (exit-trigger alert)
- `analytics` — performance metric computation

**Never imports `synthetic_history`** — enforced by `tests/execution/test_ac8_live_path_zero_diff_lpc_fix.py` as a standing structural invariant (`DE-MATH-R1-001`), not merely a current fact.

**`math_engine.py` carries zero diff for Math Remediation F7** (`DE-MATH-F7-001`, AC-5) — the fix is entirely a persist/display-time guard in this module plus render-side fixes in `static/index.js`; no exit-decision math changed. Enforced by `tests/test_scope_guard_f7.py`.
