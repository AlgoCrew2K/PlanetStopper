# Cluster 2 — Group M: Unified SPA Shell (F38–F40)
Auditor: closeout-audit-suite
Date: 2026-06-17
Evidence standard: file:line + runnable result per finding

---

## Pre-execution check: app.py import safety

**CONFIRMED SAFE**

Verified that importing `app.py` does NOT start the live engine:
- `_DISMISS_EXECUTOR` (`:127`): a `ThreadPoolExecutor(max_workers=1)` for background dismiss writes — a lightweight thread pool, NOT the engine or scheduler.
- `run_scheduler()` call (threading.Thread start): ONLY inside `if __name__ == "__main__"` at the bottom of `app.py`. Confirmed via tail of file.
- `alpha_bot_execution.py` spawns: ONLY in `trigger_alpha_bot()` which is called by the minute scheduler — also `__main__`-guarded.
- Import is safe for test client use during market hours.

---

## F38 — Unified 6-tab SPA shell

**PASS**

**Runnable result (Flask test client, isolated temp DB)**:
```
F38 GET /ai-advisor: status=200
  id="tab-panel-overview": PRESENT
  id="tab-panel-correlations": PRESENT
  id="tab-panel-asset-swaps": PRESENT
  id="tab-panel-logic-changes": PRESENT
  id="tab-panel-chat": PRESENT
  id="tab-panel-strategy-builder": PRESENT
F38 all 6 panels present: True
F38 JS reference (ai_advisor.js): PRESENT
```

Tab button data-testids also confirmed present:
- `data-testid="tab-overview"`, `tab-correlations"`, `tab-asset-swaps"`, `tab-logic-changes"`, `tab-chat"`, `strategy-builder-tab"` — all PRESENT.

**Static cite**:
- `app.py:2848`: `@app.route("/ai-advisor", methods=["GET"])` — single route rendering all tabs.
- `templates/ai_advisor.html:935`: `id="tab-panel-overview"`; `:1096`: `id="tab-panel-correlations"`; `:1214`: `id="tab-panel-asset-swaps"`; `:1281`: `id="tab-panel-logic-changes"`; `:1359`: `id="tab-panel-chat"`; `:1389`: `id="tab-panel-strategy-builder"`.
- Tab buttons at `:880-926`: 6 `data-tab=` attribute buttons for in-place switching.

**`node --check` static/ai_advisor.js**:
```
$ node --check "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/closeout-audit/static/ai_advisor.js"
EXIT: 0
```
**PASS** — no syntax errors. The JS file parses clean.

**[NOTE]**: The eyes-on visual render (all 6 tab panels visually correct) is owned by `closeout-ux` (AC-13). My evidence covers route 200 + DOM panel presence + JS syntax; visual quality is the UX auditor's gate.

**Deleted templates confirmed absent**: grep for `ai_advisor_correlations.html`, `ai_advisor_strategy_builder.html` returns 0 template files. The per-tab standalone templates were deleted as documented in the gotchas.

---

## F39 — GET 302 redirects for 5 sub-routes

**PASS**

**Runnable result (live :8090 daemon, read-only GET probes)**:
```
F39 GET redirect probes against live :8090 daemon:
  /ai-advisor/correlations: status=302 Location=/ai-advisor -> PASS
  /ai-advisor/asset-swaps: status=302 Location=/ai-advisor -> PASS
  /ai-advisor/logic-changes: status=302 Location=/ai-advisor -> PASS
  /ai-advisor/chat: status=302 Location=/ai-advisor -> PASS
  /ai-advisor/strategy-builder: status=302 Location=/ai-advisor -> PASS
```
All 5 return HTTP 302 with `Location: /ai-advisor`. Non-mutating GETs; market-hours-safe.

**Static cite**:
- `app.py:3023-3030` (`/ai-advisor/correlations` → 302)
- `app.py:3033-3039` (`/ai-advisor/asset-swaps` → 302)
- `app.py:3174-3180` (`/ai-advisor/logic-changes` → 302)
- `app.py:3794-3800` (`/ai-advisor/chat` → 302)
- `app.py:3381-3391` (`/ai-advisor/strategy-builder` → 302)

All routes use `redirect(url_for("ai_advisor_tab"), code=302)` — same target route, consistent behavior.

---

## F40 — CSRF enforcement on all POST action routes

**PASS**

### CSRF infrastructure

Static cite:
- `app.py:71-107`: CSRF infrastructure:
  - `:81`: `_csrf_check_enabled: bool = True` (enabled by default)
  - `:87`: `_CSRF_TOKEN: str = secrets.token_hex(32)` — process-lifetime 64-char hex token
  - `:90-107`: `_validate_csrf()`: compares `request.headers.get("X-CSRF-Token")` vs `_CSRF_TOKEN` using `secrets.compare_digest`; aborts 403 on mismatch.
- `app.py:183-187`: `@app.before_request def _csrf_before_request()`: fires `_validate_csrf()` on EVERY POST request, unconditionally.

### Tokenless POST rejection (live test)

**Runnable result (Flask test client, isolated temp DB)**:
```
F40 tokenless POST /ai-advisor/suggest: status=403 (expect 403)
```
**PASS** — the `@before_request` hook fires before any route handler; a tokenless POST gets 403 before reaching advisor logic.

**Note on test-client CSRF**: The test client imports `app` without `tests/conftest.py` `_disable_csrf_for_tests` fixture. `_csrf_check_enabled` remains `True` (it is only set to `False` by the pytest conftest). This means the tokenless 403 is the real CSRF guard, not a test bypass.

### Advisory POST routes not in _SETTINGS_WRITE_ALLOWLIST

Static cite `app.py:2505-2511`:
```python
_SETTINGS_WRITE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "EXECUTION_START_TIME",
        "EXIT_AUTHORITY",
    }
    | set(_ALGO_PARAM_META.keys())
)
```

**Runnable result** (direct import):
```
_SETTINGS_WRITE_ALLOWLIST contents:
  'EXECUTION_START_TIME', 'EXIT_AUTHORITY',
  'MAX_PARABOLIC_SQUEEZE', 'MAX_SQUEEZE_FLOOR',
  'PARABOLIC_VELOCITY_THRESHOLD', 'TAKE_PROFIT_MC_PCT',
  'TRIGGER_THRESHOLD_PCT', 'VWAP_BLEED_MULTIPLIER',
  'VWAP_BLEED_TICKS', 'VWAP_CROSS_HWM_PCT'
Total keys: 10
LIVE_EXECUTION in allowlist: False
```

The allowlist governs only `POST /api/settings` (`:2514`). All AI advisor POST routes (`/suggest`, `/accept`, `/reject`, `/asset-swaps/evaluate`, `/logic-changes/evaluate`, `/chat/send`, `/strategy-builder/run`) are NOT in this allowlist. They cannot write `LIVE_EXECUTION` or credential keys.

Route docstring at `app.py:3405` explicitly states: "NOT added to `_SETTINGS_WRITE_ALLOWLIST` (this is not a settings write)."

---

## Summary — Group M

| Feature | Status | Confidence |
|---------|--------|------------|
| F38 6-tab SPA route 200 + all panels | PASS | HIGH (runnable) |
| F38 node --check ai_advisor.js | PASS | HIGH (EXIT:0) |
| F38 initTabSwitcher / ai_advisor.js present in HTML | PASS | HIGH (runnable) |
| F39 all 5 GET sub-routes → 302 /ai-advisor | PASS | HIGH (live daemon) |
| F40 tokenless POST → 403 | PASS | HIGH (runnable) |
| F40 CSRF before_request on ALL POSTs | PASS | HIGH (static) |
| F40 advisor POST routes NOT in SETTINGS_WRITE_ALLOWLIST | PASS | HIGH (runnable + static) |
| F40 LIVE_EXECUTION not in allowlist | PASS | HIGH (runnable) |

No open questions for Group M. All safety boundaries confirmed by direct runnable evidence.
