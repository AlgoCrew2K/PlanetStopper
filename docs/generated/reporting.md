# reporting

> Discord webhook notifications and QuickChart-embedded EOD post-mortem generation.

**Source:** `reporting.py`
**Last updated:** 2026-07-19 (`DE-AUTOTUNE-REPORTING-001`: `send_eod_discord_post` shape-guards the per-symphony changes dict against non-`{old,new}` sibling entries and distinguishes an aborted autotune run from a genuine no-change day -- see the new API Reference entry below.) Prior: 2026-07-09 (DE-PROD-ACCURACY-001: Stage-1 if-held sourcing corrected to read the `shadow_history` table directly, with explicit `if_held_source` provenance and an off-schedule snapshot-cutoff invariant; supersedes the 2026-06-22 doc claim below)

## Overview

`reporting.py` handles all outbound notifications and end-of-day reporting. It produces a two-stage daily post-mortem JSON snapshot and sends formatted Discord messages with optional QuickChart performance embeds.

**Stage 1 (15:54 ET):** Freezes math and shadow returns -- computes guard-alpha `saved_dollars`, saves the post-mortem JSON.
**Stage 2 (post-rebalance, 16:00 ET):** Fills in tomorrow's target holdings from Composer.

Post-mortem files are written to `post_mortems/post_mortem_YYYY-MM-DD.json` (directory created on first write, anchored to project root regardless of CWD via `os.path.dirname(os.path.abspath(__file__))`).

## API Reference

### `generate_eod_snapshot(bot_state, current_date_str, is_post_rebalance=False, discord_webhook_url=None, live_prices=None) -> None`

Generates the two-stage daily post-mortem JSON snapshot and handles Discord alerts.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `bot_state` | `dict` | Live engine state from `database.load_state()` |
| `current_date_str` | `str` | Today's date in `YYYY-MM-DD` format |
| `is_post_rebalance` | `bool` | `False` = Stage 1 (math freeze); `True` = Stage 2 (fill tomorrow holdings) |
| `discord_webhook_url` | `str or None` | Discord webhook URL; `None` skips Discord notifications |
| `live_prices` | `dict or None` | Live price snapshot -- accepted for API compatibility but not used for if-held computation (see note below) |

**Stage 1 guard-alpha computation (corrected 2026-07-09, DE-PROD-ACCURACY-001 Finding 2):**

If-held return is sourced from the `shadow_history` table, not `bot_state`. Prior to this fix, `live_ret` read `bot_state[sym]["current_return"]` -- a field the action-phase "TRUE SHADOW RETURN OVERRIDE" (`alpha_bot_execution.py:1189-1203`, written at `:1548`) clobbers every cycle with a frozen-basket reconstruction. That reconstruction collapses to roughly `f_ret` (booking $0.00 saved) on basket misses and fabricates values otherwise -- 7 of 11 audited production days were sign-flipped before this fix. The 2026-06-22 fix (`DE-GUARD-ALPHA-SAVED-001`) changed the sourcing *expression* but never actually queried `shadow_history`, despite its own comment claiming it did.

The corrected logic is a three-tier lookup with an explicit provenance marker (`STAGE1_SNAPSHOT_CUTOFF_ET = "15:54:59"`, `reporting.py:22`):

```python
f_ret = sym.get("triggered_at_return", 0.0)

shadow_row = database.load_latest_shadow_row(
    sym_id, current_date_str, et_cutoff=STAGE1_SNAPSHOT_CUTOFF_ET
)
if_held_source = "shadow_history"
if shadow_row is None or shadow_row.get("current_return") is None:
    shadow_row = database.load_earliest_shadow_row(sym_id, current_date_str)
    if_held_source = "shadow_history_post_cutoff"
if shadow_row is not None and shadow_row.get("current_return") is not None:
    live_ret = float(shadow_row["current_return"])
else:
    live_ret = sym.get("current_return", 0.0)
    if_held_source = "bot_state_fallback"

saved_pct = f_ret - live_ret
saved_dollars = current_value * saved_pct / 100
```

1. **`shadow_history` (primary):** the latest `shadow_history` row for the symphony+day at/before the `STAGE1_SNAPSHOT_CUTOFF_ET` cutoff. Holds the declared snapshot-time basis even when Stage 1 runs off-schedule (manual regeneration, a late daemon -- the engine ticks past close to ~16:04).
2. **`shadow_history_post_cutoff`:** when a day's shadow rows are ALL after the cutoff (daemon started after 15:55 ET), the earliest post-cutoff row is used instead -- real off-basis shadow data beats the action-phase-clobbered `bot_state` value.
3. **`bot_state_fallback`:** only when the (symphony, day) has strictly ZERO `shadow_history` rows.

Every consumer can distinguish these tiers via the `if_held_source` field on each trigger entry -- the mechanism that makes the kind of silent regression `DE-GUARD-ALPHA-SAVED-001` shipped structurally harder to repeat, since provenance is now a queryable value instead of a comment.

**`STAGE1_SNAPSHOT_CUTOFF_ET` invariant:** must equal `SNAPSHOT_CUTOFF_ET` in `scripts/regenerate_post_mortems.py` (the historical repair tool) -- enforced by an AST drift-guard test. The repair script deliberately stays import-free for standalone droplet use, so the two constants are independently declared and guard-tested rather than shared via import. The repair script refuses to regenerate an all-post-cutoff day; only the live Stage-1 producer degrades through tier 2/3.

**Note on `live_prices`:** The parameter is accepted for API compatibility but the if-held return (`live_ret`) is sourced from `shadow_history`/`bot_state` as above, not basket-price reconstruction. A prior basket-snapshot reconstruction from `triggered_basket_snapshot + live_prices` collapsed to `live_ret ≈ f_ret` because basket baseline prices were frozen at exit level -- see `DE-GUARD-ALPHA-SAVED-001` for that original bug (its replacement sourcing expression is itself superseded by the fix documented here).

**Stage 1 output keys per trigger entry:**
- `symphony_name`, `symphony_value`, `account_id`, `exit_reason`
- `exit_return`, `attempted_trigger_level`, `shadow_return`, `shadow_hwm`
- `saved_pct_guard_alpha`, `saved_dollars`, `if_held_source`, `hwm_at_trigger`, `time_triggered`
- `symphony_vol`, `strategy_params`, `next_day_holdings`

**Field semantics (post-trigger, corrected 2026-07-09):**

| Field | Semantics |
|-------|-----------|
| `exit_return` | Locked-in exit return -- the Guard-Alpha "sell price" (`triggered_at_return`) |
| `shadow_return` | The *resolved* if-held return used in this snapshot (`round(live_ret, 2)`) -- i.e. whichever of the three tiers above supplied `live_ret`. This is NOT frozen at trigger time; despite the field name, it reflects the live `shadow_history`/`bot_state` lookup at Stage-1 time. |
| `if_held_source` | Provenance marker: `"shadow_history"` \| `"shadow_history_post_cutoff"` \| `"bot_state_fallback"`. No silent source switching -- every entry declares which tier resolved it. |
| `saved_pct_guard_alpha` | `exit_return - if_held_return` -- positive means the exit saved money |
| `saved_dollars` | `current_value x saved_pct_guard_alpha / 100` |

---

### `send_eod_discord_post(current_date_str, report_file, optimization_results, discord_webhook_url) -> None`

Builds and sends the EOD Discord embed(s): the multi-timeframe (1d/7d/30d) performance-summary embed (with an optional QuickChart chart image, attaching `report_file` to the first message batch) plus, when an autotune run was passed in, a per-symphony optimization-changes embed or an aborted-run notice. `discord_webhook_url` falsy (`None`/empty) short-circuits with an early return before any work happens. Never raises -- webhook/parse/malformed-entry failures degrade to a logged error, never a crash of the whole EOD push.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `current_date_str` | `str` | Today's date in `YYYY-MM-DD` format |
| `report_file` | `str` | Path to the day's post-mortem JSON; attached to the first Discord message batch |
| `optimization_results` | `dict or None` | `autotuner.run_autotuner`'s return value -- a truthy per-symphony `{sym_name: changes}` dict, the `{"aborted": True, "reason": ...}` marker, a falsy value (`None`/`{}`, genuine no-op), or `None` to skip the optimization embed entirely (the `resend_discord` route passes `None` deliberately) |
| `discord_webhook_url` | `str or None` | Discord webhook URL; falsy short-circuits before the function does any work |

**Optimization embed branches (`reporting.py:452-518`):**
1. **Aborted** (`isinstance(optimization_results, dict) and optimization_results.get("aborted")`): renders one red "Autotuner Aborted" embed carrying the reason -- visibly distinct from both other branches (`DE-AUTOTUNE-REPORTING-001`, F-004/AC-5).
2. **Per-symphony changes** (`optimization_results` truthy, not aborted): one embed per symphony, delta-only rendering of each real `{old,new}` entry, plus the `_baseline_chosen`/`_selection_stats` special keys rendered separately.
3. **Falsy** (`None`, `{}`): a single "No optimization changes." embed -- a genuine no-op, never conflated with an aborted run.

**Changes-dict shape guard (F-015, `reporting.py:479-488`):** the per-symphony loop skips the two special keys (`_baseline_chosen`, `_selection_stats`, rendered separately above) then, for every remaining `(var, vals)` entry, only indexes `vals["old"]`/`vals["new"]` when `isinstance(vals, dict) and "old" in vals and "new" in vals`. `autotuner.py`'s `eval_window_days` entry (`autotuner.py:2855`, a per-fold day-count stats block written unconditionally into every symphony's changes dict -- NOT a delta) is the entry that previously crashed this loop with an uncaught `KeyError`, killing the entire EOD push on every autotune day. Any future non-delta sibling key is now skipped the same way -- the guard is on the VALUE'S SHAPE, not an enumerated key name, so it needs no maintenance when a new non-delta key is added elsewhere (AC-2's shape-guard-over-skip-list design; see `DE-AUTOTUNE-REPORTING-001`). Real `{old,new}` deltas in the same dict still render regardless of where the malformed entry sits in iteration order (AC-1). A normal all-`{old,new}` day renders byte-for-byte as before the fix (AC-6 golden regression guard).

**Widened exception handling (`reporting.py:547`):** the surrounding `except` tuple now includes `KeyError` alongside `OSError, ValueError, requests.RequestException, TypeError` (AC-3) -- defense-in-depth for any malformed-entry failure mode the shape guard above does not anticipate. A single bad entry degrades to a logged error with the rest of the push still attempted, never a silent crash.

**Regression tests:** `tests/reporting/test_eod_changes_dict_shape_guard.py` (9 tests, AC-1/AC-2/AC-3/AC-6) + `tests/autotuner/test_autotune_abort_paths_structured_marker.py` (8 tests, AC-4/AC-5, parametrized across all 3 `run_autotuner` abort trigger conditions, contract-locking the marker shape end-to-end through this function). See `DE-AUTOTUNE-REPORTING-001` in `DECISIONS.md`.

## Internal Dependencies

- `database` -- `normalize_name`, `get_symphony_strategy`, `load_latest_shadow_row`, `load_earliest_shadow_row`
- `requests` -- Discord webhook HTTP posts
- External: Discord Webhooks, QuickChart API
