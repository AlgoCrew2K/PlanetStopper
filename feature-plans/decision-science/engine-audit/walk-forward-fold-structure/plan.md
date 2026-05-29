# Engine Audit — Walk-Forward Fold Structure

**Feature:** Audit the 60/20/20 three-fold split, `PURGE_DAYS=20`,
`EMBARGO_DAYS=1`, the two boundary purges, and the OOS-fold-collapse v2
tradeoff. Verify against López de Prado 2018 Ch. 7.4; pin the
invariants; surface drift.

**Phase:** Engine audit (post-Phase-1; correctness-discipline hardening
— NOT a behavior change)

**Owner agent-type:** `optuna-specialist` (drives), `risk-engine-specialist`
(reviews the purge sizing against the feature-lookback constants),
`quant-test-writer` (RED).

## Source-of-truth references

- `.claude/CLAUDE.md` (project) — agent operating rule 4: "Walk-forward
  windows: validate that `window_length` and `step_size` are consistent
  with the project's 125-trading-day standard before changing either
  value."
- `autotuner.py:120-131` — `PURGE_DAYS = 20` derivation comment (max
  of vol=20, ATR=15 lookbacks).
- `autotuner.py:133-147` — `EMBARGO_DAYS = 1` derivation comment.
- `autotuner.py:149-159` — `TRAIN_RATIO = 0.60` /
  `VALIDATION_RATIO = 0.20` / `FROZEN_EVAL_RATIO = 0.20` + the assertion
  that they sum to 1.0.
- `autotuner.py:161-171` — port-mode 50/20/30 split (Amendment F2).
- `autotuner.py:844-868` — run_autotuner docstring describing the
  walk-forward methodology and the OOS-fold-collapse v2 tradeoff.
- `autotuner.py:889-959` — actual split / purge / embargo
  implementation.
- `synthetic_history.py` — the 125-trading-day history producer.

## Why

The walk-forward fold structure is the single biggest methodology
surface in the autotuner. Every haircut decision, every selection-bias
calibration, every Gate-1 / Gate-2 parity claim assumes the fold
structure is sound. A silent drift in any of these constants
invalidates the entire stack downstream.

This audit pins the constants as invariants and verifies the
implementation matches the documentation. Per López de Prado 2018
Ch. 7.4, the held-out frozen-eval invariant + purge + embargo is the
core requirement; the specific 60/20/20 ratio is an operator choice
for AlphaBot's 125-day scale.

The audit identifies risks to surface to PM if encountered:
- `PURGE_DAYS = 20` derives from `max(vol_lookback=20, atr_lookback=15)`.
  If either lookback constant changes in `math_engine.py`, `PURGE_DAYS`
  must change in lock-step.
- `EMBARGO_DAYS = 1` derives from an estimated guard-alpha lag-1
  autocorrelation horizon. If the autotuner's deployment objective
  changes (e.g. introduces a multi-day overlap), the embargo sizing
  must be re-evaluated.

## Deliverables

### D1 — `PURGE_DAYS` lock-step assertion

A NEW startup assertion at the top of `run_autotuner`:

```python
def validate_purge_against_lookbacks():
    """Fail-loud if PURGE_DAYS no longer matches max(feature lookbacks).

    Project rule 4: walk-forward windows must be consistent with the
    feature-lookback constants. A drift in math_engine.py's
    vol/ATR lookback constants without a matching PURGE_DAYS update
    silently leaks feature-window data across the train/validation
    boundary.
    """
    vol_lookback = math_engine.LOOKBACK_DAYS
    atr_lookback = math_engine.ATR_LOOKBACK_DAYS
    expected_purge = max(vol_lookback, atr_lookback)
    if PURGE_DAYS != expected_purge:
        raise RuntimeError(
            f"PURGE_DAYS mismatch: configured {PURGE_DAYS} != "
            f"max(LOOKBACK_DAYS={vol_lookback}, "
            f"ATR_LOOKBACK_DAYS={atr_lookback})={expected_purge}. "
            f"A feature-lookback change requires a matching PURGE_DAYS "
            f"update (project rule 4 / López de Prado 2018 Ch. 7)."
        )
```

The validator is invoked at the top of `run_autotuner`, BEFORE the
fold split.

### D2 — Fold-ratio invariant tests

A NEW test:

```
T_fold_ratios_pin —
- assert TRAIN_RATIO == 0.60
- assert VALIDATION_RATIO == 0.20
- assert FROZEN_EVAL_RATIO == 0.20
- assert PORT_TRAIN_RATIO == 0.50
- assert PORT_VALIDATION_RATIO == 0.20
- assert PORT_FROZEN_EVAL_RATIO == 0.30
- assert abs(TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO - 1.0) < 1e-9
- assert abs(PORT_TRAIN_RATIO + PORT_VALIDATION_RATIO + PORT_FROZEN_EVAL_RATIO - 1.0) < 1e-9
```

The existing source-level assertions (`autotuner.py:157-159`, `:169-171`)
are belt-and-suspenders to this test.

### D3 — Purge / embargo invariant tests

A NEW test:

```
T_purge_embargo_pin —
- assert PURGE_DAYS == 20
- assert EMBARGO_DAYS == 1
- assert PURGE_DAYS == max(math_engine.LOOKBACK_DAYS, math_engine.ATR_LOOKBACK_DAYS)
```

### D4 — Two-boundary purge implementation test

A NEW integration test:

```
T_two_boundary_purge —
1. Construct a deterministic 125-day history fixture.
2. Run the fold split logic from run_autotuner (extracted to a pure
   helper for testability).
3. Assert:
   - train | validation boundary: train_dates excludes the last
     PURGE_DAYS + EMBARGO_DAYS = 21 train days.
   - validation | frozen-eval boundary: validation_dates_purged
     excludes the last PURGE_DAYS + EMBARGO_DAYS = 21 validation days.
   - validation_dates_full retains all raw validation days (the OOS
     cascade contract — autotuner.py:943-944).
   - frozen_dates is raw_frozen_dates unchanged.
   - train_dates ∩ validation_dates_purged == ∅.
   - validation_dates_purged ∩ frozen_dates == ∅.
   - The intersection of train_dates with the day immediately
     preceding the train | validation boundary is empty (purge end
     index correctness).
```

### D5 — History-window assertion

A NEW assertion in `run_autotuner` after fetching history:

```python
# Project rule 4: the 125-trading-day window is the standard. A
# materially different total_days indicates either a synthetic_history
# defect or a forced manual override; surface either case.
if not (100 <= total_days <= 150):
    print(
        f"  -> WARNING: history window has {total_days} days, "
        f"expected ~125 (project rule 4)."
    )
```

This is a soft warning, not a hard fail (synthetic_history may have
legitimate small variations); a corresponding test asserts the
warning fires when total_days is materially outside the window.

### D6 — OOS-fold-collapse v2 documented tradeoff carried forward

The audit findings reaffirm the documented tradeoff
(`autotuner.py:862-868`): at 125-day history, purge collapses the
validation and frozen-eval windows to ~4-5 usable days. This is the
**cost of honest OOS reporting**, not a defect.

The `findings.md` records the exact day-count at the audit moment:
- raw train days
- usable train days (post-purge)
- raw validation days
- usable validation days (post-purge)
- raw frozen-eval days
- usable frozen-eval days

### D7 — Audit findings record

`findings.md` committed alongside the plan:
- Confirmation that PURGE_DAYS = max(vol, ATR) lookback.
- Confirmation that EMBARGO_DAYS reasoning matches LdP Ch. 7.4
  ~1%-of-observations guidance (at 125 days, ~1 day).
- Confirmation of the 60/20/20 (per-symphony) and 50/20/30 (port-mode)
  ratios.
- Current day-count audit (D6).
- Any drift observed.

## Dependencies

- **Soft-coupled to:** Phase 2 fold-structure NN2-narrowed plan (the
  `validate_fold_structure_for_objective` validator) — both validators
  run at the top of `run_autotuner`; they are orthogonal.
- **NOT blocked by** any persistence-architect migration.

## Golden-fixture tests required

### T1 — Fold ratios pinned (D2)

Static-analysis-style on the constant values.

### T2 — Purge / embargo pinned + lookback-coupled (D3)

Static-analysis + the cross-check assertion.

### T3 — Two-boundary purge implementation (D4)

Integration test on a deterministic 125-day fixture.

### T4 — `validate_purge_against_lookbacks` fail-loud (D1)

Monkeypatch `math_engine.LOOKBACK_DAYS` to 25; assert
`validate_purge_against_lookbacks()` raises with a message naming the
expected vs configured purge.

### T5 — History-window warning (D5)

Fixture: total_days = 80 (materially below 125). Assert the warning
fires.

### T6 — Day-count audit (D6)

At the current configuration, assert the usable validation and
frozen-eval windows are >= 1 day each (the system must not collapse
to zero-day windows even at the 125-day boundary; if it does, that is
a Gate-1 ship-blocker).

### T7 — Port-mode ratios unchanged

Assert the port-mode ratios (50/20/30) are unchanged. Port-mode is a
separate-but-coupled methodology; this audit preserves it.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — unchanged behaviour.
3. `findings.md` committed with the current day-count audit.
4. `validate_purge_against_lookbacks` is invoked at the top of
   `run_autotuner`.
5. The history-window soft-warning fires under materially-different
   totals.
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   walk-forward fold-structure audit — validate_purge_against_lookbacks
   tripwire, 60/20/20 + purge/embargo invariants pinned;
   n_trials=500; objective=<unchanged>`.

## Risk callouts

- **`PURGE_DAYS` / lookback drift.** If `math_engine.LOOKBACK_DAYS` or
  `ATR_LOOKBACK_DAYS` is changed without updating `PURGE_DAYS`,
  feature-window data leaks across the train | validation boundary.
  T4 catches it loudly; D1 is the runtime guard.
- **`EMBARGO_DAYS = 1` may be too small for a multi-day objective.**
  Today's guard-alpha is a same-day difference; EMBARGO_DAYS=1 is
  vindicated. If a future Phase 2 cycle introduces a multi-day
  objective (e.g. n-day-forward CVaR improvement), the embargo must
  be re-sized. The Phase 2 fold-structure NN2-narrowed plan is the
  intersection point; the audit logs this as a future-watch item.
- **Day-count collapse to zero.** At 125-day history, purge leaves
  ~4-5 days each in validation and frozen-eval. A history short of
  ~100 days could collapse one of those folds to zero. T6 catches
  zero-day folds; the soft-warning (D5) catches the precursor.
- **Port-mode drift.** Port-mode's 50/20/30 is a separate Amendment;
  T7 pins it. If a future PR widens port-mode further "for more
  power," it must surface as an Amendment, not slide in via an
  audit.
- **125-day standard.** The history-window is set in
  `synthetic_history.py`. This audit only checks the consumer side;
  changes to the producer would surface here as T5 violations.
- **Documentation drift.** The docstring at
  `autotuner.py:844-868` describes the methodology. If the
  implementation diverges from the docstring, the docstring is the
  contract — the implementation is wrong, not the docstring. The PR
  reviewer reads both.

## Out of scope

- Changing any fold-structure constant — out of scope; the audit
  pins, does not change.
- Rolling purged k-fold — owned by the Phase 2 fold-structure
  NN2-narrowed plan; this audit only verifies the current 3-fold
  shape.
- Feature-lookback constants in `math_engine.py` — owned by
  math_engine; this audit only verifies the autotuner's coupling.
- Port-mode replay validation — owned by the port-mode plan; this
  audit only verifies the ratio constants.
- The synthetic_history 125-day window — owned by
  `synthetic_history.py`; this audit only verifies the consumer.
