# DRAFT: CLAUDE.md key-files row set — Strategy Builder Real (all cycles C1–C5)
# FOR PM TO APPLY post-merge to the primary repo CLAUDE.md
# Do NOT apply this file directly — PM copies the rows below into CLAUDE.md § Key Files table.
# This is a DRAFT; the feature is not merged; PM gates and ships the single PR.

---

## Instructions for PM

Apply ALL rows below during the cycle-end CLAUDE.md pass (post-merge, single PR).

1. **Add new rows** for the four new modules (universe_provider, build_plan_generator, plan_tree_compiler, strategy_builder_scheduler).
2. **Replace the existing `advisors/strategy_builder_engine.py` row** with the updated one below (removes stale "community_candidate_infos" + "Called post-walk-forward from autotuner.py"; adds C4/C5 pipeline description + sole-callers correction).
3. **Update the `app.py` row** — add the strategy-builder route detail and `load_atlas_candidates` reference.
4. **Carry forward** the `claude-md-ai-advisor-row-update-draft.md` (DE-FUND-002 ai_advisor.py row) and `claude-md-build-plan-generator-row-delta.md` reconciliation note (stale autotuner caller) — both still apply.

---

## NEW ROW: `advisors/universe_provider.py`

| `advisors/universe_provider.py` | Tradeable Universe Provider (Strategy Builder Component 1): `get_tradeable_set() -> frozenset[str]` — fetches the full active US-equity tradeable set from `GET https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity` (APCA-API-KEY-ID / APCA-API-SECRET-KEY; **paper host only** — live host `api.alpaca.markets` 401s with paper keys; `ALPACA_TRADING_BASE_URL` constant distinct from data-host `ALPACA_BASE_URL`); filters to `tradable==True` AND `exchange` in {NASDAQ, NYSE, ARCA, BATS, AMEX} (~12,748 symbols); weekly-cached via `atlas_cache.cached_pull` (TTL 7 days, `force_refresh` escape hatch); membership lookup only — NO ranking, NO dollar-volume, NO top-N palette; each weekly snapshot persisted to the warehouse third-DB pattern (`lens_warehouse` style — separate append-only `alphabot_warehouse.db`, `_strip_secrets` applied, no cross-DB join); D-1 / never-raises (source failure → `available=False, reason=type(exc).__name__`); off-execution-path; advisory-only; Composer `/backtest` is the final tradeability arbiter |

---

## NEW ROW: `advisors/build_plan_generator.py`

| `advisors/build_plan_generator.py` | Opus Build-Plan Generator (Strategy Builder Component 2+2b): SDK structured tool-use generation of `N_PLANS_PER_OBJECTIVE=12` diverse objective-shaped build-plans in the build-plan DSL (NOT raw Composer JSON — a constraint-typed 1:1 pre-image of the `symphony_schema` constructor API); `MAX_OUTPUT_TOKENS=16384` (raised from 4096 — DE-SB-GEN-TRUNCATION; empirical worst-case 5,015 tokens/12-plan run; 16384 ≈ 3.3x headroom); `MAX_GENERATION_ATTEMPTS=3` bounded retry fires on `stop_reason=="max_tokens"`, degrades D-1 on exhaustion; `_build_generation_prompt` seam (5 layers: DSL grammar + flat if.condition + compound if_compound union + THREE compiler-verified examples + `_OBJECTIVE_SIGNATURES`); full Composer condition grammar (binary/binary_compound/compound) generation-reachable and schema-constrained; tightened `_EMIT_BUILD_PLANS_TOOL` (kind/scheme/condition-union enum-constrained); AC-8 via `plan_matches_objective()`; AC-9 membership prune + zero-ticker guard + `_collect_condition_tickers` canonical-flat binary-leaf fix; `admit_community_candidates(community_result, objective, *, max_candidates) -> list[CandidateInfo]` — objective-matched Atlas ranking (cut_drawdown=lowest drawdown, vol_mit=lowest vol, lift_ra=best sharpe, diversify=low Jaccard overlap), AC-12 kept-last for missing-stat docs; `load_atlas_candidates(objective, *, max_candidates) -> list[CandidateInfo]` — convenience wrapper (`load_community_strategies(force_refresh=False)` then `admit_community_candidates`); **sole canonical community-admission path** for both the route (`app.py:3807`) and the weekly scheduler (`strategy_builder_scheduler.py:134`); provenance tags: built-new/atlas-suggested; D-1 never-raises; off-execution-path; advisory-only |

---

## NEW ROW: `advisors/plan_tree_compiler.py`

| `advisors/plan_tree_compiler.py` | Plan-to-Tree Compiler (Strategy Builder Component 3): `compile_plan(plan: dict) -> CompileResult` — deterministically translates each build-plan DSL node into a valid Composer `raw_value` tree via `symphony_schema` constructors; full grammar coverage (nested `make_group`, equal/specified/inverse-vol weighting, filters, simple + compound conditions via `make_if`/`make_if_compound`/`make_compound_condition`/`make_binary_compound_condition`); `_has_market_cap(plan)` pre-check drops any plan with `scheme=="market_cap"` before compilation (`CompileResult(reason="market_cap_scheme_deprecated")`) — Composer retired market-cap weighting (HTTP 422, `DE-SB-MARKETCAP-DEPRECATED`); every compiled tree gated by `symphony_schema.validate_tree` before return; tradeability repair loop: parses `composer_backtest_client` error envelope to distinguish grammar-422 (AC-15 path) from tradeability-400 (AC-16 ticker-prune+retry path); bounded repair attempts (`MAX_REPAIR_ATTEMPTS` named constant); unrepairable plans return `CompileResult(tree=None, reason=...)` — run continues; D-1 / never-raises; off-execution-path; advisory-only; `symphony_schema.KNOWN_STEPS` constructor count stays at 16 (no `wt-marketcap`) |

---

## NEW ROW: `advisors/strategy_builder_scheduler.py`

| `advisors/strategy_builder_scheduler.py` | Weekly Strategy Builder Scheduler (Strategy Builder Component 4, AC-18): `run_weekly_build()` — runs the real dual-mode builder (built-new + atlas-suggested) for all four objectives once per ISO week; same-ISO-week idempotency (`_already_ran_this_week()` seam, patchable); per-objective `build_plan_generator.load_atlas_candidates(objective)` injection INSIDE the per-objective loop (each objective ranked by its own stat — cut_drawdown=lowest drawdown, vol_mit=lowest vol, etc.); inner try/except degrades to `community_candidates=[]` on Atlas error (built-new always runs); `MAX_ATTEMPTS=3` bounded retry per objective (failed objective logged, run continues to next — stricter than `prism_scheduler.py` exit-1 pattern); all observations keyed to `symphony_id=""`; D-1 / never-raises; off-execution-path; advisory-only; invoke: `python -m advisors.strategy_builder_scheduler` |

---

## REPLACEMENT ROW: `advisors/strategy_builder_engine.py`

Replace the ENTIRE current `advisors/strategy_builder_engine.py` row with:

| `advisors/strategy_builder_engine.py` | Phase-2 Strategy Builder proposal engine (real C1→C2→C3 pipeline since C4): `propose_strategies(objective, universe, screen_config, live_returns, symphony_id, *, incumbent_oos_alpha, default_oas_alpha, community_candidates) -> ProposalRun` — generates candidate trees via the real C1 (universe) → C2 (build_plan_generator) → C3 (plan_tree_compiler) pipeline, backtests via `composer_backtest_client` (1 req/s), gates via Harvey-Liu BHY FDR + C5b PBO veto + real SPY-OOS baseline, applies `ScreenConfig` post-gate screens, persists survivors as advisory observations (`is_advisory_only=1`); `universe=[]` → C1 self-source from `universe_provider.get_tradeable_set()` (route + scheduler both pass `[]`); `community_candidates=` kwarg accepts `list[CandidateInfo]` from `build_plan_generator.load_atlas_candidates(objective)` — pooled with built-new in ONE FDR batch (AC-21); **C5b cull (autotuner-grade):** `math_engine.compute_pbo` wired per candidate (veto `pbo>0.5`; `pbo=None` passes); real SPY-OOS baseline sourced by `run_backtest` on 100%-SPY tree once per run (`_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA=float("+inf")` → conservative WITHHOLD on failure); `rejection_reason` field: `None`/`"pbo_veto"`/`"below_spy_alpha"`/`"fdr_not_winner"`; **C5 provenance (2026-06-20):** `community_candidate_infos` adapter DELETED (70 lines, zero production callers after route rewire); engine docstring updated to point to `build_plan_generator.load_atlas_candidates`; provenance tags: built-new/atlas-suggested (never "T1"–"T7", never "community"); `MAX_CANDIDATES_PER_RUN=30`; `MAX_COMMUNITY_CANDIDATES_PER_RUN=20`; sole production callers: `app.py:3813` (route) + `strategy_builder_scheduler.py` (weekly); `autotuner.py` does NOT call `propose_strategies`; off-execution-path; advisory-only; never raises |

---

## AMENDMENT: `app.py` row (strategy-builder route detail)

The existing `app.py` row in CLAUDE.md should be updated to add the C5 strategy-builder route detail. Append the following to the existing `app.py` row description (after the existing `**Guard-Alpha $-saved panel**` sentence and before the closing `|`):

> ; **Strategy Builder on-demand route (C5, 2026-06-20, commit 1d5dd48):** `POST /ai-advisor/strategy-builder/run` (`app.py:3759`) rewired to the real dual-mode builder — lazy-imports `build_plan_generator.load_atlas_candidates(objective)` (CC-2) for objective-matched Atlas injection; `load_atlas_candidates` is D-1 + bill-protected (`force_refresh=False`); calls `propose_strategies(community_candidates=...)` with both built-new + atlas-suggested in ONE FDR batch (AC-21); response JSON carries `template_id: "built-new"|"atlas-suggested"` provenance on every survivor/rejected candidate (AC-13); `run.error` branch sanitized to static `"strategy-builder-error"` token (AC-23 — never echoes `run.error` verbatim which is set from `str(exc)`); CSRF-protected, advisory-only, not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION`

---

## Stale-claim reconciliation (carry forward from earlier cycle drafts)

**In the `advisors/strategy_builder_engine.py` row** — the claim "Called post-walk-forward from autotuner.py" is STALE and does NOT appear in the replacement row above. Sole production callers are the route and the weekly scheduler. `autotuner.py` does NOT call `propose_strategies`. This was flagged in `claude-md-build-plan-generator-row-delta.md` (prior cycle); the replacement row above incorporates the correction.

**In the `advisors/ai_advisor.py` row** — the DE-FUND-002 update is in `claude-md-ai-advisor-row-update-draft.md` (prior cycle) and should also be applied.
