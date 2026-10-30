# Approach: Server state truth: one dated, consistent `/api/state` and a flat close snapshot
Spec: ../01-server-state-truth.md
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864, PlanetStopper@8c35a72f (origin/ux-overhaul/05-c1), PlanetStopper@59a1e9dd (origin/ux-overhaul/05-c2), PlanetStopper@8d0aae39 (origin/ux-overhaul/05-c3)

## Architecture

Every `file:line` below is at PlanetStopper@37c8e983 unless it is prefixed with an `origin/ux-overhaul/05-cN:` branch, in which case it is at that branch head. ux-overhaul 05, model-unpin (C9 edits `run_scheduler` and the startup block) and plans 02 and 05 all move `app.py` lines before this plan's later cycles; every cycle re-reads its anchors by function name after its base merges.

### AC-1: flat close snapshot and the one-time repair

**Builder (engine, the only engine edit).** The close-snapshot builder at `alpha_bot_execution.py:1115-1163` copies every dict-valued key of `symphony_keys_eod` (`alpha_bot_execution.py:1066`) into `_eod_accounts_map`. Its loop skips non-dicts at `alpha_bot_execution.py:1121-1122`; that one condition becomes "skip unless a dict carrying `name`", the same structural symphony test used across the code base (`app.py:3764-3779` `_is_symphony_state_entry`, inlined because the engine never imports `app`). Nothing else in the file changes: `symphony_keys_eod` and the divergence loop above it (`alpha_bot_execution.py:1066-1088`, NB-6) keep their input, the analytics calls at `:1134-1151` still receive the full `bot_state`, and the trading-day guard at `:1115-1118` stays. No I/O is added. The reserved-key sets (`database.py:380`, `alpha_bot_execution.py:2128-2133`) are not used, because a name list misses the next non-symphony dict, while the shape test drops it (S-1.5).

**Repair (new, `database.py`).** Searched: `git grep -n -i "def .*repair\|def .*backup\|pre-.*repair" 37c8e983 -- '*.py' ':!tests'` returns nothing; no state-row repair or backup helper exists. The new public function `database.repair_close_snapshot_chain() -> str` sits beside the state accessors (`database.py:322-378`) and returns an outcome token (`not_needed`, `repaired`, `engine_busy`, `backup_failed`, `write_failed`, `not_byte_stable`). It never raises. Steps, in order:
1. Take the engine lock with the existing `database.acquire_lock()` (`database.py:292-306`), polling once a second for at most `_SNAPSHOT_REPAIR_LOCK_WAIT_S = 65` (the lock's own 60 s expiry at `database.py:299` plus margin). An engine run still holding the lock finishes and saves first, so the repair reads its write (S-1.8). Not acquired: log WARNING, return `engine_busy`, and the next start retries. The lock is released in a `finally` with `database.release_lock()` (`database.py:309-317`).
2. Open its own connection with `_SNAPSHOT_REPAIR_DB_TIMEOUT_S = 10.0` (the value of `database.py:95`, named so tests can lower it), `isolation_level=None`, then `BEGIN IMMEDIATE` and `SELECT data FROM bot_state WHERE id = 1` as the raw stored text.
3. Parse it. A pure helper `_flatten_close_snapshot(state) -> (state, removed_count)` keeps, in each list of `last_market_close_snapshot.accounts_map`, only the dicts that carry `name`, and drops an account key whose list became empty by that removal (S-1.2's "Unknown"); keys that were already empty stay. Nothing else is touched. `removed_count == 0`, or no snapshot, or a non-dict snapshot: `ROLLBACK`, return `not_needed` with no backup and no write (S-1.6, S-1.7). The repair is therefore idempotent by condition, and a failed start retries by the same condition, with no marker key added to the row.
4. Byte-stability guard: if `json.dumps(json.loads(raw)) != raw` the repair cannot promise byte-equal untouched values; it rolls back, logs WARNING, returns `not_byte_stable`. The production row passed this check on the audit's copy (2,039,413 B, read-only, 2026-10-22); C1's RED re-checks it on the PM's capture.
5. Backup before any write (R-92, G-85: a file beside the state DB, not an additive table, so no migration is needed and `tests/test_mdd_window_truth_zero_diff_guard.py:131-156`, which pins the highest migration at 038, stays green). Path: the directory of `os.path.abspath(database._db_file())` (`database.py:62-81`), name `<db basename>.pre-snapshot-repair-<UTC %Y%m%dT%H%M%SZ>.backup`, which `.gitignore:41` (`*.db.*backup`) already ignores, so the droplet checkout stays clean for `git pull`. Written with exclusive create (`open(path, "x", encoding="utf-8", newline="")`), the raw text verbatim, flushed and `os.fsync`ed, then read back and compared with the raw text. Any failure (an `OSError` from a full disk or a missing or read-only directory, a mismatch, an in-memory database with no directory): best-effort removal of the partial file, `ROLLBACK`, one ERROR line whose only variable part is `type(exc).__name__` (no path, value or traceback, no `exc_info`), return `backup_failed`; the next start retries (S-1.13).
6. `UPDATE bot_state SET data = ? WHERE id = 1` with `json.dumps(_sanitize_state_for_json(repaired))`, the exact encoding `save_state` uses (`database.py:369-377`), then `COMMIT`. Any `sqlite3` failure here or at `BEGIN IMMEDIATE`: `ROLLBACK`, one ERROR line with the exception type name only, return `write_failed`; the row is unchanged and the next start retries (S-1.10). Success logs one INFO line with the number of entries removed, the row size before and after, and the backup file's basename, so the operator can find it.
7. Restoring by hand (documented in the function's docstring and by the slice's doc-gen): stop the daemon, then `sqlite3 alphabot_state.db "UPDATE bot_state SET data = CAST(readfile('<backup>') AS TEXT) WHERE id = 1"`.

**Call site.** `app.py:9013` runs `ensure_bot_state_seeded()`; the repair call is added on the next line, before `threading.Thread(target=run_scheduler, ...)` at `app.py:9016` and before `app.run` (Q-3, G-83). Flask is not serving yet, so the flush-resync reset (`app.py:6374-6391`) cannot run during the repair, and the daemon singleton (`app.py:9006`) excludes a second daemon.

**Pass-through writers after the repair (S-1.14 to S-1.16).** None of them changes: the flush-resync reset rewrites only dicts carrying `name` and saves every other key as loaded (`app.py:6377-6391`); the startup seed returns without a save when a symphony entry exists and treats the snapshot key as reserved (`alpha_bot_execution.py:2128-2133`, save at `:2162`); the engine's closed-path and per-cycle saves (`alpha_bot_execution.py:676`, `:746`, `:1017`, `:2048`) write back the snapshot they loaded, and only the EOD builder replaces it. The three scenarios pin that the repaired snapshot stays flat and byte-equal through each of them.

**Hash pin.** `tests/test_mdd_window_truth_zero_diff_guard.py:66-69` pins the LF-normalized SHA-256 of `alpha_bot_execution.py`. It is re-taken at C1's GREEN with a comment naming this plan and the builder-only diff; the `math_engine.py` pin and the migration pin stay. A new scope test pins the file with the builder block removed (located by AST: the `If` node inside `main()` whose test reads `last_market_close_snapshot`), computed at 37c8e983, so "changes only inside the builder" stays checked after the full-file pin moves (S-1.9, DC-7).

### AC-2 and AC-7: one read, one computation, one completed cycle

**Today.** `get_state()` calls `get_api_state_dict()` (`app.py:2520`), which loads state (`app.py:2414`) and computes `_compute_portfolio_strip` (`app.py:2453-2465`); `meta` is built from that first strip (`app.py:3087-3091` frozen, `:3502-3507` live) while the rows read a second strip (`app.py:3350-3352` live, `:2966-2986` frozen recompute). State is loaded again at `app.py:2627`, and analytics loads it a third time for weights (`analytics.py:1604-1628`). `dashboard()` uses only the first, live strip (`app.py:1667-1674`), including after the close. Shadow rows of cycle M are committed in the data phase (`alpha_bot_execution.py:952-965`) and `bot_state` at the end (`:2048`), so a mid-cycle request pairs M's Bot leg with M-1's Held leg.

**Design.**
- `get_api_state_dict()` (`app.py:2407-2474`) keeps its name and its base fields (`bot_state`, `is_locked`, `port_state`, `exit_authority`, `daemon_started_at`) and stops computing `portfolio_strip` (delete `app.py:2449-2465`). `get_state()` reads only the additive fields from it, so the autouse stubs in `tests/app/conftest.py:66-70`, `tests/dashboard/conftest.py:27-31`, `tests/realtime_push/conftest.py:92-97` and `tests/ui/conftest.py:40-44` keep working.
- New `_committed_shadow_conn(state) -> sqlite3.Connection`. Searched: `git grep -n -i "temp view\|create temp\|et_cutoff" 37c8e983 -- '*.py' ':!tests'` finds only the per-query `et_cutoff` of `database.load_latest_shadow_row` (`database.py:3349-3381`, used by `reporting.py:74`). A per-query cutoff would need edits to roughly ten SQL statements in `analytics.py`; nearly every analytics reader on the request path already accepts a shared `conn` (F-1), so the cutoff is applied once, on that connection. It opens the shadow DB read-only (`analytics._get_shadow_db_file()`, `analytics.py:609-615`), creates `TEMP TABLE shadow_cutoff(ts_et TEXT)`, inserts the cutoff as a bound parameter, and creates `TEMP VIEW shadow_history AS SELECT * FROM main.shadow_history WHERE ts_et <= (SELECT ts_et FROM temp.shadow_cutoff)`. SQLite resolves an unqualified name to the temp schema first, so every query on this connection sees committed cycles only (verified on SQLite 3.49.1 with a read-only connection on 2026-10-22). The cutoff is the state's `last_successful_cycle_at` converted to ET and formatted `%Y-%m-%dT%H:%M:%S`, the form of `shadow_history.ts_et`; both come from the same `current_et` per cycle (`alpha_bot_execution.py:949`, `:1016`, `:2007`), so cycle M-1's rows are included and the in-flight cycle M's rows are not. An unparseable or absent stamp gives a plain read-only connection (S-4.4).
- A failure to open this connection, or to load state, raises to the route's existing handler, which answers 500 with its error body (`app.py:3511-3513`); the per-call fallback (`app.py:3313-3318` falls back to `None`, which would read uncommitted rows) is removed (S-7.5).
- `analytics.get_portfolio_daily_returns_from_shadow` (`analytics.py:1631`) gains keyword `conn=None` (used and never closed when given, as `analytics.py:1756-1771` already does), and it and `get_portfolio_bot_and_held_daily_returns` (`analytics.py:1709`) gain keyword `state=None`, passed to `_load_position_value_weights(state=None)` (`analytics.py:1604`), which uses it instead of `database.load_state()` when given. Defaults keep every other caller unchanged.
- New `_portfolio_view(state, market_state, conn) -> dict` in `app.py`, called once per request by both `get_state()` and `dashboard()`, holding `branch` (`live`, `frozen`, `waiting`), `served_state` (the live state, or the snapshot's symphony entries keyed by id as built at `app.py:2644-2648`), `snapshot` and `portfolio_strip`. Live: `_compute_portfolio_strip(state, trading_day, conn)` (`app.py:2046`), whose series calls at `app.py:2269` and `:2280` now receive `conn` and `state`. Frozen: new `_compute_frozen_portfolio_strip(snapshot, conn)`, extracted verbatim from `app.py:2826-3036` (symphony inputs via a new `_frozen_symphony_inputs(snapshot)` from `:2834-2861`, account-basis wrap, default-window guard alpha), plus the per-symphony `_tc`/`_cr`/`_mdd` loop of `app.py:2748-2791`, plus the hero series and vol from a new `_portfolio_hist_series(conn, state)` extracted from `app.py:2264-2290` and shared with the live strip.
- `meta` is built in every branch from the view's one strip and served state (`_build_meta(served_state, ..., portfolio_strip=view["portfolio_strip"])`); `_injected_portfolio_strip` is no longer a meta source. The D-03 fallback block (`app.py:2538-2625`) now fills hist arrays into the view's strip when it has none, so the waiting branch behaves as at the ref.
- `dashboard()` (`app.py:1665-1674`) takes `bot_state`, `portfolio_strip` and `meta` from the view, so after the close it renders from the snapshot exactly as the poll does (S-2.4). Its card enrichment (`app.py:1694-1756`) skips entries that already carry `_tc`/`_cr`/`_mdd`, which the frozen view computes on the snapshot's trading day.
- Values: `_compute_portfolio_strip` and the extracted frozen strip compute exactly what they compute at the ref (DEC-2, S-2.6). The one value change of AC-2 is that `meta` now repeats the rows' strip instead of a second computation (G-57's exception).

### AC-3 and AC-4: dated stamps from the data's moment

- C3 builds the suite's shared stamp helper, `_format_data_stamp(moment, *, seconds=False, previous_session=False) -> str`, exactly as `approach/00-suite.md` RC-5 specifies (this plan owns it; plan 04 C3 and C4 reuse it, with a parity test against `analytics.format_et_stamp` on plan 04's base). It sits in `app.py` immediately before `_compute_portfolio_strip` (`app.py:2046`) and replaces the inlined minute stamps at `app.py:2310`, `:2312`, `:2314`, `:2403`, `:2877`, `:3374`, `:3376`, `:3378` (searched: `git grep -n "%Y-%m-%d %H:%M ET" 37c8e983 -- '*.py' ':!tests'` finds no dated helper). Its contract, in short: an aware `datetime` gives `YYYY-MM-DD HH:MM ET` (seconds kept when `seconds=True`, truncated, never rounded), `previous_session=True` appends ` · previous session`, a `datetime.time` gives `HH:MM ET (date not recorded)`, and `None`, a naive `datetime` or anything else gives `--`; it never reads the clock and never raises. Every stamp in this plan goes through it.
- New `_newest_observation_moment(conn) -> datetime | None`: `SELECT ts_utc FROM shadow_history ORDER BY ts_utc DESC LIMIT 1` on the committed connection (index `idx_shadow_history_ts_utc`, `migrations/008_shadow_history.sql`), the UTC write time of the newest row of a completed cycle, returned timezone-aware so `_format_data_stamp` accepts it (TA-18, DEC-1). It replaces the `last_successful_cycle_at` reads at `app.py:2295-2314` and `:3366-3378`; the five request-clock fallbacks (`app.py:2312`, `:2314`, `:2403`, `:3376`, `:3378`) become `"--"`. `last_successful_cycle_at` itself is still served (`app.py:3498`) for the header's liveness check. The Tier-1 `account_basis_as_of` fallbacks to the request clock (`app.py:2391-2393`, `:2902-2908`), unreachable in production because both globals are set together (`app.py:876-878`), become `"--"` under SD-2; the normal value keeps its form (S-4.8).
- `_build_meta` (`app.py:1925-1958`) passes absent strip figures through as `None` instead of `0.0` for `tc`, `tc_if_held`, `cr`, `cr_if_held`, `mdd`, `mdd_if_held`, so `meta.portfolio` and `portfolio_strip` carry the same nulls (S-2.5). The server render already coerces `None` to the dash (`templates/index.html:881-884`, `:934`).
- Frozen branch: the response gains `trading_day` (the snapshot's, or `null`) and `snapshot_is_previous_session`. The capture moment is the ET-aware `datetime` built from `trading_day` and `captured_at_et` (`app.py:2868-2878` today). `frozen_at` is `_format_data_stamp(capture, seconds=True)` (`D 16:00:42 ET`, SD-1); `data_as_of`, `meta.portfolio.data_as_of` and `portfolio_strip.data_as_of` are all `_format_data_stamp(capture, previous_session=marker)`. Legacy snapshot without `trading_day`: `trading_day` null, the marker false, `data_as_of` `_format_data_stamp(<captured time>)`, which reads `HH:MM ET (date not recorded)` (S-3.5), and `frozen_at` the stored `captured_at_et` unchanged.
- The marker is `trading_day < market_calendar.most_recent_completed_session(now_et)`, false when the helper returns `None`. C3 builds that helper exactly as `approach/00-suite.md` RC-6 specifies (this plan owns it; C4's latch also uses it): `most_recent_completed_session(now_et) -> date | None`, the newest trading day whose `session_close` (`market_calendar.py:81-104`, half days included) is at or before `now_et`, walking back at most `_COMPLETED_SESSION_LOOKBACK_DAYS = 10`, placed at the end of `market_calendar.py` after `get_market_state` (`market_calendar.py:107-136`) so it never collides with plan 02's insertion after `session_close`. Searched: `git grep -n -i "def .*\(prev\|previous\|last\|recent\)_\(trading\|session\|completed\)" 37c8e983 -- '*.py' ':!tests'` finds no existing helper.

### AC-5: frozen figures fixed on the snapshot, window routes on the snapshot

- New module state beside `_account_totals_last_good` (`app.py:586-589`): `_frozen_account_totals: dict | None` holding `{"key": (trading_day, captured_at_et), "totals": dict, "as_of": str}`. It is set in `_refresh_account_totals` (`app.py:813`) after a 200 response (`app.py:856-878`) by a new `_latch_frozen_account_totals(read_started_et)`, under `_account_totals_cache_lock` (`app.py:579`), because the function has three concurrent callers: the scheduler tick (`app.py:1429`), the cycle-refresh thread (`app.py:763`) and the flush-resync thread (`app.py:6403`). The latch reads the stored snapshot's key with `database.load_state()` only while the market is not open and only until the latch holds the snapshot of `most_recent_completed_session(now)`, so it reads state a few times per evening, in the background. It latches the totals of the first successful read whose request started at or after the snapshot's capture moment (Q-1). A legacy snapshot with no `trading_day` latches the first successful read the process sees. A restart empties the latch, and the first successful read after it latches again (Q-1).
- `_compute_frozen_portfolio_strip` (from C2) reads account totals only from: the latch when its key equals the snapshot's key (not stale); otherwise `_account_totals_last_good` with `account_basis_stale` true and `account_basis_as_of` `_account_totals_last_success_at` (S-5.5, S-5.6); otherwise the Tier-2 value-weighted floor with `basis` "value_weighted" (S-2.8). Today and Cumulative, `account_value` and `account_all_time_cr` all come from that one choice; the frozen branch no longer reads `_account_totals_cache` (`app.py:2892`, `:2898`, `:2904`, `:3001`; DC-4). The live branch keeps reading the cache per request (S-5.7).
- `/api/strip/<window>` (`app.py:3639-3761`) and `/api/hero-chart/<window>` (`app.py:3565-3636`) take the market state; in `closed_frozen` or `pre_market` with a snapshot they build inputs with `_frozen_symphony_inputs(snapshot)` and use the snapshot's `trading_day` for the strip's day-1 fallback query (`app.py:3657`, `:3705-3711`); the hero series is cut at dates on or before the snapshot's `trading_day` before window slicing (`_slice_series_by_window_cutoff`, `app.py:3534-3562`). `_portfolio_hist_series` applies the same cut in the frozen view, so the poll's hero and the window route end on the same day. Unknown tokens keep the 404 (`app.py:3654-3655`).

### AC-6: the frozen branch carries cards and verdicts

- New `_cards_from_entries(entries, keys) -> list[dict]` extracted from `app.py:3426-3474` (`_sym_status`, `_tc_cr_mdd_floats`, the card dict) with an added `account` field, used by both branches; the frozen response gains `symphonies` (S-6.1, S-6.7).
- Guard alpha in both branches goes through ux-overhaul 05's shared `_inject_guard_alpha(entries)` (`origin/ux-overhaul/05-c2:app.py:1666-1682`, which replaces `app.py:3407-3421`), changed to skip an entry whose `current_return` is `None` instead of computing against `0.0` (S-6.4, SD-12). In the frozen branch it runs on the snapshot entries before the `_FROZEN_SYM_DEFAULTS` normalization (`app.py:2810-2823`), which would otherwise turn a missing `current_return` into `0.0`.
- `dashboard()` after the close renders cards from the view's frozen entries (already carrying `_tc`/`_cr`/`_mdd` on the snapshot's trading day, from C2) and its verdicts through `_first_paint_verdict` (`origin/ux-overhaul/05-c2:app.py:1704-1712`), so the server render and the poll carry one number (S-6.2, S-6.5).

### AC-8: dead writers and the unread field

- Removed from `static/index.js`: the `updateStatusStrip(meta)` and `updateMarketDot(data)` renderer entries and the `#hero-ticker` writer (`static/index.js:1394-1400`), and both functions (`static/index.js:1424-1446`; `origin/ux-overhaul/05-c2:static/index.js:1434`, `:1461`, `:1472`); the comment at `static/index.js:1591` states 30 s, the value at `static/index.js:11`.
- Removed from `_build_meta`: the `triggers_today` query and key (`app.py:1885-1898`, `:1972`). The Q-4 fields stay.

### AC-9: truthful verdict label

- The label span (`origin/ux-overhaul/05-c2:templates/index.html:1249`) becomes `data-testid="guard-alpha-basis"` with text from a new render variable `verdict_basis_label` that `dashboard()` sets from its market state: "vs held now" when `open`, "at close" otherwise; the comment at `templates/index.html:1240-1242` is corrected. Same style token (Design-System Mapping).
- `updateVerdicts` (`origin/ux-overhaul/05-c2:static/index.js:385-389`) takes the market state from the poll (`data.market_state`), finds the renamed span and writes the same two labels; the figure's formula is untouched (S-9.5).

No new route, no new dependency, no migration, no new table. No order, liquidation or `LIVE_EXECUTION` path is touched (NB-7).

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-1, R-7, R-31, R-34, R-89, R-92 | `alpha_bot_execution.py:1121-1122` (builder shape test); new `database.repair_close_snapshot_chain` beside `database.py:322-378` (no repair or backup helper: `git grep -n -i "def .*repair\|def .*backup" 37c8e983`); call after `app.py:9013`; re-pin `tests/test_mdd_window_truth_zero_diff_guard.py:69` | new tests/database/test_close_snapshot_repair.py (isolation `tests/conftest.py:427`, lock pattern `tests/database/test_lock_lifecycle.py:40`); new tests/engine/test_close_snapshot_builder.py (engine `main()` harness `tests/dashboard/test_market_mode.py:267`); fixture R1, the production `bot_state` row captured read-only from the audit's droplet copy | C1 |
| AC-2 | R-1, R-2, R-7, R-26, R-43 | `app.py:2407-2474` (no strip in `get_api_state_dict`); `app.py:3087-3091` and `:3502-3507` (meta from the one strip); `app.py:1665-1674` (dashboard on the view); new `_portfolio_view` and `_compute_frozen_portfolio_strip` extracted from `app.py:2826-3036` | new tests/app/test_api_state_single_computation.py (real-DB client `tests/app/test_api_state_connection_count.py:137`, frozen clock `tests/app/test_held_basis_route_convergence.py:139`); `tests/dashboard/test_dashboard_render_consistency.py:141`; golden strips captured from the 37c8e983 producer | C2 |
| AC-2 | R-1, R-2, R-7, R-26, R-43 | `app.py:1925-1958` (`_build_meta` passes absent figures as `None`); `app.py:2395-2404` (exception strip) | tests/app/test_api_state_single_computation.py (from C2), computation forced to raise by a patched analytics call | C3 |
| AC-3 | R-1, R-4, R-5, R-9, R-13, R-25, R-48 | `app.py:2868-2878` and `:3071-3077` (dated `frozen_at`, `data_as_of`, `trading_day`, marker); the marker uses `market_calendar.most_recent_completed_session`, which C3 builds at the end of `market_calendar.py` after `:107-136` as `approach/00-suite.md` RC-6 specifies (no existing helper: `git grep -n -i "def .*\(prev\|previous\|last\|recent\)_\(trading\|session\|completed\)" 37c8e983`) | `tests/realtime_push/test_data_freshness_visibility.py:597`; frozen clock `tests/app/test_held_basis_route_convergence.py:139` with the real XNYS calendar; fixture R8 and its derived records | C3 |
| AC-4 | R-1, R-4, R-5, R-9, R-13, R-25, R-52 | `app.py:2295-2314`, `:2403`, `:3366-3378` (stamp from the newest committed observation, written through `_format_data_stamp`, which C3 builds before `app.py:2046` as `approach/00-suite.md` RC-5 specifies); new `_newest_observation_moment` (`git grep -n "ORDER BY ts_utc DESC LIMIT 1" 37c8e983 -- app.py database.py` finds only per-symphony reads, `app.py:3717`, `database.py:3372`, none portfolio-wide on a passed connection) | `tests/realtime_push/test_data_freshness_visibility.py:111` and `:729`; fixture R6 shadow rows seeded into the temp DB through `database.record_shadow_observation` | C3 |
| AC-5 | R-1, R-2, R-7, R-9, R-49, R-89 | `app.py:813-878` (`_refresh_account_totals` latch); `app.py:2880-3002` (frozen totals source); `app.py:3565-3761` (window routes on the snapshot); new `_frozen_account_totals` beside `app.py:586-589` (no post-close totals record: `git grep -n -i "frozen_account\|_frozen_totals\|def .*latch" 37c8e983`) | `tests/dashboard/test_eod_account_basis.py:703`; `tests/app/test_eod_account_basis_refresh.py:92`; `tests/app/test_windowed_strip_route_live.py:107` and `:165`; fixture `tests/fixtures/composer/v3-audit/total_stats.json` (captured) and one derived variant | C4 |
| AC-6 | R-1, R-4, R-9, R-47, R-50 | `app.py:3426-3474` (cards helper with `account`); `origin/ux-overhaul/05-c2:app.py:1666-1682` (`_inject_guard_alpha` skips absent `current_return`, frozen call before `app.py:2810-2823`); `app.py:1686-1756` (dashboard frozen cards) | `tests/app/test_closed_market_cumulative_bounce.py:199` (frozen client) and `:208`; `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:331` (render then poll world); fixture records of `origin/ux-overhaul/05-c2:tests/fixtures/dashboard/card_truth/PROVENANCE.md` plus R7 | C5 |
| AC-7 | R-1, R-2, R-9, R-51 | new `_committed_shadow_conn` in `app.py` replacing `app.py:3313-3318` (only per-query cutoff exists, `database.py:3349-3381`); `analytics.py:1604`, `:1631`, `:1709` (`conn` and `state` keywords); `app.py:2269`, `:2280` | new tests/app/test_api_state_one_cycle.py (real-DB client `tests/app/test_api_state_connection_count.py:137`); fixture R6 (two consecutive captured cycles); differential harness: route with cycle M rows present against the route with them absent | C2 |
| AC-8 | R-1, R-54, R-89 | `static/index.js:1394-1400`, `:1424-1446`, `:1591`; `app.py:1885-1898`, `:1972` | new tests/dashboard/test_index_js_writes_existing_elements.py (source reading as `tests/dashboard/test_display_truth_cluster_js.py:32`); `tests/app/test_f011_state_field_availability.py:40` and `:90` | C6 |
| AC-8 | R-1, R-54, R-89 | `app.py:2748-2791` (frozen per-symphony values feed `symphonies`) | `tests/app/test_f011_state_field_availability.py:90`; fixture R8 | C5 |
| AC-9 | R-1, R-2, R-53, R-89 | `origin/ux-overhaul/05-c2:templates/index.html:1249`; `origin/ux-overhaul/05-c2:static/index.js:385-389`; `templates/index.html:1240-1242`; `app.py:1665` (`verdict_basis_label`) | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:290`, `:323`, `:331`, `:376`; `tests/dashboard/test_card_guard_alpha_basis.py:158`; ux-overhaul 05 node harness `origin/ux-overhaul/05-c1:tests/dashboard/_index_js_harness.py` | C7 |

## Execution Model

Every cycle is one Toxic Pair (`test-writer` writes RED, `implementer` writes GREEN) in its own worktree under `.claude/worktrees/`, never the primary checkout, with `.env` copied in, named for its branch, and `git rev-parse --show-toplevel` and `git rev-parse --abbrev-ref HEAD` checked before the first edit, as the project's environment rules and `approach/00-suite.md` RC-8 require. I-2's pair is counted in `approach/00-suite.md`, not in the size below. A round's pair is dispatched fresh and never resumed across rounds.

Reviewers per head, dispatched in parallel by the PM at cycle-complete against one HEAD:
- C1: `qa`, `code-reviewer`, `risk-engine-specialist` (the engine file and money, Sensitive) and `sqlite-specialist` (the repair's lock, transaction and backup). No UI renders. The hidden-mode `qa` for this Sensitive plan runs on C1's head.
- C2: `qa`, `code-reviewer`, `api-expert` (`/api/state` contract), `integration-auditor` (route and server render of `/` together), and `ux-expert` with `behavioral-evidence-capturer` (the rows and counts on `/` now come from the view; render against first poll in all three states).
- C3: `qa`, `code-reviewer`, `api-expert`, `integration-auditor`, `ux-expert` with the capturer (the dated as-of and the previous-session cue).
- C4: `qa`, `code-reviewer`, `api-expert` (three routes), `pipelines-expert` (the latch runs inside the scheduled `_refresh_account_totals` job and its two thread callers).
- C5: `qa`, `code-reviewer`, `api-expert`, `integration-auditor`, `ux-expert` with the capturer (cards and verdicts after the close).
- C6: `qa`, `code-reviewer`, `api-expert` (`meta` loses a field), `integration-auditor` (script and route together), `ux-expert` with the capturer (no renderer error after removal).
- C7: `qa`, `code-reviewer`, `ux-expert` with the capturer (the label on render and poll, open and after close).

The capturer works against the real-data launcher (a copy of production data, no scheduler, outbound network refused). It cannot show a live market-hours cycle, so S-9.1 and S-9.3 evidence comes from the node harness and the PM's live check, and the capturer covers the after-close and pre-market states with the launcher's clock.

Merges follow `approach/00-suite.md` RC-9: the PM creates `refresh-timing/01` at 37c8e983 and each cycle branch at its first input's recorded SHA, records every SHA and dispatches the pairs; the PM never runs `git merge`. Who runs each of this plan's merges:
- FX-01, a fresh `test-writer` capture batch on `refresh-timing/fixtures` (RC-7): writes `tests/fixtures/refresh_timing/_cases_01.py` (subcommands `01-c1-bot-state-row`, `01-c2-shadow-rows`, `01-c2-exit-trigger`), hands it to the PM for the read-only droplet run, and commits the PM's output into `01-c1/` and `01-c2/` with their `PROVENANCE.md` rows.
- C1 and C2 base merges: each cycle's own pair merges `refresh-timing/fixtures` into its branch as its first commits, before RED (the `implementer` commits the merge, the `test-writer` resolves any test file).
- C3, C4, C6 and C7 have a single input and no merge.
- I-1, a fresh `test-writer` and `implementer` pair: the integration task that enters the passed heads of C1, C2, C3 and C4 into `refresh-timing/01`, so C5 has its first input.
- C5 base merge: C5's own pair merges `main`, holding ux-overhaul 05 and model-unpin, into its branch (created at `refresh-timing/01` after I-1) as its first commits, before RED.
- I-2, the fresh pair `approach/00-suite.md` counts for this plan's pre-PR integration: enters the passed heads of C5, C6 and C7 and the latest `main` into `refresh-timing/01` before the PR.
- An integration head that resolved a conflict in production code gets one `qa` and one `code-reviewer` before the next cycle forks from it (RC-9), counted when it happens.

C5's base merge and I-2 carry this plan's conflicts: `dashboard()` (ux-overhaul 05 C1 `_dashboard_initial_data`, C2 `_inject_guard_alpha`), `get_state()` (05 C2 `_active_fleet_alert`, 05 C3 account filter and `_narrowed_state`), `get_hero_chart` (05 C1 `_hero_chart_body`, which must carry C4's frozen cut) and the startup block (model-unpin C9, beside C1's repair call); after it, C2 to C4's named test sets and ux-overhaul 05's dashboard tests are rerun.

The slice's single doc-gen runs after every reviewer passes every head.

Team size: 39 = 9 test-writer (7 cycles, FX-01, I-1) + 8 implementer (7 cycles, I-1) + 2 qa + 1 qa (hidden mode) + 2 code-reviewer + 2 ux-expert + 2 behavioral-evidence-capturer + 5 api-expert + 4 integration-auditor + 1 pipelines-expert + 1 risk-engine-specialist + 1 sqlite-specialist + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 | FX-01: `tests/fixtures/refresh_timing/01-c1/` (R1) and `_cases_01.py` on `refresh-timing/fixtures` | `origin/main` at 37c8e983, branch `refresh-timing/01-c1`, with `refresh-timing/fixtures` merged in by C1's pair before RED |
| C2 | FX-01: `tests/fixtures/refresh_timing/01-c2/` (R6, R7) on `refresh-timing/fixtures` | `origin/main` at 37c8e983, branch `refresh-timing/01-c2`, with `refresh-timing/fixtures` merged in by C2's pair before RED |
| C3 | C2: `app.py` `_portfolio_view`, `_committed_shadow_conn` and `_compute_frozen_portfolio_strip` | `refresh-timing/01-c2` at its cycle-complete GREEN SHA, branch `refresh-timing/01-c3` |
| C4 | C2: `app.py` `_compute_frozen_portfolio_strip`, `_frozen_symphony_inputs` and `_portfolio_hist_series` | `refresh-timing/01-c2` at its cycle-complete GREEN SHA, branch `refresh-timing/01-c4` |
| C5 | C2: `app.py` `_portfolio_view` frozen entries with `_tc`/`_cr`/`_mdd` (on `refresh-timing/01` through I-1) | `refresh-timing/01` at I-1's merge commit, branch `refresh-timing/01-c5`, with `main` holding ux-overhaul 05 and model-unpin merged in by C5's pair before RED (RC-9 base merge; the plan's Depends on for the AC-6 cycle; it supplies `_inject_guard_alpha`, `_first_paint_verdict` and the card harness) |
| C6 |  | `main` at the ux-overhaul 05 merge commit, branch `refresh-timing/01-c6`; it starts when that merge exists (plan Depends on) |
| C7 | C5: `app.py` frozen `guard_alpha` and `symphonies`, and the absent-`current_return` rule in `_inject_guard_alpha` | `refresh-timing/01-c5` at its cycle-complete GREEN SHA, branch `refresh-timing/01-c7` |

C1 and C2 start as soon as FX-01's captures are on `refresh-timing/fixtures`, and stand alone; C1 touches only the builder, `database.py`, one line of the startup block and the hash pin. C3 and C4 run in parallel once C2's GREEN is committed. Cycle heads enter `refresh-timing/01` only through the integration tasks I-1 and I-2, by merge commits, never a rebase; the tip opens one PR to `main` after I-2.

## Per-Cycle Detail

### C1: flat close snapshot and the one-time repair (AC-1)
- Modified: `alpha_bot_execution.py:1121-1122` (one condition and a comment, inside `:1115-1163`); `app.py` after `:9013` (one call); `tests/test_mdd_window_truth_zero_diff_guard.py:66-69` (re-pin with reason).
- Added to `database.py` beside `:322-378`: `repair_close_snapshot_chain`, `_flatten_close_snapshot`, `_snapshot_repair_backup_path`, `_SNAPSHOT_REPAIR_LOCK_WAIT_S`, `_SNAPSHOT_REPAIR_DB_TIMEOUT_S`.
- Added tests: `tests/database/test_close_snapshot_repair.py`, `tests/engine/test_close_snapshot_builder.py`, `tests/engine/test_close_snapshot_builder_scope.py`; fixture records in `tests/fixtures/refresh_timing/01-c1/` with its `PROVENANCE.md` (R1 from FX-01; R2 to R5 and the EOD golden written in C1's RED), per `approach/00-suite.md` RC-7.
- Primitives used: `database.acquire_lock`/`release_lock` (`database.py:292-317`), `database._sanitize_state_for_json` (`database.py:331-366`), `database._db_file` (`database.py:62-81`).
- First production use: the next daemon start after deploy; the builder's first effect is the first close after deploy.

### C2: one read, one computation, one completed cycle (AC-2, AC-7)
- Modified: `app.py:2407-2474`, `:2513-2627`, `:2630-3093` (frozen block now a call into the view), `:3146-3352` (live loop on the committed connection), `:3502-3507`, `:1665-1674`; `analytics.py:1604-1628`, `:1631-1706`, `:1709-1771` (keywords only).
- Added to `app.py`: `_committed_shadow_conn`, `_portfolio_view`, `_compute_frozen_portfolio_strip`, `_frozen_symphony_inputs`, `_portfolio_hist_series`.
- Added tests: `tests/app/test_api_state_single_computation.py`, `tests/app/test_api_state_one_cycle.py`; fixture records in `tests/fixtures/refresh_timing/01-c2/` (R6 and R7 from FX-01; R8 and the 37c8e983 `/api/state` goldens written in C2's RED).
- Consumers rewritten with the reason (DC-3, DC-5): every test that injects `portfolio_strip` through a `get_api_state_dict` stub; the sweep command is `git grep -ln "get_api_state_dict" -- tests` intersected with `git grep -ln "portfolio_strip" -- tests` (29 files mention `"portfolio_strip":` at the ref; `tests/app/test_mdd_window_truth_routes.py` and `tests/ui/test_cycle_2_fix_live_data.py` hold most stub references).
- First production use: every `/api/state` poll and every `GET /`.

### C3: dated stamps and the frozen trading day (AC-3, AC-4, S-2.5)
- Modified: `app.py:1925-1958`, `:2295-2314`, `:2389-2404`, the extracted frozen stamp (from `:2868-2878`), the frozen response (from `:3071-3093`), `:2902-2908`, `:3366-3378`.
- Added: `_newest_observation_moment` in `app.py`; and, as owner of `approach/00-suite.md` RC-5 and RC-6, `_format_data_stamp` in `app.py` immediately before `_compute_portfolio_strip` (`app.py:2046`) and `market_calendar.most_recent_completed_session` with `_COMPLETED_SESSION_LOOKBACK_DAYS` at the end of `market_calendar.py` (after `:107-136`). Plan 04 C3 and C4 reuse `_format_data_stamp` and pin its parity with `analytics.format_et_stamp`; no second implementation is written.
- Derived records for C3's scenarios in `tests/fixtures/refresh_timing/01-c3/`.
- Consumers swept (DC-2, DC-5): `git grep -n -E "data_as_of|frozen_at|%H:%M ET" -- tests static templates` (58 test files at the ref); `static/index.js:1326-1330` writes the string verbatim and needs no change; tests pinning `meta.portfolio.tc == 0.0` for a null strip (`git grep -n -E "\[\"(tc|cr|mdd)(_if_held)?\"\] == 0" -- tests`).
- First production use: the next poll after deploy; the marker's first live effect is any poll between 16:00 and the EOD commit.

### C4: frozen figures fixed, window routes on the snapshot (AC-5)
- Modified: `app.py:586-603` (latch state), `:813-878` (latch call), the extracted frozen strip's totals source (from `:2880-3002`), `:3565-3636`, `:3639-3761`, `_portfolio_hist_series` (the cut).
- Added: `_frozen_account_totals`, `_latch_frozen_account_totals`; derived records for C4's scenarios in `tests/fixtures/refresh_timing/01-c4/`.
- First production use: the first successful account read after the first close following deploy.

### C5: frozen cards and verdicts (AC-6, S-8.3)
- Modified: the frozen branch of the view (symphonies, guard alpha before defaults), `_inject_guard_alpha` (`origin/ux-overhaul/05-c2:app.py:1666-1682`), the live card build (from `app.py:3426-3474`), `dashboard()` frozen cards.
- Added: `_cards_from_entries`; the S-6.4 derived record in `tests/fixtures/refresh_timing/01-c5/`.
- First production use: the first poll after the first close following deploy.

### C6: dead writers and the unread field (AC-8)
- Modified: `static/index.js` (renderer list and two functions, the comment), `app.py:1885-1898` and `:1972`.
- Consumers rewritten with the reason: `tests/app/test_phase2b_portfolio_display.py`, `tests/analytics/test_portfolio_vol_computation.py`, `tests/telemetry/test_exit_triggers_schema.py`, `tests/fixtures/composer/v3-audit/dashboard_api_state.json` (`git grep -l triggers_today -- tests` at the ref).
- Added test: `tests/dashboard/test_index_js_writes_existing_elements.py`.

### C7: truthful verdict label (AC-9)
- Modified: `templates/index.html` verdict block (ux-overhaul 05 form), `static/index.js` `updateVerdicts` and its caller in the renderer list, `app.py` `dashboard()` render context.
- Consumers renamed: every reader of `guard-alpha-at-exit` and the "at exit" label (`git grep -n -E "guard-alpha-at-exit|>at exit<|'at exit'|\"at exit\"" -- static templates tests app.py`, which includes `tests/dashboard/test_card_guard_alpha_basis.py:158`).

## Per-Cycle Test Plan

Write paths and entry points that reach this plan's rules: every column of the plan's two matrix tables, each a round-1 RED row of the cycle that owns its scenarios. The last six write the persisted `bot_state.last_market_close_snapshot` that C1 changes, or the new in-memory frozen totals of C4.

| Entry point | Rules |
|---|---|
| `GET /api/state` open | AC-2, AC-4, AC-5, AC-6, AC-7, AC-8 (`app.py:2513`, live branch `:3146-3512`) |
| `GET /api/state` closed_frozen | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8 (`app.py:2630-3093`) |
| `GET /api/state` pre_market | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6 (`app.py:2630-3093`) |
| `GET /` server render | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-9 (`app.py:1665-1828`) |
| `GET /api/strip/{window}` | AC-5 (`app.py:3639-3761`) |
| `GET /api/hero-chart/{window}` | AC-5 (`app.py:3565-3636`) |
| Engine EOD close-snapshot builder | AC-1 (`alpha_bot_execution.py:1115-1163`, save at `:1168`) |
| Daemon-start snapshot repair | AC-1 (new `database.repair_close_snapshot_chain`, called after `app.py:9013`) |
| Engine per-cycle `bot_state` save | AC-1 (pass-through writers `alpha_bot_execution.py:530`, `:676`, `:746`, `:1017`, `:2048`) |
| Flush-resync background reset | AC-1 (pass-through writer `app.py:6374-6391`) |
| Startup seed | AC-1 (pass-through writer `alpha_bot_execution.py:2162`) |
| Account-totals refresh job | AC-5 (`app.py:813-878`; callers `app.py:1429`, `:763`, `:6403`) |

Fixture records (SD-7; derived records signed off by the PM) follow `approach/00-suite.md` RC-7: one subfolder per cycle under `tests/fixtures/refresh_timing/`, each with its own `PROVENANCE.md`. Captured records (R1, R6, R7) come from the PM's read-only droplet run of `capture.py` with this plan's `_cases_01.py` (RC-7 source 1, run as the service user against `/opt/planetstopper/alphabot_state.db`), never the temporary launcher database the audit used, and FX-01 commits them on `refresh-timing/fixtures` into `01-c1/` and `01-c2/`. Derived records and goldens are written by the RED of the cycle whose scenarios use them, into that cycle's folder. The figures below were measured on the audit's copy of 2026-10-22 and describe the record's expected shape, not values any test asserts. One record per case:
- R1 `01-c1/nested_bot_state_row.json.gz` (case `01-c1-bot-state-row`): `SELECT data FROM bot_state WHERE id = 1`, verbatim text, gzipped (about 2 MB raw and 274 KB gzipped on the audit's copy, a chain of about 86 snapshots, the phantom as the only "Unknown" entry, 12 symphonies). C1's RED asserts the captured record still nests at least one earlier snapshot and round-trips byte-for-byte before using it.
- In `01-c1/`, written by C1's RED: R2 derived, R1 with the phantom "Unknown" entry removed (case: a flat stored snapshot). R3 derived: R1 without `last_market_close_snapshot` (case: no snapshot). R4 derived: one R1 symphony entry without `account` (case: Unknown kept). R5 derived: R1 plus a `fleet_correlation_alert` dict taken from the `example` of `tests/fixtures/engine/fleet_correlation/alert_state_shape.json` (case: a dict with no `name`).
- R6 `01-c2/shadow_rows_two_cycles.json` (case `01-c2-shadow-rows`): from the same capture, for one triggered and one untriggered symphony, the last row of the previous trading day and every row of the two newest cycles of the capture day (cases: a completed and an in-flight cycle, a cycle that wrote nothing when rows are omitted).
- R7 `01-c2/exit_trigger_row.json` (case `01-c2-exit-trigger`): the newest `exit_triggers` row of R6's triggered symphony (case: a recorded exit).
- R8 `01-c2/snapshot_two_symphonies.json` derived (C2's RED): R2's snapshot narrowed to R6's two symphonies. Its named variants live with the cycle that uses them: `captured_at_et` 16:00:42, `trading_day` one session earlier, the Friday, holiday and half-day dates, `trading_day` removed, and the R6 omissions and moved timestamp in `01-c3/`; the next-session snapshot and the total-stats variant in `01-c4/`; the triggered entry's `current_return` removed in `01-c5/`.
- Goldens: `01-c1/golden_ref_eod_snapshot.json` (the 37c8e983 engine's EOD output on R1 inputs) and `01-c2/golden_ref_api_state_<branch>.json` (37c8e983 `/api/state` for R6 and R8 seeded state in each branch), each produced by running the ref in a throwaway `git worktree add <tmp> 37c8e983`, with the command recorded in that folder's `PROVENANCE.md`.
- Captured Composer bodies already in the tree: `tests/fixtures/composer/v3-audit/symphony_stats_meta.json` and `total_stats.json`.

Harness rules for every cycle: real temp SQLite through `DB_PATH` (`tests/conftest.py:86`, `:427`) with `analytics.DB_FILE` set to the same path (`tests/app/test_api_state_connection_count.py:137-153`); the clock frozen by `monkeypatch.setattr(app_module, "datetime", ...)` (`tests/app/test_held_basis_route_convergence.py:139`) with the real XNYS calendar; network patched; no assertion copies a producer value, every expected figure comes from the golden or from a recompute on the seeded fixture. Test runs are background tasks with output to a file, at most `-n 4` under the auto-installed memory cap, and each named set runs in slices that stay under 240 s; a slice that crosses 240 s is split before it is run again, because two runs of 300 s or more in an hour block the worktree.

### C1
Slices: (a) the three new files plus `tests/test_mdd_window_truth_zero_diff_guard.py`, `tests/engine/test_startup_seed_symphonies.py`, `tests/dashboard/test_market_mode.py`, `tests/app/test_flush_resync_race.py`; (b) `tests/database`; (c) `tests/execution/test_[a-m]*.py`; (d) `tests/execution/test_[n-z]*.py` and `tests/engine`; (e) `tests/dashboard/test_r2_frozen_state.py`, `tests/app/test_api_state_connection_count.py`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | integration: `alpha_bot_execution.main()` at 16:01 ET with the real `database` on a temp DB, Composer and Alpaca fetches patched | Engine EOD close-snapshot builder | new: tests/engine/test_close_snapshot_builder.py, harness of `tests/dashboard/test_market_mode.py:267` with the real database module | R1 as the stored D-1 state; Composer stats from `tests/fixtures/composer/v3-audit/symphony_stats_meta.json` (captured) |
| S-1.2 | AC-1 | integration: repair on a temp DB, every untouched field compared by its serialized text | Daemon-start snapshot repair | new: tests/database/test_close_snapshot_repair.py, isolation `tests/conftest.py:427` | R1 (captured) |
| S-1.3 | AC-1 | route plus node: frozen `/api/state` after the repair, then `loadCharts` under node recording fetch URLs | `GET /api/state` closed_frozen | `tests/dashboard/test_r2_frozen_state.py:219`; node pattern `tests/dashboard/test_account_basis_honesty_render.py:418` | R1 written through the repair under test |
| S-1.4 | AC-1 | integration, builder harness | Engine EOD close-snapshot builder | new: tests/engine/test_close_snapshot_builder.py (as S-1.1) | R4 (derived from R1, signed off by the PM) |
| S-1.5 | AC-1 | integration, builder harness | Engine EOD close-snapshot builder | new: tests/engine/test_close_snapshot_builder.py (as S-1.1) | R5 (derived from R1 and the schema-derived alert example, signed off by the PM) |
| S-1.6 | AC-1 | integration, repair | Daemon-start snapshot repair | new: tests/database/test_close_snapshot_repair.py (as S-1.2) | R3 (derived, signed off by the PM) |
| S-1.7 | AC-1 | integration, repair run twice, no new backup file on the second run | Daemon-start snapshot repair | new: tests/database/test_close_snapshot_repair.py (as S-1.2) | R1 (captured) |
| S-1.8 | AC-1 | integration: a writer thread holds `database.acquire_lock()`, saves an engine state carrying a new field and releases while the repair waits; AST check that the repair call precedes the scheduler thread in `app.py`'s `__main__` block | Engine per-cycle `bot_state` save | new: tests/database/test_close_snapshot_repair.py; lock pattern `tests/database/test_lock_lifecycle.py:40`; AST pattern `tests/engine/test_startup_seed_symphonies.py:657` | R1 (captured) |
| S-1.9 | AC-1 | integration golden plus source scans | Engine EOD close-snapshot builder | `tests/test_mdd_window_truth_zero_diff_guard.py:69` (re-pin); new: tests/engine/test_close_snapshot_builder_scope.py (builder-excised hash pinned at 37c8e983) | `golden_ref_eod_snapshot.json` (captured from the 37c8e983 engine on R1 inputs) |
| S-1.10 | AC-1 | integration: a second connection holds `BEGIN IMMEDIATE`, `_SNAPSHOT_REPAIR_DB_TIMEOUT_S` lowered; caplog asserts one ERROR record carrying only the type name; a second call after release repairs | Daemon-start snapshot repair | new: tests/database/test_close_snapshot_repair.py (as S-1.2) | R1 (captured) |
| S-1.11 | AC-1 | integration, builder harness | Engine EOD close-snapshot builder | new: tests/engine/test_close_snapshot_builder.py (as S-1.1) | R2 (derived, signed off by the PM) |
| S-1.12 | AC-1 | integration: repair, then the backup text written back with `UPDATE` compares equal to the pre-repair text; backup basename matches `.gitignore:41` | Daemon-start snapshot repair | new: tests/database/test_close_snapshot_repair.py (as S-1.2) | R1 (captured) |
| S-1.13 | AC-1 | integration, failure simulated: the backup path points into a missing directory, and separately `os.fsync` is patched to raise `OSError(ENOSPC)`; the row is unchanged, one log record carries only the type name, a later call with a good path repairs | Daemon-start snapshot repair | new: tests/database/test_close_snapshot_repair.py (as S-1.2) | R1 (captured) |
| S-1.14 | AC-1 | integration: the repair, then `POST /api/settings/flush-resync` (`app.py:6308`) with the background reset awaited and the Composer refresh patched; the stored snapshot's serialized text compared before and after | Flush-resync background reset | `tests/app/test_flush_resync_race.py:56` (client fixture) and `:68` | R1 written through the repair under test |
| S-1.15 | AC-1 | integration: the repair, then `alpha_bot_execution.ensure_bot_state_seeded()` with the Composer fetch patched; no save, snapshot text unchanged, no reserved-key entry added | Startup seed | `tests/engine/test_startup_seed_symphonies.py:781` (TestPresenceCheckExcludesMetadataKeys) | R1 written through the repair under test |
| S-1.16 | AC-1 | integration: the repair, then `alpha_bot_execution.main()` at a closed-market minute (path `alpha_bot_execution.py:660-677`) and at an intraday minute (save `:2048`) with network patched; snapshot text unchanged after each, then an EOD run writes a new flat one | Engine per-cycle `bot_state` save | new: tests/engine/test_close_snapshot_builder.py (harness `tests/dashboard/test_market_mode.py:267` with the real database module) | R1 written through the repair; Composer stats `tests/fixtures/composer/v3-audit/symphony_stats_meta.json` (captured) |

### C2
Slices: (a) the two new files and `tests/dashboard/test_dashboard_render_consistency.py`, `tests/app/test_api_state_connection_count.py`; (b) `tests/app/test_[a-d]*.py`; (c) `tests/app/test_[e-l]*.py`; (d) `tests/app/test_[m-r]*.py`; (e) `tests/app/test_[s-z]*.py`; (f) `tests/dashboard`; (g) `tests/realtime_push`, `tests/ui`, `tests/portmode`; (h) `tests/analytics`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.1 | AC-2 | route, real DB, market open | `GET /api/state` open | new: tests/app/test_api_state_single_computation.py, harness `tests/app/test_api_state_connection_count.py:137` | R6 and R2 seeded through `database.record_shadow_observation` and `database.save_state` (captured) |
| S-2.2 | AC-2 | route, real DB, closed_frozen | `GET /api/state` closed_frozen | new: tests/app/test_api_state_single_computation.py (as S-2.1) | R8 (derived) with R6 |
| S-2.3 | AC-2 | route, real DB, pre_market | `GET /api/state` pre_market | new: tests/app/test_api_state_single_computation.py (as S-2.1) | R8 (derived) with R6 |
| S-2.4 | AC-2 | render then poll in each of three states, text of rows, headline, counts and as-of compared | `GET /` server render | `tests/dashboard/test_dashboard_render_consistency.py:141` | R8 and R6, frozen clock per state |
| S-2.6 | AC-2 | route, figures compared to the ref's strip | `GET /api/state` open | new: tests/app/test_api_state_single_computation.py (as S-2.1) | `golden_ref_api_state_<branch>.json` (captured from the 37c8e983 producer) |
| S-2.7 | AC-2 | route with `wraps=` counting spies on `_compute_portfolio_strip` and `_compute_frozen_portfolio_strip`, every branch | `GET /api/state` closed_frozen | new: tests/app/test_api_state_single_computation.py (as S-2.1) | R8 and R6 |
| S-2.8 | AC-2 | route with the account cache masked and last-good empty | `GET /api/state` open | new: tests/app/test_api_state_single_computation.py (as S-2.1) | R6 and R2 |
| S-2.9 | AC-2 | route, two threads in flight | `GET /api/state` open | new: tests/app/test_api_state_single_computation.py (as S-2.1) | R6 and R2 |
| S-7.1 | AC-7 | differential route test: cycle M's rows present with `last_successful_cycle_at` at M-1, against the same request with M's rows absent | `GET /api/state` open | new: tests/app/test_api_state_one_cycle.py, harness `tests/app/test_api_state_connection_count.py:137` | R6 (captured, two consecutive cycles) |
| S-7.2 | AC-7 | route after M's state is committed | `GET /api/state` open | new: tests/app/test_api_state_one_cycle.py (as S-7.1) | R6 |
| S-7.3 | AC-7 | route mid-cycle for a symphony with no row before M | `GET /api/state` open | new: tests/app/test_api_state_one_cycle.py (as S-7.1) | R6 with that symphony's earlier rows omitted (named omission) |
| S-7.4 | AC-7 | route after the cycle, compared to the ref | `GET /` server render | new: tests/app/test_api_state_one_cycle.py (as S-7.1) | `golden_ref_api_state_open.json` (captured from the 37c8e983 producer) |
| S-7.5 | AC-7 | route with `_committed_shadow_conn` and, separately, `database.load_state` raising (500 with the error body); and with one analytics helper's read raising (that leg the dash, other legs from one cycle) | `GET /api/state` open | new: tests/app/test_api_state_one_cycle.py (as S-7.1) | R6 |
| S-7.6 | AC-7 | two routes within one cycle, one with M's rows arriving between them | `GET /api/state` open | new: tests/app/test_api_state_one_cycle.py (as S-7.1) | R6 |

### C3
Unit families of the two suite helpers C3 owns, written in round-1 RED beside the scenario rows: RC-5 in `tests/realtime_push/test_data_freshness_visibility.py:111` (aware ET, aware UTC, naive gives `--`, `None` gives `--`, a `datetime.time` gives the date-not-recorded form, `seconds=True`, `previous_session=True`, one instant on each side of the 2026-11-01 DST change, and no output carrying the request time, checked with the clock frozen far from the moment); RC-6 in the new `tests/market_calendar/test_most_recent_completed_session.py` on the real XNYS calendar (a weekday evening, Monday pre-market, a holiday, a half day before and after 13:00, the lookback bound returning `None`).

Slices: (a) `tests/realtime_push/test_data_freshness_visibility.py`, `tests/app/test_api_state_single_computation.py`, a new `tests/market_calendar/test_most_recent_completed_session.py`; (b) to (e) the C2 `tests/app` quarters; (f) `tests/dashboard`; (g) `tests/realtime_push`, `tests/ui`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.5 | AC-2 | route with the portfolio computation forced to raise | `GET /api/state` open | tests/app/test_api_state_single_computation.py (from C2; harness `tests/app/test_api_state_connection_count.py:137`) | R6 and R2 |
| S-3.1 | AC-3 | route and render at 18:00 ET | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 variant with `captured_at_et` 16:00:42 (derived) |
| S-3.2 | AC-3 | route at 16:00:20 ET with the earlier-session snapshot | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 variant one session earlier (derived) |
| S-3.3 | AC-3 | route at Monday 08:00 ET with Friday's snapshot, plus unit tests of `most_recent_completed_session` | `GET /api/state` pre_market | `tests/realtime_push/test_data_freshness_visibility.py:597`; new: tests/market_calendar/test_most_recent_completed_session.py (real XNYS calendar, `market_calendar.py:24`) | R8 with `trading_day` set to that Friday (derived) |
| S-3.4 | AC-3 | route on a weekday holiday evening | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 with `trading_day` the session before the holiday (derived) |
| S-3.5 | AC-3 | route with a legacy snapshot | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 without `trading_day` (derived) |
| S-3.6 | AC-3 | route with no snapshot | `GET /api/state` closed_frozen | `tests/dashboard/test_market_mode.py:590` | R3 (derived) |
| S-3.7 | AC-3 | route polled across an evening with no snapshot for D | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 one session earlier (derived) |
| S-3.8 | AC-3 | route at 13:00:20 ET on an XNYS half day | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 with `trading_day` the session before a half day (derived) |
| S-3.9 | AC-3 | route: only date fields and the marker differ from C2's frozen response | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 and the C2 response captured in the same test |
| S-3.10 | AC-3 | route at 18:00 and 18:10 | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 |
| S-4.1 | AC-4 | route at 10:43 ET | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 (captured) |
| S-4.2 | AC-4 | route with rows of two later cycles omitted and the cycle stamp advanced | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 with named omissions |
| S-4.3 | AC-4 | route and render with no rows and no stamp | `GET /` server render | `tests/realtime_push/test_data_freshness_visibility.py:729` | R2 without rows |
| S-4.4 | AC-4 | route with an unparseable cycle stamp | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:729` | R6 with `last_successful_cycle_at` replaced (derived) |
| S-4.5 | AC-4 | route with the computation forced to raise | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 |
| S-4.6 | AC-4 | route at 09:30:05 ET | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 with D's rows omitted |
| S-4.7 | AC-4 | route at 00:01 ET | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 row with `ts_utc` moved to 23:59 ET (derived) |
| S-4.8 | AC-4 | route with the cache stale and last-good present; source scan for `datetime.now(` near stamp fields (DC-2) | `GET /api/state` open | `tests/app/test_eod_account_basis_refresh.py:292` | `tests/fixtures/composer/v3-audit/total_stats.json` (captured) |
| S-4.9 | AC-4 | two routes in one minute | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 |
| S-4.10 | AC-4 | route at 21:00 ET | `GET /api/state` closed_frozen | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 with 16:00:42 (derived) |
| S-4.11 | AC-4 | route at 07:00 ET next day | `GET /api/state` pre_market | `tests/realtime_push/test_data_freshness_visibility.py:597` | R8 |
| S-4.12 | AC-4 | route mid-cycle | `GET /api/state` open | `tests/realtime_push/test_data_freshness_visibility.py:111` | R6 (captured, two cycles) |

### C4
Slices: (a) `tests/dashboard/test_eod_account_basis.py`, `tests/app/test_eod_account_basis_refresh.py`, `tests/app/test_windowed_strip_route_live.py`, `tests/dashboard/test_dashboard_render_consistency.py`, `tests/app/test_closed_market_cumulative_bounce.py`; (b) to (e) the `tests/app` quarters; (f) `tests/dashboard`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-5.1 | AC-5 | route twice with `_refresh_account_totals` driven between them against a patched `requests.get` | `GET /api/state` closed_frozen | `tests/dashboard/test_eod_account_basis.py:703` | `total_stats.json` (captured) and one variant with `todays_percent_change` changed (derived) |
| S-5.2 | AC-5 | route pair: strip 30d and frozen poll | `GET /api/strip/{window}` | `tests/app/test_windowed_strip_route_live.py:107` | R8 and R6 |
| S-5.3 | AC-5 | strip 1y with live entries different from the snapshot's | `GET /api/strip/{window}` | `tests/app/test_windowed_strip_route_live.py:107` | R8 plus R2 live state (captured) |
| S-5.4 | AC-5 | hero-chart 30d after close with a later day's rows present | `GET /api/hero-chart/{window}` | `tests/app/test_windowed_strip_route_live.py:165` | R6 and R8 one session earlier (derived) |
| S-5.5 | AC-5 | route before any read after capture, then after the first one | `GET /api/state` closed_frozen | `tests/dashboard/test_eod_account_basis.py:703` | `total_stats.json` |
| S-5.6 | AC-5 | refresh failing all evening | Account-totals refresh job | `tests/app/test_eod_account_basis_refresh.py:92` | `total_stats.json` as the last good read |
| S-5.7 | AC-5 | live route reads per request | `GET /api/state` open | `tests/app/test_eod_account_basis_refresh.py:292` | `total_stats.json` and its variant |
| S-5.8 | AC-5 | Friday evening route then Monday 08:00 route, no restart | `GET /api/state` pre_market | `tests/dashboard/test_eod_account_basis.py:703` | R8 with Friday's `trading_day` (derived) |
| S-5.9 | AC-5 | new snapshot stored, then route | `GET /api/state` closed_frozen | `tests/dashboard/test_eod_account_basis.py:703` | R8 and a next-session variant (derived) |
| S-5.10 | AC-5 | unknown token after close | `GET /api/strip/{window}` | `tests/app/test_windowed_strip_route_live.py:107` | none needed beyond R8 |
| S-5.11 | AC-5 | render after a refresh following the first poll | `GET /` server render | `tests/dashboard/test_dashboard_render_consistency.py:141` | R8 and `total_stats.json` |
| S-5.12 | AC-5 | route twice with a refresh between | `GET /api/state` closed_frozen | `tests/app/test_closed_market_cumulative_bounce.py:346` | `total_stats.json` and its variant |

### C5
Slices: (a) `tests/app/test_closed_market_cumulative_bounce.py`, `tests/app/test_f011_state_field_availability.py`, `tests/dashboard/test_card_verdict_dial_first_paint.py`, `tests/dashboard/test_card_guard_alpha_basis.py`; (b) to (e) the `tests/app` quarters; (f) `tests/dashboard`; (g) `tests/security` (no-trade scans).

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-6.1 | AC-6 | route after close, guard alpha recomputed from the seeded exit row and entry | `GET /api/state` closed_frozen | `tests/app/test_closed_market_cumulative_bounce.py:221` (frozen client `:199`) | R8 and R7 (captured), written through `database.record_exit_trigger` |
| S-6.2 | AC-6 | render then poll after close, card by card | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:331` (world harness) | card_truth records R1 to R3 (`origin/ux-overhaul/05-c2:tests/fixtures/dashboard/card_truth/PROVENANCE.md`, captured and derived) |
| S-6.3 | AC-6 | route and render with no exit row | `GET /api/state` closed_frozen | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:376` | card_truth R1 without R3 |
| S-6.4 | AC-6 | route in both branches with `current_return` absent | `GET /api/state` open | `tests/app/test_closed_market_cumulative_bounce.py:208` (open client) and `:199` | R8 variant without `current_return` (derived) and R7 |
| S-6.5 | AC-6 | render and first poll at 08:00 ET | `GET /api/state` pre_market | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:331` | card_truth records with the snapshot dated the prior session |
| S-6.6 | AC-6 | live route compared to the ref | `GET /api/state` open | `tests/app/test_closed_market_cumulative_bounce.py:208` | `golden_ref_api_state_open.json` (captured from the 37c8e983 producer) |
| S-6.7 | AC-6 | route with `?account=` after close | `GET /api/state` closed_frozen | `tests/app/test_closed_market_cumulative_bounce.py:199`; client narrowing `origin/ux-overhaul/05-c3:tests/dashboard/test_account_filter_refresh.py` | `origin/ux-overhaul/05-c3:tests/fixtures/dashboard/account_filter/two_accounts_bot_state.json` (captured per its PROVENANCE) |
| S-6.8 | AC-6 | route with `database.get_guard_alpha_by_symphony` raising | `GET /api/state` closed_frozen | `tests/app/test_closed_market_cumulative_bounce.py:199` | R8 |
| S-6.9 | AC-6 | three routes on one snapshot | `GET /api/state` closed_frozen | `tests/app/test_closed_market_cumulative_bounce.py:199` | R8 and R7 |
| S-8.3 | AC-8 | route after close: each frozen entry's `_tc`, `_cr`, `_mdd` legs appear in its `symphonies` entry | `GET /api/state` closed_frozen | `tests/app/test_f011_state_field_availability.py:90` | R8 |

### C6
Slices: (a) the new scan file, `tests/app/test_f011_state_field_availability.py`, `tests/app/test_phase2b_portfolio_display.py`, `tests/analytics/test_portfolio_vol_computation.py`, `tests/telemetry/test_exit_triggers_schema.py`, `tests/js_syntax`; (b) `tests/dashboard`; (c) `tests/ui`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-8.1 | AC-8 | source scan: every id and selector `static/index.js` writes is present in `templates/index.html` or a template it includes, ids the script creates itself excepted | `GET /` server render | new: tests/dashboard/test_index_js_writes_existing_elements.py, source-reading pattern `tests/dashboard/test_display_truth_cluster_js.py:32` | the committed `static/index.js` and templates |
| S-8.2 | AC-8 | route in every branch with `database.get_triggers` spied | `GET /api/state` open | `tests/app/test_f011_state_field_availability.py:40` | R6 and R2 |
| S-8.4 | AC-8 | route: every other `meta` and top-level key against the ref | `GET /api/state` closed_frozen | `tests/app/test_f011_state_field_availability.py:90` | `golden_ref_api_state_<branch>.json` (captured from the 37c8e983 producer) |
| S-8.5 | AC-8 | source scan of the comment beside `POLL_INTERVAL_MS` | `GET /` server render | new: tests/dashboard/test_index_js_writes_existing_elements.py (as S-8.1) | the committed `static/index.js` |

### C7
Slices: (a) `tests/dashboard/test_card_verdict_dial_first_paint.py`, `tests/dashboard/test_card_guard_alpha_basis.py`, `tests/js_syntax`; (b) `tests/dashboard`; (c) `tests/ui`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-9.1 | AC-9 | render then node polls across two seeded cycles, market open | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:331` | card_truth R1 and R3 with two `current_return` values (derived) |
| S-9.2 | AC-9 | render and three polls after close | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:331` | card_truth R1 and R3 with a frozen snapshot |
| S-9.3 | AC-9 | node polls with the figure crossing zero | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:323` | card_truth R3 family values of both signs |
| S-9.4 | AC-9 | render and poll with no guard alpha | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:376` | card_truth R1 without R3 |
| S-9.5 | AC-9 | route figure against the exit row and entry | `GET /api/state` open | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:300` | card_truth R1 and R3 |
| S-9.6 | AC-9 | three node polls with no cycle | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:331` | card_truth R1 and R3 |
| S-9.7 | AC-9 | render and poll with guard alpha exactly 0; label rename consumers | `GET /` server render | `origin/ux-overhaul/05-c2:tests/dashboard/test_card_verdict_dial_first_paint.py:290`; `tests/dashboard/test_card_guard_alpha_basis.py:158` (rewritten) | card_truth R3 with `g = 0.0` |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | The repair rewrites the row the engine trades from; a concurrent writer could restore the chain or lose a field. Finding: the engine and the daemon both write `bot_state` (`alpha_bot_execution.py:2048`, `app.py:6391`), and the engine lock expires at 60 s (`database.py:299`). | Low | High | Repair under the engine lock and `BEGIN IMMEDIATE`, before the scheduler and Flask start; S-1.8 covers the writer race; the next close's builder replaces the whole snapshot, so a lost repair heals at 16:00. |
| C1 | Byte-equality rests on JSON round-trip stability. Finding: verified on the captured 2,039,413 B row; `save_state` encodes with plain `json.dumps` (`database.py:373`). | Low | High | The repair refuses (`not_byte_stable`) when the stored text does not round-trip, and S-1.2 compares serialized text field by field. |
| C1 | A failed `release_lock` leaves `is_locked = 1`, so the first engine minute after start skips. Finding: `database.py:292-317`. | Low | Medium | `release_lock` in `finally`; the 60 s expiry bounds it to one minute; the PM's live check reads the first cycle line. |
| C1 | The re-pinned full-file hash stops guarding the rest of the engine file. Finding: `tests/test_mdd_window_truth_zero_diff_guard.py:69` is the only engine pin. | Medium | High | The new builder-excised hash pinned at 37c8e983 keeps every other line pinned. |
| C1 | The S-1.2 fixture is the full production row with the real account id. Finding: about 274 KB gzipped on the audit's copy; the same id is already committed in `tests/fixtures/composer/v3-audit/dashboard_api_state.json`. | Medium | Low | Stored gzipped, one record from the PM's droplet capture, provenance recorded; derived records signed off (SD-7). |
| C2 | The temp view only filters queries on the request's connection; any analytics reader given no connection reads in-flight rows. Finding: readers open their own connection when `conn` is `None` (`analytics.py:618-650`, `:1652`). | Medium | Medium | The differential S-7.1 test fails on any such reader; the route never falls back to per-call connections (S-7.5). |
| C2 | Refactoring the 1,000-line `get_state()` regresses existing route tests. Finding: 58 test files reference `data_as_of`/`frozen_at`, and 29 set `"portfolio_strip":`, several through `get_api_state_dict` stubs. | High | Medium | DC-3 and DC-5 sweeps in round-1 RED; goldens from the ref for S-2.6; `tests/app` run in four slices. |
| C2 | Merge conflicts with ux-overhaul 05 in `dashboard()` and `get_state()`. Finding: `origin/ux-overhaul/05-c1`, `-c2` and `-c3` all edit those functions. | High | Medium | C5's base merge (C5's pair) and I-2 resolve them as RC-9 test-writer and implementer tasks and rerun C2 to C4's sets and ux-overhaul 05's dashboard tests; a production-code conflict adds a `qa` and a `code-reviewer` at that head. |
| C2 | `shadow_divergence` still reads through `database.get_shadow_divergence`'s own connection (`app.py:3390`), outside the committed view. | Medium | Low | It is a Q-4 field no card reads; noted in the route docstring. |
| C3 | The dated stamp breaks string matchers. Finding: 58 test files; `static/index.js:1328` writes it verbatim. | High | Low | DC-5 sweep with the command in Per-Cycle Detail; the client needs no change. |
| C3 | Previous-session logic misjudges a holiday or half day. Finding: `market_calendar.session_close` (`market_calendar.py:81-104`) carries half days. | Low | Medium | S-3.4 and S-3.8 run on the real XNYS calendar, never a stub. |
| C3 | `_build_meta` now passes `None` where it gave `0.0`. Finding: the template coerces with `or 0` (`templates/index.html:881-884`). | Medium | Low | Sweep of tests pinning `0.0`; render unchanged. |
| C4 | The latch adds a state read to the refresh job that runs in the scheduler thread (`app.py:1429`; TA-15 blocking). | Low | Medium | Read only while closed and only until the latch holds the latest completed session's snapshot. |
| C4 | Composer's `todays_percent_change` after the close is unconfirmed (plan Known-Unconfirmed). | Medium | Medium | Q-1 approved; the PM's two droplet reads at 16:05 and 00:30. |
| C4 | Plan 05 adds Composer-call logging inside `_refresh_account_totals`, and ux-overhaul 05 C1 moves `get_hero_chart` into `_hero_chart_body` (`origin/ux-overhaul/05-c1:app.py:3600`). | High | Low | C5's base merge carries the frozen cut into `_hero_chart_body`; S-5.4 reruns after it. |
| C5 | `_FROZEN_SYM_DEFAULTS` turns a missing `current_return` into `0.0` (`app.py:2810-2823`). | High | Medium | Inject guard alpha before the defaults; S-6.4 covers both branches. |
| C5 | `_inject_guard_alpha` exists only on ux-overhaul 05 (`origin/ux-overhaul/05-c2:app.py:1666`). | Medium | Medium | C5 forks only from the plan tip holding ux-overhaul 05. |
| C6 | The selector scan flags ids the script builds itself. Finding: cards and panels are built from strings in `static/index.js`. | Medium | Low | The scan excludes ids assigned in the same script. |
| C7 | A reader of the old test id is missed. Finding: `origin/ux-overhaul/05-c2:static/index.js:385`, `tests/dashboard/test_card_guard_alpha_basis.py:158`. | Medium | Low | DC-5 grep in RED. |

## Cycle-Close Criteria

A cycle's cycle-complete is final only when all of these hold at its HEAD:
- Every scenario assigned to the cycle above has a passing test, or a held row in `records/coverage.json` with its reason; the coverage record is complete and `plan-trace check-coverage` is clean for the cycle.
- RED preceded GREEN in the branch history; the RED commit failed for the scenario's reason.
- Each named slice ran under 240 s with zero failures and zero errors; the SHA quoted in the report is the HEAD the slices ran on.
- `ruff format --check` and `ruff check` are clean (line length 100).
- The sweep commands in Per-Cycle Detail were rerun at HEAD and every hit is changed or listed with its reason.
- C1 only: `git diff 37c8e983 -- math_engine.py` is empty, `alpha_bot_execution.py` differs only inside the builder, and the builder-excised pin passes.
- No assertion copies a producer value; fixtures carry their provenance in the cycle's own `tests/fixtures/refresh_timing/01-c<K>/PROVENANCE.md`, and captured records come from the PM's droplet run, never the scratchpad launcher database.
- Every reviewer dispatched for the head reads PASS.

## Merge Criteria

For the one PR from `refresh-timing/01` to `main`:
- The tip holds model-unpin and ux-overhaul 05 through merges from `main` (C5's base merge and I-2), ux-overhaul 05 is merged to `main` (plan Depends on), every merge is a merge commit run by a test-writer and implementer pair (RC-9), and `git log --merges` shows each input entering that way.
- `ruff format --check`, `ruff check`, and the plan's Gates pytest run (its twelve paths, `tests/analytics`, `tests/ui`, `tests/portmode`, `tests/telemetry` and `tests/security` included), in slices, at most 4 workers; CI green on the exact SHA.
- `git diff main...HEAD -- math_engine.py` is empty; `alpha_bot_execution.py` differs only inside the close-snapshot builder; the NB-7 boundary tests pass.
- No migration: the migration pin in `tests/test_mdd_window_truth_zero_diff_guard.py:131-156` still reads 038.
- Repair proof on a copy of the production database: start the daemon on the copy, then show the backup file, the flat snapshot, `/api/state` after the close with 12 entries and no phantom, and the hand restore giving back the pre-repair text exactly (S-1.12's command).
- `/review` with findings resolved or accepted.
- The PM's live functional check on the droplet after deploy, as the plan's real-entrypoint column states: the repair's log line and backup file at the first start; the 16:01 and 18:00 reads (dated as-of, `trading_day`, marker false); two polls ten minutes apart after the close with equal figures and a matching 30d click; `GET /` and a poll after the close with matching cards and "at close" verdicts; 09:29 and 09:31 stamps; a timer poll during a market-hours cycle; two market-hours cycles showing "vs held now". Tests-green is necessary and never sufficient here.

## Items Raised to the PM

none: the six write paths, the AC-6 dependency on ux-overhaul 05, the Gates paths and the S-7.5 reading are now in the plan; the stamp helper and the previous-session helper are built in C3 as `approach/00-suite.md` RC-5 and RC-6 specify, and fixtures and merges follow its RC-7 and RC-9; the derived fixture records are signed off.
