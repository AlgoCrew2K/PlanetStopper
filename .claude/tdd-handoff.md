# TDD Handoff — dashboard-realtime-push RED phase

**Branch:** `feat/dashboard-realtime-push`  
**Worktree:** `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\rt-push`  
**RED commit:** `9406ee8`  
**RED count:** 19 FAILED / 2 PASSED  

## Your job (rt-impl)
Write the MINIMUM code in `app.py` and `static/index.js` that makes all 19 RED tests GREEN. No gold-plating. Do not read the feature plan. Read this handoff only.

## What is failing and where

### app.py — add these 4 things:

**1. Module-level registry (near existing module-level `_account_totals_cache: dict = {}`)**
```python
import queue as _queue
_sse_clients: list = []
_sse_clients_lock = threading.Lock()
```

**2. `_notify_cycle_complete()` function**
- Non-blocking: iterates `_sse_clients` under the lock and puts a sentinel on each queue
- Must NOT raise on empty list, full queue (catch `queue.Full`), or any other condition
- Must complete within 100 ms (use `queue.SimpleQueue.put_nowait()` or `put()` with no blocking)
- Must also clear `_account_totals_cache` (or call `_refresh_account_totals()`) so the cache is never stale post-cycle
- Called from `trigger_alpha_bot()` in a `finally:` block (after `subprocess.run()`, success or failure)

```python
def _notify_cycle_complete() -> None:
    """Fan out a cycle-complete notification to all connected SSE clients.

    Called from trigger_alpha_bot() in a finally block — must never raise.
    Also invalidates _account_totals_cache so /api/state serves fresh data.
    """
    # Invalidate the per-cycle Composer account-totals cache so the next
    # get_state() call does not serve data from the prior cycle.
    _account_totals_cache.clear()

    with _sse_clients_lock:
        clients = list(_sse_clients)

    for q in clients:
        try:
            q.put_nowait("cycle-complete")
        except Exception:
            pass  # full or closed — skip, do not raise
```

**3. Wire `_notify_cycle_complete()` into `trigger_alpha_bot()`** (app.py:572)

Current code:
```python
def trigger_alpha_bot(force=False):
    print(...)
    try:
        cmd = [sys.executable, "alpha_bot_execution.py"]
        ...
        subprocess.run(cmd, check=True, env=env, stdout=log_fh, stderr=log_fh)
    except subprocess.CalledProcessError as e:
        print(...)
```

Add `finally:` block:
```python
def trigger_alpha_bot(force=False):
    print(...)
    try:
        cmd = [sys.executable, "alpha_bot_execution.py"]
        ...
        subprocess.run(cmd, check=True, env=env, stdout=log_fh, stderr=log_fh)
    except subprocess.CalledProcessError as e:
        print(...)
    finally:
        _notify_cycle_complete()
```

**4. `GET /api/events` SSE route**

- Returns `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`
- Auth-gated by the existing `_auth_before_request` hook (do NOT add to `_AUTH_EXEMPT_ENDPOINTS`)
- Registers a `queue.SimpleQueue` in `_sse_clients`, yields events as they arrive, deregisters on generator exit
- Yields a heartbeat comment (`: heartbeat\n\n`) on a timeout so the connection stays alive
- Pattern:

```python
@app.route("/api/events")
def sse_events():
    """Server-Sent Events endpoint — streams cycle-complete notifications (AC-2)."""
    import queue as _q

    client_q: _q.SimpleQueue = _q.SimpleQueue()

    def generate():
        with _sse_clients_lock:
            _sse_clients.append(client_q)
        try:
            while True:
                try:
                    msg = client_q.get(timeout=15)  # 15 s heartbeat cadence
                    yield f"event: {msg}\ndata: {{}}\n\n"
                except _q.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_clients_lock:
                try:
                    _sse_clients.remove(client_q)
                except ValueError:
                    pass

    response = app.response_class(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
```

Note: `queue.SimpleQueue` has no `get(timeout=...)` — use `queue.Queue` for the blocking-with-timeout path, or use `SimpleQueue` with a polling loop. The skeleton above uses `queue.Queue` semantics. The test for `queue.Full` on a bounded queue means the `put` path must handle `queue.Full` — `SimpleQueue.put()` never raises Full; the test injects a `queue.Queue(maxsize=1)` to probe the guard. The implementation should use `try/except Exception` around each put to handle both.

Simplest correct approach using `queue.Queue`:
```python
client_q: queue.Queue = queue.Queue()
```
and replace `_q.SimpleQueue` with `queue.Queue` throughout.

### static/index.js — add EventSource wiring

Inside the `DOMContentLoaded` handler, after the existing `setInterval` call, add:
```js
// AC-3: SSE event-driven update — primary path; poll (above) is the resilience fallback.
if (typeof EventSource !== 'undefined') {
    var _es = new EventSource('/api/events');
    _es.addEventListener('cycle-complete', function () { loadState(); });
    _es.onerror = function () { /* silent — poll fallback handles reconnect */ };
}
```

Do NOT remove `setInterval(loadState, POLL_INTERVAL_MS)` — it must stay as the AC-5 fallback.

## AC-7: data_as_of must reflect real data age (app.py:1142 and app.py:1619)

Currently: `"data_as_of": datetime.now(_ET).strftime("%H:%M ET")` — always looks current.
Fix: read `last_successful_cycle_at` from bot_state, parse it, format as HH:MM ET.

Pattern (both call sites — app.py:1142 in `_compute_portfolio_strip` and app.py:1619 in `get_state`):
```python
# Derive data_as_of from the actual data timestamp, not the server render clock.
# Falls back to datetime.now() if no cycle timestamp is available.
_cycle_ts = None
for _sym_v in bot_state.values():
    if isinstance(_sym_v, dict):
        _ts = _sym_v.get("last_successful_cycle_at")
        if _ts:
            _cycle_ts = _ts
            break
if _cycle_ts:
    try:
        from datetime import datetime
        # Parse ISO format (with or without timezone)
        _dt = datetime.fromisoformat(_cycle_ts.replace("Z", "+00:00"))
        # Convert to ET for display
        try:
            from zoneinfo import ZoneInfo
            _ET_tz = ZoneInfo("America/New_York")
        except ImportError:
            import pytz
            _ET_tz = pytz.timezone("America/New_York")
        _dt_et = _dt.astimezone(_ET_tz)
        data_as_of = _dt_et.strftime("%H:%M ET")
    except Exception:
        data_as_of = datetime.now(_ET).strftime("%H:%M ET")
else:
    data_as_of = datetime.now(_ET).strftime("%H:%M ET")
```

Then use `data_as_of` (not `datetime.now(_ET).strftime(...)`) in the strip dict.

## AC-8: visible staleness cue on poll failure (static/index.js:1292-1297)

Currently the `.catch` in `loadState()` is console-only — no visible indicator.
Fix requires three changes to static/index.js:

**1. Add a `lastSuccessfulPollAt` tracker** (at IIFE scope, before DOMContentLoaded):
```js
var lastSuccessfulPollAt = 0;
```

**2. Update `lastSuccessfulPollAt` in the success `.then`** (inside loadState):
```js
function loadState() {
    fetch('/api/state')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            lastSuccessfulPollAt = Date.now();
            updateDashboard(data);
        })
        .catch(function (err) {
            console.error('state load failed', err);
            showConnectionLost();  // <-- ADD THIS
        });
}
```

**3. Add `showConnectionLost()` function** (at IIFE scope):
```js
function showConnectionLost() {
    // Flip the engine badge to a visible "connection lost" state
    // so the operator knows the dashboard is stale even if it looks alive.
    var badge = document.getElementById('engine-status-badge');
    if (badge) {
        badge.textContent = 'Connection Lost';
        badge.className = badge.className.replace(/\b(live|stale)\b/g, '') + ' stale';
    }
    var dataAsOf = document.querySelector('[data-testid="data-as-of"]') ||
                   document.querySelector('.data-as-of');
    if (dataAsOf) {
        dataAsOf.textContent = 'connection lost';
    }
}
```

The exact DOM selectors are flexible — what matters is that a visible DOM element is
updated in the catch path, and `lastSuccessfulPollAt` / `showConnectionLost` appear in the source.

## Test files to run (bounded — NEVER run full suite)
```
python -m pytest tests/realtime_push/ -n0 -p no:cacheprovider -q
```

Expected: 27 passed / 0 failed.

## Hook points in app.py for reference
- `trigger_alpha_bot()` — app.py:572 — add `finally: _notify_cycle_complete()`
- `_account_totals_cache` — app.py:454 — module-level dict (already exists)
- `run_scheduler()` — app.py:696 — no changes needed here
- `get_state()` — app.py:1244 — no changes needed; cache clear in `_notify_cycle_complete` is sufficient
- `static/index.js` DOMContentLoaded handler — around line 1349

## Signal when GREEN
Send: `SendMessage rt-test "GREEN: 21 passed / 0 failed at <SHA>. Tests: tests/realtime_push/. Ready for your review."`
