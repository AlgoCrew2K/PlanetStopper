# Diagnosis: Dashboard "Actual Return" Frozen — State Not Updating

**Date:** 2026-05-14
**Type:** Research / diagnosis only — no production code written.
**Working repo:** `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM` (main, real `.env`)

---

## Root Cause (one line)

**A poisoned `history_cache.json` (written by the 07:38 cycle after Alpaca returned HTTP 401) makes every subsequent scheduled cycle silently `return` at the `if not historical_data: return` guard (`alpha_bot_execution.py:427-428`) — before any state write.**

The daemon is alive, the scheduler is firing every minute, and `database.save_state` is *reachable* — but no scheduled cycle today has ever reached it.

---

## Evidence

### 1. Daemon is alive, scheduler IS running
- `python.exe app.py` running as **PID 59420**, started **2026-05-14 07:37:52**.
- `/tmp/alphabot_daemon.log` shows `[HH:MM:01] Alpha Bot Waking Up...` lines continuing right up to the latest minute (`[09:55:00]`). Scheduler is healthy.

### 2. Every scheduled cycle dies after 3 log lines — never evaluates symphonies
Each scheduled cycle prints **only**:
```
[HH:MM:01] Alpha Bot Waking Up...
MODE: DRY RUN (SAFE)
  -> Loading static 3-year history from local cache.
```
...then nothing. The next expected line, `-> Macro Environment: SPY x.xx%` (`alpha_bot_execution.py:435`), never appears.

Counts across the full daemon log (138 cycles today):

| Log marker | Count |
|---|---|
| `Alpha Bot Waking` | 138 |
| `Loading static 3-year history` (cache hit) | 136 |
| `Fetching 3-year` (cache miss / re-fetch) | 2 |
| `Macro Environment` (got past line 427) | **0** |
| `Evaluating Symphonies` | **0** |
| `Overlap Detected` (lock skip) | 2 |

**Zero scheduled cycles all day got past the `historical_data` guard.**

### 3. The poisoned cache
`history_cache.json`: **54 bytes**, mtime **08:01:14**.
```
date:    2026-05-14   (today — so the freshness date-check PASSES)
tickers: ["SPY"]      (only 1 ticker)
data:    {}           (EMPTY — zero date-keys)
```

### 4. How the cache got poisoned (daemon log head)
```
[07:38:01] Alpha Bot Waking Up...
  -> New trading day detected (2026-05-14 ET). Wiping transient state keys...
Fetching 3-year history from Alpaca for Monte Carlo (1 tickers)...
  -> Downloading batch 1: 1 tickers...
Alpaca API Error on batch (attempt 1/3): HTTP 401
Alpaca API Error on batch (attempt 2/3): HTTP 401
Alpaca API Error on batch (attempt 3/3): HTTP 401
Failed to download batch after multiple retries.
  -> History download complete. Saving to daily cache.   <-- writes EMPTY {} to cache
```
The 07:38 cycle ran with only `SPY` in `all_tickers` (Composer returned no holdings that early, so `fetch_symphony_stats` -> `[]`). Alpaca then rejected the request with **401** three times. Despite the failure, `fetch_alpaca_history` falls through to line 214-217 and **writes the empty `historical_data` dict to `history_cache.json` with today's date** — no guard prevents caching a failed/empty result.

### 5. Why every cycle after that is stuck
On subsequent cycles `fetch_alpaca_history` checks `cache.get("date") == current_date_str` (True) **and** `cache.get("tickers") == tickers_list`. The log proves the cache-hit branch (line 148) is being taken 136 times — so `tickers_list` is *also* matching `["SPY"]`, meaning **`all_tickers` is still just `SPY` on those cycles** (Composer `fetch_symphony_stats` returning `[]` for every account in the daemon's runs). Cache hit -> returns empty `{}` -> `if not historical_data: return` (line 427) -> **silent exit, no `save_state`**.

The 2 `Fetching 3-year` cycles did try a re-fetch (tickers mismatched) but they too produced no `Macro Environment` line — they hit the same 401 and re-poisoned the cache, or exited at line 428.

### 6. State DB confirms staleness
- `alphabot_state.db` mtime updates every minute **only because `state_changed`/lock paths touch it** — NOT because returns are refreshed. (The `state_changed` block at lines 339-357 and `acquire_lock` writes can bump mtime without updating returns.)
- Stuck `bot_state` values: every symphony has `current_return == prev_return == high_water_mark` (identical), e.g. `n2ooAZTvBRN6ZzpMmWmU` = `-0.15`. These are the *initialization* values from the first (failed) evaluation — never advanced.

### 7. Manual cycle proves the engine is otherwise healthy
Ran `PYTHONIOENCODING=utf-8 python alpha_bot_execution.py` (DRY RUN, `LIVE_EXECUTION=False`):
- `fetch_symphony_stats` returned holdings -> **35 tickers**.
- Cache `tickers` mismatch (`35 != 1`) correctly triggered a re-fetch.
- Alpaca download **succeeded** -> rebuilt `history_cache.json` to **2.7 MB, 35 tickers, 772 date-keys**.
- Fully evaluated all **11 symphonies**, wrote fresh state.
- Fresh `current_return` values **differ** from the stuck DB:

| Symphony | Stuck DB | Fresh manual cycle |
|---|---|---|
| n2ooAZTvBRN6ZzpMmWmU | -0.15 | **-0.26** |
| INfCn3eKsu6i4oTTqdUp | -0.14 | **-0.01** |
| iaSOOUsmnCJHiZvbrWfs | -0.32 | **-0.42** |
| 8FAXAnQmYi1INDubazeC | 0.15 | **0.40** |
| ... | ... | ... |

The engine, the write path, `save_state`, and the Composer/Alpaca clients all work — **when the cache isn't poisoned and Composer returns holdings.**

### 8. Lock is NOT the cause
`execution_lock` row: `is_locked=0`, fresh timestamp. `acquire_lock` (`database.py:94-106`) has a correct 60s stale-expiry. Only 2 `Overlap Detected` skips all day. Not the bottleneck.

### 9. Dashboard side is fine
`/api/state` (`app.py:71-74`) calls `database.load_state()` fresh on every request — no caching. The dashboard would immediately show fresh numbers the moment `bot_state` is updated. **The dashboard is not the bug.**

---

## Contributing Bugs (the real defects)

1. **`fetch_alpaca_history` caches failure results.** After 3x HTTP 401 / "Failed to download batch", it still falls through to line 214-217 and writes whatever `historical_data` it has (here `{}`) to `history_cache.json` with today's date. A failed fetch must NOT be cached — this is what makes the failure *sticky* for the rest of the day.

2. **`if not historical_data: return` (line 427-428) is a silent, log-less, state-less early exit.** No error line, no `save_state`. The operator has no signal anything is wrong — the daemon *looks* healthy.

3. **Alpaca HTTP 401 in the daemon context.** The 07:38 daemon cycle got 401 from Alpaca; the manual cycle just now succeeded. Likely cause: the daemon process (PID 59420, started 07:37:52) was launched with an environment that lacked / had stale `ALPACA_KEY`/`ALPACA_SECRET`, OR Alpaca creds were updated in `.env` after the daemon started. `subprocess.run` in `app.py:53` passes `env=os.environ.copy()` — the spawned cycle inherits the **daemon's** process env, not a fresh read of `.env`. Note: `alpha_bot_execution.py` itself does `load_dotenv()` at import, so it *should* pick up `.env`... unless `.env` itself had a bad Alpaca key at 07:38 and was fixed later, or the 401 was a transient Alpaca-side issue at open. Either way, the *first* fetch failing is what seeded the poison; the **caching of that failure** is what made it permanent.

4. **`fetch_symphony_stats` returning `[]` in daemon cycles.** The 136 cache-hit cycles imply `all_tickers == {SPY}` repeatedly — i.e. Composer returned no symphonies in the daemon's runs even after the manual cycle got 35 tickers. Needs follow-up: is this another env/credential drift between the daemon process and a fresh shell, or Composer rate-limiting the daemon? This is secondary to the cache poison but must be confirmed, or scheduled cycles will still produce empty `all_tickers` even after the cache is cleared.

---

## Fix Recommendation

**Immediate unblock (config/ops, no code):** Delete `history_cache.json` so the next scheduled cycle is forced to re-fetch. If the daemon's Alpaca/Composer creds are stale, **restart the daemon** (`app.py`) so the subprocess env picks up the current `.env`. The manual cycle already proved a fresh run repopulates state correctly — clearing the cache + healthy creds restores per-minute updates.

**Proper fix — needs a TDD team (new codepaths in `alpha_bot_execution.py`):**

1. **Do not cache failed/empty fetches.** In `fetch_alpaca_history`, only write `history_cache.json` if the download actually succeeded and `historical_data` is non-empty. Treat "all batches failed" as a hard no-cache. (New guard codepath.)
2. **Make the `historical_data` empty-exit loud.** Replace the bare `return` at line 427-428 with an explicit `print(...)` error line (and ideally a Discord alert) so a stuck engine is visible in the log. (New codepath.)
3. **Re-validate cache content, not just date+tickers.** A cache with `data == {}` should be treated as a miss even if date/tickers match.
4. **Investigate the daemon-vs-shell credential/Composer drift** (contributing bug #3 and #4) — confirm whether `.env` was bad at 07:38, or the daemon needs to re-`load_dotenv()` per cycle, or Composer is rate-limiting the daemon.

This is **not** a one-line guard fix — it touches the cache-write decision, the empty-exit handling, and cache validation in the live API path. Per project CLAUDE.md ("API calls must be testable from a fixture") this warrants a **Quad team** (test-writer + implementer + quant-code-reviewer + composer-alpaca-integration) with fixture-driven tests for: (a) Alpaca 401 -> no cache write, (b) empty cache -> treated as miss, (c) empty `historical_data` -> loud exit.

---

## Files Referenced
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py` — `main()` (276), `fetch_alpaca_history` (138-219, cache write 214-217, cache hit 147-149), `if not historical_data: return` (427-428)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\app.py` — subprocess spawn (45-58), `/api/state` (71-74)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\database.py` — `acquire_lock` (94-106), `release_lock` (108-115)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\history_cache.json` — poisoned (now rebuilt by the manual diagnostic cycle)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alphabot_state.db` — `bot_state` table
- `/tmp/alphabot_daemon.log` — daemon log
