# Acceptance Criteria: AI Advisor — Full Suite (De-correlation, Asset-Swap, Logic-Change, Chat)

**Status:** GATE-1 APPROVED (2026-05-31) — see Gate-1 Resolutions below. Next: ux design prompt (design-first), then Gate-2 (HOW), then build (M1 first).
**Spec writer:** spec-writer · **Date:** 2026-05-31 · **Tier:** 3 (multi-capability, multi-surface, new external API write-path surface)

## Summary

The AI Advisor is an operator-facing, on-demand assistant on the Flask dashboard (`/ai-advisor`) that helps the operator — who is **not** a quant — improve their Composer.trade symphonies. For any proposed change it runs a single loop: **diagnose → propose → backtest on Composer → run through the offline overfitting gate → surface only the SURVIVORS as recommendations-with-caveats → the operator decides.** It ships four capabilities at two risk tiers: a pure de-correlation diagnostic (🟢), asset-swap proposals (🟢 when gated), logic-change proposals (🟡, highest overfitting risk), and an explain-only chat (🟡). The **hard, non-negotiable boundary** is **advise-only**: the advisor never mutates a live symphony, never places a trade, never writes back to Composer. It runs entirely off the live 1-minute execution path.

## Current State (key finding)

- **An `ai_advisor.py` already exists** (`ai_advisor.py:1-798`) but is a DIFFERENT surface: it proposes edits to the 6–7 Optuna risk-engine CONFIG KNOBS + `MAX_SQUEEZE_FLOOR` (9-key curated allowlist), not symphony logic/asset trees. It calls Claude for structured `ConfigSuggestion`s, enforces an allowlist, cross-checks risk direction, and re-validates via the autotuner OOS gate. The new suite is ADDITIVE alongside it; this module is the coherence anchor for the Claude client pattern, the never-raise graceful-degradation contract, and allowlist input-governance.
- **`/ai-advisor` route is read-only** (`app.py:2227-2491`) with `suggest`/`accept`/`reject` sub-routes; a route-guard test pins the only permitted write (`save_symphony_strategy` config-write), explicitly not a state/trade mutation.
- **Offline gate = `acceptance_gate.py`** `evaluate_acceptance_gate(...)` (`:136-245`): Stage-1 hard vetoes (BHY/Yekutieli FDR, NN1 spec-freeze, look-ahead/purge), Stage-2 fixed-weight survivor panel that can only WITHHOLD. One-directional-brake invariant. **Current signature takes walk-forward parameter-tuning inputs, NOT raw Composer backtest stats** (see Open Q1).
- **Advisor persistence** = `advisor_observations` via `insert_advisor_observation` (always stores `is_advisory_only=1` — structural "never moves money"); reads via the single `advisor_ro_query` wall.
- **Composer client**: `symphony_logic.py` fetches+condenses `/score` tree already; `POST /api/v0.1/backtest` (inline `raw_value`) is NOT yet called anywhere (the load-bearing new surface). Score trees can exceed 1 MB; condensed < 8 KB.
- **Correlation infra is partial/wrong-shape**: `detect_fleet_correlation` is trigger-clustering, not return-series correlation; a return-series cross-symphony/holding correlation MATRIX does not exist yet (capability 1 is genuinely new).
- **API research done** (`feature-plans/ai-advisor-composer-api-research.md`): `POST /api/v0.1/backtest` → Sharpe/Sortino/max-DD/returns/data_warnings/benchmark_comparisons; ~1 req/sec; uses existing Composer key (no new credential).
- **Schema at migration 027** (project CLAUDE.md's "025" is stale); additive-first.

## Acceptance Criteria

### Cross-cutting (the advise-only spine — ALL capabilities)
- **AC-X1** No capability calls a Composer write endpoint (`POST/PUT /symphonies`, `/copy`, `/deploy`, `go-to-cash`) or places a trade. Only reads (`GET /score`) + stateless `POST /api/v0.1/backtest`. Verifiable by static route/import-graph guard.
- **AC-X2** No capability runs on the 1-minute live execution path; `alpha_bot_execution.py` imports nothing from advisor modules. Import-graph test.
- **AC-X3** Every surfaced recommendation persists as an `advisor_observation` with `is_advisory_only=1` + originating `symphony_id`; no write to `symphony_strategies`/`bot_state`/Composer.
- **AC-X4** No Composer API key → clear "advisor unavailable: API key not configured" + writes nothing, never an unhandled error.
- **AC-X5** Backtest failure/timeout/non-200/429 → per-candidate "backtest failed" with reason; one failure never aborts the batch.

### Capability 1 — De-correlation Diagnostic (🟢)
- **AC-1.1** Surfaces pairwise return-correlation across current symphonies (and holdings where data exists), from a return series — distinct from trigger-clustering.
- **AC-1.2** Each figure shows its sample basis (obs count / window) so thin-data estimates are visible.
- **AC-1.3** Empty/degenerate portfolio → well-shaped "insufficient data" state, never a crash or fabricated number.
- **AC-1.4** Runs no backtest, invokes no gate (pure measurement); surfaces the crisis-instability caveat.

### Capability 2 — Asset-Swap Proposals (🟢 when gated)
- **AC-2.1 (operator-initiated)** Operator specifies a swap ("try IALT in place of X in symphony S"); advisor fetches tree → applies swap to variant → backtests → gates → surfaces only if survives.
- **AC-2.2 (advisor-suggested)** Advisor generates swap candidates from correlation analysis + candidate universe (Open Q2), backtests/gates each, surfaces survivors.
- **AC-2.3** Each surfaced swap shows baseline-vs-variant stats + gate verdict + de-correlation rationale; a gate-failed swap is NOT surfaced as a recommendation (may show as rejected w/ reason).
- **AC-2.4** A structurally-invalid variant tree → "could not backtest this variant" without affecting others.
- **AC-2.5** Zero survivors → explicit "no swap cleared the gate this run" (empty is a valid non-error outcome).

### Capability 3 — Logic-Change Proposals (🟡 — highest overfitting risk)
- **AC-3.1** Both modes (operator-initiated tweak; advisor-suggested candidates), each diagnose → propose → backtest → gate → surface-survivors.
- **AC-3.2 (multiple-testing guardrail — MANDATORY)** N backtested logic candidates → acceptance applies a multiple-testing/FDR correction across the FULL set (not per-candidate thresholds). Explicit + tested: raising N raises the bar each must clear. (Mapping onto `acceptance_gate.py`'s BHY/Yekutieli veto = Open Q1.)
- **AC-3.3** Every surfaced logic-change carries an explicit selecting-on-backtest overfitting caveat + post-correction gate verdict; pre-correction-only passers are NOT surfaced.
- **AC-3.4** Never auto-applies; surfacing writes only an advisory observation (AC-X1/X3).

### Capability 4 — Chat (🟡 — explain-only)
- **AC-4.1 (explain-not-advise boundary — MANDATORY)** Chat explains the system (why the gate accepted/rejected, current regime, correlation picture, what a recommendation means). MUST NOT issue trade directives, MUST NOT propose/apply config/logic/asset changes, is not an action surface. Verifiable: chat has no write path / no path to accept/apply routes.
- **AC-4.2** Answers grounded in surfaced advisor data (observations, gate verdicts, diagnostic) — explains existing artifacts, does not generate new unvalidated recommendations.
- **AC-4.3** Chat unavailable (no LLM key / error) → clear "chat unavailable", never a crash, never a fabricated trade instruction.

## Risk Tiers
- **🟢 De-correlation diagnostic** — soundest; pure measurement. BUT correlation estimates destabilize toward 1.0 in crises (when de-correlation matters most). Guide, not guarantee.
- **🟢 Asset-swap (gated)** — sound when the gate holds; every backtest-and-select still exposes the overfitting trap; the gate is resistance, not immunity.
- **🟡 Logic-change** — highest overfitting risk; multiple-testing correction (AC-3.2) is the load-bearing defense; survivors are sound-but-unprovable (a clean gate pass ≠ proof of live edge).
- **🟡 Chat** — low money-risk (explain-only) but LLM-explanation can be confidently wrong; the boundary (AC-4.1) keeps it safe.

## Scope Boundaries
**IN:** all four capabilities on `/ai-advisor`, both proposal modes for caps 2 & 3; inline Composer backtest of variant trees; reuse of offline gate as the recommendation screen (shape TBD, Open Q1); persist recommendations as advisory observations.
**OUT (explicit):** auto-deploy of any kind; advisor placing trades / any Composer write/deploy endpoint; any advisor code on the live 1-minute path; write-back of accepted recommendations to Composer (operator applies manually this cycle — future-cycle candidate); replacing the existing config-knob advisor (additive alongside); the deferred Narrator role.

## Dependencies
- Composer API key (existing `get_composer_headers()`; help article 235) — no new credential.
- Anthropic API key (`ANTHROPIC_API_KEY`) IF chat / advisor-suggested generation use an LLM (already used by config advisor).
- Fixture capture for `POST /api/v0.1/backtest` + `GET /score` variant trees (captured-from-producer or schema-validated; no live calls in tests).
- Possible additive migration `028_…` if recommendations need new fields.
- Resolution of the gate-input contract (Open Q1) before M3/M4 A/C is complete.

## Proposed Build Sequence / Milestones (each independently shippable + verifier-gated)
1. **M1 — De-correlation diagnostic (🟢)** — pure measurement, no backtest/gate. Establishes data layer + dashboard surface with zero overfitting exposure; the analytical foundation asset-swap generation draws on.
2. **M2 — Backtest-and-gate engine (no UI of its own)** — the reusable spine: fetch tree → apply structured variant → backtest → gate → survivor verdict + caveats. Built/verified once (incl. AC-X4/X5 degradation + fixtures) before any proposal UI.
3. **M3 — Asset-swap proposals (🟢)** — both modes on M2; proves the proposal loop on the sounder capability.
4. **M4 — Logic-change proposals (🟡)** — both modes + mandatory multiple-testing correction; lands only after M2's gate + M3's loop are proven.
5. **M5 — Chat (🟡, explain-only)** — explains the artifacts the prior milestones produce; adds no new recommendation surface.

## Gate-1 Resolutions (APPROVED by user 2026-05-31)

User approved the suite, the 5-milestone sequence, and the advise-only spine. Open questions resolved:

1. **Gate-input contract → REUSE `acceptance_gate.py` AS-IS; transform the data to fit.** Each Composer backtest's return series is sliced into the walk-forward fold structure the gate already expects (time-folds → per-fold OOS stats → existing BHY/Yekutieli veto + one-directional brake). One honesty engine; no new gate component. The symphony/backtest data is transformed to fit the gate, never the reverse.
2. **Candidate universe → NO allowlist; the full Composer-tradeable ETF universe is in scope — BUT every swap must be OBJECTIVE-DIRECTED.** The advisor must be solving for a stated objective (reduce a measured correlation, cut drawdown, lift risk-adjusted return), NOT swapping "for vibes." Candidate generation = objective-driven shortlisting (reason about what would address the measured problem → shortlist plausible ETFs → backtest → gate), not brute-forcing the universe. The objective is surfaced alongside each recommendation. This is also the overfitting control: unrestricted universe + backtest-select is the canonical trap; objective-direction bounds the search and the AC-3.2 FDR correction applies across the backtested shortlist. → Refines AC-2.2.
3. **Chat → contextual "chat about THIS", not a general Claude entry point.** Chat is anchored to a specific surfaced artifact (a recommendation, a diagnostic, a gate verdict): "open a conversation about this suggestion" — discuss its rationale, its backtest, why the gate passed/rejected it, "what if I tweaked X." The explain-only boundary (AC-4.1) still holds: no trade directives, no write path, not an action surface. → Refines AC-4.1/AC-4.2.
4. **Volume + window → defaults accepted:** top ~3 survivors per capability per run; backtest over the longest reliable Composer history (validated by the API `data_warnings`).

**Process: DESIGN-FIRST.** Before implementation, a ux-expert authors a Claude Design prompt for the advisor screens → user runs it to update the dashboard design → Gate-2 (the HOW) → then the Toxic-Pair build teams start, M1 first.

---
**Tally:** 22 acceptance criteria (5 cross-cutting + 4 + 5 + 4 + 3 + the mandatory guardrails folded into AC-3.2/AC-4.1) across 4 risk-tiered capabilities + 5 milestones. Blocking clarifying questions: 1 (Open Q1 — gate-input contract; blocks M3/M4 only). 3 non-blocking open questions.
