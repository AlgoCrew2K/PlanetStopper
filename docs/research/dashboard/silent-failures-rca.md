# RCA: AlphaBot v3 Silent Failures — Daemon-up / State-stale Family

**Date:** 2026-05-15
**Analyst:** composer-alpaca-integration
**Type:** READ-ONLY root-cause analysis. No production code written, changed, or proposed for write in this document. Every claim cites file:line, log line, or DB row evidence. Where evidence is incomplete, the gap is named and the evidence that would close it is listed.
**Working repo:** `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM` (`main`, real `.env`)
**Daemon under investigation:** PID 84536, `python.exe app.py`, started **2026-05-15 08:00:09 local** (= **10:00 ET**), confirmed via `Get-CimInstance Win32_Process` query at 10:07 ET this morning. (Note: the brief describes PID 23528 started 2026-05-14 20:02 — that daemon is no longer running; a restart has occurred since the operator's observation. Findings below cover the recurring class of failure, not only the specific PID the operator saw.)

---

## 1. Executive Summary

**Every one of the four incidents resolves to a single anti-pattern: `alpha_bot_execution.py` has multiple early-return short-circuits on its per-cycle path that exit *without writing state and without raising the log level above INFO*, and `app.py` exposes no health signal that would let an operator distinguish "daemon alive and cycling but never reached `save_state`" from "daemon alive and working".** Incidents #1, #2, and #4 are three different paths into the same trap. Incident #3 (concurrent daemons) is a related-but-distinct process-supervision gap that compounds the other three by allowing inconsistent writers to interleave. The repeat appearance of the same shape — daemon up, dashboard data frozen, operator looks at the daemon log and sees "Waking Up..." every minute, no error — is the dominant operational risk on this codebase right now and must be addressed at the architectural level, not patched per-incident.

---

## 2. Per-Incident Root Cause

### Incident #1 — Daemon up but state stale during today's market hours

**Root cause:** the daemon's intraday gate at `alpha_bot_execution.py:305-313` opens only at `EXECUTION_START_TIME`, which is currently set to `10:30` in `.env` (verified via `dotenv_values`). Real-money market open is 09:30 ET. **Every cycle from 09:30 ET to 10:30 ET this morning passed `is_weekday=True` but failed `current_time >= market_open`, hitting line 310-313:**

```python
if not is_weekday or current_time < market_open or current_time > post_mortem_cutoff:
    if not force_run:
        print(f"  -> Market closed or in Grace Period (ET: ...). Sleeping...")
        return                                    # ← line 313
```

The `return` at line 313 exits before the date-rollover wipe (line 357-365), before `save_state` (the only `save_state` calls in the file are at lines 365, 416, 883), and before `chart_history` is touched. So `bot_state.date`, `bot_state.post_mortem_run`, and `chart_history.date` remain *yesterday's* values — exactly what the operator saw.

**Confirming evidence (read-only DB probe, 2026-05-15 ~10:07 ET):**

| Field | DB value | Today's value |
|---|---|---|
| `bot_state["date"]` | `2026-05-14` | `2026-05-15` |
| `bot_state["post_mortem_run"]` | `2026-05-14` | (should be unset for today) |
| `chart_history["date"]` | `2026-05-14` | `2026-05-15` |
| `chart_first_sym_points last time` | `15:52` (yesterday) | should be ticking forward this morning |
| `alphabot_state.db` mtime | `2026-05-15 08:04:00 local` | — |
| `execution_lock` row | `(0, 1778854021.06...)` → `2026-05-15 10:07:01 ET` | lock IS being acquired and released every minute |
| `history_cache.json` date / mtime | `2026-05-14` / `2026-05-14 12:42:07` | proves `fetch_alpaca_history` has not been reached today |
| Current ET | `2026-05-15 10:07 (Fri)` | — |
| `.env` `EXECUTION_START_TIME` | `10:30` | — |

The DB mtime advances every minute *only because `acquire_lock` (line 285) and `release_lock` (line 887) both write to the `execution_lock` row* (`database.py:103-104, 114`) — those writes touch the file without going near `save_state`. So "DB mtime fresh" is **not** proof of a healthy cycle. This is itself part of the silent-failure surface — the operator sees a recent mtime and assumes data is fresh.

Note: this branch *does* print `"Market closed or in Grace Period"` (line 312), so it is not silent on stdout. **But the daemon's stdout has no log file capture** (see Incident #3 / log-path discussion below — `restart.ps1` launches with `-WindowStyle Hidden` and no redirect; the prior daemon's `alphabot_daemon3.log` was driven by a different launcher and stopped writing at 14:39 local yesterday). So the print is invisible to the operator. From the operator's vantage — dashboard, DB file mtime — every signal says "healthy" while the engine is doing nothing.

**Why `EXECUTION_START_TIME=10:30` instead of `09:30` is itself another silent-failure surface:** the variable exists at `alpha_bot_execution.py:43` and is read on every cycle via `os.getenv`. There is no validation that `start_h:start_m` falls at or before `09:30` (real market open). A misconfigured value silently delays the entire intraday loop by N minutes/hours every day with no warning. Whether it was set deliberately for testing or as a leftover is undetermined read-only — but the fact that a typo or stale setting **silently disables real-money monitoring** for any portion of the trading day is itself the bug class.

**Scope of user-facing impact:** every dashboard field that derives from `bot_state` (live `current_return`, `current_value`, `mc_prob`, `stop_trigger`, `armed`/`triggered` flags, the entire symphony table) freezes at the prior cycle's snapshot. The portfolio strip (`app.py:185-189`) value-weights frozen `current_return`s and reports a frozen aggregate. `data_as_of` (`app.py:191`) is generated per-request via `datetime.now()` (`app.py:191`), so the dashboard's only "freshness" indicator is in fact independent of whether the engine cycled — it reflects the moment the page was loaded, not when state was last written. This is the gap that made today's failure mode look "alive" to the operator. **The dashboard has no field that says "last successful engine cycle at HH:MM" — `data_as_of` is a misnomer.**

**Confidence:** **HIGH**. All evidence is consistent: lock-row timestamp proves the scheduler IS firing every minute; DB mtime + `bot_state.date` mismatch proves the daemon is *not* reaching `save_state`; `history_cache.json` mtime (yesterday 12:42) proves the daemon is *not* reaching `fetch_alpaca_history`; the only return on the path between `acquire_lock` and `fetch_alpaca_history` that doesn't write state is the line 310-313 market-closed return; and `EXECUTION_START_TIME=10:30` with current ET=10:07 satisfies its predicate. The chain is complete.

---

### Incident #2 — `history_cache.json` poisoning (un-fixed)

**Root cause (two-stage, both still in the code):**

**Stage A — failed fetches are unconditionally cached** (`alpha_bot_execution.py:222-227`):

```python
print("  -> History download complete. Saving to daily cache.")
try:
    with open(HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": current_date_str, "tickers": tickers_list, "data": historical_data}, f)
except OSError as e:
    print(f"  -> Failed to write cache: {e}")
```

The `historical_data` dict at line 222 is whatever accumulated in the loop body. If every batch hit HTTP 401 / 429 / 5xx and `break`'d out of the inner retry loop (line 192-193), `data` is never iterated into `historical_data` and the dict is `{}`. There is **no guard** between the loop and the file write that checks whether anything actually came back. The cache file is overwritten with `{"date": "...", "tickers": [...], "data": {}}` — a structurally valid cache (passing the date+tickers equality check at line 155) that contains zero usable data. Once written, `if cache.get("date") == current_date_str and cache.get("tickers") == tickers_list` evaluates True for the rest of the calendar day → line 156-157 returns `{}` → `fetch_alpaca_history` becomes a no-op for that ticker set.

**Stage B — empty `historical_data` causes a silent state-less exit** (`alpha_bot_execution.py:434-436`):

```python
historical_data = fetch_alpaca_history(list(all_tickers), current_date_str)
if not historical_data:
    return
```

The bare `return` at line 436 has no print, no `database.log_symphony_event`, no Discord alert, no state write, no health-flag set. It is a literal silent exit. Combined with Stage A, this is the failure pattern documented in `docs/research/dashboard/state-not-updating-diagnosis.md`: a single Alpaca 401 at 07:38 → poisoned cache → every subsequent cycle for that day silently returns at line 436. The operational fix (delete cache + restart) clears it but does not address the underlying defect.

Note: the file mtime today (`history_cache.json` = 2026-05-14 12:42:07) is from yesterday's successful manual cycle (per the prior diagnosis); it is *not* poisoned now. Today's failure (Incident #1) is the `EXECUTION_START_TIME` gate, not this. But the cache-poisoning code path is unchanged and will fire again on the next Alpaca-side outage.

**Symphony_logs note:** even when the engine logs to `database.log_symphony_event` (`database.py:245`), that writes to `symphony_logs.json` *per symphony id*. There is no "engine-wide error" log channel. The two silent returns at lines 313 and 436 don't have a symphony id to attach to, so they couldn't use that channel even if asked to. There is no top-level engine error log table.

**Scope of user-facing impact:** identical to Incident #1 — all `bot_state`-derived dashboard fields freeze. The mechanism (and the user signal) is indistinguishable from Incident #1 except that the cache mtime would be *today*, not yesterday. From the dashboard operator's view, they look the same.

**Confidence:** **HIGH** for both stages. Line numbers are direct grep matches; the cache-write-on-failure path was empirically reproduced and documented in the prior diagnosis (07:38 cycle log + 54-byte poisoned cache + 138 subsequent silent cycles). The bare `return` at line 436 is visible in source.

---

### Incident #3 — 3 concurrent `python.exe app.py` daemons

**Root cause (likely, but not fully read-only-determinable):** `restart.ps1` (lines 24-27) matches processes by:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '* app.py*' }
```

This is correct as a *filter*. The mechanism that allowed 3 daemons to coexist before `restart.ps1` was run is upstream of the filter. The script kills what it finds and starts a new one, but **nothing in the codebase prevents two daemons from being started independently in the first place** — e.g.:

1. Two operator-initiated `python app.py` invocations (one from a hidden window started by `restart.ps1`, another from a foreground shell).
2. An OS-level service / scheduled task launching `app.py` while a manual instance is also alive.
3. A previous `restart.ps1` invocation failing to kill (e.g., `Stop-Process -Force` blocked by a permission error) and leaving the old one alongside the new.

There is **no PID file, no SQLite-level singleton check, no `socket.bind(port)` exclusivity, and no port-collision detection in `app.py`.** `app.py:619-631` does `app.run(port=5000, ...)`. If port 5000 is already bound, Flask raises `OSError: [WinError 10048]` at startup and *that* daemon would die — but the lifetime up to `app.run()` (which includes `init_db()` via `database.py:471` import, and starting the scheduler thread at `app.py:625`) executes regardless. So a second daemon attempts to write to the same `alphabot_state.db`, spawn its own minute scheduler, and only crashes once it tries to bind the HTTP port. By then, ten seconds of scheduler ticks could have run.

**Worse: each daemon's `run_scheduler` (`app.py:67-71`) runs `schedule.every().minute.at(":00").do(threaded_trigger)`.** Two scheduler threads → two parallel `threaded_trigger` calls at every :00 → two `subprocess.run` invocations of `alpha_bot_execution.py`. The execution lock (`database.acquire_lock`, `database.py:94-106`) is designed to handle that — and it does (`is_locked=1` with 60s stale-expiry, the loser hits `Overlap Detected` at line 286). **But the lock protects the engine, not the daemon-supervisor process itself.** Two daemons stamping the same DB at startup, two `database.init_db()` invocations on import racing — both are tolerated by SQLite's WAL + the `INSERT OR IGNORE` guards at `database.py:86-88`, but neither is *intended*.

**The largest concrete risk** isn't the lock race (the lock works). It's that any *file* writes outside the lock have no mutual exclusion — `history_cache.json` (line 224), `symphony_logs.json` (`database.py:264`), `chart_history` table writes (`database.save_chart_history`), and the EOD `post_mortem_*.json` writes can interleave between two daemon-spawned subprocesses. The cache-write race is particularly bad in concert with Incident #2: daemon-A finishes a successful Alpaca fetch and is mid-`json.dump` when daemon-B (just lost the lock, returned at line 287, exited cleanly) restarts a fresh cycle. The cache could end up with a half-written file or a stale-tickers shape, re-poisoning it.

**Scope of user-facing impact:** harder to characterize because we lack a captured incident log. Possible expressions: occasional cache corruption (manifests as Incident #2), occasional duplicate Discord alerts on liquidation (each daemon's subprocess processes the queue independently), inconsistent `bot_state` writes if two subprocesses both acquire the lock back-to-back and write conflicting values.

**Confidence:** **MEDIUM** for the broad claim that no daemon-level singleton exists (HIGH — `app.py` has no such guard). **LOW** for the specific cause of the three observed daemons (the brief mentions PIDs 33416/44156/65544 but no log was captured at the moment of the 3-way state, so I can't trace which launched first or how they coexisted).

**Evidence that would raise confidence to HIGH:** (a) a saved `Get-CimInstance` snapshot at the moment of the 3-way state with `ParentProcessId` and `CreationDate` for each; (b) a daemon-startup log from each of the three showing whether each successfully bound port 5000 or hit a port collision; (c) Windows Event Viewer entries around the disputed timeframe for `python.exe` start/stop events.

---

### Incident #4 — `b9ab6f9` (CR/MDD persist) shipped without live-path verification

**Root cause (process, not code):** the fix at `cf6a0e2` added `_persist_composer_fields_to_bot_state` (`alpha_bot_execution.py:98-103`) and wired it into **only** the intraday evaluation loop at **line 689**. The intraday loop is gated by `alpha_bot_execution.py:310-316` to run only when `current_time >= market_open AND current_time < rebalance_blackout`. Per `git show -s --format="%ai" cf6a0e2 b9ab6f9`:

- `cf6a0e2` (engine GREEN) committed **2026-05-14 19:31 local** (= 21:31 ET)
- `b9ab6f9` (merge) committed **2026-05-14 20:01:44 local** (= 22:01:44 ET)

Both are post-market. The EOD post-mortem branch at lines 397-432 also writes `bot_state` (line 416) but does NOT call `_persist_composer_fields_to_bot_state`. The market-closed early-return at lines 310-313 doesn't write state at all. So between the merge at 22:01 ET on 2026-05-14 and the next intraday loop iteration (today 10:30 ET earliest, per `EXECUTION_START_TIME=10:30`), the persist call has never executed on the live daemon.

Read-only DB probe at 10:07 ET today confirms it: `bot_state` for symphony `n2ooAZTvBRN6ZzpMmWmU` has **30 keys** — none of which are `simple_return`, `net_deposits`, `time_weighted_return`, or `max_drawdown`. The keys are *absent*, not `None` or `0.0`. Had line 689 ever run, the keys would *exist* with whatever value `sym.get(...)` returned (`.get()` with no default returns `None`, which still creates the dict key on assignment). Their total absence is direct evidence the line has never run since the merge. (This finding matches and re-confirms the follow-up section of `cr-mdd-zero-diagnosis.md`.)

**The process gate that failed:** the project's Gate-2 review (the "HOW") approved an implementation that wired new behavior into a market-hours-only codepath, and the "definition of done" did not include "verify a live cycle in market hours has executed the new line and the expected fields are present in `bot_state`". A unit-test pass against `tests/fixtures/composer/symphony_stats_meta.json` is *necessary* (and was passed — `4204830`/`cf6a0e2` is the RED/GREEN pair) but it is **not sufficient** for any change that wires new state writes onto a path the daemon only exercises during a narrow daily window. There is no CI gate, no merge-time check, no operator-facing checklist that surfaces this risk class.

This is a **process silent failure**: the team shipped a "fix" that demonstrably hadn't run on the live path, and no automatic step flagged the gap. The operator only saw the issue when they opened the dashboard hours after the merge and saw `---` everywhere — a manual, after-the-fact catch.

**Scope of user-facing impact:** CR / MDD columns and the portfolio CR/MDD strip render `---` everywhere from merge time (22:01 ET 2026-05-14) until the first market-hours intraday cycle (10:30 ET 2026-05-15 at earliest, per the current `EXECUTION_START_TIME`). The columns will populate the moment the intraday loop runs once.

**Confidence:** **HIGH** for the placement-vs-execution mechanism (direct DB probe + grep-confirmed control flow). **HIGH** for the merge-after-close timeline (`git show -s --format="%H %ai"` for both commits).

---

## 3. Common-Cause Analysis

**These four incidents are NOT independent.** They share a single underlying anti-pattern and one compounding factor:

**Anti-pattern: the engine's per-cycle `main()` has six distinct return paths, three of which exit without writing state and without raising the log level above INFO/print:**

| Line | Path | Writes state? | Logs at ERROR / CRITICAL? | Operator-visible signal? |
|---|---|---|---|---|
| 287 | `Overlap Detected` | no | no | `print` only |
| 313 | `Market closed or in Grace Period` | no | no | `print` only — **fired in Incident #1** |
| 332 | `COMPOSER REBALANCE BLACKOUT` | no (already wrote in line 328 EOD snapshot) | no | `print` only |
| 337 | `CRITICAL: Missing API Keys` | no | no (the word `CRITICAL` is in the *string*, not the log level — it's a plain `print`) | `print` only |
| 401 | `EOD Post-Mortem already run` | no | no | `print` only |
| 432 | EOD complete | yes (line 416) | no | `print` only |
| 436 | empty `historical_data` (poisoned cache) | **no** | **no** | **none** — **bare `return`, fired in Incident #2** |

Every one of these is a candidate for the operator's "daemon alive, data frozen" experience. Incident #1 hit line 313, Incident #2 hit line 436. Incident #4's mechanism is different (state is written, but a specific *field* is missing on one branch) but the user-facing expression is identical: data the operator expects on the dashboard isn't there, and there is no error anywhere telling them why.

**Compounding factor: the dashboard has no engine-health signal.** `app.py:191` generates `data_as_of` via `datetime.now()` per request — it reflects when the *page loaded*, not when state was last written. `app.py:88-92` reports `next_run_seconds` from the schedule library — that tells you the *scheduler* is alive, not that any past cycle wrote state. There is no `last_successful_save_state_at` field in `bot_state`, no `engine_health` row in the DB, no `/api/health` endpoint, no Discord watchdog post-on-N-stale-cycles. The operator has to *log into* the daemon's stdout (which, per Incident #3, is currently not captured to any file) to see the print lines that *would* tell them.

**Together these mean:** any path that takes one of the silent or print-only returns above leaves the system in a state where (a) the daemon process is alive and consuming CPU every minute, (b) the dashboard renders fine and looks "active" (because `data_as_of` ticks forward and `next_run_seconds` decrements correctly), (c) the user-facing data is frozen, and (d) the only signal of the freeze is *the absence of expected progress*, which requires the operator to have known what to expect. That's a recipe for hours-long silent outages — which is exactly what has been observed.

**Incident #3 is a related-but-distinct gap** (no daemon-level singleton) that doesn't share the silent-return mechanism, but it *amplifies* incidents #1, #2, and #4: a second daemon writing to the same DB / cache / JSON files between subprocess invocations can poison state, double-fire Discord alerts on liquidation, or partially-overwrite the `history_cache.json` while another cycle is mid-read. The lock-row protects the engine math but not the supporting file writes.

**Honest qualifier:** the *triggers* for each silent return are independent (a config typo for #1, an Alpaca 401 for #2, a placement decision for #4, a process-supervision gap for #3). What unifies them is the *response*: silent state-less exits + dashboard with no health signal. If you fix the response, all four classes become loud-fail and operator-detectable in seconds instead of hours.

---

## 4. Silent-Failure Surface Map (Every Short-Circuit in `alpha_bot_execution.py`)

The full set, enumerated by line number. "Acceptable" means "this path is supposed to early-return and the operator can plausibly notice via the print". "Silent" means "no print, no DB write, no signal". "Print-only" means "prints to stdout but if stdout isn't captured (Incident #3-class daemon launch) the print is invisible, and there is no state-or-DB signal."

### main() returns

| Line | Code | Class | What it skips |
|---|---|---|---|
| 287 | `return` after `acquire_lock()` returns False | Print-only | Everything past line 288 |
| 313 | `return` after market-closed gate | Print-only | `save_state` for date rollover, intraday loop, EOD branch |
| 332 | `return` after rebalance blackout (only when `not force_run`) | Print-only | Intraday loop and EOD branch; the EOD snapshot at line 328 *did* run before this return |
| 337 | `return` on missing API keys (`COMPOSER_KEY_ID`/`ALPACA_KEY`) | Print-only — note: "CRITICAL" is in the string, not the log level | Everything past line 337 |
| 401 | `return` on `post_mortem_run == current_date_str` already-ran guard | Print-only | EOD post-mortem rerun (intended) but ALSO blocks any subsequent intraday cycle for the rest of the day (because line 397 condition is "we are in EOD window"; this `return` exits without checking whether we should fall through to intraday — moot in practice because the EOD window is 16:00-16:05 ET, but worth noting) |
| 432 | `return` at end of EOD post-mortem branch (intended) | Print-only | (intended end of cycle) |
| 436 | **`return` on empty `historical_data`** | **Silent** — no print, no log, no state write | Intraday evaluation, all symphony writes, chart history append, execution queue, `save_state` (line 883) |

### Inner / helper-function silent paths

| File:Line | Code | Class | What it hides |
|---|---|---|---|
| `alpha_bot_execution.py:92` | `return []` after `Error parsing Composer response JSON` (HTTP 200 + bad body) | Print-only | Account's symphonies silently treated as empty — flows into `all_tickers` building empty (= contributes to Incident #2's poisoned-cache trigger condition) |
| `alpha_bot_execution.py:96` | `return []` on `requests.RequestException` (broad network failure) | Print-only | Same as above |
| `alpha_bot_execution.py:96` | `return []` on Composer HTTP non-200 (e.g. 401, 403, 429, 5xx) | Print-only (line 93 prints "Error fetching account ... HTTP N") | Same — account is silently dropped from `symphony_data_cache` and any holdings missed |
| `alpha_bot_execution.py:134` | `return False` on Composer rejection (HTTP 4xx not-429) in `execute_sell_to_cash` | Print-only (line 132 prints "COMPOSER REJECTED: HTTP N") | The caller at line 838 treats `success=False` as "skip state update" (line 881 prints `EXECUTION FAILED`) — this one IS handled correctly downstream, but the failure code is `False`, not raised. No Discord alert on persistent rejection class. |
| `alpha_bot_execution.py:142` | `return False` after exhausted retries on `requests.RequestException` | Print-only | Same as above |
| `alpha_bot_execution.py:157` | `return cache.get("data", {})` — could be `{}` if cache is poisoned | **Silent** in the failure mode | Drives Incident #2's line-436 silent return |
| `alpha_bot_execution.py:191-193` | `break` out of paginated batch on `if not success` (3 retries failed) | Print-only (line 192 prints "Failed to download batch after multiple retries") | `historical_data` may be partially populated (some batches succeeded, some failed); the cache then writes a partial fetch as if it were complete (no marker that some batches failed) |
| `alpha_bot_execution.py:199` | `break` on `json.JSONDecodeError` in Alpaca response | Print-only | Same — `historical_data` may be partial |
| `alpha_bot_execution.py:220` | `break` on `not page_token` (intended pagination terminator) | (intended) | — |
| `alpha_bot_execution.py:226-227` | `try: json.dump` cache write with `except OSError: print(...)` | Print-only | Cache silently not updated if write fails (disk full, permission); next cycle re-fetches — non-catastrophic |
| `alpha_bot_execution.py:235` | `return {}` from `fetch_intraday_vwaps` when `tickers` is empty | Silent (intended on empty input, but combined with other failures becomes problematic) | If a triggered-symphony's ticker fails to appear in `all_snapshotted_tickers`, the VWAP map is empty for that ticker and the trailing-stop math operates on stale prices |
| `alpha_bot_execution.py:255` | `continue` when `bars` is falsy for a symbol | Silent | A symbol with no bars today is silently dropped — no log line — downstream VWAP calc proceeds without it |
| `alpha_bot_execution.py:264` | `except (...) as e: print(...)` — broad catch around batch fetch | Print-only — actually catches `requests.RequestException`, `ValueError`, `KeyError`, `TypeError` | A `KeyError` from `data["bars"]` (Alpaca schema drift) silently drops the entire batch; downstream VWAP computations proceed with missing tickers |
| `alpha_bot_execution.py:278` | `return utc_now - timedelta(hours=5)` from `get_current_et` ZoneInfo-unavailable fallback | Silent | A host with no `zoneinfo` data falls back to a naive UTC-5 shift; during DST mismatch this is off by 1 hour and the market-open gate at line 310 fires at the wrong wall-clock moment — would manifest exactly as Incident #1 with a different trigger |
| `alpha_bot_execution.py:802-833` | `try: execute_sell_to_cash` / `except` chain in the live-execution path | Print-only | Each broker-call failure prints `EXECUTOR EXCEPTION ...` but there is no Discord alert, no health-flag set, no auto-disable of further liquidations. A repeated failure mode would keep retrying and failing silently from the dashboard's perspective. (The narrow exception list is good practice; the lack of an escalation channel is the gap.) |

### app.py scheduler / dashboard surfaces

| File:Line | Code | Class | What it hides |
|---|---|---|---|
| `app.py:60-62` | `subprocess.run(cmd, check=True)` with `except subprocess.CalledProcessError as e: print(...)` | Print-only | If the engine subprocess exits with non-zero, the daemon prints to its (possibly uncaptured) stdout and the next-minute tick fires fresh. No tracking of *consecutive* engine-subprocess failures; no dashboard signal. |
| `app.py:64-65` | `threading.Thread(target=trigger_alpha_bot, daemon=True).start()` | Silent on exception | The thread itself catches via the try/except inside `trigger_alpha_bot` (line 54-62), but if the thread fails to start at all (resource exhaustion), there is no signal — the scheduler simply doesn't run that minute. |
| `app.py:67-71` | `while True: schedule.run_pending(); time.sleep(1)` | Silent on internal exception | If `schedule.run_pending()` raises (it shouldn't, but theoretically), the `while True` loop dies and no further cycles fire. There is no try/except, no restart, no signal. The scheduler thread is a `daemon=True` thread (line 626), so it dies with the process — but if *only the scheduler thread* dies and Flask keeps serving, the dashboard appears healthy but no cycles run. **This is the most dangerous silent-fail in `app.py`.** |
| `app.py:191` | `data_as_of = datetime.now().strftime("%H:%M ET")` | Not a failure mode per se — but it is a **labelling lie**: this field is the *page-render time*, not when state was last refreshed. It is the operator-facing source of "looks fresh when it isn't." |
| `app.py:88-92` | `next_run_seconds` computed from `schedule.get_jobs()` | Reflects scheduler-thread liveness only; says nothing about whether the last cycle did anything useful |
| `app.py:205-206` | `except Exception as e: return jsonify({"status": "error", "message": str(e)})` — broad catch on `/api/state` | Captures *and surfaces* the error to the dashboard — actually good. But also broad-`except`; doesn't distinguish DB lock contention from a real bug. Low-severity. |
| `restart.ps1:42-45` | `Start-Process python.exe -ArgumentList "app.py" -WindowStyle Hidden` with no `-RedirectStandardOutput` | Silent on stdout | **Every print in the engine becomes invisible.** This is why no current daemon log file exists for PID 84536 — its stdout goes to a hidden window's buffer that no one reads. The print-only failure classes above effectively become *fully silent* under this launcher. |

### database.py write paths

| File:Line | Code | Class | What it hides |
|---|---|---|---|
| `database.py:127-132` | `save_state` — no exception handling | Loud (uncaught exception would propagate up the engine's `finally` block, releasing the lock, then the subprocess exits non-zero, then `subprocess.run(check=True)` in `app.py:60` raises `CalledProcessError` → caught and printed at line 61) | If a `sqlite3.OperationalError: database is locked` ever escapes (10-second timeout exhausted), the path *would* be loud — but only if stdout is captured. |
| `database.py:264-267` | `log_symphony_event` — `except Exception as e: print(...)` broad catch on JSON-file write | Print-only | Silent loss of symphony event logs on disk-full / permission error; downstream timeline reconstruction loses entries |
| `database.py:108-116` | `release_lock` — no exception handling | Loud (would propagate) | Acceptable. |

---

## 5. Recommendations (No Code Written)

### 5a. Make every silent / print-only return loud

For each row in §4's table, the response pattern is the same:

1. **Promote the print to `logging.error(...)` or `logging.critical(...)`** on a named module logger (`alpha_bot_engine`) with a stream handler that writes to a **fixed file path** (e.g. `logs/engine.log`, rotated by `RotatingFileHandler`). The current `print()` model is incompatible with `restart.ps1`'s hidden-window launcher.
2. **Set a `bot_state["engine_health"]` flag** every cycle: `{"last_attempted_at": <epoch>, "last_successful_at": <epoch>, "last_failure_reason": "<string|None>", "consecutive_failures": <int>}`. Update at *every* return path, including the silent ones. Then any short-circuit becomes observable via DB read.
3. **Surface that flag on the dashboard.** A red banner across the top of `templates/index.html` when `last_successful_at` is older than 90 seconds during market hours (or when `consecutive_failures > 3`). This is the single biggest operator-UX gap and the cheapest fix.
4. **Post to Discord on N consecutive identical-reason failures** (e.g., 5 consecutive `historical_data_empty` returns triggers one Discord alert; subsequent identical reasons in the same hour are rate-limited).

Specific call-outs:

- **Line 313 (market-closed return):** add `engine_health.last_failure_reason = "before_market_open"` so the dashboard can distinguish "outside market hours, expected" from "market hours but engine isn't running, unexpected."
- **Line 436 (empty `historical_data`):** this is the most dangerous one. Promote to `logging.error("[ENGINE] historical_data empty; cache may be poisoned. cache_date=<X> cache_tickers=<N>")`. Set `engine_health.last_failure_reason = "historical_data_empty"`. Trigger a Discord alert. The cycle should still return (no recovery is possible without an external trigger), but it should *never* return silently.
- **Line 337 (missing API keys):** this is genuinely critical. Should be `logging.critical(...)` + Discord + dashboard hard-stop banner. The string `"CRITICAL:"` in the print is decorative, not a log level. The actual log level is `print` = no level.

### 5b. Add a `last_successful_cycle_at` timestamp to `/api/state`

Replace (or augment) `data_as_of` (`app.py:191`) with a field read from `bot_state["engine_health"]["last_successful_at"]`. Render it on the dashboard as e.g. `Engine: last cycle 10:07 ET (30 s ago)`. Color it red when stale during market hours, gray when outside market hours. This single change closes the gap that made Incident #1 invisible.

The contract `data_as_of` provides today (= `datetime.now()` at request time) is **misleading and should be removed or relabeled**. The current name implies "the data shown was as of this time" — false.

### 5c. Cache-write guard and content validation

In `fetch_alpaca_history` (`alpha_bot_execution.py:222-227`):

- **Add a precondition:** `if not historical_data: print(...); return historical_data` — do NOT write the cache when the dict is empty. Empty data does not deserve a daily cache slot.
- **Add content validation on cache read** (`alpha_bot_execution.py:151-159`): even if `date` and `tickers` match, treat `data == {}` (or `len(data) < some_threshold`) as a cache miss and re-fetch.
- **Add a cache schema version field.** Bumping it invalidates all stale caches on next deploy.

These are the three behavior changes that close the loop in Incident #2 without touching the engine's main control flow.

### 5d. Daemon-level singleton and stdout capture

- **Pidfile or `socket.bind` exclusivity on a fixed port at `app.py` startup**, before `init_db()` or scheduler-thread start. If the bind/lock fails, exit with `logging.critical` and a Discord alert. Refuses Incident #3's failure mode at the source.
- **Replace `restart.ps1`'s `-WindowStyle Hidden` with `-RedirectStandardOutput logs\daemon.log -RedirectStandardError logs\daemon.err`** (or equivalent). The current launcher discards every `print` in the engine, which is the *operational* reason why all the print-based signals above are effectively silent. The `print`s are necessary but not sufficient — they must land somewhere readable.
- **Heartbeat the scheduler thread.** Add `app.py`-side: a `last_scheduler_tick` timestamp updated at the top of every minute, exposed on `/api/state`. If the scheduler thread dies while Flask survives (the most dangerous `app.py` silent failure), the dashboard can detect it.

### 5e. Process model — daemon supervises its own scheduler

Right now `app.py` is the supervisor *and* the scheduler *and* the dashboard, all in one process. A scheduler-thread crash leaves Flask up and the engine dead — exactly the "looks alive, isn't" failure shape. Two viable architectures, in increasing weight:

1. **In-process watchdog.** A second daemon thread checks `last_scheduler_tick` every 30 seconds; if it's > 90 seconds stale during market hours, log critical + Discord + `os._exit(1)` to force the `restart.ps1`-class supervisor to spawn a fresh instance.
2. **External supervisor.** Move to `nssm` / Windows Service / `pm2` / a tiny PowerShell loop that watches the python process and restarts it on death. Adds operational complexity but fully removes the "Flask up, scheduler dead" case.

Option 1 is the cheaper, lower-risk first move.

### 5f. Test/CI gate — live-verification requirement for market-hours-only fixes

The `b9ab6f9` failure (Incident #4) was not preventable by any unit test, because the fixture exists and the fix passes it. What was missing is a **post-merge or pre-merge live-execution gate** for any change that adds a state write on a market-hours-only codepath. Concrete options:

1. **A `--force` + `--dry-run` invocation in CI** that runs `alpha_bot_execution.py --force` (per `app.py:56-57` and `alpha_bot_execution.py:294`), inspects the resulting `bot_state` for the new fields, and fails the merge if they're absent. Doesn't require live market hours because `--force` bypasses the gate.
2. **A pre-merge checklist item** rendered by the PM for any PR that touches `alpha_bot_execution.py:448-740` (the intraday loop): "this change writes new state fields — confirm via a `--force` run on a dev DB that the fields appear in `bot_state` after one cycle." This is process, not code, and depends on PM discipline.
3. **A unit test that mocks the full `main()` execution path** with a `force_run=True`-style harness, mocks `fetch_symphony_stats` from a fixture, runs `main()`, and asserts the post-run `bot_state` schema. Highest fidelity, highest cost.

(1) is the smallest credible step. If the next CR/MDD-class fix had run `alpha_bot_execution.py --force` against a captured-fixture API mock and a temp DB, then dumped `bot_state` and grepped for the four field names, the b9ab6f9 failure mode would have been caught at merge time.

### 5g. Specific configuration hygiene for `EXECUTION_START_TIME`

The `EXECUTION_START_TIME=10:30` setting found in `.env` today is itself a silent-failure surface — a typo there silently delays the engine. Recommendations (no code):

- **Validate the value at engine startup** against a known-good range (e.g. `09:30 <= start_time <= 09:45`). On out-of-range, `logging.critical` + Discord + refuse to start. Or, at minimum, **always log the resolved `market_open` value at INFO at the top of every cycle** so the operator sees what the engine thinks the open is.
- **Display the resolved `market_open` on the dashboard** alongside the engine-health timestamp. If the dashboard shows `Market open: 10:30 ET`, the operator immediately spots the misconfiguration.

---

## 6. Open Questions / What I Couldn't Determine Read-Only

1. **Whether today's `EXECUTION_START_TIME=10:30` is a deliberate operator setting or a leftover/typo.** The `.env` file mtime is `2026-05-15 07:56:28 local` (= 09:56 ET, ~30 minutes before market open) — someone or something edited it this morning. Read-only, I can't tell who or why. Evidence that would resolve: `.env` file history (git, but `.env` is gitignored per project rules); the operator's recollection of whether they edited the value.

2. **Whether the three concurrent daemons observed earlier were caused by `restart.ps1` failing to kill, by manual double-launch, or by an OS service.** The brief gives PIDs but no capture of process tree / parent PIDs / launch context. Evidence that would resolve: a saved `Get-CimInstance Win32_Process` snapshot at the time of the 3-way state, including `ParentProcessId` and `CommandLine` for each, plus Windows Event Viewer entries for `python.exe` start/stop in the relevant window.

3. **Where the current daemon (PID 84536) is logging stdout.** Empirically: nowhere I could find. `restart.ps1` launches with `-WindowStyle Hidden` and no redirect. The two existing log files (`alphabot_daemon.log`, `alphabot_daemon3.log` in `%TEMP%`) are stale from yesterday and earlier — neither was launched by `restart.ps1`. Evidence that would resolve: `Get-CimInstance Win32_Process` with handle inspection on PID 84536, or `handle.exe` from Sysinternals to see file descriptors of the running daemon. **Recommendation independent of this RCA:** capture stdout *unconditionally* in `restart.ps1`.

4. **Whether the daemon's `get_current_et` is using `zoneinfo` or the naive UTC-shift fallback** (`alpha_bot_execution.py:270-278`). On this host, a direct test (this session) succeeded with `zoneinfo`. But under `python.exe app.py` launched from `restart.ps1` with a Hidden window, environment differences could cause `tzdata` to be missing. There is a runbook for this (`docs/runbooks/tzdata-missing-on-host.md`) noting the issue is known. If today's daemon hit the fallback path, all market-open math is off by up to an hour. Evidence that would resolve: an INFO log line at the top of every cycle showing `current_et` raw value with timezone info — currently absent.

5. **Exact mechanism of the operator's "PID 23528 from 2026-05-14 20:02" observation.** That daemon is no longer running; a new daemon (PID 84536) is in its place, started this morning at 08:00 local. Whether the operator's restart happened between observation and now, or whether PID 23528 died on its own and was respawned (no auto-respawn exists in the code, so this would require external intervention) is undetermined. The class of failure is, however, fully characterized in Incident #1 — whichever daemon was running this morning before 10:30 ET would have hit the same silent return.

6. **Whether the `restart.ps1` HTTP health check ever caught a broken daemon.** The script's `Invoke-WebRequest` only checks `GET /` returns 200 (`restart.ps1:64-68`). It does NOT check whether the engine is cycling, only that Flask is responsive. A daemon with a dead scheduler thread + alive Flask would pass this check trivially. Evidence that would resolve: nothing — the check as written cannot detect the failure class. Recommendation: change the health check to hit a `/api/health` endpoint that asserts `engine_health.last_successful_at > now() - 90s` during market hours.

7. **Whether any of the silent returns at lines 92, 96, 255, 264 have fired in production this week.** No engine-error log exists, no `symphony_logs.json` entry corresponds to these paths (they don't have a `symphony_id` to attach to). Evidence that would resolve: a structured logger with file output (per §5a recommendation) running for a week.

8. **Whether the 11-hour PID 23528 daemon lifetime spans an OS sleep/resume event.** If the host slept overnight (08:00 PM to 06:00 AM local, e.g.), the Python `schedule` library does NOT compensate for missed ticks — it simply resumes scheduling from wake. Combined with `EXECUTION_START_TIME` gating, this could produce an extra silent window post-wake. Read-only, I cannot determine whether sleep/resume occurred. Evidence that would resolve: Windows Event Viewer entries for system sleep/resume in the disputed timeframe.

---

## 7. Files & Evidence Referenced

- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py` — `main()` start (284), market-closed return (310-313), rebalance blackout (316-332), missing-key return (335-337), `bot_state["date"]` advance (357-365), `historical_data` empty return (434-436), intraday loop (448-740), persist call (689), `_persist_composer_fields_to_bot_state` (98-103), `fetch_symphony_stats` (82-96), `fetch_alpaca_history` (146-229), cache hit (155-157), cache write (222-227), `execute_sell_to_cash` (106-143), `get_current_et` (270-278), `EXECUTION_START_TIME` env-read (43)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\app.py` — `trigger_alpha_bot` subprocess spawn (52-62), `run_scheduler` (67-71), `/api/state` route (78-206), `data_as_of` (191), `next_run_seconds` (88-92), broad `except` (205-206), HTTP daemon launch (619-631)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\database.py` — `acquire_lock` (94-106), `release_lock` (108-116), `save_state` (127-132), `log_symphony_event` (245-267), `init_db` (30-91)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\restart.ps1` — process filter (24-27), kill loop (29-38), launch with hidden window (42-45), HTTP health check (64-69)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\synthetic_history.py` — confirmed NOT the daemon's `history_cache.json` writer (that path is `alpha_bot_execution.py:222-227`); `synthetic_history.py` writes `cache/synthetic_history_v2_*.json` for the autotuner only
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.env` — `EXECUTION_START_TIME=10:30` (verified via `dotenv_values`); Composer/Alpaca/account keys all present, non-empty
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alphabot_state.db` — `bot_state.date = 2026-05-14`, `bot_state.post_mortem_run = 2026-05-14`, `chart_history.date = 2026-05-14`, 11 symphonies each with 30 keys (none = `simple_return` / `net_deposits` / `time_weighted_return` / `max_drawdown`), `execution_lock = (0, 1778854021.06...)` → ET 10:07:01 today; DB mtime `2026-05-15 08:04:00 local` (= ET 10:04)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\history_cache.json` — date `2026-05-14`, 35 tickers, 772 date-keys; mtime `2026-05-14 12:42:07 local` (yesterday — proves `fetch_alpaca_history` has not been reached today)
- `C:\Users\paulm\AppData\Local\Temp\alphabot_daemon.log` — stale, mtime `2026-05-14 10:02` (previous daemon)
- `C:\Users\paulm\AppData\Local\Temp\alphabot_daemon3.log` — stale, mtime `2026-05-14 14:39` (previous daemon, ran full intraday loop yesterday with chart points up to 15:52 ET, transitioned to rebalance blackout at 15:53 ET, completed EOD post-mortem at 16:00-16:05 ET, then idle "Market closed or in Grace Period" lines)
- Running daemon process: PID 84536, `python.exe app.py`, started `2026-05-15 08:00:09 local` (= ET 10:00); no captured stdout log file located
- Git: `cf6a0e2` (engine persist GREEN) committed `2026-05-14 19:31:28 local`; `b9ab6f9` (merge) committed `2026-05-14 20:01:44 local`; `alpha_bot_execution.py` file mtime matches merge time
- Prior diagnoses cross-referenced: `docs/research/dashboard/state-not-updating-diagnosis.md`, `docs/research/dashboard/actual-return-diagnosis.md`, `docs/research/dashboard/cr-mdd-zero-diagnosis.md`
- Project rule references: `.claude/CLAUDE.md` (Architecture Constraints — engine 1-min cadence, dashboard read-only, two-DB pattern), `~/.claude/CLAUDE.md` (Universal Hard Rules — no destructive ops without operator approval)
