# autotuner

> Optuna walk-forward optimizer: runs 500 trials per symphony over a 250-day sliding window, selects the best trial via the CRRA-EU objective + Harvey & Liu BHY haircut + CSCV PBO acceptance gate, and enforces NN1 spec-freeze discipline throughout. Also provides `run_calibration_sweep` — a separate, advisory-only 2-param sweep over `PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT`.

**Source:** `autotuner.py`
**Last updated:** 2026-07-19 (`DE-AUTOTUNE-REPORTING-001`: `run_autotuner`'s three graceful-abort return sites now uniformly return the structured `{"aborted": True, "reason": ...}` marker -- see the new note in the Returns section below.) Prior: 2026-07-18 (Math Remediation R3-c, `DE-MATH-R3C-001`, merged to `origin/main` via PR #102 @ `d92a6f4f` (code-complete `a5c011dd`; droplet-deploy status not reconciled by this doc-writer, outside this cycle's scope -- flagged to PM separately)) — `_replay_exit_tick`'s `compute_active_trailing_stop` call (`:1276`) now passes `squeeze_floor=p.get("MAX_SQUEEZE_FLOOR", _replay_squeeze_floor_default())`; new `_replay_squeeze_floor_default()` helper (`:76`) returns `alpha_bot_execution.MAX_SQUEEZE_FLOOR` live, matching production's own fallback source (never a replay-local mirror); see the new Squeeze-Floor Replay Parity section below. Prior: 2026-07-18 (Math Remediation R3-b, `DE-MATH-R3B-001`, SHIPPED @ `origin/main` `f3c7e050`, droplet-deployed + verified) — `_replay_exit_tick`'s arm/disarm block is now delegated to the shared `math_engine.compute_arm_disarm_decision` seam, replacing the disarm condition that had been INVERTED (MA-4) and was independently duplicated from production; a new `disarm_confirm_count` key is added to `_fresh_replay_state`; see the corrected note below. Prior: 2026-07-18, Math Remediation R3-a, `DE-MATH-R3A-001` — pre-retune checklist item (a): walk-forward objective-variance coverage extended to all 6 tuned dims (`scripts/objective_variance_probe.py`), source-derived enumeration + suggest-call drift-guard, config-robust across `EXECUTION_START_TIME ∈ {09:30, 9:35}`; prior: 2026-07-17, Math Remediation R2, `DE-MATH-R2-001` — split-level CPCV scoring replacing the 5-path aggregation, train-only adoption holdout, real CRRA-EU frozen-eval metric, regime-conditional exit ticks wired into the undated search path; prior: 2026-07-17, Math Remediation R1 — replay fail-open arm (MA-10), regime-conditional exit ticks (F5), session-window action-phase gate (F6); prior: 2026-07-12, Workstream E)

## Overview

`autotuner.py` implements the per-symphony Bayesian optimization loop. At each autotuner run it:

1. Fetches 250 trading days of synthetic replay history via `synthetic_history.generate_synthetic_history`, passing `n_jobs=_AUTOTUNE_REPLAY_N_JOBS` (= 1) to bound intraday-replay parallelism on the 2-core / 3.0 GiB droplet (DE-AUTOTUNE-OOM). **Math Remediation R1 (`DE-MATH-R1-001`):** as of this cycle, the holdings this history carries are stamped with a REAL per-tick `last_percent_change` by `synthetic_history.build_replay_day` itself (see [synthetic_history](synthetic_history.md) — the fix lives there, not here) — this module's replay loop now receives genuinely price-sensitive MC opinions instead of a day-constant degenerate baseline.
2. Splits history 60/20/20 (train/validation/frozen-eval) with `PURGE_DAYS=20` and `EMBARGO_DAYS=1` per López de Prado 2018.
3. Builds CPCV folds via `_generate_cpcv_folds` (N=6 groups, k=2 test groups, 15 splits) and scores each split DIRECTLY on its own test dates — split-level scoring, `DE-MATH-R2-001` AC-1. The pre-R2 5-path aggregation (`_aggregate_cpcv_paths`) is DELETED; see "CPCV Fold Generation" below for why.
4. Runs `OPTUNA_N_TRIALS_PRODUCTION` (500) Optuna TPE trials; each trial's objective uses the CRRA-EU branch (`run_simulation_crra_eu`) or the legacy Sortino branch. `locked_vars` keys are excluded from `suggest_*` calls.
5. Applies the Harvey & Liu BHY selection haircut via `_haircut_select`, using `compute_n_effective` to compute the honest `N_effective = N_optuna + S` multiple-testing count.
6. Applies STAGE-1 PBO veto gate via `math_engine.compute_pbo` on the top-`_CSCV_TOP_K` (20) pre-BHY configs: rejects if PBO > `PBO_REJECT_THRESHOLD` (0.5).
7. Enforces NN1 spec-freeze: the caller must supply `spec_bundle_id`; `run_autotuner` raises `ValueError` if it is `None`. The caller resolves it via `database.get_or_create_phase1_theory_bundle_id` before calling.
8. **Queries the DoF ledger** (`researcher_dof_ledger WHERE spec_bundle_id = ?` via `advisor_ro_query`) and sums `n_configs_searched` over `BACKTEST_SELECTION` rows into `s_count` (Workstream E, AC-E2, hoisted 2026-07-12 to run BEFORE step 9 so the sum is available to persist — previously this query ran only after the save, feeding solely the in-memory Overfitting Conscience call in step 10 below).
9. Persists the autotune run row via `database.save_autotune_run(..., s_count=...)` (returns int row id used by the OC producer). `s_count` feeds Indicator-3 (operator drift) on LATER runs via their own `prior_runs` query — see "s_count Wiring" below.
10. Post-walk-forward: invokes `run_overfitting_conscience`, `run_spec_critic` (with `symphony_id=None`, called once per bundle), `run_divergence_explainer` (each wrapped in `try/except logger.warning` — Advisor failures are non-fatal). `run_overfitting_conscience` reuses the SAME ledger rows queried in step 8 — the query was hoisted, not duplicated.

## API Reference

### Public Entry Point

#### `run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False, spec_bundle_id: "int | None" = None) → dict`
Main entry point. Runs the full walk-forward optimization for one symphony.

Raises `ValueError` if `spec_bundle_id` is `None` — the caller must resolve it via `database.get_or_create_phase1_theory_bundle_id` before calling. This is the NN1 Phase-1 spec-freeze contract.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `bot_state` | `dict` | Current daemon state dict (all symphonies) |
| `current_date_str` | `str` | ISO-8601 date string for today |
| `account_uuids` | `list[str]` | Composer account UUIDs for this run |
| `is_forced` | `bool` | When True, bypasses market-hours guard |
| `spec_bundle_id` | `int \| None` | Phase-1 theory bundle id; raises ValueError if None |

**Returns:** dict with keys:
- `baseline_decision`: `"AI"`, `"fallback"`, or `"default"`
- `oos_alpha`: OOS guard-alpha of the selected strategy
- `train_alpha`: train-fold guard-alpha
- `fallback_oos_alpha`, `default_oos_alpha`: cascade alternatives
- `selection_tstat`: Harvey & Liu haircut t-statistic of the winning trial
- `n_effective`: `N_optuna + S`
- `spec_bundle_id`: bundle_hash of the active Phase-1 theory bundle
- `eval_window_days`: per-fold day-counts for operator visibility (OPTUNA-4)

**Graceful-abort return (`DE-AUTOTUNE-REPORTING-001`, F-004):** on any of the three abort conditions below, `run_autotuner` returns `{"aborted": True, "reason": <str>}` instead of the success dict above -- a uniform shape across all three sites so `reporting.send_eod_discord_post` renders an explicit "Autotuner Aborted" notice rather than the abort being indistinguishable from a genuine no-change run:
- `synthetic_history.HistoryShortfallError` raised during history fetch (`autotuner.py:2362-2364`) -- reason = the exception's own message.
- Empty/falsy `history_125d` after fetch (`autotuner.py:2365-2367`) -- reason = `"Failed to generate synthetic history."`. **Previously returned bare `None`** (F-004).
- Fewer than 2 total trading days after date-partitioning (`autotuner.py:2376-2378`) -- reason = `"Need at least 2 days of history for WFA."`. **Previously returned bare `None`** (F-004).

All three sites return the SAME shape as of this fix; no `run_autotuner` abort path returns bare `None` anymore. Regression tests: `tests/autotuner/test_autotune_abort_paths_structured_marker.py` (8 tests, parametrized across all three trigger conditions). See `DE-AUTOTUNE-REPORTING-001` in `DECISIONS.md`.

---

### Walk-Forward Simulation

#### `run_simulation(params: dict, history_data: dict, acc_sym_ids: list, current_date_str: str, deviation_dict: dict) → float`
Runs the validation-fold simulation with `params` and returns the aggregate guard-alpha. Used for OOS re-validation in `ai_advisor.revalidate_suggestion_oos`. **Math Remediation R2 (AC-4, `DE-MATH-R2-001`):** this is one of the two UNDATED simulation entry points; it now resolves `exit_confirm_ticks` via `_replay_resolve_regime_exit_ticks` (R1's shared no-lookahead helper, reused not duplicated) before calling `_replay_exit_tick`, matching `_collect_sim_returns_dated`'s R1-era pattern. R1's `xfail(strict=False)` tripwire (`tests/autotuner/test_ac4_r2_residual_tripwire.py`) XPASSed and its marker is REMOVED — the test now passes for real. **Math Remediation R3-a:** also the scoring entry point `scripts/objective_variance_probe.py`'s `walkforward_dim_sweep` drives (never `run_autotuner`) — see [scripts/objective_variance_probe](scripts_objective_variance_probe.md).

#### `_collect_sim_returns(p, history_data, acc_sym_ids, current_date_str, deviation_dict) → list[float]`
Runs the guard-alpha simulation and returns per-triggered-day guard-alpha values. Shared tick loop with `run_simulation` via `_replay_exit_tick`. No recency decay (Decision D5). **Math Remediation R2 (AC-4, `DE-MATH-R2-001`):** the second UNDATED entry point — same regime-tick wiring as `run_simulation` above, landed in the same commit (`c66457dd`).

#### `_collect_sim_returns_dated(p, history_data, acc_sym_ids, current_date_str, deviation_dict, *, score_dates=None) → list[tuple[str, float]]`
Runs the guard-alpha simulation and returns `(date, guard_alpha)` pairs (guard_alpha as raw percent — the T5 provenance contract; NOT a decimal-fraction dict, correcting a stale claim in a prior revision of this doc). Used by `_haircut_select` to build `cscv_date_returns` for `math_engine.compute_pbo` (STAGE-1 PBO veto) and, as of R2, by `objective()`'s per-split CPCV scoring loop as the SOLE replay pass per split (see "CPCV Fold Generation" below — the flat score list is derived from this call's own output rather than issuing a second `_collect_sim_returns` call over the same dates).

**`score_dates` keyword-only parameter (`set[str] | None`, default `None`) — Math Remediation R2, AC-1-adjacent (`DE-MATH-R2-001`), "the catch of the cycle":** when provided, restricts which dates undergo the expensive per-tick replay and appear in the returned list — dates present in `history_data` but absent from `score_dates` are skipped entirely. Regime-lookback chronology (`sorted_dates`/`date_to_idx`, and therefore `_replay_resolve_regime_exit_ticks`'s trailing window) is ALWAYS built from the FULL `history_data` regardless of `score_dates` — this is the decoupling that lets a per-split CPCV caller pass the full per-symphony history (gap-free regime chronology) while restricting each split's own scored-date set, instead of the old pattern of pre-filtering `history_data` itself (which would corrupt the trailing-window chronology into a gappy sample). `None` preserves the original behavior: every date in `history_data` is scored. Regime-conditional `exit_confirm_ticks` resolution (R1, extended below) is unchanged by this parameter — it was already wired here.

#### `replay_exit_sequence(ticks, params, *, grace_minutes) → list[dict]`
Pure observability helper. Runs the per-tick exit loop over one day and returns one `{"tick_idx", "exit_reason", "armed", "tp_armed", "para_armed"}` dict per executed tick — exit_reason is the `resolve_trigger_priority` exit-reason string on the tick the position exits, `None` on every non-exit tick; the loop stops after the first exit (production commits the exit and freezes the symphony for the day). **Math Remediation R1 (AC-6, `DE-MATH-R1-001`):** the three `*_armed` keys are new this cycle — they mirror `test_c3_replay_exit_parity.py`'s `_production_exit_sequence` output shape so the AC-6 bar-level parity battery can compare per-tick STATE, not just the final exit decision. AC-4 is deliberately NOT wired through this function — day-level regime resolution lives one layer up, in `_collect_sim_returns_dated` above, and every AC-6 scenario uses `regime_label=None` to match this function's implicit default; extending this signature would break that alignment. Used by AC-6 parity tests. **Math Remediation R3-a:** also the fire-trace `scripts/objective_variance_probe.py`'s `walkforward_dim_sweep` reads its codepath-fire counts from (the same per-tick core `run_simulation` scores with, not a second simulation).

#### `_replay_exit_tick(state, tick, tick_idx, n_ticks, p, grace_minutes, execution_start_hhmm: str = "09:30", exit_confirm_ticks: int = math_engine.EXIT_CONFIRM_TICKS) → str | None`
Single-tick exit core. All three simulation callers (`run_simulation`, `_collect_sim_returns`, `_collect_sim_returns_dated` via `replay_exit_sequence`) call this function — one canonical copy of the exit orchestration. Returns the `resolve_trigger_priority` exit-reason string when an exit fires, else `None`.

**Math Remediation R1 (`DE-MATH-R1-001`) — three fixes landed in this function:**
- **AC-5/F6 (session-window action-phase gate):** immediately after the unconditional DATA-phase HWM update, calls `_replay_in_action_phase(tick_idx, execution_start_hhmm)` and returns `None` before any decision logic if the action phase has not opened yet — mirroring production's `if current_time < market_open and not force_run: return` gate (`alpha_bot_execution.py:951-953`). Distinct from the pre-existing N-3 VWAP-grace suppression, which only silences VWAP signals inside a grace window AFTER the action phase has already opened.
- **AC-3/MA-10 (fail-open arm):** `should_arm` resets to `False` every tick and is set `True` either on the pre-existing `mc_available and take_profit_mc <= mc < trigger_threshold` condition OR, new this cycle, when `not mc_available` — an absent MC opinion now ARMS the protective stop (mirrors `alpha_bot_execution.py:1324-1326`, audit rule H-3) instead of leaving it dark.
- **Disarm (Math Remediation R3-b, `DE-MATH-R3B-001`, 2026-07-18, SHIPPED @ `origin/main` `f3c7e050`, droplet-deployed + verified):** both `should_arm` and disarm are delegated entirely to `math_engine.compute_arm_disarm_decision` (see [math_engine](math_engine.md)) — disarm requires `prob_underperforming` to fall STRICTLY below `take_profit_mc` (the arm-band's own lower edge, i.e. genuine recovery) for `DISARM_CONFIRM_TICKS` consecutive ticks; `current_return` plays no role in the disarm decision. `is_triggered=False` is passed as a literal (not read from `state`, which carries no `"triggered"` key) — the replay loop breaks on the first exit and never re-enters a tick with triggered state mid-day, mirroring the `is_triggered=False` literal already passed to `compute_exit_confirmation`/`compute_tp_confirmation`/`compute_vwap_breakdown_update` in this same function. See `DE-MATH-R3B-001` in `DECISIONS.md` for the prior (MA-4 inverted) behavior this replaced.
- **AC-4/F5 (regime-conditional confirm ticks):** the new `exit_confirm_ticks` keyword param is passed explicitly into `math_engine.compute_exit_confirmation` rather than left to that function's own module-level default — defaults to the SAME `math_engine.EXIT_CONFIRM_TICKS` constant, so a caller that never resolves a regime label (both undated entry points, see above) sees byte-unchanged behavior.

`state` is a mutable dict carrying per-position transient state across ticks within a single day. `n_ticks` is the day's tick count, used to derive time_ratio from the actual session length.

**Config robustness, R3-a finding (`DE-MATH-R3A-001`):** this function's timing gates (`_replay_in_action_phase`, VWAP grace) are keyed off `alpha_bot_execution.EXECUTION_START_TIME`, which is operator-configurable — the droplet-production `.env` sets it to `9:35`, not the `09:30` code default the test-suite conftest pins. `scripts/objective_variance_probe.py`'s fixtures were re-timed (`db164fb8`) to pad discriminating ticks past both gates at either value, so the R3-a walk-forward objective-variance proof (item (a) below) holds under the config the R3-d retune actually runs under, not just the test-pinned default.

#### `_replay_in_action_phase(tick_idx: int, execution_start_hhmm: str) → bool`
**New, Math Remediation R1 (AC-5/F6).** Returns `True` iff `tick_idx` is at or after `EXECUTION_START_TIME`'s session-open-anchored offset — i.e. production's ACTION PHASE would have run on this tick. Mirrors `alpha_bot_execution.py:951-953`'s hard gate; the DATA phase (HWM tracking) runs unconditionally from the true 09:30 open regardless of this gate.

#### `_replay_execution_start_offset_minutes(execution_start_hhmm: str) → int`
**New, Math Remediation R1.** Returns `EXECUTION_START_TIME`'s minute-bar offset past the 09:30 ET session open (tick_idx 0): `(h - 9) * 60 + (m - 30)`. Single source of truth for this formula, extracted this cycle so the pre-existing `_replay_in_open_window_grace` (N-3, VWAP-grace suppression) and the new `_replay_in_action_phase` (AC-5) can never drift apart — both now call this helper instead of each computing the offset inline.

#### `_replay_resolve_regime_exit_ticks(dates_data: dict, sorted_dates: list, date_idx: int) → int`
**New, Math Remediation R1 (AC-4/F5).** Recomputes the regime-conditional `exit_confirm_ticks` FRESH for one replay day, using ONLY EOD daily returns from dates strictly before `sorted_dates[date_idx]` (no lookahead). Resolves the regime label via `regime_classifier.classify_regime()` over the trailing `regime_classifier.MIN_LABEL_SERIES_LENGTH` (=20) days rather than reading `database.get_cached_regime_label` — that accessor is a single-row, latest-wins LIVE cache with no per-historical-date granularity, so consulting it during a walk-forward replay would inject today's label into every one of the ~250 replayed days (a lookahead violation). Insufficient trailing history → `classify_regime` returns `None` → `math_engine.apply_regime_exit_adjustment`'s own safe default fires (base ticks unchanged), never an invented replay-only fallback. Mirrors production's `apply_regime_exit_adjustment(regime_label, base_ticks)` composition (`alpha_bot_execution.py:1436-1448`) with a walk-forward-safe label source.

**R1 residual, CLOSED by Math Remediation R2 (`DE-MATH-R2-001` AC-4):** R1 wired this helper into `_collect_sim_returns_dated` only (the CSCV/PBO selection path), deliberately deferring the undated `run_simulation`/`_collect_sim_returns` (Optuna's per-trial search-score objective) to R2, since those surfaces were about to be rebuilt by R2's CPCV redesign anyway. **As of R2, all three simulation entry points call this same helper** — `run_simulation` (`autotuner.py:1864`), `_collect_sim_returns` (`autotuner.py:1475`), and `_collect_sim_returns_dated` (`autotuner.py:1603`) — so Optuna's per-trial search score and the selection/diagnostic layer finally share ONE exit semantic. `run_simulation_crra_eu` needed no separate change — it delegates entirely to `_collect_sim_returns` and inherits regime-faithfulness for free. R1's binding rider ("no R3 retune ships until the search objective itself is regime-faithful") is now satisfied. `tests/autotuner/test_ac4_r2_residual_tripwire.py`'s `xfail(strict=False)` marker is REMOVED — the test passes for real.

---

### Squeeze-Floor Replay Parity (Math Remediation R3-c, `DE-MATH-R3C-001`, 2026-07-18, merged to `origin/main` via PR #102 @ `d92a6f4f` (code-complete `a5c011dd`; droplet-deploy status not reconciled by this doc-writer, outside this cycle's scope -- flagged to PM separately))

#### `_replay_squeeze_floor_default() → float`
Returns `alpha_bot_execution.MAX_SQUEEZE_FLOOR` — the SAME module
attribute production's own `acc_params.get("MAX_SQUEEZE_FLOOR",
MAX_SQUEEZE_FLOOR)` fallback reads (`alpha_bot_execution.py:1236`), never
a replay-local mirror literal (the R1/F6 `EXECUTION_START_TIME` idiom,
reused — see `_replay_execution_start_time` above). The `import
alpha_bot_execution` is function-local because a top-level import would be
circular (`alpha_bot_execution` imports `autotuner`). Read live at call
time, not cached at import — an operator's env override or a test
monkeypatch of the module attribute reaches the replay exactly as it
reaches production.

`_replay_exit_tick`'s own `compute_active_trailing_stop` call (`:1276`)
passes `squeeze_floor=p.get("MAX_SQUEEZE_FLOOR",
_replay_squeeze_floor_default())` — a per-symphony params dict missing the
key resolves to the identical floor on both paths. See
[math_engine](math_engine.md) for the seam contract itself.

---

### CPCV Fold Generation (Phase 2)

#### `_generate_cpcv_folds(sorted_dates, n_groups=_CPCV_N_GROUPS, k_test=_CPCV_K_TEST_GROUPS, purge_days=PURGE_DAYS, embargo_days=EMBARGO_DAYS) → list`
Partitions dates into Combinatorial Purged Cross-Validation (CPCV) folds. Generates every `C(n_groups, k_test)` = `C(6,2)` = 15 splits. Applies purge+embargo at EVERY train/test seam. Each fold descriptor dict contains:
- `train_dates` — effective (post-purge/embargo) training date set
- `test_dates` — raw test date set

**`path_membership` is DROPPED from the returned dict as of Math Remediation R2 (`DE-MATH-R2-001` AC-1)** — its sole consumer, `_aggregate_cpcv_paths`, was deleted (see below). Pure function of the date list — no I/O, no DB calls. Unchanged by R2 otherwise: `test_cpcv_fold_generation.py`'s 34 tests confirm the 15 splits themselves are unaffected (already pairwise-distinct ~1/3-window subsets both before and after R2).

**Docstring/comment audit finding (filed to r2-stats, not self-edited):** `autotuner.py`'s own docstring for this function (and the still-declared-but-now-dead `group_path_ptr` local variable at `autotuner.py:530`) still describe the mlfinlab first-available-slot `path_membership` assignment algorithm in detail — that algorithm is no longer executed (confirmed: `grep -n group_path_ptr autotuner.py` returns exactly one hit, the declaration itself, never read or incremented). The docstring should be updated to match the current (path_membership-free) return contract, and the dead `group_path_ptr` line removed or explicitly marked historical.

#### Split-level CPCV scoring (Math Remediation R2, `DE-MATH-R2-001` AC-1 — replaces `_aggregate_cpcv_paths`)

**`_aggregate_cpcv_paths` is DELETED.** Trial scoring no longer assembles 5 backtest "paths" from the 15 splits — `run_autotuner`'s `objective()` closure (`autotuner.py:2475-2598`) scores each of the 15 real splits DIRECTLY on its own `test_dates`, via one call per split to `_collect_sim_returns_dated(p, _cpcv_history, [target_sym_id], current_date_str, deviation_dict, score_dates=_split_dates)`. The trial's objective value is the mean across the 15 `split_scores` (`autotuner.py:2598`). `_cpcv_history` is `history_train` — the SAME train-only, purge-trimmed dict shared by every split's call (never pre-filtered to that split's own dates); `score_dates` restricts which dates that call actually replays, without corrupting the regime-lookback chronology (see `_collect_sim_returns_dated`'s `score_dates` parameter above — this is the AC-1-adjacent fix that makes split-level scoring safe).

**WHY canonical backtest-path aggregation was retired — this is the load-bearing paragraph; a future cycle must NOT "restore" path aggregation as a fix for anything.** Canonical CPCV at N=6/k=2 mathematically FORCES every one of the phi=5 assembled paths to span the FULL eligible window — each path is stitched from N/k=3 non-overlapping splits whose test groups union to ALL N groups, by the combinatorics of the mlfinlab path-assignment algorithm itself, regardless of how date-attribution within that assembly is implemented. Because this codebase has NO per-fold refit — a date's guard_alpha is a pure function of `(date, trial params)`, entirely independent of which fold or path it is attributed to — identical path date-sets produce BITWISE-IDENTICAL path scores BY CONSTRUCTION. **No aggregation-level fix of any kind can produce distinct path scores; the defect was never in HOW dates got attributed to paths, it is that "backtest paths" are a REFIT-WORLD construct with no honest meaning in a no-refit codebase.** This was proven three independent ways before the fix landed (`DECISIONS.md` `DE-MATH-R2-001`, "AC-1 ruling history"): r2-test's live probe, r2-stats's `phi=C(N-1,k-1)` bijection proof plus a 150-date empirical check, and the PM's refit-world argument. An earlier hypothesis in the SAME plan round (per-group date-attribution bug, `feature-plans/math-r2.md` ADDENDUM 1) was RATIFIED then FALSIFIED by r2-test's own probe (ADDENDUM 3) — recorded honestly in `DE-MATH-R2-001`, not silently corrected. The honest CSCV-native granularity, once path aggregation is abandoned, is the `C(6,2)=15` SPLIT ensemble itself — which is exactly what `_generate_cpcv_folds` already produces and always has.

**Consumer trace confirmed zero remaining consumers (`DE-MATH-R2-001` ADDENDUM 5):** zero DB columns, zero OC/Spec-Critic readers ever read path-level artifacts; `math_engine.compute_pbo` independently implements its own S=8 CSCV (its body is zero-diff — only `cscv_date_returns`'s construction moved from path- to split-provenance). Renames: `path_scores` trial user_attr → `split_scores`; the sentinel-filter divisor moved from `_CPCV_N_PATHS` to `_CPCV_N_SPLITS` (`_PARTIAL_SENTINEL_MEAN_THRESHOLD`, below). `_CPCV_N_PATHS` itself is **NOT deleted** — see Constants below.

**Path-completeness tests' supersession story:** the pre-existing path-completeness tests (`test_cpcv_fold_generation.py`'s `TestAC2PathAggregation`, 4 tests) correctly pinned a real property of the OLD design — but that design is a refit-world construct the new split-level design no longer uses for scoring. They received a documented root-cause SUPERSESSION verdict (never a blind deletion): `TestAC2PathAggregation`'s 4 tests are marked superseded; `test_objective_calls_aggregate_not_fifteen_separate_evaluations` was re-pinned as a no-trial-count-inflation check (a sibling test already covers the same property under the new design); fold-key assertions were updated to drop `path_membership`; sentinel-filter tests re-derive their fixtures at N=15. One test needed NO supersession at all: `test_haircut_tstat_no_path_duplication`'s date-keyed dedup invariant survives the redesign unchanged, because de-duplication by date was always independent of path vs. split provenance. Full routing detail: `DECISIONS.md` `DE-MATH-R2-001`, "Decision: AC-1."

**Superseded — the original canonical backtest-path design (walk-forward-overhaul cycle, retired 2026-07-17 by `DE-MATH-R2-001`; kept here for historical record, never called at runtime as of this cycle):** `_aggregate_cpcv_paths(folds: list, n_paths: int = _CPCV_N_PATHS) -> list` used to assemble `n_paths`=5 OOS backtest paths from the 15 fold descriptors' `path_membership` lists, returning a list of `n_paths` sorted date lists — one "path" per assembled sequence of non-overlapping splits, following the canonical mlfinlab `_fill_backtest_paths` first-available-slot algorithm (each group tracked a "next available path pointer," incremented as combinations were assigned in lexicographic order). This machinery was built during the walk-forward-overhaul cycle (`feature-plans/walk-forward-overhaul.md` Phase 2, `project_walk_forward_overhaul_complete` in project memory) as the intended CSCV consumption mechanism. The App-Math Audit (`DE-MATH-AUDIT-001` MA-2, CRITICAL, 2026-07-17) found it was a structural no-op — `_aggregate_cpcv_paths` read only `test_dates`, `train_dates` and all purge/embargo arithmetic had zero consumers, and (as later proven, see above) no fix to that reading behavior could have worked anyway, since the paths themselves are mathematically forced to be identical without refit. This entry is a superseded-with-date record, not a rewrite of the walk-forward-overhaul cycle's original design intent.

---

### Adoption Cascade (OOS Validation) — Math Remediation R2, `DE-MATH-R2-001` AC-2

After the Optuna search completes and a winning trial is selected (via the Harvey & Liu haircut, below), `run_autotuner` evaluates that winner — plus the fallback and default-params baselines — on an out-of-sample fold before deciding whether to adopt it. As of R2, this fold is a GENUINE holdout: CPCV/trial selection (above) is restricted to the TRAIN-only, purge-trimmed window (`train_dates`), so `validation_dates_full` (the raw validation fold) is never touched by selection at all.

**`history_validation_full` is read DIRECTLY by all three adoption-cascade `run_simulation` calls** (`oos_alpha`, `fallback_oos_alpha`, `default_oos_alpha` — `autotuner.py:2807`, `:2839`, `:2849`) — symmetrically, no pro-adoption asymmetry between the AI arm and the baselines. The pre-fix intermediate `history_test = history_validation_full` alias (which the audit's MA-5 finding cited) is REMOVED entirely rather than reassigned: all three calls share one identical argument expression.

**`purge_integrity_ok`** (`autotuner.py:2378`) is now a genuinely computed boolean — `(val_start_idx - effective_train_cutoff) >= (PURGE_DAYS + EMBARGO_DAYS)` — replacing the pre-fix hardcoded `True` literal the audit flagged as a false attestation. It can legitimately evaluate `False` on a short-history edge case where `effective_train_cutoff`'s `max(0, ...)` clamp bites — an attestation that can fail is a real attestation.

### Frozen-Eval (Honest Post-Selection Metric) — Math Remediation R2, `DE-MATH-R2-001` AC-3

The final 20% fold (`history_frozen`) is consumed exactly once, post-selection, as the honest performance metric no Optuna trial callback ever saw. Prior to R2, the CRRA-EU objective branch hardcoded `frozen_eval_sharpe_value = None` here — 20% of the data budget bought zero measurement whenever a symphony used the production CRRA-EU objective.

**As of R2**, the `crra_eu` branch (`autotuner.py:2884-2899`) computes a REAL metric via `math_engine.compute_crra_eu_objective` over the frozen fold's returns (percent→decimal via `RETURN_PCT_TO_FRACTION`, mirroring `run_simulation_crra_eu`'s own conversion) — the same utility function the Optuna objective itself uses, just applied to data selection never touched. The Sortino branch (`compute_sortino_ratio`) is unchanged. The resulting value flows into the SAME, already-wired `frozen_eval_sharpe` persisted column and `reporting.py`'s Discord selection-line display (`reporting.py:500`) — no new consumer was needed; the "reported and consumed" requirement was satisfied structurally once the value stopped being hardcoded `None`. The pre-existing null-on-rejection discipline (N-1's haircut-rejection / cascade-demotion paths still null the field) is unchanged.

---

### CRRA-EU Objective Branch

#### `run_simulation_crra_eu(params, history_data, acc_sym_ids, current_date_str, deviation_dict, *, gamma: float) → float`
Runs the Phase-1 CRRA-EU objective. Collects per-triggered-day percent returns via `_collect_sim_returns`, converts to decimal fractions (`/ RETURN_PCT_TO_FRACTION`), then calls `math_engine.compute_crra_eu_objective(daily_returns_fraction, gamma)`.

**Returns:** `float` — mean CRRA utility over triggered days. Returns `0.0` if no triggered days. Note: returns a single `float`, not a tuple.

---

### CRRA-EU Derivation Helpers

These functions live in `autotuner.py` (not `math_engine.py`) and are called by `_haircut_select` for the CRRA-EU branch.

#### `derive_wealth_argument(r_policy_fraction: float) → float`
Returns the raw per-period gross wealth ratio `W = 1 + r_policy_fraction` (W-H2 formula). The floor is NOT applied here.

#### `derive_floored_wealth_argument(r_policy_fraction: float) → float`
Returns `max(WEALTH_ARG_FLOOR, 1 + r_policy_fraction)`. Complete W-H2 + W-H4 construction. Call this before `compute_crra_utility`.

#### `compute_crra_eu_tstat(U_series: list[float]) → float`
Per-trial t-statistic for the CRRA-EU objective: `mean(U) / (sd(U) / sqrt(T))`. Uses sample stdev (ddof=1). Returns `0.0` for T ≤ 1 or a constant series. Do NOT use `compute_sortino_tstat` for CRRA-EU (H-6 category error).

---

### Harvey & Liu Selection Haircut

#### `_haircut_select(completed_trials, n_effective=None, tstat_fn=compute_sortino_tstat, gamma=None) → tuple`
Applies the BHY selection-bias haircut to a set of completed Optuna trials.

Steps per trial:
1. t-statistic via `tstat_fn(daily_returns)` (for CRRA-EU: U-transforms the returns first)
2. One-sided p-value via `compute_haircut_pvalue(t)`
3. BHY adjustment via `benjamini_hochberg_adjust` over `N_effective` p-values (Shape A: pads with `S` copies of 1.0)
4. Winner = argmin(p_adj); deployable iff `p_adj <= HARVEY_LIU_FDR_Q`

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `completed_trials` | `list` | Optuna trial objects with `value` and `user_attrs["daily_returns"]` |
| `n_effective` | `int \| None` | Honest N from `compute_n_effective`; defaults to `len(trials)` |
| `tstat_fn` | `callable` | `compute_sortino_tstat` (Sortino) or `compute_crra_eu_tstat` (CRRA-EU) |
| `gamma` | `float \| None` | CRRA gamma for U-transform; required when `tstat_fn` is CRRA-EU |

**Returns:** `(winner_trial, winner_p_adj, winner_tstat)` — all `None` when no trial clears the gate.

#### `compute_sortino_tstat(returns, seed: int = 0) → float`
Per-trial t-statistic via nonparametric bootstrap SE (Efron 1979): `Sortino / SE_bootstrap`. Returns `0.0` (conservative rejection) when bootstrap SE is unavailable. **Not the same seed-derivation context as `advisors.backtest_gate_engine.evaluate_candidate_batch`'s AC-D3 fix** — that call site now derives its seed from a stable hash of each candidate's own id (not batch position); this module's own `_haircut_select` caller uses `seed=trial_idx` within one fixed, never-reordered Optuna study, which was never order-dependent and was intentionally left untouched by the AC-D3 fix.

#### `compute_haircut_pvalue(t_stat: float) → float`
One-sided p-value `1 - Φ(t)`, clamped to `[_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON]`.

#### `benjamini_hochberg_adjust(p_values: list[float]) → list[float]`
BHY step-up adjustment with Yekutieli `c(N) = sum(1/j)` arbitrary-dependence factor. Returns one adjusted p-value per input in the original order.

#### `compute_n_effective(n_optuna: int, ledger_query, winning_spec_bundle_id: str | None = None) → int`
Returns `N_optuna + S`, the honest multiple-testing count. `S` = sum of `n_configs_searched` over `BACKTEST_SELECTION` ledger rows, excluding frozen-eval-tainted rows and the winning bundle. `ledger_query` is a callable injected for testability.

**Distinct from `s_count` (Workstream E):** `compute_n_effective`'s `S` is computed for THIS RUN's own N_effective (current-run I-1/I-2 math, unchanged by Workstream E) and excludes the winning bundle; the `s_count` persisted via `save_autotune_run` (see below) is a separate accumulator over ALL `BACKTEST_SELECTION` rows for the run's `spec_bundle_id` (no winning-bundle exclusion), consumed by LATER runs' `overfitting_conscience` drift check, not by this run's own haircut.

---

### NN1 Spec-Freeze Enforcement

#### `validate_search_space_nn1() → None`
Asserts that `OPTUNA_SEARCH_SPACE_KEYS` contains no frozen facets. Called at the top of `run_autotuner` before any trial runs.

#### `validate_nn1_compliance(spec_bundle_id: int) → tuple[bool, list[str]]`
Returns `(is_nn1_honest, violations)`. Checks spec_facets discipline (must all be in `NN1_HONEST_DISCIPLINES`) AND researcher_dof_ledger for `evidence_source='OOS'` (frozen-eval peek). Default-deny: any unrecognized discipline is a violation.

---

### Calibration Sweep

`run_calibration_sweep` is a standalone advisory function — separate from the production walk-forward. It sweeps exactly 2 parameters (`PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT`) per symphony, applies the same overfitting controls as `run_autotuner` (Harvey & Liu BHY haircut, PBO veto), and returns report rows for the operator. It never writes to the state DB, never applies any parameter to live settings, and is not on the execution path.

**Search-space scope (2-param, research-verified):** Three candidates were evaluated and excluded:
- `VWAP_BLEED_ARM_MIN` / `VWAP_BLEED_ARM_MAX` — output clamps on the already-swept `VWAP_BLEED_MULTIPLIER`; the trailing-stop literature (Kaminski & Lo 2014; Dai et al. 2021) shows guardrail threshold response is flat across a wide range — no fittable optimum exists for an optimizer to find.
- `VWAP_BREAK_CONFIRM_TICKS` — excluded this cycle on data-sufficiency grounds: adding it moves the dedicated sweep 2-D → 3-D at the 100-trial floor (`100^(1/3) ≈ 4.6` levels/axis, a >2x density drop vs. 2-D); requires ~1,000-trial floor raise before inclusion.

See `DE-CALSWEEP-001` in `DECISIONS.md` and `.claude/calibration-methodology-verdict.md` for the full research basis.

#### `run_calibration_sweep(history_data, current_params, current_date_str, deviation_dict, random_state, *, min_history_days: int = _CALSWEEP_MIN_HISTORY_DAYS) → list[dict]`
V1 calibration sweep over `PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT` only. Applies identical fold methodology to `run_autotuner` (60/20/20 split with O1 purge+embargo) but search space is limited to 2 params. Does NOT persist anything to the DB (read-only; operator-gated rollout).

**AC-4 — insufficient-history skip:** Symphonies with fewer than `min_history_days` days of history are skipped with a warning log. The production default is `_CALSWEEP_MIN_HISTORY_DAYS` (125) — production behavior is unchanged. Below this threshold the fold partitioning produces validation windows too small for the Sortino objective to yield meaningful signal. The param is injectable so test suites can pass `min_history_days=0` to exercise contracts on short fixtures without weakening the production floor (see DE-CALSWEEP-002).

**AC-5 — PBO veto surfaced per symphony:** When the BHY haircut finds no trial that clears the FDR gate, `pbo_veto_status=True` is set on every report row for that symphony. This is surfaced prominently in the advisory report so the operator knows the proposed value is the naive Optuna winner and is NOT statistically qualified.

**AC-6 — timestamped study-name with `__calsweep` suffix:** Each symphony's study name is `{timestamp}__{symphony_id}__calsweep` — identifiable at a glance and guaranteed never to collide with production `run_autotuner` study names.

**AC-7 — trigger-frequency operator-review flag:** When the proposed params would cause trigger frequency to exceed `_CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER` (2.0×) the current count on the validation fold, `flag_for_operator_review=True` is set. The operator must review before any per-symphony deploy.

**VWAP_CROSS_HWM_PCT bounds asymmetry:** The V1 calibration sweep uses asymmetric bounds (`_SS_VWAP_CROSS_HWM_V1_MIN=0.3`, `_SS_VWAP_CROSS_HWM_V1_MAX=2.0`) vs. the production walk-forward bounds (0.5, 2.5). The lower bound expands below production (3-tick confirm gate behaviour at low values); the upper bound narrows (above ~2.0, System A is effectively disabled for normal sessions). A proposed value in `[0.3, 0.5)` falls outside the production walk-forward search space — treat such proposals as informational only.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `history_data` | `dict` | Per-symphony tick-history dict (same format as `run_autotuner`) |
| `current_params` | `dict` | Current live parameter dict for delta/frequency comparison |
| `current_date_str` | `str` | ISO-8601 date string for today |
| `deviation_dict` | `dict` | Execution-deviation penalties from `calculate_historical_deviation` |
| `random_state` | `int` | TPE sampler seed for reproducibility |
| `min_history_days` | `int` | History floor (days) below which a symphony is skipped (AC-4). Default: `_CALSWEEP_MIN_HISTORY_DAYS` = 125 — production-unchanged. Pass a lower value (e.g. 0) in test suites to exercise contracts on short fixtures (DE-CALSWEEP-002). |

**Returns:** `list[dict]` — one dict per (symphony, param_name) pair. Keys:
| Key | Type | Description |
|-----|------|-------------|
| `symphony_id` | `str` | Symphony identifier |
| `param_name` | `str` | `"PARABOLIC_VELOCITY_THRESHOLD"` or `"VWAP_CROSS_HWM_PCT"` |
| `current_value` | `float` | Current live value of the param |
| `proposed_value` | `float` | Best haircut-selected value (or naive winner if haircut found no cleared trial) |
| `delta_pct` | `float` | `(proposed - current) / abs(current) * 100` |
| `expected_trigger_freq_change` | `float` | Proposed minus current trigger count on validation fold |
| `frozen_eval_alpha` | `float \| None` | Sortino on the 20% frozen-eval fold with proposed params |
| `naive_sharpe` | `float \| None` | Optuna best-value (Sortino) before haircut selection |
| `selection_tstat` | `float \| None` | BHY haircut t-stat of the winner; `None` when no trial cleared the gate |
| `haircut_outcome` | `str` | `"cleared"`, `"no_trial_cleared"`, `"not_run"`, or `"no_completed_trials"` |
| `pbo_veto_status` | `bool` | True when haircut found no qualified winner (AC-5) |
| `flag_for_operator_review` | `bool` | True when proposed trigger frequency >2x current (AC-7) |
| `sortino` | `float \| None` | Sortino of best-params on validation fold |
| `n_trials` | `int` | Number of completed Optuna trials |
| `study_name` | `str` | `{timestamp}__{symphony_id}__calsweep` (AC-6) |
| `trading_day_start` | `str` | First date in the history window |
| `trading_day_end` | `str` | Last date before the frozen-eval boundary |
| `cycle_id` | `str` | UTC ISO-8601 timestamp identifying this sweep run |

**Overfitting controls (same methodology as production):**
- Harvey & Liu BHY haircut (`_haircut_select`) — multiplicity axis
- `compute_n_effective` wired via `lambda: []` (no prior-run ledger for the calibration context; NEFF-001 fix — ledger returns empty, so `n_eff == len(haircut_trials)`, byte-identical to pre-wiring behavior)
- `filter_sortino_sentinels` removes zero-downside and partial-sentinel trials before the haircut
- PBO veto status exposed but NOT applied as a hard veto here (the advisory report surfaces it; the operator decides)

---

### Sortino Objective (Legacy)

#### `run_simulation_sortino_legacy(params, history_data, acc_sym_ids, current_date_str, deviation_dict) → float`
Legacy objective retained under Option B. Returns the Sortino ratio on the validation-fold guard-alpha series.

#### `compute_sortino_ratio(returns: list, target: float = 0.0) → float`
Sortino ratio: `mean(r) / downside_deviation`. Population denominator. Returns 1e6 when downside_deviation is zero (sentinel); returns 0.0 for empty series.

---

### s_count Wiring (Workstream E, advisor-rewire cycle, 2026-07-12)

**The gap:** migration `023_autotune_runs_s_count.sql` added the `s_count` column to `autotune_runs` long before this cycle, but no caller ever populated it — every row's `s_count` stayed `NULL` forever. `advisors.overfitting_conscience`'s Indicator-3 (operator drift — comparing this run's `S` accumulation against PRIOR runs' `s_count`) requires `>= 2` prior runs with non-`NULL`, increasing `s_count` to fire; with every historical row `NULL`, `drift_signal_available` could structurally never become `True` on live data, no matter how much genuine researcher drift had occurred.

**The fix:** the DoF-ledger query (`SELECT evidence_source, n_configs_searched, ... FROM researcher_dof_ledger WHERE spec_bundle_id = ?`, run via `advisor_ro_query`) was hoisted from AFTER `save_autotune_run` (where it fed only the in-memory Overfitting Conscience call) to BEFORE it. The same query result is now used for BOTH: (a) `_s_count_for_persistence = sum(n_configs_searched for BACKTEST_SELECTION rows)`, passed as `save_autotune_run(s_count=...)`; (b) the pre-existing Overfitting Conscience call, unchanged. **This is a control-flow reorder only** — the current-run I-1/I-2 `S` computation and verdict logic in `overfitting_conscience.py` were NOT modified; they re-derive their own `S` from the same ledger rows independently of the persisted `s_count`.

**NULL tolerance (AC-E4):** legacy rows with `s_count IS NULL` (everything written before this cycle) are tolerated — `overfitting_conscience`'s prior-runs scan skips `NULL` entries rather than crashing; drift detection needs `>= 2` non-`NULL` priors, so it will not fire until enough post-fix runs accumulate, which is expected and correct (no retroactive backfill of historical rows).

### Advisor Invocations (Sprint 3)

Post-walk-forward, after `save_autotune_run` (see "s_count Wiring" above for the 2026-07-12 hoist that now feeds `s_count` into that same call):

1. **Overfitting Conscience** — `run_overfitting_conscience(autotune_run, ledger_rows, prior_runs=advisor_ro_query(...))`.
2. **Spec Critic** — `run_spec_critic(stored_hash, sc_facets_rows, symphony_id=None)`. Called once per bundle; `symphony_id=None` is intentional.
3. **Divergence Explainer** — `run_divergence_explainer(autotune_run, cvar_row=None)`.

All three are wrapped in `try/except logger.warning` — Advisor failures are non-fatal.

---

### Walk-Forward State Machine Helpers

#### `build_symphony_study_name(timestamp: str, symphony_id: str) → str`
Returns `"{timestamp}__{symphony_id}"`. Study names must never be reused.

#### `_fresh_replay_state() → dict`
Returns a fresh per-position transient-state dict. Used by all three simulation callers.

#### `_replay_grace_minutes() → int`
Returns `alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES`. Function-local import avoids circular import.

#### `_build_optuna_sampler_from_env() → optuna.samplers.TPESampler`
Returns `TPESampler` with seed sourced from `OPTUNA_SAMPLER_SEED` env var (None when unset; preserves non-deterministic behaviour for operators who have not opted in to reproducibility).

#### `_resolve_optuna_n_jobs_from_env() → int`
Returns n_jobs from `OPTUNA_N_JOBS` env var; falls back to 1 on unset or garbled. Default 1 (not cpu_count) because SQLite RDBStorage does not support parallel writes.

#### `calculate_historical_deviation(current_date_str: str) → dict`
Scans `post_mortem_*.json` files (last 45 days). Returns average execution-deviation penalties by exit reason.

## Types

### NN1 Freeze Discipline Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `FREEZE_DISCIPLINE_THEORY` | `"THEORY"` | Source: published theory |
| `FREEZE_DISCIPLINE_MANDATE` | `"MANDATE"` | Operator mandate |
| `FREEZE_DISCIPLINE_STYLIZED_FACT` | `"STYLIZED_FACT"` | Empirical codebase fact |
| `FREEZE_DISCIPLINE_POLITIS_WHITE` | `"POLITIS_WHITE"` | Politis-White bootstrap |
| `FREEZE_DISCIPLINE_CADENCE` | `"CADENCE"` | Scheduling convention |
| `FREEZE_DISCIPLINE_CALIBRATION` | `"CALIBRATION"` | Calibration result |
| `FREEZE_DISCIPLINE_BACKTEST_SELECTION` | `"BACKTEST_SELECTION"` | NN1 VIOLATION |
| `NN1_HONEST_DISCIPLINES` | frozenset | All disciplines that do NOT constitute an NN1 violation |

### Walk-Forward Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `TRAIN_RATIO` | 0.60 | 60% training fold |
| `VALIDATION_RATIO` | 0.20 | 20% validation fold (selection metric) |
| `FROZEN_EVAL_RATIO` | 0.20 | 20% frozen-eval fold (honest post-selection report) |
| `PURGE_DAYS` | 20 | Feature-lookback purge window |
| `EMBARGO_DAYS` | 1 | Post-fold embargo window |
| `HARVEY_LIU_FDR_Q` | 0.05 | BHY false-discovery rate level |
| `SORTINO_TARGET_RETURN` | 0.0 | Sortino denominator target (operator decision PA-5) |
| `RETURN_PCT_TO_FRACTION` | 100.0 | Percent-to-decimal unit conversion for CRRA |
| `OPTUNA_N_TRIALS_PRODUCTION` | 500 | Production walk-forward main study; 5x the 100-trial stability floor |
| `OPTUNA_N_TRIALS_CALIBRATION` | 100 | Calibration sweep; equals the statistical-stability floor exactly |
| `ACTIVE_OPTUNA_PRUNER_FAMILY` | `"NOP"` | Explicit NOP pruner — prevents silent MedianPruner activation |
| `_AUTOTUNE_REPLAY_N_JOBS` | 1 | Intraday-replay `n_jobs` override passed to `synthetic_history.generate_synthetic_history` on the autotune path. `n_jobs=1` uses joblib sequential backend (no fork), keeping peak RSS at 2.03 GiB on the 2-core / 3.0 GiB droplet (990 MB headroom, AC-1 empirical profile 2026-06-29, DE-AUTOTUNE-OOM). Other callers of `generate_synthetic_history` are untouched. |

### CPCV Constants (Phase 2)

| Constant | Value | Description |
|----------|-------|-------------|
| `_CPCV_N_GROUPS` | 6 | Number of contiguous date groups |
| `_CPCV_K_TEST_GROUPS` | 2 | Groups held out as test per split |
| `_CPCV_N_SPLITS` | 15 | `C(6,2)` total splits |
| `_CPCV_N_PATHS` | 5 | `φ[6,2] = (2/6)·15 = 5`. **Retained as documented combinatorial theory only (`DE-MATH-R2-001`); NOT consumed by any runtime scoring path as of R2** — split-level scoring (above) reads `_CPCV_N_SPLITS` exclusively. The pre-R2 sentinel-filter divisor (`_PARTIAL_SENTINEL_MEAN_THRESHOLD = math_engine._SORTINO_SENTINEL / _CPCV_N_SPLITS`, used by `filter_sortino_sentinels`) also moved off this constant onto `_CPCV_N_SPLITS`. |

### Calibration Sweep Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_CALSWEEP_MIN_HISTORY_DAYS` | 125 | AC-4: minimum days before running the sweep for a symphony |
| `_CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER` | 2.0 | AC-7: proposed/current trigger count ratio above which operator review is required |
| `_SS_VWAP_CROSS_HWM_V1_MIN` | 0.3 | V1 calibration lower bound — expands below production 0.5 (3-tick confirm gate) |
| `_SS_VWAP_CROSS_HWM_V1_MAX` | 2.0 | V1 calibration upper bound — narrows below production 2.5 (~2sigma reliability limit) |
| `_SS_PARA_VEL_MIN` | 1.0 | Shared with production walk-forward |
| `_SS_PARA_VEL_MAX` | 4.0 | Shared with production walk-forward |

### Optuna Search Space

`OPTUNA_SEARCH_SPACE_KEYS` = `{"TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT", "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS", "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE"}`

NN1-frozen facets that must NEVER appear in the search space: `gamma`, `utility_family`, `wealth_argument`, `generator_family`, `horizon_convention`, `lambda`.

The calibration sweep uses a SEPARATE, narrower 2-key space (`PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT`) — it does NOT modify `OPTUNA_SEARCH_SPACE_KEYS`.

`TRIGGER_THRESHOLD_PCT` is explicitly NOT a member of this set — it is a frozen, non-tuned default read via `p.get("TRIGGER_THRESHOLD_PCT", 15.0)` (`autotuner.py:1173`), never a `trial.suggest_*` call. `scripts/objective_variance_probe.py`'s `suggest_names_in_run_autotuner_objective()` (Math Remediation R3-a) confirms this by reading the real `trial.suggest_*` calls out of `run_autotuner`'s objective closure source directly — independent of, and matching, this constant.

**Math Remediation R1 note (`DE-MATH-R1-001`):** as of R1, `TAKE_PROFIT_MC_PCT` was no longer objective-inert in the replay (AC-1/AC-2/AC-7 fixed the day-constant MC degeneracy that made it unreachable); `PARABOLIC_VELOCITY_THRESHOLD`/`MAX_PARABOLIC_SQUEEZE` had their inertness CAUSE removed (see `_replay_exit_tick`'s AC-3/AC-5 fixes above), but a full walk-forward objective-variance demonstration for those two specifically was deferred to the R3 pre-retune checklist. **Math Remediation R3-a (`DE-MATH-R3A-001`, 2026-07-18) — walk-forward variance coverage extended to all 6 dims:** `scripts/objective_variance_probe.py` (see [scripts/objective_variance_probe](scripts_objective_variance_probe.md)) demonstrates non-zero walk-forward objective variance for every key in `OPTUNA_SEARCH_SPACE_KEYS`, including the 3 VWAP dims (never walk-forward-tested before this cycle — the pre-existing AC-7 fixture always set `vwap == close`), with a non-vacuity `force_inert` collapse control and config-robustness across `EXECUTION_START_TIME ∈ {09:30, 9:35}` (the droplet-production value the R3-d retune actually runs under). See `DE-MATH-R3A-001` in `DECISIONS.md` for the pre-retune checklist record (pending r3a-review's non-vacuity verdict at time of writing). **No retune ships on any of the six search-space keys until the full pre-retune checklist clears** — see `DE-MATH-R1-001` and `DE-MATH-R3A-001`.

## Internal Dependencies

- `math_engine` — all per-tick decision primitives, `WEALTH_ARG_FLOOR`, `_SORTINO_SENTINEL`, `compute_pbo`, `compute_crra_eu_objective`, `EXIT_CONFIRM_TICKS`, `apply_regime_exit_adjustment` (R1: regime-conditional confirm-tick resolution)
- `database` — `get_spec_bundle_by_id`, `save_autotune_run` (now called with `s_count=`, Workstream E), `advisor_ro_query`. **`get_cached_regime_label` is explicitly NEVER called from this module** — forbidden by ruling (`DE-MATH-R1-001` AC-4): it is a live, latest-wins single-row cache and would inject lookahead into the replay if consulted for a historical date.
- `regime_classifier` — `classify_regime`, `MIN_LABEL_SERIES_LENGTH` (R1: fresh per-day regime-label recomputation for the replay, no-lookahead; pre-existing module, newly wired here)
- `synthetic_history` — `generate_synthetic_history`, `_MC_REPLAY_SIMULATION_PATHS` (R1: the AC-6 parity battery shares this MC-path-count config rather than `math_engine`'s 5000-path default)
- `acceptance_gate` — reusable overfitting acceptance gate
- `advisors.overfitting_conscience` — `run_overfitting_conscience`
- `advisors.spec_critic` — `run_spec_critic`
- `advisors.divergence_explainer` — `run_divergence_explainer`
- `optuna` — TPE sampler, study management
