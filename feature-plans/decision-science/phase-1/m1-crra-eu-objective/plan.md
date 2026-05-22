# Phase 1 — M1 — CRRA-EU Autotuner Objective (Deployment-Side)

**Feature:** Replace the Sortino + loss-aversion deployment objective in
`autotuner.py` with a theory-grounded CRRA expected-utility objective on the
existing per-day guard-alpha series. Offline; autotuner-only; live exit logic
unchanged.

**Phase:** Phase 1 (HARDEN floor)

**Owner agent-type:** `optuna-specialist` (drives), `risk-engine-specialist`
(W-H2 wealth-argument derivation), `quant-test-writer` (golden fixture for the
new objective). Adversarial RED via `quant-test-writer`; GREEN via the
`optuna-specialist` + `risk-engine-specialist` pair.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.1, §3.1 (M1 row),
  §3.3 (the precise honest claim), §3.5 (BHY preserved, search space stays
  6-D), §3.9 W-H2.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.1 (H-1 —
  CRRA unbounded; named `WEALTH_ARG_FLOOR`), §A.4 (H-4 — M1 = defensibility
  win; M2 = operator instrumentation; both Phase 1, not co-equal framing).
- `docs/handoff/council-converged-migration-plan.md` §3.1 (migration 022 —
  `spec_bundle_id`, `d_spec`, `n_effective`, `ce_metric`, `gamma`,
  `overfitting_verdict`).
- `autotuner.py:81-114` — the five hand-tuned loss-aversion constants
  (`MISSED_UPSIDE_PENALTY_MULT`, `MISSED_UPSIDE_THRESHOLD_PCT`,
  `DRAWDOWN_PENALTY_MULT`, `DRAWDOWN_THRESHOLD_PCT`, `DRAWDOWN_MIN_GAIN_PCT`,
  `NEGATIVE_GUARD_ALPHA_LOSS_AVERSE_MULT`) — the R3 provenance gap M1
  eliminates.
- `autotuner.py:735-802` — `run_simulation`: where the new CRRA-EU objective
  attaches.
- `autotuner.py:645-695` — `_collect_sim_returns`: returns the
  per-triggered-day `guard_alpha` series the new objective transforms.
- `autotuner.py:980-998` — `objective(trial)` closure (validation-fold
  scoring).
- `.claude/CLAUDE.md` no-magic-numbers rule for `math_engine.py`; the same
  discipline applies here.

## Why

The current deployment objective is a Sortino ratio with **five hand-tuned
loss-aversion multipliers** sitting inline in `autotuner.py:81-114`. Per the
council synthesis §3.3, that block is residual **R3** — a documented
provenance gap. M1 replaces it with **one** pre-registered theory-frozen
parameter `gamma`, transforms per-day guard-alpha through a CRRA utility,
and feeds the trial value to BHY-haircut machinery already in place.

This is the council's **one unambiguous win** (§2.1). It is the user's
"defensibility upgrade" deliverable: methodology over heuristic. Offline,
deterministic, bit-identical-replayable, and validatable.

Per H-4: M1 = **the defensibility win**. M2 = **operator instrumentation**.
Both are Phase 1, but they answer different motivations and the plan must
say so.

## Deliverables

### D1 — `compute_crra_utility(W, gamma)` (`math_engine.py`)

A pure function returning the CRRA utility of a wealth argument `W`:

- `gamma != 1`: `u(W) = (W ** (1.0 - gamma)) / (1.0 - gamma)`
- `gamma == 1`: `u(W) = log(W)` (log-utility limit)
- `W` must already be floored at the named module-scope constant
  `WEALTH_ARG_FLOOR > 0` (see D3) before being passed in; the function does
  not silently re-floor. Failing to apply the floor returns `-inf` for
  `gamma >= 1`, which `compute_crra_eu_tstat` must propagate, not swallow
  (the H-1 NaN-poisoning surface).
- Type-hinted; docstring states what + why.
- Single source of truth: the autotuner imports this from `math_engine`,
  never re-derives.

### D2 — `derive_wealth_argument(guard_alpha_series, eod_baseline)` (`math_engine.py`) — W-H2

The wealth argument fed to CRRA is **derived**, not assumed. Guard-alpha is
a *difference* (`triggered_return - eod_return`), not a wealth ratio (council
synthesis §3.9 W-H2; v3-and-divergence-evaluation §A.1 — W-H2 vs W-H4
distinction).

The plan delegates the choice between **growth factor** (`1 + guard_alpha`)
and the **explicit-baseline reconstruction** (`(triggered_total + baseline)
/ (eod_total + baseline)`) to the implementing-team risk-engine-specialist.
Whichever shape ships:

- it is named, single-source-of-truth in `math_engine.py`;
- its derivation is commented at the function with the W-H2 reference;
- its output is a strictly positive float pre-floor — the function does NOT
  floor; the caller does. Separation of concerns: derivation vs stability.

### D3 — `WEALTH_ARG_FLOOR` named constant (`math_engine.py`) — H-1 / W-H4

A named module-scope `WEALTH_ARG_FLOOR: float > 0` with a source comment
stating: *"Lower floor on the wealth argument `W` fed to CRRA. CRRA is
unbounded below as `W → 0+` for `gamma >= 1`; an unfloored `W` produces a
non-finite `u(W)` that NaN-poisons `mean(U)`, `sd(U)`, and the BHY haircut
running-min (`autotuner.py:349-354`). The floor goes on the **input `W`**,
NEVER on the output `U` — flooring `U` compresses the lower tail of `U`,
artificially shrinks `sd(U)`, and inflates the t-stat
`mean(U)/(sd(U)/√T)`, re-introducing an anti-conservative bias the haircut
cannot correct."*

Floor value pre-registered (e.g. `0.5` — half the wealth gone; a 50% intra-day
loss is the worst case any rational guard-alpha series should produce). The
exact value is the team's call **subject to** the constraint that the
near-floor sub-case in §8 test 1 produces a finite `t` and a finite `sd(U)`.

### D4 — `gamma` pre-registration

`gamma` is **NOT in the Optuna search space** in Phase 1 (council synthesis
§3.5 — "search space stays 6-D, gamma frozen, not added"). It is a frozen
theory-chosen scalar persisted as a `spec_facets` row with
`freeze_discipline = 'THEORY'` and `evidence_source = 'THEORY'` (migration
015 / 020).

`gamma` pre-registration:
- value picked by team's risk-engine-specialist (recommended range: `[1.0,
  5.0]` — `gamma=1` is log-utility, `gamma>1` is risk-averse CRRA).
- written to a single source of truth — preferably the `spec_facets` row;
  the autotuner reads it on each run via the `spec_bundles` accessor, never
  hard-coded inline.
- the alternative — a named constant `CRRA_GAMMA` in `autotuner.py` — does
  NOT satisfy the persistence-architect's "immutable + content-hashed +
  `frozen_at`" constraint (council synthesis §3.7 last paragraph). A
  source-code named constant fails on all three. The plan therefore requires
  **`gamma` to live in `spec_bundles`/`spec_facets` from Phase-1 day 1**;
  the autotuner reads it through that surface.

### D5 — Objective swap in `run_simulation` / `_collect_sim_returns`

`run_simulation` (`autotuner.py:735`) is the deployment objective.
`_collect_sim_returns` (`autotuner.py:645`) returns the per-day guard-alpha
series. The swap:

- `_collect_sim_returns` continues to return `daily_returns` — the raw
  guard-alpha series. **No change to this function's signature or return
  type.**
- A new `run_simulation_crra_eu(p, history_data, acc_sym_ids,
  current_date_str, deviation_dict, *, gamma)` returns `mean(U)` over the
  CRRA-transformed series. **NOT** the CE in return units —
  `mean(U)` is the Optuna value because BHY runs on `mean(U)`; CE
  (`u⁻¹(mean(U))`) is a monotone transform with identical trial rankings
  (synthesis §2.1) and is computed separately for the audit display only.
- The five loss-aversion constants at `autotuner.py:81-114` are **deleted**
  in the same commit as the swap — not left as dead code; per project
  CLAUDE.md "no backwards-compatibility hacks — if something is unused,
  delete it." Existing tests against those constants update or move.
- `run_simulation` is **renamed** to `run_simulation_sortino_legacy` if it
  must be retained for a transition window; default plan is **delete**.

### D6 — `objective(trial)` closure rewire (`autotuner.py:980-998`)

`objective(trial)` continues to suggest 6 params (no `gamma`). The body
calls the new `run_simulation_crra_eu(..., gamma=current_gamma)` where
`current_gamma` is read from the active `spec_bundle` (D4). The
`trial.set_user_attr("daily_returns", daily_returns)` call **still records
the raw guard-alpha series** — not `U` — because:

- the haircut's per-trial t-stat (D7-domain — see the
  `compute_crra_eu_tstat` plan) re-transforms `daily_returns` through
  `derive_wealth_argument` and `compute_crra_utility` in one place;
- storing `U` would mean a future gamma re-pre-registration would render
  the persisted user-attr inconsistent with the active gamma — a silent
  drift surface.

### D7 — Persistence write-back

Per migration 022: `autotune_runs` row writes `spec_bundle_id`, `gamma`,
`ce_metric` (CE in return units — `u⁻¹(mean(U))` of the WINNING trial only),
`d_spec`, `n_effective`, `overfitting_verdict`. Phase 1: `d_spec = 1`,
`n_effective = n_optuna` (NN1-honest case). The dual-write contract (H1 — see
the converged migration plan) is the implementing team's obligation.

## Dependencies

- **Blocks:** Phase 1 — `compute_crra_eu_tstat` plan (the haircut t-stat
  depends on `compute_crra_utility` + `derive_wealth_argument` +
  `WEALTH_ARG_FLOOR` being in `math_engine.py`).
- **Blocks:** Phase 1 — BHY haircut preservation plan (the per-trial
  haircut statistic switches to `compute_crra_eu_tstat`).
- **Blocked by:** persistence-architect's migration 015 (`spec_bundles` +
  `spec_facets`) + migration 022 (`autotune_runs` EUT columns), because
  D4/D7 read/write through them.
- **Soft-coupled to:** the engine-audit plan for trial-floor justification —
  M1 does not change `n_trials = 500`, but a future trial-floor change
  would invalidate the assumed `T_optuna ≈ 500` Yekutieli c(N) calibration.

## Golden-fixture tests required

(RED-first; the test-writer authors with hostility — looking for paths a
worse implementation could also pass.)

### T1 — CRRA utility correctness

Pure: `compute_crra_utility(W, gamma)` for a fixed set of `(W, gamma)`
pairs returns within `1e-12` of the analytic value. Includes `gamma=1`
(log limit). Includes the **near-floor** case `W = WEALTH_ARG_FLOOR + ε`
and asserts `u(W)` is finite.

### T2 — Wealth argument derivation (W-H2)

Pure: `derive_wealth_argument(...)` over a fixed guard-alpha + EOD-baseline
fixture returns the documented mapping. Asserts the output is strictly
positive **before** flooring. (Whichever derivation shape ships — growth
factor vs explicit-baseline — has its own deterministic mapping; this test
pins it.)

### T3 — End-to-end objective on a frozen guard-alpha series

Fixture: a frozen daily guard-alpha series (~25 days, mimicking the
validation fold). Assert `run_simulation_crra_eu(...)` returns `mean(U)`
matching a hand-computed reference. The reference is computed in the test
using the same `compute_crra_utility` (the test re-derives, never reads
from the SUT) — but uses an independent NumPy-mean path so a typo in the
SUT's reduction is caught.

### T4 — H-1 NaN-poisoning surface

Fixture: a daily series containing one near-floor wealth-argument day.
Assert:
- `run_simulation_crra_eu(...)` returns a **finite** value;
- the floor was applied to `W` (asserted by spying on the
  `compute_crra_utility` call argument), **not** to `U`;
- a parallel implementation that floors `U` directly produces a measurably
  larger (less safe) trial value — pins the H-1 fix in the intended
  direction.

### T5 — `gamma` provenance

Assert that `objective(trial)` reads `gamma` from `spec_bundles` /
`spec_facets`, NOT from a module-level constant. Implementation: monkeypatch
the `spec_bundles` accessor and assert the trial value tracks the patched
gamma. A regression here would catch a future "let me just hard-code gamma
in autotuner.py" drift.

### T6 — Five-constant deletion regression

Static-analysis-style: assert `MISSED_UPSIDE_PENALTY_MULT`,
`MISSED_UPSIDE_THRESHOLD_PCT`, `DRAWDOWN_PENALTY_MULT`,
`DRAWDOWN_THRESHOLD_PCT`, `DRAWDOWN_MIN_GAIN_PCT`, and
`NEGATIVE_GUARD_ALPHA_LOSS_AVERSE_MULT` no longer exist in
`autotuner.py`. Tripwire: a future re-introduction is caught loudly.

## Definition of Done

1. All six tests T1-T6 RED on a clean implementer commit, GREEN after
   implementation.
2. `pytest tests/autotuner/ tests/engine/ tests/execution/` PASS (the
   project-memory rule: math_engine additions break tests mocking
   `math_engine` wholesale; run all three suites before GREEN).
3. The five loss-aversion constants are deleted from `autotuner.py`.
4. `spec_bundles` + `spec_facets` populated with the `gamma` facet
   (`freeze_discipline='THEORY'`, `evidence_source='THEORY'`,
   `frozen_at`-stamped, content-hashed).
5. `autotune_runs` row writes `spec_bundle_id`, `gamma`, `ce_metric`,
   `d_spec=1`, `n_effective=n_optuna`, `overfitting_verdict`.
6. A walk-forward replay produces a **bit-identical decision record** to
   the reference Guard-Alpha sequence (Gate 1 — backtest-replay parity).
   M1 is offline; this assertion is byte-identical-strict.
7. Commit message: `feat(autotuner): study_name=<TS>__<symphony>, search
   space delta vs prior run = -5 loss-aversion constants, +1
   spec_bundles.gamma facet; n_trials=500; objective=CRRA-EU mean(U)`
   (per the autotuner-charter format).

## Risk callouts

- **NaN poisoning (H-1).** If `WEALTH_ARG_FLOOR` is mis-sized OR the
  derived wealth argument can structurally approach 0 (W-H2), `u(W) →
  -inf` and the entire haircut silently breaks. T4 catches this; the team
  must also verify by inspection that the chosen `derive_wealth_argument`
  shape cannot produce a non-positive output pre-floor (a non-positive
  output is a derivation defect, not a floor problem).
- **`gamma` drift.** A subtle drift surface: a developer adds a "default
  gamma" to autotuner.py to "let it run without a populated spec_bundle."
  T5 catches this. The implementing team must NOT add such a default —
  fail loud if no spec_bundle is present.
- **Five-constant deletion.** Any test that *mocks* the autotuner objective
  by patching those constants will break. The implementing team must sweep
  the test tree for references to all six constant names before GREEN.
- **Two-DB cleanliness.** `spec_bundles` is in the state DB; the autotuner
  reads it via the existing single-DB accessor pattern
  (`get_symphony_strategy` precedent). NEVER cross-join from
  `optuna_studies.db`. The `gamma` value is **copied** into the
  `autotune_runs` row, not joined.
- **Replay parity (Gate 1).** A naive change in floating-point reduction
  order (e.g. switching from a Python `sum()` to `numpy.sum()`) can
  produce a non-bit-identical replay. T3 pins the reduction shape; the
  implementing team must use the same reduction primitive in production
  and in the test reference.
- **`compute_sortino_tstat` retention.** It is **NOT** deleted in this
  cycle — it remains the per-trial statistic for any retained
  Sortino-objective study (e.g. the calibration sweeps). See the BHY
  preservation plan for the call-site discipline.

## Out of scope

- The CRRA t-stat function itself — owned by the `compute_crra_eu_tstat`
  plan in this same Phase-1 folder.
- BHY haircut wiring changes beyond a single call-site swap — owned by the
  BHY haircut preservation plan.
- `N_effective = N_optuna + S` accounting — owned by the additive
  N_effective plan. In Phase 1 with NN1 honest, `S = 0`, so M1 ships with
  `n_effective = n_optuna`; the accounting plan installs the consumer.
- M2 (CVaR diagnostic) — owned by the risk-architect lens, not the
  autotuner lens.
- M3 (Phase 1.5 — time-squeeze + VWAP re-derivation) — owned by Phase 1.5
  plans.
- `gamma` Optuna-searching — Phase 2 ONLY; see Phase 2 — gamma integration
  plan.
- Refactoring `run_simulation`'s per-day loop structure — preserve it
  verbatim; only the per-day terminal reduction changes.
- HAC / Newey-West serial-correlation correction — W-H5, explicitly
  out-of-scope per v3-and-divergence-evaluation §A.6.
