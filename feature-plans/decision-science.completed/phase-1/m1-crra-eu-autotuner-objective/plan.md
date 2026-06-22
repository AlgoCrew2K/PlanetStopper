# Feature: M1 — CRRA-EU Autotuner Objective (canonical integration plan)

**Supersedes (collapsed per README §4 option ii):**
- `phase-1/m1-crra-eu-objective/plan.md` — CRRA-EU deployment-side objective (SUPERSEDED — see §"Deliverables — CRRA-EU objective slice" below)
- `phase-1/m1-crra-eu-tstat/plan.md` — re-derived per-trial t-stat `compute_crra_eu_tstat` (SUPERSEDED — see §"Deliverables — S-2 t-stat slice" below)
- `phase-1/m1-bhy-haircut-preservation/plan.md` — BHY haircut preservation under CRRA (SUPERSEDED — see §"Deliverables — BHY haircut preservation slice" below)

**Phase / Lane:** Phase 1 — HARDEN-core floor (the defensibility win — replaces R3, the five hand-tuned loss-aversion multipliers).
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED) + `optuna-specialist` (autotuner-side wiring) + `quant-code-reviewer` (review). Standing Quad team for math-layer work.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.1 (CRRA-EU objective settled), §3.1 (M1 floor row), §3.3 (Phase-1 honest claim — the **four-part** statement: 3 facets + 1 statistical component + D_spec=1-conditional-on-sensitivity), §3.5 (autotuner integration: gamma frozen + NOT Optuna-searched, search space stays 6-D), §3.9 W-H2 (wealth argument derivation), §4 binding condition **S-2** (re-derived t-stat).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.1 hole **H-1** (CRRA unbounded below; "bounded" is false; new residual **W-H4**; named **`WEALTH_ARG_FLOOR > 0`** on the input `W`, never on the output `U`), §A.6 **H-6** (S-2 inherits serial-correlation anti-conservatism — new residual **W-H5**, disclose-and-accept, named remediation **explicitly out-of-scope** for Phase 1), §A.7 **H-7** (§8 test 1 **PINS** the formula, does not VALIDATE it — verb distinction).
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 022 (`022_autotune_runs_eut.sql` columns: `gamma`, `lambda_budget`, `ce_metric`, `spec_bundle_id`, `d_spec`, `n_effective`, `cvar_feasible`, `overfitting_verdict`, `paired_heuristic_study_name`) + §6 hazard **H1** (dual-write 022 columns to BOTH the `ALTER` and `init_db()`'s `CREATE TABLE autotune_runs`).
- `docs/handoff/council-attack-rubric.md` Family **A** (A-1 estimator, A-2 ★ NaN-propagation closure, A-4 wealth-argument consistency), Family **D** ★ (D-1 golden fixture, D-2 non-circular fixture provenance), Family **H** ★ (H-1 spec-freeze, H-2 BHY preserved, H-3 frozen-eval untouched, H-4 honest effective parameter count).
- Code anchors: `math_engine.py:30-54` (`_reject_non_finite` policy); `autotuner.py:94-114` (the five loss-aversion multipliers being replaced); `autotuner.py:266-271` (the **H-6 category-error precedent** comment — the code already rejected `effect_size·√T` for a non-ratio metric once); `autotuner.py:289-301` (`compute_sortino_tstat` — the function being replaced for this objective only); `autotuner.py:304-316` (`compute_haircut_pvalue` — preserved); `autotuner.py:319-356` (`benjamini_hochberg_adjust` with Yekutieli c(N) — preserved); `autotuner.py:706-728` (the BHY pipeline call site — wires `compute_*_tstat` into `compute_haircut_pvalue` into `benjamini_hochberg_adjust`); `autotuner.py:129,147` (`PURGE_DAYS=20`, `EMBARGO_DAYS=1` — preserved).

## Why (problem statement)

The current deployment autotuner objective is a Sortino ratio combined with FIVE hand-tuned loss-aversion multipliers (`autotuner.py:94-114`). These multipliers are the project's residual **R3** (council synthesis §3.3): a load-bearing piece of math with no defensible provenance. They are the single largest hole in the autotuner's defensibility story.

The Decision-Science Council converged on a **theory-grounded CRRA expected-utility objective** as the replacement, frozen by theory rather than backtest P&L. This is "the one unambiguous win" of the entire council process (council synthesis §2.1) and the **defensibility deliverable** the user's binding motivation asked for (§3.7 framing). M1 ships it.

Three concrete defects must be fixed in the same TDD cycle:

1. **The per-trial significance statistic must match the new objective.** Reusing `compute_sortino_tstat` (returns `sortino*√T`) for a CRRA-mean objective would be the **exact H-6 category error** the code already fixed once (comment at `autotuner.py:266-271`). A mean needs `mean/(sd/√T)`, not `effect_size·√T`. This is binding condition **S-2**.
2. **CRRA utility is unbounded below — the haircut can be NaN-poisoned.** v3 §2.1 incorrectly described the CRRA-transformed series as "bounded." CRRA `u(W) = W^(1-γ)/(1-γ)` diverges to `-∞` as `W → 0+` for `γ ≥ 1`. A near-total-loss fold day sends `u → -∞`, which makes `mean(U)` and `sd(U)` non-finite, which the `erf` clamp at `autotuner.py:314` cannot rescue. The NaN propagates through `benjamini_hochberg_adjust`'s running-min and silently breaks the whole haircut. This is the exact NaN/Inf propagation the risk-engine charter forbids (`math_engine.py:30-54`). The fix is a named module-scope constant **`WEALTH_ARG_FLOOR > 0`** applied to the **input wealth argument `W`, never to the output utility `U`** (flooring `U` compresses the lower tail, shrinks `sd(U)`, and *inflates* the t-stat — re-introducing exactly the anti-conservative bias the haircut exists to prevent).
3. **The wealth argument fed to CRRA is unverified (W-H2).** Guard-alpha is a *difference*, not a wealth ratio; feeding it into CRRA as a wealth proxy violates A-4 (wealth-argument consistency). The correct argument must be **derived, not assumed** — a Phase-1 design item with its own golden fixture. W-H4's floor sits on top of whatever W-H2 derives.

## Deliverables

### Code

- **`math_engine.py`** — new pure functions, each with `_reject_non_finite` entry validation matching `math_engine.py:30-54`:
  - `compute_crra_utility(W: float, gamma: float) -> float` — the CRRA `u(W) = W^(1-γ)/(1-γ)` transform. Applies `WEALTH_ARG_FLOOR` to `W` first (never to the output). For `γ == 1` returns `ln(W_floored)` (log utility, the limit).
  - `derive_wealth_argument(...)` — W-H2's derivation; the exact signature is the Phase-1 design item this plan binds. The function consumes the same per-day guard-alpha series the Sortino objective consumes and returns a **wealth ratio** (a multiplicative growth factor, not a difference). The derivation must be A-4-consistent: the same wealth argument is used everywhere CRRA is evaluated.
  - `compute_crra_eu_objective(daily_returns: list[float], gamma: float) -> float` — returns `mean(u(W_i))` over the fold. Used by the autotuner as the trial objective (NOT a CE transform — Optuna trial ranking is identical under a monotone CE transform, but the haircut runs on `mean(U)`; CE is a *display* convention only). The function is small, pure, deterministic, and dependence-injectable for tests.
- **Module-scope constants in `math_engine.py`** (project rule: no magic numbers):
  - `WEALTH_ARG_FLOOR: float` — value pre-registered as part of W-H2; source comment cites the council synthesis §3.9 W-H2 + the evaluation §A.1 H-1 mathematical justification. Strictly positive.
  - `CRRA_LOG_UTILITY_GAMMA_TOL: float` — narrow tolerance around `γ == 1` triggering the log-utility branch (avoids `1/(1-γ)` blow-up).
- **`autotuner.py`** — wire the new objective into `run_simulation` (~`autotuner.py:94-114`), guarded by a `spec_bundle.objective_kind in {"sortino_loss_aversion", "crra_eu"}` discriminator. The five hand-tuned loss-aversion multipliers stay in the Sortino branch (preserved for legacy paired-heuristic studies — migration 022 column `paired_heuristic_study_name`); the new branch consumes `compute_crra_eu_objective`. The search space stays **6-D** (gamma frozen, NOT added).
  - New per-trial significance statistic: `compute_crra_eu_tstat(U_series: list[float]) -> float` — implements **`t = mean(U)/(sd(U)/√T)`** (S-2). Uses `_z`-style zero-std guard (cf. `math_engine.py:783-786`). Replaces `compute_sortino_tstat` *for the CRRA branch only*; `compute_sortino_tstat` is unchanged for the Sortino branch. The BHY pipeline `autotuner.py:706-728` switches statistic function based on `objective_kind`.
  - BHY step-up + `compute_haircut_pvalue` + `benjamini_hochberg_adjust` + Yekutieli c(N) + `PURGE_DAYS=20` + `EMBARGO_DAYS=1` + 60/20/20 split + `HARVEY_LIU_FDR_Q=0.05` — **100% PRESERVED**. Only the per-trial statistic changes.
- **`database.py`** + **`migrations/022_autotune_runs_eut.sql`** — new additive ALTER columns on `autotune_runs`: `gamma`, `lambda_budget`, `ce_metric`, `spec_bundle_id`, `d_spec`, `n_effective`, `cvar_feasible`, `overfitting_verdict`, `paired_heuristic_study_name`. All NULLable + DEFAULT NULL. **Hazard H1 (binding):** the nine columns MUST be dual-written to BOTH the `022` ALTER statements AND the `init_db()` `CREATE TABLE autotune_runs` (a fresh DB never runs migrations; an upgraded DB never re-runs `CREATE TABLE`). Omitting either path leaves one population with missing columns. The `cvar_feasible` and `paired_heuristic_study_name` columns are reserved for downstream cycles (M2 and the Phase-2 cutover); M1 writes them NULL.

### Tests

The five RED golden-fixture tests for HARDEN-core are specified in council synthesis §8. M1 owns **test 1, 2, and 4**:

- **Test 1 (S-2 + H-7 wiring pin):** known `U`-series with `sd(U) ≠ 1`; assert `compute_crra_eu_tstat(U) == mean(U)/(sd(U)/√T)` AND `compute_crra_eu_tstat(U) != mean(U)*√T` (would-be Sortino form) AND `compute_crra_eu_tstat(U) != effect_size·√T`. Language **per H-7**: "PINS the formula (wiring)" — does NOT claim to validate methodology. Sub-case (W-H4): a `U`-series produced from a near-`WEALTH_ARG_FLOOR` wealth argument; assert the returned t-stat is **finite**, `sd(U)` is finite, and the floor was applied to `W` (intermediate-value assertion via dependency injection of `compute_crra_utility`).
- **Test 2 (W-H2):** once `derive_wealth_argument` is implemented, a golden fixture asserts the function returns a wealth ratio (multiplicative > 0), uses the same argument at every CRRA evaluation site (A-4 consistency), and the value is **derived** from inputs — never a constant pinned to today's behavior. Provenance: schema-derived with a runtime validator; NOT hand-authored alongside the function under test (D-2).
- **Test 4 (replay-determinism anchor):** same `cycle_id` run twice through the M1 autotuner branch yields **bit-identical** trial objective values, bit-identical t-stats, and a bit-identical winner. Depends on `derive_cycle_mc_seed` discipline (`math_engine.py:695-702`) being preserved at the MC layer (M1 itself is not stochastic — but the run uses the seeded MC; the test verifies the seam, F-2 ★ gate).

Additional regression tests in this cycle:

- **NaN-propagation closure (A-2 ★):** explicit pytest asserting `compute_crra_utility(W=NaN, γ=2.0)` raises `ValueError` at entry; `compute_crra_utility(W=-Inf, γ=2.0)` raises; `compute_crra_eu_objective` rejects any non-finite in the input series.
- **`compute_sortino_tstat` regression:** the Sortino branch is untouched — a regression fixture asserts the legacy paired-heuristic study path still produces byte-identical results against a committed reference (preserves the "paired heuristic study" comparison value).
- **Migration 022 dual-write verification (H1):** a database fixture test opens a freshly-created DB AND a DB with the `022` migration applied; asserts both have the nine new `autotune_runs` columns and both insert successfully (E-1 ★).
- **Documentation fixture (H-6 / W-H5):** a known `U`-series with injected lag-1 autocorrelation; the test asserts plain-`√T` is used and **documents** the result is known anti-conservative (W-H5). Per H-6 disposition this is disclose-and-accept; the test makes the residual visible rather than asserted.

### Documentation

- `math_engine.py` module-level docstring for each new function: what + why (citing the council synthesis sections).
- `autotuner.py:289-301` neighbours: a new comment block stating WHICH `compute_*_tstat` is selected by WHICH `objective_kind`, plus an inline note pointing at `autotuner.py:266-271`'s H-6 precedent (a future reader must SEE that the precedent governs THIS choice).
- The new `WEALTH_ARG_FLOOR` named constant carries the project-rule source comment plus a one-line evaluation-§A.1 H-1 mathematical justification.

---

## Deliverables — CRRA-EU objective slice (folded from `m1-crra-eu-objective/plan.md`)

This section captures the additive deliverable detail from the deployment-side objective slice. The canonical plan above covers the high-level requirements; this section adds binding implementation constraints that the above does not specify.

### Objective slice — function signatures and caller discipline

- **`compute_crra_utility(W: float, gamma: float) -> float`** — the CRRA transform with the following binding constraints from the slice:
  - `W` must be floored at `WEALTH_ARG_FLOOR` **by the caller** before being passed in; the function does NOT silently re-floor. Failing to apply the floor returns `-inf` for `gamma >= 1`, which `compute_crra_eu_tstat` must propagate, not swallow (the H-1 NaN-poisoning surface). This separation-of-concerns applies everywhere the function is called.
  - `gamma != 1`: `u(W) = (W ** (1.0 - gamma)) / (1.0 - gamma)`.
  - `gamma == 1`: `u(W) = log(W)` (log-utility limit).

- **`derive_wealth_argument(guard_alpha_series, eod_baseline)` → `float`** — W-H2 derivation details:
  - Choice between **growth factor** (`1 + guard_alpha`) and **explicit-baseline reconstruction** (`(triggered_total + baseline) / (eod_total + baseline)`) is **delegated to the implementing `risk-engine-specialist`**.
  - Whichever shape ships: it is a single-source-of-truth function in `math_engine.py`; its derivation is commented with the W-H2 reference; its output is a strictly positive float **before** flooring — the function does NOT floor, the caller does (derivation vs stability are separate concerns).

- **`WEALTH_ARG_FLOOR: float`** named constant — binding source comment text: *"Lower floor on the wealth argument `W` fed to CRRA. CRRA is unbounded below as `W → 0+` for `gamma >= 1`; an unfloored `W` produces a non-finite `u(W)` that NaN-poisons `mean(U)`, `sd(U)`, and the BHY haircut running-min (`autotuner.py:349-354`). The floor goes on the **input `W`**, NEVER on the output `U` — flooring `U` compresses the lower tail of `U`, artificially shrinks `sd(U)`, and inflates the t-stat `mean(U)/(sd(U)/√T)`, re-introducing an anti-conservative bias the haircut cannot correct."* Floor value pre-registered (recommended: `0.5` — a 50% intra-day loss is the worst case any rational guard-alpha series should produce). Exact value is the team's call subject to the near-floor sub-case in §8 test 1 producing a finite `t` and a finite `sd(U)`.

### Objective slice — `gamma` pre-registration (binding constraint)

`gamma` is **NOT in the Optuna search space** in Phase 1 (council synthesis §3.5 — "search space stays 6-D, gamma frozen, not added"). It is a frozen theory-chosen scalar persisted as a `spec_facets` row with `freeze_discipline = 'THEORY'` and `evidence_source = 'THEORY'` (migrations 015 / 020).

**Binding constraint:** a named constant `CRRA_GAMMA` in `autotuner.py` does NOT satisfy the persistence-architect's "immutable + content-hashed + `frozen_at`" constraint (council synthesis §3.7 last paragraph). A source-code named constant fails on all three counts. `gamma` MUST live in `spec_bundles`/`spec_facets` from Phase-1 day 1; the autotuner reads it through that surface.

Test `T-gamma-provenance` (from the slice): assert that `objective(trial)` reads `gamma` from `spec_bundles`/`spec_facets`, NOT from a module-level constant. Implementation: monkeypatch the `spec_bundles` accessor and assert the trial value tracks the patched gamma. Catches a future "let me just hard-code gamma in autotuner.py" drift.

### Objective slice — `run_simulation_crra_eu` and `objective(trial)` wiring

- **Function name:** `run_simulation_crra_eu(p, history_data, acc_sym_ids, current_date_str, deviation_dict, *, gamma)` returns `mean(U)` over the CRRA-transformed series, where each `U_i = compute_crra_utility(derive_wealth_argument(g_i, ...), gamma)`. The trial objective value is `mean(U)`, NOT the CE in return units — CE (`u⁻¹(mean(U))`) is a monotone transform with identical trial rankings (synthesis §2.1) and is computed separately for the audit display only (`ce_metric` column).
- **Five loss-aversion constants DELETED:** `MISSED_UPSIDE_PENALTY_MULT`, `MISSED_UPSIDE_THRESHOLD_PCT`, `DRAWDOWN_PENALTY_MULT`, `DRAWDOWN_THRESHOLD_PCT`, `DRAWDOWN_MIN_GAIN_PCT`, `NEGATIVE_GUARD_ALPHA_LOSS_AVERSE_MULT` are **deleted in the same commit** as the objective swap — not left as dead code, per project CLAUDE.md "no backwards-compatibility hacks — if something is unused, delete it." The implementing team must sweep the test tree for references to all six constant names before GREEN. If the legacy Sortino branch must be retained for a transition window, `run_simulation` is renamed to `run_simulation_sortino_legacy`; default plan is delete.
- **`objective(trial)` stores raw guard-alpha, NOT `U`:** `trial.set_user_attr("daily_returns", daily_returns)` records the raw guard-alpha series. Reason: storing `U` would mean a future gamma re-pre-registration would render the persisted user-attr inconsistent with the active gamma — a silent drift surface. The haircut's per-trial t-stat re-transforms `daily_returns` through `derive_wealth_argument` and `compute_crra_utility` in one place.

### Objective slice — additional tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| T3 — End-to-end objective on a frozen guard-alpha series | Fixture: a frozen ~25-day daily guard-alpha series. Assert `run_simulation_crra_eu(...)` returns `mean(U)` matching a hand-computed reference (test re-derives via independent NumPy-mean path, so a typo in the SUT's reduction is caught). |
| T5 — `gamma` provenance | Monkeypatch the `spec_bundles` accessor; assert `objective(trial)` tracks the patched gamma. Catches hard-coded gamma drift. |
| T6 — Five-constant deletion regression | Static-analysis assertion: all six loss-aversion constant names no longer exist in `autotuner.py`. Tripwire against re-introduction. |

---

## Deliverables — S-2 t-stat slice (folded from `m1-crra-eu-tstat/plan.md`)

This section captures the additive implementation detail from the t-stat slice. The canonical plan specifies `compute_crra_eu_tstat` at a high level; this section adds binding precision.

### T-stat slice — function signature and implementation constraints

**`compute_crra_eu_tstat(daily_returns: list[float], gamma: float) -> float`** — binding implementation constraints from the slice:

- **Signature:** takes the raw guard-alpha series + gamma (NOT a pre-computed U-series). Internally calls `derive_wealth_argument` and `compute_crra_utility` from `math_engine` — single source of truth. Applies `WEALTH_ARG_FLOOR` to each wealth argument **before** the CRRA transform. NEVER floors `U` (H-1).
- **`statistics.stdev` (sample, n-1):** the one-sample t-stat denominator is the **sample** standard deviation. Using `pstdev` (population) or `numpy.std()` without `ddof=1` would silently inflate `t` by `sqrt(n/(n-1))` and shift the haircut calibration. Test T3 pins this distinction.
- **Degenerate-series guard:** if `sd(U) == 0.0` (constant series), returns `0.0`, not `float('inf')`. The haircut ranks the trial last via `argmin p_adj` naturally; degenerate-trial detection is the haircut's job, not the t-stat's.
- **Returns `0.0` for `T <= 1`** (fewer than 2 observations — sd is undefined).
- Pure: no side effects, no logging, no DB writes.

### T-stat slice — call-site and companion changes

- **`_haircut_select` parametrization:** `_haircut_select` receives an explicit `tstat_fn: Callable[[Trial], float]` parameter (default = `compute_sortino_tstat` for backward-compatibility with retained Sortino sweeps). The CRRA-EU path's `run_autotuner` caller passes `tstat_fn=compute_crra_eu_tstat` AND threads the active `gamma` through (closure or `functools.partial`). The rest of `_haircut_select` — `compute_haircut_pvalue`, `benjamini_hochberg_adjust`, `argmin` selection, the `HARVEY_LIU_FDR_Q` gate, the `None`-return branch — is byte-identical to today's.
- **`compute_sortino_tstat` retention + warning:** `compute_sortino_tstat` is NOT deleted. Its docstring gains: *"WARNING: appropriate ONLY for the Sortino objective (a ratio). For a mean-valued objective (e.g. CRRA-EU), use compute_crra_eu_tstat; reusing this function for a mean is the H-6 category error (autotuner.py:266-271)."*
- **Inline comment update at `autotuner.py:266-271`:** the existing H-6 comment is extended: *"The H-6 category error was a Sharpe-derived deflation applied to a Sortino. Since 2026, the same category-discipline applies between compute_sortino_tstat (Sortino objective) and compute_crra_eu_tstat (CRRA-EU objective) — a mean-valued functional needs the one-sample t-stat, not effect_size·√T."*
- **`compute_crra_eu_tstat` location:** placed in `autotuner.py` as a sibling of `compute_sortino_tstat` (not in `math_engine.py`).

### T-stat slice — additional tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| T3 — Sample vs population stdev pin | Fixture: `T=5` series with known sample and population stdev differing by `sqrt(5/4) ≈ 1.118`. Assert `compute_crra_eu_tstat` matches the **sample-stdev** computation, not the population one. Catches a future `pstdev` swap. |
| T4 — Degenerate-series guard | Fixture: constant `U`-series (`sd(U)==0`). Assert returns `0.0`, not `inf`, not NaN. |
| T5 — Per-objective routing in `_haircut_select` | Small synthetic Optuna trial set. Run `_haircut_select` with each `tstat_fn`. Assert the two routings produce **different** winner rankings AND that swapping is the explicit caller's choice (not auto-detected). Catches a "let me just always use the CRRA t-stat" silent drift. |
| T6 — H-6 negative-pin regression | Static-analysis: assert `compute_crra_eu_tstat` source does NOT contain `effect_size * sqrt(T)` or `value * sqrt(T)`. Tripwire against a "simplification" back to the H-6 shape. |

---

## Deliverables — BHY haircut preservation slice (folded from `m1-bhy-haircut-preservation/plan.md`)

This section captures the additive implementation detail from the BHY haircut preservation slice. The canonical plan specifies "100% preserved, single call-site swap" at a high level; this section adds the binding line-level and semantic constraints.

### BHY slice — zero-change preservation requirements (binding)

The following code is **byte-identical unchanged** after M1 ships. The line references are binding:

- `autotuner.py:262-356` — the entire haircut block (including `compute_haircut_pvalue` and `benjamini_hochberg_adjust`)
- `autotuner.py:272-286` — `HARVEY_LIU_FDR_Q = 0.05` and `_HAIRCUT_PVALUE_EPSILON = 1e-12`
- `autotuner.py:345` — the Yekutieli `c(N) = sum(1.0/j for j in range(1, n+1))` line

No signature change, no defaulting change, no reordering, no "minor cleanup" on any of these. Test T1's `diff-empty` DoD step verifies the textual form; T1's numerical pin catches the numerical form.

### BHY slice — Sortino-sentinel filter preservation (binding)

`autotuner.py:1041-1043` filters out trials whose value equals `math_engine._SORTINO_SENTINEL` (the 1e6 zero-downside sentinel). This filter stays — it is a no-op under CRRA-EU (mean-valued objective has no zero-downside-divide-by-zero hazard) but harmless, and is load-bearing for any retained Sortino-objective sweep. The implementing team does NOT delete the filter in M1's cycle. No new "CRRA degenerate-series sentinel" is introduced: the `sd(U)==0` branch's `0.0` return (see S-2 t-stat slice) ranks the trial last via `argmin p_adj` naturally.

### BHY slice — `selection_tstat` column semantics

The `autotune_runs` row's `selection_tstat` column (the winner's t-statistic) continues to be the winner's significance scalar. Under the CRRA objective that is `compute_crra_eu_tstat(winner.daily_returns, gamma)`, NOT `compute_sortino_tstat(study.best_value, T)` (which would be the H-6 category error: using a Sortino-shape statistic on a CRRA-objective value). Test T4 catches this at the `_haircut_select` level; the column's semantic stays "higher-is-better significance scalar."

### BHY slice — additional tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| T1 — `benjamini_hochberg_adjust` byte-identical pin | Fixture: frozen N=10 p-value vector with hand-derived expected adjusted p-values. Assert exact match to `1e-15`. Tripwire against any future "cleanup" of the step-up direction or running-min order. |
| T2 — Yekutieli c(N) closed-form pin | Assert `c(N) == harmonic_number(N)` for `N in [1, 5, 10, 100, 500]` to `1e-15`. Catches log-approximation drift (`c(N) ≈ ln(N) + γ`). For `N=500`: `ln(500) ≈ 6.21` vs `c(500) ≈ 6.79` — a ~10% under-correction. |
| T3 — Clamp boundary pin | Assert `compute_haircut_pvalue(10.0)` returns exactly `_HAIRCUT_PVALUE_EPSILON` (Φ saturates beyond ~8.3) and `compute_haircut_pvalue(-10.0)` returns `1 - _HAIRCUT_PVALUE_EPSILON`. Catches a future loosening of the clamp. |
| T4 — End-to-end haircut under both objectives | Fixture: ~20 fake trials each carrying `daily_returns` user-attr. Two runs: (1) `_haircut_select(trials, tstat_fn=compute_sortino_tstat_wrapper)`, (2) `_haircut_select(trials, tstat_fn=compute_crra_eu_tstat_wrapper(gamma=2.0))`. Assert both runs reach the same `benjamini_hochberg_adjust` call count; the `HARVEY_LIU_FDR_Q` gate consulted exactly once per run; the winner's `selection_tstat` matches the `tstat_fn(winner)` output (not re-derived from `trial.value` post-hoc). |
| T5 — Sortino-sentinel filter retention | Fixture: trial list including a trial with `value == math_engine._SORTINO_SENTINEL`. Assert it is filtered out under BOTH `tstat_fn` choices. Tripwire against "this filter is dead code, delete it." |
| T6 — Replay parity (Gate 1) — haircut bit-identical | Run `run_autotuner` on a deterministic seed under CRRA-EU. Assert `selection_tstat`, `p_adj`, and `winner_params` in the `autotune_runs` row match a committed frozen reference, bit-identical. Catches numerical-reduction-order changes. |

---

## Dependencies

- **Blocks:** every subsequent Phase-1 plan that consumes `autotune_runs.gamma` / `n_effective` / `spec_bundle_id` (notably M2's `cvar_diagnostics` is sibling, not dependent; the AI Advisor's Overfitting Conscience consumes the EUT columns, but Phase-1 ledger work is upstream of Advisor work).
- **Blocked by:** **migration 015** (`spec_bundles` + `spec_facets`) — `autotune_runs.spec_bundle_id` (column from 022) is a soft FK to `spec_bundles.id`. The spec-registry migration must land in the same release. If `spec_bundles` is collapsed to a `facets_json` column per H-8 A2 team's-choice, the soft-FK semantics are unchanged.
- **Blocked by:** **migration 020** (`researcher_dof_ledger`) — the `N_effective = N_optuna + S` accounting consumer reads `evidence_source='BACKTEST_SELECTION'` rows; the ledger must exist from Phase 1. Honest-case `S = 0` → `N_effective = N_optuna` → BHY is byte-identical to today's.
- **Soft dependency:** the W-H2 derivation. M1 cannot ship until `derive_wealth_argument` exists and its golden fixture is committed; the function may be implemented in this same cycle (it's pure math, small, testable in isolation).

## Golden-fixture tests required (RED before GREEN)

Mapping to council synthesis §8:

| # | RED-test | What must exist before GREEN |
|---|---|---|
| 1 | CRRA t-stat formula pin (S-2 + H-7) | A known-`U`-series fixture; an assertion the new statistic ≡ `mean/(sd/√T)`; an assertion it ≠ `effect_size·√T`; a near-floor-wealth sub-case asserting finite t-stat + W-floored (W-H4). |
| 2 | W-H2 wealth-argument derivation | A schema-derived fixture defining the input series and the expected wealth ratio; an A-4 consistency assertion (same argument at hold and exit branches once Phase 2 ships — Phase 1 only has the autotuner branch). |
| 4 | One-anchor replay-determinism (M1 slice) | Two runs of the same `cycle_id` through the M1 branch; bit-identical trial objective values, t-stats, and winner. Excludes `id`/`ts_utc` per H-8 A3. |
| — | NaN-propagation closure (A-2 ★) | `compute_crra_utility` / `compute_crra_eu_objective` reject NaN / ±Inf at entry. |
| — | Sortino-branch regression | Legacy paired-heuristic study produces byte-identical result against a committed reference (`compute_sortino_tstat` unchanged). |
| — | Migration 022 dual-write (E-1 ★) | Fresh DB + upgraded DB both have the nine new columns; both can insert. |
| — | Documentation fixture for W-H5 | Injected-lag-1-AR `U`-series; assertion plain-`√T` is used and is **known** anti-conservative (the test exists to make W-H5 visible). |

**Fixture provenance (D-2 ★, council-attack-rubric):** every fixture is captured-from-producer OR schema-derived with a runtime validator. NO hand-authored expected value derived from the same code under test.

## Definition of Done

- All RED tests above land first (RED before GREEN, project Agent-Teams discipline).
- GREEN: every RED test passes; `pytest tests/autotuner/ tests/engine/ tests/execution/` all pass (per `feedback_run_execution_engine_suites_for_alpha_bot_execution_changes` — math_engine additions break tests mocking `math_engine` wholesale; run all three suites before GREEN handoff). Full-tree pytest quoted with HEAD SHA + count + zero errors per `feedback_full_suite_means_genuine_full_tree`.
- `WEALTH_ARG_FLOOR` and `CRRA_LOG_UTILITY_GAMMA_TOL` are named module-scope constants with source-comment justifications (no-magic-numbers project rule).
- `compute_crra_eu_tstat` is wired into `autotuner.py:706-728` conditionally on `spec_bundle.objective_kind == "crra_eu"`; `compute_sortino_tstat` is unchanged for the legacy branch.
- Migration 022 columns appear in BOTH `_MIGRATION_FILES` (via `022_autotune_runs_eut.sql`) AND `init_db()`'s `CREATE TABLE autotune_runs` (H1).
- `spec_bundle_id` is wired into the autotuner so the deployed gamma is recorded as a frozen facet with a `frozen_at` timestamp and a content hash (the persistence-architect binding constraint — a source-code named constant is NOT acceptable as the spec record; the table is). If the team collapses `spec_facets` to a JSON column on `spec_bundles`, the binding constraint still holds.
- Word "**bounded**" is **struck** everywhere it appears in v3 — replaced with "bounded by construction below by `WEALTH_ARG_FLOOR` applied to `W`" or the equivalent. The synthesis edit is in scope of the documentation sweep, not this code cycle.
- The Phase-1 honest claim (synthesis §3.3) is restated in the autotuner's docstring exactly: **3 facets + 1 new validated statistical component + D_spec=1 conditional on the gamma sensitivity check** — never "adds 1 facet."
- **From objective slice:** the six loss-aversion constants are deleted from `autotuner.py`; `spec_bundles` + `spec_facets` populated with the `gamma` facet (`freeze_discipline='THEORY'`, `evidence_source='THEORY'`, `frozen_at`-stamped, content-hashed); `autotune_runs` row writes `spec_bundle_id`, `gamma`, `ce_metric`, `d_spec=1`, `n_effective=n_optuna`, `overfitting_verdict`; a walk-forward replay produces a bit-identical decision record (Gate 1).
- **From t-stat slice:** `compute_crra_eu_tstat` lives in `autotuner.py` as a sibling of `compute_sortino_tstat`; `_haircut_select` signature gains exactly one new parameter (`tstat_fn`), no other field changes; inline H-6 comment at `autotuner.py:266-271` updated.
- **From BHY haircut slice:** `autotuner.py:262-356` and `:272-286` are **diff-empty** in the M1 commit (verified by diff inspection in the PR review); `_haircut_select`'s `tstat_fn` default is `compute_sortino_tstat` (backward-compatible).

## Risk callouts / hazards

- **H-1 (CRRA unbounded below).** Without `WEALTH_ARG_FLOOR > 0` on `W`, a near-zero wealth argument poisons the BHY haircut with non-finite values and breaks Gate 1 replay parity. Binding correctness defect — not a residual.
- **W-H4 (numerical-stability residual).** Distinct from W-H2: a correctly-derived wealth ratio can still legitimately approach 0. Live `WEALTH_ARG_FLOOR` is the answer. Floor `W`, **never** `U`.
- **H-6 / W-H5 (serial-correlation anti-conservatism).** Inherited from `compute_sortino_tstat`; M1 is not a regression. Disclose-and-accept; remediation (HAC / Newey-West / `T_eff`) is explicitly **out-of-scope for Phase 1**. Documentation fixture makes the residual visible.
- **H-7 (PINS vs VALIDATES).** Test 1's verb is **PINS**, not VALIDATES — a unit test cannot discharge a methodology claim. The downstream user-facing acceptance language must use "PINS."
- **NN1 spec-freeze (★ load-bearing, council synthesis §2.5).** `gamma` is frozen by **theory / mandate / pre-registration** — never by P&L. Frozen by P&L would make the BHY haircut a lie by omission. Validation: a runtime check in the autotuner asserts `spec_facets.evidence_source` for `facet_name='gamma'` is NOT `BACKTEST_SELECTION`.
- **H1 (migration 022 dual-write).** Failure mode is silent on a fresh DB: a missing column → an INSERT fails the cycle. Test surface verifies BOTH paths.
- **MC sentinel discipline.** M1 does not change `run_monte_carlo`'s signature; `MC_INSUFFICIENT_HISTORY_SENTINEL = None` is preserved. The 7+ consumers of `run_monte_carlo` (memory `project_mc_sentinel_consumer_blast_radius`) are NOT in this cycle's blast radius.
- **Replay-determinism anchor.** M1's only new randomness source is the seeded MC (already deterministic via `derive_cycle_mc_seed`); the CRRA branch itself is deterministic. Replay parity test 4 verifies this.
- **Two-DB boundary (E-2 ★).** All migration 022 columns are in the **state DB**. No optimization-DB migration. `autotuner.py` already reads `get_symphony_strategy` from the state DB — placement keeps every haircut read single-DB.

## Out of scope

- M2 CVaR diagnostic computation — sibling Phase-1 plan (`phase-1/m2-cvar-diagnostic/plan.md`).
- M3 re-derivation of time-squeeze + VWAP System-A curves — Phase 1.5 (`phase-1.5/m3-redrive-provenance-gaps/plan.md`).
- HAC / Newey-West / `T_eff` correction for serial correlation in the t-stat — disclosed in H-6 disposition; **deferred indefinitely** (closing it for CRRA without also closing it for `compute_sortino_tstat` would be incoherent; at the ~5-day frozen-fold scale, lag-1 `ρ` is itself unestimable, so a `T_eff` correction is not even constructible there).
- AI Advisor LLM authorship — `advisor_observations` table writes in Phase 1 are the **computed** Overfitting-Conscience verdict (one row per autotune run) per the migration plan; LLM-authored Advisor commentary is Phase 2.
- The Regime & Decision Narrator role (escalation #3, council synthesis §6.3) — structurally inapplicable to Phase 1 (HARDEN has no drift to narrate).
- Touching `run_monte_carlo`. Frozen through both phases (H7 migration plan).
- A new Optuna parameter for `gamma`. The search space stays 6-D; `gamma` is pre-registered, NOT searched.
