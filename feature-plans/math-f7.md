# Feature: Math F7 — Honest Post-Trigger MC Display (+ MAPERF-15 staleness tripwire)
Status: ready
Created: 2026-07-18
Program: feature-plans/math-remediation-program.md · Findings basis: docs/audit/math-audit/VERDICT.md @ audit/app-math (ma-core F7 JOINT HIGH-boundary + ma-perf addendum; MAPERF-15 resolved tracks-logic per docs/research/composer/maperf15-post-sale-lpc-semantics.md) · Predecessors: DE-MATH-R0/R1/R2-001 (all shipped) · Design authority: domain correctness (operator delegation, on record). Operator directive 2026-07-18: "Don't stop until this is production level, and accurate." Authored by the PM; assumptions marked [PM-ASSUMED].

## Summary
After a symphony's guard exit fires, the engine keeps computing and persisting a Monte Carlo probability against a FICTIONAL 0% baseline (`alpha_bot_execution.py:1549`, unguarded) — a fabricated statistic rendered live on 3+ operator surfaces: the main table's MC Prob column (whose tooltip additionally missells it as "probability this symphony beats SPY"), the MC dial on every poll, the detail view's Risk Math, and the chart fallback (`templates/table_partial.html:98,181`, `static/index.js:1033-1037`). Diagnostic-only (never money), but it is a number the operator reads that means nothing. Fix: post-trigger, the persisted mc becomes an honest "exited" sentinel (never a fabricated probability), and every consuming surface renders an explicit exited/— state; the tooltip is corrected to what the statistic actually is (probability of the symphony underperforming its own baseline). Rider: the MAPERF-15 passive staleness tripwire (Composer's post-sale `last_percent_change` tracks-logic behavior is undocumented — a cheap detector logs loudly if it ever changes). Display/diagnostic surfaces only; engine file touched but no exit-decision math altered → PR ship path (engine-file caution), no retune content.

## Acceptance Criteria
- [ ] **AC-1 (persist honest):** post-trigger, the engine persists NO fabricated mc — the `:1549` write is guarded so a triggered symphony's `mc_prob` becomes an explicit exited sentinel (`None` + a `triggered`-aware consumer contract, or an equivalent explicit marker — implementer designs, plan-approval ratifies). The live UNTRIGGERED path's mc computation/persistence is byte-identical.
- [ ] **AC-2 (render honest, all four surfaces):** main-table MC Prob column, MC dial, detail Risk Math, and chart fallback each render an explicit exited/"—" state for triggered symphonies — never 0, never a stale number, never blank-that-looks-broken. No `| safe`, textContent discipline.
- [ ] **AC-3 (tooltip truth):** the MC Prob tooltip states the actual statistic (probability of underperforming the symphony's own baseline per the engine's MC), not "beats SPY". Any other surface repeating the "beats SPY" claim is swept and corrected.
- [ ] **AC-4 (MAPERF-15 tripwire):** a passive, off-hot-path staleness check: when a TRIGGERED symphony's `current_return` (Composer lpc) is bit-static across N consecutive engine cycles during market hours (the tracks-account signature; tracks-logic keeps moving), log ONE loud warning naming DE-GUARD-ALPHA-SAVED-001's dependency — never raises, never gates, no schema. Threshold named + source-commented.
- [ ] **AC-5 (no-regression):** live exit-decision math untouched (existing execution goldens byte-identical); the R1/R2 batteries stay green; `math_engine.py` zero diff.

## Architecture
Surfaces: `alpha_bot_execution.py` (the `:1549` persist site + the AC-4 tripwire — risk-engine territory; NO exit-decision logic), `templates/table_partial.html` + `static/index.js` (+ detail/chart render paths as traced — flask-dashboard territory), tests. Fork base `f2932368` (current origin/main). No schema, no routes added, no new write paths.

**[PM-ASSUMED]:** `None`-sentinel + consumer-side `triggered` awareness is preferred over a magic string/number sentinel (matches the engine's existing `mc_available=None` contract class). The implementer's plan traces every consumer of the persisted `mc_prob` (dashboard poll JSON, chart_history, any analytics reader) before choosing — a consumer that chokes on None is a finding, not a excuse for a fake number.

## Design-System Mapping
Existing dashboard idioms only — the exited state reuses the table's existing muted/em-dash styling conventions; no new components.

## Edge Cases
Symphony triggers mid-day then untriggers next epoch (state must recover to live mc); poll races right at trigger time; chart fallback with a mixed history of pre/post-trigger points; JSON serialization of None across the poll payload; the tripwire across market-closed periods (no false "static" alarms when the market legitimately isn't moving — market-hours-gated); dry-run vs live mode (tripwire applies to both; the fabricated-mc fix applies to both).

## Security Considerations
No new inputs or write paths; render changes are output-only with escaping discipline preserved.

## Testing Strategy
TDD via /tdd → /tdd-implement → /tdd-finalize. RED first on: the post-trigger persist guard (golden: triggered → sentinel, untriggered → real mc byte-identical), each of the four render surfaces (route/JSON-level + template assertion per surface), the tooltip text sweep, the tripwire (static-lpc detector fires once, market-hours-gated, never raises), and the AC-5 zero-diff pins. Targeted `-n0`; credential-less; BOTH ruff gates in every GREEN report (standing rule); tests seeding "today" use the ET-trading-day idiom, never `date.today()` (standing rule from the R2 fix-forward).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Honest sentinel over computing a "true shadow" MC | The audit called the 0%-baseline number fabricated; a shadow-portfolio MC is a new statistic needing its own design — out of a display-truth cycle's scope (R4-adjacent if wanted) |
| Tripwire logs-only, never gates | MAPERF-15's behavior is confirmed tracks-logic today; the tripwire insures against silent upstream change without adding a failure mode |
| PR ship path despite display-only semantics | The diff touches the engine file; the PR + full-gate lane is the conservative default for any `alpha_bot_execution.py` change |

## Scope Boundaries
- **IN**: AC-1..5; the tooltip sweep; the tripwire.
- **OUT**: any exit-decision change (R3); any shadow-portfolio MC statistic; MA-4/MA-11 (R3); the retune (R3); advisor re-base (R4); F8's if-held compounding (only load-bearing if a MAPERF-15 fix persists the reconstruction — it doesn't, tracks-logic stands).

## ADDENDUM (PM rulings from f7-review's baseline recon, 2026-07-18 ~02:5xZ)
- **Chart-fallback resurrection = BINDING design constraint (the cycle's sharpest case):** the backward null-scan (index.js ~661-666/~832-848) would resurrect the last PRE-trigger mc as current for an exited symphony — a null-scan cannot distinguish no-data-yet from exited. The render MUST short-circuit on trigger STATE; a mixed pre/post-trigger golden is a required RED case.
- **MC dial stale-skip = named AC-2 hard-fail case:** `!= null → skip update` (index.js:1033-1037) freezes a stale pre-trigger reading on screen; the exited state must actively update the dial.
- **Sentinel = None, CONDITIONALLY ratified:** contingent on end-to-end JSON-null serialization checks (never 0.0-coerced, never key-dropped, Jinja `is not none` + JS `!= null` verified); if the consumer trace finds a genuine None-choker, the codebase's numeric-sentinel idiom (isSentinel |v|>=900) is the ruled fallback — decided at plan approval, never bilaterally.
- **"Already works" is not evidence:** the two surfaces with existing None guards (table column "---", detail Risk Math sentinelToNull) still get tests exercising JSON-serialized None through the REAL path.
- **Persist completeness:** f7-engine's trace must answer whether :1549 is the ONLY fabricated-mc persist (chart_history historical writers matter — the chart surface reads history).
- **Decision-path purity, stated precisely:** the AC-1 guard lives strictly at the persist site; an untriggered symphony's decision path sees byte-identical prob_underperforming; no decision function ever receives a guard-produced None for an untriggered symphony.
- **Tooltip second hit:** .design-handoff's tracked mirror carries the same "beats SPY" claim — fix or explicitly scope out with a stated reason; silence is a finding.
