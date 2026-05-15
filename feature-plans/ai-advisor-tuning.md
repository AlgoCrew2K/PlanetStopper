# Feature: Tuning Page (replaces AI Advisor page) — Cycle A
Status: gate-1-and-gate-2-approved-ready-for-dispatch
Created: 2026-05-15
Successor: feature-plans/portfolio-mode.md (Cycle B — port-wide operating mode)

## Summary

Replace the standalone `/ai-advisor` page — currently a free-text symphony ID box that produces value only after the operator already knows what to type — with a `/tuning` page that is the operator's primary surface for inspecting and editing AlphaBot exit-criteria parameters. The new page lists the currently-running symphonies (sourced from the same `bot_state` blob the dashboard reads). Selecting a symphony reveals a table of the 8 exit-criteria parameters with three columns side-by-side per row: **Current** (editable), **Optuna Suggested** (from the latest walk-forward run), and **AI Suggested** (populated on demand by an "AI Advisor" button, with full rationale visible inline).

Accepting an AI value automatically locks that parameter — Optuna will still surface a suggestion for the locked parameter on subsequent tuning runs (so the operator can compare and choose to unlock), but Optuna's writeback skips locked keys.

The non-exit-criteria globals (API keys, execution mode, account IDs, Discord webhook) stay in the existing Settings modal — only the per-symphony exit-criteria block migrates.

A mode-toggle stub (per-symphony / portfolio-wide) is rendered on the page but **disabled** in Cycle A with a "Portfolio mode coming soon" tooltip. Cycle B (`feature-plans/portfolio-mode.md`) ships the real toggle.

## Acceptance Criteria

### A/C — Page structure
- [ ] **AC-1:** Header link "AI Advisor" → renamed to "Tuning". Old `/ai-advisor` route hard-cut (no redirect — single operator, no external links). [OQ-5 resolved: hard-cut]
- [ ] **AC-2:** Tuning page renders a scope picker listing every symphony present in `bot_state` (each shown as `{name} — {account}`, identified by symphony UUID).
- [ ] **AC-3:** A mode-toggle ("Per-Symphony" / "Portfolio-Wide") is rendered above the scope picker. "Portfolio-Wide" is **disabled** with tooltip "Coming soon — see feature-plans/portfolio-mode.md". Default and only-selectable mode in Cycle A is Per-Symphony. [OQ-1 resolved: Cycle A is per-symphony only; portfolio mode is Cycle B]
- [ ] **AC-4:** Page loads with no scope selected — table area shows an empty state pointing at the scope picker. No data fetch fires until a scope is chosen. [OQ-4 resolved: empty state default]
- [ ] **AC-5:** Selecting a symphony loads its parameters via `GET /tuning/strategy?symphony_id=...`.

### A/C — Parameter table
- [ ] **AC-6:** Table has one row per parameter, in this fixed order: `TRIGGER_THRESHOLD_PCT`, `MAX_SQUEEZE_FLOOR`, `TAKE_PROFIT_MC_PCT`, `VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS`, `PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`.
- [ ] **AC-7:** Each row shows: human-readable label, raw key (monospace subtext), definition tooltip, **Current** column (editable number input — min/max/step from canonical source AC-15), **Optuna Suggested** column, **AI Suggested** column (with rationale visible inline — not truncated), lock toggle, "Accept Optuna" button, "Accept AI" button, risk-direction indicator.
- [ ] **AC-8:** Numeric inputs enforce canonical min/max/step. Out-of-range entry shows inline validation and disables the Save button.
- [ ] **AC-9:** Lock toggle is explicit and per-row. **A locked row means one thing only: the autotuner's writeback skips this key on its next run.** Locked rows do NOT disable any UI controls — operator can still manually edit Current, click Accept-Optuna, or click Accept-AI. Adopting either suggestion while locked overwrites Current; the row stays locked. The lock is muted/visually distinct so the operator knows Optuna won't auto-overwrite. Toggling the lock OFF re-enables Optuna writeback on the next run.
- [ ] **AC-9b:** **Manual edit + Save does NOT auto-lock.** Manually edited values persist until the next autotuner writeback overwrites them. An operator who wants a manual value preserved across tunes must explicitly toggle the lock. (Symmetric with the "Optuna gets carte blanche until you commit" model.)
- [ ] **AC-10:** "Save Changes" button persists all Current edits + `locked_vars` set via `POST /api/tuning/strategy`. Single transaction (all 8 params + locks in one POST). [OQ-2 resolved: single transaction]
- [ ] **AC-11:** After save, the row repaints from server state (no optimistic UI).

### A/C — Optuna column
- [ ] **AC-12:** Optuna-Suggested column reads the latest `autotune_runs.best_params` JSON for the symphony. Cell shows the value Optuna last proposed for each parameter.
- [ ] **AC-13:** If no autotune run exists, or the row predates the new column, cell shows "—" with hover text "No tuning run yet".
- [ ] **AC-14:** For `MAX_SQUEEZE_FLOOR` (not in Optuna search space today), cell always shows "—" with tooltip "Not Optuna-tuned".
- [ ] **AC-15:** Canonical parameter spec (label, definition, min, max, step, type, risk-direction) lives in `ai_advisor.PARAM_SPEC` (promoted from the existing `_PARAM_VALID_RANGES` private dict). Exposed via `GET /api/tuning/param-spec`. Template reads from there — no literals in HTML or JS. RED test asserts this spec exactly matches the `autotuner.py` trial-suggest bounds (CI fails on drift).
- [ ] **AC-16:** "Accept Optuna" button copies the suggested value into the Current input (does not auto-save — operator hits Save Changes). Works regardless of lock state — adopting Optuna's value while locked leaves the row locked (Optuna becomes a manual-pick from here on). Disabled only when no Optuna suggestion exists for that row.
- [ ] **AC-16b:** **Symmetry semantics.** Once the operator has accepted AI on a param (auto-lock per AC-21), Optuna's role flips from auto-writer to manual suggester for that row — its column keeps refreshing every tune (AC-25), but values only adopt when the operator explicitly clicks Accept-Optuna. This is the inverse of the unlocked state, where Optuna writes back automatically and AI is the manual suggester.

### A/C — AI Advisor button + rationale
- [ ] **AC-17:** Page has one "Run AI Advisor" button operating on the currently-selected symphony.
- [ ] **AC-18:** Before firing, a confirmation modal shows: target scope, model (`claude-opus-4-7`), max tokens, approximate cost estimate, expected latency. Operator must confirm. [OQ-3 resolved: yes, cost confirmation]
- [ ] **AC-19:** During the call: button shows spinner and disables; AI column shows per-row loading state.
- [ ] **AC-20:** On success: AI column populates with `suggested_value`, `confidence` chip, `data_sufficiency` chip, and **full rationale text** (no truncation — visible inline by default, with a row expander only for very long rationales >400 chars).
- [ ] **AC-21:** **Accept-AI semantics:** "Accept AI" button triggers the existing `POST /ai-advisor/accept` flow (3 C2 gates unchanged: allowlist + risk-direction + OOS revalidation). On success, the param value is written via `database.save_symphony_strategy` AND the param key is **automatically added to `locked_vars`** for that symphony. The UI re-renders the row in locked state. Accept-AI on an already-locked row is allowed (operator is swapping one accepted AI value for another); the row stays locked.
- [ ] **AC-22:** "Reject AI" calls `POST /ai-advisor/reject` and clears the AI cell.
- [ ] **AC-23:** AI errors render inline on the page (no `alert()`).

### A/C — Optuna-writeback honors locks (engine-side change)
- [ ] **AC-24:** `autotuner.run_autotuner` writeback at `autotuner.py:421` is changed from "write all best_params" to "merge best_params into current params, **skipping keys in locked_vars**". Locked params retain their operator-accepted value. RED test covers this.
- [ ] **AC-25:** Optuna trial-suggest behavior is **unchanged** — all 8 keys are still suggested every trial so the Tuning page's Optuna column always has a value to display, regardless of locks.
- [ ] **AC-26:** `autotune_runs.best_params` is populated with Optuna's full suggested set (including locked keys) so the side-by-side display works for locked rows.

### A/C — Settings modal cleanup
- [ ] **AC-27:** Strategy Variables section deleted from the existing Settings modal (`templates/index.html:359–377`). Modal retains only Execution Mode + API Credentials & Accounts.
- [ ] **AC-28:** Header button "Edit Variables" renamed to "App Settings".
- [ ] **AC-29:** JS functions `renderStrategyTabs`, `generateTabHTML`, `switchTab`, and the strategy-tab portion of `saveSettings` deleted.

### A/C — Tier 1: Auto-review of Optuna results with portfolio awareness

- [ ] **AC-31:** At the end of every `autotuner.run_autotuner` invocation (after all symphonies have tuned and `best_params` is persisted), an auto-review batch fires: **N Claude calls, one per symphony.** This runs in the same EOD subprocess as the autotuner — never on the minute scheduler. Auto-review is gated to EOD only (forbidden during market hours).
- [ ] **AC-32:** Each per-symphony review call's payload contains the target symphony's review context PLUS a **portfolio context block** assembled from all currently-running symphonies:
  - **Per-symphony portfolio inventory:** for each symphony in `bot_state` — name, account, current params, `locked_vars`, today's intraday state (triggered/armed/tp_armed/para_armed, current_return, current_value, high_water_mark), latest Optuna `best_params`, condensed Composer logic via `symphony_logic.get_condensed_logic`.
  - **Correlation metrics computed fresh:**
    1. Pairwise daily-return correlation matrix over a rolling 60-trading-day window (sourced from existing chart-history data, computed via `numpy.corrcoef` in a new `analytics.compute_return_correlation_matrix()` helper).
    2. Behavioral co-trigger count: pairwise count of days in the last 60 where two symphonies both exited and the exit reasons matched (computed from chart_history / post_mortem records).
    3. Decision-tree overlap: pairwise set-intersection of tickers across condensed logic (simple cardinality).
  - **Portfolio aggregate stats** (reuse existing analytics functions):
    - 60-day aggregate return, aggregate drawdown, aggregate volatility (`analytics.compute_aggregate_returns`, `get_portfolio_max_drawdown`)
    - Concentration: per-symphony value share + Herfindahl-Hirschman index across `bot_state[*].current_value`
  - **Cap on context size:** if portfolio has > 15 symphonies, include the target symphony's k-nearest correlated peers (k=10) plus aggregate stats, rather than the full N. This is a guardrail; expected N today is ~5-10.
- [ ] **AC-33:** Claude returns a structured review with two layers:
  - **Per-param flags** — for each of Optuna's 8 `best_params`: `{verdict: "approve" | "flag" | "info", reasoning: str, severity: "low" | "med" | "high"}`. Reasoning may reference portfolio context (e.g., "Optuna picked aggressive TRIGGER on the only diversifier — would over-correlate this symphony's exit timing with the tech-momentum cluster").
  - **Portfolio correlation block** — `{severity: "ok" | "info" | "warn", correlated_groups: [{symphonies: [...], shared_exposure: str, behavioral_evidence: str, recommendation: str}], top_findings: [str]}`. This block is **redundant across the N calls** (each Claude review sees roughly the same portfolio data).
- [ ] **AC-34:** Persistence: new table `autotune_reviews(id, run_timestamp, symphony_id, per_param_flags TEXT, portfolio_correlation TEXT, prompt_hash TEXT)`. Each Friday batch writes N rows. The `portfolio_correlation` JSON is only stored on the FIRST successful review in the batch (deterministic order: alphabetical by `normalized_name`); subsequent rows in the same batch store `NULL` for `portfolio_correlation` to avoid storage bloat. `prompt_hash` lets us reproduce a review's input.
- [ ] **AC-35:** New routes:
  - `GET /api/tuning/review/latest` — returns the most recent batch's per-symphony flags + the single portfolio_correlation block. Used by both the Tuning page banner and per-row chips.
  - `GET /api/tuning/review/<symphony_id>` — returns the latest per-param flags for one symphony.
- [ ] **AC-36:** Tuning page **portfolio-level banner** at the top of the page (above scope picker): renders portfolio_correlation if `severity != "ok"`. Banner shows top_findings inline with an expander revealing the full correlated_groups detail. Persists across scope changes (it's portfolio-level, not symphony-level).
- [ ] **AC-37:** Tuning page **per-row Optuna chip**: when a symphony scope is selected, each row's Optuna Suggested column shows a chip from `per_param_flags[key]`:
  - "approve" → green check, no expansion
  - "info" → blue info icon with reasoning on hover/expand
  - "flag" → yellow/red warning icon with reasoning expanded by default; severity-tiered styling
- [ ] **AC-38:** **On-demand AI Advisor (Tier 2) also carries the portfolio context.** The `assemble_advisor_context` function is extended to accept an optional `include_portfolio_context: bool = True`. The new Run AI Advisor button defaults to `True` so on-demand suggestions also reason about cross-symphony correlation. The existing `/ai-advisor/suggest` route is updated; the existing `/ai-advisor/accept` revalidation path is unchanged.
- [ ] **AC-39:** **Cost guard:** auto-review only fires when `len(bot_state.symphonies) > 0`. If fewer than 2 symphonies exist, the portfolio_correlation block is skipped (set `severity: "ok"`, no Claude reasoning about correlation across N=1) — Claude still reviews per-param but without portfolio framing.
- [ ] **AC-40:** **Failure isolation:** if Claude review fails for one symphony in the Friday batch, the remaining reviews still attempt. Failed symphonies persist a row with `per_param_flags = NULL` and an error string in a new error column. UI shows "AI review unavailable for this symphony" inline.

### A/C — Tests (Quad team RED phase)
- [ ] **AC-30:** RED tests cover:
  - (a) `PARAM_SPEC` exactly matches `autotuner.py` Optuna trial bounds (drift = CI fail)
  - (b) `POST /api/tuning/strategy` rejects extra keys (allowlist), out-of-range values, NaN/inf, wrong types
  - (c) `locked_vars` round-trip persists correctly through save
  - (d) AI-accept auto-locks the key — assert the persisted `locked_vars` after a successful accept
  - (e) Autotuner writeback skips locked keys; `best_params` column still records full suggestion (locked + unlocked alike)
  - (f) `autotune_runs.best_params` schema migration is additive and NULLable; old rows remain NULL
  - (g) Accept-AI path still invokes all 3 C2 gates (allowlist + risk-direction + OOS revalidation) — must not bypass
  - (h) `symphony_id` query parameter is SQL-safe (parameterized queries; test `?symphony_id=' OR 1=1`)
  - (i) Cost-confirmation modal blocks the AI call until acknowledged (behavioral)
  - (j) Mode toggle "Portfolio-Wide" is disabled and cannot be selected (behavioral)
  - (k) **Accept-Optuna while locked** is allowed (does NOT 4xx); writes Optuna's value to Current; lock state preserved post-save
  - (l) **Manual edit + Save while unlocked** does NOT add the key to `locked_vars`; the next simulated autotuner writeback overwrites the manual value
  - (m) **Manual edit + Save while locked** is allowed; lock state preserved; next simulated autotuner writeback does NOT overwrite
  - (n) **Tier-1 auto-review** fires after autotuner completes and writes N rows to `autotune_reviews`; portfolio_correlation persists only on the first row; subsequent rows have NULL portfolio_correlation
  - (o) **Auto-review payload contains portfolio context** — assert each call's prompt includes ALL running symphonies' condensed logic, current state, and the computed correlation matrix
  - (p) **Correlation matrix math** — `analytics.compute_return_correlation_matrix` produces a symmetric matrix, NaN-safe for symphonies with insufficient history, agrees with `numpy.corrcoef` on a fixture
  - (q) **Failure isolation** — one Claude failure mid-batch does not abort the remaining reviews
  - (r) **Auto-review forbidden during market hours** — invoking the review function with `current_et` between 09:30 and 16:00 raises
  - (s) **On-demand AI Advisor (Tier 2)** payload also includes portfolio context when `include_portfolio_context=True` (default)
  - (t) **Portfolio banner** renders only when `portfolio_correlation.severity != "ok"`; hidden otherwise
  - (u) **Per-row Optuna chip** renders the per_param_flags verdict; "flag" expands reasoning by default; "approve" shows just the check

## Architecture

### Routes (new)
- `GET /tuning` — renders `templates/tuning.html`. Replaces `GET /ai-advisor`.
- `GET /tuning/scopes` — returns `{symphonies: [{id, name, account, normalized_name, triggered_today}]}`.
- `GET /tuning/strategy?symphony_id=...` — returns `{params, locked_vars, optuna_suggested, normalized_name}`.
- `POST /api/tuning/strategy` — payload `{symphony_id, params, locked_vars}`. Validates against `PARAM_SPEC`. Writes via `database.save_symphony_strategy`. Returns the persisted row.
- `GET /api/tuning/param-spec` — returns the canonical 8-key spec.
- `GET /api/tuning/review/latest` — returns the most recent Tier-1 review batch (portfolio_correlation + per-symphony per_param_flags map).
- `GET /api/tuning/review/<symphony_id>` — returns the latest per_param_flags for one symphony.

### Reused routes (unchanged)
- `POST /ai-advisor/suggest`, `/ai-advisor/accept`, `/ai-advisor/reject` — same C2 gates. AC-21 adds the auto-lock side-effect to `accept`'s caller (Tuning UI), not to the route itself — the route still only writes `parameters`. The Tuning UI separately POSTs an updated `locked_vars` immediately after a successful accept. RED test (AC-30d) asserts the post-accept lock state.
  - **Alternative considered:** modify `/ai-advisor/accept` itself to write the lock. Rejected because that route is also reachable from the now-deprecated `ai_advisor.js` and from tests; bundling a write semantics change there has a wider blast radius than necessary.

### Removed route
- `GET /ai-advisor` — hard-cut. `templates/ai_advisor.html` deleted. `static/ai_advisor.js` deleted.

### Schema change (additive, both migrations idempotent)
- Add `autotune_runs.best_params TEXT NULL` (JSON of `{param_key: value}`). Populated by `autotuner.run_autotuner` writing `study.best_params` JSON. Older rows remain NULL; UI shows "—" for those.
- Add new table `autotune_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, run_timestamp TEXT NOT NULL, symphony_id TEXT NOT NULL, per_param_flags TEXT, portfolio_correlation TEXT, error TEXT, prompt_hash TEXT)`. Indexed on `(symphony_id, run_timestamp DESC)` for "latest review for symphony X" reads.

### Engine-side change (live-ops impact — flagged per project CLAUDE.md)
- `autotuner.py:421` writeback honors `locked_vars`. Behavior diff: today, accepted AI values get clobbered on next Friday tune. New: AI-accepted values persist through Optuna runs until operator explicitly unlocks.
- This is a one-line semantics change in `run_autotuner` at the writeback step. No change to the trial objective, no change to read paths, no change to live execution. The change executes only during the Friday EOD tune window — never on the minute-scheduler hot path.

### Files touched
- `app.py` — 7 new routes (5 tuning + 2 review); remove `/ai-advisor` page route + template + JS.
- `autotuner.py` — write `best_params` JSON; merge writeback (honor locks); call Tier-1 review batch at end of `run_autotuner`.
- `database.py` — two additive migrations; `get_latest_autotune_run` returns `best_params`; new `get_symphony_scope_list()`; new `save_autotune_review` + `get_latest_review_batch` + `get_latest_review_for_symphony`; existing `save_symphony_strategy` unchanged.
- `ai_advisor.py` — promote `_PARAM_VALID_RANGES` → public `PARAM_SPEC`; new `assemble_review_context(symphony_id, portfolio_state)` + `request_optuna_review(context)` + `Pydantic` output models for `PerParamReview` and `PortfolioCorrelation`; extend `assemble_advisor_context` with `include_portfolio_context` param; no change to C2 gates on `/ai-advisor/accept`.
- `analytics.py` — new `compute_return_correlation_matrix(symphony_returns, window_days=60)` returning `{symphony_pairs: {(a,b): corr}}`; new `compute_co_trigger_counts(post_mortem_records, window_days=60)`; new `compute_concentration_hhi(value_by_symphony)`.
- `symphony_logic.py` — unchanged; `get_condensed_logic` already cached, gets called once per symphony per review batch.
- `templates/tuning.html` — new file (replaces `ai_advisor.html`). Includes portfolio banner + per-row Optuna chips.
- `templates/index.html` — header link rename + Settings modal Strategy Variables section deletion.
- `static/tuning.js` — new file. Drives scope picker, table, AI Advisor button, cost-confirmation modal, accept flow, lock-after-accept POST, portfolio banner render, per-row chip render.
- `static/ai_advisor.js` — deleted.
- `tests/tuning/` — new test directory.
- `tests/analytics/test_correlation_matrix.py` — new file for correlation math.
- `tests/autotuner/test_tier1_review_batch.py` — new file for batch fire + persistence + failure isolation.

## Edge Cases

- Symphony triggered (exited to cash) today — still in scope picker, badge shown, params still editable.
- Fresh symphony with no `autotune_runs` row — Optuna column all "—", Save still works, Accept-Optuna disabled.
- Operator edits Current while AI call in flight — AI result paints into AI column without overwriting Current; Save uses Current. AI-Accept overwrites Current at accept time.
- Concurrent edits across browser tabs — last-write-wins (acceptable, single operator).
- AI returns suggestion for a locked key — UI shows the suggestion but Accept-AI button is suppressed for already-locked rows (re-locking a locked row is a no-op; suggestion is informational).
- `MAX_SQUEEZE_FLOOR` AI suggestion — Optuna column always "—" but AI Advisor can still propose; lock semantics identical to Optuna-tuned params.
- AI Advisor failure mid-call — error renders inline, button re-enables, no partial state written.
- Operator declines cost-confirmation modal — no LLM call fires.
- Save with no edits — round-trips identically (no-op); harmless.
- Schema migration on a DB that already has `best_params` column (re-run) — `ALTER TABLE` is wrapped in a try/except checking column existence first.

## Security Considerations

- `POST /api/tuning/strategy`:
  - Server-side allowlist (8-key set from `PARAM_SPEC`). Extra keys → 400.
  - Server-side range validation. Out-of-range → 400. NaN/inf rejected.
  - Type coercion (float/int per `PARAM_SPEC.type`).
  - `locked_vars` elements must be from the 8-key set.
  - Parameterized SQL throughout `database.py` (already true).
- `/ai-advisor/accept` C2 gates are unchanged — RED test asserts all three still run on the Tuning page accept path.
- No new auth — dashboard remains loopback-bound; threat model unchanged.
- Rationale text from Anthropic is rendered as `textContent` (not `innerHTML`) — no XSS surface via LLM output.

## Testing Strategy

### Unit / integration (pytest, Quad RED phase)
- `tests/tuning/test_routes.py` — 5 new routes, happy + error paths.
- `tests/tuning/test_param_spec_source_of_truth.py` — `PARAM_SPEC` vs `autotuner` Optuna bounds equality (drift = CI fail).
- `tests/tuning/test_save_validation.py` — allowlist, range, type, NaN/inf, locked-edit rejection.
- `tests/tuning/test_ai_auto_lock.py` — after `/ai-advisor/accept` + Tuning's follow-up lock POST, `locked_vars` contains the accepted key.
- `tests/tuning/test_ai_gates_not_bypassed.py` — Accept path invokes allowlist + risk-direction + OOS revalidation (mock the LLM, assert call sequence).
- `tests/autotuner/test_writeback_honors_locks.py` — RED test for AC-24; given current params with locked subset, after `run_autotuner` the locked keys preserve their pre-run values and unlocked keys take `best_params`.
- `tests/database/test_autotune_runs_best_params.py` — schema migration idempotent; column NULLable; old rows survive.

### Behavioral (Playwright via flask-dashboard-specialist)
- Page loads at 1280px+, scope picker populates from fixture bot_state.
- Mode toggle "Portfolio-Wide" is disabled with tooltip.
- Select symphony → 8-row table renders with correct Current values.
- Edit + Save round-trip.
- Run AI Advisor → cost-confirmation modal → confirm → results paint with full rationale visible.
- Accept AI → row enters locked state, edit input disabled, lock toggle visible.
- Lock toggle off → Current input re-enables, Accept Optuna re-enables.
- Optuna column shows "—" for symphony with no autotune row; shows values for one with a populated `best_params`.

### Project hard rules
- Agent Teams TDD with Quad in shared worktree.
- Additive-first schema (NULLable column).
- No magic numbers — single source of truth (`PARAM_SPEC`).
- No blocking I/O on engine hot path — the one engine-side change (autotuner writeback) runs only during EOD tune, never in the minute scheduler.

## Decisions

| Decision | Rationale |
|---|---|
| Per-symphony only in Cycle A; portfolio mode in Cycle B | Live-ops blast radius of mode-aware engine reads + new autotuner objective + real scope=global AI logic warrants its own cycle with research-first design. See `feature-plans/portfolio-mode.md`. |
| AI accept auto-locks the param | Matches user model: accepting AI means "don't let Optuna overwrite this." Explicit-unlock-only is fail-safe. |
| Optuna still suggests all keys (visible) even when locked; writeback skips locked | Side-by-side display always has data; operator can later choose to Accept-Optuna manually or unlock to let writeback resume. |
| **Lock controls writeback only, not edit/accept.** Accept-Optuna and Accept-AI both work in any lock state. | The lock's single job is "autotuner stops auto-writing this key." It is NOT an edit gate. This matches the symmetry the user described: after AI accept, Optuna becomes a manual suggester (symmetric with how AI is a manual suggester when unlocked). |
| Manual edit + Save does NOT auto-lock | Locks are commitment. Manual tweaks may be exploratory and the operator wants Optuna to keep iterating. If they want a manual value preserved, the explicit lock toggle is one click. |
| Single-transaction save per symphony | No bulk cross-symphony save in Cycle A (would compound mis-edit risk). |
| Cost-confirmation modal before LLM call | $/time visibility before commit; single-operator surface, no harm in the extra click. |
| Empty state on page load (no auto-select) | Operator picks the scope explicitly; avoids accidental edits to whichever symphony is alphabetically first. |
| Hard-cut `/ai-advisor` URL (no redirect) | Single operator, single browser, no external links. |
| Keep API credentials / execution mode in old Settings modal | App-config ≠ per-symphony tuning. Mixing surfaces re-creates the UX problem. |
| Promote `_PARAM_VALID_RANGES` to public `PARAM_SPEC` | Single source of truth for ranges + labels + definitions + risk-direction. UI reads via API. |
| Add `autotune_runs.best_params` column | Today's behavior writes best_params into `symphony_strategies.parameters` directly, making "current" and "latest Optuna" indistinguishable. Persisted column gives an unambiguous read for the side-by-side. |
| Modify Tuning UI to POST `locked_vars` after AI accept, not modify `/ai-advisor/accept` route | Smaller blast radius; existing tests of `/ai-advisor/accept` still pass. Tuning UI owns the lock semantics. |
| **Tier-1 auto-review fires N per-symphony Claude calls, each carrying full portfolio context** | Per-symphony output is cleaner to render and persist; per-param flags are inherently per-symphony; portfolio context is what makes Claude's per-param reasoning portfolio-aware. The "big picture while tuning the small cog" requirement. |
| Portfolio_correlation block stored on only the first row of each batch | Identical across the N calls in a batch; deduping avoids storage bloat. Alphabetical ordering by normalized_name makes the "first row" deterministic. |
| Correlation matrix computed in `analytics.py`, not by Claude | Claude consumes the matrix as input; computing it locally is faster, cheaper, and verifiable. Claude's value is interpretation, not arithmetic. |
| Tier-1 auto-review runs only in EOD subprocess, never on the minute scheduler | Live-ops safety: LLM latency must never enter the engine hot path. Locked behind a market-hours guard in `autotuner.run_autotuner`. |
| On-demand AI Advisor (Tier 2) also receives portfolio context by default | Same "big picture" benefit. The operator-initiated flow shouldn't be blinder than the auto-review. |

## Open Questions

All Cycle A open questions resolved:
- OQ-1 (portfolio-table semantics) — moved to Cycle B (`feature-plans/portfolio-mode.md`).
- OQ-2 — single transaction.
- OQ-3 — yes, cost confirmation.
- OQ-4 — empty state default.
- OQ-5 — hard-cut.

Carry-forward to Cycle B: OQ-B1 through OQ-B5 in `feature-plans/portfolio-mode.md`.

## Scope Boundaries

- **IN (Cycle A):**
  - `/tuning` page with scope picker + 8-param table + side-by-side Current/Optuna/AI with full rationale
  - Mode-toggle stub (Portfolio-Wide disabled)
  - Lock toggle per row
  - AI Advisor button with cost-confirmation modal
  - Auto-lock on AI accept
  - Autotuner writeback honors locks (one-line semantics fix)
  - `autotune_runs.best_params` column + migration
  - `autotune_reviews` table + migration
  - `PARAM_SPEC` promotion
  - Settings modal cleanup (Strategy Variables removed)
  - Hard-cut `/ai-advisor` route + template + JS
  - **Tier-1 auto-review:** N per-symphony Claude calls after every Friday autotune, each carrying portfolio composition + correlation matrix + behavioral co-trigger data + aggregate stats
  - **Portfolio banner** on Tuning page surfacing correlated_groups + recommendations
  - **Per-row Optuna chips** rendering per-param verdict + reasoning
  - **Tier-2 AI Advisor (on-demand)** also carries portfolio context

- **OUT (Cycle B and later):**
  - Portfolio-Wide operating mode (Cycle B — `feature-plans/portfolio-mode.md`)
  - Bulk per-param edit across symphonies
  - Streaming/parallel LLM calls
  - Historical view of past Optuna runs (only "latest" surfaced)
  - Param-level audit trail UI (the `llm_suggestions` table exists but is not wired)
  - Mobile/tablet responsive
  - "Compare to default" view
  - AI Advisor scheduling / auto-run cadence

## Team Composition (project CLAUDE.md hard requirement)

**Quad in shared worktree:**
- `quant-test-writer` — adversarial RED tests across UI + API + schema + autotuner writeback + cross-source drift
- `implementer` — minimal GREEN
- `quant-code-reviewer` — math/safety/schema review
- `flask-dashboard-specialist` — Tuning template + JS + Settings modal cleanup

**Consult-only:** `sqlite-specialist` for the additive `best_params` + `autotune_reviews` migrations. `optuna-specialist` for the writeback semantics fix and the EOD auto-review batch hook in `autotuner.run_autotuner`.

Standing team per project CLAUDE.md is a Quad; `risk-engine-specialist` is consult-only since the engine read path is untouched (autotuner writeback is the only engine-side change, runs only EOD, never on the minute scheduler).

## Gate-2 — Implementation Approach (HOW)

### Worktree + branch
- Implementation branch: `cycle-a/tuning-page` (forked from `main` after this plan is merged)
- Single shared worktree path: `../AlphaBotPM-cycle-a-tuning`
- All 4 Quad agents work in the same worktree; autonomous Toxic Pair handoffs via `SendMessage`. PM does NOT relay between handoffs.
- Single PR back to `main` at cycle-complete.

### Phase ordering (dependency-driven)

**Phase 1 — Foundation (no functional behavior change; unblocks all later phases):**
- DB migrations (additive, idempotent): `autotune_runs.best_params TEXT NULL` + new `autotune_reviews` table with `(symphony_id, run_timestamp DESC)` index
- Promote `_PARAM_VALID_RANGES` → public `ai_advisor.PARAM_SPEC` (label, definition, range, step, type, risk_direction)
- New `analytics.py` helpers: `compute_return_correlation_matrix`, `compute_co_trigger_counts`, `compute_concentration_hhi`
- New `database.py` helpers: `save_autotune_review`, `get_latest_review_batch`, `get_latest_review_for_symphony`, `get_symphony_scope_list`

**Phase 2 — Backend write paths:**
- `autotuner.run_autotuner` writeback merges `best_params` into current params, skipping `locked_vars` keys; full `best_params` persists to `autotune_runs.best_params` regardless of locks
- `autotuner.run_autotuner` calls Tier-1 review batch at end of run (market-hours guard raises during 09:30-16:00 ET)
- `ai_advisor.assemble_review_context(symphony_id, portfolio_state)` and `request_optuna_review(context)` with new Pydantic models (`PerParamReview`, `PortfolioCorrelation`)
- Extend `assemble_advisor_context` with `include_portfolio_context: bool = True`
- C2 gates on `/ai-advisor/accept` untouched

**Phase 3 — API routes:**
- 7 new Flask routes (5 tuning + 2 review per `Routes (new)` section above)
- Hard-cut `GET /ai-advisor` page route; keep `/ai-advisor/suggest|accept|reject` unchanged (Tuning UI reuses them)

**Phase 4 — UI:**
- `templates/tuning.html` + `static/tuning.js` (scope picker, 8-row table, side-by-side columns, cost-confirmation modal, lock toggle, portfolio banner, per-row Optuna chips)
- `templates/index.html` header link rename + Settings modal Strategy Variables section deletion + "Edit Variables" → "App Settings"
- Delete `templates/ai_advisor.html` + `static/ai_advisor.js`

**Phase 5 — Cycle-complete:**
- Full pytest run (new `tests/tuning/`, `tests/analytics/test_correlation_matrix.py`, `tests/autotuner/test_tier1_review_batch.py`, schema migration tests)
- Playwright behavioral pass via `flask-dashboard-specialist`
- `quant-code-reviewer` final pass on math + schema + live/replay safety boundary
- PM opens PR to `main`; awaits user approval before merge

### RED test inventory — ordered by phase

`quant-test-writer` writes all of these in the initial RED sweep before `implementer` starts.

| Phase | Test file | Asserts |
|---|---|---|
| 1 | `tests/tuning/test_param_spec_source_of_truth.py` | PARAM_SPEC ranges exactly match autotuner trial bounds (drift = CI fail) |
| 1 | `tests/analytics/test_correlation_matrix.py` | symmetric, NaN-safe, matches `numpy.corrcoef` on fixture |
| 1 | `tests/database/test_autotune_runs_best_params.py` | migration idempotent, NULLable, old rows survive |
| 1 | `tests/database/test_autotune_reviews_table.py` | migration idempotent, indexed correctly |
| 2 | `tests/autotuner/test_writeback_honors_locks.py` | locked keys preserved, unlocked keys take best_params, best_params column always full |
| 2 | `tests/autotuner/test_tier1_review_batch.py` | N rows written, portfolio_correlation on row 0 only, failure isolation, market-hours guard |
| 2 | `tests/ai_advisor/test_review_context_assembly.py` | payload includes all running symphonies + correlation metrics + aggregates |
| 3 | `tests/tuning/test_routes.py` | 7 routes, happy + error paths |
| 3 | `tests/tuning/test_save_validation.py` | allowlist, range, type, NaN/inf, SQL-injection probe |
| 3 | `tests/tuning/test_ai_auto_lock.py` | post-AI-accept `locked_vars` contains the key |
| 3 | `tests/tuning/test_ai_gates_not_bypassed.py` | C2 gates all 3 invoked on accept path |
| 4 (Playwright) | `tests/tuning/tuning_page.spec.py` | scope picker, table render, edit+save, AI button + cost modal, lock semantics matrix, portfolio banner, per-row chips |

### Risks flagged for the Quad kickoff

1. **Daily-return data source for the correlation matrix.** `analytics.compute_aggregate_returns` exists but the 60-day pairwise correlation needs per-symphony daily return series. Test-writer must verify the data source (likely `chart_archive` keyed by `(date, symphony_id)`, or daily-aggregated `simple_return`/`time_weighted_return`) is queryable BEFORE writing `test_correlation_matrix.py`. If a symphony has < 30 days of history, NaN-pad and document the fallback in the Pydantic model.
2. **`symphony_logic.get_condensed_logic` Composer API calls during Tier-1 batch.** In-process cache resets when the EOD subprocess exits, so every Friday batch fetches N times. Confirm Composer rate limits accommodate this at expected N ≤ 10; if not, persist a daily on-disk cache keyed by `(date, symphony_id)`.
3. **Autotuner writeback semantics change is one line but in the EOD path.** `quant-code-reviewer` must verify no downstream consumer of `save_symphony_strategy` implicitly depended on the "all keys overwritten" behavior.
4. **`autotune_reviews.portfolio_correlation` dedup ordering.** RED test must assert deterministic alphabetical ordering by `normalized_name`, not insertion-order accident.
5. **Tier-1 review cost & wall time.** N=10 symphonies × ~100KB prompts × Opus 4.7 with 30s timeout = ~5 min worst case in the EOD subprocess. Acceptable; but Phase 5 must include a real-budget dry-run with mocked Anthropic responses to verify the batch loop's failure isolation under timeout.

### Model routing for Quad agents

Per global model-routing guidance:
- `quant-test-writer` → `sonnet`
- `implementer` → `sonnet`
- `quant-code-reviewer` → `sonnet`
- `flask-dashboard-specialist` → `sonnet`
- Consult-only `sqlite-specialist` / `optuna-specialist` → `sonnet`

### Dispatch readiness

Gate-1 and Gate-2 are both captured in this document. Dispatch is gated on:
- This document merged to `main`
- User explicit go-ahead on Quad kickoff
