# TDD Handoff — startup-seed-symphonies

## Status
GREEN — 22/22 tests passing. No test file issues. No disputed tests.

Implementer GREEN commit: (see git log)

## Status Log
- [GREEN] implementer: GREEN complete — 22/22 tests passing, 0 test bugs documented. Ruff lint ✓.

Phase: green

## What to implement (GREEN phase)

You are the minimalist implementer.  Read THIS FILE, not the feature plan.
Write the minimum code to make all 21 failing tests pass.  No gold-plating.

---

## Two new functions required in alpha_bot_execution.py

### 1. `seed_symphonies_into_bot_state(bot_state: dict) -> int`

Purpose: for each account in `ACCOUNT_UUIDS`, call `fetch_symphony_stats(account)`
and create the per-symphony baseline entry in `bot_state` for each symphony id
that is NOT already present.

Required:
- Create entry for each symphony id not already in bot_state
- The entry MUST include at minimum: `high_water_mark` (set from current_return),
  `shadow_hwm`, `triggered` (False), `armed` (False), `mc_history` ([]),
  `below_stop_count` (0), `position_epoch` (from database.mint_position_epoch()),
  and `name` (from sym["name"]).  Match the existing create-block at
  alpha_bot_execution.py:771-790 — the DATA PHASE create-block.
- Call `_persist_composer_fields_to_bot_state(bot_state, s_id, sym)` after creating
  each entry (same as the DATA PHASE does).
- MUST NOT call `database.record_shadow_observation` (shadow_history write path).
- MUST NOT open/write any post_mortem_*.json files.
- MUST call `fetch_symphony_stats` (not raw requests.get) to inherit its timeout.
- Per-account exception: catch any Exception raised by fetch_symphony_stats, log it
  (`print(...)` is sufficient — no new logging infrastructure), and continue to the
  next account (partial success allowed, no re-raise).
- Returns `int`: count of NEW entries created (0 if nothing was new).

### 2. `ensure_bot_state_seeded() -> None`

Purpose: the conditional startup entry point.

Required:
- Call `database.load_state()`
- If any value in the dict is itself a dict (i.e., any symphony entry exists),
  return immediately — NO save_state, NO fetch. (AC-2: presence-based check)
- Otherwise: call `seed_symphonies_into_bot_state(bot_state)`; if it created >= 1
  entry, call `database.save_state(bot_state)`.
- Wrap the entire body in try/except Exception: log and return (AC-4 fail-safe).
- MUST NOT be called from within `main()` in alpha_bot_execution.py.

---

## Startup hook in app.py

Call `ensure_bot_state_seeded()` once at daemon startup in app.py, BEFORE the
Flask scheduler starts.  Place it in the startup code path that runs in the
daemon process only (not in pytest / not on every request).

Lazy-import it: `from alpha_bot_execution import ensure_bot_state_seeded` inside
the startup function to avoid circular imports.

---

## Tests to make GREEN

File: `tests/engine/test_startup_seed_symphonies.py`
Run: `python -m pytest tests/engine/test_startup_seed_symphonies.py -n0`

All 21 currently-RED tests must pass.  The 1 currently-passing structural test
(`test_ensure_bot_state_seeded_not_called_inside_main_body`) must remain passing.

---

## Do NOT change

- The `shadow_history` write path (`database.record_shadow_observation`)
- The market-hours DATA PHASE in `main()` (lines 766-949)
- The post-mortem write path (lines 1014-1032)
- Any existing test files
- Any migration SQL files

## When GREEN

Run `python -m pytest tests/engine/test_startup_seed_symphonies.py -n0` and
confirm 22 passed / 0 failed.  Then commit on `feat/startup-seed-symphonies`
and SendMessage `seed-testwriter` (me) with your GREEN commit SHA so I can
review the implementation for sufficiency and write any remaining RED tests.

---

## Implementation Notes

### Functions added to alpha_bot_execution.py (end of file, before `if __name__ == "__main__":`)

**`seed_symphonies_into_bot_state(bot_state: dict) -> int`**
- Iterates `ACCOUNT_UUIDS`, calls `fetch_symphony_stats(account)` per account
- Per-account `except Exception`: logs with `print(...)`, continues to next account (partial success)
- For each symphony id not already in bot_state: creates entry matching the DATA PHASE create-block (lines 771-790) including `position_epoch = database.mint_position_epoch()`, then calls `_persist_composer_fields_to_bot_state`
- `last_percent_change` can be `None` in fixture (and live) — handled as `(raw_pct or 0.0) * 100`
- Does NOT call `database.record_shadow_observation`; does NOT open post_mortem files
- Returns count of NEW entries created

**`ensure_bot_state_seeded() -> None`**
- Loads state, checks `any(isinstance(v, dict) for v in bot_state.values())` — early return if any entry exists (no fetch, no save)
- Otherwise calls `seed_symphonies_into_bot_state(bot_state)`, saves if `created >= 1`
- Entire body wrapped in `try/except Exception` — logs, returns (never raises)

### Startup hook in app.py
- Lazy import `from alpha_bot_execution import ensure_bot_state_seeded` inside `if __name__ == "__main__":` block
- Placed BEFORE `threading.Thread(target=run_scheduler, daemon=True).start()`
- NOT called under pytest (the block is guarded by `if __name__ == "__main__":`)

## Test File Issues (for test-writer to fix)
None.

## Disputed Tests
None.
