# V3 Test Suite Triage — 2026-05-20

**Branch:** feat/studio-design-handoff
**Commit of Phase 1 fixes:** 19cad9e
**Starting state (Phase 1):** 86 failed + 8 errors / 2619 passed
**After Phase 1:** 68 failed + 3 errors / 2642 passed
**After Phase 3 (this pass):** 58 failed + 3 errors / 2652 passed
**Phase 3 Bucket A fixes:** 6 tests fixed (4 advisor env isolation + 2 math_engine source comments)
**After Phase 3 B-stale + B-valid triage:** 25 failed + 0 errors / 2678 passed
**Phase 3 B-stale fixed:** B-01, B-02, B-03, B-08, B-11 (33 tests rewritten to Studio design contract)
**Phase 3 B-valid fixed:** B-10 (inline onclick removed, addEventListener wired in chrome.js)
**Phase 3 ERRORs fixed:** 3 live test errors eliminated via `-m 'not live'` in pyproject.toml addopts
**Remaining failures (25):** All B-build — genuine missing features, require implementation workers

---

## Phase 1 — Bucket 1 — Stale Tests Fixed (2026-05-20)

### C-01 — Optuna search space keys regex (multiline frozenset)
- **File:** `tests/ai_advisor/test_ai_advisor_safety.py:1016`
- **Fix:** Added `\s*` around `{` in regex: `frozenset\(\s*\{(.*?)\}\s*\)` — the constant in `autotuner.py` is defined with whitespace/newlines between `frozenset(` and `{`.

### C-02 — Stale worktree fixture path (5 test classes)
- **File:** `tests/execution/test_cycleat_fix.py:541`
- **Fix:** Replaced hardcoded dead worktree path `.claude/worktrees/cycleat-team/...` with `pathlib.Path(__file__).parent.parent.parent / "tests" / "fixtures" / "composer" / "symphony_stats_meta.json"`. Added `import pathlib`.
- **Note:** 4/5 tests now pass. `test_cr_dry_run_is_percent_not_decimal_for_normal_symphony` remains RED — see Bucket B below.

### C-03 — setInterval tests asserting wrong file (index.html vs index.js)
- **File:** `tests/ui/test_cycle_2_fix_live_data.py:400,424`
- **Fix:** Repointed both tests from `templates/index.html` to `static/index.js`. Updated interval detection to match `POLL_INTERVAL_MS` named constant pattern (not magic literal).

### C-08 — Regex syntax error in window-selector fetch handler test
- **File:** `tests/ui/test_comprehensive_audit.py:325,343`
- **Fix (regex):** Removed unbalanced `\(` from negative lookahead in `class_only_pattern`. Fix: `(?:(?!fetch|loadState|_cumChart|renderHeroChart).)*?`.
- **Fix (rfind→find):** Changed `combined.rfind("window-selector")` to `src.find("window-selector")` — `rfind` was landing in the HTML buttons block (last occurrence), not the JS handler (first occurrence). Test now correctly detects `fetch('/api/hero-chart/')` in the click handler.

### C-16 — Autotune panel table→card contract (3 tests)
- **Files:** `tests/ui/test_cycle_4_advisor.py:792,801`, `tests/reporting/test_dsr_surfacing.py:721`
- **Fix:** Updated `test_advisor_autotune_panel_has_table` and `test_advisor_autotune_runs_tbody_present` to assert `id="autotune-runs-list"` (new card-list contract) instead of `<table>` / `id="autotune-runs-tbody"`.
- Updated `test_ai_advisor_page_has_naive_sharpe_column_header`: panel is JS-rendered cards now (no static `<th>` header); test now checks `static/ai_advisor.js` references `naive_sharpe` field.

### C-19 — Poll cadence: tests assert 15s in index.html, code runs 30s in index.js
- **File:** `tests/dashboard/test_r9_poll_cadence.py` (full rewrite)
- **Fix:** Rewrote to check `static/index.js` + `loadState` + `POLL_INTERVAL_MS = 30000`. Behavior audit B-27 confirmed 30s is the correct cadence. Widened comment-search window from 3 to 15 lines to reach the `// Poll loop` comment block.

### Additional stale tests fixed (Phase 1, not in original C-list)

**Max-width media query false positives:**
- `tests/ui/test_comprehensive_audit.py::test_history_no_max_width_cap_on_top_level_container`
- `tests/ui/test_comprehensive_audit.py::test_advisor_no_max_width_cap_on_top_level_container`
- **Fix:** Added `_strip_media_query_breakpoints()` helper to remove `@media (max-width: ...)` blocks before checking. `history.html` 1200px and 640px are responsive breakpoints, not container caps; `ai_advisor.html` 1100px is a media query.

**tokens.css comment false positive:**
- `tests/ui/test_cycle_1_foundation.py::test_tokens_css_no_hardcoded_hex_in_root`
- **Fix:** Added filter for lines starting with `*` (inside block comments) and ending with `*/` (end of block comment). A design comment `/* Light accent #1f7a4d is too dark... */` was incorrectly flagged.

**Status strip V-40 contract:**
- `tests/ui/test_cycle_2_dashboard.py::test_dashboard_has_status_strip`
- **Fix:** V-40 deliberately removed the separate `data-testid="status-strip"` element and moved status info inline to the chrome nav. Updated test to assert `system-online-dot` or `next-run` presence instead. The market state label check was preserved.

---

## Phase 3 — Bucket A — Stale Tests Fixed (2026-05-21)

### PA-01 — DEV_ADVISOR_FIXTURE env var bypasses advisor route mocks (4 tests)
- **Files:** `tests/ui/test_cycle_4_advisor.py` (2 tests), `tests/app/test_ai_advisor_tab.py` (2 tests)
- **Root cause:** `DEV_ADVISOR_FIXTURE=1` in `.env` is loaded into `os.environ` at app startup. The `/ai-advisor/suggest` route checks `os.environ.get("DEV_ADVISOR_FIXTURE")` first and returns the dev fixture, bypassing all mocked `ai_advisor.*` functions.
- **Fix:** Added `os.environ.pop("DEV_ADVISOR_FIXTURE", None)` inside `_patch_advisor()` context manager in `test_cycle_4_advisor.py` (with restore in `finally`). Added `monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)` to the `mock_advisor` fixture and to `test_post_suggest_failed_request_suggestions_returns_error_payload` in `test_ai_advisor_tab.py`.
- **Tests fixed:**
  - `test_suggest_response_impact` — impact.delta now present (route uses `_enrich_suggestion_impact`)
  - `test_suggest_error_path_returns_error_key` — error key now present when `request_suggestions` returns error
  - `test_post_suggest_calls_assemble_advisor_context_and_request_suggestions` — mock now called
  - `test_post_suggest_failed_request_suggestions_returns_error_payload` — error path now exercised

### PA-02 — math_engine.py multiline constants lack inline source comment (2 tests)
- **File:** `math_engine.py` (source fix), `tests/math_engine/test_vwap_bleed_arm.py`, `tests/math_engine/test_vwap_breakdown.py`
- **Root cause:** `VWAP_BLEED_ARM_MIN` and `VWAP_BREAK_CONFIRM_TICKS` were defined as multiline `(...)` expressions with the `# comment` on the closing `)` line. The test uses AST to find the assignment lineno and checks THAT line for a `#` comment. The project rule "every constant named + source comment" requires the comment on the same line as the assignment.
- **Fix:** Collapsed both constants to single-line form: `CONSTANT = value  # comment`
  - `VWAP_BLEED_ARM_MIN = -3.0  # most-negative clamp; ...`
  - `VWAP_BREAK_CONFIRM_TICKS = 3  # consecutive qualifying ticks; ...`
- **Tests fixed:**
  - `test_named_clamp_constants_exist_with_source_comments`
  - `test_new_named_constants_have_source_comments`

---

## Phase 3 — Bucket B — Three-Way Triage

### B-stale — Tests asserting old design patterns (FIXED — rewritten to Studio contract)

#### B-01 — Portfolio strip DOM + JS wiring (6 tests) — FIXED
- **File:** `tests/app/test_dashboard_m2.py::TestPortfolioStripUiWiring`
- **Root cause:** Tests asserted old `id="portfolio-strip"` DOM pattern. Studio uses `data-testid="hero-section"`.
- **Fix:** Rewrote 6 tests to assert Studio contract: `data-testid="hero-section"`, `portfolio_strip` in `static/index.js`, `today_change`/`cumulative_return`/`max_drawdown` in `static/index.js`, `_chrome.html` include before hero-section.

#### B-02 — Portfolio strip sizing + parenthetical format (5 tests) — FIXED
- **File:** `tests/app/test_dashboard_m2fix.py`
- **Root cause:** Tests asserted Tailwind `max-w-7xl` pattern and flex-col. Studio uses `class="page-wrap"` with custom CSS `max-width: 100%`, `vs-rows` layout, inline stats in `static/index.js`.
- **Fix:** Rewrote 5 tests to assert Studio contract: `class="page-wrap"` no narrow max-width, `data-testid="hero-section"` has padding, `.vs-row-label` CSS defined, `class="vs-rows"` not flex-col, `portfolio_strip`/`today_change`/`cumulative_return`/`max_drawdown` in `static/index.js`.

#### B-03 — Dashboard body/wrapper/table vertical fill (4 tests) — FIXED
- **File:** `tests/app/test_dashboard_vertical_fill.py`
- **Root cause:** Tests asserted Tailwind `flex flex-col` on body/wrapper. Studio uses `<body>` without class, `class="page-wrap"` custom CSS.
- **Fix:** Rewrote 4 tests to assert Studio contract: no fixed height on body, `class="page-wrap"` no narrow max-width, `class="cards-grid"` exists, Studio card sections (`data-testid="active-section"` etc.) exist.

#### B-08 — R15 shadow perf pill attributes + hover-highlight (12 tests) — FIXED
- **File:** `tests/dashboard/test_r15_shadow_names.py`
- **Root cause:** Tests asserted old shadow-banner JS pattern (`data-symphony-id`, `cross-highlighted`, `escapeHtml`). Studio uses per-card inline layout with `class="card-name"`, `data-sym-id`, `.sym-card` CSS, Jinja autoescaping.
- **Fix:** Rewrote 12 tests to assert Studio contract: `class="card-name"` with font styling, `data-sym-id="{{ ... }}"` on sym-cards, `.sym-card:hover` CSS, `openDetailPanel(` calls, `id="detail-panel"` element, Jinja `{{ sym` autoescaping, no `|safe` on name fields.

#### B-11 — AI advisor settings modal — Anthropic API key field (3 tests) — FIXED
- **File:** `tests/app/test_ai_advisor_tab.py`
- **Root cause:** Tests hit `GET /` for ANTHROPIC_API_KEY input. Studio moved credentials to `/settings` page.
- **Fix:** Rewrote 3 tests to assert Studio contract: `GET /settings` returns page with `anthropic` string, `<input type="password">` with anthropic key, `ANTHROPIC_API_KEY` in settings page.

### B-valid — Code quality tests correct regardless of design (FIXED)

#### B-10 — Force-run button inline onclick (3 tests) — FIXED
- **File:** `tests/ui/test_cycle_1_ux_blocks.py::test_topbar_force_run_button_no_inline_onclick`
- **Root cause:** `templates/_chrome.html` had `onclick="forceRun(event)"` — CSP violation.
- **Fix:** Removed inline `onclick` from button. Added `addEventListener('click', forceRun)` in `static/chrome.js` via `DOMContentLoaded` + `querySelector('[data-testid="force-run-btn"]')`.

### B-build — Genuine missing features (25 tests — require implementation workers)

#### B-04 — Edit vars indicator (4 tests)
- **File:** `tests/app/test_edit_vars_indicator.py`
- **Root cause:** No `id="edit-vars-indicator"` element in `templates/index.html`. Operator has no visual signal that variables are locked/set.
- **Fix spec:** Server-render an `<span id="edit-vars-indicator">N</span>` (or `data-testid="edit-vars-indicator"`) in `templates/index.html` near the "Edit Variables" button, populated from the `locked_vars` count passed in the template context. Must be present on initial load (not JS-injected). Count must match the number of locked vars from `database.get_symphony_strategy`.
- **Owner:** `flask-dashboard-specialist`

### B-05 — Emergency Liquidate / Panic confirm modal (3 tests)
- **File:** `tests/app/test_sell_account_panic_confirm.py`
- **Root cause:** `templates/index.html` has no "Emergency Liquidate" button, no panic modal, no `LIQUIDATE` confirmation JS.
- **Fix spec:** Add a panic button (`Emergency Liquidate` text or `id="emergency-liquidate"`) to `templates/index.html`. Add a modal with account selector from `accounts_map`. Add JS that disables the confirm button until the user types `"LIQUIDATE"` in a text input.
- **Owner:** `flask-dashboard-specialist`

### B-06 — Fleet correlation banner (4 tests)
- **File:** `tests/dashboard/test_fleet_banner.py`
- **Root cause:** `templates/index.html` has no `fleet-correlation-banner` element, no `fleet_correlation_alert` JS reference, no `/api/fleet-alert/dismiss` call.
- **Fix spec:** Add `<div id="fleet-correlation-banner" hidden>...</div>` to `templates/index.html`. In `renderState()` add JS to show/hide the banner based on `data.fleet_correlation_alert`. Add a dismiss button that POSTs to `/api/fleet-alert/dismiss`.
- **Owner:** `flask-dashboard-specialist`

### B-07 — Market mode banner + EOD snapshot (4 tests)
- **File:** `tests/dashboard/test_market_mode.py`
- **Root cause (3 banner tests):** `templates/index.html` has no `id="market-state-banner"` or `data-market-state` element. Must appear above the portfolio strip. Tests inject bot_state with `market_state` field and assert banner text shows "Market Open" or "Market Closed/Frozen".
- **Root cause (EOD snapshot test):** `alpha_bot_execution.py` EOD post-mortem branch never populates `bot_state['last_market_close_snapshot']` with the required schema keys (open, close, vix, breadth etc.) before calling `database.save_state`.
- **Fix spec (banner):** Add `<div id="market-state-banner">` to `index.html` above portfolio strip. Wire in `renderState()` to read `data.market_state` and show "Market Open" / "Market Closed" text.
- **Fix spec (EOD):** In `alpha_bot_execution.py` EOD post-mortem branch, populate `bot_state['last_market_close_snapshot']` dict with required schema keys before saving state.
- **Owner:** `flask-dashboard-specialist` (banner), `risk-engine-specialist` (EOD snapshot)

### B-09 — Staleness badge (4 tests)
- **File:** `tests/execution/test_cycleat_fix.py::TestStalenessBadgeMarkupInIndexHtml` (2), `TestStalenessBadgeJsWiring` (2)
- **Root cause:** `templates/index.html` has no `id="cycle-staleness-badge"` element, no `last_successful_cycle_at` JS reference.
- **Fix spec:** Add `<span id="cycle-staleness-badge" hidden>Stale</span>` to the header area of `templates/index.html`. In `renderState()`, read `data.last_successful_cycle_at`, compute age, and toggle the badge visibility. `/api/state` must include `last_successful_cycle_at` in its response.
- **Owner:** `flask-dashboard-specialist`

### B-12 — Hist series too few entries (2 tests)
- **File:** `tests/ui/test_cycle_2_fix_live_data.py::test_api_state_hist_dates_populated_from_db`, `test_api_state_hist_bot_held_parallel_and_nonzero`
- **Root cause:** `/api/state` hist series is built from trigger days only (4 entries); tests require ≥30 entries when analytics returns 35 days of chart_archive.
- **Fix spec:** In `app.py` `/api/state` route, build `hist_dates` and `hist_bot_held` from the analytics `get_chart_archive` result (continuous daily series), not from bot_state trigger log.
- **Owner:** `risk-engine-specialist`

### B-13 — History per-reason charts (3 tests)
- **Files:** `tests/ui/test_cycle_2_fix_live_data.py::test_history_rendered_html_has_per_reason_chart_elements`, `test_history_js_render_reason_cards_emits_reason_bar_svg`, `tests/ui/test_cycle_5_history.py::test_history_by_reason_cards_present`
- **Root cause:** `templates/history.html` has no `data-testid="reason-card"` elements server-rendered. `static/history.js` `renderReasonCards` function does not emit `reason-bar` SVG elements in its innerHTML.
- **Fix spec:** Server-render at least one `<div data-testid="reason-card">` in `templates/history.html` for each exit reason (or leave as JS-rendered but ensure `renderReasonCards` emits `data-testid="reason-bar"` SVG elements with rect width proportional to win-rate/alpha).
- **Owner:** `flask-dashboard-specialist`

### B-14 — CR dry_run reads live DB (1 test)
- **File:** `tests/execution/test_cycleat_fix.py::TestCumulativeReturnPercentScaling::test_cr_dry_run_is_percent_not_decimal_for_normal_symphony`
- **Root cause:** `analytics.get_symphony_cumulative_return` ignores `bot_state_entry=None` and reads shadow data from the live DB regardless. Test expects `dry_run == if_held` when no bot state is provided.
- **Fix spec:** The test needs either a `db_path` fixture override to use an isolated DB, or `get_symphony_cumulative_return` needs to respect `bot_state_entry=None` as meaning "no dry_run divergence" (return `if_held` for `dry_run`).
- **Owner:** `risk-engine-specialist` or `sqlite-specialist`

---

## ERRORs — FIXED

**3 ERRORs in `tests/ai_advisor/test_live_claude_advisor.py` — FIXED**
- Root cause: `ANTHROPIC_API_KEY` present in `.env` caused `_anthropic_key_present()` to return True, so the module-scoped `live_suggestions_response` fixture attempted a real API call during default test run.
- Fix: Added `-m 'not live'` to `addopts` in `pyproject.toml` so all `@pytest.mark.live` tests are excluded from default suite run. Live tests now cleanly deselected (11 deselected in current run).
