# reporting

> Discord webhook notifications and QuickChart-embedded EOD post-mortem generation.

**Source:** `reporting.py`
**Last updated:** 2026-05-27

## Overview

`reporting.py` handles all outbound notifications and end-of-day reporting. It produces a two-stage daily post-mortem JSON snapshot and sends formatted Discord messages with optional QuickChart performance embeds.

**Stage 1 (15:54 ET):** Freezes math and shadow returns — computes guard-alpha, saves the post-mortem JSON.
**Stage 2 (post-rebalance):** Fills in tomorrow's target holdings from Composer.

Post-mortem files are written to `post_mortems/post_mortem_YYYY-MM-DD.json` (directory created on first write, anchored to project root regardless of CWD).

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
| `live_prices` | `dict or None` | Live price snapshot for post-trigger move computation |

**Stage 1 output keys per trigger entry:**
- `symphony_name`, `symphony_value`, `account_id`, `exit_reason`
- `exit_return`, `attempted_trigger_level`, `shadow_return`, `shadow_hwm`
- `saved_pct_guard_alpha`, `saved_dollars`, `hwm_at_trigger`, `time_triggered`
- `symphony_vol`, `strategy_params`, `next_day_holdings`

## Internal Dependencies

- `database` -- `normalize_name`, `get_symphony_strategy`
- `requests` -- Discord webhook HTTP posts
- External: Discord Webhooks, QuickChart API
