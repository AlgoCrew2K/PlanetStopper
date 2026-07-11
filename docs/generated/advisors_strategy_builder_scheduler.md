# advisors/strategy_builder_scheduler

> Weekly Strategy Builder Scheduler (AC-18): runs the real dual-mode builder (built-new + atlas-suggested) unattended for all four objectives with same-ISO-week idempotency and bounded retry, then runs the Frontrunner Builder over all live symphonies; advisory-only, never raises.

**Source:** `advisors/strategy_builder_scheduler.py`
**Last updated:** 2026-07-11 (Frontrunner Builder AC-1 hook, `f1592a2`; prior: 2026-06-20 C5 dual-mode Atlas injection 147a181)

## Overview

`advisors/strategy_builder_scheduler.py` is the standalone weekly automation layer for the Strategy Builder (Component 4, AC-18). It drives `propose_strategies` for all four `Objective` values once per ISO week, guarded by an idempotency check and per-objective bounded retry. As of C5 (commit 147a181), each objective injects objective-matched Atlas community candidates via `build_plan_generator.load_atlas_candidates(objective)` — the weekly run is genuinely dual-mode: built-new (Opus C1→C2→C3) AND atlas-suggested candidates pooled in ONE FDR batch. Modelled on `prism_scheduler.py`.

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

Run the real dual-mode builder for all four objectives; skip if already ran this ISO week.

**Orchestration contract (C5 — commit 147a181):**

1. **Idempotency check:** `_already_ran_this_week()` — if any `STRATEGY_BUILDER` `advisor_observations` row exists from the current ISO week (Monday 00:00 UTC → Sunday 23:59 UTC), log and return (no-op). Prevents multiple runs per week on restarts or cron-overlap.
2. **Per-objective Atlas injection:** for each `Objective`, call `_bpg.load_atlas_candidates(objective)` (CC-2 lazy import as `advisors.build_plan_generator`, `strategy_builder_scheduler.py:134`). `load_atlas_candidates` is D-1 (never raises) and bill-protected (`force_refresh=False` inside). On any Atlas error the inner `try/except` sets `community_candidates=[]` and logs the class name — built-new always proceeds (`strategy_builder_scheduler.py:135-142`).
3. **Per-objective build:** call `propose_strategies(objective=objective, universe=[], screen_config=ScreenConfig(), live_returns=[], community_candidates=community_candidates)`. `universe=[]` triggers C1 self-sourcing from `universe_provider.get_tradeable_set()` (Q2-A). Atlas-suggested candidates flow through the same single-batch FDR gate as built-new (AC-21).
4. **Bounded retry:** each objective retries up to `MAX_ATTEMPTS` times on exception. A failed objective is logged (class name only, D-1) and the loop continues to the next objective — one objective's failure does not abort the others.
5. **Never raises (D-1):** all exceptions are caught and logged with `type(exc).__name__` only — no key/path/message leak.

**Returns:** `None`

## Frontrunner Builder Hook (AC-1, 2026-07-11, commit f1592a2)

After the four-objective loop completes, `run_weekly_build()` calls `advisors.frontrunner_builder.run_frontrunner_build()` (CC-2 lazy import as `_fbld`) over ALL live symphonies -- this is the Frontrunner Builder's own AC-1 weekly-cadence requirement, reusing this module's scheduler rather than adding a second one. Isolated in its own try/except so a frontrunner failure never blocks or aborts the objective loop above it, which has already completed by that point. This call NEVER creates a Composer symphony directly -- accepted frontrunner candidates are only queued for operator approval (`frontrunner_proposals` table); the actual Composer create happens exclusively via the operator-driven `/approve` route (not yet built, see `docs/generated/advisors_frontrunner_builder.md`). See `DE-FRONTRUNNER-001` in `DECISIONS.md`.

## Internal Helpers

### `_already_ran_this_week() -> bool`

Patchable idempotency seam. Checks `database.get_advisor_observations_for_symphony(symphony_id="", advisor_role="STRATEGY_BUILDER", limit=50)` for any row whose `created_at` falls in the current ISO year/week. Degrades to `False` (run anyway) on any DB error (D-1). Tests monkeypatch this to `True` (same-week no-op) or `False` (fresh run).

## Design Decisions

**Idempotency is ISO-week scoped, not day-scoped.** The Strategy Builder run is computationally expensive (one backtest per candidate, all four objectives). Weekly granularity matches the community-strats Atlas cache TTL and provides enough freshness for the operator dashboard without hammering the Composer API daily.

**`universe=[]` is always passed.** The scheduler always self-sources from C1 (`universe_provider.get_tradeable_set()`). It does not maintain its own ticker list — that is the universe provider's responsibility.

**Atlas injection is per-objective, not once-for-all.** `load_atlas_candidates(objective)` is called inside the per-objective loop (not hoisted above it) so each objective's Atlas admission uses the correct ranking stat (e.g. lowest drawdown for `cut_drawdown`, lowest volatility for `volatility_mitigation`). A hoisted call with any single objective would incorrectly rank Atlas candidates for the other three.

**D-1 contract is stricter than `prism_scheduler.py`'s bounded retry.** `prism_scheduler.py` fails loudly after exhausting attempts (exit 1). The strategy scheduler continues to the next objective after exhaustion — a single objective's persistent failure should not block the others from running.

**`symphony_id=""` for all scheduler observations.** The scheduler has no per-symphony context; all observations are keyed to the empty-string symphony ID. This matches the `_already_ran_this_week` check, which queries `symphony_id=""`.

## Internal Dependencies

- `advisors.strategy_builder_engine` — `Objective`, `ScreenConfig`, `propose_strategies` (CC-2 lazy import)
- `advisors.build_plan_generator` — `load_atlas_candidates` (CC-2 lazy import as `_bpg`, per-objective Atlas injection)
- `advisors.frontrunner_builder` — `run_frontrunner_build()` (CC-2 lazy import as `_fbld`, AC-1 weekly hook, after the objective loop)
- `database` — `get_advisor_observations_for_symphony` (inside `_already_ran_this_week`)

No import of `alpha_bot_execution`, `app`, `autotuner`, or any execution module. Off-execution-path; advisory-only.
