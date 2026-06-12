# Strategy Builder — Phase 3.5 Contract: Metrics Persistence at Observation Write

**Status:** BINDING contract for the Phase-3.5 Toxic Pair TDD team.
Closes the ledgered deferrals: Phase-3 deviations 1–2 (baseline column +
sparkline blocked on missing persisted metrics) and Phase-4 [PM-ASSUMED]
field-drops (cagr/sharpe/calmar/correlation_vs_live/blended_drawdown
allowlisted but never populated).

## 1. Objective

At proposal-persist time (`advisors/strategy_builder_engine.py::_persist_survivor`
and the rejected-candidate persist path), write the candidate's quantstats
metrics and screen metadata INTO `raw_response` so downstream surfaces
(Phase-3 cards, Phase-4 M6 artifacts) can render them without recomputation
or AC-5-violating reads.

## 2. Scope of change (exhaustive)

| Surface | Granted edit |
|---------|--------------|
| `database.py` + new migration SQL | OPTIONAL migration 032 ONLY if a new column is genuinely required. STRONG DEFAULT: no schema change — `raw_response` is already a JSON blob; extend its payload. If a migration is added: additive, NULLable + DEFAULT, never destructive (project standard). |
| `advisors/strategy_builder_engine.py` | Extend the persisted `raw_response` payload with: `cagr`, `sharpe`, `calmar`, `max_drawdown`, `correlation_vs_live`, `blended_drawdown`, `n_candidates`, `n_survivors`, plus `live_baseline` sub-dict (same metric keys computed from the `live_returns` series passed to `propose_strategies`, when provided; omitted when not). Values come from metrics ALREADY computed in the run — no new computation on the persist path. |
| `app.py` GET strategy-builder route | Surface the new fields into card context + M6 `card_artifacts` (fields already allowlisted in Phase 4 — construction only). |
| `templates/ai_advisor_strategy_builder.html` | Render the baseline column in the stats table when `live_baseline` present (design-prompt Screen-3 anatomy); keep single-column rendering when absent (backward compat with pre-3.5 rows). Sparkline remains OUT OF SCOPE (viz dependency decision deferred). |
| `tests/**` | test-writer owned. |
| `feature-plans/**` | doc-writer owned. |

## 3. Hard requirements

- HR-1: Backward compatibility — pre-3.5 observation rows (no metrics in
  raw_response) MUST render exactly as today: no KeyError, no None-formatting
  artifacts ("None%"), single-column table. Golden-fixture test on an OLD row.
- HR-2: No recomputation at read time; no new blocking I/O; persist path adds
  dict assembly only.
- HR-3: Phase-2 gate semantics untouched — metrics are recorded, never used to
  re-gate. FDR fields recorded as computed by the gate.
- HR-4: All Phase-2/3/4 invariant tests still pass (no-action-affordance,
  allowlist caps, CSRF, read-only GET). Full-suite baseline: 5,987 / 6 / 0.
- HR-5: `live_baseline` is computed from the SAME tail-aligned window used by
  the correlation screen (reuse the existing alignment helper — do not
  reimplement; head/tail misalignment was a Phase-2 reviewer MAJOR).

## 4. Acceptance criteria

- AC-1: New proposal run persists all §2 fields; M6 artifact for a new row
  carries populated cagr/sharpe/calmar/correlation_vs_live/blended_drawdown.
- AC-2: Old-row golden fixture renders single-column, crash-free.
- AC-3: Baseline column renders iff `live_baseline` present.
- AC-4: Full default suite 0 failures vs 5,987/6/0 baseline; ruff clean.

## 5. Team

Standard Toxic Pair TDD per CLAUDE.md: test-writer (quant-test-writer) ⇄
implementer, quant-code-reviewer + sqlite-specialist (reviewer — schema/JSON
payload discipline) + flask-dashboard-specialist (template), doc-writer.
Minimum 2 adversarial cycles — the PM WILL commission cycle 2 independently
if the team exits without commit-evidenced cycle-2 tests (Phase-4 precedent:
cycle 2 found 3 real bugs).

[PM-ASSUMED] `live_baseline` metric keys mirror the candidate metric keys for
table-row pairing. [PM-ASSUMED] rejected candidates persist metrics too (their
cards and artifacts benefit equally).
