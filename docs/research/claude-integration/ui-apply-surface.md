# Research: Accept/Reject AI Config Suggestion UI

**Scope:** On-demand feature — operator triggers Claude → receives suggested config edits → reviews them as accept/reject-able diffs with per-suggestion rationale tooltips → accepted edits are applied to config.

**Branch:** `research-ui-apply-surface`
**Date:** 2026-05-14
**Codebase snapshot:** `app.py` (506 lines), `database.py` (241 lines), `templates/index.html`, `templates/performance.html`

---

## 1. Dashboard Placement

### Finding: New standalone tab at `/ai-advisor`

**Recommendation:** A new tab following the Performance tab pattern — a dedicated route at `/ai-advisor` rendering `templates/ai_advisor.html`.

**Reasoning from the codebase:**

The nav bar in `index.html` (lines 125–156) has an established pattern for cross-tab links:
```
/performance  — "Performance" link (amber, top-right header)
Simulator     — modal trigger (emerald, top-right header)
Edit Variables — modal trigger (blue, top-right header)
```

The Settings/Variables surface is a modal (`#settings-modal`) that opens inline on the main dashboard. This works because settings edits are quick. The AI advisor flow is categorically different: it has async state (pending/complete job), a structured diff UI, and per-suggestion accept/reject decisions. A modal is the wrong container — the operator needs stable screen real estate to compare current vs. suggested values across potentially 8–10 parameters per symphony.

The Performance tab (`/performance`, `templates/performance.html`) established the pattern for operator-facing surfaces that need their own page: a standalone Flask route that renders a standalone template with the same Tailwind `slate-900` body, same header chrome, and a "Back to Dashboard" link in the header. This is the right model.

**Nav placement:** Add a new link in the `index.html` header nav alongside the Performance link:
```
Performance | AI Advisor | Simulator | Edit Variables
```

The AI Advisor link would sit between Performance and Simulator, using an appropriate accent color (violet-400 is unused and distinguishable from amber/emerald/blue).

**Why not embed in the Settings modal:** The existing settings modal already writes params. Embedding an async AI diff workflow inside a modal that also has direct-edit inputs creates ambiguity about which path is authoritative. Keeping them separate maintains a clear mental model: "Settings modal = manual edit; AI Advisor tab = AI-assisted review."

---

## 2. Accept/Reject Diff UI Pattern

### HTML structure sketch (Tailwind slate-900 theme, no Chart.js)

The UI is a table of suggestions. Each row represents one config key change. The columns are: Parameter, Current Value, Suggested Value, Rationale (tooltip trigger), and Accept/Reject controls.

```html
<!-- Suggestion row — one per config key -->
<div class="suggestion-row flex items-center gap-4 p-4 bg-slate-800 rounded-xl border border-slate-700 mb-3"
     data-key="TRIGGER_THRESHOLD_PCT"
     data-scope="symphony"
     data-symphony="my-symphony-name">

  <!-- Parameter name -->
  <div class="w-52 shrink-0">
    <span class="text-xs font-bold text-slate-300 tracking-wide uppercase">
      TRIGGER_THRESHOLD_PCT
    </span>
    <div class="text-[10px] text-slate-500 mt-0.5">per-symphony · my-symphony-name</div>
  </div>

  <!-- Current → Suggested diff -->
  <div class="flex items-center gap-3 grow">
    <span class="font-mono text-sm text-slate-400 bg-slate-900 px-3 py-1 rounded border border-slate-700">
      15.0
    </span>
    <svg class="w-4 h-4 text-slate-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
    </svg>
    <span class="font-mono text-sm text-emerald-400 bg-slate-900 px-3 py-1 rounded border border-emerald-800">
      12.5
    </span>
  </div>

  <!-- Rationale tooltip trigger -->
  <div class="relative group shrink-0">
    <button class="text-[10px] text-slate-400 hover:text-slate-200 border border-slate-600 hover:border-slate-400
                   rounded px-2 py-1 font-medium tracking-wide transition-colors flex items-center gap-1">
      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      Why?
    </button>
    <!-- Tooltip — visible on hover via Tailwind group-hover -->
    <div class="absolute right-0 bottom-full mb-2 w-72 bg-slate-700 border border-slate-600
                rounded-xl p-3 text-[11px] text-slate-300 leading-relaxed shadow-xl z-10
                opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto
                transition-opacity duration-150">
      <p class="font-bold text-slate-100 mb-1 text-xs">Claude's Rationale</p>
      <p>The 30-day volatility of this symphony has declined 18% below its baseline.
         A tighter threshold captures more of the reversion move without increasing
         false-trigger frequency.</p>
    </div>
  </div>

  <!-- Accept / Reject controls -->
  <div class="flex items-center gap-2 shrink-0">
    <button class="accept-btn px-4 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-emerald-100
                   text-xs font-bold rounded-lg border border-emerald-600 transition-colors active:scale-95">
      Accept
    </button>
    <button class="reject-btn px-4 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300
                   text-xs font-bold rounded-lg border border-slate-600 transition-colors active:scale-95">
      Reject
    </button>
  </div>

  <!-- Decided state indicator (hidden until decided) -->
  <div class="decided-badge hidden shrink-0 text-[10px] font-bold tracking-widest px-2 py-1 rounded">
    <!-- JS sets: text-emerald-400 + "ACCEPTED" or text-rose-400 + "REJECTED" -->
  </div>
</div>
```

**Interaction model:** Accept/Reject are per-row. A "Apply X Accepted" button at the bottom of the list becomes active once at least one row is accepted. Rejected rows are visually dimmed (opacity-50) and excluded from the apply payload. The accepted payload is assembled client-side and POSTed to the write route.

**Tooltip implementation note:** The Tailwind `group-hover` approach above requires no JavaScript for hover. For accessibility and mobile, a click-toggle fallback can be added in the static JS file (not inline).

**Global vs. per-symphony labeling:** Each row must clearly label its scope. Global `.env` params show "global" under the param name; per-symphony params show the symphony name. This is critical — the operator must know whether accepting a suggestion changes all symphonies or just one.

---

## 3. Read-Only Tension Resolution

### Position: Config writes are NOT violations of the read-only constraint.

**The constraint verbatim:**
- Constraint #2: "Dashboard is a read-only operator surface — never an action surface for live trades."
- Constraint #5: "Templates open SQLite read-only; UI never reruns the engine."

**The existing write precedent — confirmed:**

`/api/settings` POST handler (app.py lines 474–492) currently:
1. Iterates `payload["globals"]` and calls `set_key(ENV_FILE_PATH, key, str(val))` — writing to `.env`
2. Iterates `payload["symphonies"]` and calls `database.save_symphony_strategy(sym_name, params, locked)` — writing to the `symphony_strategies` SQLite table

This route is already live and reachable from the "Edit Variables" modal in the dashboard. It writes both config stores. The dashboard is therefore already a partial write surface — but for config only, never for trade execution.

**Resolution:**

The read-only constraints specifically target two things: (a) issuing live trades, and (b) running the engine (math computation, Composer/Alpaca API calls) from within a route handler. Writing config parameters is neither of those things. The `/api/sell_account` route (lines 413–441) shows where the real guardrail is: that route has a `live_mode` check and only spawns the liquidation thread when `LIVE_EXECUTION=True`. The write-to-config path has no such restriction because it is safe by design — config changes take effect at the next engine execution cycle, they do not bypass the engine's own `is_live=True` guard.

**Write targets:**

Accepted suggestions map to one of two destinations, both of which are already handled by the existing `/api/settings` POST handler:

| Suggestion type | Write target | Handler |
|---|---|---|
| Global engine param (e.g., `EXECUTION_START_TIME`) | `.env` via `set_key` | `save_settings()` globals path |
| Per-symphony risk param (e.g., `TRIGGER_THRESHOLD_PCT`) | `symphony_strategies` SQLite table via `database.save_symphony_strategy` | `save_settings()` symphonies path |

**Conclusion:** No new write route is needed. The accepted suggestion payload is structurally identical to what the settings modal already POSTs — a JSON body with a `globals` dict and/or a `symphonies` dict. The new feature can POST accepted suggestions directly to the existing `/api/settings` endpoint. The only addition needed is the audit write (see Section 5), which should be a separate step handled server-side inside the apply handler or as a thin new route wrapping `save_settings`.

One caution: the existing `save_settings` handler does not distinguish between "operator manually edited a value" and "AI suggested a value the operator accepted." For audit purposes, it is slightly cleaner to add a `/api/ai-suggestions/apply` POST route that: (a) validates the payload came from a known job-id, (b) calls the same `set_key`/`save_symphony_strategy` internals, and (c) appends an audit record before returning. This keeps the audit logic out of the general-purpose settings route and makes the apply path traceable.

---

## 4. Non-Blocking Trigger Pattern

### Pattern: Background thread + in-memory job registry + polling

**Why this fits AlphaBot's existing architecture:**

`app.py` already uses `threading.Thread` for two classes of long-running work:
- `threaded_trigger()` (line 57) — spawns the engine subprocess in a daemon thread
- `force_eod()` (lines 179–208) — runs EOD analysis + autotuner in a daemon thread, returns a job-started response immediately

The EOD pattern is the closest analogue: the route returns `{"status": "success", "message": "EOD Analysis initiated"}` immediately, and the actual work runs in a `daemon=True` thread. There is no polling route for EOD results — they just appear in Discord. For the AI advisor we need the results back in the browser, so we add a polling step.

**Recommended pattern:**

```
POST /api/ai-suggestions/request
  Body: { "context": { "symphony_id": "...", "scope": "symphony"|"all" } }

  Handler:
    1. Generate job_id = str(uuid4())[:8]  (short, URL-safe)
    2. Store _AI_JOBS[job_id] = {"status": "pending", "result": None, "error": None}
    3. Spawn daemon thread: target=_run_claude_suggestion, args=(job_id, context)
    4. Return immediately: {"status": "accepted", "job_id": job_id}

GET /api/ai-suggestions/<job_id>
  Handler:
    1. Look up _AI_JOBS[job_id]
    2. If status=="pending": return {"status": "pending"}
    3. If status=="complete": return {"status": "complete", "suggestions": [...]}
    4. If status=="error": return {"status": "error", "message": "..."}
    5. If job_id not found: return 404

POST /api/ai-suggestions/apply
  Body: { "job_id": "...", "accepted": [ { "key": "...", "scope": "...", "symphony": "...", "value": ... }, ... ] }
  Handler:
    1. Validate job_id exists and status=="complete" (prevents replay attacks)
    2. Build globals/symphonies payload in the shape save_settings() expects
    3. Call set_key / save_symphony_strategy for each accepted item
    4. Append audit record (see Section 5)
    5. Mark _AI_JOBS[job_id]["status"] = "applied"
    6. Return {"status": "success", "applied_count": N}
```

**In-memory job registry (`_AI_JOBS` dict) vs. file/SQLite:**

For a single-process daemon with no worker restarts during a session, an in-memory dict is sufficient. The operator requests suggestions, reviews them, and applies or discards them within a single session. If the daemon restarts, pending jobs are lost — this is acceptable because the operator can simply request again. Persisting job state to SQLite would complicate the schema for minimal gain.

**Client-side polling:**

The client polls `GET /api/ai-suggestions/<job_id>` every 2 seconds until `status != "pending"`. 2 seconds is well above the dashboard's 15-second auto-refresh floor (which applies to state polling; this is a short-lived one-shot poll that stops as soon as the job completes). The poll loop should have a timeout (e.g., 30 seconds) after which it surfaces an error to the operator.

**Thread safety note:** The `_AI_JOBS` dict must be protected with a `threading.Lock` if multiple suggestion requests can be in flight simultaneously. For this operator surface, concurrent requests are unlikely, but the lock is cheap and should be included.

**Does this block the scheduler?**

No. The scheduler runs in its own daemon thread (`run_scheduler`, line 60) on a `schedule` loop that fires `threaded_trigger` at `:00`. The Claude API call runs in a separate daemon thread. Flask's built-in development server (`use_reloader=False`, single-threaded by default) does serialize request handling — but the suggestion POST returns immediately (before any Claude API I/O), and the polling GET route is a dictionary lookup (microseconds). Neither blocks the scheduler thread.

If the operator opens the AI Advisor during market hours, the 2-second poll requests hit the Flask server but each returns in <1ms. This is safe.

---

## 5. Audit Trail

### Recommendation: New `ai_suggestion_audit.json` file

**Why not `symphony_logs.json`:**

`symphony_logs.json` is keyed by `symphony_id`. AI suggestions can span multiple symphonies and also affect global `.env` params that have no symphony scope. There is no natural key to use — splitting a single suggestion session across multiple `symphony_id` keys would make it impossible to reconstruct which suggestions were reviewed together and what the operator decided holistically. The data model is wrong.

**Why a new JSON file over a new SQLite table:**

The state DB (`alphabot_state.db`) is held under a per-minute execution lock (`acquire_lock`). The audit write happens at apply time, which can overlap with a cycle execution. Adding a write to the state DB during an apply would compete with the lock. A separate JSON file (same pattern as `symphony_logs.json`) sidesteps this entirely — no lock contention, no schema migration, consistent with the existing pattern for operator-generated logs.

**Recommended file:** `ai_suggestion_audit.json`

**Schema (append-only list at the top level):**

```json
[
  {
    "session_id": "a3f1b2c4",
    "timestamp_utc": "2026-05-14T14:32:00Z",
    "trigger_context": {
      "scope": "symphony",
      "symphony_id": "my-symphony-name"
    },
    "suggestions": [
      {
        "key": "TRIGGER_THRESHOLD_PCT",
        "scope": "symphony",
        "symphony": "my-symphony-name",
        "current_value": 15.0,
        "suggested_value": 12.5,
        "rationale": "30-day volatility declined 18%...",
        "operator_decision": "accepted"
      },
      {
        "key": "MAX_SQUEEZE_FLOOR",
        "scope": "symphony",
        "symphony": "my-symphony-name",
        "current_value": 0.20,
        "suggested_value": 0.15,
        "rationale": "...",
        "operator_decision": "rejected"
      }
    ],
    "applied_count": 1,
    "applied_at_utc": "2026-05-14T14:33:15Z"
  }
]
```

**Key design decisions:**
- `session_id` ties the suggestion request to the apply action (same as `job_id` in the backend)
- Every suggestion is recorded whether accepted or rejected — no cherry-picking
- `applied_at_utc` is separate from `timestamp_utc` (suggestion time vs. apply time) — the operator may review for several minutes before deciding
- The rationale text is stored verbatim — this is the provenance chain for why a real-money parameter changed

**Database helper functions needed (future implementation scope, not now):**
- `append_ai_audit_session(session_record: dict)` — appends to `ai_suggestion_audit.json`
- `get_ai_audit_history(limit=50)` — reads the last N sessions for a future audit log view

---

## 6. Open Questions / Risks

1. **Claude API key management.** The Claude API key must be stored in `.env` and scrubbed before any template context (per the flask-dashboard-specialist's Operating Rule #6). It must never appear in `_AI_JOBS` result payloads or audit records. The implementation must explicitly strip it from any context passed to `render_template`.

2. **What context does Claude receive?** This research does not specify the prompt sent to Claude. The PM must define: does Claude receive the full `symphony_strategies` for the target symphony, the recent post-mortem JSON (performance data), or both? The richer the context, the more useful the suggestions — but the larger the API payload. This is a Gate-1 question for the implementation cycle.

3. **Locked vars.** The `symphony_strategies` table has a `locked_vars` list per symphony (e.g., `TRIGGER_THRESHOLD_PCT` is locked by default — see `DEFAULT_LOCKED_VARS` in `database.py`). Claude should not suggest changes to locked vars. The suggestion request handler must filter locked params out before building the suggestion prompt, or the apply handler must reject suggestions for locked params. Both layers are safer than one.

4. **Suggestion staleness.** If the operator requests suggestions, leaves the tab open for 2+ hours, then applies — the config may have changed in the interim (autotuner may have updated `symphony_strategies`). The apply handler should re-read the current config at apply time and detect conflicts (current value no longer matches the `current_value` in the suggestion). Surface a warning rather than silently overwriting the autotuner's work.

5. **`performance.html` and `index.html` do not use Jinja inheritance.** The planned `ai_advisor.html` template will also need to replicate the `<head>` and header chrome unless a base layout template is introduced first. The flask-dashboard-specialist's Operating Rule #3 requires Jinja inheritance for all templates — but neither existing template complies. The implementation cycle should either (a) introduce `base.html` and migrate all three templates, or (b) note explicitly in the A/C that the base layout migration is out of scope for this cycle and carry it as tech debt. This decision needs PM / user input before implementation begins.

6. **Rate limiting and cost.** Claude API calls are not free. If the operator hammers "Get AI Suggestions" on every symphony, costs accumulate. A simple per-session debounce (cooldown between requests for the same symphony) should be part of the implementation A/C.

7. **Single-process assumption.** The in-memory `_AI_JOBS` dict works only because AlphaBot runs as a single-process daemon (`use_reloader=False`). If the deployment model changes (e.g., Gunicorn multi-worker), the job registry must move to Redis or the state DB. Flag this in the implementation A/C as a known constraint.
