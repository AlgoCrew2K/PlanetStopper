# Phase 1 — NN1: Spec-Freeze Discipline (Synthesis Hard Gate)

**Feature:** Encode and enforce the NN1 spec-freeze hard gate in
`autotuner.py` — the generator family and horizon convention may NEVER
be frozen by P&L / backtest selection. Frozen by stylized-fact /
Politis-White / mandate / cadence, OUTSIDE the Optuna search space.

**Phase:** Phase 1 (HARDEN floor — overfitting-accounting spine)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED — test-writer hunts for facets that *could* be
P&L-toured silently).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.5 (NN1 verbatim
  — the synthesis hard gate, "not a methodology preference; it is the
  precondition that keeps the BHY haircut TRUE"), §3.3 ("adds three
  specification facets … all frozen by theory/a-priori choice and
  pre-registration, none by backtest P&L"), §3.7 (`spec_bundles` and
  `spec_facets` with `freeze_discipline` and `evidence_source`).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0
  bullet 4 (verified NN1 is correct and load-bearing), §A.5 (H-5 — the
  Phase-1 floor removes R3 only; R1/R2 wait for M3), §B.3 route 2
  (Defect 2 — a P&L-toured spec with its own sub-sweep contributes its
  full sub-sweep count).
- `docs/handoff/council-converged-migration-plan.md` §3.1 (migration 015
  `spec_bundles` + `spec_facets` with `freeze_discipline` ENUM;
  migration 020 `researcher_dof_ledger.evidence_source` ENUM).
- `autotuner.py:52-79` — current `OPTUNA_SEARCH_SPACE_KEYS` and the
  search-space named bounds.

## Why

The BHY haircut's Yekutieli `c(N)` factor corrects multiple-testing
**only over the Optuna trial search `benjamini_hochberg_adjust` can
see**. A spec facet (generator family, horizon, gamma, utility family,
wealth argument, regime-bucket boundaries) chosen by looking at strategy
P&L is an UNCOUNTED testing event — the haircut understates its
effective N, the FDR gate is silently miscalibrated, the haircut
becomes a **lie by omission** (synthesis §2.5 verbatim).

NN1 is therefore not a methodology *preference*. It is the **precondition
that keeps the BHY haircut TRUE**. Violating NN1 is not a "best-practice"
miss; it is a correctness defect of the entire FDR machinery.

The additive `N_effective = N_optuna + S` accounting (separate plan) is
the **structural enforcement** of NN1 — a tripwire that bumps `N` when
NN1 is violated. This plan installs the **rule itself**: the discipline
and the test surface that prevent NN1-violation facets from being
introduced at all.

## Deliverables

### D1 — `FREEZE_DISCIPLINE_ENUM` and `EVIDENCE_SOURCE_ENUM` named constants

In `autotuner.py` (single source of truth for the autotuner-side
consumer; the schema-side constants ship with migration 015 / 020):

```python
# NN1 — synthesis hard gate (council §2.5). The acceptable freeze
# disciplines for any spec_facets row. BACKTEST_SELECTION is the
# NN1-violation tripwire — present in the enum so the violation has a
# name, never silently as a fallback for unclassifiable rows.
FREEZE_DISCIPLINE_THEORY               = "THEORY"
FREEZE_DISCIPLINE_MANDATE              = "MANDATE"
FREEZE_DISCIPLINE_STYLIZED_FACT        = "STYLIZED_FACT"
FREEZE_DISCIPLINE_POLITIS_WHITE        = "POLITIS_WHITE"
FREEZE_DISCIPLINE_CADENCE              = "CADENCE"
FREEZE_DISCIPLINE_CALIBRATION          = "CALIBRATION"
FREEZE_DISCIPLINE_BACKTEST_SELECTION   = "BACKTEST_SELECTION"  # NN1 VIOLATION

NN1_HONEST_DISCIPLINES = frozenset({
    FREEZE_DISCIPLINE_THEORY,
    FREEZE_DISCIPLINE_MANDATE,
    FREEZE_DISCIPLINE_STYLIZED_FACT,
    FREEZE_DISCIPLINE_POLITIS_WHITE,
    FREEZE_DISCIPLINE_CADENCE,
    FREEZE_DISCIPLINE_CALIBRATION,
})

EVIDENCE_SOURCE_THEORY               = "THEORY"
EVIDENCE_SOURCE_MANDATE              = "MANDATE"
EVIDENCE_SOURCE_STYLIZED_FACT        = "STYLIZED_FACT"
EVIDENCE_SOURCE_BACKTEST_SELECTION   = "BACKTEST_SELECTION"  # NN1 VIOLATION
EVIDENCE_SOURCE_OOS                  = "OOS"                 # WORSE violation
```

The enums are named, source-commented per the no-magic-numbers rule, and
documented as the *single source of truth* for the autotuner's NN1
consumer.

### D2 — `validate_nn1_compliance(spec_bundle_id) → tuple[bool, list[str]]`

A NEW function in `autotuner.py`:

```python
def validate_nn1_compliance(spec_bundle_id: int) -> tuple[bool, list[str]]:
    """Return (is_nn1_honest, violations).

    Reads spec_facets rows for the bundle. NN1-honest iff every facet's
    freeze_discipline is in NN1_HONEST_DISCIPLINES.

    A bundle with even one BACKTEST_SELECTION facet is NN1-violation.
    Violations is a human-readable list naming each violating facet —
    the Overfitting Conscience advisor (Phase 1 computed branch) writes
    these into the advisor_observations row's raw_response.

    NN1 violation does NOT block the autotuner run — the additive
    N_effective accounting is the structural enforcement (the haircut
    will fire harder). This function exists to surface the violation
    LOUD so the team sees it before the haircut alone makes the call.
    """
```

Constraints:
- Reads from state DB (single-DB query, never cross-join).
- Pure with respect to its DB read (callable injection seam for tests).
- Returns deterministic strings — never `f"{value!r}"`-style debug dumps.

### D3 — Phase-1 NN1 facet registry

The three Phase-1-frozen facets per council synthesis §3.3 must be
registered into `spec_facets` at autotune-run setup time with the
explicit disciplines:

| Facet name        | Value (Phase 1)       | freeze_discipline | evidence_source     |
|-------------------|-----------------------|-------------------|---------------------|
| `gamma`           | pre-registered scalar | `THEORY`          | `THEORY`            |
| `utility_family`  | `"CRRA"`              | `THEORY`          | `THEORY`            |
| `wealth_argument` | name of derivation    | `THEORY`          | `THEORY`            |

Each is `frozen_at`-stamped, content-hashed via the parent
`spec_bundles` row, and IMMUTABLE.

The autotuner does NOT write these rows on each run — they are written
ONCE at the Phase-1 cutover (a one-shot registration migration or
fixture-seed step), then read-only thereafter. A change in `gamma`
requires a NEW `spec_bundles` row with a new `bundle_hash` and
`frozen_at`; the old bundle is preserved (immutability).

### D4 — Search-space disclosure block (`autotuner.py`)

A NEW commented block alongside `OPTUNA_SEARCH_SPACE_KEYS`
(`autotuner.py:52-79`) stating verbatim:

```
NN1 (synthesis hard gate — council §2.5): the following facets MUST NEVER
appear in OPTUNA_SEARCH_SPACE_KEYS — they are frozen OUTSIDE the search
space by the spec_bundles registry:
  - gamma                 (THEORY)
  - utility_family        (THEORY)
  - wealth_argument       (THEORY)
  - generator_family      (STYLIZED_FACT)  [Phase 2]
  - horizon_convention    (CADENCE)        [Phase 2]
  - lambda (CVaR budget)  (MANDATE)        [Phase 2]
  - regime_bucket_thresh  (CALIBRATION)    [Phase 2]
Adding any of the above to OPTUNA_SEARCH_SPACE_KEYS is a structural NN1
violation - the Yekutieli c(N) factor would see only the trial-sweep,
not the spec-facet tour, and the haircut would silently understate its
effective N. Adding a NEW name here without classifying it in this
block is a Gate-1 review fail.
```

### D5 — `validate_search_space_nn1` runtime check

At the top of `run_autotuner`, BEFORE `optuna.create_study`:

```python
def validate_search_space_nn1():
    """Fail-loud if OPTUNA_SEARCH_SPACE_KEYS contains a known-frozen
    facet name. Last-line defence against a future PR that adds e.g.
    'gamma' to the search space without removing the spec_bundles
    registration."""
    forbidden_in_search_space = {
        "gamma", "utility_family", "wealth_argument",
        "generator_family", "horizon_convention", "lambda",
        "regime_bucket_thresh",
    }
    leaked = OPTUNA_SEARCH_SPACE_KEYS & forbidden_in_search_space
    if leaked:
        raise RuntimeError(
            f"NN1 VIOLATION: search space contains theory-frozen facet(s) "
            f"{sorted(leaked)} — see council synthesis §2.5 and the "
            f"NN1 disclosure block in autotuner.py. Refusing to start."
        )
```

Fail-loud, not silent. NN1 violation at this level is unrecoverable.

## Dependencies

- **Blocks:** Phase 1 — Additive N_effective accounting (T2 of that plan
  requires the `BACKTEST_SELECTION` enum value to be canonical).
- **Blocks:** Phase 1 — M1 CRRA-EU objective plan (D4 — gamma read from
  `spec_bundles`/`spec_facets`).
- **Blocked by:** persistence-architect's migrations 015 (`spec_bundles`
  + `spec_facets`) and 020 (`researcher_dof_ledger`).
- **Soft-coupled to:** the spec_bundles ↔ N_effective interaction plan
  (separate plan in this folder).

## Golden-fixture tests required

### T1 — NN1-honest bundle passes validation

Fixture: a `spec_bundles` row with three `spec_facets` (gamma,
utility_family, wealth_argument) all `freeze_discipline='THEORY'`.
Assert `validate_nn1_compliance(bundle_id) == (True, [])`.

### T2 — Single BACKTEST_SELECTION facet trips violation

Fixture: a `spec_bundles` row whose gamma facet has
`freeze_discipline='BACKTEST_SELECTION'`. Assert:
- `validate_nn1_compliance(bundle_id) == (False, ["gamma:
  BACKTEST_SELECTION"])`;
- the violation message names the offending facet.

### T3 — OOS_PEEK facet trips harder violation

Fixture: `freeze_discipline='THEORY'` but `evidence_source='OOS'`.
Assert the violation message distinguishes this as a stricter
violation — frozen-eval peek. (The accounting plan's T4 holds the
frozen-eval-tainted row exclusion from `S`; this plan ensures the
alarm fires.)

### T4 — `validate_search_space_nn1` static guard

Monkeypatch `OPTUNA_SEARCH_SPACE_KEYS` to include `"gamma"`. Assert
`validate_search_space_nn1()` raises `RuntimeError` with a message
containing the offending name.

### T5 — Immutability of frozen facets

Attempt to UPDATE a `spec_facets` row in place. Assert it fails (either
by a write-guard in the data-access layer, or by absence of an UPDATE
helper). The convention is "new bundle, new row, never modify."

### T6 — `gamma`-NOT-in-search-space negative pin

Static-analysis-style: assert `"gamma"` NOT in
`OPTUNA_SEARCH_SPACE_KEYS` AND `"utility_family"` NOT in
`OPTUNA_SEARCH_SPACE_KEYS` AND `"wealth_argument"` NOT in
`OPTUNA_SEARCH_SPACE_KEYS`. Tripwire against a future "let me just
search gamma to see if it improves the validation Sharpe" PR.

### T7 — Three Phase-1 facets registered

Read the active `spec_bundles` row at autotune-run time; assert
three rows in `spec_facets` with names `gamma`, `utility_family`,
`wealth_argument`, all `freeze_discipline='THEORY'`. Confirms the
Phase-1 cutover registration step ran.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite.
3. The NN1 disclosure block is present alongside
   `OPTUNA_SEARCH_SPACE_KEYS` in `autotuner.py`.
4. `validate_search_space_nn1()` is invoked at the top of
   `run_autotuner`, BEFORE `optuna.create_study`.
5. `validate_nn1_compliance` is exposed for the Overfitting Conscience
   advisor (Phase 1 computed branch) to consume.
6. `spec_bundles`/`spec_facets` populated with the three Phase-1 facets,
   `frozen_at`-stamped, content-hashed.
7. Commit message: `feat(autotuner): NN1 spec-freeze discipline encoded
   (council §2.5) + validate_search_space_nn1 runtime check + 3 Phase-1
   THEORY facets registered; n_trials=500; objective=CRRA-EU mean(U)`.

## Risk callouts

- **The "let me just A/B gamma" PR.** The single biggest social risk: a
  reasonable developer adds `gamma` to `OPTUNA_SEARCH_SPACE_KEYS` to
  "see if a slightly different gamma improves validation Sharpe." This
  is NN1 violation by definition — `gamma` is a spec facet, not a
  trial parameter. T6 (negative pin) catches the static form; D5
  (`validate_search_space_nn1`) catches the runtime form. **Both** must
  ship so the violation is caught at edit-time AND at runtime.
- **Renaming the violation.** A maintainer might soften
  `"BACKTEST_SELECTION"` to `"OBSERVED_PERFORMANCE"` or some less
  pejorative term. The enum is the canonical name; renaming requires
  re-justifying the council §2.5 disclosure. T2's assertion on the
  literal string protects this.
- **Silent fall-through.** If a `spec_facets` row has an
  unrecognized `freeze_discipline` value (a typo, or a forward-compat
  shape), `validate_nn1_compliance` must FAIL the validation (not
  silently allow). T-default: any value NOT in
  `NN1_HONEST_DISCIPLINES` is treated as a violation, with the message
  naming the unrecognized value. The implementer must wire this
  default-deny path.
- **Phase-2 facet creep.** When Phase 2 unlocks, four new facets enter
  the registry (`generator_family`, `horizon_convention`, `lambda`,
  `regime_bucket_thresh`). NN1 requires all four to be theory-/mandate-
  /calibration-frozen, NOT Optuna-searched. The NN1 disclosure block
  pre-names them so a future PR adding `lambda` to the search space
  fails both the static negative pin and the runtime check.
- **The Overfitting Conscience advisor.** The Phase-1 computed branch
  of the advisor reads `validate_nn1_compliance` output and writes the
  verdict string to `advisor_observations.raw_response`. The advisor's
  LLM authorship is Phase-2 — this plan ships the data the LLM advisor
  will eventually consume, but not the LLM itself.
- **Two-DB cleanliness.** All NN1-related queries are single-DB
  (state DB). The autotuner reads from `spec_bundles` /
  `spec_facets` / `researcher_dof_ledger` using existing single-DB
  accessor patterns. Never cross-join.

## Out of scope

- The `spec_bundles` / `spec_facets` / `researcher_dof_ledger` table
  schemas — owned by persistence-architect's migrations 015 / 020.
- The Overfitting Conscience advisor's prompt / LLM authorship — Phase 2.
- The additive `N_effective = N_optuna + S` accounting consumer — owned
  by the separate accounting plan.
- The Phase-2 facets (`generator_family`, `horizon_convention`,
  `lambda`, `regime_bucket_thresh`) registration — owned by Phase-2
  plans; this plan only pre-names them in the disclosure block.
- The `gamma` value itself — owned by the M1 CRRA-EU objective plan
  (D4 — gamma pre-registration).
- The frozen-eval wall structural accessor — owned by
  persistence-architect's migration 021.
