# Feature: Advisor Gate-3 Direction Fix + Real Holdout Split (F-013)
Status: ready
Created: 2026-07-20

## Summary
`ai_advisor.revalidate_suggestion_oos` (Gate-3 on the operator-Accept path, ai_advisor.py:2096) is INVERTED. `autotuner.run_simulation` returns a NEGATED objective (`return -total_guard_alpha`, autotuner.py:~1949) — smaller = better — but the gate passes on `patched_oos_alpha > baseline_oos_alpha` (ai_advisor.py:~2184), so it greenlights the candidate with LESS guard-alpha and blocks genuine improvements; both `detail` strings describe the comparison backwards. Additionally the function's "OOS" claim is false: both `run_simulation` calls evaluate the FULL 125-day replay window (the same window the autotuner trains on) — full-window in-sample, no holdout. Confirmed unfired in production (`llm_suggestions` table has zero rows — no operator Accept has ever reached Gate-3), so this is a pre-fire defuse: fix the direction, make the evaluation a real holdout comparison, and make every operator-facing string honest. Audit provenance: F-013 (conf-method flag → PM falsification-read → conf-truth reachability trace, HIGH-unfired).

## Acceptance Criteria
- [ ] AC-1 (direction): a suggestion whose patched strategy produces STRICTLY MORE guard-alpha than baseline on the evaluation window → `passed=True`; STRICTLY LESS → `passed=False`. Pinned via a seam fake mirroring the real contract (fake returns `-GA` for controlled GA values, matching `return -total_guard_alpha`).
- [ ] AC-2 (tie): exact tie still fails (strict improvement required — autotuner strict-positive cascade rule preserved).
- [ ] AC-3 (honest fields + strings): the returned dict's semantics are made honest — the `detail` PASSED/FAILED strings correctly describe which candidate had more guard-alpha, and no field/name/docstring claims "OOS alpha" for a value that is neither OOS (pre-split) nor alpha (raw negated objective). Whether the team returns re-negated guard-alpha values or relabeled raw values is the implementer's design call — but the persisted/displayed consumers (app.py accept route, `record_llm_suggestion` writer, any chat artifact) must be verified compatible in the same cycle (blast-radius grep + tests).
- [ ] AC-4 (real holdout): the Gate-3 comparison is evaluated on a HELD-OUT tail segment of the replay history — not the full training window — with a purge gap between train-tail and holdout, mirroring the repo's existing fold conventions (60/20/20 + PURGE_DAYS in `advisors/backtest_gate_engine.py` / autotuner CPCV constants). Both baseline and patched strategies are evaluated on the SAME holdout slice. Named constants, source comments (no magic numbers — math-layer rule).
- [ ] AC-5 (fail-closed edges): non-finite objective from either run (`inf`/`nan`), empty `acc_sym_ids`, or degraded/empty holdout slice → `passed=False` with an honest `detail` (never a crash, never a silent pass) — D-1 contract preserved.
- [ ] AC-6 (blast radius): Gate-3 remains BLOCKING on the accept path; Gates 1/2/4 byte-unchanged; no engine/trade-path change (`alpha_bot_execution.py`, `math_engine.py` untouched); `llm_suggestions` schema unchanged; no `_SETTINGS_WRITE_ALLOWLIST` change.
- [ ] AC-7 (regression): existing Gate-3 / accept-route tests updated to the corrected contract; full `tests/ai_advisor/` + accept-route suites green `-n0`.

## Architecture
- `ai_advisor.py` `revalidate_suggestion_oos` (:2096-2131 docstring + :2170-2205 body): (1) comparison direction fixed; (2) holdout slicing of `history_data` before the two `run_simulation` calls (slice ONCE, pass the same slice to both — apples-to-apples preserved); (3) docstring + `detail` strings rewritten to actual semantics; (4) returned-dict field semantics per AC-3 with consumer verification.
- Holdout mechanics: `history_data` is the 125-day synthetic replay (`generate_synthetic_history`). Slice a tail segment as holdout (sized per the repo's existing test-fold fraction convention, e.g. the 20% test split in `backtest_gate_engine._fold_transform_single`, with `PURGE_DAYS`-style gap); evaluate both strategies only on the holdout. `run_simulation` accepts `history_data` as an argument, so slicing at the call site requires NO autotuner change — keep `autotuner.py` untouched.
- The math-layer golden-fixture rule applies: the direction fix + holdout selection get fixture tests with known day-series → known guard-alpha outcomes.

## Edge Cases
- Tie exactly at 0 delta → fail (AC-2).
- Non-finite objective (empty holdout, all-degraded days) → fail-closed with honest detail (AC-5).
- Holdout window shorter than the purge gap / too few days → fail-closed with "insufficient holdout history" detail — NEVER silently fall back to full-window in-sample (that would resurrect the false-OOS claim).
- `suggested_value == current value` (no-op patch) → tie path → fail (no validated improvement).
- History cache cold (autotuner hasn't run) → existing degraded-latency path unchanged; if generation fails, D-1 error contract as today.

## Security Considerations
- Advisory-only surface; the config write stays behind the operator Accept + the other gates; nothing enters `_SETTINGS_WRITE_ALLOWLIST`; no `LIVE_EXECUTION` interaction; no new external input.

## Testing Strategy
- **RED (quant-test-writer):** seam-based direction tests (monkeypatch `run_simulation` at the ai_advisor call site with a fake honoring the `-GA` contract): improvement→pass (RED today), worsening→fail (RED today — currently PASSES, the inversion), tie→fail (green anchor); holdout tests: the slice passed to both calls is identical, is a strict tail subset with a purge gap, and full-window evaluation is provably NOT used (assert on the fake's received `history_data` length/dates); fail-closed edges (AC-5); detail-string direction honesty (assert the PASSED string names the genuinely-better candidate); route-level accept-path test proving Gate-3 still blocks the live save on fail (route-level RED lesson: mock at the module-function seam, not the whole module).
- **Blast-radius grep:** all consumers of `revalidate_suggestion_oos` + its returned fields (`passed`/`oos_alpha`/`baseline_oos_alpha`/`detail`) — app.py accept route, `record_llm_suggestion`, chat artifacts, existing tests asserting the OLD inverted contract (must be rewritten, not deleted).
- `-n0` only; both ruff gates; LF endings.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Fix direction AND add the real holdout in ONE cycle | The audit remediation line is "sign-fix + OOS split"; shipping the sign-fix alone would leave the function's name/claims dishonest ("OOS" on in-sample data) — the confidence program's display-truth bar applies to advisor text too. |
| Slice at the call site, keep autotuner untouched | `run_simulation` takes `history_data` as a parameter; slicing before the call fixes Gate-3 without touching the (validated) autotuner objective machinery. |
| Fail-closed on degraded holdout, never fall back to full-window | A silent fallback would resurrect the false-OOS claim under data stress — the exact class of quiet dishonesty this program exists to remove. |
| Seam fake mirrors `-GA` contract rather than asserting raw signs | Tests stay pinned to the REAL documented contract (`return -total_guard_alpha`) and remain valid if the objective is later re-expressed, as long as the contract line is updated with them. |

## Scope Boundaries
- IN: `ai_advisor.revalidate_suggestion_oos` (direction, holdout, docstring/detail honesty, returned-field semantics + consumer compatibility), its tests.
- OUT: `autotuner.py` (objective machinery untouched); Gates 1/2/4; the suggest-path hash-resolution bug (BACKLOG, separate); display cluster findings (F-011/F-014/F-016/F-018/F-025 — next cycle); any engine/trade change; `llm_suggestions` schema.
