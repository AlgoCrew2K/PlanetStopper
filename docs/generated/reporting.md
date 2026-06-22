# reporting

> Discord webhook notifications and QuickChart-embedded EOD post-mortem generation.

**Source:** `reporting.py`
**Last updated:** 2026-06-22

## Overview

`reporting.py` handles all outbound notifications and end-of-day reporting. It produces a two-stage daily post-mortem JSON snapshot and sends formatted Discord messages with optional QuickChart performance embeds.

**Stage 1 (15:54 ET):** Freezes math and shadow returns — computes guard-alpha `saved_dollars`, saves the post-mortem JSON.
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
| `live_prices` | `dict or None` | Live price snapshot — passed through but not used for if-held computation (see note below) |

**Stage 1 guard-alpha computation:**

`saved_dollars` per triggered symphony is computed as:

```
f_ret      = sym["triggered_at_return"]          # locked-in exit return (%)
live_ret   = sym["current_return"]               # if-held return (%) — sourced from
                                                 # shadow_history.current_return, kept
                                                 # current by alpha_bot_execution.py
                                                 # post-trigger (alpha_bot_execution.py:1189-1203)
saved_pct  = f_ret - live_ret
saved_dollars = current_value * saved_pct / 100
```

**Note on `live_prices`:** The parameter is accepted for API compatibility but the if-held return (`live_ret`) is sourced from `sym["current_return"]`, not basket-price reconstruction. The engine's `current_return` tracks the live if-held trajectory accurately post-trigger; a basket-snapshot reconstruction from `triggered_basket_snapshot + live_prices` collapsed to `live_ret ≈ f_ret` because basket baseline prices were frozen at exit level (see DE-GUARD-ALPHA-SAVED-001).

**Stage 1 output keys per trigger entry:**
- `symphony_name`, `symphony_value`, `account_id`, `exit_reason`
- `exit_return`, `attempted_trigger_level`, `shadow_return`, `shadow_hwm`
- `saved_pct_guard_alpha`, `saved_dollars`, `hwm_at_trigger`, `time_triggered`
- `symphony_vol`, `strategy_params`, `next_day_holdings`

**Field semantics (post-trigger):**

| Field | Semantics |
|-------|-----------|
| `exit_return` | Locked-in exit return — the Guard-Alpha "sell price" |
| `shadow_return` | Frozen at `triggered_at_return`; never updated post-trigger |
| `saved_pct_guard_alpha` | `exit_return - if_held_return` — positive means the exit saved money |
| `saved_dollars` | `current_value x saved_pct_guard_alpha / 100` |

## Internal Dependencies

- `database` -- `normalize_name`, `get_symphony_strategy`
- `requests` -- Discord webhook HTTP posts
- External: Discord Webhooks, QuickChart API
