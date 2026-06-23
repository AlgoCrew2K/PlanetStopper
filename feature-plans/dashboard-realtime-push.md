# Feature Plan: Dashboard Realtime Push
Status: ready

## Summary
Replace the dashboard's exclusive 30 s timer-based polling with an event-driven update
path using Server-Sent Events (SSE). When the engine subprocess finishes a cycle the
daemon publishes a `cycle-complete` event to all connected clients. The client subscribes
via `EventSource`, fetches fresh `/api/state` immediately on the event, and retains the
existing 30 s poll as a resilience fallback. `/api/state` is also hardened to serve the
freshest completed-cycle data (the `_account_totals_cache` is currently refreshed on its
own per-minute slot; after this change the engine-cycle hook also triggers a cache
refresh so the served data is from the just-completed cycle, not the prior one).

## Acceptance Criteria

### AC-1 — Engine cycle-completion event
When the engine subprocess (`alpha_bot_execution.py`) finishes a cycle, the daemon
publishes a `cycle-complete` event to all connected SSE clients within ~1 s of
subprocess exit. The hook point is immediately after `subprocess.run()` returns in
`trigger_alpha_bot()` (app.py:581). The publication mechanism must not block the
scheduler thread (fire-and-forget notify, or a thread-safe queue drained by the SSE
generator).

### AC-2 — SSE endpoint exists and streams
A route (e.g. `GET /api/events`) exists and returns `text/event-stream` responses.
It is auth-gated by the existing `_auth_before_request` hook (unauthenticated → 401 JSON,
not a redirect, consistent with other `/api/` endpoints).
CSRF infrastructure is unaffected — SSE is GET-only, no state mutation.
The endpoint name is added to `_AUTH_EXEMPT_ENDPOINTS` if needed, or left protected.
Long-lived connections use a generator that yields heartbeats; the generator must not
starve other threads (no GIL-holding sleep in the scheduler path).

### AC-3 — Client updates on event
`static/index.js` subscribes to the SSE endpoint via `EventSource`. On a `cycle-complete`
event the client calls `loadState()` immediately (fetch fresh `/api/state` and call
`updateDashboard(data)`). The 30 s `setInterval(loadState, POLL_INTERVAL_MS)` is
retained as a resilience fallback — the PRIMARY update path is the SSE event. If
`EventSource` is unavailable (old browser) the existing poll continues unchanged.

### AC-4 — Fresh /api/state after a new cycle write
After a new cycle's state is written to the DB (engine subprocess exits), a subsequent
call to `GET /api/state` must return data reflecting that cycle, not the prior cycle's
cached data. Specifically: `_account_totals_cache` must be refreshed (or marked stale)
at the engine cycle completion hook so that the first `get_state()` call after the
cycle returns the freshest values.

The RED regression: write a fake "new cycle" state to the DB via the existing
`_isolate_db` fixture, then call `/api/state` — it must not return data from a prior
cache population that predates the DB write.

### AC-5 — Graceful degradation when SSE drops
If the SSE connection is dropped or `EventSource` is not supported the dashboard must
not go blank or broken. The fallback 30 s poll keeps state live. No new visual breakage.

### AC-6 — No execution-path harm
The SSE mechanism must never block or raise inside `trigger_alpha_bot()` or the
scheduler's `run_scheduler()` loop. It is a display-path read-only feature.
Long-lived SSE response generators must run in their own thread (Flask dev server) or
be handled by the WSGI server without starving the scheduler thread.

## Architecture

### Server side (app.py)
- Add a module-level `_sse_clients: list[queue.SimpleQueue]` (or `threading.Event`
  pattern) to track connected SSE generators.
- Add `_notify_cycle_complete()` — a non-blocking helper that puts a sentinel on every
  registered client queue and prunes disconnected ones.
- Call `_notify_cycle_complete()` in `trigger_alpha_bot()` immediately after the
  `subprocess.run()` call returns (success or failure — the cycle is done either way).
- Also call `_refresh_account_totals()` in the same hook so the next `get_state()` call
  is fresh (or mark the cache stale so the next call re-fetches).
- Add `GET /api/events` route: registers a `queue.SimpleQueue`, yields heartbeats every
  ~15 s (so proxies don't time out), yields `data: cycle-complete\n\n` on notification,
  deregisters queue on generator exit.
- Route returns `Response(generate(), mimetype="text/event-stream")` with
  `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers.

### Client side (static/index.js)
- After `DOMContentLoaded` (alongside the existing `setInterval`):
  ```js
  if (typeof EventSource !== 'undefined') {
      var _es = new EventSource('/api/events');
      _es.addEventListener('cycle-complete', function() { loadState(); });
      _es.onerror = function() { /* silent — poll fallback handles it */ };
  }
  ```
- The existing `setInterval(loadState, POLL_INTERVAL_MS)` is unchanged.

## Edge Cases
- SSE endpoint requires auth (same as other `/api/` routes). Unauthenticated request
  returns 401 JSON — the EventSource error handler fires and falls back to poll.
- Daemon restart: existing SSE clients get a connection error and reconnect via
  EventSource's built-in retry. They fall back to poll during the reconnect window.
- Engine cycle fails (CalledProcessError): `_notify_cycle_complete()` is still called —
  the client polls fresh state which will reflect the failure.
- Multiple simultaneous clients: `_sse_clients` list protected by a `threading.Lock`
  for append/remove operations.
- Very long SSE connections that accumulate dead clients: `_notify_cycle_complete()`
  prunes queues that raise `Full` (if bounded) or tracks live generators via a weak ref.
  Simpler: use an unbounded `SimpleQueue` — no `Full` exception; pruning happens when
  the generator catches `GeneratorExit`.

## Security Considerations
- SSE is a GET endpoint — no CSRF exposure. The `_validate_csrf` hook only applies to
  mutating requests.
- Auth gate applies normally: unauthenticated SSE requests return 401 JSON (same as
  other `/api/` routes). The `EventSource` API does not send cookies by default across
  origins — but this is same-origin, so cookies are sent and the auth cookie is valid.
- No secrets in the SSE event payload — `data: cycle-complete\n\n` only; no DB data
  pushed over the wire.

## Testing Strategy
1. **SSE endpoint contract (AC-2):** call `/api/events` with the test client, assert
   `Content-Type: text/event-stream` and 200 status. Assert 401 when auth is removed.
2. **Cycle-completion notification (AC-1):** mock `subprocess.run` to return immediately;
   call `trigger_alpha_bot()`; assert `_sse_clients` received a notification (a sentinel
   was queued). Mock/seam the notification function to avoid the generator.
3. **Fresh /api/state after new cycle (AC-4):** pre-populate `_account_totals_cache`
   with stale values; simulate a cycle completion hook; assert the cache is cleared or
   refreshed so `/api/state` does NOT return the stale values.
4. **Client wiring (AC-3):** `node --check static/index.js` (handled by the consolidated
   `tests/js_syntax/test_js_syntax.py` — no new per-file check added here). A contract
   test that the JS source file contains `EventSource` and `cycle-complete` and
   `loadState()` in the event handler block.
5. **No execution-path blocking (AC-6):** assert `_notify_cycle_complete()` does not
   raise and returns within a short timeout when called from a mock `trigger_alpha_bot`.

## Scope Boundaries
- No changes to auth infrastructure beyond ensuring `/api/events` is handled correctly.
- No changes to `alpha_bot_execution.py` — all hooks are in `app.py`'s wrapper
  `trigger_alpha_bot()`.
- No WebSocket — SSE only (one-way server→client, works behind standard HTTP proxies,
  no additional dependency).
- No push of actual state data in the SSE payload — clients fetch `/api/state` on the
  event (simpler, avoids duplicating get_state serialization logic in the push path).
