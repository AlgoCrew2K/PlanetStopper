# env_flags

> Boolean environment-flag parsing shared by the engine, the dashboard and the sleeve tick.

**Source:** `env_flags.py`
**Last updated:** 2026-11-06

## Overview

`env_flags` holds the one definition of which spellings mean true. It uses the standard library only, so the engine, `app.py` and `sleeves/tick_orchestrator.py` can all import it without a cycle. It performs no I/O and reads no environment itself: callers pass the raw value.

## API Reference

### `TRUE_SPELLINGS`
The tuple `("true", "1", "yes")`.

### `is_true(raw: str) → bool`
`True` when `raw.lower()` is one of `TRUE_SPELLINGS`. The comparison is exact: whitespace is not stripped (`" true"` is false). A `None` input raises `AttributeError`, so each caller keeps its own default (usually `os.getenv(NAME, "False")`) and its own `None` guard.

| Name | Type | Description |
|------|------|-------------|
| `raw` | `str` | the raw flag text |

**Returns:** `bool`.

Callers: `alpha_bot_execution.LIVE_EXECUTION`; in `app.py` the `SESSION_COOKIE_SECURE` setting and every `LIVE_EXECUTION` read (`_build_meta`, `get_state`, `sell_account`, `get_symphony_settings`) plus the `SLEEVE_LIVE_EXECUTION` read in `arm_sleeve_live`; `sleeves.tick_orchestrator._compute_live_gates` for `SLEEVE_LIVE_EXECUTION`. The accepted spellings are the same as before the helper existed, so no flag changed meaning.

## Types
None.

## Internal Dependencies
None (standard library only).
