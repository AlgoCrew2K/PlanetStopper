# Math Remediation R0 — Advisory Quick Wins (veto units + render truth)
Status: ready
Program: feature-plans/math-remediation-program.md (charter @ audit/app-math 677811e2) · Findings basis: docs/audit/math-audit/VERDICT.md (DE-MATH-AUDIT-001) · Design authority: domain correctness (operator delegation 2026-07-17, on record)

## Summary
Fix the CRITICAL advisor-veto unit corruption and the operator-facing render defects on the Performance/History surfaces — the two failure classes the operator sees every day. No engine, no autotuner, no math_engine changes (those are R1–R3). Advisory + dashboard surfaces only.

## Acceptance Criteria
- **AC-1 (MA-3, CRITICAL):** `advisors/backtest_gate_engine.py` converts `dated_returns` percent→decimal at its boundary before `math_engine.compute_pbo` (the "decimal return" contract, math_engine.py:1939-1941). Golden fixture reproduces the audit's flip case: identical data yields PBO≈0.87 (veto) post-fix where production computed 0.17 (pass). All four producers' conventions traced and documented at the boundary.
- **AC-2 (M2):** `_BATCH_PBO_GAMMA` aligned to the frozen THEORY gamma (2.0, `database.py` `PHASE1_THEORY_GAMMA`); the comment cites the REAL constant (the current "autotuner GAMMA = 1.0" citation is nonexistent — lead-grep-verified).
- **AC-3 (MA-6, HIGH):** `/api/performance` scope=symphony sources per-symphony daily series from `shadow_history` per-day rows (the same source class the aggregate scope was moved to per the route's own Finding-4 comment) — NEVER the post-mortem trigger arrays. The dashboard Risk Profile panel (`static/index.js:461` fetch) inherits the fix. Regression pin: a 4-trigger symphony must NOT annualize to triple-digit CAGR from event-sample treatment.
- **AC-4 (MA-7, HIGH):** both `if not dates:` fallbacks in the performance route are scope-gated to `scope=="aggregate"`; symphony scope with no data renders an honest empty state (never portfolio numbers under a symphony's name).
- **AC-5 (ma-perf 03, MED):** ONE window semantic per picker click: "30d/90d/1y/YTD" = CALENDAR windows everywhere (chart, strip, History, Performance). The hero chart's trading-day slicing converts from the calendar cutoff; YTD no longer feeds a calendar count into a trading-day slice. Same-click chart and strip must cover the same period (test-pinned).
- **AC-6 (ma-perf 06, MED — the operator's "TP saved me 10%" sighting):** the History Detail column has ONE semantic: saved-alpha (guard-alpha pp), both intraday and post-15:54. If exit-level return is worth showing, it appears as its OWN labeled value — never silently swapped into the same "+X.XX%" cell.
- **AC-7 (ma-perf 05, MED):** volatility delta renders with correct polarity (bot MORE volatile = negative/red), reusing the existing `invertDelta` pattern from index.js in performance.js.
- **AC-8 (ma-perf 04 + 13, MED):** the strip fallback triggers on `guard_alpha is None` — never on falsy 0.0 (a legitimate $0.00 window renders $0.00); the fallback estimate is day-filtered (no cross-day at_return minus today's current_return arithmetic); the 30d window either uses an honest available-trading-days floor or renders an explicit "insufficient window" state — never a silent fallback (30 calendar days can never meet a 30-trading-day floor; that permanent arming is the bug).

## Architecture
Surfaces: `advisors/backtest_gate_engine.py` (AC-1/2), `app.py` performance/history/strip routes (AC-3/4/5/8), `analytics.py` window helpers (AC-5/8), `static/performance.js` + `static/index.js` + `static/history.js` (AC-3/5/6/7 render), `templates/history.html`/`performance.html` as needed (AC-6 labeling). Fork base f8e6e295 (== post-ship main).

## Edge Cases
Zero-trigger symphonies (AC-4 empty state); single-day windows; symphonies with <2 shadow_history rows (quantstats min-observation gate unchanged); inf/None metric values (existing `_safe` idiom preserved); epoch boundaries in per-symphony series (epoch-additive semantics preserved — verified sound by the audit, must not regress).

## Security Considerations
No new write paths; all routes remain read-only; no credential surface touched; JS changes render-only with `| e`/textContent discipline.

## Testing Strategy
TDD via /tdd → /tdd-implement → /tdd-finalize. RED first on: the PBO unit golden fixture (AC-1 flip case), gamma constant pin (AC-2), per-symphony source swap + event-sample regression pin (AC-3), scope-gated fallbacks (AC-4), same-click same-window cross-surface pin (AC-5), Detail-column single-semantic pin (AC-6), vol polarity (AC-7), None-vs-falsy + day-filter + honest 30d state (AC-8). Targeted `-n0` batteries only; credential-less pass; ruff both gates. Audit probes (scratchpad probe_calmar.py etc.) adapted as regression tests where cheap.

## Scope Boundaries
- NO changes to `autotuner.py`, `math_engine.py`, `alpha_bot_execution.py`, `synthetic_history.py`, `acceptance_gate.py` (R1–R3 territory).
- NO change to the aggregate-scope performance path (already correct per Finding-4) beyond shared-helper refactors proven zero-diff on its outputs.
- Droplet post-mortem regeneration is a DATA operation (operator's --apply paste), not part of this code cycle.
- Ship path: advisory (FF to origin/main after full gates + PM live E2E).
