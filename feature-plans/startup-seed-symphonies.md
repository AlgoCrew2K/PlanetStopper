# Feature Plan — Startup Symphony Seeding

Status: ready

## Summary
The daemon only populates the per-symphony `bot_state` entries inside the market-hours-gated DATA PHASE of `alpha_bot_execution.py:main()` (gate at `alpha_bot_execution.py:636`: `if not is_trading or current_time > post_mortem_cutoff or current_time < REAL_MARKET_OPEN: return`). The weekend/closed path (`alpha_bot_execution.py:644`) calls `fetch_symphony_stats` but only refreshes symphonies **already** present (`if _s_id in _closed_bot_state`) — it never *creates* entries. Consequently, after a DB wipe (or any first start with empty `bot_state`) that happens outside market hours, the dashboard shows **0 symphonies** until the next market-open cycle.

Add a **startup seed**: when the daemon starts, if `bot_state` has no symphony entries, fetch the symphony list from Composer and create the baseline entries — regardless of market hours — so the dashboard reflects the live portfolio immediately. Idempotent, fail-safe, and collection-clean (no historical telemetry rows written).

## Acceptance Criteria
- **AC-1 — Seed-when-empty on startup.** On daemon startup, if `bot_state` contains no symphony entries, the daemon fetches symphonies for every configured account (`ACCOUNT_UUIDS`) via `fetch_symphony_stats` and **creates** the per-symphony `bot_state` entries, regardless of market hours / weekday.
- **AC-2 — Idempotent / non-clobbering.** If `bot_state` already has ≥1 symphony entry, startup performs NO seeding and does not modify existing entries (high_water_mark, shadow_hwm, triggered, current_holdings, etc. are preserved untouched). The "is it seeded?" test is based on presence of symphony entries, not a flag.
- **AC-3 — No collection pollution.** Startup seeding creates only the display/operational baseline fields needed for the dashboard + engine continuity. It MUST NOT insert any `shadow_history` rows or write any post-mortem files (the historical Guard-Alpha collection). Verifiable: `shadow_history` row count is unchanged by a startup seed.
- **AC-4 — Fail-safe startup.** If the Composer fetch raises / times out / returns an error for any/all accounts, the seed logs the failure (clear message, no secret leakage) and the daemon continues startup normally (never crashes, never blocks serving). Partial success allowed: seed the accounts that succeed, log the ones that fail (D-1 contract — degrade, never raise).
- **AC-5 — Market-hours cycle continuity.** After an off-hours startup seed, the next market-hours DATA PHASE cycle operates on the seeded entries with no duplication and no reset of the seeded baseline (the existing `if _s_id in bot_state` / create-or-update logic behaves correctly against seeded entries).
- **AC-6 — Empty account safe.** If a configured account returns 0 symphonies, `bot_state` is left without entries for that account and no error is raised.
- **AC-7 — Startup-cost bounded.** The seed runs once at startup (not on the per-minute execution path) and must not block the Flask server from coming up indefinitely — the Composer fetch carries the same bounded timeout/retry the existing `fetch_symphony_stats` uses.

## Architecture
- **Extract the entry-creation logic.** The per-symphony entry *creation* currently lives inline in `main()`'s DATA PHASE (around `alpha_bot_execution.py:860–896`, where `bot_state[s_id][...]` baseline fields + `current_holdings` are first set, then `_persist_composer_fields_to_bot_state`). Extract a reusable, market-gate-free helper — e.g. `seed_symphonies_into_bot_state(bot_state) -> int` (returns count seeded) — that, for each `account in ACCOUNT_UUIDS`, calls `fetch_symphony_stats(account)` and creates the baseline `bot_state` entry for each symphony id (id, name, current_holdings, baseline high_water_mark / shadow_hwm from `simple_return`/current return) + applies `_persist_composer_fields_to_bot_state`. It MUST NOT call the `shadow_history` telemetry write path (the `compute shadow_return / write telemetry row` block after line 896).
- **Conditional entry point.** Add `ensure_bot_state_seeded()` (or similar) that `load_state()`s, returns early if any symphony entry exists (AC-2), else calls the helper and `save_state()`s. Wrap in try/except → log + return on failure (AC-4).
- **Hook into daemon startup.** Call `ensure_bot_state_seeded()` once during `app.py` daemon startup, before the minute scheduler begins (and before/around the Flask `app.run`), off the per-cycle execution path. Confirm it runs in the daemon process (not in pytest / not on dashboard read-only opens).
- **Reuse, don't duplicate.** The market-hours DATA PHASE should call the SAME extracted helper for entry creation where practical, so seed logic and cycle logic cannot drift (single source of truth for "create a bot_state symphony entry").
- Constants/timeouts inherit from the existing `fetch_symphony_stats` (no new magic numbers).

## Edge Cases
- Empty `bot_state` (`{}`) after wipe → seeds all (AC-1).
- Already-seeded `bot_state` (mid-week restart) → no-op (AC-2); existing HWM/triggers preserved.
- Composer auth/network failure at startup → daemon still starts (AC-4); next market cycle seeds.
- Partial account failure (1 of N accounts errors) → seed the rest, log the failure.
- Account with 0 symphonies → no entries, no error (AC-6).
- Startup during market hours with empty bot_state → seed runs (AC-1 is market-agnostic); the immediately-following first cycle must not double-create (AC-5).
- pytest / read-only contexts → seed must not fire against the production DB (respect the `DB_PATH`/pytest sentinel; the autouse fixtures must keep tests hermetic).

## Security Considerations
- No credential/secret in logs (account UUIDs may be truncated; never log keys).
- Read-only Composer GET (`fetch_symphony_stats`) — no trade/exec path touched; `LIVE_EXECUTION` untouched.
- No new write paths beyond `bot_state` (the existing `save_state`); not added to `_SETTINGS_WRITE_ALLOWLIST` (it's engine state, not a dashboard write).

## Testing Strategy (adversarial — quant-test-writer)
- RED: seed-when-empty creates N entries for a mocked `fetch_symphony_stats` returning N symphonies, market-closed (mock `is_trading=False`/weekend) — entries present after.
- RED: idempotency — pre-seeded `bot_state` with a sentinel field (e.g. a custom HWM + `triggered=True`) is UNCHANGED after `ensure_bot_state_seeded()` (no clobber).
- RED: no-pollution — `shadow_history` row count is identical before/after a startup seed.
- RED: fail-safe — `fetch_symphony_stats` raising → `ensure_bot_state_seeded()` returns without raising; daemon-startup caller does not propagate.
- RED: partial-failure — account A returns syms, account B raises → A seeded, no raise.
- RED: empty account → no entries, no raise.
- RED: continuity — after seed, a simulated market-hours create/update pass does not duplicate entries.
- All Composer/DB access via existing fixtures/mocks; assert shape/presence, never hardcode producer return values.

## Scope Boundaries
- IN: startup seed of the symphony LIST/baseline `bot_state` entries; extraction of the create helper; the startup hook; tests; docs.
- OUT: changing the market-hours trading/exit logic; changing `shadow_history`/Guard-Alpha collection semantics; adding accounts (the ROTH/TRAD `ACCOUNT_UUIDS` gap is a separate `.env` config item, not this feature); any dashboard template change (the dashboard already renders whatever `bot_state` holds); any live-execution behavior.
