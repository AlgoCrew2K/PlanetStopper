# Engine Audit — Optuna Pruner Choice

**Feature:** Audit the autotuner's Optuna pruner. Currently
`optuna.create_study(...)` (`autotuner.py:1009`) accepts the default
pruner. Verify that walk-forward replay objectives are end-of-trial-scored
(no intermediate `trial.report(...)` calls), so a pruner is logically
inapplicable. Make the choice explicit: instantiate `NopPruner()` at the
call site, with a documented rationale.

**Phase:** Engine audit (post-Phase-1; methodology-change-discipline
hardening — NOT a behavior change)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the "add a MedianPruner" PR shape).

## Source-of-truth references

- `.claude/CLAUDE.md` (project) — agent operating rule 1:
  "Every sampler or pruner change is a methodology change."
- `autotuner.py:1009` — `optuna.create_study(...)` with NO `pruner=`
  kwarg. Optuna's default since v2.x is `MedianPruner`.
- `autotuner.py:980-998` — `objective(trial)`: a single end-of-trial
  return; NO intermediate `trial.report(...)` calls.
- `autotuner.py:735-802` — `run_simulation`: a single scalar return
  per trial, computed after the full guard-alpha simulation. The
  simulation is not iterative-with-checkpoints; it is run-to-completion.

## Why

Optuna's pruners (`MedianPruner`, `HyperbandPruner`,
`SuccessiveHalvingPruner`, etc.) require **intermediate trial
reports** — calls to `trial.report(value, step)` mid-evaluation. The
pruner inspects the intermediate value at each step and decides whether
to prune (`trial.should_prune()` → raise `optuna.TrialPruned`).

The autotuner's `objective(trial)` is a **single end-of-trial scalar
return**. There are no intermediate reports. Under any pruner, the
following holds:
- `trial.report(...)` is never called → the pruner has no data to
  inspect.
- `trial.should_prune()` is never queried.
- Every trial runs to completion.

In other words: the default `MedianPruner` is **silently inactive** —
it does no work and changes no behaviour. Replacing it with `NopPruner`
is a documentation change, not a behaviour change.

The risk this audit guards against: a future PR adds
`trial.report(...)` inside the objective loop (e.g. mid-simulation
guard-alpha checkpoints), at which point the default `MedianPruner`
**silently activates** and starts pruning trials based on partial
information that does NOT reflect the final objective. The BHY haircut
then receives a censored trial set whose dependency structure is
ill-defined (TPE-pruned-trials != TPE-completed-trials). The c(N)
factor is no longer correctly calibrated.

The audit makes the pruner choice **explicit and intentional**: `NopPruner`
documents "the objective is end-of-trial-scored; pruning does not
apply." Any future intermediate-report addition is a methodology
change visible at PR review.

## Deliverables

### D1 — Explicit `NopPruner` instantiation

```python
# Pruner: NopPruner — the objective is end-of-trial-scored (a single
# scalar from run_simulation; NO intermediate trial.report calls
# anywhere in objective() or run_simulation). A pruner has no
# intermediate data to inspect; a default MedianPruner is silently
# inactive. Explicit NopPruner documents the intent and prevents a
# future intermediate-report addition from silently activating
# pruning — which would censor the BHY haircut's trial set and
# break the c(N) calibration. A pruner change is a methodology
# change (project rule 1) — surface to PM first.
pruner = optuna.pruners.NopPruner()
study = optuna.create_study(
    study_name=f"{study_timestamp}__{normalized_name}",
    storage=storage,
    load_if_exists=False,
    direction="maximize",
    sampler=sampler,   # see sampler-choice plan
    pruner=pruner,
)
```

### D2 — Named constant for pruner family

```python
# Named for cross-file inspectability and methodology-change tripwire.
ACTIVE_OPTUNA_PRUNER_FAMILY = "NOP"
```

### D3 — `trial.report` static guard

A NEW test:

```
T_no_trial_report — assert the source of autotuner.py does NOT contain
the substring "trial.report" or "should_prune". The autotuner's
objective is end-of-trial-scored; adding intermediate reports is a
methodology change that must surface to PM. The static guard catches
the form at edit time.
```

### D4 — Audit findings record

`findings.md` committed alongside the plan:
- Current pruner (Optuna default, verified against pinned version).
- Confirmation that `trial.report` does not appear in `autotuner.py`.
- Confirmation that NopPruner is a no-op given the objective shape.
- The methodology-change linkage: any future intermediate-report
  addition forces a pruner re-decision.

### D5 — No behavior change

The audit is **PIN-and-DOCUMENT**. Trial-completion behaviour is
byte-identical before and after. The BHY haircut's input trial set
is byte-identical.

## Dependencies

- **NOT blocked by** any other plan or migration.
- **Soft-coupled to** the sampler-choice plan (same code-region edit).

## Golden-fixture tests required

### T1 — Pruner is Nop (positive pin)

Inspect the `study` object after `create_study`. Assert
`isinstance(study.pruner, optuna.pruners.NopPruner)`.

### T2 — Pruner family constant pinned

Assert `ACTIVE_OPTUNA_PRUNER_FAMILY == "NOP"`.

### T3 — `trial.report` / `should_prune` absent (D3)

Static-analysis-style: read `autotuner.py` source; assert it contains
NEITHER `"trial.report"` NOR `".should_prune"`. Tripwire against a
future PR.

### T4 — Pruner kwargs documented at call site

Read the source around `create_study`; assert the `pruner=...`
construction is accompanied by a comment block explaining the choice
and the methodology-change linkage.

## Definition of Done

1. T1-T4 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite, unchanged behaviour.
3. `findings.md` committed.
4. The `pruner=` kwarg is explicit at every `create_study` call site.
5. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   explicit NopPruner instantiation (project rule 1 — pruner
   methodology pin); trial.report static guard; n_trials=500;
   objective=<unchanged>`.

## Risk callouts

- **Silent pruner activation via `trial.report`.** The exact bug shape
  the static guard prevents: a future PR adds
  `trial.report(intermediate_guard_alpha, step=day_idx)` inside the
  simulation loop "to monitor progress." With the default
  `MedianPruner`, this would prune slow-starting trials. With NopPruner,
  it's a no-op (still safe), but the static guard catches the addition
  AT EDIT TIME so the implementing team explicitly considers whether
  to pivot to a Hyperband-style architecture (a real methodology
  change).
- **Optuna upgrade churn.** Optuna has changed its default pruner
  between versions. Explicit instantiation at the call site
  immunizes against upstream default drift.
- **HyperbandPruner temptation.** A future Phase-2 cycle that
  introduces longer per-trial simulation times (heavy path-bank
  generation) might argue for Hyperband to "kill obviously-bad trials
  early." That is a methodology change — the haircut's c(N) is
  calibrated over the FULL N trials; Hyperband's adaptive bracket
  structure breaks that. Surface to PM. This plan documents the
  trade-off; it does not permit it.
- **`MedianPruner` as default-tomorrow.** If Optuna's default flips
  between versions, the implicit-default situation re-emerges. The
  named-constant + explicit-instantiation discipline guards this.
- **BHY interaction.** The `_haircut_select` filter
  (`autotuner.py:1034`) keeps only trials with non-None values
  (`completed_trials = [t for t in study.trials if t.value is not
  None]`). Pruned trials have `value=None` and would be filtered out
  silently. With NopPruner, no trial is pruned, so this filter is a
  no-op safety net. T1 confirms NopPruner; the filter remains as the
  belt-and-suspenders.

## Out of scope

- Actually changing the pruner — methodology change.
- Adding intermediate `trial.report` to the objective — would force a
  methodology re-decision.
- The sampler choice — owned by the sampler-choice plan.
- The c(N) calibration assumption — owned by the BHY-implementation
  audit plan.
- Phase-2 architectural changes that might warrant Hyperband-style
  pruning — out of scope; future cycle, PM-gated.
