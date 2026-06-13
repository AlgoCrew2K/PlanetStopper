# Planet Stopper — Project-Local CLAUDE.md
# EXTENDS ~/.claude/CLAUDE.md — do NOT duplicate global content.

## Project Identity
- **Name:** Planet Stopper
- **Purpose:** Institutional-grade algorithmic risk engine; monitors live Composer.trade portfolios; executes intelligent trailing stops ("Guard Alpha")
- **Stack:** Python 3 / Flask monolithic daemon; SQLite (state DB + optimization DB); Optuna; Composer.trade + Alpaca + Discord integrations
- **Workflow:** Hard fork — never re-syncing upstream; full autonomy within global guidelines
- **PM autonomy (operator directive 2026-06-11):** never pause to ask the operator mid-backlog — proceed phase to phase until the backlog is exhausted or blocked on an operator-only input (credentials, scope change). Document assumptions as `[PM-ASSUMED]` in contract docs instead of asking.
- **PM status integrity (operator directive 2026-06-12):** NEVER report team/agent status without validating it first in the same turn — check the process table (`pgrep`), file mtimes, and `git log` before every claim. Inference is never reported as fact; if something is unverified, it is labeled unverified. A missing completion notification is NOT evidence of progress.
- **Roadmap (in scope):** historical analysis, live-vs-Planet Stopper comparison stats, charts/graphs in Flask dashboard, pytest + GitHub Actions test harness

## Key Files (quick reference for workers)
| File | Role |
|------|------|
| `app.py` | Flask dashboard + minute-by-minute scheduler; `_DISMISS_EXECUTOR` (background dismiss thread) + `_FLUSH_STATE_LOCK` (flush serialization); spawns `alpha_bot_execution.py` at :00; CSRF infrastructure (`_validate_csrf`, `_csrf_before_request`); `_SETTINGS_WRITE_ALLOWLIST` gates the two guarded write paths; AI Advisor routes — unified SPA at `GET /ai-advisor` renders all 6 tabs; all 5 GET sub-routes 302-redirect to `/ai-advisor`; POST action routes unchanged; `POST /ai-advisor/strategy-builder/run` (CSRF-protected, dispatches `propose_strategies` via lazy import, returns survivor/rejected/FDR JSON — advisory-only, no `LIVE_EXECUTION` interaction, not in allowlist) |
| `alpha_bot_execution.py` | Core engine — per-cycle execution; wired to canonical THEORY spec bundle via `get_or_create_phase1_theory_bundle_id` |
| `math_engine.py` | Risk math: volatility scaling, sqrt-time squeeze (`1-sqrt(1-t)`), parabolic ratchet, MC gating, VWAP, breakeven, exit confirm, regime-match guard; CRRA-EU utility (`compute_crra_eu_objective`); CVaR diagnostic (`compute_portfolio_cvar`, `CVaRAssessment`); PBO (`compute_pbo`); 6-layer exit decision (`resolve_trigger_priority`) |
| `autotuner.py` | Optuna walk-forward (250 trading days, 500 trials per symphony); CPCV folds (`_generate_cpcv_folds`, N=6, k=2, 15 splits, 5 paths); CRRA-EU `_haircut_select` objective with `compute_n_effective` additive accounting; PHASE-3 PBO gate (`compute_pbo`, top-20 pre-BHY, PBO>0.5 veto); `compute_crra_eu_tstat` lives here (not math_engine); NN1 spec-freeze enforcement at entry; invokes Overfitting Conscience + Spec Critic + Divergence Explainer post-walk-forward; OC reads `prior_runs` via `advisor_ro_query`; `save_autotune_run` returns the inserted row id |
| `database.py` | State DB: 32 numbered migration SQL files (001–032); `_MIGRATION_FILES` wires 29 entries (004–032, 021 precedes 020 — intentional, see ARCH-002 inline comment); public accessors include Phase-1 originals (`record_cvar_diagnostic`, `read_cvar_diagnostic_for_symphony`, `get_or_create_phase1_theory_bundle_id`, `insert_dof_ledger_row`, `query_wall_breach_tripwire`) plus post-Sprint-3 additions: `insert_advisor_observation` (accepts `symphony_id`), `get_advisor_observations_for_symphony`, `get_symphony_live_mode`, `set_symphony_live_mode`, `save_regime_label`, `get_cached_regime_label`, `get_or_create_phase15_m3_bundle_id`; `compute_composition_hash` promoted here from deleted `port_selector.py`; **Prism Phase 1 (migration 032)**: `insert_prism_audit_entry(run_id, agent_role, phase, content) -> int`, `get_prism_audit_for_run(run_id) -> list[dict]` — append-only deliberation trail keyed by `run_id`; **pytest sentinel guard**: `_db_file()` raises `RuntimeError` when `"pytest" in sys.modules` AND basename == `alphabot_state.db` — tests MUST set `DB_PATH` via `tests/conftest.py` |
| `reporting.py` | Discord webhooks + QuickChart embeds |
| `synthetic_history.py` | 250-day live Alpaca historical fetcher (parallel + file cache); feeds autotuner replay |
| `acceptance_gate.py` | Reusable overfitting acceptance gate — used by autotuner and AI Advisor proposal suite |
| `ai_advisor.py` | Claude-backed config advisor: `assemble_advisor_context` (accepts `composer_symphony_id` + `autotune_run` params; `autotune_run` is HONORED — pass a pre-fetched row to skip the internal DB fetch, or use the default `_SENTINEL` to fetch internally), `build_assessment_from_context` (per-symphony informative empty-state), `request_suggestions` (D-1 fully honored: all error paths return `type(exc).__name__` only), C2 safety gates; 7-item suggestible allowlist (6 Optuna search-space keys + MAX_SQUEEZE_FLOOR) |
| `advisors/symphony_schema.py` | Phase-1 Strategy Builder schema layer: constructs synthetic Composer `raw_value` trees + inspects arbitrary ones (built or real `/score`). Pure stdlib. Never-raising read-only `validate_tree` (HARD errors) / `lint_tree` (soft warnings — size/depth caps and unknown indicator fns are lint-only per handoff amendments 1–7) / `extract_tickers` / deterministic `render_rules_text`; 10 constructors (`make_root`, `make_asset`, `make_weight_*`, `make_inverse_vol`, `make_group`, `make_filter`, `make_indicator`, `make_condition`, `make_if`) emitting fresh uuid4 ids + deep-copied children. Vocabulary pinned by `feature-plans/strategy-builder-composer-grammar.md`. Iterative traversal (depth-230 fixtures safe). |
| `advisors/strategy_builder_engine.py` | Phase-2 Strategy Builder proposal engine: builds candidate trees from 7 templates (T1–T7) via `symphony_schema` constructors → backtests via `composer_backtest_client` (1 req/s) → single-batch FDR gate via `backtest_gate_engine.evaluate_candidate_batch` (gate input = FULL backtested batch; screens never shrink it) → `ScreenConfig` post-gate screens (tail-aligned correlation/blended-DD) → persists survivors via `database.insert_advisor_observation`. Public surface: `propose_strategies(objective, universe, screen_config, live_returns, ...) -> ProposalRun`; `Objective` enum (diversify / cut_drawdown / lift_risk_adjusted); `ScreenConfig` dataclass. Never-raises; off-execution-path; advisory-only. T6/T7 sort-by-fn values are a PM-accepted unverified-grammar deviation (see phase-2 contract header). |
| `advisors/lens_pipeline.py` | Off-hours lens pipeline (Cycle 4): `run_pipeline(*, dry_run=False) -> dict`; 4-pass (per-lens exception isolation → citation validation via `build_citation` → Claude Haiku synthesis → MARKET_PRISM persistence); always writes one `advisor_role="MARKET_PRISM"` `advisor_observations` row per non-dry_run call even when all lenses unavailable (`verdict="limited-inputs"`); D-1 contract (`type(exc).__name__` only); CC-2 boundary (lazy import inside `app.py` daemon thread, never module-level); scheduled daily at 03:00 via `run_scheduler()`. `database.get_latest_market_prism_summary()` returns the most recent row for Cycle-5 Overview tab. |
| `advisors/lens_warehouse.py` | Nightly lens data warehouse (feat/lens-warehouse): separate SQLite DB at `WAREHOUSE_DB_PATH` (default `alphabot_warehouse.db`) — distinct from state and optimization DBs; `init_warehouse()` (idempotent schema + WAL), `persist_lens_snapshot(lens, symbol, source, available, raw_payload, fetch_ts=None) -> int` (append-only INSERT, secret-stripped before write), `get_lens_snapshots(lens, symbol=None, since=None) -> list[dict]`; D-1 error contract; no Flask dependency; no production caller yet. |
| `advisors/` | Phase-1 Advisor producers: `overfitting_conscience.py`, `spec_critic.py`, `divergence_explainer.py`. Narrator deferred. AI Advisor proposal suite: `correlation_diagnostic.py`, `composer_backtest_client.py`, `backtest_gate_engine.py`, `asset_swap_engine.py`, `logic_change_engine.py`, `advisor_chat.py`. **Prism Phase 1 (Cycle 5)**: `prism_audit_write.py` — agent-callable CLI (`python -m advisors.prism_audit_write --run-id <id> --role <r> --phase <p>`, content from STDIN, prints row id; D-1 error contract; no Flask dependency). `asset_swap_engine.py` (Cycle-3): `extract_lens_scores(context)` extracts per-ticker scores from the 5 honest-availability lens blocks; `generate_objective_directed_candidates` / `propose_operator_swap` / `suggest_swaps` accept `lens_scores=None` kwarg — lens evidence blended into ranking via `_apply_lens_blend` (weight `LENS_BLEND_WEIGHT=0.25`); persistence writes `lens_evidence` + `sources` into `raw_response`; BHY-FDR gate unchanged. `advisor_chat.py` supports M1–M4 + M6 (`strategy_proposal`) artifact types; `CHAT_ARTIFACT_ALLOWED_FIELDS` extended with 13 M6 fields (Phase 4) + Cycle-1 multi-lens fields; `explain_artifact` re-validates internally (defense-in-depth). All observations write to `advisor_observations` keyed by `symphony_id`. Called post-walk-forward from `autotuner.py`. |
| `templates/ai_advisor.html` | Single unified AI Advisor SPA template — all 6 tabs (Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder) rendered in one server-side render; tab switching in-place via JS; Overview tab includes always-on Market Prism block (sentiment chip, rationale, per-lens digest, cited sources, informative empty state when no nightly row exists) |
| `static/ai_advisor.js` | AI Advisor client logic: `initTabSwitcher`, suggestion card rendering with per-symphony assessment block, accept/reject, autotune run feed, Strategy Builder tab functions (`sbRunAnalysis`, `openChatWithArtifact`) |
| `tests/conftest.py` | Pytest configuration: `pytest_configure()` hook sets `DB_PATH` to a session temp path before collection; autouse `_isolate_db` per-test fixture; `_disable_csrf_for_tests` autouse fixture |

## Build / Run
```
python app.py          # run daemon
/run-tests             # pytest (wraps exclusions)
/lint                  # ruff format + check
```

## Architecture Constraints (hard rules for workers)
1. Engine runs 1-minute cadence during market hours — **no blocking I/O on the execution path.**
2. Dashboard has two guarded write paths: `POST /api/settings` (`app.py:2186`) writes allowlisted .env keys; `POST /api/symphony-settings/<name>` (`app.py:2265`) calls `database.set_symphony_live_mode`. Both are CSRF-protected and enforced by `_SETTINGS_WRITE_ALLOWLIST`. **The dashboard is NOT a live-trade-action surface** — settings are operator config, not trade orders. `LIVE_EXECUTION` and all credential keys are excluded from the allowlist.
3. Two-DB pattern: state DB owns live positions/decisions; optimization DB owns Optuna studies. **Never cross-join across DBs in app code** — copy needed rows.
4. `is_live=True` is explicit, never a default.
5. Templates open SQLite read-only; UI never reruns the engine.
6. **AI Advisor Composer hash rule:** all Advisor routes that call Composer endpoints must use the Composer hash ID, not the normalized display name. The route resolves NAME→hash from `bot_state`; `assemble_advisor_context` receives the hash via `composer_symphony_id`. Passing the name causes HTTP 400 from Composer's `/score` API.

## Coding Standards (project additions)
- No magic numbers in `math_engine.py` — every constant named + source comment
- Every change to math layers requires a golden-fixture test
- API calls must be testable from a fixture (Composer + Alpaca)
- Schema migrations: additive-first, NULLable + DEFAULT, never destructive in one step

## Known Gotchas
| Issue | Fix |
|-------|-----|
| Composer.trade API is poorly documented | Assume drift; invoke `composer-api-researcher` before any client change |
| Walk-forward study names | Use `<timestamp>__<symphony>`; never reuse a study name |
| Minute scheduler in `app.py` spawns subprocesses at :00 | Blocking changes to `alpha_bot_execution.py` impact live ops — flag in A/C |
| Default Optuna trial floor | 100 trials (statistical stability) |
| `test_live_*.py` files | Excluded by default — opt-in via `--include-live` |
| Migration 021 listed before 020 in `_MIGRATION_FILES` | Intentional — see ARCH-002 inline comment in `database.py`. Reordering would corrupt live DBs that already have 021 applied. |
| Blast-radius scanners sweeping `.claude/worktrees/` | `rglob("*.py")` scanners must exclude `.claude/worktrees/` and `.claude/audit-worktrees/`; stale pre-deletion .py files in orphan worktrees produce false-positive import violations. |
| Test writing to production DB | `database._db_file()` raises `RuntimeError` under pytest if `DB_PATH` resolves to `alphabot_state.db`. Fix: ensure `tests/conftest.py` `pytest_configure()` runs before any DB import. Per-test isolation via `_isolate_db` autouse fixture. |
| AI Advisor empty suggestions (most symphonies) | Expected. The CRRA-EU + Harvey-Liu FDR gate is intentionally strict. `build_assessment_from_context` explains why — `oos_alpha=None` means all trials were haircut-rejected, not an error. |
| Advisor tab templates deleted | The 4 old per-tab advisor templates (`ai_advisor_correlations.html`, etc.) were deleted in the advisor-cleanup cycle (2026-06-10). Do not recreate them — the unified SPA at `templates/ai_advisor.html` is canonical. |
| Strategy Builder standalone template deleted | `templates/ai_advisor_strategy_builder.html` was deleted in the spa-port cycle (2026-06-13) when the Strategy Builder was folded in as the 6th tab of the unified SPA. Do not recreate it. `GET /ai-advisor/strategy-builder` now 302-redirects to `/ai-advisor`. |
| Lens warehouse has no production caller | `advisors/lens_warehouse.py` is scaffolded infrastructure; no app code calls it yet. Tests use `WAREHOUSE_DB_PATH` env override to an isolated temp path. Do not reference `alphabot_warehouse.db` from production code until a caller is wired. |

## Project-Local Specialist Agents (`.claude/agents/`)
**Task-engine specialists:**
`risk-engine-specialist` · `optuna-specialist` · `flask-dashboard-specialist` · `sqlite-specialist` · `composer-alpaca-integration` · `quant-test-writer` · `quant-code-reviewer`

**Domain researchers:**
`composer-api-researcher` · `alpaca-api-researcher` · `quant-risk-researcher` · `optuna-methodology-researcher` · `viz-library-researcher` · `trading-compliance-researcher`

**Cross-cutting:**
`security-auditor` · `synthesizer` · `verifier`

## Project-Local Skills (`.claude/skills/`)
`/backtest` · `/optuna-compare` · `/db-inspect` · `/api-fixture` · `/discord-test` · `/run-tests` · `/lint` · `/perf-snapshot` · `/symphony-diff`

## Agent Team Composition (HARD REQUIREMENT)
**All new codepaths — and bug fixes that introduce new codepaths — MUST be built via the Toxic Pair TDD composition of Agent Teams.** The PM may NOT approximate a team with sequential solo-agent RED → GREEN → review dispatches. Real Agent Teams only: shared worktree, one branch, `SendMessage` handoffs, autonomous Toxic Pair (test-writer ⇄ implementer) cycling with reviewers wrapped around it.

**Skill-driven TDD is MANDATORY (not ad-hoc RED/GREEN).** Every dev cycle is driven through the TDD skills, and the kickoff prompt MUST say so explicitly:
- Planning artifact first: each feature has a `/scaffold`-format plan in `feature-plans/<name>.md` with `Status: ready` (Summary / Acceptance Criteria as `AC-N` / Architecture / Edge Cases / Security Considerations / Testing Strategy / Scope Boundaries). Use the `/scaffold` skill to produce it — never hand-roll.
- **test-writer** runs `/tdd <feature-plan>` → writes RED tests + creates `.claude/tdd-handoff.md`.
- **implementer** runs `/tdd-implement` → reads the handoff (NOT the plan — deliberately blind), writes minimal GREEN.
- finalize the cycle with `/tdd-finalize` before cycle-complete.
This requires the `Skill` tool in the agent's `tools:` frontmatter — all dev specialists (quant-test-writer, sqlite-specialist, risk-engine-specialist, optuna-specialist, flask-dashboard-specialist, composer-alpaca-integration) carry it. If a specialist lacks a needed capability, fix its frontmatter — never work around it.

Standing TDD team: **Quint** (test-writer + implementer + `quant-code-reviewer` + domain specialist matched to the surface touched + **doc-writer**).
**A `doc-gen` doc-writer is a MANDATORY member of EVERY team (global hard rule) — added on top of the Quad/whatever composition.** It documents the cycle's changes into `docs/generated/` / `DECISIONS.md` / README and commits them on the shared branch before cycle-complete; the cycle is not complete until those updates are in. Never omit it (TDD, audit, or research teams alike).
Math-layer changes always add `quant-test-writer` as the adversarial test author.
**doc-writer goes on EVERY team** (TDD and non-TDD): owns feature-plans/ docs for the cycle, audits docstrings/constant comments (files findings to the owning teammate — never edits their files), and drafts CLAUDE.md key-files updates for PM approval. Bad documentation is a shippable defect.

**Exceptions — no TDD required (but often still a team):** config/doc-only edits, one-line fixes to existing codepaths, behavior-preserving refactors fully covered by existing tests, and pure research/diagnosis tasks (no code written). These skip the Toxic Pair because there is no code to drive RED→GREEN — but a multi-surface diagnosis or audit should still run as a **non-TDD Agent Team** (communicating auditors + a synthesizing lead), not a swarm of disconnected solo agents. Reserve solo background agents for genuinely independent single-surface tasks. See `~/.claude/CLAUDE.md` §"Agent Teams" for the composition catalogue.
