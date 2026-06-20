# advisors/strategy_builder_scheduler

> Weekly Strategy Builder Scheduler (AC-18): runs the real builder unattended for all four objectives with same-ISO-week idempotency and bounded retry; advisory-only, never raises.

**Source:** `advisors/strategy_builder_scheduler.py`
**Last updated:** 2026-06-20

## Overview

`advisors/strategy_builder_scheduler.py` is the standalone weekly automation layer for the Strategy Builder (Component 4, AC-18). It drives `propose_strategies` for all four `Objective` values once per ISO week, guarded by an idempotency check and per-objective bounded retry. Modelled on `prism_scheduler.py`.

Off-execution-path: not imported from `alpha_bot_execution.py`. Advisory-only: no `LIVE_EXECUTION` interaction, no new settings-write path, no Composer write call.

Invoke:
```
python -m advisors.strategy_builder_scheduler
```

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_ATTEMPTS` | `3` | Maximum attempts to run the builder for a given objective before giving up (named constant — no magic numbers) |

## Public API

### `run_weekly_build() -> None`

Run the real builder for all four objectives; skip if already ran this ISO week.

**Orchestration contract:**

1. **Idempotency check:** `_already_ran_this_week()` — if any `STRATEGY_BUILDER` `advisor_observations` row exists from the current ISO week (Monday 00:00 UTC → Sunday 23:59 UTC), log and return (no-op). Prevents multiple runs per week on restarts or cron-overlap.
2. **Per-objective loop:** for each of the four `Objective` values (`diversify`, `cut_drawdown`, `lift_risk_adjusted`, `volatility_mitigation`), call `propose_strategies(objective=objective, universe=[], screen_config=ScreenConfig(), live_returns=[])`. `universe=[]` triggers C1 self-sourcing from `universe_provider.get_tradeable_set()` (Q2-A).
3. **Bounded retry:** each objective retries up to `MAX_ATTEMPTS` times on exception. A failed objective is logged (class name only, D-1) and the loop continues to the next objective — one objective's failure does not abort the others.
4. **Never raises (D-1):** all exceptions are caught and logged with `type(exc).__name__` only — no key/path/message leak.

**Returns:** `None`

## Internal Helpers

### `_already_ran_this_week() -> bool`

Patchable idempotency seam. Checks `database.get_advisor_observations_for_symphony(symphony_id="", advisor_role="STRATEGY_BUILDER", limit=50)` for any row whose `created_at` falls in the current ISO year/week. Degrades to `False` (run anyway) on any DB error (D-1). Tests monkeypatch this to `True` (same-week no-op) or `False` (fresh run).

## Design Decisions

**Idempotency is ISO-week scoped, not day-scoped.** The Strategy Builder run is computationally expensive (one backtest per candidate, all four objectives). Weekly granularity matches the community-strats Atlas cache TTL and provides enough freshness for the operator dashboard without hammering the Composer API daily.

**`universe=[]` is always passed.** The scheduler always self-sources from C1 (`universe_provider.get_tradeable_set()`). It does not maintain its own ticker list — that is the universe provider's responsibility.

**D-1 contract is stricter than `prism_scheduler.py`'s bounded retry.** `prism_scheduler.py` fails loudly after exhausting attempts (exit 1). The strategy scheduler continues to the next objective after exhaustion — a single objective's persistent failure should not block the others from running.

**`symphony_id=""` for all scheduler observations.** The scheduler has no per-symphony context; all observations are keyed to the empty-string symphony ID. This matches the `_already_ran_this_week` check, which queries `symphony_id=""`.

## Internal Dependencies

- `advisors.strategy_builder_engine` — `Objective`, `ScreenConfig`, `propose_strategies` (CC-2 lazy import)
- `database` — `get_advisor_observations_for_symphony` (inside `_already_ran_this_week`)

No import of `alpha_bot_execution`, `app`, `autotuner`, or any execution module. Off-execution-path; advisory-only.
