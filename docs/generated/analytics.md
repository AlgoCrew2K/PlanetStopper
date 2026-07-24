# analytics

> Performance/History tab data layer -- loads per-day `post_mortem_<YYYY-MM-DD>.json` snapshots and exposes aggregate/per-symphony return series plus quantstats-derived risk metrics.

**Source:** `analytics.py`
**Last updated:** 2026-07-24 (guard-alpha-preconditions, live-gate correction R3, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- `get_shadow_current_return_daily_series` corrected to NOT epoch-scope: concatenates ALL position epochs now, since production position_epoch churns every 1-2 trading days and the original current-epoch scoping capped n_obs at 1-2 permanently; see the updated section below). Prior: 2026-07-23 (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- new `get_shadow_current_return_daily_series`, the "shadow" sample accessor for `GET /api/guard-alpha-preconditions`; see the new section below and `docs/generated/guard_preconditions.md`). Prior: 2026-07-21 (fix-ops-cluster, `DE-OPS-CLUSTER-001` F-1 -- an optional `conn: sqlite3.Connection | None = None` kwarg threaded through 11 functions to fix the `/api/state` ~157-connects/poll finding; see the F-1 section below; and F-020 -- `get_history_summary` gains `daily_dates`/`daily_exits`, see that function's own section). Prior: 2026-07-21 (fix-display-cluster, `DE-DISPLAY-TRUTH-001` F-018 -- `compute_windowed_portfolio_strip` documented for the first time; see the section below). Prior: 2026-07-20 (fix-f008-data-integrity, `DE-POSTMORTEM-INTEGRITY-001` -- first doc-gen entry for this module; round-2 review hardening at `2eac42d0`)

**Coverage note (honest scope):** `analytics.py` is a large module (quantstats risk metrics, windowed strip/history aggregation, portfolio return series) with no exhaustive `docs/generated/` entry. This file documents the post-mortem-reading surface (`_POST_MORTEMS_DIR`, `is_valid_post_mortem_entry`, `load_post_mortem_history`, `get_history_summary`), `compute_windowed_portfolio_strip`, and, as of this cycle, `get_shadow_current_return_daily_series` -- the functions successive cycles have actually touched or depended on. The remainder of the module's public API (quantstats metrics, portfolio return-series helpers, etc.) is a pre-existing documentation gap, flagged to the PM as backlog -- not silently backfilled here, to keep this entry scoped to what was actually verified against each cycle's GREEN diff.

## Overview

`analytics.py` loads post-mortem snapshots written by `reporting.py:generate_eod_snapshot` and exposes aggregate / per-symphony return series plus quantstats-derived risk metrics for the Performance and History dashboard tabs. Every post-mortem trigger entry carries a producer-mapped field set (see `docs/generated/reporting.md`'s Stage-1 output keys) plus, since `DE-GUARD-ALPHA-SAVED-001` (PR #80), an `if_held_source` provenance stamp.

## API Reference

### F-1 -- shared read-only connection threading (`DE-OPS-CLUSTER-001`, 2026-07-21)

**The finding:** the PM reproduced ~157 SQLite connects / 1.4s on a single real `/api/state` poll. Every per-symphony and per-portfolio analytics helper below opened its OWN `sqlite3.connect()` call -- most of them internally, inside a loop over every symphony (via `_value_weighted_portfolio` and `_get_windowed_divergence_trajectory`) -- so a portfolio strip that fans out through 5 portfolio-level helpers times N symphonies produced O(helpers x symphonies) connects for ONE poll.

**The fix:** an optional `conn: sqlite3.Connection | None = None` keyword param, added to the END of each function's signature (never repositioning existing positional/keyword params), threaded through 11 functions:

| Function | Role |
|----------|------|
| `get_symphony_today_change` | per-symphony today-change |
| `get_symphony_cumulative_return` | per-symphony cumulative return |
| `get_symphony_max_drawdown` | per-symphony max drawdown |
| `get_portfolio_today_change` | portfolio-level today-change (loops every symphony) |
| `get_portfolio_cumulative_return` | portfolio-level cumulative return (loops every symphony) |
| `get_portfolio_max_drawdown` | portfolio-level max drawdown (loops every symphony) |
| `compute_windowed_symphony_guard_alpha` | per-symphony windowed guard-alpha |
| `compute_windowed_portfolio_strip` | windowed portfolio strip (the hero headline's data source) |
| `_load_latest_shadow_row_for_analytics` | internal: latest `shadow_history` row lookup |
| `_get_shadow_divergence_trajectory` | internal: per-symphony divergence trajectory |
| `_get_windowed_divergence_trajectory` | internal: windowed divergence trajectory (the loop `get_portfolio_*` helpers fan out through) |

**Contract:** `conn=None` (the default on every one of the 11 functions) is byte-identical to pre-fix behavior -- each function opens and closes its own connection exactly as before. When a caller passes a pre-opened `sqlite3.Connection`, the function uses it instead and never closes it -- lifecycle ownership stays with the caller (`app.py`'s `get_api_state_dict()` / `get_state()`, see `docs/generated/app.md`). `_load_latest_shadow_row_for_analytics`'s `row_factory` save/restore is wrapped in an inner `try/finally` so the restore runs even if the query raises -- exception-safe regardless of whether it's operating on its own connection or a shared one; it is the ONLY function in this module that mutates `row_factory` (grep-confirmed).

**Blast radius:** only 3 production files reference any of these 11 functions -- `alpha_bot_execution.py` (the engine's one call site, unaffected, no `conn=`), `analytics.py` itself (internal call chains), and `app.py` (the two threaded call sites plus `dashboard()`'s own separate per-symphony loop, `app.py` ~1018-1033, which is intentionally NOT threaded -- out of this fix's scope).

---

### `compute_windowed_portfolio_strip(symphonies: list, bot_state: dict, *, window: str, db_path: str | None = None) -> dict`

Computes the windowed portfolio strip (the hero guard-alpha headline's data source) for a given time window token (`30d`/`60d`/`90d`/`125d`/`ytd`/`1y`/`all`), including `guard_alpha` -- the value-weighted `windowed_alpha` across the portfolio's symphonies.

**F-018 basis-consistency fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21).** The `windowed_alpha` loop now excludes the TWR-fallback symphony (`sym.get("simple_return") == 0.0 and sym.get("net_deposits") == 0.0` -- the identical condition `get_symphony_cumulative_return` checks at `analytics.py:826` to set `_twr_fallback`), matching the exclusion its own `if_held` anchor already applies via `get_portfolio_cumulative_return` -> `_value_weighted_portfolio` (the shipped F4 fix, `analytics.py:972-978`).

**Why this matters:** before this fix, `windowed_alpha` was value-weighted across *all* symphonies (including the TWR-fallback one) while the `if_held` anchor it is compared against excluded that same symphony -- two different weighting populations feeding one ratio. Net effect: the windowed guard-alpha headline (the tool's core value metric, e.g. "GUARD ALPHA 30D") systematically **under-reported** roughly 2x on every window. Verified on the golden fixture `tests/fixtures/math/guard_alpha_windowed_basis_mix.json` (`tests/analytics/test_windowed_strip.py::TestF018WindowedAlphaBasisConsistency`, expected values derived in-test from raw `shadow_history` rows, never hardcoded) and independently corroborated against a real live-DB snapshot recompute (`docs/audit/confidence-program/truth/f014-lifetime-label-basis-mismatch.md`): **0.627195 (old, mixed-basis) vs 1.241529 (fixed, 10-symphony-consistent basis)**.

This is a **display-truth fix, not a math-layer redesign**: the exclusion condition is inlined with a comment (not extracted to a new named constant) because it mirrors an existing shipped exclusion at `analytics.py:826` rather than introducing a new tunable threshold. See `DE-DISPLAY-TRUTH-001` in `DECISIONS.md` for the full account, including the F-016 escalation this cycle also shipped.

---

### `is_valid_post_mortem_entry(entry: object) -> bool`

Single source of truth for the post-mortem data-integrity guard (`F-008`, `DE-POSTMORTEM-INTEGRITY-001`) -- returns `True` iff `entry` is a `dict` and its `if_held_source` field is a `str` matching one of the 3 producer-recognized values:

```python
_TRUSTED_IF_HELD_SOURCES = frozenset(
    {"shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"}
)
```

These are exactly the 3 tiers `reporting.py:generate_eod_snapshot`'s Stage-1 if-held lookup declares (see `docs/generated/reporting.md`). An entry that is not a `dict`, or whose `if_held_source` is missing, non-string, or not one of these 3 values, is invalid. Never raises.

**Round-2 review hardening (AC-4, `2eac42d0`):** the initial implementation checked `entry.get("if_held_source") in _TRUSTED_IF_HELD_SOURCES` directly -- safe for the expected string case, but a `list`/`dict` value (a corrupted-but-syntactically-valid post-mortem file stamping the wrong type) would raise `TypeError` on the frozenset membership check (unhashable type), crashing every caller instead of degrading to "invalid." Fixed with an explicit `isinstance(value, str)` guard before the membership test, closing a blast-radius gap the reviewer found.

**Why this exists:** two historical post-mortem days -- the real captured `post_mortem_2026-06-22.json` and a 2026-07-09-style day -- predate the `if_held_source` field entirely (it did not exist before PR #80) and carry wrong/sign-flipped `saved_dollars`. Before this cycle, every reader of `post_mortem_*.json` summed `saved_dollars` unconditionally, so these two contaminated days rode silently into the operator's $-saved headline, History tab, and Performance tab.

**Callers (all 3 live post-mortem-reading consumers route through this one function so they cannot diverge):**
1. `app.guard_alpha_summary()` (`app.py:2690`) -- the `$`-saved headline aggregate. See `docs/generated/app.md`'s `GET /api/guard-alpha-summary` section.
2. `analytics.load_post_mortem_history` (below).
3. `analytics.get_history_summary` (below).

**Honest scope note:** this is a PROVENANCE guard, not a value-recompute. A hypothetical future entry stamped with a RECOGNIZED `if_held_source` but still numerically wrong would NOT be caught -- no such case exists in the fixtures verified for this cycle (both known-contaminated days are unstamped, caught by "missing" alone). See `DE-POSTMORTEM-INTEGRITY-001` in `DECISIONS.md` for the full ruling, including why the trusted set is 3 values rather than just `"shadow_history"`.

---

### `load_post_mortem_history(days: int = 60, base_dir: str = ".") -> dict`

Loads up to `days` most-recent `post_mortem_<YYYY-MM-DD>.json` files from `base_dir` and returns the DV1 internal shape: `{date_str: {symphony_id: {"live_ret", "f_ret", "value", ...trigger}}}`, applying the producer-field mapping (`symphony_name`->key, `symphony_value`->`value`, `shadow_return`->`live_ret`, `exit_return`->`f_ret`; all other trigger fields carried forward verbatim).

**F-008 validity guard (AC-5):** each trigger entry is passed through `is_valid_post_mortem_entry` before being folded into the returned shape -- an entry lacking a recognized `if_held_source` is skipped, the same as a `None`/non-dict trigger.

**Resilience:**
- Missing/unreadable files are silently skipped.
- Malformed JSON is silently skipped (does not raise).
- Files without a parseable date in the filename are skipped.
- When more than `days` files exist, the `days` most-recent (by the date embedded in the filename) are kept.

---

### `get_history_summary(days: int = 30, base_dir: str = ".") -> dict`

Aggregates guard-alpha history for the History tab. Returns the envelope `GET /api/history/<int:days>` (`app.py:3095` `get_history()`) emits directly: `total_alpha`, `total_saved`, `trigger_count`, `wins`, `by_reason` (per-exit-reason `{alpha, count, wins, dollars}`), `avg_guard_alpha`, `win_rate`, `daily_alpha` (chronological list), plus `todays_exits` (today's post-mortem file, or an `exit_triggers` intraday fallback -- see `docs/generated/app.md`'s `GET /api/history/<int:days>` section for that fallback's semantics).

**F-008 validity guard (AC-5b, added at plan-approval 2026-07-20 after `get_history_summary` was found to be a THIRD independent unguarded post-mortem consumer, not originally scoped in the plan's first draft):** each trigger entry within the `[start_date, end_date]` window is passed through `is_valid_post_mortem_entry` -- an invalid entry contributes to neither `total_alpha`/`total_saved`/`trigger_count`/`wins`/`by_reason`/`daily_alpha`. Without this, AC-5's stated goal (History/Performance don't serve contaminated days) would have been defeated by this route's own headline-stats producer.

**Not touched by this guard:** the `todays_exits` intraday fallback (`exit_triggers`/`shadow_history` live query, not a post-mortem file read) -- the `if_held_source` provenance stamp only exists on post-mortem JSON, so it doesn't apply there.

**F-020 per-day drill-down fields (`DE-OPS-CLUSTER-001`, 2026-07-21):** the envelope also carries `daily_dates` (a chronological list of ISO date strings, parallel index-for-index to `daily_alpha` -- backs `static/history.js`'s per-bar tooltip/click hook) and `daily_exits` (a `{date_str: [exit_entry, ...]}` map, one key per day that had at least one trigger -- a zero-trigger day is simply absent from the map, never an empty-list key). Both are a pure reshape of the SAME already-parsed `reason`/`alpha`/`t.get(...)` locals the rest of this function already computes inside its existing per-day loop -- zero new I/O, zero new computation. Each `daily_exits` entry additionally carries `time_triggered` (sourced `t.get("time_triggered") or t.get("timestamp") or t.get("ts", "")`, the identical sourcing expression `todays_exits` already uses) so the drill-down table can render a timestamp column the same way the live "Today's exits" table does.

### `get_shadow_current_return_daily_series(symphony_id: str, db_file: str) -> list[float] | None`

New this cycle (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001`, 2026-07-23) -- the "shadow" (live) sample accessor for `GET /api/guard-alpha-preconditions` (see `docs/generated/app.md`; math consumer is `docs/generated/guard_preconditions.md`).

Returns the RAW per-day `current_return` values from `shadow_history`, in trading-day order, **EOD-row-only** (last row per `trading_day` by `ts_utc` -- never an intraday row), **concatenated across ALL position epochs** (no epoch filter), and **never differencing** the values: `current_return` is already a per-day return, not cumulative (proven against production data, see project memory `project_shadow_return_per_day_proven_empirically.md`; the plan's original AC-5 wording called for "daily diffs," which the PM amended after this recon finding -- differencing an already-per-day series would have been a return-of-a-return error). Returns `None` only when the symphony has zero rows (genuinely absent); a present-but-thin symphony (even one day) returns a real short list -- insufficiency is `guard_preconditions.compute_persistence_stats`'s job via `N_MIN_OBS`, not this accessor's.

**NOT epoch-scoped (corrected 2026-07-24 at the PM's live gate, `DE-GUARD-ALPHA-PRECONDITIONS-001`).** The original implementation scoped this query to the current `position_epoch` only, mirroring `_get_shadow_cumulative_trajectory`'s epoch-resolution pattern below. A read-only probe of the production droplet DB found `position_epoch` is a UUID that churns every 1-2 trading days (14-18 distinct epochs per symphony over 23 retained trading days), so epoch-scoping capped every symphony's `n_obs` at 1-2 -- permanently `INSUFFICIENT_DATA` in production despite real trading history existing. The fix removed the epoch filter entirely (and with it, the `has_epoch_column` branch -- the legacy no-`position_epoch`-column path and the migrated-schema path are now byte-identical, a single query). **This does not contradict the epoch-additive rule `_get_shadow_cumulative_trajectory` still correctly follows** (see that function's own docstring/section below, UNCHANGED by this fix): that rule protects a CUMULATIVE level from chaining a phantom return across a position reset. A per-day `current_return` is that day's own independent if-held observation regardless of which epoch it fell in, so concatenating independent daily observations across epochs is statistically valid -- a different operation from chaining a cumulative level.

**Deliberately does NOT use `database._shadow_cr_cache`.** That shared dict is keyed `(symphony_id, today, db_file, resolved_epoch)` regardless of which `shadow_history` column is cached under that key, and `_get_shadow_cumulative_trajectory` already populates it for `shadow_return` under that exact key shape -- reusing it here for a different column (`current_return`) risked one accessor silently serving the other's cached series for the same key. This route is off-execution-path and advisory-only, so the correctness risk outweighed the caching win; the cache's key-shape gap is tracked as a follow-on in `feature-plans/BACKLOG.md`.

## Internal Dependencies

- `database` -- consulted by other (undocumented-in-this-entry) parts of this module for live state.
- `reporting.py` -- upstream producer whose `generate_eod_snapshot` trigger schema this module consumes; `is_valid_post_mortem_entry`'s trusted set is a direct mirror of `reporting.py`'s 3-tier `if_held_source` lookup (see `docs/generated/reporting.md`).

See `DE-POSTMORTEM-INTEGRITY-001`, `DE-DISPLAY-TRUTH-001`, and `DE-GUARD-ALPHA-PRECONDITIONS-001` in `DECISIONS.md`.
