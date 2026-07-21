# Feature: Display-Truth Cluster (F-011, F-014, F-016, F-018, F-025, F-026, F-027)
Status: ready
Created: 2026-07-21

## Summary
Seven confidence-program findings sharing one theme: the engine computes correct numbers, but the display/labeling layer misrepresents them. Audit provenance: `docs/audit/confidence-program/INSTITUTIONAL-READINESS-REPORT.md` @ branch `audit/confidence-program` (readable at `.claude/worktrees/confidence-program/...`) — quote it for any spec ambiguity. Central verdict context: "computation sound, presentation defective." NO engine/trade change anywhere in this cluster.

- **F-011 (LOW, 1-line):** `static/index.js:1193` reads non-existent `data.symphonies` → section-count badges (active/standby) deterministically overwritten to 0. Fix: read the real field (`data.state`).
- **F-014 (MEDIUM):** `static/index.js:919-923` — the hero "CUMULATIVE · LIFETIME" stat renders the 30-DAY WINDOWED VW value (client prefers `/api/strip/30d` on the closed/frozen path) instead of the account-lifetime value from `/api/state` (34.11% shown vs 34.72% true at audit time). A time-bomb: invisible only while 30d ≥ dataset age; worsens as history grows. Fix: LIFETIME stat sources the lifetime value; windowed values stay clearly windowed.
- **F-016 (MEDIUM, fleet-wide):** `static/index.js:927` — null "TODAY Bot" coerced via `||0` to a false "+0.00%" (portfolio header + all 11 cards), inconsistent with the em-dash empty-state used elsewhere. Fix: honest empty-state (em-dash) for null; 0 renders as 0 only when genuinely 0.
- **F-018 (MEDIUM — a real wrong number, NOT cosmetic):** `analytics.py:1753-1754` — the WINDOWED cumulative-return/guard-alpha headline mixes a 10-symphony `cr_if_held` anchor with an 11-symphony `windowed_alpha` → the tool's CORE value metric (guard-alpha, "GUARD ALPHA 30D") is systematically UNDER-reported ~2× on every window. **Basis decision is SETTLED (operator-ratified class): unify on the 10-SYMPHONY-CONSISTENT basis** — follow the existing `get_portfolio_cumulative_return` convention (exclude the TWR-fallback symphony). Do NOT re-open the basis question.
- **F-025 (MEDIUM — most operator-visible):** the hero chart plots a valid shadow_history day-by-day cumulative (≈−3%) directly beneath the VW-lifetime scalar headline (≈+34%) — different metric, opposite sign, ~12× magnitude — with BOTH chart axes hidden (`display:false`). A naive glance reads "the tool contradicts itself." Data is CORRECT; presentation misleads. Fix: show the y-axis AND distinctly label the chart's metric (e.g. "day-by-day cumulative since <start>, shadow-history basis") so the two numbers can't be conflated. Do NOT change the plotted series.
- **F-026 (LOW):** `app.py:1110` — `"hist_source": ps.get("hist_source", "post_mortem")` silently mislabels correct shadow_history chart data as "post_mortem" (the closed-market path never sets the key). Fix: set `hist_source` explicitly where the arrays are built; the default must not lie.
- **F-027 (MEDIUM, fires every run):** `advisors/build_plan_generator.py:1376` hardcodes `template_id="community"` — violating the documented contract (CLAUDE.md: provenance tags are built-new/atlas-suggested, "never 'community'"); the correct value already sits in `params["provenance"]` (:1381) but is not the surfaced tag; `objective` blank on all 112 community rows (243 total, 0 ever tagged "atlas-suggested"). Fix: surface `template_id="atlas-suggested"` (the existing constant) + populate the `objective` key. Advisory-only.

## Acceptance Criteria
- [ ] AC-1 (F-011): section-count badges render the real active/standby counts from the field `/api/state` actually returns; a regression test pins the correct field name (static-source or fixture-driven).
- [ ] AC-2 (F-014): the hero "CUMULATIVE · LIFETIME" value comes from the LIFETIME source on BOTH market-open and closed/frozen paths; a windowed value can never render under the LIFETIME label. Any stat that renders a windowed value is labeled as windowed.
- [ ] AC-3 (F-016): null TODAY values render the honest em-dash empty-state (matching the existing convention), never "+0.00%"; genuine 0.0 still renders "+0.00%". Fleet-wide (portfolio header + per-card).
- [ ] AC-4 (F-018): the windowed guard-alpha/cumulative-return headline computes BOTH terms on the 10-symphony-consistent basis (`get_portfolio_cumulative_return` convention); golden-fixture test with known per-symphony series proves the mixed-basis understatement is gone (old formula's output pinned as the WRONG value, new as RIGHT). Math-layer rule: named constants/comments, no magic numbers.
- [ ] AC-5 (F-025): hero chart y-axis visible + an explicit metric label distinguishing the chart's day-by-day cumulative basis from the scalar headline basis; plotted SERIES byte-unchanged (presentation-only).
- [ ] AC-6 (F-026): `hist_source` is set explicitly at array-build time to the true source ("shadow_history" where that's the source); the misleading "post_mortem" default cannot be served for shadow_history data.
- [ ] AC-7 (F-027): community-sourced candidates surface `template_id="atlas-suggested"` (existing constant, not a new string) + a populated `objective`; built-new provenance unchanged; test derives expectations from the documented contract, not from current behavior.
- [ ] AC-8 (blast radius): NO changes to `alpha_bot_execution.py`, `math_engine.py`, `reporting.py`, `autotuner.py`, ai_advisor gates, `_SETTINGS_WRITE_ALLOWLIST`, or any schema. The engine's own uses of `cr_if_held`/alpha (if any) are traced before touching analytics.py:1753 — F-018's fix is display-analytics ONLY.
- [ ] AC-9 (regression): full `tests/ui/`, `tests/dashboard/`, `tests/app/` display suites + `tests/js_syntax/` green `-n0`; existing tests asserting the OLD wrong behaviors are rewritten (not deleted) with why-comments.

## Architecture
- `static/index.js`: :1193 field-name fix (F-011); :919-923 lifetime-source fix (F-014); :927 null-vs-zero honest empty-state (F-016); hero-chart config axis+label (F-025 — the Chart.js config lives here).
- `analytics.py` ~:1753-1754: both terms on the 10-sym basis (F-018). Nothing else in analytics changes.
- `app.py` ~:1110: explicit `hist_source` (F-026). No route logic changes.
- `advisors/build_plan_generator.py` :1376-1381: surface the existing provenance value + objective (F-027).
- `templates/index.html` only if a label element is needed for F-025/F-014.
- PM live E2E at the gate = local flask render harness (F-023 pattern): DB copy, Playwright — badges non-zero, LIFETIME label shows the lifetime value, null-day renders em-dash, chart axis+label visible, `/api/state` hist_source truthful; F-018 verified via the golden test + an API recompute check.

## Edge Cases
- F-016: distinguish `null`/missing from genuine `0.0` — only null gets the em-dash.
- F-014: when history < 30d the two values coincide numerically — the test must pin the SOURCE, not the value.
- F-018: windows with zero triggered symphonies → headline degrades honestly (existing empty-state), not NaN.
- F-025: axis formatting must not clip small magnitudes (chart values ≈ ±few %).
- F-027: rows with a missing `params["provenance"]` (if any) must not crash — fall back to the built-new tag only where genuinely built-new.

## Security Considerations
No new inputs; read-only surfaces; no secrets; advisory-only for F-027. No live-API calls in tests (standing rule: never `-o addopts=""`; append `-n0`).

## Testing Strategy
- RED (quant-test-writer): JS static-source pins (precise regex, not substring — F-023 lesson) for F-011/F-014/F-016/F-025 client changes; route/fixture tests for F-026 (`hist_source` truthful on the closed-market path) and F-027 (provenance surfaced, objective populated — fixture-derived, contract-anchored); golden-fixture math test for F-018 (both bases computed from a known series; old mixed-basis output asserted GONE). Blast-radius grep: every consumer of the F-018 headline fields, `hist_source` readers, `template_id` readers (ai_advisor.js renders it), tests pinning old behaviors.
- All `-n0`; both ruff gates; LF blobs (git ls-files --eol); node --check via the parametrized js_syntax module (no per-file additions).

## Decisions
| Decision | Rationale |
|----------|-----------|
| One cycle for all 7 | Audit's own fix-plan clusters them (frozen-path trio = one rendering pass; provenance pair = trivial; F-018+F-025 complete the display-truth story); surfaces don't overlap other cycles. |
| F-018 basis = 10-sym-consistent | Operator-ratified decision class, PM-owned, SETTLED — do not re-litigate. |
| F-025 = presentation-only | Audit refuted the wrong-series hypothesis; the series is correct. Axis + label, nothing else. |
| F-027 uses the existing constant | Contract says "atlas-suggested" exists as the canonical tag; no new strings. |

## Scope Boundaries
- IN: the 7 findings above, their tests, docs.
- OUT: F-024 (double-"+" glyph — still deferred cosmetic); F-020 (drill-down UI = ops cluster); F-001 (Discord label heuristic = reporting, separate); any engine/trade/reporting.py change; schema changes; the ops cluster (F-1/F-005/F-010/F-030).
