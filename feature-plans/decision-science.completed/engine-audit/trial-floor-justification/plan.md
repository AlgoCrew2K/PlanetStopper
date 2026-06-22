# Engine Audit — Trial-Floor Justification

**Feature:** Audit `n_trials=500` (autotuner.py:1010) against the
project's 100-trial statistical-stability floor (project rule). Verify
500 is conservative-above-floor. Document the choice; no change unless
justified — and only with explicit PM/user direction (project rule 3).

**Phase:** Engine audit (post-Phase-1; correctness-discipline hardening
+ documentation)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(RED).

## Source-of-truth references

- `.claude/CLAUDE.md` (project) — agent operating rule 3 (autotuner
  charter): "Never reduce trial count below 100 without explicit user
  direction (statistical stability floor)."
- `autotuner.py:1010` — `study.optimize(objective, n_trials=500,
  n_jobs=-1)`.
- `autotuner.py:319-356` — `benjamini_hochberg_adjust`: c(N) scales
  with N=n_trials. At N=500, c(500)≈6.79. At N=100, c(100)≈5.19.

## Why

The trial-floor is the structural stability constraint on the BHY
haircut. At too few trials:
- the TPE sampler does not explore enough to give a stable best
  estimate;
- the c(N) factor is too small to correct for selection bias
  realistically (c(50)≈4.5 vs c(500)≈6.79 — a 50% reduction in the
  correction);
- the FDR gate (`HARVEY_LIU_FDR_Q = 0.05`) is easier to clear
  spuriously.

500 is well above the 100 floor; it is the historical AlphaBot
default. The audit pins it as a NAMED constant (currently it is an
inline literal at `autotuner.py:1010`).

The plan does **NOT propose changing** the trial count. It documents,
pins, and creates a tripwire for any future change.

## Deliverables

### D1 — `N_TRIALS` named constant

```python
# Number of Optuna trials per autotune run. 500 is well above the
# project's 100-trial statistical-stability floor (project rule 3 —
# autotuner charter). The BHY haircut's Yekutieli c(N) factor scales
# with N: at N=500, c(N)≈6.79; at N=100, c(N)≈5.19. Reducing below
# 100 understates selection-bias correction AND under-explores the
# 6-D search space; reducing requires EXPLICIT PM/user direction
# per the project rule.
#
# The cost-side dial: each trial runs the full guard-alpha
# simulation across all symphonies; at typical history scale and
# n_jobs=-1 on an 8-core machine, 500 trials per symphony complete
# in O(minutes), well within the EOD batch budget.
N_TRIALS = 500
```

The `study.optimize(...)` call becomes:
```python
study.optimize(objective, n_trials=N_TRIALS, n_jobs=_resolve_n_jobs())
```

### D2 — `N_TRIALS >= 100` invariant test

A NEW test:

```
T_n_trials_floor —
- assert N_TRIALS >= 100
- read the source of run_autotuner; assert the study.optimize call
  uses N_TRIALS, NOT a literal integer.
A PR that lowers N_TRIALS below 100 fails the assertion AND the test
documentation comment explains the project rule.
```

### D3 — `N_TRIALS` change requires PM-surfaced commit

A NEW commit-message linter rule (or PR-template item) — out of
scope for code changes, but recorded in `findings.md`:

> A commit that modifies the value of N_TRIALS must include the
> phrase "PM approval:" followed by the rationale in the commit
> body, or the PR is blocked by review.

(This is a SOCIAL guard; the test in D2 is the technical guard.)

### D4 — c(N) cost-table in findings.md

`findings.md` records the c(N) values at common candidate trial
counts:

| N    | c(N)   | Notes                                              |
|------|--------|----------------------------------------------------|
| 50   | 4.499  | BELOW floor; project rule violation                |
| 100  | 5.187  | Floor; minimum acceptable                          |
| 200  | 5.878  | Common alternative for slow objectives             |
| 500  | 6.793  | Current default                                    |
| 1000 | 7.486  | More aggressive; useful when search space widens   |
| 2000 | 8.179  | Diminishing returns on c(N)                        |

The table is the reference for any future PM-surfaced change request.

### D5 — Active-run trial-count audit

A NEW write to `autotune_runs` (or `findings.md`) recording the
ACTUAL trial count for each run — including failed/pruned trials
(though under NopPruner per the pruner-choice plan, no trials are
pruned). This is already implicit (Optuna stores per-trial state
in `optuna_studies.db`); the audit records the post-completion count
in the state-DB row for cross-DB-clean diffing.

The relevant column already exists implicitly via the BHY haircut's
`completed_trials` filter (`autotuner.py:1034`); the audit ratifies
that the count is the BHY input N, not just the configured
`n_trials`.

### D6 — Audit findings record

`findings.md` committed alongside the plan:
- Confirmation that `N_TRIALS = 500`.
- Confirmation that 500 is well above the 100 floor.
- c(N) cost table (D4).
- Statement of the PM-surfacing requirement for any change.
- Observed actual completed-trial counts (for recent runs, if
  retrievable from the optimization DB) — confirms that under
  `n_jobs=-1`, trials complete reliably (no widespread sampler
  errors silently reducing N).

## Dependencies

- **Coupled to:** Engine Audit parallelism/reproducibility plan
  (`N_TRIALS` is referenced in the `study.optimize` call alongside
  `_resolve_n_jobs()`).
- **Coupled to:** Engine Audit BHY implementation correctness plan
  (the c(N) cost table cross-references the haircut audit).

## Golden-fixture tests required

(D2 above is the primary test.)

### T1 — `N_TRIALS >= 100` (D2)

Static-analysis-style pin.

### T2 — `study.optimize` uses the constant

Static-analysis: assert `study.optimize(objective, n_trials=N_TRIALS,
...)` — the kwarg is `N_TRIALS`, not a literal integer. A PR that
re-introduces a literal `500` regresses to the pre-audit shape;
caught at edit time.

### T3 — c(N) cost-table consistency

A unit test asserts the c(N) values in the findings.md table are
within `1e-3` of the computed harmonic numbers — catches a
documentation drift (the table goes stale if the BHY implementation
changes harmonic-number computation, which it cannot, but the
fallback safeguard is cheap).

### T4 — completed-trial count surface

After a small fixture autotuner run, assert `len(completed_trials)`
in `_haircut_select` matches `N_TRIALS` (no silent failures).
Catches a future sampler error path that swallows trials.

## Definition of Done

1. T1-T4 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — unchanged behaviour.
3. `findings.md` committed with the c(N) cost table.
4. `N_TRIALS = 500` is a named module-scope constant in
   `autotuner.py` with the documented rationale.
5. The `study.optimize(...)` call uses `N_TRIALS` (not a literal).
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   N_TRIALS=500 named constant (project rule 3 — statistical-
   stability floor); c(N) cost-table recorded; n_trials=500;
   objective=<unchanged>`.

## Risk callouts

- **"Cut to 100 for speed" PR.** A common pressure: a developer
  argues "the autotuner takes 10 minutes; let me cut to 100 trials,
  it's 5×faster." 100 is the FLOOR (NOT a recommendation). c(N) at
  N=100 is meaningfully smaller (5.19 vs 6.79 at 500) — a borderline
  trial set that clears the FDR gate at N=500 might fail at N=100,
  AND a borderline trial set that fails at N=500 might clear at
  N=100. Direction depends on the trial distribution. T1 catches
  anything < 100; between 100 and 500, the PM-surfacing requirement
  (D3) is the social guard.
- **"Push to 1000 for better exploration" PR.** Less common but
  possible. Doubling N doubles compute cost AND raises c(N) by ~10%
  (6.79 → 7.49) — slightly stricter haircut. Not a correctness
  defect, but a methodology change requiring PM surface.
- **Silent N reduction via failed trials.** If a future bug causes
  many trials to fail with `value = None`, the BHY haircut filters
  them out (`autotuner.py:1034`) and operates on a smaller N than
  configured. T4 catches widespread failures; small failure rates
  (< 5%) are tolerated as natural sampler exploration noise.
- **`N_TRIALS` literal duplication.** If a future PR adds a SECOND
  `study.optimize` call site (e.g. for a port-mode dedicated run),
  the new call must also use `N_TRIALS`. The static-analysis pin
  catches `n_trials=<literal>` regardless of which call site.
- **Trial count under `_apply_optuna_archive_migration_if_needed`.**
  The archive migration touches study NAMES, not trial counts; no
  interaction. T3 / T4 confirm.
- **125-day-history coupling.** At 125 trading days × 60/20/20
  split, the validation fold has ~25 days; the c(N) scaling
  decision (500 trials) is independent of the fold size — they are
  orthogonal axes. The trial-floor audit does not depend on the
  fold-structure audit.

## Out of scope

- Changing `N_TRIALS` — out of scope; PM-surfacing required.
- The 100-floor itself — owned by the project autotuner charter;
  this audit ratifies it.
- The BHY c(N) machinery — owned by the BHY implementation audit
  plan; this audit only consumes its scaling.
- The parallelism / n_jobs configuration — owned by the
  parallelism/reproducibility audit plan.
- Per-symphony adaptive trial counts (e.g. fewer trials for
  symphonies with few history days) — out of scope; would be a
  methodology change requiring PM surface and a separate plan.
