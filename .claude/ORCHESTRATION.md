# Planet Stopper PM — Project-Local Orchestration

Extends `~/.claude/ORCHESTRATION.md`. Global playbook governs; this file adds
Planet Stopper-specific dispatch mappings, review gates, and workflow sequences only.

---

## Specialist Dispatch Map

| Surface touched                      | Dispatch to                    |
|--------------------------------------|--------------------------------|
| `math_engine.py` / exec logic        | `risk-engine-specialist`       |
| `autotuner.py` / Optuna tuning       | `optuna-specialist`            |
| `app.py` + `templates/`              | `flask-dashboard-specialist`   |
| `database.py` / migrations           | `sqlite-specialist`            |
| Composer or Alpaca clients           | `composer-alpaca-integration`  |
| `tests/`                             | `quant-test-writer`            |
| PR gate review                       | `quant-code-reviewer`          |

---

## Researcher Dispatch Triggers

| Question type                                      | Researcher                      |
|----------------------------------------------------|---------------------------------|
| Composer API behavior / endpoint semantics         | `composer-api-researcher`       |
| Alpaca SDK upgrade or breaking-change check        | `alpaca-api-researcher`         |
| Strategy or risk-method design                     | `quant-risk-researcher`         |
| Walk-forward methodology / Optuna sampler choice   | `optuna-methodology-researcher` |
| Chart library decision (dashboard work)            | `viz-library-researcher`        |
| Broker ToS / regulatory / data-licensing question  | `trading-compliance-researcher` |

---

## Project Review Gates (quant-code-reviewer)

Every PR must pass these in addition to the global code-reviewer checklist:

- **Math safety** — golden-fixture diff in PR summary for any `math_engine.py` change
- **Live-trade boundary** — `is_live` flag propagation verified; no live calls from tests/backtests
- **Fixture provenance** — parser and fixture not co-designed (circular = auto-fail)
- **Schema reversibility** — additive migration file present for any DB change
- **Secrets hygiene** — no keys, webhooks, or account IDs in diff
- **Engine constants** — every numeric literal in `math_engine.py` is named with a source comment
- **Logging redaction** — no verbatim API response bodies logged
- **Dashboard side effects** — routes do not mutate state via the engine

---

## Common Workflows

### A. Adding a new math layer
1. `quant-risk-researcher` — literature scan, recommend approach
2. `quant-test-writer` — write golden-fixture tests (RED)
3. `risk-engine-specialist` — implement (GREEN)
4. `quant-code-reviewer` — final review against project gates

### B. Adding a new dashboard chart
1. `viz-library-researcher` — confirm library fit (QuickChart vs Chart.js etc.)
2. `flask-dashboard-specialist` — route + template + chart hookup
3. `sqlite-specialist` (only if a new query is needed) — read-only access patterns

### C. Capturing an API contract for tests
1. `/api-fixture composer <method>` — capture live response
2. `composer-alpaca-integration` — wire parser against fixture
3. `quant-test-writer` — assert shape and format, not computed values

### D. Schema migration
1. `sqlite-specialist` — write additive migration file under `migrations/`
2. `quant-test-writer` — update fixture DBs
3. `quant-code-reviewer` — reversibility check

---

## Merge and Branch Conventions

- `main` is the working branch (hard fork — no upstream sync)
- Branch naming: `feature/<short-desc>`, `fix/<short-desc>`, `chore/<short-desc>`
- Always new commits — never amend or force-push (global rule)
- All PRs require `quant-code-reviewer` pass, even when global reviewer also runs
- Prune worktrees after every isolated dispatch (`git worktree list` → prune orphans)

---

## CI/CD (Pending)

- Pytest under `tests/`; config in `pytest.ini` or `pyproject.toml [tool.pytest.ini_options]`
- GitHub Actions target: `.github/workflows/`
- Live API calls excluded by default — opt-in via `--include-live`
- Lint: `ruff format --check` + `ruff check`
- When CI lands, reference workflow names here so pre-flight checks can target them

---

## Live-Ops Sensitivity

The bot runs as a local daemon during market hours.

- **Never** dispatch a worker that may invoke `python app.py` against a live `.env`
- All replay and backtest work uses the dual-DB read-only copy pattern
- Confirm `is_live=False` in scope before any dispatch touching `app.py` or Alpaca clients
