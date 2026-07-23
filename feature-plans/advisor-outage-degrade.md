# Feature Plan — Strategy-Builder Compile: Degrade-on-Outage (not fail-closed)

**Status:** ready
**Branch:** `fix/advisor-outage-degrade` (off origin/main `28f83e82`)
**Origin:** advisor-remediation-r1 surfaced this defect (r1-test root-cause during the CI credential-less investigation, disclosed + deferred by PM for a scoped cycle). It is the SAME honesty theme as R1: a Composer outage currently makes Strategy Builder silently emit "0 candidates," indistinguishable from "the gate rejected everything" — misleading the operator.
**Advisory-only, off-execution-path.** Ships DIRECT to origin/main via FF after PM live gate (no PR).

## Summary
`advisors/plan_tree_compiler.py`'s tradeability-repair loop calls the Composer `/backtest` endpoint (wired as `backtest_fn=run_backtest` since R1 AC-12) to parse tradeability errors and prune untradeable tickers. When that call fails for a **genuine 400 gate/tradeability rejection**, dropping/pruning is CORRECT. But when it fails for an **infrastructure/transport reason** (connection refused, DNS failure, timeout, 5xx, non-HTTP error — i.e. Composer is unreachable, not rejecting), the loop currently ALSO drops the plan → `CompileResult(tree=None)` → the run emits zero candidates with no distinguishable reason. A real Composer outage or droplet network loss therefore silently zeroes Strategy-Builder output. This cycle makes the compiler DEGRADE on infra-unavailability (emit the already-`validate_tree`-valid tree, flagged as tradeability-unverified) and surface an HONEST, distinct run-level signal, while preserving the current prune/drop behavior for genuine 400 rejections.

## Acceptance Criteria
- **AC-1** — `compile_plan`'s repair loop DISTINGUISHES a genuine HTTP-400 tradeability/gate rejection (parseable Composer error envelope) from an infrastructure/transport failure (connection error, timeout, DNS failure, HTTP 5xx, or any non-parseable/non-200-non-400 result from `run_backtest`). Classification is explicit and tested.
- **AC-2** — On an INFRA/transport failure, `compile_plan` returns the `validate_tree`-valid tree (does NOT return `tree=None` / drop the plan), carrying a machine-readable marker that tradeability was NOT verified (e.g. `CompileResult.tradeability_unverified=True` + a `reason` token like `"backtest_unavailable"`). The tree is otherwise unchanged from its pre-repair validated form.
- **AC-3** — On a genuine HTTP-400 tradeability rejection, the CURRENT prune/retry/drop behavior is byte-for-byte PRESERVED (this is correct — the ticker really is untradeable). Existing 400-path tests stay green unchanged.
- **AC-4** — The run-level result (`strategy_builder_engine`/route response) surfaces a DISTINCT, honest outcome when candidates were emitted tradeability-unverified due to backtest unavailability — e.g. a run-level `backtest_unavailable` flag + operator-readable notice ("N candidate(s) could not be tradeability-checked — Composer backtest unavailable"), rendered distinctly from "0 passed the gate" and from a normal survivor/rejection. The operator can never again see a silent zero on an outage.
- **AC-5** — Honest empty-state preserved everywhere: absent/null degrade fields render nothing (no fabricated strings); the marker only appears when the outage path actually fired.
- **AC-6** — D-1 / never-raises contract preserved; the compiler + engine remain off-execution-path and advisory-only; no change to the trade path (`alpha_bot_execution.py`, `math_engine.py`) whatsoever.
- **AC-7** — Tests: (a) infra/transport error (mocked `run_backtest` raising a transport error / returning a 5xx/connection-error result) → tree emitted + `tradeability_unverified` + run-level `backtest_unavailable` surfaced; (b) genuine 400 → prune/drop preserved; (c) success → unchanged; (d) all pass BOTH with-creds AND credential-less (empty-string method) so this can't regress on CI's credential-less runners. **Tests mock the network seam — NEVER hit the live Composer API** (the R1 fix + the "no unmocked live API in tests = real money" rule).

## Architecture
- Primary change: `advisors/plan_tree_compiler.py` — the repair loop's error-classification branch. Add an infra-vs-400 classifier reading the `run_backtest` result/exception; on infra → degrade (return validated tree + marker) instead of drop.
- `advisors/composer_backtest_client.py` — confirm `run_backtest`/`BacktestResult` exposes enough to distinguish transport error from a parsed 400 (it may already carry a status/error-class; extend minimally if not, additively).
- `advisors/strategy_builder_engine.py` — thread the per-candidate `tradeability_unverified` up into the run-level result (a `backtest_unavailable` rollup flag + count), analogous to the R1 `screens_skipped`/`mode_notice` fields.
- Route (`app.py` SB run route) + `static/ai_advisor.js` `sbRunAnalysis()` — surface the run-level `backtest_unavailable` notice distinctly (mirror the R1 `mode_notice`/`screens_skipped` render pattern; honest empty-state).
- Provenance: `CompileResult.reason` token `"backtest_unavailable"` (distinct from the existing `market_cap_scheme_deprecated`, grammar-422, tradeability-400 reasons).

## Edge Cases
- Transport error on the FIRST backtest call (no prior successful prune) → emit the un-pruned validated tree, flagged. Transport error MID-repair (after some prunes) → emit the partially-pruned validated tree, flagged (the prunes done so far are valid; the remaining check couldn't run).
- A 5xx from Composer = infra (degrade), NOT a 400 (prune). A 429 rate-limit = infra-transient (degrade or bounded-retry-then-degrade — decide in TDD; do NOT drop).
- Genuine 400 with an UNPARSEABLE envelope (can't extract the bad ticker) → today's behavior (drop with a grammar/parse reason) is preserved — this is NOT the outage path.
- Batch: if SOME candidates hit infra-error and others succeed, the successful ones gate normally; the infra ones are emitted unverified + counted in the run-level `backtest_unavailable` rollup.

## Security Considerations
- No credential/secret handling change. Tests must NOT embed live creds or hit live APIs (mock the seam).
- The degraded tree is emitted tradeability-UNVERIFIED — the UI must be honest that Composer (the operator's own apply step) remains the final tradeability arbiter; do not imply the tree is tradeability-checked.

## Testing Strategy
- Unit: `plan_tree_compiler` infra-vs-400 classification + degrade-vs-prune behavior, mocking `run_backtest` to raise transport errors / return 5xx / return a real 400 envelope / return success.
- Integration: `strategy_builder_engine` run-level rollup surfaces `backtest_unavailable`; route JSON carries it; JS renders it distinctly.
- Cross-env: every test passes with-creds AND credential-less (empty-string method — `.env` auto-refills unset vars).
- Golden: a fixture for the transport-error envelope + the 400 envelope.
- Run local `-n0` + `ALPHABOT_TEST_MEM_CAP_GB` + scratch DB_PATH; PM gate = CI (`-n2`) + first-hand render check (drive an outage-simulated SB run OR confirm the render path).

## Scope Boundaries
- IN: compiler degrade-on-infra + run-level honest signal + UI surface + tests.
- OUT: any change to the genuine-400 prune logic (preserved); any trade-path change; R2's reasoning-pipeline port; retry-policy overhaul (a single bounded 429 retry is allowed but not a new retry framework).
- ALSO IN (doc-writer, folded from R1): apply the committed R1 CLAUDE.md key-files corrections `docs/audit-inputs/claude-md-corrections-r1.md` §1-4/6/7 (§5/§8 NO-CHANGE) to `.claude/CLAUDE.md`, PLUS document this cycle's changes. PM reviews the applied CLAUDE.md before commit.
