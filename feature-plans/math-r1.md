# Feature: Math Remediation R1 — Replay Fidelity (per-tick lpc + fail-open arm + regime ticks)
Status: ready
Created: 2026-07-17
Program: feature-plans/math-remediation-program.md (charter, committed alongside this plan) · Findings basis: docs/audit/math-audit/VERDICT.md @ audit/app-math (DE-MATH-AUDIT-001) · Design authority: domain correctness (operator delegation 2026-07-17, on record). Authored by the PM under the standing autonomy directive; assumptions are marked [PM-ASSUMED].

## Summary
Make the autotuner's walk-forward replay faithful to production exit-decision semantics. Today the replay optimizes a bot whose Trailing-Stop and Take-Profit exits CANNOT fire (MA-1 CRITICAL): replay holdings carry only ticker+allocation — no `last_percent_change` — so `math_engine.py:1162-1166` excludes them from the MC baseline sum, MC args are day-constant (`synthetic_history.py:428-435`), and the arm band [5,15) (`alpha_bot_execution.py:1318-1321`) vs the exit gate ≥60 (`math_engine.py:552`) are mutually exclusive under a day-constant mc. Consequence the operator lives with: 3 of 6 nightly-tuned params (`TAKE_PROFIT_MC_PCT`, `PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) are objective-inert noise applied to live money. Two further replay/production divergences ride along: the replay drops production's fail-open arming on MC-absent ticks (MA-10, `autotuner.py:1181-1187` vs `alpha_bot_execution.py:1324-1326`), and it hardcodes exit-confirm ticks to 3 instead of the regime-conditional 2/5/3 (F5, `autotuner.py:1228-1235`). F6 is resolved as an input: the droplet runs `EXECUTION_START_TIME='9:35'` (phase-2 check, 2026-07-17) while the replay assumes the code-default 09:30 session start — the replay must honor the same setting. Trade-touching (the autotuner's output is applied to live money) → PR ship path.

## Acceptance Criteria
- [ ] **AC-1 (MA-1 fix):** replay holdings carry a REAL per-tick `last_percent_change`, derived from the replay bar series, stamped before EVERY `run_monte_carlo` call on the replay path (construction sites: `synthetic_history.py:472-477`, `alpha_bot_execution.py:888-894`/`:1557-1560`). Semantics match production's Composer feed: lpc is a FRACTION (production value is dollar-cross-checked in the audit), computed against the same reference price production uses. The day-constant-MC degeneracy is dead: MC output varies within a replay day as lpc varies (the audit's lead probe showed lpc ±2% swings mc 10.3↔96.7 — that sensitivity must now exist in replay).
- [ ] **AC-2 (exit reachability, golden):** a golden canned-day fixture where production semantics fire Trailing-Stop, and one where they fire Take-Profit, now fire the SAME exits in replay (arm band [5,15) and exit gate ≥60 both reachable). The pre-fix never-fires behavior is pinned as the regression case (fixture-documented, not just asserted in prose).
- [ ] **AC-3 (MA-10):** the replay exit tick gains production's fail-open arming on MC-absent ticks — mirror of `alpha_bot_execution.py:1324-1326` (audit rule H-3) at `autotuner.py:1181-1187`; the replay comment that asserts the opposite of production behavior is corrected in the same diff.
- [ ] **AC-4 (F5):** regime-conditional `exit_confirm_ticks` (2/5/3) are passed into the replay (`autotuner.py:1228-1235`) — the replay resolves the SAME regime-dependent tick count production resolves, never a hardcoded 3. Absent/unknown regime label degrades to production's own default, not to a replay-only constant.
- [ ] **AC-5 (F6):** the replay session window honors `EXECUTION_START_TIME` through the SAME config path production reads (`alpha_bot_execution.py:611-616`) — droplet reality is '9:35'; absent env falls back to production's default (09:30) so replay and production can never disagree on session start by construction.
- [ ] **AC-6 (parity battery — the charter's acceptance heart):** a replay-vs-production parity harness drives IDENTICAL canned-day inputs through production exit-decision logic and through the replay path, asserting IDENTICAL decisions tick-for-tick (exit type, tick index, armed/disarmed states). Battery covers at minimum: trailing-stop fire day, take-profit fire day, VWAP-exit day, MC-absent fail-open day, regime-tick-variation days (2/5/3), and a no-exit day.
- [ ] **AC-7 (inert-dims verification):** a bounded walk-forward smoke (small trial count, deterministic seed) demonstrates the three previously-inert dims (`TAKE_PROFIT_MC_PCT`, `PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) now MOVE the objective — non-zero objective variance across values, each factor shown to VARY (the e2e-exam lesson: "N results" can hide a dead factor).
- [ ] **AC-8 (no live-path regression):** ZERO behavior change on the live execution path. Live-path exit decisions on existing golden fixtures are byte/value-identical pre/post; any shared-code refactor is proven zero-diff on live-path outputs. Both the execution AND engine suites run (mocking-consumers lesson).

## Architecture
Surfaces: `autotuner.py` (replay loop: exit tick, confirm ticks, session window — optuna-specialist territory), `synthetic_history.py` + the replay-holding construction sites inside `alpha_bot_execution.py` (`:888-894`, `:1557-1560`) (risk-engine-specialist territory — engine file, but replay-path code; live-path functions untouched), `math_engine.py` read-only (its lpc-exclusion contract at `:1162-1166` is CORRECT — the fix supplies the missing input, never relaxes the contract). Fork base `0626ef86` (== post-R0 origin/main). No schema, no routes, no UI.

**[PM-ASSUMED] lpc derivation:** per-tick lpc in replay = (tick price − prior session close) / prior session close, matching the Composer `last_percent_change` semantic the engine consumes live (fraction vs prior close). The test-writer must verify this against the production fixture semantics (`tests/fixtures/` Composer captures) before encoding, and escalate if the fixture contradicts it — fixture provenance rule, not parser+fixture co-design.

## Design-System Mapping
N/A — no UI surface in this cycle.

## Edge Cases
First tick of a replay day (lpc basis = prior close, not intra-day); missing/gapped bars (holiday, half-day — NYSE calendar respected in canned days); zero prior close / degenerate price data (no div-by-zero — mirror production's guard); MC seed determinism preserved (per-day seed stays reproducible run-to-run); regime label absent → production default ticks; `EXECUTION_START_TIME` absent → 09:30 default parity; holdings legitimately without lpc on the LIVE path (the `math_engine.py:1162-1166` exclusion must keep working for genuinely lpc-less live holdings).

## Security Considerations
No new inputs, routes, credentials, or write paths — replay is an offline computation over local bar data. The only guarded boundary is unchanged: replay never places orders, `is_live` semantics untouched. No injection/authz/exposure surface added.

## Testing Strategy
TDD via /tdd → /tdd-implement → /tdd-finalize, quant-test-writer as adversarial author (math-layer change ⇒ golden-fixture rule applies). RED first on: lpc stamping + intra-day MC variance (AC-1), the two exit-reachability goldens (AC-2), fail-open arm parity (AC-3), regime-tick pass-through (AC-4), session-window env parity (AC-5), the tick-for-tick parity battery (AC-6), the inert-dims objective-variance smoke (AC-7), and live-path zero-diff pins (AC-8). Golden fixtures under `tests/fixtures/math/`. Targeted `-n0` batteries only (238GB lesson); credential-less pass; ruff both gates. Run `tests/execution/` + engine suites for every `alpha_bot_execution.py` touch.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Supply lpc to replay rather than relax `math_engine`'s lpc-exclusion | The exclusion contract is correct and live-safe; the defect is the missing input (audit: production is NOT degenerate) |
| Replay reads `EXECUTION_START_TIME` via production's own config path | Two readers of one env var can never drift; a replay-local mirror constant would re-create F6 |
| Retune NOT in this cycle | R3 owns the retune once R1+R2 make its statistics trustworthy (charter order) |
| PR ship path | Trade-touching: autotuner output is applied to live money — the advisory direct-to-main lane does not apply |

## Scope Boundaries
- **IN**: replay-path fidelity (AC-1..8), the parity harness, the program charter file landing in `feature-plans/`.
- **OUT**: CPCV structure (MA-2/5/9 — R2); disarm band + `MAX_SQUEEZE_FLOOR` + any retune (MA-4/11 — R3); advisor gate engine (MA-3 shipped in R0; MA-8/M3-M5 — R2/R4); MC-persistence render findings (F7) and live-sold $-saved semantics (MAPERF-15) — separate phase-2 items; any live-path behavior change whatsoever.
