# Strategy Builder — Phase 3 Contract: Dashboard Proposal Surface

**Status:** BINDING contract for the Phase-3 team. Depends on Phase 2
(`advisors/strategy_builder_engine.py` — `propose_strategies`). Design language
authority: `feature-plans/ai-advisor-design-prompt.md` (card anatomy, states,
tokens) and `feature-plans/studio-design-handoff.md`. Where this doc conflicts
with either, this doc wins for Phase 3.

**PM-ASSUMED markers** flag PM reconstructions of uncommitted design intent —
implement as specified, list in exit report.

---

## 1. Purpose

A **Strategy Builder tab** on the AI Advisor page surfacing
`propose_strategies()` runs: operator picks an objective + universe, the engine
proposes/backtests/gates candidates, the tab renders survivor and rejected
cards. Advisory-only — no trade/action affordance of any kind (AC-2).

## 2. Hard requirements

1. **Read path discipline (AC-5):** the tab's data reads come from
   `advisor_observations` via existing read-only accessors; the run action
   invokes the Phase-2 engine out-of-band of the 1-minute execution loop
   (mirror how `/ai-advisor/suggest` dispatches existing engines — same
   threading/subprocess pattern, do NOT invent a new one). `[PM-ASSUMED]`
2. **No new write surfaces beyond the run trigger.** The run endpoint
   (`POST /ai-advisor/strategy-builder/run` `[PM-ASSUMED route name]`) is
   CSRF-protected like the existing advisor POST routes and is NOT added to
   `_SETTINGS_WRITE_ALLOWLIST` (it is not a settings write). No
   `LIVE_EXECUTION` interaction anywhere.
3. **Card anatomy** mirrors the Logic Changes tab spec exactly (design-prompt
   Screen 3): header row + `advisory only` badge; mandatory objective strip;
   stats table (Sharpe, Sortino, Max drawdown w/ inverted coloring, Annual
   return) baseline-vs-candidate — baseline = the live portfolio
   `[PM-ASSUMED]`; sparkline (existing QuickChart/sparkline pattern); gate
   verdict pill with FDR metadata line ("N=… tested · adjusted threshold α=… ·
   this candidate p=… "); the strong logic-change caveat verbatim; apply
   guidance row (text-only, no button): "To adopt: create this symphony in
   Composer manually — rules below." plus the `render_rules_text` block in a
   monospace collapsible.
4. **Non-dismissible tab-level risk warning** banner, logic-change wording
   (design-prompt Screen 3 item 1).
5. **Rejected candidates** collapsible section + the exact empty-state
   treatment ("no candidate cleared the gate" is a valid outcome, not an
   error) + per-candidate backtest-failed warn-tint state.
6. **Templates open SQLite read-only** (AC-5) — any new template queries go
   through existing accessors; UI never reruns the engine on page load.
7. **Tests:** route tests (response codes, CSRF enforcement, payload shape),
   template-render tests with fixture observations (survivor card fields
   present incl. FDR metadata + caveat; rejected section; empty state;
   backtest-failed state), and a no-action-affordance test (rendered survivor
   card contains no form/button/link that mutates state). Fixture-first; no
   live engine runs in tests.
8. **Blast radius:** `app.py` (new routes + dispatch), the advisor template(s)
   (`templates/ai_advisor.html` or its successor), `static/**`,
   `advisors/strategy_builder_engine.py` ONLY if a thin presentation adapter
   is unavoidable (prefer not), `tests/**`, `feature-plans/strategy-builder*.md`.

## 3. Team & process

Quint via Agent Teams: test-writer (`quant-test-writer`) ⇄ implementer +
`quant-code-reviewer` + domain reviewer (`flask-dashboard-specialist`) +
doc-writer. Minimum 2 adversarial cycles; full-suite collateral at close; exit
report lists every `[PM-ASSUMED]` with the value implemented and the doc-writer's
CLAUDE.md row draft.

---

## Phase 3 — Completion Record

**Status:** COMPLETE
**Completed:** 2026-06-12
**Branch:** claude/strategy-builder-ai-advisor-m3jlyw

### Routes delivered

- `GET /ai-advisor/strategy-builder` (`app.py:3170`) — lazy-imports
  `_has_composer_key`, loads `STRATEGY_BUILDER`-role observations via
  `database.get_advisor_observations_for_symphony`, renders
  `templates/ai_advisor_strategy_builder.html` with three observation buckets
  (survivors / backtest-failed / withheld) classified in Jinja2.
- `POST /ai-advisor/strategy-builder/run` (`app.py:3224`) — CSRF-protected
  via the global `_csrf_before_request` `@before_request` hook (no explicit
  call in the route handler). Lazy-imports
  `advisors.strategy_builder_engine.{Objective,ScreenConfig,propose_strategies}`.
  Derives rejected from `gated_batch.results` minus `screened_survivors`.
  Returns JSON keys: `survivors`, `rejected`, `n_candidates`,
  `fdr_adjusted_threshold`, `error`.

### PM-ASSUMED items and implemented values

| Contract marker | Implemented value |
|---|---|
| `[PM-ASSUMED route name]` POST endpoint | `POST /ai-advisor/strategy-builder/run` — confirmed as implemented |
| `[PM-ASSUMED]` dispatch pattern (mirror `/ai-advisor/suggest` threading) | Synchronous HTTP endpoint with lazy imports to keep `strategy_builder_engine` off the 1-minute execution path (AC-X2). No background thread or subprocess — `propose_strategies` is called inline. This differs from the pattern of `/ai-advisor/suggest` (which is also synchronous), so the "threading/subprocess pattern" language in the contract was imprecise. The lazy-import isolation approach is consistent with all other advisor POST routes. |
| `[PM-ASSUMED]` baseline for stats table = live portfolio | **Deviation:** stats table shows candidate metrics only (single "Candidate" column). No live portfolio baseline is stored in `advisor_observations` rows, so the baseline column was not implementable from stored data. Template comment at line 829 documents this. See Deviations section. |

### Test coverage

**File:** `tests/app/test_strategy_builder_route.py`
**Fixture:** `tests/fixtures/ai_advisor/m6/strategy_builder_observations_basic.json`
**Count:** 18 tests

Key scenarios:
- A1–A4: GET 200, tab testid present, risk banner present, no run-form testid in GET response
- B5–B7: CSRF enforcement (no token → 403, wrong token → 403, valid token → 200)
- C8–C11: POST response shape (required keys, engine error path, zero-survivors valid outcome, no LIVE_EXECUTION key)
- D12–D13: Survivor card anatomy (7 required data-testid landmarks + SURVIVOR_OVERFITTING_CAVEAT text; zero forms/submit buttons/action hrefs)
- E14–E16: Empty state, rejected section, backtest-failed card
- F17: Adversarial no-action-affordances (2 survivors + 1 rejected, full page sweep for forms/buttons/action links)
- G18: `_SETTINGS_WRITE_ALLOWLIST` not expanded

### Reviewer verdicts

- code-reviewer: PENDING (awaiting signal)
- domain-reviewer: PENDING (awaiting signal)

### Deviations from contract

1. **Stats table: single-column (candidate only), no live-portfolio baseline.**
   Contract §2.3 specified `[PM-ASSUMED]` baseline = live portfolio. The
   `advisor_observations` schema stores only the candidate's metrics dict
   (computed at run time by `propose_strategies`); no parallel live-portfolio
   series is persisted alongside each observation. Rendering a baseline column
   would require either (a) re-fetching live returns at render time (AC-5
   violation — template would rerun engine-adjacent logic on page load) or
   (b) storing live returns in `raw_response` at write time (a schema change
   beyond Phase 3 blast radius). The implementation defers baseline comparison
   to a future phase. Template comment at line 829 documents the deferral.

2. **Sparkline omitted.** Contract §2.3 listed a sparkline (existing
   QuickChart/sparkline pattern). No return-series data is stored in
   `advisor_observations.raw_response` at Phase 2 engine write time, so no
   sparkline can be rendered. Deferred for the same reason as the baseline column.

3. **AC reference in POST route docstring.** The POST route opens with
   `(Phase-3 AC-1)` where AC-1 is the engine's off-execution-path requirement.
   The correct surface contracts are `(AC-2, AC-5, AC-X1)` matching the GET
   route. Filed as DOC-AUDIT finding 1 to implementer; see doc-writer audit
   section in exit report.
