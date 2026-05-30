# Planet Stopper — Project-Local CLAUDE.md
# EXTENDS ~/.claude/CLAUDE.md — do NOT duplicate global content.

## Project Identity
- **Name:** Planet Stopper
- **Purpose:** Institutional-grade algorithmic risk engine; monitors live Composer.trade portfolios; executes intelligent trailing stops ("Guard Alpha")
- **Stack:** Python 3 / Flask monolithic daemon; SQLite (state DB + optimization DB); Optuna; Composer.trade + Alpaca + Discord integrations
- **Workflow:** Hard fork — never re-syncing upstream; full autonomy within global guidelines
- **Roadmap (in scope):** historical analysis, live-vs-Planet Stopper comparison stats, charts/graphs in Flask dashboard, pytest + GitHub Actions test harness

## Key Files (quick reference for workers)
| File | Role |
|------|------|
| `app.py` | Flask dashboard + minute-by-minute scheduler; `_DISMISS_EXECUTOR` (background dismiss thread) + `_FLUSH_STATE_LOCK` (flush serialization); spawns `alpha_bot_execution.py` at :00 |
| `alpha_bot_execution.py` | Core engine — per-cycle execution; wired to canonical THEORY spec bundle via `get_or_create_phase1_theory_bundle_id` |
| `math_engine.py` | Risk math: volatility scaling, log time squeeze, parabolic ratchet, MC gating, VWAP, breakeven, exit confirm; CRRA-EU utility (`compute_crra_eu_objective`, `compute_crra_eu_tstat`); CVaR diagnostic (`compute_portfolio_cvar`, `CVaRAssessment`); 6-layer exit decision (`resolve_trigger_priority`) |
| `autotuner.py` | Optuna walk-forward (125 trading days, 500 trials per symphony); CRRA-EU `_haircut_select` objective with `compute_n_effective` additive accounting; NN1 spec-freeze enforcement at entry; invokes Overfitting Conscience + Spec Critic + Divergence Explainer post-walk-forward; OC reads `prior_runs` via `advisor_ro_query`; `save_autotune_run` returns the inserted row id |
| `database.py` | State DB: 25 migration SQL files (001–025); `_MIGRATION_FILES` applies 004–025 in declared order (021 precedes 020 — intentional, see ARCH-002 inline comment); public accessors include Phase-1 originals (`record_cvar_diagnostic`, `read_cvar_diagnostic_for_symphony`, `get_or_create_phase1_theory_bundle_id`, `insert_researcher_dof_ledger`, `query_wall_breach_tripwire`) plus Sprint-3 additions: `insert_advisor_observation` (accepts `symphony_id`), `get_advisor_observations_for_symphony`; `compute_composition_hash` promoted here from deleted `port_selector.py` |
| `reporting.py` | Discord webhooks + QuickChart embeds |
| `synthetic_history.py` | 125-day live Alpaca historical fetcher (parallel + file cache); feeds autotuner replay |
| `advisors/` | Phase-1 Advisor producers: `overfitting_conscience.py`, `spec_critic.py`, `divergence_explainer.py`. Narrator deferred. All write to `advisor_observations` keyed by `symphony_id`. Called post-walk-forward from `autotuner.py`. |

## Build / Run
```
python app.py          # run daemon
/run-tests             # pytest (wraps exclusions)
/lint                  # ruff format + check
```

## Architecture Constraints (hard rules for workers)
1. Engine runs 1-minute cadence during market hours — **no blocking I/O on the execution path.**
2. Dashboard is a read-only operator surface — **never an action surface for live trades.**
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

## Project-Local Skills (`.claude/skills/`)
`/backtest` · `/optuna-compare` · `/db-inspect` · `/api-fixture` · `/discord-test` · `/run-tests` · `/lint` · `/perf-snapshot` · `/symphony-diff`

## Agent Team Composition (HARD REQUIREMENT)
**All new codepaths — and bug fixes that introduce new codepaths — MUST be built via the Toxic Pair TDD composition of Agent Teams.** The PM may NOT approximate a team with sequential solo-agent RED → GREEN → review dispatches. Real Agent Teams only: shared worktree, one branch, `SendMessage` handoffs, autonomous Toxic Pair (test-writer ⇄ implementer) cycling with reviewers wrapped around it.

Standing TDD team: **Quad** (test-writer + implementer + `quant-code-reviewer` + domain specialist matched to the surface touched).
Math-layer changes always add `quant-test-writer` as the adversarial test author.

**Exceptions — no TDD required (but often still a team):** config/doc-only edits, one-line fixes to existing codepaths, behavior-preserving refactors fully covered by existing tests, and pure research/diagnosis tasks (no code written). These skip the Toxic Pair because there is no code to drive RED→GREEN — but a multi-surface diagnosis or audit should still run as a **non-TDD Agent Team** (communicating auditors + a synthesizing lead), not a swarm of disconnected solo agents. Reserve solo background agents for genuinely independent single-surface tasks. See `~/.claude/CLAUDE.md` §"Agent Teams" for the composition catalogue.
