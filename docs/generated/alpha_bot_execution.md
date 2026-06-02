# alpha_bot_execution

> Core per-cycle execution engine: fetches live portfolio state from Composer, runs all per-symphony exit decisions, calls autotuner post-market, and writes state back to the DB.

**Source:** `alpha_bot_execution.py`
**Last updated:** 2026-05-27

## Overview

`alpha_bot_execution.py` is the execution engine spawned by `app.py` at each `:00` minute. It is the top-level orchestrator for the live trading decision loop. Key responsibilities:

- Fetches live Composer portfolio state (holdings, returns, VWAP, MC probability).
- For each symphony: runs all exit-decision checks via `math_engine` primitives.
- Post-market: calls `autotuner.run_autotuner` per symphony.
- Writes state to the DB via `database.save_state`.
- Calls `database.get_or_create_phase1_theory_bundle_id` at startup to satisfy the NN1 Phase-1 spec_bundle_id requirement.

**Sprint 3 change (SITE-A1–A5):** The port-level dispatch block was removed. No port-level decision math is reachable. All exit decisions flow through per-symphony paths only.

## API Reference

### Top-Level Entry Point

#### `main() → None`
Entry point when run as `python alpha_bot_execution.py`. Acquires the execution lock, loads state, runs the per-symphony execution loop, and releases the lock. On `--force`, bypasses the market-hours guard.

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

- `database` — `acquire_lock`, `release_lock`, `load_state`, `save_state`, `get_or_create_phase1_theory_bundle_id`, `record_exit_trigger`, `record_shadow_observation`, `get_symphony_live_mode` (per-symphony dry-run/live dispatch)
- `math_engine` — all per-tick decision primitives, `run_monte_carlo`, `resolve_trigger_priority`, `compute_regime_match_quality`, `apply_regime_exit_adjustment` (regime-exit-adjustment on the live path)
- `autotuner` — `run_autotuner` (post-market)
- `reporting` — `generate_eod_snapshot`, Discord notifications
- `analytics` — performance metric computation
