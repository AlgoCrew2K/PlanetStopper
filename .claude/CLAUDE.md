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
| `app.py` | Flask dashboard + minute-by-minute scheduler; `_DISMISS_EXECUTOR` (background dismiss thread) + `_FLUSH_STATE_LOCK` (flush serialization); spawns `alpha_bot_execution.py` at :00; CSRF infrastructure (`_validate_csrf`, `_csrf_before_request`); `_SETTINGS_WRITE_ALLOWLIST` gates the two guarded write paths |
| `alpha_bot_execution.py` | Core engine — per-cycle execution; wired to canonical THEORY spec bundle via `get_or_create_phase1_theory_bundle_id` |
| `math_engine.py` | Risk math: volatility scaling, sqrt-time squeeze (`1-sqrt(1-t)`), parabolic ratchet, MC gating, VWAP, breakeven, exit confirm, regime-match guard; CRRA-EU utility (`compute_crra_eu_objective`); CVaR diagnostic (`compute_portfolio_cvar`, `CVaRAssessment`); PBO (`compute_pbo`); 6-layer exit decision (`resolve_trigger_priority`) |
| `autotuner.py` | Optuna walk-forward (250 trading days, 500 trials per symphony); CPCV folds (`_generate_cpcv_folds`, N=6, k=2, 15 splits, 5 paths); CRRA-EU `_haircut_select` objective with `compute_n_effective` additive accounting; PHASE-3 PBO gate (`compute_pbo`, top-20 pre-BHY, PBO>0.5 veto); `compute_crra_eu_tstat` lives here (not math_engine); NN1 spec-freeze enforcement at entry; invokes Overfitting Conscience + Spec Critic + Divergence Explainer post-walk-forward; OC reads `prior_runs` via `advisor_ro_query`; `save_autotune_run` returns the inserted row id |
| `database.py` | State DB: 31 numbered migration SQL files (001–031); `_MIGRATION_FILES` wires 28 entries (004–031, 021 precedes 020 — intentional, see ARCH-002 inline comment); public accessors include Phase-1 originals (`record_cvar_diagnostic`, `read_cvar_diagnostic_for_symphony`, `get_or_create_phase1_theory_bundle_id`, `insert_dof_ledger_row`, `query_wall_breach_tripwire`) plus post-Sprint-3 additions: `insert_advisor_observation` (accepts `symphony_id`), `get_advisor_observations_for_symphony`, `get_symphony_live_mode`, `set_symphony_live_mode`, `save_regime_label`, `get_cached_regime_label`, `get_or_create_phase15_m3_bundle_id`; `compute_composition_hash` promoted here from deleted `port_selector.py` |
| `reporting.py` | Discord webhooks + QuickChart embeds |
| `synthetic_history.py` | 250-day live Alpaca historical fetcher (parallel + file cache); feeds autotuner replay |
| `acceptance_gate.py` | Reusable overfitting acceptance gate — used by autotuner and AI Advisor proposal suite |
| `advisors/symphony_schema.py` | Phase-1 Strategy Builder schema layer: constructs synthetic Composer `raw_value` trees + inspects arbitrary ones (built or real `/score`). Pure stdlib. Never-raising read-only `validate_tree` (HARD errors) / `lint_tree` (soft warnings — size/depth caps and unknown indicator fns are lint-only per handoff amendments 1–7) / `extract_tickers` / deterministic `render_rules_text`; 10 constructors (`make_root`, `make_asset`, `make_weight_*`, `make_inverse_vol`, `make_group`, `make_filter`, `make_indicator`, `make_condition`, `make_if`) emitting fresh uuid4 ids + deep-copied children. Vocabulary pinned by `feature-plans/strategy-builder-composer-grammar.md`. Iterative traversal (depth-230 fixtures safe). |
| `advisors/strategy_builder_engine.py` | Phase-2 Strategy Builder proposal engine: builds candidate trees from 7 templates (T1–T7) via `symphony_schema` constructors → backtests via `composer_backtest_client` (1 req/s) → single-batch FDR gate via `backtest_gate_engine.evaluate_candidate_batch` (gate input = FULL backtested batch; screens never shrink it) → `ScreenConfig` post-gate screens (tail-aligned correlation/blended-DD) → persists survivors via `database.insert_advisor_observation`. Public surface: `propose_strategies(objective, universe, screen_config, live_returns, ...) -> ProposalRun`; `Objective` enum (diversify / cut_drawdown / lift_risk_adjusted); `ScreenConfig` dataclass. Never-raises; off-execution-path; advisory-only. T6/T7 sort-by-fn values are a PM-accepted unverified-grammar deviation (see phase-2 contract header). |
| `advisors/` | Phase-1 Advisor producers: `overfitting_conscience.py`, `spec_critic.py`, `divergence_explainer.py`. Narrator deferred. AI Advisor proposal suite: `correlation_diagnostic.py`, `composer_backtest_client.py`, `backtest_gate_engine.py`, `asset_swap_engine.py`, `logic_change_engine.py`, `advisor_chat.py`. All observations write to `advisor_observations` keyed by `symphony_id`. Called post-walk-forward from `autotuner.py`. |

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

Standing TDD team: **Quint** (test-writer + implementer + `quant-code-reviewer` + domain specialist matched to the surface touched + **doc-writer**).
Math-layer changes always add `quant-test-writer` as the adversarial test author.
**doc-writer goes on EVERY team** (TDD and non-TDD): owns feature-plans/ docs for the cycle, audits docstrings/constant comments (files findings to the owning teammate — never edits their files), and drafts CLAUDE.md key-files updates for PM approval. Bad documentation is a shippable defect.

**Exceptions — no TDD required (but often still a team):** config/doc-only edits, one-line fixes to existing codepaths, behavior-preserving refactors fully covered by existing tests, and pure research/diagnosis tasks (no code written). These skip the Toxic Pair because there is no code to drive RED→GREEN — but a multi-surface diagnosis or audit should still run as a **non-TDD Agent Team** (communicating auditors + a synthesizing lead), not a swarm of disconnected solo agents. Reserve solo background agents for genuinely independent single-surface tasks. See `~/.claude/CLAUDE.md` §"Agent Teams" for the composition catalogue.
