# Feature: Exit-Tuning Friction Term + Realized-Basis $-Saved + Turnover Surfacing
Status: ready
Created: 2026-07-23

## Summary
Close the transaction-cost gap identified in docs/research/methodology-validation-2026-07.md (internal audit: the exit-parameter tuning loop is 100% gross-of-cost; the "$-saved" headline is decision-time-snapshot based; turnover is unquantified despite `exit_triggers` holding the data). Three additive parts, one cycle: (1) a named, non-tunable friction constant applied per simulated exit round-trip inside the autotuner's replay so the CRRA-EU objective optimizes net-of-friction; (2) a realized-basis $-saved computed from the first post-rebalance observed value, reported alongside (never replacing) the existing snapshot basis; (3) per-symphony exit-frequency/turnover stats read from `exit_triggers`, including an estimated annual friction drag. No live-engine decision-path changes anywhere.

## Acceptance Criteria
- [ ] AC-1: New named constant `SIM_EXIT_FRICTION_PCT` (proposed 0.005, i.e. 0.5% per round-trip [PM-ASSUMED — sourced for consistency from the Composer backtest client's existing `slippage_percent=0.005` assumption at `composer_backtest_client.py:237-296`; operator may override with a measured value]) with a source comment citing both the Composer client assumption and the validation report.
- [ ] AC-2: The friction term is applied in the replay collection path (`autotuner.py:1346-1419`, `_collect_sim_returns`) to each simulated triggered exit as a distinct subtraction — NOT folded into the existing −0.20% deviation-dict penalty (different meaning: modeling-uncertainty haircut vs trading friction; both retain their own named constants).
- [ ] AC-3: `SIM_EXIT_FRICTION_PCT` is NOT in the Optuna search space, NOT env-tunable, and a test asserts it is absent from the tunable-parameter set (a tunable friction would let the optimizer game its own cost model).
- [ ] AC-4: Golden fixtures updated/added: identical replay scenario with friction 0 vs `SIM_EXIT_FRICTION_PCT` shows exactly the expected objective delta; existing objective fixtures re-pinned with the constant's value explicit in the fixture (never silently absorbed).
- [ ] AC-5: Stage-2 post-mortem (16:00 ET, post-rebalance) additionally records, per triggered symphony, the first post-rebalance observed value from existing Stage-2/bot_state data [PM-ASSUMED: no new external API calls — reuse what Stage 2 already fetches], as additive JSON field(s); absent data → field absent, never fabricated.
- [ ] AC-6: `saved_dollars_realized` computed per triggered symphony using the post-rebalance observed value in place of the decision-time snapshot; `/api/guard-alpha-summary` returns BOTH `saved_dollars` (snapshot basis, unchanged math — DE-GUARD-ALPHA-SAVED-001 semantics preserved: if-held from `shadow_history.current_return`) and the realized aggregate with an honest `realized_coverage` count (how many exits have realized data). Dashboard headline shows both bases, labeled ("snapshot basis" / "realized basis, N of M exits").
- [ ] AC-7: When realized data is unavailable for an exit (pre-feature post-mortems, missing Stage-2), that exit is excluded from the realized aggregate and counted in the coverage denominator — never silently substituted with the snapshot value.
- [ ] AC-8: New read-only turnover stats: per-symphony exit count over trailing 30/90/365 days from `exit_triggers` (`migrations/005_exit_triggers.sql`), plus `est_annual_friction_drag_pct = exits_per_year × SIM_EXIT_FRICTION_PCT`, exposed on the same or a sibling GET route and rendered beside the preconditions/turnover area of the Performance tab. Re-entry legs are NOT counted (exit-leg-only; documented limitation — re-entry is implicit in Composer's rebalance and not discretely logged).
- [ ] AC-9: Post-mortem JSON schema change is additive-only; History/Performance tabs and the Discord embed continue to parse old and new files (malformed/old-file-safe, per existing consumer patterns).
- [ ] AC-10: Zero changes to live exit-decision codepaths in `alpha_bot_execution.py`/`math_engine.py` decision functions; a test (or blast-radius assertion in review) confirms the friction constant is referenced only from replay/reporting/analytics surfaces.

## Architecture
- **Replay math**: constant in `autotuner.py` (or `math_engine.py` constants block if convention prefers; source comment either way); subtraction at the simulated-exit accounting site in `_collect_sim_returns`. Blast radius: objective values shift for all future autotune runs → next retune may select different parameters (intended: that is the point). Flag in cycle kickoff per the scheduler gotcha (engine spawn at :00 unaffected — no engine file behavior change).
- **Post-mortem producer**: `reporting.py` Stage 2 — additive fields; Stage-1 freeze math untouched (`reporting.py:50-61` snapshot basis preserved exactly).
- **API**: extend `guard_alpha_summary()` (`app.py:2172`) response additively; turnover either in the same payload or `GET /api/exit-turnover` (implementer's call; both read-only, global auth hook, not in `_SETTINGS_WRITE_ALLOWLIST`).
- **UI**: `dollar-saved-panel` headline gains the second labeled figure; turnover column/section on Performance tab; JS follows the 401-guarded fetch pattern.
- **DB**: no schema migration required (exit_triggers exists; post-mortems are JSON files). If the cycle finds a migration necessary, additive-first NULLable per house rule.
- **Dependencies**: none new.

## Design-System Mapping
No design system declared. House conventions: extend the existing `dollar-saved-panel` markup/classes for the dual-basis headline; Performance-tab table classes for turnover; no inline styles; semantic class names for the coverage annotation.

## Edge Cases
- Friction larger than a simulated exit's gain → negative guard-alpha for that event (correct; must flow through, not clamp at zero).
- Multiple triggers same symphony/day → per-event friction application; realized matching uses the event timestamp, not the date alone where possible.
- Market holiday after trigger (no next-day observation) → realized field waits for first available observation; if none within the post-mortem's horizon, absent (AC-7 path). Check the NYSE calendar behavior in tests (known deploy gotcha class).
- Old post-mortem files without new fields → parsed cleanly, counted as no-coverage.
- `realized_coverage = 0` → realized headline renders "no realized data yet", never $0.00.
- Epoch boundary: realized aggregate is epoch-additive like the snapshot basis (same accumulation semantics — SUM across epochs of per-epoch values).
- Friction constant edge in fixtures: 0.0 must reproduce today's objective exactly (backward-compatibility fixture).

## Security Considerations
- All new/changed routes read-only; no write-path or allowlist changes; no `LIVE_EXECUTION` interaction; auth via global hook.
- Post-mortem JSON is producer-owned (our own daemon) but consumers stay malformed-file-safe (existing hard rule) — treat as untrusted at parse time.
- No raw Composer/Alpaca response bodies logged or echoed (logging-redaction house rule); realized values are derived numbers only.
- No user input on any new surface; XSS surface limited to symphony names already escaped in templates.

## Testing Strategy
- **Golden fixtures (mandatory — math layer)**: friction-off vs friction-on objective delta (AC-4); backward-compat 0.0 fixture; negative-guard-alpha flow-through case.
- **Unit**: constant-not-tunable assertion (AC-3); Stage-2 additive-field writer with missing-data paths; realized-basis math from fixture post-mortems (values derived from fixture, never hardcoded producer literals — house test rule); coverage accounting (AC-7); turnover queries against a seeded temp DB incl. 30/90/365 windows and empty table.
- **Route tests**: extended guard-alpha-summary schema, old+new post-mortem mix, turnover route, honest empty states.
- **Suite scope**: run the execution+engine suites (changes adjacent to `alpha_bot_execution` consumers mock it — known blast-radius class) plus reporting/app suites; full-tree gate before merge per house merge protocol.
- **JS**: extend the parametrized syntax test module.
- **Behavioral (PM live gate)**: droplet dashboard render showing dual-basis headline + turnover with real post-mortems; one real Stage-2 cycle observed writing the new fields (dry-run acceptable if market-closed; check DRY RUN before market-gating the deploy).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Friction value from Composer client's slippage assumption (0.005) [PM-ASSUMED] | Internal consistency: the Strategy Builder gate already validates candidates under this assumption; one friction worldview across both research surfaces until a measured value replaces it |
| Friction is a fixed environmental constant, never tunable | The optimizer must not be able to game its own cost model |
| Keep deviation penalty and friction as separate constants | Different semantics (modeling uncertainty vs trading cost); folding them would destroy the audit trail of both |
| Realized basis reported alongside snapshot basis, not replacing it | Snapshot basis has shipped semantics (DE-GUARD-ALPHA-SAVED-001); dual display is honest and preserves comparability |
| Exit-leg-only turnover | Re-entry is implicit in Composer's daily rebalance and not discretely logged; inferring round-trips from `bot_state["triggered"]` transitions is deferred |
| No new external API integration for fills | Stage 2 already observes post-rebalance state; true fill-level reconciliation would need Composer API research (separate cycle if the dual-basis gap proves material) |

## Scope Boundaries
- **IN**: replay friction constant + application, dual-basis $-saved (producer + API + panel), exit-leg turnover stats + friction-drag estimate, golden fixtures.
- **OUT**: live engine decision changes; Optuna search-space changes; fill-level reconciliation via new Composer/Alpaca endpoints; round-trip (re-entry) turnover inference; Discord embed redesign; retroactive backfill of realized data for historical post-mortems; any DB migration unless the cycle proves one necessary.
