# app

> Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor endpoints (single-page SPA), and daemon singleton lifecycle.

**Source:** `app.py`
**Last updated:** 2026-06-18

## Overview

`app.py` is the Flask application and process host. It owns:

- **Daemon singleton** — pidfile-based single-instance enforcement at startup.
- **Minute scheduler** — spawns `alpha_bot_execution.py` at `:00` via `subprocess.run`; refreshes Composer account totals once per minute; prunes telemetry at 02:00.
- **Dashboard routes** — operator UI routes. Two CSRF-protected write paths exist: `POST /api/settings` (allowlisted .env keys) and `POST /api/symphony-settings/<name>` (per-symphony live-mode toggle). Templates open SQLite read-only; the dashboard is NOT a live-trade-action surface.
- **AI Advisor routes** — unified single-page SPA at `GET /ai-advisor` renders all 6 tabs in one server-side render; GET sub-routes for all 5 old per-tab pages now 302-redirect to `/ai-advisor`; POST action routes (suggest, evaluate, accept, reject, chat/send, strategy-builder/run) are unchanged.
- **CSRF infrastructure** — `_validate_csrf()` hook; `_csrf_before_request` before-request handler; `GET /api/csrf-token` token endpoint; `_SETTINGS_WRITE_ALLOWLIST` restricts which .env keys the settings write path can touch.

Module-level thread-safety constructs:

- `_DISMISS_EXECUTOR` — `ThreadPoolExecutor(max_workers=1)` for fleet-alert dismiss writes. Registered with `atexit` for graceful shutdown.
- `_FLUSH_STATE_LOCK` — `threading.Lock()` serializing `flush_resync` background writes against engine `save_state` writes.
- `_CHAT_RATE_LIMITER` — per-IP rate-limiter for AI Advisor chat endpoint (cost-DoS guard; max `CHAT_RATE_LIMITER_MAX_TRACKED_IPS` IPs).

## API Reference

### Daemon Singleton

#### `_acquire_daemon_singleton(pidfile: str) → None`
Enforces one-process invariant at startup. Reads the pidfile (if present), checks whether the stored PID refers to a live Planet Stopper process. Live → exit(1). Stale → take ownership. Registers atexit handler and SIGTERM handler to remove the pidfile on clean shutdown.

---

### CSRF Infrastructure

#### `GET /api/csrf-token`
Returns a fresh CSRF token for the current session. Required for all CSRF-protected write endpoints.

#### `_validate_csrf() → None`
Validates the `X-CSRF-Token` header against the session token. Raises `403` on mismatch. Called at the top of every CSRF-protected route.

#### `_csrf_before_request`
Flask `before_request` hook. Injects CSRF enforcement for the two guarded write paths.

---

### Scheduler

#### `run_scheduler() → None`
Runs the `schedule` loop: every minute at `:00` triggers `threaded_trigger()` and `_refresh_account_totals()`; daily at 02:00 runs `_run_trigger_retention()`.

#### `trigger_alpha_bot(force: bool = False) → None`
Spawns `alpha_bot_execution.py` as a subprocess. Passes `--force` when `force=True`. Logs stdout/stderr to `alphabot_daemon.log`.

#### `_refresh_account_totals() → None`
Fetches Composer account-level total-stats (`portfolio_value`, `simple_return`, `todays_percent_change`, `max_drawdown`) and populates `_account_totals_cache`. Swallows all exceptions — stale cache is preferred over an empty one.

#### `_run_trigger_retention() → None`
Prunes `exit_triggers` rows older than `TRIGGER_TELEMETRY_RETENTION_DAYS` (default 90) and `shadow_history` rows older than `SHADOW_HISTORY_RETENTION_DAYS` (default 180).

---

### Dashboard Routes

#### `GET /`
Dashboard root. Calls `get_api_state_dict()`, partitions symphonies into active/standby, enriches each with analytics data.

#### `GET /api/state`
Returns the full API state dict as JSON. Includes `bot_state`, `portfolio_strip`, `meta`, `exit_authority`, and `port_state` (SITE-D1 KEEP-DISPLAY).

#### `GET /history`
Render historical performance page.

#### `GET /performance`
Render performance analytics page.

#### `GET /api/performance`
Returns per-day performance metrics as JSON.

#### `GET /api/performance/symphonies`
Returns per-symphony performance breakdowns.

#### `GET /api/history/<int:days>`
Returns historical portfolio data for the last `days` days.

#### `GET /api/logs/<symphony_id>`
Returns symphony execution logs.

#### `GET /api/triggers`
Returns recent exit trigger telemetry rows.

#### `GET /api/accounts`
Returns Composer account information.

#### `GET /api/hero-chart/<window>`
Returns hero chart data for the given window.

#### `GET /api/chart/<symphony_id>`
Returns per-symphony chart data.

#### `POST /api/fleet-alert/dismiss`
CSRF-protected. Dismisses the active fleet alert via `_DISMISS_EXECUTOR`.

#### `POST /api/trigger`
Operator-initiated manual trigger (protected endpoint).

#### `POST /api/force_eod`
Operator-initiated force end-of-day (protected endpoint).

#### `POST /api/resend_discord`
Resends the most recent Discord notification.

#### `POST /api/sell_account`
Operator-initiated account sell (protected; writes via Composer API).

#### `GET /api/autotune-runs`
Returns paginated autotune run history via `database.get_all_autotune_runs`. Response normalized via `_normalize_autotune_row`.

#### `GET /api/advisor-observations`
Returns `advisor_observations` rows for a symphony. Accepts `?symphony_id=` query parameter.

---

### Settings Write Paths

#### `GET /settings`
Renders the settings page.

#### `GET /api/settings`
Returns Globals from .env and Symphony Strategies from SQLite. Masks keys in `_MASKED_SETTINGS_KEYS` (e.g. `DISCORD_WEBHOOK_URL`). Includes `param_meta` (8-key `_ALGO_PARAM_META`).

#### `POST /api/settings`
CSRF-protected write path. Accepts JSON `{key: value}` pairs; only keys in `_SETTINGS_WRITE_ALLOWLIST` are written to `.env`. Excludes `LIVE_EXECUTION` and all credential keys.

#### `GET /api/settings/flush-resync`
Flushes and resyncs the settings cache.

#### `GET /api/symphony-settings/<symphony_name>`
Returns per-symphony live-mode state and strategy params for the gear-icon modal.

#### `POST /api/symphony-settings/<symphony_name>`
CSRF-protected write path. Requires explicit `confirmed=true` in request body. Calls `database.set_symphony_live_mode` to toggle per-symphony dry-run/live mode.

---

### AI Advisor Routes

The AI Advisor SPA was extended from 5 to **6 in-place tabs** in the spa-port cycle (2026-06-13). The Strategy Builder was formerly a separate page (`GET /ai-advisor/strategy-builder` → `render_template("ai_advisor_strategy_builder.html")`); it is now the 6th tab panel in the unified `templates/ai_advisor.html` SPA. Its GET route now 302-redirects to `/ai-advisor` like all the other former sub-pages. The POST action route (`POST /ai-advisor/strategy-builder/run`) is unchanged.

All 6 panels (Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder) are rendered in one server-side template at `GET /ai-advisor`. Tab switching is in-place via JS (`initTabSwitcher` in `static/ai_advisor.js`). All 5 old GET sub-routes 302-redirect to `/ai-advisor`; the POST action routes are unchanged.

#### `GET /ai-advisor` — `ai_advisor_tab()`

Unified single-page render for all 6 in-place tab panels. Server-side assembles all data needed for every panel in one request:

**Template context:**
| Key | Source | Panel |
|-----|--------|-------|
| `observations` | `database.get_advisor_observations_for_role` per `_ADVISOR_ROLES`; deduped; `NOT_APPLICABLE`/`feature_flag=off` stubs suppressed; **RF-1:** each non-MARKET_PRISM obs is stamped with `_preview_text` via `advisors.prism_render.humanize_obs_preview(obs["raw_response"])` before template render | Overview |
| `correlation_matrix` | `correlation_diagnostic.compute_pairwise_correlations` over analytics history | Correlations |
| `as_of` | `datetime.now(_ET).isoformat()` | Correlations |
| `crisis_caveat` | `correlation_diagnostic.CRISIS_CAVEAT` | Correlations |
| `insufficient_data` | `True` when matrix is empty | Correlations |
| `no_api_key` | `True` when Composer credentials absent | Asset Swaps, Logic Changes |
| `symphonies` | `analytics.list_available_symphonies` | Asset Swaps, Logic Changes forms |
| `chat_available` | `bool(os.environ.get("ANTHROPIC_API_KEY"))` — key presence only, value never passed to template | Chat |
| `sb_observations` | `database.get_advisor_observations_for_role("STRATEGY_BUILDER")`, reversed (oldest-first); empty list on error | Strategy Builder |
| `sb_card_artifacts` | dict keyed by `obs["id"]`; each value is an M6 `strategy_proposal` artifact dict for the Discuss/Chat affordance; built from `raw_response` fields per observation | Strategy Builder |
| `market_prism_summary` | `database.get_latest_market_prism_summary()`; `dict` or `None`; wrapped in `try/except` — `None` on failure renders an informative empty state; **RF-1:** `per_lens_digest` summaries are pre-humanized in-place by `advisors.prism_render.humanize_lens_summary` before template render (no new context key — template's existing `_lens.get('summary')` reads humanized prose) | Overview (Market Prism block) |

The Correlations, API-key, Symphonies, Strategy Builder, and Market Prism data assembly sections are wrapped in `try/except` — if those panels' data fails, the others still render. The Overview observations loop is not wrapped.

**RF-1 (prose render guard — two fixes):**

- **R1 (per-lens digest):** If `market_prism_summary` is present, `ai_advisor_tab()` iterates `per_lens_digest` and mutates each `_le["summary"]` in-place via `advisors.prism_render.humanize_lens_summary(_ln, _le)` (app.py:2966–2984). The template's existing `{{ _lens.get('summary') | e }}` renders humanized prose; no new context key is added. Council prose passes through unchanged; `lens_pipeline` JSON is humanized per lens type.

- **R2 (obs-raw-preview):** For each non-MARKET_PRISM observation in `observations`, `ai_advisor_tab()` stamps `obs["_preview_text"] = humanize_obs_preview(obs["raw_response"])` (app.py:2892–2902). The template renders `obs.get('_preview_text', '') | e` for non-MARKET_PRISM rows and `obs.verdict | e` for MARKET_PRISM rows.

See `DE-RF1-PROSE-RENDER` in `DECISIONS.md` and [advisors/prism_render](advisors_prism_render.md).

#### `GET /ai-advisor/correlations` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/asset-swaps` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/logic-changes` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/chat` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/strategy-builder` → 302 redirect to `/ai-advisor` — `ai_advisor_strategy_builder()`

Old bookmarks and links redirect cleanly rather than 404ing. The Strategy Builder content is now rendered as the 6th tab panel in the unified SPA. The POST action route is unaffected.

#### `POST /ai-advisor/asset-swaps/evaluate` — `ai_advisor_asset_swaps_evaluate()`

Accepts JSON: `{ symphony_id, from_ticker, to_ticker, objective_type? }`. Resolves the display name to a Composer hash (fails loudly if unresolvable — RC-6). Constructs a typed `SwapObjective`, calls `propose_operator_swap`, returns `SwapRunResult` fields as JSON. Never calls Composer write endpoints. Persistence handled inside `propose_operator_swap`.

**Key fix (2026-06-10):** Routes now resolve the display name → Composer hash before calling the engine (AC-8). Previously passing the display name to the engine caused silent empty results from the Composer backtest API.

#### `POST /ai-advisor/logic-changes/evaluate` — `ai_advisor_logic_changes_evaluate()`

Accepts JSON: `{ symphony_id, objective_type?, change_description }`. Same name→hash resolution as asset-swaps. Calls `propose_operator_logic_change` with the Composer hash. Returns `LogicChangeRunResult` fields as JSON including FDR metadata (n_candidates, fdr_q, fdr_adjusted_threshold via Yekutieli c(n) harmonic-sum).

**Key fix (2026-06-10):** `gate_reason` is derived as `gr.verdict.decision.replace("_", " ").title()` when `vetoes_passed` is True, else `"veto failed"`. The previous code attempted `gr.verdict.reason` which does not exist on `AcceptanceVerdict`.

#### `POST /ai-advisor/suggest` — `ai_advisor_suggest()`

Resolves the Composer hash → normalized symphony name for DB lookups; pre-fetches the autotune run once via `database.get_latest_autotune_run` and passes it to `ai_advisor.assemble_advisor_context` (which now honors the passed value and skips its own internal fetch — single DB round-trip total). Passes both `symphony_id` (normalized name) and `composer_symphony_id` (original hash) to `ai_advisor.assemble_advisor_context`. Returns `{"suggestions": [...], "assessment": {...}}` — the `assessment` key carries `build_assessment_from_context` output so the UI can explain the empty-suggestions state per symphony.

D-1 security contract: fully honored on this route and all advisor routes (asset-swaps/evaluate, logic-changes/evaluate, and the ImportError handler) — on exception, returns `{"error": type(exc).__name__}` only, never `str(exc)`.

#### `POST /ai-advisor/accept` — `ai_advisor_accept()`

Applies one suggestion through C2 safety gates: (1) allowlist, (2) risk-direction log, (3) OOS revalidation via `ai_advisor.revalidate_suggestion_oos`, (4) locked-var guard. On all-pass, writes new param value via `database.save_symphony_strategy` and persists the operator decision to `llm_suggestions` via `database.record_llm_suggestion`.

#### `POST /ai-advisor/reject` — `ai_advisor_reject()`

Records operator rejection to `llm_suggestions` audit trail. No config write, no `save_symphony_strategy` call.

#### `POST /ai-advisor/chat/send` — `ai_advisor_chat_send()`

Rate-limited (per-IP via `_CHAT_RATE_LIMITER`) explain-only chat endpoint. Accepts `{ artifact_type, artifact_id, artifact, history, message }`. Delegates to `advisors.advisor_chat.explain_artifact`. Hard constraints: no write path, no trade directives, no new unvalidated recommendations. Returns `{reply: str}` on success, `{error: str}` on failure; never returns 500 or HTML.

#### `POST /ai-advisor/strategy-builder/run` — `ai_advisor_strategy_builder_run()`

CSRF-protected. Accepts JSON: `{ objective, universe, symphony_id? }`. Lazy-imports `propose_strategies` from `advisors.strategy_builder_engine` (keeps the engine off the live 1-minute execution path). Calls `propose_strategies` with a `ScreenConfig`, gates candidates via the full FDR batch, and returns JSON with survivor/rejected detail plus FDR metadata for the operator audit trail. Advisory-only: never calls Composer write endpoints, never touches `LIVE_EXECUTION`. Not in `_SETTINGS_WRITE_ALLOWLIST`. D-1 contract honored: returns `{"error": type(exc).__name__}` on exception, never `str(exc)`.

---

### State Helpers

#### `get_api_state_dict() → dict`
Assembles the full state payload for `/api/state` and the dashboard template. Reads `bot_state`, computes `portfolio_strip`, builds `meta`, adds `exit_authority` via `os.getenv("EXIT_AUTHORITY")`.

#### `_compute_suggestion_gates(suggestion, symphony_id: str) → dict`
Computes four-gates verdict booleans for one suggestion: `allowlist`, `risk_direction`, `oos_frozen_eval`, `locked_vars`.

#### `_enrich_suggestion_impact(suggestion) → dict`
Builds impact dict with `before`, `after`, `delta`, `metric` fields from the suggestion's raw impact payload.

#### `_normalize_autotune_row(row: dict) → dict`
Normalizes an `autotune_runs` DB row for the `/api/autotune-runs` JSON response.

## Internal Dependencies

- `ai_advisor` — context assembly, Claude call, C2 safety gates, assessment builder
- `database` — all state-DB reads and writes for advisor routes
- `analytics` — symphony history, correlation data, symphony list
- `advisors.correlation_diagnostic` — `compute_pairwise_correlations`, `CRISIS_CAVEAT`
- `advisors.asset_swap_engine` — `propose_operator_swap`, `SwapObjective`, `_has_composer_key`
- `advisors.logic_change_engine` — `propose_operator_logic_change`, `LogicTweak`, `LogicChangeObjective`
- `advisors.advisor_chat` — `explain_artifact`, `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS`
- `advisors.strategy_builder_engine` — `propose_strategies`, `Objective`, `ScreenConfig` (lazy import)
- `symphony_logic` — `fetch_symphony_score`
