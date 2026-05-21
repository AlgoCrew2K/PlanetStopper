# Diagnosis: Dashboard CR / MDD Reading 0.00% on Every Symphony and Portfolio

**Date:** 2026-05-14
**Analyst:** composer-alpaca-integration
**Type:** Read-only diagnosis. No production code written or changed.
**Working repo:** `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM` (main, real `.env`)

---

## Root Cause (one line)

**`/api/state` builds the `symphonies_list` it feeds the M1 CR/MDD helpers entirely from `bot_state`, which never contains the Composer fields (`simple_return`, `net_deposits`, `time_weighted_return`, `max_drawdown`) — so `app.py:153-166` substitutes a hardcoded `0.0` for every one of them, and the helpers faithfully return `0.00%`.**

This is **option (c) combined with option (b)** from the investigation menu: the Composer fields are genuinely absent from the input the helpers receive, *because* the `/api/state` wiring constructs that input from the wrong data source (`bot_state` instead of a Composer `symphony-stats-meta` fetch). The helpers themselves are correct. The fetch path is correct. The wiring in between is the defect.

It is a **real bug, not a market-closed artifact.** Composer serves `simple_return` and `max_drawdown` for every symphony 24/7 (confirmed in `composer-per-symphony-stats.md` — empirically pulled non-zero values for all 11 symphonies). The dashboard shows `0.00%` because nothing ever asks Composer for those numbers on the dashboard path.

---

## Evidence

### 1. `bot_state` does not contain the Composer CR/MDD fields

Inspected the live `alphabot_state.db` `bot_state` blob (single-row JSON, 14 top-level keys). A representative symphony dict (`n2ooAZTvBRN6ZzpMmWmU`) has these 29 fields:

```
above_tp_count, account, active_stop_distance, armed, below_stop_count,
breakeven_locked, current_holdings, current_return, current_value,
high_water_mark, hwm_hold_ticks, mc_history, mc_prob, name, para_armed,
prev_return, shadow_hwm, stop_trigger, symphony_vol, tp_armed,
trigger_prices, triggered, triggered_at_hwm, triggered_at_return,
triggered_at_stop, triggered_at_time, triggered_basket_snapshot,
triggered_reason, vwap_bleed_ticks, vwap_ticks
```

Direct field probe on that symphony:
```
current_return        = -1.92      (present, live)
current_value         = 1612.51    (present, live)
simple_return         = None       (ABSENT)
max_drawdown          = None       (ABSENT)
net_deposits          = None       (ABSENT)
time_weighted_return  = None       (ABSENT)
```

A `grep` for `simple_return | net_deposits | time_weighted_return | max_drawdown` across `alpha_bot_execution.py` and `database.py` returned **zero matches** — the engine never writes these fields into `bot_state`. `bot_state` is a single-session live-tracking structure; it has never carried Composer cumulative/inception metrics. This is consistent with `table-audit.md` §4(a): *"Cumulative Return (CR) and Max Drawdown (MDD) — NOT in `bot_state`."*

### 2. `/api/state` constructs the helper input from `bot_state`, zero-filling the missing fields

`app.py:150-166` — the comment is explicit about what it is doing:

```python
# Build symphonies list for M1 analytics helpers from bot_state.
# Fields derived: last_percent_change from current_return/100, value from current_value.
# CR/MDD fallback to 0 when not stored in bot_state — helpers still need the fields.
symphonies_list = []
for k in symphony_keys:
    s = state_data[k]
    cr = s.get("current_return") or 0.0
    val = s.get("current_value") or 0.0
    symphonies_list.append({
        "id": k,
        "value": val,
        "last_percent_change": cr / 100.0,
        "simple_return": s.get("simple_return", 0.0),          # -> 0.0, always
        "net_deposits": s.get("net_deposits", 0.0),            # -> 0.0, always
        "time_weighted_return": s.get("time_weighted_return", 0.0),  # -> 0.0, always
        "max_drawdown": s.get("max_drawdown", 0.0),            # -> 0.0, always
    })
```

Because §1 proves `bot_state` never has those four keys, `s.get(..., 0.0)` returns `0.0` for **every symphony, every request**. The route never calls `fetch_symphony_stats` (the only occurrence of that function on the dashboard side is in `app.py`'s liquidation path, `perform_account_liquidation`, not in `/api/state`).

> Note: the task brief states `/api/state` "passes them the fetched Composer symphony list." That premise is **incorrect** — verified by reading the route. `/api/state` passes a `bot_state`-derived list with the Composer fields stubbed to `0.0`. This is the crux of the bug.

### 3. The M1 helpers then faithfully compute 0.00%

`analytics.py` — given the zeroed input:

- `get_symphony_cumulative_return` (`analytics.py:418-433`):
  ```python
  simple_return = float(sym_dict["simple_return"])   # = 0.0
  net_deposits  = float(sym_dict["net_deposits"])    # = 0.0
  if simple_return == 0.0 and net_deposits == 0.0:
      if_held = float(sym_dict["time_weighted_return"])   # = 0.0  <-- TWR fallback fires...
  else:
      if_held = simple_return
  return {"if_held": if_held, "dry_run": if_held}    # {0.0, 0.0}
  ```
  **The documented TWR-fallback path DOES fire** (because `simple_return == 0.0 AND net_deposits == 0.0` is trivially true for the stubbed input) — but it falls back to `time_weighted_return`, which is **also `0.0`** in the stub. So the fallback is not "misfiring" in a logic sense; it is doing exactly what it was written to do, but its escape hatch (`time_weighted_return`) is just as zeroed as the primary field. This is investigation option **(d) ruled IN as a contributing mechanic, but not the originating cause** — the fallback cannot save a fully-zeroed input.

- `get_symphony_max_drawdown` (`analytics.py:436-444`): `if_held = float(sym_dict["max_drawdown"])` = `0.0`. No fallback. Returns `{0.0, 0.0}`.

- `dry_run` side: both helpers hardcode `dry_run = if_held` (docstrings: *"bot_state does not store CR / MDD; always equals if_held"*). So dry_run is `0.0` not because the shadow has no history (option (e)), but because it is **defined** to mirror `if_held` — and `if_held` is `0.0`. Option (e) is **ruled OUT** as the mechanism; the dry_run side has no independent computation to fail.

- Portfolio: `_value_weighted_portfolio` (`analytics.py:447-486`) value-weights the per-symphony results. Weighting `0.0` by any set of positive `value`s yields `0.0`. `get_portfolio_cumulative_return` / `get_portfolio_max_drawdown` therefore also return `{0.0, 0.0}`. This is why **the portfolio strip reads 0.00% too** — same root cause, propagated by aggregation. (Note: `value` weights ARE populated — `current_value` is in `bot_state` — so the portfolio is not hitting the `total_weight == 0.0 -> {0.0, 0.0}` empty-guard; it is averaging genuine zeros.)

### 4. Input list is NOT empty or malformed

Investigation option (a) ruled **OUT**: `bot_state` currently has 11 symphony dicts with live `current_return` / `current_value`, so `symphonies_list` is well-formed and non-empty. `try/except (KeyError, TypeError, ValueError)` guards at `app.py:173-183` are also not the cause — the helpers do not raise on the stubbed input (every key the helper reads is present, just `0.0`); they return `0.00%` cleanly. The `except -> {0.0, 0.0}` fallback is a red herring here; the `try` block succeeds and itself produces `{0.0, 0.0}`.

### 5. Engine cycle / cache health — NOT in play for this symptom

The task brief asked whether the known poisoned-cache bug is short-circuiting cycles. For symptom 2 (CR/MDD = 0), it is **not relevant**, and current state is healthy regardless:

- `history_cache.json`: **2,728,917 bytes**, valid JSON, `date = 2026-05-14` (today), **35 tickers, 772 date-keys**. This is a healthy, fully-populated cache — **not poisoned.** (It was rebuilt by the manual diagnostic cycle described in `state-not-updating-diagnosis.md` and has not been re-poisoned since.)
- `alphabot_state.db` mtime: **2026-05-14 19:01** — fresh, after the ~18:57 restart. State IS being written.
- `bot_state` `current_return` values (e.g. `n2ooAZTvBRN6ZzpMmWmU = -1.92`) differ from the `chart_history` 10:00 snapshot (`-0.15`) and the 11:56 point (`-0.26`) — confirming the engine advanced state past the stale chart points. A full cycle HAS completed since the restart.
- `execution_lock`: `is_locked = 0`, timestamp `1778806861` — not stuck.
- Caveat: `/tmp/alphabot_daemon.log` is **stale** (last line `[10:02:00]`, owned by the *previous* daemon PID 59420). The newly restarted daemon (~18:57) is evidently logging elsewhere or to a fresh console not captured at that path. **Undetermined:** I could not locate the current daemon's live log output. But the DB mtime + advanced `bot_state` values are sufficient independent proof that the post-restart daemon is completing cycles and writing state. The CR/MDD bug would persist even with a perfectly healthy engine — it is a dashboard-wiring defect, fully independent of the execution path.

### 6. Composer genuinely has the data (cross-reference)

`composer-per-symphony-stats.md` (2026-05-14, empirical read-only GET against the live account) confirms `symphony-stats-meta` returns populated `simple_return` (e.g. `0.6633`), `time_weighted_return` (e.g. `3.13443`), and `max_drawdown` (e.g. `0.2552`) for every symphony. So the data the dashboard *should* be showing exists and is one `fetch_symphony_stats` call away. The dashboard simply never makes that call on the `/api/state` path.

---

## Are the two symptoms linked?

**Independent root causes. Not linked.** They share a family resemblance (both are "the dashboard shows nothing / zero after a restart") but the mechanisms are distinct:

| | Symptom 1 — empty symphony table after restart | Symptom 2 — CR/MDD = 0.00% everywhere |
|---|---|---|
| **Mechanism** | `database.load_state()` returned empty/no symphony dicts immediately post-restart (state DB not yet (re)populated, or the poisoned-cache early-return from `state-not-updating-diagnosis.md` had left `bot_state` stale/thin). A clean restart + healthy cache let a full cycle write symphonies back. | `/api/state` builds the M1-helper input from `bot_state`, which structurally never contains the Composer CR/MDD fields; `app.py:153-166` zero-fills them. |
| **Depends on engine cycle completing?** | YES — symptom 1 resolves once the engine writes `bot_state`. It is transient. | NO — symptom 2 persists forever, on every cycle, healthy or not. Even a perfectly running engine writing fresh `bot_state` every minute will still show `0.00%` CR/MDD, because `bot_state` is the wrong source. It is permanent until the wiring is fixed. |
| **Fix surface** | Engine / cache hardening (the contributing bugs in `state-not-updating-diagnosis.md`: don't cache failed Alpaca fetches, make the empty-`historical_data` exit loud, treat `data == {}` as a cache miss). | Dashboard data-wiring (`app.py` `/api/state` + possibly `analytics.py`). |

The ONLY shared thread is the operator observing both right after a restart — symptom 1 was genuinely restart-transient and self-healed; symptom 2 was *always* there and just became the operator's focus once the table repopulated. They should be fixed by separate work items.

That said — symptom 1's underlying mechanism (the poisoned-cache silent early-return documented in `state-not-updating-diagnosis.md`, plus three contending daemon processes found by `restart.ps1`) is a **real, still-unfixed reliability bug** and should not be dismissed just because a clean restart papered over it this time. It is simply not the *cause* of the CR/MDD zeros.

---

## What a fix would need to touch (NOT implemented)

The CR/MDD helpers in `analytics.py` are correct and should not change. The fix is to **feed them real Composer data**. Two viable approaches — this is a design fork for the PM/operator, not a mechanical fix:

### Approach A — dashboard fetches `symphony-stats-meta` on the `/api/state` path
- **`app.py` `/api/state`:** call `fetch_symphony_stats(account_id)` (or a read-only equivalent) for each configured account, and build `symphonies_list` from the *Composer* response (which has real `simple_return` / `net_deposits` / `time_weighted_return` / `max_drawdown`) instead of from `bot_state`. The `id` join key between the Composer symphony objects and the `bot_state` entries must be verified — `bot_state` keys are symphony IDs; confirm the Composer object's id field name (`symphony_id` vs `id` — note `app.py:458` already handles both for liquidation).
- **Constraint conflict:** project CLAUDE.md hard rule — *"Engine runs 1-minute cadence... no blocking I/O on the execution path"* and *"Dashboard is a read-only operator surface."* A Composer GET on `/api/state` is read-only (allowed) but `/api/state` is a hot poll route and `fetch_symphony_stats` has a built-in `time.sleep(1.5)` per call plus a 15s timeout — 3 accounts = ~4.5s+ added latency per dashboard poll. That is a real UX regression and risks the dashboard poll cadence. Mitigation: a separate, less-frequently-polled route (mirrors the `actual-return-diagnosis.md` and `table-audit.md` §4 recommendation of a decoupled `/api/state/metrics` route), or a short TTL cache on the Composer response.
- **Fixture/testing:** per project rule *"API calls must be testable from a fixture"* — needs a captured `symphony-stats-meta` fixture and a parser test. `composer-per-symphony-stats.md` already documents the field schema; a fixture capture via `/api-fixture` is the clean provenance path.

### Approach B — engine persists the Composer fields into `bot_state`, dashboard keeps reading `bot_state`
- **`alpha_bot_execution.py`:** `fetch_symphony_stats` already returns the full Composer symphony objects every cycle. The engine could carry `simple_return`, `net_deposits`, `time_weighted_return`, `max_drawdown` through into the per-symphony `bot_state` dict it writes (the write site is the `bot_state` assembly around `alpha_bot_execution.py:440-740`).
- **`app.py` `/api/state`:** then `s.get("simple_return", 0.0)` etc. at `app.py:162-166` would pick up real values with **no change to the route logic** — the existing wiring becomes correct once the source has the fields.
- **Trade-off:** keeps the dashboard zero-I/O (best fit for the no-blocking-I/O and read-only-dashboard constraints), but adds fields to the `bot_state` schema and makes the dashboard's CR/MDD only as fresh as the last engine cycle (acceptable — matches how `current_return` already works, per `actual-return-diagnosis.md`). It also means CR/MDD inherit the engine's availability — if the engine is stuck (symptom-1-class bug), CR/MDD go stale too. Arguably fine since they are inception-to-date figures that barely move intraday.

### Either approach must also address the stale-vs-zero ambiguity
Whichever source is chosen: when the Composer fields are genuinely unavailable (auth failure, Composer down, symphony not yet fetched), the helpers/route should surface a `—` / "unavailable" placeholder rather than `0.00%`. Right now a real `0.00%` return is indistinguishable from "no data." This is the same gap `actual-return-diagnosis.md` flagged for staleness. The `app.py:173-183` `except -> {0.0, 0.0}` blocks and the `_value_weighted_portfolio` empty-guard `return {"if_held": 0.0, "dry_run": 0.0}` both currently *manufacture* a misleading `0.00%` on failure — they should return an explicit "no data" sentinel instead.

### Files in scope for a fix
- `app.py` — `/api/state` route, `symphonies_list` construction (`app.py:150-189`). Primary change site under Approach A; no change under Approach B (only the data source it reads from changes).
- `alpha_bot_execution.py` — `fetch_symphony_stats` (line 82) is already correct; under Approach B, the `bot_state` write assembly (~`440-740`) gains four carried-through fields.
- `analytics.py` — helpers (`418-501`) are correct; only touch them if adopting an explicit "no data" sentinel instead of `0.0` for the unavailable case.
- `database.py` — under Approach B, no destructive change; `bot_state` is a JSON blob so new fields are additive automatically (consistent with project "additive-first" schema rule).
- Test fixtures — a captured `symphony-stats-meta` fixture + parser test (project hard rule).

This is **new-codepath work** (a new fetch wiring or new persisted fields) — per project CLAUDE.md it warrants an Agent Teams Quad (test-writer + implementer + quant-code-reviewer + composer-alpaca-integration), gated through normal A/C, with the design fork (Approach A vs B) decided by the operator first.

---

## Items I could NOT determine definitively

1. **The current daemon's live log location.** `/tmp/alphabot_daemon.log` is stale (last line 10:02, previous PID). I could not find where the post-18:57 daemon writes its log. State-DB evidence independently confirms the daemon is cycling, so this does not change the diagnosis — but the operator should know the log path is unclear.
2. **Whether the post-18:57 daemon has Alpaca auth working.** Not testable read-only without watching a live cycle or making a live API call (which I will not do). The healthy 2.7 MB cache means even if Alpaca auth is currently broken, cycles will still complete from cache today. Irrelevant to the CR/MDD bug regardless — that bug is Composer-side data wiring.
3. **The exact `id` join-key field name** in the Composer `symphony-stats-meta` response objects (`symphony_id` vs `id`). `app.py:458` handles both defensively for liquidation; a fix under Approach A must confirm which the response actually uses. Not determinable without a captured fixture or a live call.
4. **Exact mechanism of symptom 1** (empty table) at the moment the operator saw it — whether it was the poisoned-cache early-return, the three-contending-daemons race, or simply `load_state()` racing a not-yet-written DB on a fresh restart. It self-healed, so I could not reproduce it. `state-not-updating-diagnosis.md` documents the poisoned-cache path as the most likely; the three-daemon contention found by `restart.ps1` is a plausible compounding factor. Either way it is independent of symptom 2.

---

## Files Referenced
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\app.py` — `/api/state` route (78-206); `symphonies_list` build with zero-fill (150-166); helper calls + try/except (169-189)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\analytics.py` — `get_symphony_cumulative_return` (418-433), `get_symphony_max_drawdown` (436-444), `_value_weighted_portfolio` (447-486), `get_portfolio_*` (489-501)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py` — `fetch_symphony_stats` (82-96)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\database.py` — no occurrences of the four Composer fields (grep-confirmed)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alphabot_state.db` — `bot_state` (29 fields per symphony, none being the Composer CR/MDD fields), `chart_history`, `execution_lock`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\history_cache.json` — healthy: 2.7 MB, 35 tickers, 772 date-keys, today's date
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\dashboard\composer-per-symphony-stats.md` — confirms Composer serves non-zero `simple_return` / `max_drawdown` per symphony
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\dashboard\table-audit.md` §4 — prior finding that CR/MDD are not in `bot_state`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\dashboard\state-not-updating-diagnosis.md` — the (separate) poisoned-cache reliability bug

---

## Follow-up: why CR/MDD still show `---` after b9ab6f9

**Date:** 2026-05-14 (evening, post-merge)
**Analyst:** composer-alpaca-integration
**Type:** Read-only diagnosis. No production code written or changed.

### One-line answer

**The b9ab6f9 fix is code-correct, but it has never executed on the live path. The engine code that calls `_persist_composer_fields_to_bot_state` lives *only* inside the intraday evaluation loop (`alpha_bot_execution.py:448-740`), and that loop has not run a single time since the fix was committed (19:31 ET) / merged (20:01 ET) — the market had already closed.** The 4 fields are therefore *absent entirely* from live `bot_state` (not `None`, not `0.0` — the keys were never written), so `app.py:162-165` reads `s.get("simple_return")` → `None`, and `analytics.py:430` / `:450` return the `None` sentinel → dashboard renders `---`.

This is **not** a fixture-vs-live data-shape mismatch. It is a **deployment-timing / control-flow-placement** problem: the persist was correctly written but placed on a codepath the live daemon only exercises during market hours, and it was merged after the close.

### The data path, traced end to end

**Step 1 — raw Composer call (`fetch_symphony_stats`, `alpha_bot_execution.py:82-96`).** Hits `GET /portfolio/accounts/{account_id}/symphony-stats-meta` (line 83), `timeout=15`. On HTTP 200 it returns `response.json().get("symphonies", [])` (line 89) — the **full, unmodified list of symphony objects**. It extracts no subset and drops no fields. **Suspect #2 (`fetch_symphony_stats` strips fields) is RULED OUT** — the prior diagnosis's "this function is correct" call was right. Each object in that list carries `id`, `simple_return`, `net_deposits`, `time_weighted_return`, `max_drawdown` (confirmed against the captured fixture `tests/fixtures/composer/symphony_stats_meta.json`: all 11 objects have all 4 fields plus `id`).

**Step 2 — `fetch_symphony_stats` output.** The returned list flows into `symphony_data_cache[account]` at `alpha_bot_execution.py:378-379` with the full objects intact. Verified: no transformation between the call and the cache assignment.

**Step 3 — the per-cycle fold into `bot_state`.** Two distinct consumers of `symphony_data_cache`:
  - **EOD post-mortem branch** (`alpha_bot_execution.py:404-432`, runs when `16:00 <= current_time <= 16:05` ET): iterates `symphony_data_cache`, writes `current_holdings` and `current_return` per symphony (lines 408-412), `save_state` at line 416, then `return` at 432. **It does NOT call `_persist_composer_fields_to_bot_state`.** This branch can write a fresh `current_return` while leaving the 4 Composer fields untouched.
  - **Intraday evaluation loop** (`alpha_bot_execution.py:448-740`, runs when `market_open <= current_time < 16:00` ET): iterates `symphony_data_cache`; for each `sym`, `symphony_id = sym["id"]` (line 450). At lines 681-688 it writes `name`, `account`, `current_return`, `mc_prob`, `stop_trigger`, `active_stop_distance`, `symphony_vol`, `current_value` — and then **line 689 calls `_persist_composer_fields_to_bot_state(bot_state, symphony_id, sym)`**. `save_state` for this path is at line 883.

  **The fix's recon claim — "`sym` already carries all 4 fields at line 673-680" — is CORRECT.** `sym` at line 689 *is* the raw Composer object straight from `fetch_symphony_stats`; it carries all 4 fields. And there is **no `continue` / `return` / `raise` / `break` / `except` anywhere between line 449 and line 692** (grep-confirmed) — so within the intraday loop, if line 683 runs, line 689 *must* run in the same iteration. The persist is unconditional on that path.

**Step 4 — is `_persist_composer_fields_to_bot_state` invoked on the live path?** It is invoked on the *intraday* path (line 689), with a join key (`symphony_id = sym["id"]`) that is **identity** — the same `sym["id"]` is used both to key `bot_state` and as the dict the helper reads. The join cannot silently miss; there is no separate Composer-id-vs-bot_state-id lookup. **Suspect #4 (join miss) is RULED OUT.** BUT: it is *not* invoked on the EOD post-mortem path, and *not* invoked on the market-closed early-return path (`alpha_bot_execution.py:310-313`).

**Step 5 — live `bot_state` inspection.** Inspected the running daemon's `alphabot_state.db` `bot_state` blob (single row, 11 symphony entries). Every symphony entry has **30 keys** — `current_return` and `current_value` are present and live (e.g. `n2ooAZTvBRN6ZzpMmWmU`: `current_return = -1.92`, `current_value = 1612.51`), but `simple_return` / `net_deposits` / `time_weighted_return` / `max_drawdown` are **KEY-ABSENT** (not `None`, not `0.0` — the keys do not exist on the dict). This is the smoking gun: had line 689 ever run for these symphonies, the keys would *exist* with whatever value `sym.get(...)` returned (even `0.0` or `None` from `.get()` would create the key). Their total absence proves **line 689 has never executed for the current `bot_state` generation.**

### Why line 689 has never run — the timeline

| Time (ET) | Event | Code that ran |
|---|---|---|
| up to 15:52 | last intraday evaluation loop of the day — proven by `chart_history` last point = `15:52` for all symphonies (the loop appends a chart point at `alpha_bot_execution.py:711-718` every cycle) | **pre-fix** `alpha_bot_execution.py` (line 689 did not exist yet) |
| 15:53-15:59 | rebalance blackout — `alpha_bot_execution.py:316` → `return` at 332 | no `bot_state` symphony write |
| 16:00-16:05 | EOD post-mortem — `alpha_bot_execution.py:397` branch → writes `current_return`/`current_holdings` only, `save_state` at 416, `return` at 432 | could be pre- or post-fix; **either way this branch never calls the persist** |
| 19:31 | `cf6a0e2` committed — `_persist_composer_fields_to_bot_state` + its call at line 689 first exist on disk | — |
| 20:01:44 | `b9ab6f9` merged to `main`; `alpha_bot_execution.py` on disk now has the fix (file mtime `20:01:44`) | — |
| 20:02:21 | daemon PID 23528 (`app.py`) started — **after** the merge, so every subprocess it spawns runs post-fix code | — |
| 20:06:01 | last `bot_state` write (DB mtime) | post-fix code, but **market closed** → `alpha_bot_execution.py:310` → `return` at 313. No symphony write at all. |

The intraday loop containing line 689 runs **only** during `09:30-16:00` ET. The fix landed at 20:01. **There has not been a market-hours minute since the fix was merged.** The current `bot_state` was last meaningfully written by the 16:00-16:05 post-mortem (post-fix code, but the persist-free branch) and/or the 15:52 intraday cycle (pre-fix code). Neither could populate the 4 fields.

### Captured fixture vs. live path — what each actually shows

- **The fixture (`tests/fixtures/composer/symphony_stats_meta.json`) is genuine and correctly shaped.** It is the raw `symphony-stats-meta` response; all 11 objects carry `id` + the 4 fields. The fix's tests passing against it is *valid* — the parser/persist logic is correct.
- **One caveat worth noting (not the cause of the `---`):** in the fixture, the first symphony has `simple_return: 0.0` and `net_deposits: 0.0` *literally* — so for that symphony the persist would write real `0.0`s, and `get_symphony_cumulative_return` (`analytics.py:434`) would take the `time_weighted_return` fallback (`3.13212`). That is the helper working as designed; it does **not** produce `---`. `---` comes only from a `None`/absent `simple_return`, which is the live-`bot_state` situation today.
- **The live path is not contradicting the fixture.** There is no data-shape drift between them. The live path simply hasn't *executed the persist yet*. The "fixture-vs-live mismatch" hypothesis in the task brief is **not supported by the evidence** — the mismatch is temporal (code merged after close), not structural.

### Was the prior diagnosis's scope boundary the mistake?

Partially, but not in the way the brief suspected. The prior diagnosis correctly declared `fetch_symphony_stats` "correct" — it *is* correct; it does not drop fields. The real scope gap was different: **Approach B placed the persist call inside the intraday evaluation loop only, and nobody verified it against the EOD/market-closed branches.** `_persist_composer_fields_to_bot_state` should arguably also run on the EOD post-mortem path (lines 404-412), and the fix's verification should have included "run a live market-hours cycle and re-inspect `bot_state`" — which was impossible to satisfy because the work was done and merged after 16:00 ET. The tests proved the *unit* was correct; nothing proved the *placement* covered every live path that writes `bot_state`.

### What a correct fix must touch (NOT implemented)

1. **Primary — verify, do not re-code, first.** The most likely outcome is that **no code change is needed at all**: when the market reopens (next trading day, 09:30 ET) and daemon PID 23528 spawns the first intraday `alpha_bot_execution.py` subprocess, line 689 will run, the 4 fields will be written to `bot_state`, `save_state` at line 883 will persist them, and the dashboard will render real CR/MDD. **The operator should re-inspect `bot_state` after the first post-09:30 cycle before any further code is written.** If the fields populate, the fix worked and this whole follow-up resolves to "merged after hours; wait for market open."

2. **If same-day / after-hours correctness is required** (i.e. CR/MDD should show real values even when the daemon is restarted after close, as it was today): the persist must also run on the paths that currently skip it —
   - **EOD post-mortem branch** (`alpha_bot_execution.py:404-412`): this branch already iterates `symphony_data_cache` with the full `sym` objects in scope and already writes `current_return` per symphony — add the `_persist_composer_fields_to_bot_state(bot_state, s_id, sym)` call here too (note it uses `s_id`, guarded by `if s_id in bot_state`).
   - **Market-closed early-return** (`alpha_bot_execution.py:310-313`): this path returns *before* `fetch_symphony_stats` is ever called, so there is no `sym` to persist. Making CR/MDD available here would require a separate lightweight "fetch stats and persist only" path — larger scope; likely out of bounds for a bug-fix and better served by the prior diagnosis's "Approach A" decoupled metrics route.

3. **Do NOT touch** `fetch_symphony_stats` (`alpha_bot_execution.py:82-96`), `_persist_composer_fields_to_bot_state` (`:98-103`), the `analytics.py` helpers (`:418-453`), or the `app.py` `/api/state` wiring (`:150-189`) — all four are correct as written. The defect is *exclusively* that the correct persist call exists on only one of the three `bot_state`-writing codepaths.

### Items I could NOT determine definitively (read-only)

1. **Whether the 15:52 intraday cycle ran pre- or post-fix code is moot** — `cf6a0e2` is timestamped 19:31, hours after 15:52, so the 15:52 cycle was unambiguously pre-fix. Stated as fact, not uncertainty.
2. **Whether the 16:00-16:05 post-mortem ran pre- or post-fix code** — indeterminate read-only (depends on exactly when the daemon was last restarted relative to 16:00 and whether the on-disk file had the fix then). It does not matter: the post-mortem branch does not call the persist under *either* version, so the outcome (4 fields absent) is identical.
3. **I did not make a live Composer API call.** The fixture + the prior `composer-per-symphony-stats.md` empirical pull are sufficient to confirm the raw endpoint carries the 4 fields; a fresh live call would add nothing and this is a real-money account.
4. **Confirmation that the next 09:30 cycle will in fact populate the fields** can only be verified by inspecting `bot_state` after that cycle runs — it cannot be proven in advance read-only. The control-flow analysis says it *should*; the operator must confirm empirically.

### Files referenced (follow-up)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py` — `fetch_symphony_stats` (82-96); `_persist_composer_fields_to_bot_state` (98-103); market-closed early-return (310-313); rebalance blackout (316-332); EOD post-mortem branch, no persist call (404-432); `symphony_data_cache` build (377-379); intraday evaluation loop (448-740); per-symphony `bot_state` writes + persist call (681-689); intraday `save_state` (883)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\app.py` — `/api/state` `symphonies_list` build reading `bot_state` with `None` default (150-166); helper calls (169-189); engine subprocess spawn (55-60)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\analytics.py` — `get_symphony_cumulative_return` `None` sentinel (430-431) + `0.0/0.0` TWR fallback (434-435); `get_symphony_max_drawdown` `None` sentinel (450-451)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\fixtures\composer\symphony_stats_meta.json` — raw `symphony-stats-meta` capture; 11 objects, each with `id` + all 4 fields (first object has literal `simple_return: 0.0`, `net_deposits: 0.0`, `time_weighted_return: 3.13212`, `max_drawdown: 0.2552`)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alphabot_state.db` — `bot_state` (11 symphony entries, 30 keys each, 4 Composer fields KEY-ABSENT; `current_return`/`current_value` present & live); `chart_history` (date `2026-05-14`, last point `15:52` all symphonies)
- Git: `b9ab6f9` merged 20:01:44 ET; `cf6a0e2` (engine persist GREEN) committed 19:31 ET; daemon PID 23528 started 20:02:21 ET; `alpha_bot_execution.py` file mtime 20:01:44; DB mtime 20:06:01
