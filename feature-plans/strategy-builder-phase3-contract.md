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
