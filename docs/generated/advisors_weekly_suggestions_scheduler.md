# advisors/weekly_suggestions_scheduler

> Weekly Suggestions Orchestrator (Workstream B) plus the two previously-missing per-symphony loop callers (Workstream C.1/C.2): wires Strategy Builder, Asset Swap, and Logic Change to real unattended weekly execution.

**Source:** `advisors/weekly_suggestions_scheduler.py` (new module, advisor-rewire cycle, 2026-07-12)
**Last updated:** 2026-07-12

## Overview

Before this cycle, three advisor suggestion engines existed with complete implementations and test suites but no scheduled/automatic caller: `advisors.strategy_builder_scheduler.run_weekly_build()` (existed, but its own idempotency guard was broken — see `docs/generated/advisors_strategy_builder_scheduler.md` AC-A1), `advisors.asset_swap_engine.suggest_swaps()`, and `advisors.logic_change_engine.suggest_logic_changes()` (the latter two had no caller of any kind, scheduled or manual-triggered-in-production). This module is the single weekly entry point that wires all three to real, unattended, recurring execution.

Invoke:
```
python -m advisors.weekly_suggestions_scheduler
```

Off-execution-path: not imported from `alpha_bot_execution.py` (statically verified by a test). No `LIVE_EXECUTION` reference anywhere in the module (statically verified). No order-placement path — advisory-only, read + inline-backtest Composer endpoints only, via the UNCHANGED underlying engines.

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_CORRELATION_LOOKBACK_DAYS` | `90` | Calendar-day lookback for the asset-swap loop's `correlation_data` bar fetch. ~63 trading days — clears `correlation_diagnostic.py`'s `THIN_DATA_THRESHOLD=30`-observation floor with margin, while staying inside a single weekly oneshot's Composer/Alpaca budget. |
| `_ASSET_SWAP_CANDIDATE_POOL_SIZE` | `15` | Bounded, deterministic-alphabetical sample size drawn from `universe_provider.get_tradeable_set()`'s full ~12,748-symbol universe for the weekly asset-swap loop. Each pool member costs one Composer `/backtest` call (rate-limited to 1 req/s inside `suggest_swaps`) — an unbounded full-universe sweep would make a single symphony's weekly run take hours. |
| `_DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE` | `"reduce_drawdown"` | Default `LogicChangeObjective.objective_type` for the unattended weekly sweep — not pinned by the RED tests; chosen as the most broadly protective default absent an operator-specified target. |
| `_DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE` | `"reduce_correlation"` | Default `SwapObjective.objective_type` for the unattended weekly sweep — AC-C2 v1 scope boundary, pinned by `TestAssetSwapLoopObjectiveDefault`. |

## API Reference

### `run_weekly_suggestions() -> None` (AC-B1)

Runs all three weekly advisor engines in sequence, each wrapped in its OWN `try/except` (D-1 isolation — one engine's failure never blocks the next, never propagates even when all three fail):

1. `strategy_builder_scheduler.run_weekly_build()`
2. `run_weekly_asset_swap_suggestions()` (this module)
3. `run_weekly_logic_change_suggestions()` (this module)

`if __name__ == "__main__":` guard makes `python -m advisors.weekly_suggestions_scheduler` invocable. Never raises, even if all three engines fail.

**AC-B2 (same-ISO-week idempotency):** per-engine, not orchestrator-level. `strategy_builder_scheduler._already_ran_this_week()` guards step 1. The swap/logic engines persist `ASSET_SWAP`/`LOGIC_CHANGE` rows that a role-filtered dedup guard (the same `get_advisor_observations_for_role` pattern as AC-A1) can query the same way — this orchestrator adds no conflicting outer guard.

**Does NOT modify `strategy_builder_scheduler.py`** — that module stays Strategy-Builder-only per its own AC-18 scope (a static test asserts `strategy_builder_scheduler` does not gain a `run_weekly_suggestions` attribute).

### `run_weekly_asset_swap_suggestions() -> None` (AC-C2)

Enumerates every live symphony (`database.load_state()`, filtered via `_live_symphony_hashes`), and for each one:

1. Fetches the symphony's score tree via `symphony_logic.fetch_symphony_score(symphony_hash)`.
2. Extracts held tickers via `advisors.asset_swap_engine.extract_tickers(score_tree)`.
3. Assembles `correlation_data` (ticker → daily-return-series) via `_build_correlation_data`, over `held_tickers ∪ candidate_pool` (the candidate pool is a bounded, deterministic-alphabetical sample of `_ASSET_SWAP_CANDIDATE_POOL_SIZE` tickers from `universe_provider.get_tradeable_set()`, fetched ONCE per run, not per symphony).
4. Builds a `SwapObjective(objective_type=_DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE, target_pair=(primary_ticker, primary_ticker), ...)` — `target_pair` is a documented v1 simplification: the symphony's own alphabetically-first held ticker. True best-pair selection is `correlation_diagnostic.py`'s separate, more sophisticated job — out of this loop-wiring workstream's scope.
5. Calls `advisors.asset_swap_engine.suggest_swaps(symphony_hash, score_tree, objective, correlation_data, candidate_pool, lens_scores=lens_scores)`.

**Lens-scores wiring (AC-C2 completion, 2026-07-12):** `lens_scores` is fetched ONCE per weekly run (not per symphony — lens evidence is market-wide) via `_fetch_lens_scores()` and passed to every `suggest_swaps` call. This closes a "fixed math, dead in production" gap: Workstream D fixed `_apply_lens_blend`'s formula, but `_apply_lens_blend` is reachable ONLY via `generate_objective_directed_candidates <- suggest_swaps <- run_weekly_asset_swap_suggestions` (`propose_operator_swap` does not use the blend), so the fixed math was still unreachable from any real production path until this wiring landed — in the same cycle, not deferred.

Per-symphony D-1: one symphony's score-fetch/universe-fetch/bar-fetch failure or `suggest_swaps` exception never blocks the others (each iteration is independently `try`/`except`-wrapped). Never raises.

### `run_weekly_logic_change_suggestions() -> None` (AC-C1)

Enumerates every live symphony (same `_live_symphony_hashes` helper), and for each one:

1. Fetches the score tree via `symphony_logic.fetch_symphony_score(symphony_hash)`.
2. Builds a `LogicChangeObjective(objective_type=_DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE, measured_value=0.0, rationale="Weekly automatic suggestion sweep...")`.
3. Calls `advisors.logic_change_engine.suggest_logic_changes(symphony_hash, score_tree, objective)`.

Per-symphony D-1, same shape as the asset-swap loop. Never raises. `suggest_logic_changes` itself is UNCHANGED by this workstream (AC-C3) — this module is purely the enumeration/caller layer.

## Internal Helpers

### `_live_symphony_hashes(bot_state: dict) -> list`

Returns the Composer-hash keys of every live symphony in `bot_state` (`database.load_state()`) — filters out reserved top-level scalars (`"date"`, `"last_execution_mode"`, etc.) by requiring each value be a `dict` with a `"name"` key. Same filter convention used throughout `app.py` (e.g. `app.py:1156`, `app.py:2565`).

### `_closes_from_bars(bars: list | None) -> list`

Extracts a close-price series from a bar list. Tolerates real Alpaca daily-bar dicts (`{"c": price, ...}`, per `synthetic_history.fetch_bars`) AND plain numeric sequences (test-double shapes). Never raises — malformed entries are silently skipped.

### `_returns_from_closes(closes: list) -> list`

Converts a close-price series to daily percent returns. Returns `[]` when fewer than 2 closes are available, or skips a step where the prior close is `0` (avoids divide-by-zero).

### `_build_correlation_data(tickers: set) -> dict`

Fetches bars for `tickers` via `synthetic_history.fetch_bars` (read-only GET, no write verb) and converts each ticker's bars to a daily-return-series. Returns `{}` for an empty ticker set, a non-dict fetch response, or when no ticker yields a usable series — `suggest_swaps` already handles an empty/degenerate `correlation_data` honestly (neutral scoring, never a crash).

### `_fetch_lens_scores() -> dict`

Read-only fetch of market-wide lens evidence, called ONCE per weekly run (lenses are market-wide, not per-symphony — the same dict is passed to every `suggest_swaps` call). Sourced from `database.get_latest_market_lens_cache()` → `raw_response["lenses"]` → `advisors.asset_swap_engine.extract_lens_scores(lenses)`. **NEVER a live lens-API fetch** — the 5 lens producers are `advisors.lens_pipeline`'s job (nightly, 03:00); re-fetching them live here would blow the weekly run's bounded budget and duplicate that pipeline's work. Honest degradation: a cold cache (no row yet) or a row whose lenses are all `available=False` both degrade to `{}`, which `_apply_lens_blend` already treats as a no-op (falsy check, same as the pre-existing `lens_scores=None` contract) — never fabricates evidence. Defense-in-depth `try/except` around the DB read even though `get_latest_market_lens_cache` is itself D-1 never-raising.

## Design Decisions

**One orchestrator, three engines, one module.** All of Workstream B (orchestrator) and C.1/C.2 (loop callers) live in this single new file rather than three separate modules, because the loop functions and the orchestrator share the D-1 / bounded-retry / `.env`-credential shape and the orchestrator needs direct access to the loop function names. `strategy_builder_scheduler.py` was deliberately NOT extended to hold the new loops (it stays Strategy-Builder-only, AC-18).

**No `MAX_BUDGET_USD`-style spend guard (unlike `prism_scheduler.py`).** This module makes NO direct Anthropic API calls — its cost surface is Composer `/backtest` calls (rate-limited to 1 req/s inside the engines) and Alpaca bar fetches. The bounding mechanism here is `_ASSET_SWAP_CANDIDATE_POOL_SIZE` (caps the per-symphony backtest count) plus each engine's own existing bound (`strategy_builder_scheduler.MAX_ATTEMPTS`, `logic_change_engine.MAX_SUGGESTED_CANDIDATES`).

**Every persisted row still carries `is_advisory_only=1`** (forced inside `database.insert_advisor_observation` regardless of caller) — AC-C4. No new write path, no trade action, no `LIVE_EXECUTION` interaction anywhere in this module.

## Deployment (AC-B3)

This module is deployed via a systemd oneshot service + weekly timer — see `docs/DEPLOYMENT.md` §"Step 9 — Weekly Suggestions scheduler" for the exact unit files. Key facts:

- `OnCalendar=*-*-* Mon 04:00 America/New_York`, `Persistent=true` (a missed run fires as soon as the system is back up).
- `EnvironmentFile=/opt/planetstopper/.env` ONLY — this is the metered-API-key SDK path (`ANTHROPIC_API_KEY` from `.env`), NOT the Market Prism council's OAuth/subscription path (`prism_scheduler.py` strips `ANTHROPIC_API_KEY` and uses a separate `council-env` `EnvironmentFile`; this module does neither — it makes no direct Claude API calls at all, see "Design Decisions" above).
- Runs as non-root `planetstopper` (`User=planetstopper`).
- Droplet timer REGISTRATION (`systemctl enable --now`) is a PM-gated deploy step — this cycle ships only the unit files + docs.

## Internal Dependencies

- `database` — `load_state`, `get_latest_market_lens_cache`
- `symphony_logic` — `fetch_symphony_score`
- `advisors.strategy_builder_scheduler` — `run_weekly_build`
- `advisors.asset_swap_engine` — `SwapObjective`, `extract_tickers`, `extract_lens_scores`, `suggest_swaps`
- `advisors.logic_change_engine` — `LogicChangeObjective`, `suggest_logic_changes`
- `advisors.universe_provider` — `get_tradeable_set`
- `synthetic_history` — `fetch_bars`

All imports are lazy (function-local, CC-2) — off-execution-path, never imported from `alpha_bot_execution.py` (static text/AST guard tested).
