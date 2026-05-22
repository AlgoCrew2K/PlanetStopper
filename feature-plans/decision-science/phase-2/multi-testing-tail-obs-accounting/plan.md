# Phase 2 — Multi-Testing Accounting: T = Independent Tail-Obs, NEVER Path Count

**Feature:** Encode the binding discipline that any CVaR-derived haircut
statistic uses `T = count of genuine independent tail observations
(~7-8)`, NEVER the simulation path count (`simulation_paths=5000`). A
shared path bank's pseudo-replication would over-credit `√T` significance
by ~27×.

**Phase:** Phase 2 (HONEST RESHAPE — evidence-gated)

**Owner agent-type:** `optuna-specialist` (drives the
autotuner-statistic surface), `quant-test-writer` (adversarial RED on
the path-count-leak shape).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.2 last
  paragraph ("For any CVaR-derived haircut score (Phase 2 only): the
  t-stat's `T` is the count of genuine independent tail observations
  (~7-8), **never the simulation path count** — a shared path bank's
  pseudo-replication would over-credit `√T`").
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.2
  H-2 ("M2's S-3 stderr must use the distinct-tail-observation count,
  not the resample count"; the standard error naively computed on
  5000 resampled draws would understate the true estimation error by
  a factor of roughly `√(5000/7) ≈ 27×`).
- `math_engine.py:705-833` — `run_monte_carlo`: the single-day i.i.d.
  resampler. `math_engine.py:828-829`: the resample line
  `rng.choice(nearest_day_returns, size=simulation_paths)` — where the
  5000-resample inflation surface originates.
- `docs/handoff/council-converged-migration-plan.md` §3.1 migration 023
  (`cvar_diagnostics.cvar_n_tail` — the **persisted auditable
  denominator** for the M2 standard error; the same column shape is
  the source-of-truth for Phase 2's haircut T).

## Why

`run_monte_carlo` draws 5000 samples *with replacement* from a kNN pool
of at most ~150 neighbour-days. At the 5% tail that pool contains only
~7-8 **distinct** sub-5% neighbour-day returns. The 5000 resampled
draws reduce **resampling noise**; they add **zero estimation
information** beyond those ~7-8 distinct tail observations (phase0 §2.2
states this explicitly).

A naive haircut t-stat that uses `T = 5000` would over-credit
significance by `√(5000/7) ≈ 27×`. That converts the FDR gate into a
false-precision generator — a borderline-noise trial set could clear a
gate it should not by a factor of 27. **This is the most dangerous
shape a Phase-2 CVaR haircut can take**: the math LOOKS reasonable
(`sortino * sqrt(T)` shape applied to a CVaR-derived ratio), but `T`
silently carries the wrong number.

NN1 itself is unaffected; this is a separate but compounding
discipline. NN1 keeps the search count honest; this plan keeps the
per-observation count honest.

## Deliverables

### D1 — `compute_cvar_haircut_tstat(...)` function (if and only if Phase 2 introduces a CVaR-valued objective)

**The default Phase-2 plan does NOT introduce a CVaR-valued objective.**
The gamma-2d-search-space plan keeps the deployment objective as
`mean(U)` over the CRRA-transformed series — i.e., the Phase-1 CRRA-EU
objective continues to drive trial selection in Phase 2, with the
CVaR co-signal acting as a *budget-shape* via gamma, not as a separate
objective.

This plan exists to encode the discipline IF a future Phase-2 cycle
introduces a CVaR-valued statistic (e.g. a haircut over CVaR-improvement
across trials). Whichever Phase-2 cycle introduces such a statistic
MUST install:

```python
def compute_cvar_haircut_tstat(
    cvar_per_trial: float,
    n_genuine_tail_obs: int,
) -> float:
    """Per-trial t-stat for a CVaR-derived haircut score.

    T is the count of genuine INDEPENDENT tail observations (~7-8 at
    the 5% level for AlphaBot's kNN pool), NEVER `simulation_paths`
    (5000 — math_engine.py:828-829). A shared path bank's
    pseudo-replication would over-credit √T by ~27× (council §2.2;
    v3-and-divergence-evaluation §A.2).

    `n_genuine_tail_obs` MUST come from cvar_diagnostics.cvar_n_tail
    (migration 023) or the equivalent producer-owned denominator —
    NEVER re-derived from a 5000-array shape.

    Returns 0.0 for T <= 1 (degenerate-trial protection).
    """
    if n_genuine_tail_obs <= 1:
        return 0.0
    return cvar_per_trial * math.sqrt(n_genuine_tail_obs)
```

**This is a stub-with-discipline.** The exact functional shape of
`cvar_per_trial` (a CVaR improvement? a CVaR-implied effect size?) is
the risk-architect's lens — out of scope here. The plan's contract:
**whatever the shape, `T` is the distinct tail-obs count, period.**

### D2 — `n_genuine_tail_obs` must come from a producer-owned source

The denominator `n_genuine_tail_obs`:
- MUST be read from `cvar_diagnostics.cvar_n_tail` (a producer-owned
  column populated by the M2 diagnostic);
- MUST NOT be hard-coded as a magic number ("7" or "8");
- MUST NOT be derived from `simulation_paths` (a 5000-array length);
- MUST NOT be derived from `len(rng.choice(...))`-shape access (the
  5000-resample shape) — even via a derived expression like
  `min(simulation_paths, 8)` that "looks right."

The auditable persisted denominator is the contract. A reviewer reading
the haircut t-stat can trace back to the persisted column and confirm
the count is the producer's distinct-tail-obs assertion.

### D3 — Negative-pin static check

A NEW test asserts the SOURCE of `compute_cvar_haircut_tstat` (and any
similarly-named Phase-2 CVaR haircut helper) does NOT contain the
substring `simulation_paths` AND does NOT contain a numeric magic-number
between 100 and 10000 (the range where a path-count leak would land).
Static-analysis-style tripwire — catches a careless "let me just use
5000 here" PR at edit time.

### D4 — N_genuine_tail_obs propagation through `_haircut_select`

If a Phase-2 cycle wires a CVaR-derived haircut score, the
`_haircut_select` call gains a `tstat_fn` that captures the
n_genuine_tail_obs as part of its closure (or `functools.partial`).
The autotuner reads `cvar_n_tail` from the trial's
`user_attrs["cvar_n_tail"]` and threads it into the t-stat:

```python
def crra_eu_tstat_wrapper(trial, gamma):
    series = trial.user_attrs.get("daily_returns", [])
    return compute_crra_eu_tstat(series, gamma=gamma)

def cvar_haircut_tstat_wrapper(trial):
    cvar = trial.user_attrs.get("cvar_per_trial", 0.0)
    n_tail = trial.user_attrs.get("cvar_n_tail", 0)
    return compute_cvar_haircut_tstat(cvar, n_genuine_tail_obs=n_tail)
```

Composability: a Phase-2 cycle that has **both** a CRRA-EU and a
CVaR-derived statistic per trial would invoke `_haircut_select` twice
— once per statistic — and apply the FDR gate's logical AND. This
composition is OUT of scope for this plan; the plan only ensures that
*if* a CVaR statistic is wired, its T is honest.

### D5 — Disclosure block alongside the function

A NEW comment block alongside `compute_cvar_haircut_tstat`:

```
T-DISCIPLINE (council §2.2 last paragraph; H-2):
  T is the count of genuine INDEPENDENT tail observations (~7-8 at the
  5% level for AlphaBot's kNN pool).
  T is NEVER:
    - simulation_paths (5000) — math_engine.py:828-829
    - len(rng.choice(...)) — the resample-array length
    - any reformulation that scales with the resample count
  A naive T=5000 would over-credit √T by √(5000/7) ≈ 27×, converting
  the FDR gate into a false-precision generator.
  The auditable persisted denominator lives in
  cvar_diagnostics.cvar_n_tail (migration 023).
```

This is **the design-intent record**. Future PR reviewers see the
discipline at the function definition, not buried in a design doc.

### D6 — `cvar_diagnostics.cvar_n_tail` producer-side contract

The persistence layer's M2 diagnostic write (migration 023) populates
`cvar_n_tail` with the **distinct** tail-observation count, NOT the
resample count. This plan does NOT own the M2 producer — that is the
risk-architect's M2 plan. The plan asserts the consumer-side contract:
this autotuner-side haircut reads the column under the producer-side
guarantee.

A NEW contract test:

```
T1c — cvar_n_tail producer contract.
Open a cvar_diagnostics fixture row. Assert cvar_n_tail < 100 (a
distinct-tail-obs count at 5% tail from a ~150-day kNN pool is at most
~7-8; certainly < 100). A row with cvar_n_tail = 5000 indicates a
producer-side bug — fail loud.
```

### D7 — Same-discipline applied to T_optuna for the CRRA-EU haircut (NO CHANGE)

A clarification: the Phase-1 CRRA-EU haircut uses
`T = len(daily_returns)` (validation-fold day count, ~5-25 days). That
is also a count of GENUINE independent observations (one per day),
NOT a resample count. The Phase-1 t-stat's `T` discipline was
ALREADY honest. This plan **does not modify it**; it only ensures
Phase 2's CVaR statistic (if introduced) follows the same discipline.

## Dependencies

- **Blocked by:** Phase 1 — `compute_crra_eu_tstat` plan (the
  per-statistic t-stat pattern this plan extends to a CVaR variant).
- **Blocked by:** persistence-architect's migration 023
  (`cvar_diagnostics.cvar_n_tail`).
- **Blocked by:** Phase 2 unlock — all four §5.1 preconditions.
- **Coupled to:** Phase 2 gamma-2d-search-space plan (gamma searches;
  CVaR-shape consumed via `cvar_n_tail`).
- **Soft-coupled to:** the risk-architect's M2 / Phase-2 path-generator
  plan — the producer of `cvar_n_tail` lives there.

## Golden-fixture tests required

### T1 — Honest T pin

Fixture: `cvar_per_trial = 0.5`, `n_genuine_tail_obs = 7`. Assert
`compute_cvar_haircut_tstat(0.5, 7) == 0.5 * sqrt(7)` to `1e-12`.

### T2 — Path-count negative pin

Fixture: same `cvar_per_trial = 0.5`. Assert
`compute_cvar_haircut_tstat(0.5, 5000)` is NOT what would be returned
by the "honest" call — i.e. assert that the **caller** would not
accidentally pass 5000. (This is more of a negative regression: if a
maintainer passes 5000 by mistake, the function returns the BAD value;
the test asserts the autotuner's call-site does NOT pass 5000.)

The robust shape: assert there is NO code path in `autotuner.py`
that invokes `compute_cvar_haircut_tstat(..., n_genuine_tail_obs=X)`
where X is `simulation_paths` or `len(...) >= 100`. Static analysis on
the call site.

### T3 — Degenerate-trial guard

Fixture: `cvar_per_trial = 0.5, n_genuine_tail_obs = 1`. Assert the
function returns `0.0` (degenerate-trial protection — single tail-obs
cannot establish significance).

Same for `n_genuine_tail_obs = 0`.

### T4 — Static source negative pin (D3)

Read the source of `compute_cvar_haircut_tstat` and any sibling
`*_haircut_tstat` function in autotuner.py; assert the substring
`simulation_paths` does NOT appear. Tripwire against a future
copy-paste that "just uses the array length."

### T5 — Producer contract sanity check

Fixture: a `cvar_diagnostics` row with `cvar_n_tail = 5000` (bogus —
producer-side bug). The CONSUMER side asserts loud — either by a
read-time guard in the autotuner that refuses to compute the haircut
under a suspicious `cvar_n_tail`, or by a startup-validation step
that rejects the run. The exact shape is the implementing team's
choice; the plan asserts the surface needs to exist.

### T6 — Stderr-vs-T-stat parallel

Sanity: the same `cvar_n_tail` denominator that drives the haircut
t-stat MUST drive the M2 standard error (H-2 / S-3 element a). A
fixture asserting both consume the SAME column from `cvar_diagnostics`
catches a maintainer who accidentally hard-codes "8" in one place and
reads the column in the other.

### T7 — 27× false-precision regression scenario

Property-based: for `cvar_per_trial = 0.3`, compare
`compute_cvar_haircut_tstat(0.3, 7)` vs
`compute_cvar_haircut_tstat(0.3, 5000)`. Assert the latter is roughly
`sqrt(5000/7) ≈ 27` times larger. This is the documented harm
quantification — the test PINS the magnitude of the bug being
prevented.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite.
3. `compute_cvar_haircut_tstat` lives in `autotuner.py` (or a
   Phase-2-specific module) with the D5 disclosure block.
4. The static source negative-pin (T4) is wired in CI.
5. The consumer-side guard against bogus `cvar_n_tail` (T5) is in
   place.
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   compute_cvar_haircut_tstat (council §2.2 — T = genuine tail-obs
   count, NEVER simulation_paths); 27× false-precision tripwire;
   n_trials=500; objective=CRRA-EU-with-CVaR-shaping mean(U)`.

## Risk callouts

- **The "let me just use the array length" PR.** The single most
  likely shape: a maintainer writes
  `T = len(rng.choice(neighbour_days, size=simulation_paths))` because
  it "just works" and `T` looks like a count. T4 catches the static
  form; T2 / T7 catch the numerical form. **All three required.**
- **Producer-consumer schema drift.** If `cvar_diagnostics.cvar_n_tail`
  is renamed or retypes, the consumer-side reads silently break — or
  silently swallow a None. T5 catches the consumer-side; the
  fixture-update obligation in the converged-migration-plan §7 catches
  the producer-side.
- **Composition with CRRA-EU.** A Phase-2 cycle that wires both a
  CRRA-EU AND a CVaR-derived haircut statistic per trial creates a
  FDR-composition question (logical AND? a single combined statistic?
  two parallel FDR gates?). This plan does NOT prescribe the
  composition — that is a future Phase-2 design decision. The plan
  ensures whichever composition ships uses honest Ts.
- **Phase-2 may never unlock.** Per the council's §5.1 preconditions
  and §0 BLUF, Phase 2 may never proceed. If it does not, this plan
  stays in the feature-plans tree as the canonical record of the
  T-discipline that would have applied.
- **NN1 interaction.** The honest T-count does NOT replace NN1. Both
  disciplines apply: NN1 keeps the search-count honest (Yekutieli c(N)
  over `N_effective = N_optuna + S`); this plan keeps the
  per-observation count honest (`T = distinct tail-obs`). A
  CVaR-derived haircut with the right NN1 N and the wrong T is still
  broken; ditto the reverse. Both required.
- **Composability with Phase-1 CRRA-EU T.** The CRRA-EU haircut's
  `T = len(daily_returns)` is ALREADY a count of genuine independent
  observations (one per validation-fold day). The Phase-1 discipline
  was already honest. This plan does NOT touch it.

## Out of scope

- The CVaR-derived statistic itself (the `cvar_per_trial` value) —
  owned by the risk-architect's Phase-2 plans.
- The M2 producer of `cvar_n_tail` — owned by the risk-architect's M2
  plan and persistence-architect's migration 023.
- FDR composition between CRRA-EU and CVaR-derived haircuts — a future
  Phase-2 design decision.
- The HAC / Newey-West remediation of W-H5 — that addresses the
  independence assumption of the `√T` denominator, a separate issue
  from the T-source discipline this plan owns.
- The horizon-convention choice — escalated to user per §6.2.
- Phase-1 CRRA-EU haircut — already honest; not in scope.
