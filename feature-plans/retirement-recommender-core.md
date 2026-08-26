# Feature: Retirement Recommender — Core (Phase 2, Cycle 2a)
Status: ready
Created: 2026-08-26

## Summary
An ADVISORY, read-only module that flags a live symphony as a *retirement candidate* when it is BOTH redundant (highly correlated with a sibling) AND the weaker performer of the correlated pair. It computes a pairwise-correlation **screen** over each symphony's continuous live daily returns, a CAGR-dominant **composite performance rank** over live risk metrics, and passes each flagged pair through three conservative **gates** (uncertainty / structural-redundancy / reversibility) before emitting a recommendation. Cycle 2a builds ONLY the deterministic math core + advisory persistence + a read-only API/panel. It has NO execution, liquidation, or trade primitive of any kind, and never surfaces an actionable control. The LLM explainer, the operator approval lifecycle, and the Composer liquidation checklist are Cycle 2b (explicitly OUT here). Design intent (Phase-1 audit, option C): a plain calm-regime correlation point estimate over-prunes crash-diversification, so retirement is never recommended on the point estimate alone — the gates guard estimation error, crisis-understatement, and tail-blindness.

## Acceptance Criteria
- [ ] AC-1 (screen): The module computes pairwise Pearson correlation over each live symphony's **continuous** daily return series, date-aligned, and flags every pair with `correlation >= CORRELATION_SCREEN_THRESHOLD` (0.65) as a redundancy candidate. The correlation input is `{symphony_id: {date: return}}` fed to `advisors.correlation_diagnostic.compute_pairwise_correlations` (reused verbatim, date-keyed). A pair whose `PairResult.correlation is None` (zero-variance / `<2` aligned obs) is NOT a screen hit.
- [ ] AC-2 (basis): The daily return series for BOTH the screen (AC-1) and the composite metrics (AC-3) is the **actual Planet-Stopper-traded** series — element `[1]` (bot / `shadow_return`) of `analytics.get_symphony_bot_and_held_daily_returns(symphony_id, days=...)` — NEVER the sparse trigger-day series (`compute_per_symphony_returns`) and NEVER the if-held counterfactual (element `[2]`). One basis is used throughout. Percent-scale is preserved (fed as-is to `compute_quantstats_metrics`, which divides by 100 internally).
- [ ] AC-3 (composite rank): For each live symphony a CAGR-dominant composite score is computed from its live metrics via `analytics.compute_quantstats_metrics` on the AC-2 series — CAGR (`annualized_return`), Sharpe (`sharpe`), Sortino (`sortino`), Max Drawdown (`max_drawdown`, `<=0` convention), Calmar (`calmar`). Each metric is normalized across the current fleet and oriented so higher = better (deeper drawdown scores worse); the composite is a weighted sum with CAGR weighted strictly higher than each of the other four (named weights, no magic numbers). Higher composite = keep; lower = retirement candidate.
- [ ] AC-4 (candidate selection): Within a flagged pair, the **lower-composite** member is the retirement candidate. Ties are broken deterministically (lower CAGR first, then lexical symphony_id) so the same inputs always yield the same candidate.
- [ ] AC-5 (uncertainty gate): A pair yields a recommendation ONLY if its correlation estimate is robust — the Fisher-z 95% CI **lower bound** is also `>= CORRELATION_SCREEN_THRESHOLD` AND `n_obs >= MIN_OBS_FLOOR`. Otherwise: no recommendation for that pair (fail-closed).
- [ ] AC-6 (structural-redundancy gate): A pair yields a recommendation ONLY if it is redundant **across regimes** — the correlation within the stressed sub-window (the highest-volatility / deepest-drawdown subset of the aligned days) is also `>= STRESS_REDUNDANCY_THRESHOLD`. A calm-only correlation that collapses under stress → no recommendation (the sibling provides crash-diversification). When BOTH symphonies' `logic_holdings` are populated (market hours), holdings/asset overlap is recorded as a corroborating signal; when either is empty (off-hours / flat), the gate degrades to correlation-only and still applies. If the stressed sub-window has too few points to estimate (`< STRESS_MIN_OBS`), the gate fails-closed (no recommendation).
- [ ] AC-7 (reversibility / safety boundary): The module contains NO trade / order / liquidation / deploy / `LIVE_EXECUTION` primitive and never writes settings. Recommendations persist advisory-only (`insert_advisor_observation` forces `is_advisory_only=1`). Structurally enforced by an adversarial source-scan test.
- [ ] AC-8 (persistence): Each run persists its recommendations as `advisor_observations` rows via `database.insert_advisor_observation(advisor_role="RETIREMENT_RECOMMENDATION", subject_type="symphony", subject_id=<candidate>, symphony_id=<candidate>, verdict=<"retire_candidate">, raw_response=<full evidence dict>)`. `raw_response` carries: the pair, `correlation`, CI bounds, `n_obs`, both members' composite scores + per-metric values, each gate verdict, the stressed-window correlation, holdings-overlap (or `null`), and a `basis_label`. The new role is **NOT** added to `_ADVISOR_ROLES` (app.py) — it stays out of the Overview observations loop. No schema migration (advisor_role is free-text).
- [ ] AC-9 (read-only API): `GET /api/retirement-recommendations` returns the current recommendations as JSON with an honest empty-state (`{"recommendations": []}`), auth via the global `_auth_before_request` hook (401 for unauthenticated XHR), NaN/Inf sanitized to `null` before `jsonify`. It is NOT in `_SETTINGS_WRITE_ALLOWLIST` and has no `LIVE_EXECUTION` interaction. It mirrors `api_incubation` (app.py:3734).
- [ ] AC-10 (read-only panel): A minimal, non-interactive, server-rendered section on the AI Advisor tab lists the current recommendations (candidate, sibling, correlation, composite gap, gate summary) with a clear honest empty-state when there are none. NO approve/reject/any actionable control (that is 2b). All fields HTML-escaped, no `| safe`.
- [ ] AC-11 (empty/degenerate): `<2` symphonies, no pair `>= 0.65`, all-thin, or all-uncorrelated → honest empty result, never an error. A symphony with ANY `None` among the 5 metrics is INELIGIBLE to be a retirement candidate (fail-closed) but may still be the "keep" member of a pair.

## Architecture
**New module** — `advisors/retirement_recommender.py` (pure-ish; reads via existing accessors, off the 1-minute execution path, never imports `alpha_bot_execution`/`math_engine` trade paths):
- `screen_correlated_pairs(series_by_symphony) -> list[PairResult]` — thin wrapper over `correlation_diagnostic.compute_pairwise_correlations`, filtering to `correlation >= CORRELATION_SCREEN_THRESHOLD`.
- `compute_composite_scores(metrics_by_symphony) -> dict[str, CompositeScore]` — fleet-normalized CAGR-dominant composite; each `CompositeScore` carries the composite + the 5 raw metric values + eligibility (False if any metric `None`).
- `evaluate_uncertainty_gate(pair) -> GateVerdict` — Fisher-z CI lower bound + `n_obs` floor.
- `evaluate_structural_redundancy_gate(pair, stressed_corr, holdings_overlap) -> GateVerdict` — regime-conditioned redundancy (+ optional holdings corroboration).
- `build_recommendations(*, db_file=None, days=RETIREMENT_LOOKBACK_DAYS) -> list[Recommendation]` — orchestrator: pull per-symphony continuous bot series (AC-2), build the date-keyed dict, screen, score, per-pair gate, pick the lower-composite candidate, assemble the evidence dict. Never raises (D-1 honest-degradation contract like the other advisors modules).
- `persist_recommendations(recs, *, db_file=None) -> int` — one `insert_advisor_observation` per rec.
- Named constants (source-commented, no magic numbers): `CORRELATION_SCREEN_THRESHOLD=0.65`, `MIN_OBS_FLOOR`, `UNCERTAINTY_CI_CONFIDENCE=0.95`, `STRESS_REDUNDANCY_THRESHOLD`, `STRESS_MIN_OBS`, `STRESS_WINDOW_FRACTION`, `RETIREMENT_LOOKBACK_DAYS`, and the composite weights `W_CAGR` (dominant) / `W_SHARPE` / `W_SORTINO` / `W_MAXDD` / `W_CALMAR`.

**Persistence**: reuse `database.insert_advisor_observation` (no new DDL; role free-text). Reuse `database.load_state()` for `logic_holdings`. Source series entirely state-DB / filesystem (shadow_history in `alphabot_state.db`); **no cross-DB join** (Constraint #3).

**Route** (app.py): `GET /api/retirement-recommendations` mirroring `api_incubation` — global auth, NaN-sanitized JSON, honest empty-state, NOT in `_SETTINGS_WRITE_ALLOWLIST`. Reads the latest persisted recommendations (or recomputes read-only — implementer's call, but must not write on a GET). **Scheduler**: an off-hours daily tick (mirroring the 03:30 incubation slot pattern in `run_scheduler()`) may be added to persist a fresh run; if scheduling is deferred to 2b, the route recomputes read-only. (Team decides via the RED tests; either is acceptable so long as the GET never writes.)

**Panel** (templates/ai_advisor.html + ai_advisor_tab in app.py): a minimal server-rendered read-only section, mirroring the existing Market-Prism/Correlations render blocks. No new interactivity.

## Design-System Mapping
Project declares NO formal component-library design system for the Flask dashboard; styling is the existing CSS-token convention in `templates/ai_advisor.html` / `static/*.css`. The new panel MUST reuse existing dashboard tokens/classes (the `.prism-*` / correlations-panel section styles as precedent) — no raw hex, no inline colors, mirror an existing read-only section's markup. Empty-state uses the same informative-empty-state pattern as the Market Prism block.

## Edge Cases
- `<2` live symphonies → no pairs → empty result (AC-11).
- No pair `>= 0.65` / all-uncorrelated → empty result.
- Thin history: `n_obs < MIN_OBS_FLOOR` → uncertainty gate fails-closed (no rec).
- Stressed sub-window too thin (`< STRESS_MIN_OBS`) → structural-redundancy gate fails-closed.
- Tied composite within a pair → deterministic tiebreak (CAGR, then lexical).
- A metric is `None` (`<2` finite obs) → that symphony ineligible as candidate (fail-closed), may be "keep" member.
- `correlation is None` (zero-variance) → not a screen hit.
- Off-hours / weekend / flat market → `logic_holdings` empty → holdings-overlap unavailable → gate degrades to correlation-only (must NOT crash or auto-fail).
- Percent vs fraction scale: feed percent-scale returns to `compute_quantstats_metrics` (it divides by 100); do not double-scale.
- Naming inversion trap: use `series[1]` (bot / actual-traded), never `series[2]` (if-held). A test must pin this.
- A symphony appears in the screen but is missing from the metrics map (or vice-versa) → coverage mismatch handled without KeyError.
- Same symphony correlated with several siblings (cluster) → each pair evaluated independently; the same symphony may be flagged in multiple pairs (dedupe on the candidate at persist/render, keeping the strongest-evidence pair).

## Security Considerations
- **Safety boundary (primary)**: the module must never gain a trade/liquidation/deploy path. Adversarial source-scan test (mirror `tests/security/test_frontrunner_no_trade_boundary.py`) asserting no import/call of order/sell/liquidate/deploy/`LIVE_EXECUTION` write paths, and that the role is not wired to any write allowlist.
- **Read-only route**: auth via the global hook (401 unauth); no user-supplied input for 2a (no query params, or an optional read-only `symphony_id` filter validated as a known id); returns only advisory data (no secrets, no credentials).
- **Injection**: no raw SQL — uses parameterized accessors only. No user-supplied file paths / URLs → no path-traversal / SSRF.
- **Data exposure**: `raw_response` contains only computed metrics + symphony ids (no secrets). Error messages must not echo internals (D-1: `type(exc).__name__` only if surfaced).
- **DoS**: bounded work (C(n,2) over a small live fleet, one metrics pass per symphony); no unbounded fan-out.

## Testing Strategy
Unit / golden-fixture (`tests/advisors/test_retirement_recommender_*.py`):
- Composite math: known metric inputs → known normalized composite + ranking; CAGR-dominance (a symphony strictly-worse on all 5 always ranks lower); MDD orientation (deeper drawdown lowers score).
- Screen: `>= 0.65` inclusion boundary; `None` correlation excluded; date-alignment correctness (reuses #137 behavior).
- Uncertainty gate: Fisher-z CI lower-bound boundary; `n_obs` floor; fail-closed below.
- Structural-redundancy gate: calm-only pair (high full-window corr, low stressed-window corr) → NO rec; genuinely-redundant pair (high in both) → passes; stressed-window-too-thin → fail-closed; holdings-overlap corroboration present vs absent (off-hours empty) both handled.
- Property invariants (quant-test-writer, adversarial): adding an uncorrelated symphony never creates a recommendation; a calm-only correlation never yields a recommendation; determinism under input reordering; no recommendation ever targets an ineligible (None-metric) symphony as candidate.
- Fail-closed / degenerate: `<2` symphonies, all-thin, None-metric, zero-variance.
- Safety: no-trade-boundary source-scan (AC-7).
- Persistence: `insert_advisor_observation` called with the right role/subject/raw_response; `is_advisory_only=1`; role NOT in `_ADVISOR_ROLES`.
Route/behavioral (`tests/app/test_retirement_recommendations_route.py`):
- 401 for unauthenticated XHR; 200 + JSON shape when authed; honest empty-state `{"recommendations": []}`; NaN/Inf → null; route NOT in `_SETTINGS_WRITE_ALLOWLIST`; GET performs no write.
- Panel render: section present with recommendations; honest empty-state; all fields escaped (no `| safe`).
Live PM gate (post-merge, PM-run): real render of the panel + a JSON probe against a droplet DB copy with `>=2` symphonies; spot-check that a flagged pair's candidate is the lower-composite member and that a calm-only pair is correctly withheld.
NOTE: run the app/route suites for any app.py change; targeted `-n0` locally (never the uncapped full tree); CI is the full-tree gate.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Basis = continuous bot series (`get_symphony_bot_and_held_daily_returns` element `[1]`), NOT trigger-day | Trigger-day series is sparse/selection-biased (annualizes ~4 samples into absurd CAGRs — flagged in `analytics.py:1698-1707`). Continuous shadow_history is the honest live basis. |
| Rank on actual-traded (`shadow`/bot), not if-held (`live_`/held counterfactual) | Retirement is about how the symphony ACTUALLY performed under Planet Stopper. The `/api/performance` payload labels are inverted (`live_metrics`=counterfactual); use `series[1]`. A test pins this. |
| Correlation screen uses the SAME continuous basis as the composite metrics | One coherent basis; avoids a second selection-biased source and keeps the screen consistent with the ranking. |
| Structural-redundancy = regime-conditioned correlation (primary), holdings-overlap (corroborating, when available) | `logic_holdings` is empty off-hours, so holdings-overlap alone is unreliable; regime-conditioning directly guards the crisis-understatement/tail-blindness Phase-1 flagged. |
| 0.65 = SCREEN (flags a pair), gates decide | Operator ruling + Phase-1: a calm-regime point estimate alone over-prunes crash-diversification. |
| Composite = CAGR+Sharpe+Sortino+MDD+Calmar, CAGR-dominant, fleet-normalized | Operator ruling. Named weights, no magic numbers; tunable. |
| New advisory role NOT added to `_ADVISOR_ROLES`; no migration | Keeps it out of the Overview observations loop; advisor_role is free-text so no DDL. Surfaced via the dedicated route/panel. |
| Fail-closed everywhere (thin/None/stressed-thin/holdings-empty) | A retirement (capital) decision must never fire on weak evidence. |

## Scope Boundaries
- **IN**: `advisors/retirement_recommender.py` (screen + composite + 3 gates + orchestrator + persistence call); advisory persistence via existing `insert_advisor_observation` (no migration); `GET /api/retirement-recommendations` (read-only, mirrors `api_incubation`); a minimal server-rendered read-only panel section; full unit/property/route/safety test coverage.
- **OUT (Cycle 2b or later)**: the LLM explainer over the recommendation; the operator approval/reject lifecycle (a `frontrunner_proposals`-style table + approve/reject routes); the Composer liquidation checklist; ANY execution/liquidation/trade primitive; any interactive control on the panel; changes to `alpha_bot_execution.py` / `math_engine.py` / the 1-minute execution path (ZERO diff there); adding the role to `_ADVISOR_ROLES`.
