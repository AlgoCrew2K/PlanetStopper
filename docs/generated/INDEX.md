# Planet Stopper -- Generated Module Reference Index

**Last regenerated:** 2026-06-13 (spa-port cycle — Strategy Builder folded into unified AI-Advisor SPA as 6th tab; standalone template deleted; sbRunAnalysis/openChatWithArtifact moved to static/ai_advisor.js)

All pages in this directory are auto-generated from source. Do not hand-edit generated sections. Sections marked `<!-- manual -->` are preserved across regenerations.

---

## Module Index

| Module | File | Description | Last Updated |
|--------|------|-------------|--------------|
| `ai_advisor` | [ai_advisor.md](ai_advisor.md) | Claude-backed config advisor: context assembly, per-symphony assessment, structured-output Claude call, safety gates (7-item allowlist, risk-direction check, OOS re-validation), and Cycle-1 multi-lens scaffold (5 honest-availability stub lens blocks + citation convention) | 2026-06-10 |
| `alpha_bot_execution` | [alpha_bot_execution.md](alpha_bot_execution.md) | Core per-cycle execution engine: fetches live Composer state, runs per-symphony exit decisions with regime-exit adjustment and per-symphony live-mode dispatch, calls autotuner post-market | 2026-06-02 |
| `app` | [app.md](app.md) | Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor routes (single-page SPA at /ai-advisor, 6 in-place tabs including Strategy Builder as the 6th), CSRF infrastructure, settings write paths, daemon singleton lifecycle | 2026-06-13 |
| `autotuner` | [autotuner.md](autotuner.md) | Optuna walk-forward optimizer: 250-day window, CPCV folds (N=6, k=2, 15 splits, 5 paths), CRRA-EU objective, Harvey & Liu BHY haircut, CSCV PBO acceptance gate, and NN1 spec-freeze enforcement | 2026-06-02 |
| `database` | [database.md](database.md) | SQLite state management: schema, 31 migrations (001–031), all read/write accessors, and pytest sentinel guard in _db_file() that structurally prevents tests from writing to the production DB | 2026-06-10 |
| `engine/exit_authority` | [engine_exit_authority.md](engine_exit_authority.md) | Display helpers for the exit-authority badge and restart-notice context (decision-path functions removed in Sprint 3 SITE-C1) | 2026-05-27 |
| `math_engine` | [math_engine.md](math_engine.md) | Pure risk-math primitives: trailing-stop mechanics, sqrt-time squeeze (1-sqrt(1-t)), CRRA-EU utility, CVaR diagnostics, PBO (CSCV), regime-match guard, Monte Carlo gating, VWAP signals, 6-layer exit resolver | 2026-06-02 |
| `reporting` | [reporting.md](reporting.md) | Discord webhook notifications and QuickChart-embedded EOD post-mortem generation | 2026-05-27 |
| `synthetic_history` | [synthetic_history.md](synthetic_history.md) | 250-day Alpaca historical fetcher with parallel download, file cache, and eligibility guards -- feeds the autotuner replay | 2026-06-02 |
| `advisors/advisor_chat` | [advisors_advisor_chat.md](advisors_advisor_chat.md) | Explain-only chat backend (M5): scopes client artifacts to the known M1–M4 allowlist (with Cycle-1 ADD_CANDIDATE + citation fields), calls Claude to explain a surfaced artifact, enforces hard boundary against any write/trade/config-mutation path | 2026-06-10 |
| `advisors/divergence_explainer` | [advisors_divergence_explainer.md](advisors_divergence_explainer.md) | Sprint 3 Stream B producer: surfaces two independent CVaR window values; permanently forbids signed divergence quantities | 2026-05-27 |
| `advisors/overfitting_conscience` | [advisors_overfitting_conscience.md](advisors_overfitting_conscience.md) | Sprint 3 producer: characterises overfitting risk via S-counter vs N_effective; verdicts CLEAR / WATCH / BREACH | 2026-05-27 |
| `advisors/spec_critic` | [advisors_spec_critic.md](advisors_spec_critic.md) | Sprint 3 producer: critiques Phase-1 spec bundle structural integrity (facet completeness, freeze-discipline validity, age, phase-scope leaks) | 2026-05-27 |
| `static/ai_advisor.js` | [static_ai_advisor_js.md](static_ai_advisor_js.md) | Client-side AI Advisor SPA: in-place tab switching (initTabSwitcher), suggestion card rendering with per-symphony assessment block, accept/reject lifecycle, autotune run feed, symphony selection, Strategy Builder tab functions (sbRunAnalysis, openChatWithArtifact) | 2026-06-13 |
| `market_calendar` | *(no generated page)* | Market session state helpers — `get_market_state`; imported by `app.py` | — |
| `composer_backtest` | *(no generated page)* | Composer backtest client; imported by `advisors/asset_swap_engine.py` | — |
| `regime_classifier` | *(no generated page)* | Regime classification helpers; label cache consumed by `database.save_regime_label` | — |

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

## Deleted Templates

The following templates were deleted as dead code after their content was folded into the unified AI-Advisor SPA at `templates/ai_advisor.html`. Their GET routes 302-redirect to `/ai-advisor`; the templates are never rendered. Do not recreate any of them.

**Advisor-cleanup cycle (2026-06-10)** — 4 per-tab advisor templates deleted after the 5-tab SPA migration (DE-ADV-007):
- `templates/ai_advisor_correlations.html` (deleted)
- `templates/ai_advisor_asset_swaps.html` (deleted)
- `templates/ai_advisor_logic_changes.html` (deleted)
- `templates/ai_advisor_chat.html` (deleted)

**Spa-port cycle (2026-06-13)** — standalone Strategy Builder template deleted after fold-in as the 6th tab (DE-SPA-001):
- `templates/ai_advisor_strategy_builder.html` (deleted)

---

## Architecture Notes

- **Two-DB pattern:** State DB (`alphabot_state.db`, this module index) and Optuna optimization DB (`optuna_studies.db`) are never cross-joined in application code.
- **Advisor wall:** All Advisor DB reads go through `database.advisor_ro_query`. Direct connection access from Advisor code is prohibited and CI-enforced.
- **NN1 spec-freeze:** `autotuner.OPTUNA_SEARCH_SPACE_KEYS` must never contain `gamma`, `utility_family`, `wealth_argument`, `generator_family`, `horizon_convention`, or `lambda`.
- **Return units:** `synthetic_history.py` emits returns in **percent**. The CRRA-EU branch converts at its boundary via `RETURN_PCT_TO_FRACTION = 100.0`.
- **Per-symphony live mode:** `database.set_symphony_live_mode` / `get_symphony_live_mode` (migration 030) + `POST /api/symphony-settings/<name>` (CSRF-protected). Default is dry-run (0).
- **DB isolation guard:** `database._db_file()` raises `RuntimeError` under pytest if path resolves to `alphabot_state.db`. `tests/conftest.py` `pytest_configure()` hook sets a session temp path before any module import.
- **AI Advisor SPA:** All 6 advisor panels render from a single `templates/ai_advisor.html`; tab switching is in-place JS (`initTabSwitcher` in `static/ai_advisor.js`). The 4 old per-tab templates were deleted in the advisor-cleanup cycle (2026-06-10); the standalone strategy-builder template was deleted in the spa-port cycle (2026-06-13).
- **Multi-lens scaffold (Cycle-1):** Five lens blocks (`technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals`) are added to the `assemble_advisor_context` output dict. All return `available=False` in Cycle-1 (stubs). The honest-availability contract and citation convention (`build_citation`) are established; fast-follow producers connect real sources. Two new advisor roles (`MARKET_PRISM`, `ADD_CANDIDATE`) added to `_ADVISOR_ROLES` with `is_advisory_only=1`. See `DE-ML-001` through `DE-ML-004` in `DECISIONS.md`.
- **Strategy Builder (spa-port cycle):** `STRATEGY_BUILDER` observations are prefetched by `ai_advisor_tab()` and passed as `sb_observations`/`sb_card_artifacts` to the unified template. `sbRunAnalysis` and `openChatWithArtifact` moved from the deleted standalone template's inline script into `static/ai_advisor.js`. The POST action route (`POST /ai-advisor/strategy-builder/run`) is unchanged. See `DE-SPA-001` in `DECISIONS.md`.
