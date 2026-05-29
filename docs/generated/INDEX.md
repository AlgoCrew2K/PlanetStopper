# Planet Stopper -- Generated Module Reference Index

**Last regenerated:** 2026-05-27 (Sprint 3 full regen)

All pages in this directory are auto-generated from source. Do not hand-edit generated sections. Sections marked `<!-- manual -->` are preserved across regenerations.

---

## Module Index

| Module | File | Description | Last Updated |
|--------|------|-------------|--------------|
| `ai_advisor` | [ai_advisor.md](ai_advisor.md) | Claude-backed config advisor: context assembly, structured-output Claude call, and safety gates (allowlist, risk-direction check, OOS re-validation) | 2026-05-27 |
| `alpha_bot_execution` | [alpha_bot_execution.md](alpha_bot_execution.md) | Core per-cycle execution engine: fetches live Composer state, runs per-symphony exit decisions, calls autotuner post-market | 2026-05-27 |
| `app` | [app.md](app.md) | Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor endpoints, daemon singleton lifecycle | 2026-05-27 |
| `autotuner` | [autotuner.md](autotuner.md) | Optuna walk-forward optimizer with CRRA-EU objective, Harvey & Liu BHY haircut, and NN1 spec-freeze enforcement | 2026-05-27 |
| `database` | [database.md](database.md) | SQLite state management: schema, 25 migrations, and all read/write accessors for the state DB | 2026-05-27 |
| `engine/exit_authority` | [engine_exit_authority.md](engine_exit_authority.md) | Display helpers for the exit-authority badge and restart-notice context (decision-path functions removed in Sprint 3 SITE-C1) | 2026-05-27 |
| `math_engine` | [math_engine.md](math_engine.md) | Pure risk-math primitives: trailing-stop mechanics, CRRA-EU utility, CVaR diagnostic type, Monte Carlo gating, VWAP signals, 6-layer exit resolver | 2026-05-27 |
| `reporting` | [reporting.md](reporting.md) | Discord webhook notifications and QuickChart-embedded EOD post-mortem generation | 2026-05-27 |
| `synthetic_history` | [synthetic_history.md](synthetic_history.md) | 125-day Alpaca historical fetcher with parallel download, file cache, and eligibility guards -- feeds the autotuner replay | 2026-05-27 |
| `advisors/divergence_explainer` | [advisors_divergence_explainer.md](advisors_divergence_explainer.md) | Sprint 3 Stream B producer: surfaces two independent CVaR window values; permanently forbids signed divergence quantities | 2026-05-27 |
| `advisors/overfitting_conscience` | [advisors_overfitting_conscience.md](advisors_overfitting_conscience.md) | Sprint 3 producer: characterises overfitting risk via S-counter vs N_effective; verdicts CLEAR / WATCH / BREACH | 2026-05-27 |
| `advisors/spec_critic` | [advisors_spec_critic.md](advisors_spec_critic.md) | Sprint 3 producer: critiques Phase-1 spec bundle structural integrity (facet completeness, freeze-discipline validity, age, phase-scope leaks) | 2026-05-27 |

---

## Pruned (Sprint 3)

The following modules were deleted in Sprint 3 port-level deprecation and have no doc pages:

| Deleted Module | Reason |
|----------------|--------|
| `engine/dual_altitude` | Removed in SITE-C1 (port-level deprecation) |
| `engine/multi_cycle` | Removed in audit-fix B (port-level deprecation) |
| `port_aggregator` | Removed in audit-fix B (port-level deprecation) |
| `port_selector` | Removed in audit-fix B (`compute_composition_hash` promoted to `database.py`) |

---

## Architecture Notes

- **Two-DB pattern:** State DB (`alphabot_state.db`, this module index) and Optuna optimization DB (`optuna_studies.db`) are never cross-joined in application code.
- **Advisor wall:** All Advisor DB reads go through `database.advisor_ro_query`. Direct connection access from Advisor code is prohibited and CI-enforced.
- **NN1 spec-freeze:** `autotuner.OPTUNA_SEARCH_SPACE_KEYS` must never contain `gamma`, `utility_family`, `wealth_argument`, `generator_family`, `horizon_convention`, or `lambda`.
- **Return units:** `synthetic_history.py` emits returns in **percent**. The CRRA-EU branch converts at its boundary via `RETURN_PCT_TO_FRACTION = 100.0`.
