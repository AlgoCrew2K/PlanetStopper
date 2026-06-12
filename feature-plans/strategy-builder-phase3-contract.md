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

### Commit chain

| SHA | Description |
|---|---|
| `8bcd441` | test(strategy-builder-phase3): RED tests — 18 tests, Groups A–G |
| `ec78325` | feat(strategy-builder-phase3): GET + POST /ai-advisor/strategy-builder routes |
| `3c1998b` | feat(strategy-builder-phase3): template initial — card anatomy, states, JS trigger |
| `90690f7` | fix(ui): correct POST route docstring AC reference + stats table comment |
| `f34f3f4` | test(strategy-builder-phase3): adversarial cycle 2 — 10 tests, Groups H–P |
| `72c5646` | fix(ui): store fdr_adjusted_threshold in persist + caveat verbatim + FDR display |

### Routes delivered

- `GET /ai-advisor/strategy-builder` (`app.py:3170`) — lazy-imports
  `_has_composer_key`, loads `STRATEGY_BUILDER`-role observations via
  `database.get_advisor_observations_for_symphony` (filtered to
  `advisor_role == "STRATEGY_BUILDER"` in the route to prevent role-bleed),
  renders `templates/ai_advisor_strategy_builder.html` with three observation
  buckets (survivors / backtest-failed / withheld) classified in Jinja2.
- `POST /ai-advisor/strategy-builder/run` (`app.py:3224`) — CSRF-protected
  via the global `_csrf_before_request` `@before_request` hook (no explicit
  call in the route handler, per AC-2 / AC-X1). Lazy-imports
  `advisors.strategy_builder_engine.{Objective,ScreenConfig,propose_strategies}`.
  Derives rejected from `gated_batch.results` minus `screened_survivors`.
  Computes `fdr_adjusted_threshold = fdr_q / c(n)` (Yekutieli harmonic factor)
  inline. Returns JSON keys: `survivors`, `rejected`, `n_candidates`,
  `fdr_adjusted_threshold`, `error`. No `LIVE_EXECUTION` interaction anywhere.
  Not added to `_SETTINGS_WRITE_ALLOWLIST`.

### Blast radius

Files touched beyond Phase 3 contract §2.8 base scope:

- `advisors/strategy_builder_engine.py` — `_persist_survivor` extended with
  `fdr_q` kwarg; computes and stores `fdr_adjusted_threshold` in
  `raw_response` so the dashboard template renders the numerically correct
  Yekutieli-adjusted threshold rather than the nominal `fdr_q`. Additive only:
  no change to gate logic, screen logic, or public `propose_strategies`
  signature. Within contract §2.8 ("thin presentation adapter … if
  unavoidable").

### PM-ASSUMED items and implemented values

| Contract marker | Implemented value |
|---|---|
| `[PM-ASSUMED route name]` POST endpoint | `POST /ai-advisor/strategy-builder/run` — confirmed |
| `[PM-ASSUMED]` dispatch pattern (mirror `/ai-advisor/suggest` threading/subprocess) | Synchronous HTTP with lazy imports (AC-X2). No background thread or subprocess — `propose_strategies` is called inline. All other advisor POST routes follow the same pattern; the "subprocess" language in the contract was imprecise. |
| `[PM-ASSUMED]` baseline for stats table = live portfolio | **Deviation** — candidate metrics only (single column). See Deviations §1. |

### Test coverage

**File:** `tests/app/test_strategy_builder_route.py`
**Fixture:** `tests/fixtures/ai_advisor/m6/strategy_builder_observations_basic.json`
**Count:** 28 tests (all GREEN)
**Full suite:** 5932 passed, 6 skipped, 0 failed

| Group | Tests | What is locked |
|---|---|---|
| A1–A4 | 4 | GET 200; tab testid; non-dismissible risk banner; no run-form testid in GET |
| B5–B7 | 3 | CSRF enforcement: no token → 403, wrong token → 403, valid token → 200 |
| C8–C11 | 4 | POST response shape; engine error path; zero-survivors valid outcome; no LIVE_EXECUTION key |
| D12–D13 | 2 | Survivor card: 7 required data-testid landmarks + SURVIVOR_OVERFITTING_CAVEAT; zero forms/submit buttons/action hrefs |
| E14–E16 | 3 | Empty state; rejected-candidates section; backtest-failed card |
| F17 | 1 | Adversarial: 2 survivors + 1 rejected, full-page sweep for forms/buttons/action links |
| G18 | 1 | `_SETTINGS_WRITE_ALLOWLIST` not expanded |
| H19–H20 | 2 | AC-X2: `strategy_builder_engine` not at module scope (AST); `propose_strategies` imported lazily inside run handler |
| I21 | 1 | Role-bleed guard: only `STRATEGY_BUILDER` observations reach template |
| J22 | 1 | Unknown objective string defaults to `diversify` — no 400/500 |
| K23 | 1 | Missing JSON body handled gracefully — no 500 |
| L24 | 1 | GET route never calls `insert_advisor_observation` (read-only contract) |
| M25 | 1 | `fdr_adjusted_threshold` is a positive float when `n_candidates > 0` |
| N26 | 1 | Run trigger button is `type="button"` not `type="submit"` (template static analysis) |
| O27 | 1 | `fdr-metadata` renders numeric `α=` value — content precision, not just testid |
| P28 | 1 | Caveat text contains exact wording: "Overfitting risk cannot be eliminated — only resisted." |

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
   to a future phase. Template comment documents the deferral.

2. **Sparkline omitted.** Contract §2.3 listed a sparkline (existing
   QuickChart/sparkline pattern). No return-series data is stored in
   `advisor_observations.raw_response` at Phase 2 engine write time, so no
   sparkline can be rendered. Deferred for the same reason as Deviation 1.

3. **Dispatch pattern: synchronous HTTP with lazy imports, not subprocess.**
   Contract §2.1 said `[PM-ASSUMED]` "mirror how `/ai-advisor/suggest`
   dispatches existing engines — same threading/subprocess pattern." All advisor
   POST routes (including `/ai-advisor/suggest`) are synchronous HTTP endpoints
   with lazy imports for AC-X2 isolation — there is no subprocess or background
   thread in any of them. The contract language was imprecise. The implemented
   pattern is correct and consistent with all existing advisor surfaces.

4. **AC reference in POST route docstring (RESOLVED).** Initial implementation
   cited `(Phase-3 AC-1)` where AC-1 is the engine off-execution-path
   requirement. Fixed at commit `90690f7` to `(AC-2, AC-5, AC-X1)` matching
   the GET route. Resolved — not open.
