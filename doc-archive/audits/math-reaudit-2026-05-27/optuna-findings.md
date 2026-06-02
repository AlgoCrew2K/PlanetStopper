<!-- ARCHIVED from audit/math-engine-reaudit @ e246d08, original date 2026-05-27. Conclusion: OPTUNA-1/6/2/7 open findings fed walk-forward overhaul planning; OPTUNA-4 (OOS fold collapse) addressed by DE-WF-001/002 (DECISIONS.md); OPTUNA-8/5/9 verified CORRECT. See memory/project_walk_forward_overhaul_complete.md. -->
# Optuna Walk-Forward Methodology — Re-Audit Findings

**Auditor:** math-optuna (Optuna methodology auditor)
**Branch:** audit/math-engine-reaudit (forked from plan/finalist-a-scaffold @ 8d38a43)
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/audit-worktrees/math-reaudit
**Date:** 2026-05-27
**Scope:** Optuna walk-forward optimization correctness in `autotuner.py` + `synthetic_history.py`. Read-only.

---

## Severity counts

| Severity | Count |
|---|---|
| BLOCKER | 0 |
| HIGH | 2 |
| MEDIUM | 4 |
| LOW | 3 |

One-line summary: BHY haircut wiring is sound and S-2 / NN1 / N_effective binding decisions are correctly implemented; sampler + pruner + seed remain Optuna defaults (implicit) and the parallel `n_jobs=-1` run is non-deterministic — these are documented engine-audit lanes, not closed gates.

---

## 1. Sampler choice — `run_autotuner` uses Optuna's IMPLICIT default

**Severity:** HIGH
**Status:** Open — methodology lane scaffolded but not landed
**Location:** `autotuner.py:1500-1507` (`optuna.create_study(... )` — no `sampler=` kwarg)

### Verification

```python
study = optuna.create_study(study_name=f"{study_timestamp}__{normalized_name}",
                            storage=storage, load_if_exists=False, direction="maximize")
study.optimize(objective, n_trials=500, n_jobs=-1)
```

No `sampler=` kwarg — Optuna defaults to `TPESampler(seed=None)`. The methodology-change implication is load-bearing because the BHY haircut's Yekutieli c(N) factor (autotuner.py:393-430) is calibrated to TPE's *non-independent* trial dependency structure. The inline justification at autotuner.py:404-407 quotes this verbatim:

> "the Optuna trial statistics are NOT independent (the TPE sampler concentrates the search), so plain Benjamini-Hochberg 1995 — which assumes independence / PRDS — would under-correct the false-discovery rate by a factor of c(N)."

### Finding

The TPE sampler is the load-bearing dependency-structure assumption of the c(N) correction. Today the choice is **implicit** (Optuna default). The engine-audit lane `engine-audit/sampler-choice/plan.md` proposes:

- Explicit `sampler=TPESampler(seed=...)` at the call site
- Named constant `ACTIVE_OPTUNA_SAMPLER_FAMILY = "TPE"`
- Static back-reference test (T3) so a future PR swap is visible

That plan is **scaffolded but not landed** (scaffold-only at branch tip 4cf7be3). A future PR could silently substitute `CmaEsSampler` and the c(N) factor would be miscalibrated relative to the new dependency structure with no test failure.

### Sub-finding 1a — `run_calibration_sweep` IS explicit

`autotuner.py:1877-1883` in `run_calibration_sweep` constructs `optuna.samplers.TPESampler(seed=random_state)` explicitly. The two call sites are inconsistent: the V1 sweep pins the sampler, the main autotuner does not. The main path is the one whose output deploys.

### Recommendation (options + trade-offs only — PM decides)

- Land `engine-audit/sampler-choice/plan.md` to make the choice explicit, or
- Accept the implicit-default risk and document the c(N) dependency assumption at the `create_study` site with no code change.

---

## 2. Pruner choice — IMPLICIT default `MedianPruner`; silently inactive

**Severity:** MEDIUM
**Status:** Open — methodology lane scaffolded but not landed
**Location:** `autotuner.py:1506` (no `pruner=` kwarg)

### Verification

- `objective(trial)` (autotuner.py:1467-1495) returns ONCE at end-of-trial (`compute_crra_eu_objective` or `compute_sortino_ratio`). Grep confirms ZERO `trial.report(` / `should_prune(` calls in `autotuner.py`.
- Optuna's default since v2.x is `MedianPruner`. Without intermediate `trial.report(...)` calls the pruner has no data to inspect — it is **silently inactive**, not actively pruning.

### Finding

Behaviour is correct today (no pruning) because the objective is end-of-trial-scored. The risk is **forward-looking**: if a future PR adds `trial.report(intermediate, step=...)` inside `objective` or `_collect_sim_returns`, the default `MedianPruner` would silently activate, censoring the BHY haircut's trial set — TPE-pruned-trials ≠ TPE-completed-trials, breaking c(N) calibration. `_haircut_select` filters `t.value is not None` (autotuner.py:1535), which masks the censoring as "no trials" rather than as a methodology violation.

The engine-audit lane `engine-audit/pruner-choice/plan.md` proposes explicit `pruner=NopPruner()` + a static guard test for `trial.report` / `should_prune` substrings. Scaffolded but not landed.

### Recommendation

Land `engine-audit/pruner-choice/plan.md` together with sampler-choice (same code-region edit). Trade-off: zero behaviour change today; protective tripwire against a future regression.

---

## 3. Study persistence + uniqueness — CORRECT

**Severity:** LOW (verify-only)
**Status:** Passes
**Location:** `autotuner.py:1500-1506`, `autotuner.py:207-209`, `migrations/optuna_001_archive_accumulated_studies.sql`

### Verification

- Storage URL: `sqlite:///optuna_studies.db` with `RDBStorage(... connect_args={"timeout": 60})` — separate DB from `alphabot_state.db` (architecture constraint 3 — two-DB pattern preserved).
- Study name: `f"{study_timestamp}__{normalized_name}"` where `study_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")` — microsecond-resolution UTC ISO format. The `build_symphony_study_name` helper (line 207) is the canonical form and matches the project gotcha "Walk-forward study names — Use `<timestamp>__<symphony>`; never reuse a study name."
- `load_if_exists=False` — never reuses an existing name (would raise `DuplicatedStudyError`).
- Legacy archival migration `optuna_001` renames bare legacy names with `LEGACY__` prefix (UPDATE only, no DELETE, idempotent via `INSTR(study_name, '__') = 0` guard).

### Sub-finding 3a — Microsecond timestamp + parallel symphonies

`run_timestamp` (autotuner.py:1458) is generated ONCE per run-invocation (used as the row key for autotune_runs grouping). `study_timestamp` is regenerated inside the per-symphony loop (autotuner.py:1505) — fresh for each symphony. Collision is theoretically possible if two symphonies enter the loop within the same microsecond, but the `for normalized_name in symphony_names:` loop is sequential, not parallel, so this cannot happen in practice. [Confidence: High]

### Sub-finding 3b — `_apply_optuna_archive_migration_if_needed` runs once per `run_autotuner` invocation

autotuner.py:1363 — idempotent; SQL guard prevents `LEGACY__LEGACY__` accumulation. Non-fatal exception handler at autotuner.py:1116-1117 prints a warning rather than aborting the run. Operator-loud failure mode acceptable for an archival migration.

---

## 4. Walk-forward folds — purge + embargo CORRECT; OOS fold-collapse acknowledged

**Severity:** MEDIUM (acknowledged limitation, not defect)
**Status:** Methodology sound; statistical power constraint disclosed
**Location:** `autotuner.py:166-205`, `autotuner.py:1392-1446`

### Verification

- Three-fold split 60/20/20 (TRAIN_RATIO=0.60, VALIDATION_RATIO=0.20, FROZEN_EVAL_RATIO=0.20), asserted to sum to 1.0 at module-load (autotuner.py:203).
- `PURGE_DAYS = 20` derived from `max(LOOKBACK_DAYS=20, ATR_LOOKBACK_DAYS=15)` — feature-lookback overlap purge. Inline citation: López de Prado 2018 Ch. 7 (Purged k-fold CV).
- `EMBARGO_DAYS = 1` — guard-alpha is a same-day difference (no multi-day lookback), so the only residual is the indirect vol-clustering channel which the 20-day purge already absorbs. Embargo sized to LdP Ch. 7.4 ~1%-of-observations guidance.
- Purge + embargo applied at **BOTH** fold boundaries:
  - boundary 1 (train|val): `effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)` at autotuner.py:1418
  - boundary 2 (val|frozen): `val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS` at autotuner.py:1425
- Two validation sets: `history_validation` (purge-reduced, used by Optuna objective only — autotuner.py:1480) and `history_validation_full` (full raw, used by OOS cascade — autotuner.py:1622-1636).
- Frozen-eval is **consumed exactly once** post-selection (autotuner.py:1656 — single `_collect_sim_returns` call; no separate `run_simulation` re-read).

### Finding 4a — OOS-fold-collapse v2 (disclosed)

At 125-day history the three raw folds are 75/25/25. After PURGE_DAYS=20 at each boundary, the usable validation and frozen-eval windows shrink to ~4-5 days each. autotuner.py:1255-1261 acknowledges this:

> "After PURGE_DAYS=20 at each boundary, the usable validation and frozen-eval windows each shrink to approximately 4-5 usable days. This is an acknowledged tradeoff — the purge is methodologically correct and the short evaluation window is the cost of honest OOS reporting."

**Methodology assessment:** the purge is correct; the small post-purge window is the visible cost of methodological honesty at a 125-day history. The validation Sortino / CRRA-EU is therefore a high-variance estimator. The BHY haircut and `n_effective` partially compensate — but the effective T per trial entering `compute_crra_eu_tstat` is the *triggered-day count over that ~4-5 day window*, which can be very small.

**Risk surface:** for symphonies that rarely trigger, `T` in `compute_crra_eu_tstat = mean(U) / (sd(U) / sqrt(T))` may be 0-2, making the t-stat dominated by noise. The H-6 / W-H5 residual at autotuner.py:333-335 names this as a known disclosed-and-accepted limitation. [Confidence: High — disclosed in code + plan §6.1 OOS-fold-collapse v2]

### Finding 4b — `_collect_sim_returns` is the single integration point

The validation fold scoring (autotuner.py:1480), the OOS cascade (autotuner.py:1622-1636 indirectly via `run_simulation`), and the frozen-eval read (autotuner.py:1656) all go through `_collect_sim_returns` (autotuner.py:765-815). One copy of the per-day simulation logic; `_replay_exit_tick` (autotuner.py:557-713) is the shared per-tick exit core called from all three replay paths AND `replay_exit_sequence`. No-leak guard at AC-6 test. [Confidence: High]

### Recommendation

The 125-day history floor is the binding constraint on statistical power. The engine-audit lane `engine-audit/walk-forward-fold-structure/plan.md` is the proper home for "extend history window or use purged k-fold CV" — out of scope for this audit. Option enumeration belongs there.

---

## 5. BHY (Benjamini-Hochberg-Yekutieli) haircut — correctly implemented, S-2 binding decision wired

**Severity:** LOW (verify-only)
**Status:** Passes
**Location:** `autotuner.py:243-493` (haircut block), `database.py:1478+` (`get_researcher_dof_ledger_for_run`)

### Verification — Yekutieli c(N) factor

```python
c_n = sum(1.0 / j for j in range(1, n + 1))
```
(autotuner.py:419) — exact N-th harmonic number, no log-approximation. Step-up walks ranks `n..1` and maintains `running_min` to enforce monotone non-decreasing adjusted p-values. The `min(max(..., 0.0), 1.0)` clamp closes the [0, 1] requirement.

### Verification — N_effective = N_optuna + S additive accounting (S-2 binding decision)

`compute_n_effective` at autotuner.py:443-493 implements:
- `N_effective = n_optuna + sum(n_configs_searched)` over researcher_dof_ledger rows where:
  - `evidence_source == 'BACKTEST_SELECTION'` (filtered upstream by `get_researcher_dof_ledger_for_run`)
  - `touched_frozen_eval` is falsy (excludes OOS-peek alarm path; handled separately)
  - `spec_bundle_id != winning_spec_bundle_id` (winner already counted in n_optuna sweep)

The formula matches synthesis §2.2 verbatim:

> N_effective = N_optuna + S (additive). Yekutieli c(N) preserved.

### Verification — NN1-honest steady-state byte-identical to pre-wiring

When every facet is THEORY/MANDATE/CALIBRATION-frozen (no BACKTEST_SELECTION rows), the ledger query returns []. `s = 0`, `N_effective = N_optuna`, c(N_effective) = c(N_optuna), padded p-value tail is empty. Byte-identical to the pre-wiring haircut behaviour (plan D2 backward-compatibility contract honored).

### Verification — Shape A p-value padding (plan D3)

`_haircut_select` at autotuner.py:898-907 pads the p-value list with `S = effective_n - n_trials` copies of `1.0` ("tested and rejected at no significance") so the BHY adjustment computes c(N) over the honest N_effective. Winner selection is over `p_adj_all[:n_trials]` — never the padded tail. BHY preservation contract honored — `benjamini_hochberg_adjust` itself unchanged.

### Verification — TYPE-002 string vs int guard

`compute_n_effective` at autotuner.py:476-482 asserts `winning_spec_bundle_id` is `str` or `None`, NEVER an integer PK. The production path at autotuner.py:1560-1567 passes `stored_hash` (the bundle_hash TEXT), not the integer `id`. Hardened against the type-confusion the audit fix CRRA-001 / NEFF-001 / ARCH-001 (commit 836e0ed) closed.

### Verification — Sentinel exclusion preserved

`_haircut_select` filters `math_engine._SORTINO_SENTINEL` (1e6 zero-downside trials) BEFORE the haircut at autotuner.py:864-866. Comment notes it is a no-op on the CRRA-EU path but retained for both branches.

### Verification — p-value clamp

`compute_haircut_pvalue` at autotuner.py:378-390 clamps into `[_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON]` with `_HAIRCUT_PVALUE_EPSILON = 1e-12`. Source comment names IEEE-754 saturation at |t| > ~8.3 — methodology-sound.

### Verification — D_spec audit column

D_spec at autotuner.py:1568-1574 is COUNT DISTINCT spec_bundle_ids over the ledger query (council §5; differs from S which is SUM(n_configs_searched)). Persisted into `autotune_runs.d_spec` via `save_autotune_run`. Wired correctly per ARCH-001.

[Confidence: High — every binding item from synthesis §2.2 / §3.5 / §4 traced to code]

---

## 6. Parallelism + reproducibility — `n_jobs=-1` HARDCODED, `seed=None` implicit

**Severity:** HIGH
**Status:** Open — methodology lane scaffolded but not landed
**Location:** `autotuner.py:1507` (`study.optimize(objective, n_trials=500, n_jobs=-1)`)

### Verification

- `n_jobs=-1` is hardcoded at the call site. Project rule (engine-audit/parallelism-reproducibility/plan.md §rule 5): "read `n_jobs` from `.env`; never hardcode CPU counts." **VIOLATED today.**
- TPESampler `seed` is unspecified at the main autotuner call site (sampler is Optuna's default — sampler-choice finding above), so seed = `None`. Non-deterministic by construction.
- `run_calibration_sweep` IS seeded (`TPESampler(seed=random_state)` at autotuner.py:1877) AND runs single-threaded (`n_jobs=1` at autotuner.py:1884). Two-site inconsistency.

### Finding

Under `n_jobs > 1`, even with a fixed seed, joblib task-scheduling non-determinism makes repeated runs **not bit-identical** in best_params. The engine-audit plan acknowledges this and reframes the deterministic-replay contract as "Gate-1 replay parity is single-threaded; the autotuner's BEST_PARAMS selection under n_jobs > 1 is a sampling-noise question handled by the BHY haircut at scale."

Today the implicit `seed=None` makes the determinism question moot — repeated runs are non-deterministic regardless of n_jobs. The hardcoded `n_jobs=-1` violates project rule 5. The `engine-audit/parallelism-reproducibility/plan.md` proposes `_resolve_n_jobs()` + `_resolve_optuna_seed()` env-driven helpers. Scaffolded but not landed.

### Risk surface

- Two operators running the same EOD autotuner cycle on the same history can land on different `best_params` (BHY haircut + OOS cascade catch the worst cases, but variability persists).
- The CRRA-EU objective + c(N)-corrected BHY haircut is the correctness safeguard — but the trial-level non-determinism is invisible at PR-review time.
- The RDBStorage `timeout=60` (autotuner.py:1503) mitigates SQLite write contention under `n_jobs=-1` but is not a determinism control.

### Recommendation

Land `engine-audit/parallelism-reproducibility/plan.md`. Trade-off: minor code change; documented determinism contract; closes the project-rule-5 violation. Behaviour change is zero unless the operator sets `AUTOTUNER_N_JOBS` / `AUTOTUNER_SEED` in `.env`.

---

## 7. Trial floor — 500 hardcoded literal; project floor 100

**Severity:** MEDIUM
**Status:** Open — methodology lane scaffolded but not landed
**Location:** `autotuner.py:1507` (`n_trials=500` literal)

### Verification

- Hardcoded literal `n_trials=500` at the main call site.
- Project rule (CLAUDE.md gotcha): "Default Optuna trial floor — 100 trials (statistical stability)."
- Project rule 3 (autotuner charter, quoted in engine-audit/trial-floor-justification/plan.md): "Never reduce trial count below 100 without explicit user direction (statistical stability floor)."
- 500 is well above the 100 floor — methodologically conservative. c(500) ≈ 6.79; c(100) ≈ 5.19.
- `run_calibration_sweep` uses 100 trials explicitly (autotuner.py:1884) — at the floor, not above.

### Finding

Today's value (500) is correct per the project rule. The literal is **unnamed** — a future PR could lower it below 100 with no test failure. The engine-audit lane `engine-audit/trial-floor-justification/plan.md` proposes a named `N_TRIALS = 500` constant + a `N_TRIALS >= 100` invariant test. Scaffolded but not landed.

### Recommendation

Land `engine-audit/trial-floor-justification/plan.md` together with sampler/pruner/parallelism — these are all "name the implicit constant + tripwire" plans in the same code region. Trade-off: zero behaviour change; cheap protective tripwire.

---

## 8. CRRA-EU objective wiring — S3-AUDIT-001 fix landed; CORRECT

**Severity:** LOW (verify-only)
**Status:** Passes
**Location:** `autotuner.py:1337-1359` (objective-kind discriminator), `autotuner.py:1467-1495` (objective closure), `autotuner.py:1546-1577` (tstat routing)

### Verification — gamma sourced from spec_facets

`autotuner.py:1343-1351` reads `gamma` from `database.get_spec_facets_for_bundle(stored_hash)` — sourced from the registered bundle, NOT a module-level constant. T5 gamma provenance contract honored. Default 2.0 (prudential CRRA coefficient) if facet absent.

### Verification — objective routing

`_objective_kind` at autotuner.py:1357-1359 resolves to `'crra_eu'` when `utility_family == 'CRRA'` or `objective_kind == 'crra_eu'`; otherwise `'sortino_loss_aversion'` (legacy).

Inside `objective(trial)`:
- `daily_returns` (RAW percent) persisted via `trial.set_user_attr` at autotuner.py:1485 — NOT the U-series. Future gamma re-pre-registration cannot silently stale stored attrs (T5 binding).
- CRRA-EU branch (autotuner.py:1489-1492): `compute_crra_eu_objective(daily_returns_fraction, _gamma)` after percent→fraction conversion at `RETURN_PCT_TO_FRACTION = 100.0`. Unit-conversion factor sourced + commented (W-H2 boundary).
- Sortino branch (autotuner.py:1493-1495): `compute_sortino_ratio(daily_returns)` unchanged. Annotation acknowledges annualization intentionally omitted (ranking signal, not annualized statistic).

### Verification — t-stat routing (S-2 binding decision)

`_haircut_select` (autotuner.py:818-916) routes via `tstat_fn`:
- Sortino branch: `compute_sortino_tstat(t.value, len(series))` = `sortino * sqrt(T)`
- CRRA-EU branch: re-transforms raw percent returns → fraction → floored W → U via `compute_crra_utility`, then `compute_crra_eu_tstat(u_series) = mean(U) / (sd(U) / sqrt(T))`. Uses sample stdev (ddof=1, Bessel-corrected) per docstring at autotuner.py:347-354.

The H-6 category error (Sortino `effect_size·sqrt(T)` form for a mean-valued objective) is explicitly named at autotuner.py:333-335 and structurally prevented by the discriminator. Audit-fix CRRA-001 (commit 836e0ed) closed the U-transform omission in `_haircut_select`.

### Verification — Sortino suppression for CRRA-EU

autotuner.py:1646-1660: `validation_sharpe_value` and `frozen_eval_sharpe_value` are set to `None` for CRRA-EU bundles (Sortino is not applicable to the CRRA-EU objective). Avoids misleading dual-metric reporting.

### Verification — W-H4 wealth-argument floor

`derive_floored_wealth_argument` at autotuner.py:305-318: `W_i = max(WEALTH_ARG_FLOOR, 1 + r_i_fraction)`. The floor is on the INPUT W, NEVER on the output U (per H-1 hazard callout). `WEALTH_ARG_FLOOR` imported from `math_engine` — single source of truth.

[Confidence: High — every S-2 / W-H2 / W-H4 / H-1 binding traceable to landed code]

### Sub-finding 8a — _objective_kind resolution from facet defaults

autotuner.py:1355-1359 derives `_objective_kind` from `utility_family` when `objective_kind` is absent. The Phase-1 THEORY bundle (`get_or_create_phase1_theory_bundle_id`) sets `utility_family='CRRA'` per project CLAUDE.md, so the production path activates the CRRA-EU branch.

---

## 9. Spec-bundle freeze at autotuner entry — NN1 enforced; CORRECT

**Severity:** LOW (verify-only)
**Status:** Passes
**Location:** `autotuner.py:1120-1140` (`validate_search_space_nn1`), `autotuner.py:1143-1234` (`validate_nn1_compliance`), `autotuner.py:45-89` (NN1 discipline constants)

### Verification — NN1_HONEST_DISCIPLINES set

```python
NN1_HONEST_DISCIPLINES = frozenset({
    "THEORY", "MANDATE", "STYLIZED_FACT",
    "POLITIS_WHITE", "CADENCE", "CALIBRATION",
})
```
Default-deny: any `freeze_discipline` NOT in this set is treated as a violation, including `BACKTEST_SELECTION` (NN1 VIOLATION) and unknown forward-compat values. Matches synthesis §2.5 + §3.7 verbatim.

### Verification — Search-space NN1 hard gate

`validate_search_space_nn1` (autotuner.py:1120-1140) runs at autotuner.py:1266 — BEFORE `optuna.create_study` — and refuses to start if `OPTUNA_SEARCH_SPACE_KEYS` contains any of:
`{gamma, utility_family, wealth_argument, generator_family, horizon_convention, lambda, regime_bucket_thresh}`.

The current `OPTUNA_SEARCH_SPACE_KEYS` (autotuner.py:45-49) is:
`{TAKE_PROFIT_MC_PCT, VWAP_CROSS_HWM_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS, PARABOLIC_VELOCITY_THRESHOLD, MAX_PARABOLIC_SQUEEZE}` — no leak.

The NN1 disclosure block at autotuner.py:51-65 lists every theory/mandate/calibration-frozen facet and labels each one's source (THEORY / MANDATE / STYLIZED_FACT / etc.). Adding a new name without classifying it is named as a Gate-1 review fail.

### Verification — Bundle compliance hard gate

`validate_nn1_compliance` (autotuner.py:1143-1234) runs at autotuner.py:1307:
- (a) every `spec_facets.freeze_discipline` must be in `NN1_HONEST_DISCIPLINES`
- (b) no `researcher_dof_ledger` row may have `evidence_source='OOS'` for this bundle
- A `BACKTEST_SELECTION` facet is logged AND written into `researcher_dof_ledger` (+S contribution to N_effective via `database.insert_dof_ledger_row`) so the violation feeds the haircut as well.
- OOS-peek violations labelled distinctly (stricter than BACKTEST_SELECTION per synthesis §2.5).

### Verification — Bundle hash integrity

autotuner.py:1294-1303: recomputes `bundle_hash` from `facets_json` and compares to stored `bundle_hash`. A tampered bundle is rejected before any Optuna work begins. T14 contract honored.

### Verification — Explicit spec_bundle_id required

autotuner.py:1270-1275: `if spec_bundle_id is None: raise ValueError`. No implicit bundle defaults — Phase-1 strict.

[Confidence: High — every NN1 hazard callout from synthesis §2.5 traced to landed code]

### Sub-finding 9a — Search-space lower-bound `VWAP_CROSS_HWM_PCT` mismatch (LOW)

`OPTUNA_SEARCH_SPACE_KEYS` includes `VWAP_CROSS_HWM_PCT` with bounds `_SS_VWAP_CROSS_HWM_MIN = 0.5` / `_SS_VWAP_CROSS_HWM_MAX = 2.5` (autotuner.py:101-102). The V1 calibration sweep uses narrower bounds `_SS_VWAP_CROSS_HWM_V1_MIN = 0.3` / `_SS_VWAP_CROSS_HWM_V1_MAX = 2.0` (autotuner.py:117-118) with rationale (3-tick confirm + ~2σ daily-return ceiling). This is intentional per the inline comment — not a defect — but the asymmetry between main-path bounds and V1-sweep bounds is worth flagging for the engine-audit lane that owns search-space justification (none currently scaffolded). Methodologically the wider main-path bounds are fine; flagged as LOW because a future audit of search-space provenance may want a single source of truth. [Confidence: Medium]

---

## 10. Cross-cutting — Synthetic history feed + replay determinism

**Severity:** LOW (verify-only)
**Status:** Passes for Phase-1; Phase-2 anchor count documented
**Location:** `synthetic_history.py:30-73`, `synthetic_history.py:281-363`

### Verification — 125-day walk-forward floor

- `_WALK_FORWARD_TRADING_DAYS = 125` (synthetic_history.py:32) — the autotuner replay slice.
- `_MC_WARMUP_TRADING_DAYS = MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1)` — additive warmup PRECEDING the replay window. Matches the project memory `project_mc_eligible_pool_vs_raw_day_boundary`: minimum raw history is MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) = 39, not 20.
- `_REQUIRED_FETCH_TRADING_DAYS = WALK_FORWARD (125) + MC_WARMUP + BUFFER (10)` — enforced by `fetch_daily_bars_with_floor` which widens + refetches up to `_MAX_FETCH_WIDEN_ATTEMPTS = 3`, raising `HistoryShortfallError` rather than feeding a degenerate replay.
- Replay-slice shortfall (intraday axis) raises `HistoryShortfallError` at synthetic_history.py:497-510. Both axes surface identically.
- `run_autotuner` catches it narrowly at autotuner.py:1383-1387 and returns `{"aborted": True, "reason": ...}` rather than swallowing the failure.

### Verification — MC sentinel discipline preserved

- `run_monte_carlo` may return `None` (`MC_INSUFFICIENT_HISTORY_SENTINEL`). The sentinel is carried verbatim into the tick (synthetic_history.py:340-350).
- `_replay_exit_tick` gates every MC-driven branch on `mc_available = mc is not None` (autotuner.py:584). Production's Cluster 2 fail-safe parity.

### Verification — neighbor_k matches production

`synthetic_history.py:340-347`: `math_engine.MC_DEFAULT_NEIGHBOR_K` is the kNN bootstrap CDF parameter. Source-comment correctly justifies the lower path count (300) vs production's 5000: "Paths only add unbiased variance to the bootstrap estimate, so a lower count than production's default is an acceptable speed/precision tradeoff for tuning — only neighbor_k must match production." Replay-determinism anchor honored.

### Verification — Per-(symphony, day) seed

`seed=math_engine.derive_cycle_mc_seed(f"{sym_id}_{date_str}")` (synthetic_history.py:346) — cache rebuilds produce bit-identical mc_prob series. Replay-determinism anchor (Phase-1 = 1 anchor: M2's CVaR off the cycle_id-seeded kNN pool). [Confidence: High]

---

## 11. Statistical validity overall — Phase-1 binding decisions honored

The Phase-1 binding decisions from `feature-plans/decision-science/README.md` §0 are traceable to landed code:

| Binding decision | Site | Verdict |
|---|---|---|
| NN1 spec-freeze (★) | autotuner.py:1266, 1307 | Enforced |
| BHY haircut integrity, N_effective = N_optuna + S | autotuner.py:443-493, 1556-1577 | Implemented |
| S-2 re-derived `compute_crra_eu_tstat` | autotuner.py:321-354, _haircut_select branch | Implemented |
| W-H4 floor on INPUT W | autotuner.py:305-318 | Implemented |
| Replay-determinism anchor (Phase-1 = 1 anchor) | synthetic_history.py:340-347 | Implemented |
| MC sentinel discipline (F-4 ★) | autotuner.py:584, synthetic_history.py:340-350 | Honored |
| Two-DB boundary (E-2 ★) | autotuner.py:1500 (sqlite:///optuna_studies.db) | Honored |
| 125-day walk-forward + 60/20/20 split + purge + embargo at both boundaries | autotuner.py:166-205, 1418-1428 | Implemented |
| 100-trial floor | autotuner.py:1507 (500 hardcoded) | Above floor; not named-constant-pinned (Finding 7) |
| Two-DB pattern + state-DB-only Advisor reads | autotuner.py:1319-1325, 1750-1779 | Honored (advisor_ro_query) |

Methodologically the walk-forward optimization is statistically valid for Phase-1 binding decisions: purge + embargo prevent feature-lookback leakage, frozen-eval is consumed exactly once, BHY haircut + Yekutieli c(N) correct for selection bias under TPE-induced trial dependency, CRRA-EU objective + W-H4 floor close the H-1 NaN-poisoning surface, and the OOS cascade (AI/fallback/default) preserves last-known-good params when the AI proposal does not beat baselines on the validation fold.

The open methodology lanes (sampler/pruner/seed/n_jobs/trial-floor explicitness) are documentation-and-tripwire plans — they harden the discipline but do not change today's behaviour.

---

## Open findings — action surface for the PM (no recommendations, just options)

| # | Finding | Severity | Lane |
|---|---|---|---|
| 1 | Sampler is Optuna implicit default (TPE) at main call site; explicit in V1 sweep | HIGH | `engine-audit/sampler-choice/plan.md` (scaffolded) |
| 6 | `n_jobs=-1` hardcoded (project rule 5 violated); seed implicit `None` | HIGH | `engine-audit/parallelism-reproducibility/plan.md` (scaffolded) |
| 2 | Pruner is Optuna implicit default; `trial.report` absent so silently inactive | MEDIUM | `engine-audit/pruner-choice/plan.md` (scaffolded) |
| 4 | OOS-fold-collapse v2: ~4-5 day usable validation after purge | MEDIUM | `engine-audit/walk-forward-fold-structure/plan.md` (scaffolded) |
| 7 | `n_trials=500` hardcoded literal, not named constant | MEDIUM | `engine-audit/trial-floor-justification/plan.md` (scaffolded) |
| 9a | Search-space bound asymmetry between main path and V1 sweep | LOW | No lane scaffolded (provenance audit) |
| 3 | Study persistence + microsecond-resolution uniqueness | LOW (verify-only) | Closed |
| 5 | BHY haircut + N_effective wiring | LOW (verify-only) | Closed |
| 8 | CRRA-EU objective + S-2 t-stat routing | LOW (verify-only) | Closed |
| 9 | NN1 spec-freeze enforcement | LOW (verify-only) | Closed |

No BLOCKER findings.

---

## Sources

- `autotuner.py` lines 1-1986 (read end-to-end)
- `synthetic_history.py` lines 1-572 (read end-to-end)
- `feature-plans/decision-science/README.md` §0 (binding decisions), §1 (cross-cutting hazards)
- `feature-plans/decision-science/engine-audit/sampler-choice/plan.md`
- `feature-plans/decision-science/engine-audit/pruner-choice/plan.md`
- `feature-plans/decision-science/engine-audit/parallelism-reproducibility/plan.md`
- `feature-plans/decision-science/engine-audit/trial-floor-justification/plan.md`
- `feature-plans/decision-science/engine-audit/bhy-implementation-correctness/plan.md`
- `migrations/optuna_001_archive_accumulated_studies.sql`
- `database.py:1478+` (`get_researcher_dof_ledger_for_run`)
- Project CLAUDE.md (gotchas; team composition)
- Project memory: `project_mc_eligible_pool_vs_raw_day_boundary`, `project_mc_sentinel_consumer_blast_radius`
- Decision-science council synthesis §2.2, §2.5, §3.5, §3.7, §4
