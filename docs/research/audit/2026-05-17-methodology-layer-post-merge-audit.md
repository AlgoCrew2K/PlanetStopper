# Methodology Layer — Post-Merge Audit (2026-05-17)

**Scope:** Autotuner methodology + calibration workstreams O1, O2, O3, O4, O5, O6, V1.
**Repo state audited:** `main @ 0228a37` (head matched origin/main at audit time).
**Auditor mode:** READ-ONLY. No source files modified.
**Auditor citations:** López de Prado 2018 (Ch. 7 Purged k-fold CV, Ch. 7.4 frozen evaluation); Bailey & López de Prado 2014 *Financial Analysts Journal* 70(5):94-107 Eq. 9 (Deflated Sharpe Ratio); Sortino & van der Meer 1994 *Journal of Portfolio Management* (downside-deviation Sortino ratio).

---

## Summary verdict by workstream

| WS | Verdict | Headline |
|----|---------|----------|
| **O1** Purge + embargo | VALIDATED | Purge sized by MAX feature lookback (vol=20, ATR=15) → PURGE_DAYS=20; EMBARGO_DAYS=1; LdP Ch. 7 cited. |
| **O2** Deflated Sharpe | VALIDATED (math + DB persistence) / **ISSUE-Med** (no Discord/UI surface) | DSR formula pinned to Bailey & LdP 2014 Eq. 9 against 5 fixture cases; DB columns present; **not surfaced to Discord or dashboard UI.** |
| **O3** Timestamped studies | VALIDATED | `study_timestamp = strftime("%Y%m%dT%H%M%S%fZ")` + `__symphony`; `load_if_exists=False` in both call sites. |
| **O4** Locked-vars consistency | VALIDATED | Single source of truth (`database.DEFAULT_LOCKED_VARS`); excluded from Optuna; excluded from AI advisor allowlist + Gate 4 in `app.py`; parametrized tests cover every locked var. |
| **O5** Sortino objective | VALIDATED | Sortino replaces 5-magic-number composite; `SORTINO_TARGET_RETURN=0.0`; Sortino & van der Meer 1994 cited; degenerate cases handled. |
| **O6** Frozen-eval fold | VALIDATED | 60/20/20 split; frozen consumed once post-selection from purge+embargo-isolated fold; LdP Ch. 7.4 cited. |
| **V1** Calibration sweep | VALIDATED (search space + report fields + frozen-eval gate + E1) / **ISSUE-Low** (range-mismatch with general allowlist) | Search space limited to the two V1 params; full report schema present; no fleet-wide auto-flip path; post-E1 corrected velocity confirmed by inspection test. |

---

## O1 — Purge + Embargo

`autotuner.py:60-77` defines `PURGE_DAYS=20` and `EMBARGO_DAYS=1` as module-level constants. Source comment explicitly enumerates the two purge-relevant feature lookbacks (`calculate_20d_vol` LOOKBACK_DAYS=20 and `calculate_14d_atr_pct` ATR_LOOKBACK_DAYS=15) and explicitly excludes the exponential decay weight from purge sizing on the correct rationale that decay is an objective aggregation weight, not a feature lookback.

López de Prado 2018 Ch. 7 (Purged k-fold CV) cited at `autotuner.py:71` and `autotuner.py:76`.

Applied at BOTH fold boundaries inside `run_autotuner`:
- Boundary 1 (train|validation): `autotuner.py:618-622` — `effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)`.
- Boundary 2 (validation|frozen-eval): `autotuner.py:624-631` — `val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS`.

Applied identically in `run_calibration_sweep`: `autotuner.py:929-939`.

Tests `tests/autotuner/test_o1_purge_embargo.py` assert (a) feature lookback inventory is read from fixture rather than hard-coded, (b) PURGE_DAYS equals `max(20,15)=20`, (c) embargoed samples are excluded, and (d) literature citation string present in the source.

**Acknowledged tradeoff** documented at `autotuner.py:562-568`: at 125-day history the post-purge usable validation/frozen windows shrink to ~4-5 days each ("OOS-fold-collapse v2"). Future workstream noted — expand history window or move to rolling purged k-fold.

**Verdict: VALIDATED.**

---

## O2 — Deflated Sharpe Ratio

`compute_deflated_sharpe_ratio` at `autotuner.py:122-155` implements Bailey & López de Prado 2014 Eq. 9 literally:
```
DSR = (SR_obs - SR_0) * sqrt(T-1) / sqrt(1 - gamma3*SR_obs + (gamma4-1)/4 * SR_obs^2)
```
with citation in docstring (`autotuner.py:133-135`). Degenerate cases:
- `T <= 1` → 0.0 (no DoF in `sqrt(T-1)`).
- `denom_sq <= 0` → `float("-inf")` (never `+inf` — avoids unfairly favoring the AI branch).

Formula pinned against 5 fixture cases in `tests/fixtures/autotuner/dsr_examples/` (normal, positive-skew, negative-skew, fat-tail, low-Sharpe) with the parametrized test at `tests/autotuner/test_o2_deflated_sharpe.py:100-142`.

Cross-trial moment computation at `autotuner.py:718-729` uses the population estimator (divisor = N, not N-1) for gamma3/gamma4, matching standard moment-based deflation usage in Bailey & LdP 2014; degenerate spread (`std_v == 0`) falls back to normal-distribution assumption (gamma3=0, gamma4=3). DSR re-ranks all completed trials and selects the DSR-maximum (`autotuner.py:731-748`) — not the naive Sortino-max.

Persistence path:
- DB schema: `database.py:62-63` (`deflated_sharpe`, `naive_sharpe` columns on `autotune_runs`).
- Migration: `migrations/006_autotune_runs_sharpe.sql` (additive, NULLable, DEFAULT NULL — matches the project's additive-first migration rule).
- Write site: `autotuner.py:880-892` (`database.save_autotune_run(..., deflated_sharpe=..., naive_sharpe=...)`).

### Issue — Med severity: no Discord / UI surface for DSR

The audit checklist requires DSR be persisted "audit table + Discord report + UI surface". Confirmed coverage:
- Audit table: YES (`autotune_runs.deflated_sharpe`, `autotune_runs.naive_sharpe`).
- Discord report: **NO.** `reporting.py` contains no `deflated|DSR|naive_sharpe` references (grep negative).
- UI surface: **NO.** `templates/` contains no `deflated|naive_sharpe|frozen_eval` references (grep negative).

The operator runtime log line at `autotuner.py:863-868` prints `DSR: {value} (naive: {value})` to stdout, which surfaces in the daemon log but not in the operator-facing surfaces the methodology mandate requires. Recommendation: route DSR + naive into the Discord autotune summary embed and the `/ai-advisor` tab's recent-runs view so the deflation magnitude is visible to the human-in-loop.

**Verdict: VALIDATED (math + persistence) with ISSUE-Med (operator-facing surfaces).**

---

## O3 — Timestamped study names

`autotuner.py:695-696`:
```python
study_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
study = optuna.create_study(study_name=f"{study_timestamp}__{normalized_name}",
                            storage=storage, load_if_exists=False, direction="maximize")
```
Microsecond resolution + UTC + Zulu suffix guarantee collision-free study names across same-second daemon restarts.

Identical pattern in `run_calibration_sweep` at `autotuner.py:962-986`. `load_if_exists=False` is set in both call sites — Optuna will never append trials to a pre-existing study, satisfying the global rule against silent trial accumulation.

Legacy migration: `_apply_optuna_archive_migration_if_needed` at `autotuner.py:505-541` renames any bare (non-prefixed) legacy study to `LEGACY__<name>` non-destructively. SQL migration `migrations/optuna_001_archive_accumulated_studies.sql` is idempotent.

### Minor observation (not an issue)

`run_timestamp` (shared run-level, line 661) is distinct from per-symphony `study_timestamp` (regenerated per-symphony, line 695). The shared `run_timestamp` is used only for `save_autotune_run` row grouping; the per-symphony `study_timestamp` is what appears in the Optuna study name. This means one logical "autotune run" produces N Optuna studies with N distinct timestamps. Cross-cutting concern (see below).

**Verdict: VALIDATED.**

---

## O4 — Locked-vars consistency

Single source of truth: `database.DEFAULT_LOCKED_VARS = ["TRIGGER_THRESHOLD_PCT"]` at `database.py:25-27`. The parametrized test at `tests/calibration/test_locked_vars_consistency.py:140-165` asserts via AST that `autotuner.py` and `ai_advisor.py` do **not** redefine `DEFAULT_LOCKED_VARS`.

Verified by grep — only definition site is `database.py:25`. `autotuner.py:20-21` and `ai_advisor.py:37-38` carry source comments explicitly referencing `database.DEFAULT_LOCKED_VARS` and excluding those keys from their respective allowlists.

### Coverage by branch

1. **Optuna search path** — `OPTUNA_SEARCH_SPACE_KEYS` (`autotuner.py:22-26`) contains the 6 tunable keys; `TRIGGER_THRESHOLD_PCT` is absent. The `objective()` closure at `autotuner.py:670-685` has six `suggest_*` calls — none for `TRIGGER_THRESHOLD_PCT`. AST + regex parametrized tests in `tests/calibration/test_locked_vars_consistency.py:208-317` verify this for every var in `DEFAULT_LOCKED_VARS`.
2. **AI advisor context-assembly path** — `_OPTUNA_SEARCH_SPACE_KEYS` in `ai_advisor.py:39-43` mirrors the autotuner contract. The suggestible surface at `ai_advisor.py:286-303` exposes `locked: key in locked_vars` so Claude can see the lock but cannot have its suggestion adopted.
3. **AI advisor allowlist path** — `_SUGGESTIBLE_ALLOWLIST = frozenset(_OPTUNA_SEARCH_SPACE_KEYS) | {_UNTUNED_SUGGESTIBLE_KEY}` at `ai_advisor.py:572-574`. Since `_OPTUNA_SEARCH_SPACE_KEYS` excludes `TRIGGER_THRESHOLD_PCT`, the allowlist excludes it; `enforce_suggestion_allowlist` at `ai_advisor.py:603-629` structurally rejects it.
4. **Defense-in-depth at write site** — `app.py:846-849` (C2 Gate 4) re-checks `suggestion_obj.config_key in locked_vars` after all three gates and rejects with `"locked var"`. Comment notes this is defense-in-depth.

Parametrized tests cover **all** entries of `DEFAULT_LOCKED_VARS` (the fixture `tests/fixtures/calibration/locked_vars/locked_vars_definition.json` is asserted to mirror the live list — `tests/calibration/test_locked_vars_consistency.py:166-179`).

**Verdict: VALIDATED.**

---

## O5 — Sortino objective

`compute_sortino_ratio` at `autotuner.py:91-119`:
```
mean(r) / sqrt(mean(min(r - target, 0)^2))
```
with population denominator (divisor = N, all observations — not `N_downside`). Reference Sortino & van der Meer 1994 cited at `autotuner.py:98-99`. Degenerate cases:
- Empty returns → 0.0.
- All `r >= target` (zero downside deviation) → `1e6` sentinel (finite — TPE-friendly).

`SORTINO_TARGET_RETURN = 0.0` at `autotuner.py:57-59` with operator-decision PA-5 cited as source.

The objective at `autotuner.py:670-685` calls `_collect_sim_returns(...)` (returns a list of per-triggered-day weighted guard-alphas) and returns `compute_sortino_ratio(daily_returns)`. The pre-O5 composite objective (5-magic-number `missed_upside*1.5 + drawdown*0.75 + 2x loss multiplier` weighting in `run_simulation`) is **not** the active objective; it remains in `run_simulation` solely for the OOS cascade tie-breaking comparison (`autotuner.py:478-501`), which is methodologically acceptable — Sortino selects, composite reports.

The `_GUARD_ALPHA_DECAY_RATE = 0.015` constant at `autotuner.py:54-55` is the only remaining named-constant magic in `_collect_sim_returns`; promoted from a bare literal per the O5 reviewer pass (`4acfd2f`).

Test fixtures `tests/fixtures/autotuner/sortino_examples/` pin the formula against literature values; degenerate-empty case has a dedicated RED test.

**Verdict: VALIDATED.**

---

## O6 — Frozen evaluation fold (60/20/20)

`autotuner.py:79-88` defines `TRAIN_RATIO=0.60`, `VALIDATION_RATIO=0.20`, `FROZEN_EVAL_RATIO=0.20` with a runtime `assert` that they sum to 1.0. López de Prado 2018 Ch. 7.4 cited at `autotuner.py:82` and `autotuner.py:549`.

Split site at `autotuner.py:608-616`:
```python
val_start_idx    = int(total_days * TRAIN_RATIO)
frozen_start_idx = int(total_days * (TRAIN_RATIO + VALIDATION_RATIO))
raw_train_dates    = sorted_dates[:val_start_idx]
raw_val_dates      = sorted_dates[val_start_idx:frozen_start_idx]
raw_frozen_dates   = sorted_dates[frozen_start_idx:]
```

Frozen fold is hidden from the Optuna objective: the objective at `autotuner.py:670-685` passes `history_validation` (purge-reduced validation only) — never `history_frozen`. Confirmed by the call-order tracking test at `tests/calibration/test_v1_calibration_sweep.py:548-625` which marks `optimize_complete` and asserts no `_collect_sim_returns` call hits frozen dates before `study.optimize` returns.

Frozen fold is consumed **once** post-selection at `autotuner.py:817-823` via a single `_collect_sim_returns` call (the Revise consolidation at `d3dc60d` collapsed the prior dual-call pattern). The "consumed once" invariant is asserted in `tests/autotuner/test_o6_frozen_eval.py` (Revise pass added `total-reads` adversarial test at `d5238cb`).

Schema migration `migrations/007_autotune_runs_frozen_eval.sql` adds `validation_sharpe` and `frozen_eval_sharpe` columns to `autotune_runs` (additive, NULLable, DEFAULT NULL). Same Discord/UI surfacing gap noted under O2 applies here.

**Verdict: VALIDATED.**

---

## V1 — Calibration sweep

`run_calibration_sweep` at `autotuner.py:898-1088`.

### Search space — ONLY two parameters

`autotuner.py:967-978`:
```python
p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float(..., _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX)
p["VWAP_CROSS_HWM_PCT"]           = trial.suggest_float(..., _SS_VWAP_CROSS_HWM_V1_MIN, _SS_VWAP_CROSS_HWM_V1_MAX)
```
No other `suggest_*` calls in the V1 closure. Verified by inspection; the contract fixture at `tests/fixtures/calibration/v1/search_space_contract.json` enumerates the prohibited keys and the test class enforces the contract.

### Narrowed V1 VWAP_CROSS_HWM bounds (Low-severity observation)

V1 uses **narrowed** bounds (`_SS_VWAP_CROSS_HWM_V1_MIN=0.3`, `_SS_VWAP_CROSS_HWM_V1_MAX=2.0`) vs. the general autotuner bounds (`_SS_VWAP_CROSS_HWM_MIN=0.5`, `_SS_VWAP_CROSS_HWM_MAX=2.5`) and vs. the AI advisor `_PARAM_VALID_RANGES` allowlist (`low=0.5, high=2.5`). The narrower V1 lower bound (0.3) is justified in the comment block at `autotuner.py:46-51` — the math-engine 3-tick confirm gate prevents spurious single-tick exits at this level. The narrower upper bound (2.0 vs 2.5) is also documented.

**Issue — Low severity:** the AI advisor's `_PARAM_VALID_RANGES[VWAP_CROSS_HWM_PCT]` (`ai_advisor.py:135`) still mirrors the general autotuner range (0.5-2.5). If the V1 sweep proposes a value in `[0.3, 0.5)` and the operator routes it through the AI advisor review flow, the validation range mismatch may trigger a confusing rejection downstream. Recommend either (a) widening `_PARAM_VALID_RANGES` to the V1 envelope, or (b) source-commenting why the discrepancy is intentional.

### Report fields

`autotuner.py:1070-1086` emits per-row dicts containing every required field per `tests/fixtures/calibration/v1/calibration_report_schema.json`: `symphony_id, param_name, current_value, proposed_value, delta_pct, expected_trigger_freq_change, frozen_eval_alpha, naive_sharpe, deflated_sharpe, sortino, n_trials, study_name, trading_day_start, trading_day_end, cycle_id`.

### No fleet-wide auto-flip path

Grep for `auto.?flip|fleet.?wide` in `autotuner.py` returns no matches. Function docstring at `autotuner.py:898-914` explicitly states "Does NOT persist anything to the DB (AC-V1.3: read-only, operator-gated rollout)". `run_calibration_sweep` returns a list of report dicts; no call to `database.save_symphony_strategy` exists in the function body.

### Post-E1 corrected velocity

`autotuner.py:217-261` (`_collect_sim_returns`) initializes `prev_return = None` (E1 sentinel) and uses `effective_prev = ret if prev_return is None else prev_return` before delegating to `math_engine.compute_para_arm_decision`. Same E1 contract in `run_simulation` at `autotuner.py:362-391`. Both are consumed by `run_calibration_sweep` exclusively; no inline parabolic-arm logic remains in the autotuner module. Test `test_calibration_sweep_sim_path_uses_e1_corrected_velocity` and the source-inspection test `test_sim_loops_initialize_prev_return_as_none` both pass against the current code.

**Verdict: VALIDATED with ISSUE-Low (AI advisor valid-range mismatch).**

---

## Cross-cutting concerns

### C1 — V1 consumes O1/O2/O5/O6 consistently

`run_calibration_sweep` re-implements the same fold partitioning logic as `run_autotuner`:
- O1 (purge + embargo at both boundaries): `autotuner.py:929-939` mirrors `autotuner.py:618-631`.
- O2 (DSR re-rank): `autotuner.py:993-1038` mirrors `autotuner.py:705-763`.
- O5 (Sortino objective): `autotuner.py:975-978` and `autotuner.py:1048` use the same `compute_sortino_ratio`.
- O6 (frozen fold consumed once post-selection): `autotuner.py:1050-1054`.

The duplication is real (not a function call) — same logic written twice. Acceptable post-merge because V1 is the operator-gated read-only path, but a near-term refactor opportunity to lift the fold-partitioning + DSR re-rank into a shared helper.

**Issue — Low severity:** fold-partition + DSR re-rank code duplicated between `run_autotuner` and `run_calibration_sweep`. Drift risk if one path is updated without the other. Recommend extracting `_partition_folds_with_purge_embargo()` and `_dsr_rerank_completed_trials()` helpers.

### C2 — O3 timestamped studies vs. operator's ability to compare runs

Each `run_autotuner` invocation generates one `run_timestamp` (line 661) shared across symphonies plus per-symphony `study_timestamp` values. `optuna-compare` (project skill) ingests study names that are unique per-symphony per-second — comparison across days is possible because the timestamp encodes the date. The shared `run_timestamp` written to `autotune_runs.run_timestamp` groups all per-symphony rows from one invocation, so operators can `WHERE run_timestamp = ?` to recover a logical run. **No operator-visibility regression from O3.**

### C3 — Single source of truth audit (locked vars + Optuna + AI advisor)

| Source | Locked-var list | Search space | AI advisor allowlist |
|--------|----------------|--------------|----------------------|
| `database.py:25-27` | `DEFAULT_LOCKED_VARS` (canonical) | — | — |
| `autotuner.py:22-26` | (references DEFAULT_LOCKED_VARS by comment) | `OPTUNA_SEARCH_SPACE_KEYS` — 6 keys | — |
| `ai_advisor.py:39-43` | (references DEFAULT_LOCKED_VARS by comment) | `_OPTUNA_SEARCH_SPACE_KEYS` — duplicate frozenset, 6 keys | `_SUGGESTIBLE_ALLOWLIST` = 6 + `MAX_SQUEEZE_FLOOR` = 7 keys |

`autotuner.OPTUNA_SEARCH_SPACE_KEYS` and `ai_advisor._OPTUNA_SEARCH_SPACE_KEYS` are *duplicate frozenset literals*. The comment at `ai_advisor.py:30-38` explains the duplication is intentional (avoid pulling in `optuna + joblib` import side effects from `autotuner` into the `ai_advisor` import path) and that the C1 test suite asserts equality against the live autotuner contract.

**Issue — Low severity:** verify drift safeguard. Confirmed `tests/calibration/test_locked_vars_consistency.py:430-450` asserts `ai_advisor._OPTUNA_SEARCH_SPACE_KEYS` excludes every locked var. A *positive* drift test (assert exact equality between the two frozensets) would be stronger — currently if `autotuner.OPTUNA_SEARCH_SPACE_KEYS` gains a new key, `ai_advisor` may silently lag.

### C4 — Operating Rule #6: optimization DB metadata table

The system prompt's operating rule #6 requires a one-line who/when/why summary written to the optimization DB metadata table after every run. Confirmed:
- `autotune_runs` is in `alphabot_state.db` (state DB), not `optuna_studies.db` (optimization DB).
- `optuna_studies.db` only stores Optuna's own internal study tables; no `metadata` table written by `autotuner.py`.

**Issue — Med severity (open question):** Operating-rule #6 ("optimization DB metadata table") is not literally implemented. The closest analogue is `autotune_runs` in the state DB, which captures the *outcome* (baseline_decision, deflated_sharpe, frozen_eval_sharpe) but not a free-text "why" field. If the rule's intent is captured by `autotune_runs.baseline_decision + run_timestamp + deflated_sharpe`, no action needed; if a literal "metadata" table with a free-text reason is expected, this is a gap. Flag for PM / user disposition.

---

## Issues summary

| # | Severity | Workstream | Description |
|---|----------|------------|-------------|
| 1 | Med | O2 / O6 | DSR + naive_sharpe + frozen_eval_sharpe persisted to DB but not surfaced to Discord report or dashboard UI. |
| 2 | Med | Cross-cutting C4 | No literal "optimization DB metadata table" with who/when/why. `autotune_runs` covers outcome but not free-text reason. |
| 3 | Low | V1 | AI advisor `_PARAM_VALID_RANGES[VWAP_CROSS_HWM_PCT]` (0.5-2.5) does not match V1 narrowed bounds (0.3-2.0). |
| 4 | Low | Cross-cutting C1 | Fold-partition + DSR re-rank logic duplicated between `run_autotuner` and `run_calibration_sweep`. Drift risk. |
| 5 | Low | Cross-cutting C3 | `autotuner.OPTUNA_SEARCH_SPACE_KEYS` and `ai_advisor._OPTUNA_SEARCH_SPACE_KEYS` are independent literals; positive-equality drift test missing (current tests only assert locked-var exclusion). |

No High-severity issues. No NOT-FIXED workstreams.

---

## File path index

- `autotuner.py` — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/autotuner.py`
- `database.py` — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/database.py`
- `ai_advisor.py` — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/ai_advisor.py`
- `app.py` (C2 Gate 4) — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/app.py:826-854`
- `math_engine.compute_para_arm_decision` — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/math_engine.py:68-89`
- Migrations — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/migrations/006_autotune_runs_sharpe.sql`, `007_autotune_runs_frozen_eval.sql`, `optuna_001_archive_accumulated_studies.sql`
- Tests — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/tests/autotuner/test_o1_purge_embargo.py`, `test_o2_deflated_sharpe.py`, `test_o5_sortino_objective.py`, `test_o6_frozen_eval.py`, `test_study_naming_o3.py`; `tests/calibration/test_locked_vars_consistency.py`, `test_v1_calibration_sweep.py`
- Fixtures — `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/tests/fixtures/autotuner/dsr_examples/`, `purge_embargo/`, `sortino_examples/`, `frozen_eval/`, `e1_sentinel/`, `study_naming/`; `tests/fixtures/calibration/v1/`, `tests/fixtures/calibration/locked_vars/`
