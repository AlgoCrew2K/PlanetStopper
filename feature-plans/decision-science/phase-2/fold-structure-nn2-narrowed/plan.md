# Phase 2 — Fold-Structure Considerations (NN2 Narrowed)

**Feature:** Encode the narrowed NN2 ruling: a **rolling purged k-fold**
fold structure is required **only if** a CVaR-VALUED Optuna objective is
ever introduced. The 60/20/20 standard is preserved for the CRRA-mean
objective (small-sample-estimable on n≈5). Phase 2's default keeps
60/20/20; the rolling-k-fold change is a Phase-2-entry decision tied to
introducing a CVaR-valued objective.

**Phase:** Phase 2 (HONEST RESHAPE — evidence-gated)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the "let me just switch to k-fold because more
folds is always better" PR shape).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.5 (HARDEN
  Phase 1 keeps 60/20/20 — "M1's CRRA-mean objective is a sample mean
  of a bounded transform, **small-sample-estimable** on the ~4-5-day
  frozen fold (the standard error of a mean is merely *wide* at n≈5,
  not undefined). **No rolling k-fold needed.** This is H-3 PASS and
  it also closes the NN2 question: NN2's route-(a)/(b) dilemma only
  ever bit a CVaR-*valued* Optuna objective; no finalist uses one.").
- `autotuner.py:149-159` — the three-fold split ratios + assertion.
- `autotuner.py:154-156` — `TRAIN_RATIO = 0.60`, `VALIDATION_RATIO =
  0.20`, `FROZEN_EVAL_RATIO = 0.20` named constants.
- `autotuner.py:129` — `PURGE_DAYS = 20` (max of vol=20, ATR=15
  lookbacks).
- `autotuner.py:147` — `EMBARGO_DAYS = 1` (within-fold serial-dependence
  embargo).
- `autotuner.py:860-868` — the OOS-fold-collapse v2 documented tradeoff
  (validation/frozen windows shrink to ~4-5 days after purge).

## Why

NN2 was the unresolved fold-structure question raised during the
council's adversarial debate: a CVaR-valued objective on a ~4-5-day
frozen fold has insufficient tail observations for ES-validation
power, forcing a choice between (a) a rolling purged k-fold (more
windows, more total data) or (b) accepting weak frozen-eval power.

The council's converged ruling (§3.5): **no finalist uses a CVaR-valued
Optuna objective.** The Phase-1 CRRA-EU objective is a sample MEAN
of a CRRA-transformed series — a bounded mean, small-sample-estimable
at n≈5 (wide standard error, but finite). The Phase-2
gamma-2d-search-space plan also preserves the CRRA-EU `mean(U)`
objective (with gamma as the searched dimension); the CVaR
co-signal enters as a *budget-shape*, not as the objective. So
NN2 is closed for both Phase-1 and the default Phase-2 reshape.

NN2 re-opens **only** if a future Phase-2 cycle introduces a
CVaR-VALUED Optuna objective (e.g. "maximize CVaR improvement
across trials"). The plan encodes this as a tripwire: changing the
deployment objective to a CVaR-valued shape forces a fold-structure
re-decision.

## Deliverables

### D1 — Disclosure block at the fold-ratio constants

A NEW comment block alongside `TRAIN_RATIO` / `VALIDATION_RATIO` /
`FROZEN_EVAL_RATIO` (`autotuner.py:149-156`):

```
NN2 (council §3.5 — narrowed): the 60/20/20 three-fold split is
preserved for any MEAN-VALUED Optuna objective (Phase-1 CRRA-EU and
Phase-2 CRRA-EU-with-CVaR-shaping both fit this — mean of a bounded
transform is small-sample-estimable at n≈5; standard error wide but
finite).
NN2 RE-OPENS ONLY IF a future cycle introduces a CVaR-VALUED Optuna
objective (e.g. maximize CVaR improvement across trials). A CVaR at
the 5% tail on a ~4-5-day frozen fold has 0-1 tail observations —
insufficient for ES validation power. In that case, fold-structure
MUST be re-decided between:
  (a) rolling purged k-fold (more windows, more total tail-obs)
  (b) accepting weak frozen-eval power explicitly
A PR that changes the deployment objective to a CVaR-valued shape
without re-deciding fold structure is a Gate-1 review fail.
```

### D2 — `OBJECTIVE_FAMILY` named constant + validator

A NEW named constant in `autotuner.py`:

```python
# Objective-family classification — drives the fold-structure choice
# (council §3.5 / NN2). MEAN_VALUED objectives are sample means of a
# bounded transform (Sortino legacy, CRRA-EU); CVAR_VALUED objectives
# are tail-quantile-valued. NN2 narrows: only CVAR_VALUED forces a
# k-fold reconsideration.
OBJECTIVE_FAMILY_MEAN_VALUED  = "MEAN_VALUED"
OBJECTIVE_FAMILY_CVAR_VALUED  = "CVAR_VALUED"

# Active family — Phase 1: MEAN_VALUED (CRRA-EU mean(U)). Phase 2
# default: MEAN_VALUED (CRRA-EU with CVaR-shaping; CVaR is a budget
# constraint, not the optimization target).
ACTIVE_OBJECTIVE_FAMILY = OBJECTIVE_FAMILY_MEAN_VALUED
```

A NEW runtime validator at the top of `run_autotuner`:

```python
def validate_fold_structure_for_objective():
    """Fail-loud if the fold structure is incompatible with the active
    objective family.

    MEAN_VALUED + 60/20/20: PASS (council §3.5).
    CVAR_VALUED + 60/20/20: FAIL — a CVaR at 5% tail on ~4-5-day
        frozen fold has 0-1 tail observations; ES validation power is
        structurally zero. Re-decide fold structure (k-fold) before
        introducing a CVaR-valued objective.
    """
    if ACTIVE_OBJECTIVE_FAMILY == OBJECTIVE_FAMILY_CVAR_VALUED:
        if (TRAIN_RATIO, VALIDATION_RATIO, FROZEN_EVAL_RATIO) == (0.60, 0.20, 0.20):
            raise RuntimeError(
                "NN2 VIOLATION: CVAR_VALUED objective + 60/20/20 split. "
                "A CVaR at 5% tail on a ~4-5-day frozen fold has "
                "0-1 tail observations — ES validation power is "
                "structurally zero. Re-decide fold structure (council "
                "§3.5 — rolling purged k-fold or explicit acceptance "
                "of weak power)."
            )
```

The validator is a tripwire: it never fires under Phase 1 or the
default Phase 2. It fires the moment a maintainer flips
`ACTIVE_OBJECTIVE_FAMILY` without addressing the fold structure.

### D3 — 125-trading-day standard preservation

The plan does NOT propose any change to:
- `PURGE_DAYS = 20`
- `EMBARGO_DAYS = 1`
- `TRAIN_RATIO = 0.60`
- `VALIDATION_RATIO = 0.20`
- `FROZEN_EVAL_RATIO = 0.20`
- The 125-trading-day history-window standard (`autotuner.py:866` —
  documented at 125 trading days for the rolling-window math).

The agent operating-rule "validate that `window_length` and `step_size`
are consistent with the project's 125-trading-day standard before
changing either value" is honored: this plan **does not change** any
of them.

### D4 — `OOS-fold-collapse v2` documented tradeoff carried forward

The Phase-1 cycle's documented tradeoff (`autotuner.py:862-868` —
purge collapses validation/frozen windows to ~4-5 days) carries through
Phase 2 unchanged. This plan does NOT reduce trial count or compress
the fold — only re-affirms the tradeoff is acceptable under
MEAN_VALUED objectives.

### D5 — Explicit "k-fold deferred" record

A NEW comment in `autotuner.py` near the OOS-collapse v2 documentation:

```
NN2 (deferred): a rolling purged k-fold structure was considered for
expanded frozen-eval power. Per council §3.5, the rolling k-fold is
NOT needed under the active MEAN_VALUED objective (a bounded mean is
small-sample-estimable at n≈5). The k-fold remains a future workstream
ONLY if a CVAR_VALUED objective is ever introduced. The current
60/20/20 + purge + embargo is the council-converged honest minimum;
the wide validation-fold standard error is the COST of honest OOS
reporting, not a defect.
```

### D6 — `n_trials = 500` floor not lowered

The plan does NOT propose a reduction of `n_trials` (autotuner.py:1010).
Per the project's autotuner charter: "Never reduce trial count below
100 without explicit user direction (statistical stability floor)."
The current 500 is well above the 100-floor and matches the BHY
machinery's c(N) calibration scale. No change.

## Dependencies

- **Blocked by:** Phase 1 — M1 CRRA-EU objective plan (the MEAN_VALUED
  classification it establishes).
- **Coupled to:** Phase 2 gamma-2d-search-space plan (gamma searches;
  objective stays MEAN_VALUED).
- **Coupled to:** Phase 2 multi-testing-tail-obs-accounting plan (a
  future CVAR_VALUED objective would invoke both this plan's k-fold
  re-decision AND that plan's T-source discipline).
- **NOT blocked by** any persistence-architect migration — the
  validator is code-only.

## Golden-fixture tests required

### T1 — MEAN_VALUED + 60/20/20 PASSES validation

Fixture: `ACTIVE_OBJECTIVE_FAMILY = OBJECTIVE_FAMILY_MEAN_VALUED`,
60/20/20 split. Assert `validate_fold_structure_for_objective()` does
NOT raise. This is the steady-state Phase-1/Phase-2 default; the
validator is silent.

### T2 — CVAR_VALUED + 60/20/20 RAISES

Fixture: monkeypatch `ACTIVE_OBJECTIVE_FAMILY` to CVAR_VALUED. Assert
`validate_fold_structure_for_objective()` raises `RuntimeError` with
a message mentioning "NN2", "CVAR_VALUED", and "0-1 tail observations".

### T3 — CVAR_VALUED + k-fold PASSES (forward-compat shape)

Fixture: monkeypatch `ACTIVE_OBJECTIVE_FAMILY` to CVAR_VALUED AND
override the fold structure to a hypothetical k-fold marker (e.g.
`USE_ROLLING_KFOLD = True`). Assert the validator does NOT raise.
This catches the "if a future cycle adds k-fold, the validator
correctly stops blocking" forward-compat.

The exact shape of `USE_ROLLING_KFOLD` and how the validator detects
it is the implementing team's choice — at minimum, a separate
constant the validator checks before raising.

### T4 — 60/20/20 ratios unchanged (regression)

Static-analysis-style: assert `TRAIN_RATIO == 0.60`,
`VALIDATION_RATIO == 0.20`, `FROZEN_EVAL_RATIO == 0.20`. Tripwire
against a "let me just nudge the ratios" PR.

### T5 — `n_trials >= 100` floor

Static-analysis-style: assert the `study.optimize(...,
n_trials=N)` call in `run_autotuner` has `N >= 100`. Tripwire against
a "let me cut to 50 trials, it's faster" PR (project autotuner rule
3: "Never reduce trial count below 100 without explicit user
direction").

### T6 — 125-trading-day history-window unchanged

Read `synthetic_history.generate_synthetic_history` (call site
`autotuner.py:897`); assert the docstring / call configuration still
references 125 trading days. (The actual history-window source of
truth lives in `synthetic_history.py` — this test is a regression
guard at the autotuner consumer site.)

### T7 — `ACTIVE_OBJECTIVE_FAMILY` value pin

Assert `ACTIVE_OBJECTIVE_FAMILY == OBJECTIVE_FAMILY_MEAN_VALUED` AS OF
the Phase-2 cutover. A PR that flips it to CVAR_VALUED without
addressing the fold structure triggers T2 at runtime AND fails T7 at
edit time.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite.
3. `ACTIVE_OBJECTIVE_FAMILY`, `OBJECTIVE_FAMILY_*` constants and
   `validate_fold_structure_for_objective()` live in `autotuner.py`
   with the D1, D2, D5 disclosure blocks.
4. `validate_fold_structure_for_objective()` is invoked at the top of
   `run_autotuner`, BEFORE `optuna.create_study`. Alongside
   `validate_search_space_nn1()` from the NN1 plan.
5. Fold ratios, purge/embargo, trial count, and history-window
   unchanged.
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>, NN2
   narrowed — fold-structure validator for MEAN_VALUED vs CVAR_VALUED
   objectives (council §3.5); 60/20/20 unchanged; n_trials=500
   unchanged; objective=CRRA-EU-with-CVaR-shaping mean(U)
   (MEAN_VALUED)`.

## Risk callouts

- **"More folds is always better" temptation.** A maintainer with a
  classical ML background reasonably argues "a rolling k-fold gives
  more statistical power; let's switch." The council §3.5 ruling is:
  k-fold's value depends on the OBJECTIVE FAMILY. For a sample mean
  of a bounded transform, n≈5 is sufficient (wide standard error, but
  finite; statistically informative). The cost of a switch — the
  additional non-stationarity exposure across rolling windows, the
  schema additions — is not justified by the marginal power gain at
  the current data scale. The validator's silence under MEAN_VALUED
  documents this trade-off; T2's RAISE makes the cost explicit when
  the trade-off changes.
- **`ACTIVE_OBJECTIVE_FAMILY` flip without fold-structure work.** The
  exact shape of the bug the validator catches: a future cycle adds a
  `compute_cvar_objective` function, flips `ACTIVE_OBJECTIVE_FAMILY`,
  but does NOT touch the fold structure. The runtime check fires
  before the first trial runs; the cycle stops with a clear message.
- **Ratio drift.** "Let me just bump frozen_eval to 25%." T4
  catches the static form. The implementing team must justify any
  ratio change as a methodology change (project autotuner rule 4 — the
  125-day standard) and surface to PM first.
- **`n_trials` cut.** Most common pressure: "the autotuner is slow,
  let me lower trials to 200." T5 catches anything < 100. Between 100
  and 500, the cut is a methodology change that requires PM/user
  surface (project autotuner rule 3).
- **History-window drift.** `synthetic_history.generate_synthetic_history`
  pulls 125 trading days. The autotuner's `total_days` check
  (`autotuner.py:911-913`) currently requires only 2 days, which is
  permissive on purpose. T6 is a regression guard at the consumer
  site.
- **K-fold as out-of-band escape hatch.** If a future cycle introduces
  k-fold via a side-channel (e.g. by pre-computing rolling folds
  outside `run_autotuner`), the validator does NOT catch it.
  Mitigation: T3's forward-compat shape requires the k-fold change to
  be encoded in a discoverable constant the validator inspects. The
  implementing team commits to NOT bypassing the validator.
- **Port-mode tradeoff (separate but visible).** The port-mode split
  is 50/20/30 (autotuner.py:166-168), AMENDMENT F2 — a wider frozen
  fold. This plan does NOT propose changes to port-mode; it preserves
  the existing port-mode discipline. The validator MAY want a port-
  mode variant of the assertion in a future cycle; out of scope here.

## Out of scope

- A rolling purged k-fold implementation — explicitly deferred per
  council §3.5; only built when a CVAR_VALUED objective is introduced.
- Changes to `PURGE_DAYS`, `EMBARGO_DAYS`, or the 60/20/20 ratios —
  explicit preservation discipline; methodology changes require PM
  surface.
- History-window changes (the 125-trading-day standard) — preserved.
- Trial-count changes — preserved at 500 (well above the 100-floor).
- The port-mode 50/20/30 split — out of scope; preserved.
- The CVaR-valued objective itself, if one is ever introduced — owned
  by a future Phase-2 cycle's plan; this plan only installs the
  tripwire.
- W-H5 serial-correlation remediation — the Engine Audit BHY plan
  owns that workstream.
