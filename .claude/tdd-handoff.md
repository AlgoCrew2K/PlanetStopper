# TDD Handoff — dfix cycle (AC-7 + AC-8 defect fixes)

**Branch:** feat/dashboard-realtime-push
**RED commit:** see git log (test(ac7-ac8): re-point hollow fixtures...)
**Handoff owner:** dfix-impl (flask-dashboard-specialist)
**Do NOT read the PM brief or dfix-test brief** — implement ONLY what is in this file.

---

## What is RED and why

Running `python -m pytest tests/realtime_push/test_data_freshness_visibility.py -n0`
produces **5 FAILED / 5 PASSED**.

The 5 failing tests expose two concrete defects:

### Defect 1 — AC-7: per-symphony-dict reader misses the top-level key

The engine writes `last_successful_cycle_at` at the **top level** of `bot_state`, never
inside a per-symphony sub-dict (alpha_bot_execution.py:948/1092/1878):

```python
bot_state["last_successful_cycle_at"] = current_et.isoformat()
```

Two reader sites in app.py loop over per-symphony dicts and miss the top-level key:

**Site 1 — `_compute_portfolio_strip` (app.py:1281-1287):**
```python
_cycle_ts = None
for _sym_v in bot_state.values():
    if isinstance(_sym_v, dict):
        _ts = _sym_v.get("last_successful_cycle_at")   # <-- never in a per-sym dict
        if _ts:
            _cycle_ts = _ts
            break
```
Local variable name here is `bot_state`.

**Site 2 — `get_state` top-level data_as_of (app.py:2125-2131):**
```python
_tl_cycle_ts = None
for _tl_v in state_data.values():
    if isinstance(_tl_v, dict):
        _tl_ts = _tl_v.get("last_successful_cycle_at")  # <-- never in a per-sym dict
        if _tl_ts:
            _tl_cycle_ts = _tl_ts
            break
```
Local variable name here is `state_data` (different from site 1 — do not copy-paste a NameError).

Both sites fall through to `datetime.now(_ET)` because `last_successful_cycle_at` is
never found in any per-symphony dict. The correct read pattern already exists at
app.py:2255: `state_data.get("last_successful_cycle_at")` — a direct top-level get.

### Defect 2 — AC-8: `showConnectionLost()` targets non-existent DOM element ids

`static/index.js:1299-1310` — `showConnectionLost()` uses wrong selectors:

```js
var badge = document.getElementById('engine-status-badge');     // <-- id does not exist
// ...
var dataAsOf = document.querySelector('[data-testid="data-as-of"]') ||
               document.querySelector('.data-as-of');           // <-- no such testid/class
```

Real element ids in the templates:
- `templates/_chrome.html:51-53`: `id="engine-status-dot"` and `id="engine-status-label"`
  (there is no `engine-status-badge` id anywhere)
- `templates/index.html:846`: `id="hero-data-as-of" class="legend-as-of"`
  (there is no `data-as-of` testid and no `.data-as-of` class anywhere)

Result: `showConnectionLost()` silently no-ops on every call — no visible cue renders.

---

## Minimal GREEN implementation

### Fix 1 — app.py site 1 (`_compute_portfolio_strip`, local var `bot_state`)

Replace the per-sym-dict loop (app.py:1281-1287) with a direct top-level get:

```python
# Derive data_as_of from the actual data timestamp, not the server render clock.
# The engine writes last_successful_cycle_at at the TOP LEVEL of bot_state
# (alpha_bot_execution.py:948/1092/1878) — never inside per-symphony sub-dicts.
# Falls back to datetime.now() if no cycle timestamp is available.
_cycle_ts = bot_state.get("last_successful_cycle_at")
```

Then use `_cycle_ts` in the existing isoformat-parse block that follows (the try/except
that formats `_data_as_of` is already correct — only the lookup changes).

### Fix 2 — app.py site 2 (`get_state` top-level, local var `state_data`)

Replace the per-sym-dict loop (app.py:2125-2131) with a direct top-level get:

```python
# AC-7: top-level data_as_of is the JS fallback hero freshness signal.
# last_successful_cycle_at is a top-level key (alpha_bot_execution.py:948/1092/1878).
_tl_cycle_ts = state_data.get("last_successful_cycle_at")
```

Then use `_tl_cycle_ts` in the existing isoformat-parse block that follows.

### Fix 3 — `static/index.js` `showConnectionLost()` (index.js:1299-1310)

Replace the two wrong selector calls with the real element ids:

```js
function showConnectionLost() {
    // Badge cluster: _chrome.html:51-53 uses engine-status-dot + engine-status-label
    var dot = document.getElementById('engine-status-dot');
    var label = document.getElementById('engine-status-label');
    if (dot) {
        dot.style.background = 'var(--studio-neg, #e53e3e)';
    }
    if (label) {
        label.textContent = 'Connection Lost';
        label.style.color = 'var(--studio-neg, #e53e3e)';
    }
    // Data-as-of element: index.html:846 uses id="hero-data-as-of"
    var dataAsOf = document.getElementById('hero-data-as-of');
    if (dataAsOf) {
        dataAsOf.textContent = 'connection lost';
    }
}
```

The mutation logic (textContent, color/style) is your choice — the test asserts only
that the function body references `engine-status-dot` or `engine-status-label`
(at least one real badge id from `_chrome.html`) AND references `hero-data-as-of`
(the real data-as-of id from `index.html`).

---

## Scope boundary

- Do NOT touch the AC-4 path (`_StaleFlagDict`, `_refresh_account_totals`, SSE freshness).
- Do NOT touch the snapshot branch (`closed_frozen` / `TestSnapshotBranchDataAsOfUsesSnapshotTimestamp`).
- Do NOT touch `alpha_bot_execution.py`.
- Do NOT create a PR or merge to main.

---

## After your GREEN

Run:
```
python -m pytest tests/realtime_push/test_data_freshness_visibility.py -n0 --tb=short
```

Expected: **0 failed, 10 passed** (all 5 previously-RED tests now GREEN, 5 previously-GREEN
tests still GREEN).

Also run ruff on changed files:
```
python -m ruff check app.py static/index.js
python -m ruff format app.py --check
```

Commit path-scoped (do NOT `git add -A`):
```
git add app.py static/index.js
git commit -m "fix(ac7-ac8): top-level last_successful_cycle_at reader + showConnectionLost real selectors"
```

Then SendMessage dfix-test: "GREEN — 0 failed / 10 passed. SHA=<sha>. Ready for review."
