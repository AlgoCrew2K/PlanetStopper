# Engine Audit — Optuna Sampler Choice

**Feature:** Audit the autotuner's Optuna sampler. Currently
`optuna.create_study(...)` (`autotuner.py:1009`) accepts the default
sampler (`TPESampler`). Verify the choice is correct and justified;
make it **explicit named** (sampler instance constructed in code) so the
methodology-change rule has a single point of enforcement.

**Phase:** Engine audit (post-Phase-1; methodology-change-discipline
hardening — NOT a behavior change unless audit finds a defect)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the "let me just swap samplers silently" PR shape).

## Source-of-truth references

- `.claude/CLAUDE.md` (project) — agent operating rule 1:
  "Every sampler or pruner change is a methodology change. Surface to PM
  before implementing; do not silently swap `TPESampler` for
  `CmaEsSampler` or any equivalent substitution."
- `autotuner.py:1009` — `optuna.create_study(study_name=..., storage=...,
  load_if_exists=False, direction="maximize")` — NO `sampler=` kwarg;
  Optuna defaults to `TPESampler(seed=None)`.
- `autotuner.py:319-356` — `benjamini_hochberg_adjust`: comment at
  lines 330-333 explicitly states "the Optuna trial statistics are NOT
  independent (the TPE sampler concentrates the search), so plain
  Benjamini-Hochberg 1995 — which assumes independence / PRDS — would
  under-correct the false-discovery rate by a factor of c(N)."
- `docs/handoff/decision-science-council-synthesis.md` §3.5 — BHY
  haircut preserved unchanged under Phase 1; the c(N) calibration
  assumes TPE-induced dependence.

## Why

The TPE sampler is the **load-bearing dependency-structure assumption**
of the Yekutieli c(N) factor in the BHY haircut. The inline comment at
`autotuner.py:330-333` explicitly justifies c(N) by reference to TPE.
If a maintainer swaps the sampler to something with different
dependency properties (e.g. `RandomSampler` — independent trials,
making c(N) over-conservative; `CmaEsSampler` — strongly correlated
within a generation, potentially under-correcting), the BHY haircut's
calibration silently shifts.

Today, the sampler is **implicit**: Optuna's default. The audit's
output is:

1. **Verify** the default is `TPESampler` AND its seed behaviour is
   correct for the n_jobs=-1 parallelism (see the parallelism/
   reproducibility audit plan).
2. **Make explicit:** pass `sampler=TPESampler(seed=...)` to
   `optuna.create_study(...)` so the choice is at the call site, not
   in Optuna's default.
3. **Document the c(N)-dependency link** at the sampler instantiation.

If the audit finds the default has changed (Optuna upgrades have
historically toggled `TPESampler` default `multivariate` flag, etc.),
surface to PM. This plan does NOT change the sampler; it documents
and pins it.

## Deliverables

### D1 — Explicit sampler instantiation

In `run_autotuner`, before `optuna.create_study`:

```python
# Sampler: TPE — the load-bearing dependency-structure assumption of
# the BHY haircut's Yekutieli c(N) factor (autotuner.py:330-333).
# A sampler swap is a methodology change (project rule 1) — surface to
# PM first. The seed argument controls reproducibility (see the
# parallelism/reproducibility audit plan).
sampler = optuna.samplers.TPESampler(
    seed=_resolve_optuna_seed(),  # see parallelism/reproducibility plan
)
study = optuna.create_study(
    study_name=f"{study_timestamp}__{normalized_name}",
    storage=storage,
    load_if_exists=False,
    direction="maximize",
    sampler=sampler,
)
```

The exact `TPESampler` parameters (multivariate, group, constant_liar
under parallelism, etc.) are documented at the call site by inline
comments referencing the Optuna version pinned in the project's
`requirements.txt`.

### D2 — Named constant for sampler family

```python
# Named for cross-file inspectability — the BHY c(N) comment can
# reference this constant rather than re-explaining the dependency
# structure.
ACTIVE_OPTUNA_SAMPLER_FAMILY = "TPE"
```

The BHY block's inline comment (`autotuner.py:330-333`) gains a
back-reference to `ACTIVE_OPTUNA_SAMPLER_FAMILY`. A future PR changing
the family must update both ends, surfacing the methodology change.

### D3 — Methodology-change tripwire test

A NEW test:

```
T_sampler_family_pin — assert ACTIVE_OPTUNA_SAMPLER_FAMILY == "TPE".
A PR that flips this constant fails the test, forcing PM surfacing.
```

### D4 — Audit findings record

The audit produces a short findings report in
`feature-plans/decision-science/engine-audit/sampler-choice/findings.md`
(committed alongside the plan). The report records:
- The current sampler (verified against Optuna defaults as of the
  pinned version).
- The current sampler's default kwargs.
- A literature reference for TPE (Bergstra et al. 2011, "Algorithms
  for Hyper-Parameter Optimization") and a one-paragraph note on its
  dependency structure across trials.
- The c(N) calibration assumption: TPE's intra-search concentration
  is the council-converged justification for the Yekutieli c(N)
  factor over plain Benjamini-Hochberg.
- Whether any Optuna version upgrade between the original c(N)
  decision and the current pin changed the default sampler or its
  defaults. If yes, surface to PM with an explicit ask.

### D5 — No behavior change

The audit is **PIN-and-DOCUMENT**, not change. The sampler stays TPE.
The only code-level diff: an explicit `sampler=TPESampler(seed=...)`
kwarg at the `create_study` site.

If the audit finds a defect (e.g. Optuna upgraded its TPE defaults in
a way that changed dependency structure), the finding is surfaced; a
follow-up cycle (separate plan, separate PM review) addresses it. This
plan ships only the pin.

## Dependencies

- **Soft-coupled to:** Engine Audit parallelism/reproducibility plan
  (the seed argument's source).
- **NOT blocked by** any persistence-architect migration — code-only.

## Golden-fixture tests required

### T1 — Sampler is TPE (positive pin)

Inspect the `study` object after `create_study`. Assert
`isinstance(study.sampler, optuna.samplers.TPESampler)`.

### T2 — Sampler family constant pinned

Assert `ACTIVE_OPTUNA_SAMPLER_FAMILY == "TPE"`.

### T3 — BHY back-reference present

Static-analysis-style: assert the BHY block's comment near
`autotuner.py:330` references `ACTIVE_OPTUNA_SAMPLER_FAMILY` OR the
literal string "TPE".

### T4 — Sampler kwargs documented at call site

Read the source around `create_study`; assert the `sampler=...`
construction is on a line within 5 lines of `create_study`, in a code
block accompanied by a comment block referencing the project rule and
the c(N) link.

## Definition of Done

1. T1-T4 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite, unchanged behaviour.
3. `findings.md` committed alongside this plan.
4. The `sampler=` kwarg is explicit at every `create_study` call site
   (currently one — `autotuner.py:1009`).
5. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   explicit TPESampler instantiation (project rule 1 — methodology
   pin); BHY c(N) back-reference; n_trials=500;
   objective=<unchanged>`.

## Risk callouts

- **Silent sampler swap.** The single most likely failure: a future
  PR swaps `TPESampler` for `CmaEsSampler` "to try CMA-ES" without
  realizing c(N) is calibrated for TPE. T1 / T2 catch the static form.
  The PR-review-time scrutiny on the back-reference in the BHY block
  (T3) is the social safeguard.
- **Optuna default upgrades.** Optuna's default `TPESampler`
  parameters have changed between minor versions (multivariate
  defaults, constant_liar behaviour). Explicit instantiation at the
  call site immunizes the autotuner from upstream default drift.
- **Seed argument coupling.** The seed comes from
  `_resolve_optuna_seed()` (defined in the parallelism/reproducibility
  plan). If that plan does not ship in the same cycle, the audit's D1
  cannot complete. Sequence: ship parallelism/reproducibility first,
  then sampler.
- **`n_jobs=-1` + TPE.** TPE's standard form is sequential — under
  `n_jobs=-1`, Optuna applies a constant-liar style smoothing of
  pending trials. The exact behaviour is Optuna-version-dependent
  and is the parallelism/reproducibility plan's concern; this plan
  documents the linkage at the sampler call site.
- **CMA-ES temptation.** Optuna documentation recommends CMA-ES for
  continuous parameter spaces (which the autotuner's 6-D search
  largely is). The recommendation is valid for SEARCH POWER, not for
  the dependency structure the BHY haircut assumes. The trade-off
  must be evaluated as a methodology change — surface to PM, NOT
  swap silently.

## Out of scope

- Actually changing the sampler — out of scope; methodology change.
- The Optuna seed argument's source — owned by the parallelism/
  reproducibility audit plan.
- BHY machinery — preserved per the Phase-1 BHY preservation plan.
- Multivariate TPE tuning — out of scope unless the audit finds a
  defect.
- A CMA-ES feasibility study — out of scope; if pursued, a separate
  plan with a methodology-change PM gate.
