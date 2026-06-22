# alpha_bot_execution

> Core per-cycle execution engine: fetches live portfolio state from Composer, runs all per-symphony exit decisions, calls autotuner post-market, and writes state back to the DB.

**Source:** `alpha_bot_execution.py`
**Last updated:** 2026-06-21 (startup-seed-symphonies)

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
  - `below_stop_count`, `above_tp_count`, `vwap_ticks`, `vwap_bleed_ticks`, `hwm_hold_ticks` = `0`
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

## Module-Level Configuration Constants

| Constant | Source | Description |
|----------|--------|-------------|
| `LIVE_EXECUTION` | `os.getenv("LIVE_EXECUTION", "False")` | Master safety flag — must be explicit True for live orders |
| `EXECUTION_START_TIME` | `os.getenv("EXECUTION_START_TIME", "09:30")` | Market session start HH:MM |
| `VWAP_OPEN_WINDOW_GRACE_MINUTES` | `os.getenv("...", "15")` | Suppress VWAP exits for this many minutes after open |
| `TRIGGER_THRESHOLD_PCT` | `os.getenv("...", "15.0")` | MC probability ceiling to arm the risk guard |
| `SIMULATION_PATHS` | `os.getenv("...", "5000")` | MC path count per cycle |
| `NEIGHBOR_K` | `os.getenv("...", "150")` | kNN pool size per cycle |
| `FLEET_CORRELATION_PCT` | `os.getenv("...", "0.50")` | Fleet circuit-breaker threshold |

## Internal Dependencies

- `database` — `acquire_lock`, `release_lock`, `load_state`, `save_state`, `get_or_create_phase1_theory_bundle_id`, `record_exit_trigger`, `record_shadow_observation`, `get_symphony_live_mode` (per-symphony dry-run/live dispatch), `mint_position_epoch` (startup seed), `_WIPE_RESERVED_KEYS` (extended by `_SEED_RESERVED_KEYS`)
- `math_engine` — all per-tick decision primitives, `run_monte_carlo`, `resolve_trigger_priority`, `compute_regime_match_quality`, `apply_regime_exit_adjustment` (regime-exit-adjustment on the live path)
- `autotuner` — `run_autotuner` (post-market)
- `reporting` — `generate_eod_snapshot`, Discord notifications
- `analytics` — performance metric computation
