<!-- ARCHIVED from audit/math-engine-reaudit @ e246d08, original date 2026-05-27. Conclusion: PERF-003 (double vol call) open; PERF-005 closed by OPTUNA-6 fix per docs/audit/final-audit-2026-05-29/math-soundness.md. Performance optimization deferred to post-overhaul. -->
# AlphaBot v3 — Math Engine Re-Audit: Performance Findings

## Metadata
- Auditor: math-performance
- Run date: 2026-05-27
- Repo commit SHA: `8d38a434833a376d6adbcca07b42a53413ffda92`
- Branch: `audit/math-engine-reaudit`
- `git status -sb`: `## audit/math-engine-reaudit` (clean worktree)
- Scope: `math_engine.py`, `alpha_bot_execution.py`, `autotuner.py`, `synthetic_history.py`, `advisors/*.py`, `database.py` (advisor accessors + `advisor_ro_query`), `app.py` (`/api/advisor-observations`, `/api/state`)
- Tooling: static read + complexity analysis (no live profiler)

---

## Executive Summary

| Category | BLOCKER | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| PER-CYCLE I/O | 0 | 1 | 0 | 0 |
| PER-CYCLE CPU | 0 | 0 | 2 | 1 |
| AUTOTUNER | 0 | 0 | 1 | 2 |
| SYNTHETIC HISTORY | 0 | 0 | 0 | 1 |
| ADVISORS | 0 | 0 | 0 | 1 |
| API ROUTES | 0 | 0 | 0 | 1 |

**Top highest-risk findings:** PERF-003 (HIGH — double `calculate_20d_vol` call per symphony per action-phase cycle), PERF-001 (MEDIUM — O(N) rolling-vol loop in `run_monte_carlo` on full 3-year history).

**Confirmed blockers:** None.

**Known-debt items skipped:** None relevant in prior audits.

---

## Findings — PER-CYCLE I/O

### [PERF-003] Double `calculate_20d_vol` call per symphony per action-phase cycle

- **File:** `alpha_bot_execution.py:734` (data phase) and `alpha_bot_execution.py:1121` (action phase)
- **Confidence:** HIGH (grep-confirmed two distinct call sites per symphony per minute)
- **Severity:** HIGH
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  # data phase (~line 734)
  bot_state[s_id]["symphony_vol"] = math_engine.calculate_20d_vol(
      holdings_for_vol, data_phase_history
  )
  # action phase (~line 1121) — same holdings, same historical_data (cache hit same-day)
  symphony_vol = math_engine.calculate_20d_vol(holdings, historical_data)
  ```
- **Proposed Pattern:** Promote the data-phase result: read `symphony_vol` from `bot_state[symphony_id]` in the action phase rather than recomputing. Guard: only recompute if the data-phase value is absent (first-run sentinel).
- **Catalog / Rule:** Introduce Explaining Variable / Avoid Repeated Computation (Fowler: Inline Temp)
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS (golden-fixture tests for `calculate_20d_vol`)
- **Notes:** At 390 cycles/trading-day × N symphonies, this is a pure Python loop over a 20-day returns matrix built from a dict keyed on ~750 trading days twice. The action-phase call at line 1121 re-sorts `historical_data.keys()`, slices the last 20, and rebuilds the matrix — all O(D × T) where D = days (~750) and T = tickers. With the same-day history-cache guarantee the result is identical. The data-phase write to `bot_state` makes the cached value available without any architectural change.

---

## Findings — PER-CYCLE CPU

### [PERF-001] O(N) rolling-vol Python loop in `run_monte_carlo` on full 3-year history

- **File:** `math_engine.py:819-824`
- **Confidence:** MEDIUM (single hot-path, not multi-occurrence; complexity is clear)
- **Severity:** MEDIUM
- **Effort:** MEDIUM
- **Current Pattern:**
  ```python
  spy_vols = np.zeros_like(spy_returns)
  for i in range(len(spy_returns)):
      start_idx = max(0, i - (MC_VOL_WINDOW_DAYS - 1))
      if i > 0:
          spy_vols[i] = np.std(spy_returns[start_idx : i + 1])
      else:
          spy_vols[i] = 0.0
  ```
- **Proposed Pattern:** Replace with a vectorized rolling standard deviation using `pd.Series(spy_returns).rolling(MC_VOL_WINDOW_DAYS).std()` (or a manual sliding-window stdev via cumsum² trick on the numpy array). The loop calls `np.std` once per element — ~750 calls for a 3-year history, each over an expanding or fixed 20-element window. The first `MC_VOL_WINDOW_DAYS - 1` elements use an expanding (small) window so a naive fixed-window rolling std is not a drop-in; the pandas `min_periods=1` parameter covers that correctly.
- **Catalog / Rule:** Vectorize Loop (NumPy/Pandas idiomatic replacement of per-element Python loop)
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS (run_monte_carlo golden fixtures)
- **Notes:** This runs once per symphony per cycle (called at `alpha_bot_execution.py:1113`). At 5 symphonies × 750 iterations × np.std overhead, the contribution is measurable but not dominant. Becomes more relevant if symphony count grows.

### [PERF-002] `sorted(list(historical_data.keys()))` repeated inside per-symphony action-phase hot path

- **File:** `math_engine.py:797`, `math_engine.py:912`, `math_engine.py:947`, `math_engine.py:1225`
- **Confidence:** MEDIUM (3+ occurrences per cycle, all on the same `historical_data` dict)
- **Severity:** MEDIUM
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  valid_dates = sorted(list(historical_data.keys()))   # in run_monte_carlo
  valid_dates = sorted(list(historical_data.keys()))[-LOOKBACK_DAYS:]  # in calculate_20d_vol
  valid_dates = sorted(list(historical_data.keys()))[-ATR_LOOKBACK_DAYS:]  # in calculate_14d_atr_pct
  valid_dates = sorted(list(historical_data.keys()))  # in compute_portfolio_cvar
  ```
- **Proposed Pattern:** The caller (`alpha_bot_execution.py`) fetches `historical_data` once per cycle and passes it to all three functions. Pre-sort `valid_dates = sorted(historical_data.keys())` once at the call site and thread it through, or cache as a module-level side-effect-free helper `_sorted_dates(historical_data)` that memoizes on dict identity. The simplest fix is a single sort before the per-symphony loop in `alpha_bot_execution.py` action phase.
- **Catalog / Rule:** Hoist Invariant Out of Loop (equivalent to "loop-invariant code motion")
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS
- **Notes:** `sorted(list(d.keys()))` on a ~750-key dict is cheap (microseconds each), but is called 3–4 times per symphony per cycle. For 5 symphonies that is 15–20 redundant sorts per minute. Trivial effort, trivial gain — but consistent with the architecture constraint that nothing blocking may run on the 1-minute path.

### [PERF-004] `_reject_non_finite_in_records` iterates ALL of `historical_data` on every `run_monte_carlo` / `calculate_20d_vol` call

- **File:** `math_engine.py:784-786`, `math_engine.py:909-911`
- **Confidence:** LOW (single-pattern; the cost depends on dict size vs early-exit behavior)
- **Severity:** LOW
- **Effort:** MEDIUM
- **Current Pattern:**
  ```python
  for day_data in historical_data.values():
      for ticker_data in day_data.values():
          _reject_non_finite_in_records([ticker_data], "daily_ret")
  ```
- **Proposed Pattern:** These guards walk O(D × T) entries (~750 days × N tickers) on every call. On a 3-year history this is ~750 × 5 = 3750 dict lookups each cycle. The invariant being asserted — that `historical_data` contains no non-finite `daily_ret` — is already guaranteed at the Alpaca fetch boundary (fetch_alpaca_history only stores bars where `prev_close > 0`, and IEEE-754 divide on normal positive floats cannot produce NaN/Inf). Moving this validation to the fetch boundary (once, at write time) rather than the hot-path read sites would preserve the correctness guarantee with O(1) amortized cost per call. Tag: [NEEDS BEHAVIORAL VERIFICATION] — confirm the Alpaca fetch path cannot introduce non-finite values outside of the division-by-zero guard already present.
- **Catalog / Rule:** Move Assertions to System Boundary (defensive validation at I/O boundary, not on every pure-function call)
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS

---

## Findings — AUTOTUNER

### [PERF-005] `study.optimize(n_jobs=-1)` uses all available cores with SQLite `RDBStorage` — no contention analysis documented

- **File:** `autotuner.py:1507`
- **Confidence:** MEDIUM (single call site; observed pattern)
- **Severity:** MEDIUM
- **Effort:** LARGE
- **Current Pattern:**
  ```python
  study.optimize(objective, n_trials=500, n_jobs=-1)
  ```
- **Proposed Pattern:** With `RDBStorage` (SQLite backend at `optuna_studies.db`) and `n_jobs=-1`, Optuna spawns threads that each open their own SQLite connection and write trial results simultaneously. SQLite in WAL mode serializes concurrent writes, so at high thread counts the bottleneck shifts from trial computation to SQLite write contention, and the effective parallel speedup degrades. The project default Optuna trial floor is 100; 500 trials per symphony × N symphonies is the total load. Benchmarking the wall-clock time with `n_jobs=4` vs `n_jobs=-1` is the recommended diagnostic. This is not a blocking issue — the autotuner runs post-EOD, not on the 1-minute path — but is called out because the `engine_kwargs={"connect_args": {"timeout": 60}}` in the storage constructor (line 1504) already acknowledges write-lock contention.
- **Catalog / Rule:** Measure Before Optimizing (Knuth); SQLite WAL write serialization under concurrent threads
- **Prerequisites:** None
- **Test Coverage:** NO TESTS — CHARACTERIZATION TEST REQUIRED (wall-clock profiling benchmark)

### [PERF-006] `benjamini_hochberg_adjust` harmonic-number sum `c_n = sum(1/j for j in range(1, n+1))` is O(N) on every call with N=500 trials

- **File:** `autotuner.py:419`
- **Confidence:** LOW (single-occurrence; N=500 is well within acceptable Python loop range)
- **Severity:** LOW
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  c_n = sum(1.0 / j for j in range(1, n + 1))
  ```
- **Proposed Pattern:** For N=500, `sum(1/j for j in range(1, 501))` completes in microseconds. This is not a meaningful performance concern at current trial counts, but the harmonic number could be memoized or replaced with the well-known approximation `ln(N) + 0.5772` for large N if trial counts ever scale to tens of thousands.
- **Catalog / Rule:** Not a current performance concern; flagged LOW for completeness.
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS

### [PERF-007] `calculate_historical_deviation` uses `glob.glob("post_mortem_*.json")` with a CWD-relative path — sensitive to daemon working directory

- **File:** `autotuner.py:515`
- **Confidence:** LOW (behavioral correctness concern more than performance, but an unexpected glob miss produces a silent default deviation penalty with no I/O error)
- **Severity:** LOW
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  files = glob.glob("post_mortem_*.json")
  ```
- **Proposed Pattern:** Use an absolute path anchored to `analytics._POST_MORTEMS_DIR` (the directory where the action phase writes `post_mortem_*.json` files, e.g. `os.path.join(analytics._POST_MORTEMS_DIR, "post_mortem_*.json")`). A CWD mismatch causes a silent zero-file result and the function returns hardcoded defaults for all deviation penalties — an invisible config error that shifts the objective without any log indication.
- **Catalog / Rule:** Use Absolute Paths for File Lookups (operational robustness)
- **Prerequisites:** None
- **Test Coverage:** NO TESTS — CHARACTERIZATION TEST REQUIRED

---

## Findings — SYNTHETIC HISTORY

### [PERF-008] `_MC_REPLAY_SIMULATION_PATHS = 300` — documented tradeoff, confirmed acceptable

- **File:** `synthetic_history.py:220`
- **Confidence:** HIGH (constant is named and sourced)
- **Severity:** LOW (advisory — confirm remains deliberate)
- **Effort:** N/A
- **Current Pattern:**
  ```python
  _MC_REPLAY_SIMULATION_PATHS = 300
  ```
- **Proposed Pattern:** No change recommended. The constant is deliberately set lower than production's `MC_DEFAULT_SIMULATION_PATHS = 5000` for the replay path; the inline comment at line 216–219 documents the rationale ("300 paths is sufficient for the tuning approximation"). The comment correctly notes that `neighbor_k` must match production (`MC_DEFAULT_NEIGHBOR_K`) to preserve CDF resolution. This finding is a confirmatory check, not a defect.
- **Catalog / Rule:** N/A — deliberate tradeoff documented in source
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS

---

## Findings — ADVISORS

### [PERF-009] `run_overfitting_conscience` and `run_divergence_explainer` each execute one `advisor_ro_query` per symphony post-walk-forward — confirmed O(1) per symphony

- **File:** `autotuner.py:1753-1784`; `advisors/overfitting_conscience.py:196`; `advisors/divergence_explainer.py:175-183`
- **Confidence:** HIGH (code read; single parameterized SELECT per call)
- **Severity:** LOW (confirmatory — no N+1 pattern)
- **Effort:** N/A
- **Current Pattern (OC ledger query):**
  ```python
  _oc_ledger_rows = database.advisor_ro_query(
      "SELECT evidence_source, n_configs_searched, touched_frozen_eval, "
      "spec_bundle_id, facet_name FROM researcher_dof_ledger "
      "WHERE spec_bundle_id = ?",
      (stored_hash,),
  )
  ```
- **Current Pattern (OC prior_runs query):**
  ```python
  _oc_prior_raw = database.advisor_ro_query(
      "SELECT id, symphony_id, s_count FROM autotune_runs "
      "WHERE symphony_id = ? AND id != ? ORDER BY run_timestamp ASC",
      (normalized_name, _inserted_id),
  )
  ```
- **Proposed Pattern:** No change. Each advisor invocation issues exactly 1–2 parameterized SELECTs, all filtered by `spec_bundle_id` or `symphony_id`. No fan-out, no N+1. The `advisor_ro_query` wrapper enforces read-only access via the URI `?mode=ro` connection and the `COALESCE` fold-role guard. Confirmed compliant with architecture constraint #1 (no blocking I/O on the execution path) — these calls occur post-walk-forward on the EOD/Friday path only.
- **Catalog / Rule:** Confirmatory. Single SELECT per advisor per symphony = O(1) per call.
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS

---

## Findings — API ROUTES

### [PERF-010] `/api/advisor-observations?symphony_id=` route confirmed O(1) DB hits via single-query `get_advisor_observations_for_symphony`

- **File:** `app.py:2426-2455`; `database.py:911-932`
- **Confidence:** HIGH (single-query implementation confirmed by code read)
- **Severity:** LOW (confirmatory — S3-AUDIT-004/010 closure verified)
- **Effort:** N/A
- **Current Pattern:**
  ```python
  rows = database.get_advisor_observations_for_symphony(symphony_id)
  # ...
  cursor.execute(
      "SELECT ... FROM advisor_observations WHERE symphony_id = ? ORDER BY id ASC",
      (symphony_id,),
  )
  ```
- **Proposed Pattern:** No change. The route issues exactly one parameterized SELECT on the `symphony_id` column added by migration 025. S3-AUDIT-004 and S3-AUDIT-010 are closed. The legacy 3x subject_type fan-out is gone; the docstring at line 2428 records the closure correctly.
- **Catalog / Rule:** Confirmatory. O(1) DB hits per request.
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS

---

## /api/state Route — Pre-Sprint-3 Field Invariant

The `/api/state` route (`app.py:701`) was reviewed for Sprint-3 regressions. Confirmed:

- The three additive Sprint-3 fields (`port_state`, `exit_authority`, `daemon_started_at`) are merged into the response at `app.py:711-716` alongside `live_mode` — all additive, no existing field renamed or removed.
- `portfolio_strip` is computed once from `shadow_history` / `chart_archive` fallback; no new DB fan-out introduced by Sprint 3.
- The `get_api_state_dict()` function at line 647 calls `database.load_state()` once and `database.get_ro_connection()` once for the lock query. No additional reads added by Sprint 3 on this path.

**Finding:** No performance regression on `/api/state` from Sprint-3 changes. Unchanged from pre-Sprint-3 baseline.

---

## Advisor Producers — Per-Cycle Path Verification

**Critical architecture constraint #1:** "Engine runs 1-minute cadence during market hours — no blocking I/O on the execution path."

All three Sprint-3 advisor producers (`overfitting_conscience`, `spec_critic`, `divergence_explainer`) are called exclusively from `autotuner.run_autotuner` (lines 1777, 1331, 1784), which is itself called only from the EOD/Friday path in `alpha_bot_execution.py:960`:

```python
autotuner_changes = autotuner.run_autotuner(...)  # alpha_bot_execution.py:960
```

This line is reached only when `current_et.weekday() >= 4 or force_run` AND after the EOD post-mortem block (which itself gates on `market_close <= current_time <= post_mortem_cutoff`). The advisor producers are **not on the 1-minute per-cycle action path**. Architecture constraint #1 is satisfied.

The Spec Critic is also called once per bundle (not per symphony) at `autotuner.py:1331` before the per-symphony loop — this is the pre-loop advisory check, confirmed advisory-only with exception swallowing at line 1332-1333.

---

## Optuna Sampler / Pruner / Trial Floor

- **Sampler:** Default (TPE). Not explicitly overridden in `create_study` call at `autotuner.py:1506`. TPE is the correct default for this search space (6 continuous/integer parameters); no evidence of a performance-degrading sampler choice.
- **Pruner:** Default (NopPruner). No pruning is configured — intermediate trial values are not reported (the objective returns a scalar at the end of the simulation, not incremental). This is correct — MedianPruner would require intermediate reporting and would not apply to this objective structure.
- **Trial floor:** `n_trials=500` at line 1507, consistent with the project CLAUDE.md "Default Optuna trial floor: 100 trials". The production value of 500 exceeds the floor. No under-floor issue.
- **Study persistence:** `RDBStorage` with `optuna_studies.db` (SQLite). Unique timestamped study names (`{study_timestamp}__{normalized_name}`) prevent reuse. Each run creates a new study — no carry-over of stale trial history across runs. Correct.
- **Reproducibility:** The objective uses `_collect_sim_returns` which calls `_replay_exit_tick` which calls `math_engine.run_monte_carlo` with a per-(symphony, date) deterministic seed. Optuna's TPE sampler is not seeded (no `sampler=optuna.samplers.TPESampler(seed=...)` in `create_study`). TPE output is therefore non-reproducible across runs — this is a known and acceptable tradeoff for production (the BHY haircut selects on p-values, not raw trial order). No performance implication.

---

## MC Sufficiency Boundary — Confirmed

`run_monte_carlo` gates on `eligible_days >= MC_MIN_HISTORY_DAYS (20)` where `eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)`. The minimum raw history required is `MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) = 20 + 19 = 39`. The memory `project_mc_eligible_pool_vs_raw_day_boundary.md` records this as 39 raw days, confirmed consistent with the source at `math_engine.py:805`. No regression.

---

## Patterns Observed

1. **Defensive NaN-rejection overhead:** `_reject_non_finite_in_records` walks entire `historical_data` on every per-cycle hot-path call. The validation is correct but occurs at the reader rather than the writer; see PERF-004.
2. **Repeated sorting of the same historical data key set:** `sorted(list(historical_data.keys()))` appears 4+ times per cycle across `run_monte_carlo`, `calculate_20d_vol`, `calculate_14d_atr_pct`, `compute_portfolio_cvar`. See PERF-002.
3. **Double vol computation per symphony per cycle:** `calculate_20d_vol` is called twice per symphony — once in the data phase and once in the action phase — on the same historical data. See PERF-003 (highest-priority non-BLOCKER finding).
4. **Advisor producers cleanly isolated to post-EOD path:** No Sprint-3 advisor call is reachable from the 1-minute action path. Architecture constraint #1 intact.
5. **kNN-MC path is well-vectorized:** `run_monte_carlo` uses numpy vectorized distance computation (`np.sqrt`, broadcasting), `np.argpartition` for O(K) partial sort, and `rng.choice` for the bootstrap. The only remaining pure-Python loop is the O(N) rolling-vol computation (PERF-001).

---

## Risk Summary

| Category | Severity | Count |
|---|---|---|
| PER-CYCLE I/O | HIGH | 1 |
| PER-CYCLE CPU | MEDIUM | 2 |
| PER-CYCLE CPU | LOW | 1 |
| AUTOTUNER | MEDIUM | 1 |
| AUTOTUNER | LOW | 2 |
| SYNTHETIC HISTORY | LOW | 1 |
| ADVISORS | LOW | 1 |
| API | LOW | 1 |

**Highest-risk:** PERF-003 (HIGH) — double vol computation on the action-phase hot path. PERF-001 (MEDIUM) — O(N) rolling-vol Python loop in MC. PERF-005 (MEDIUM) — SQLite WAL contention under `n_jobs=-1` autotuner (non-blocking path, acceptable risk).

---

## Recommendations Index

(Ordered by execution priority; all non-blocking on the autotuner path)

1. **PERF-003** — Eliminate double `calculate_20d_vol` call: read `bot_state[symphony_id]["symphony_vol"]` in the action phase rather than recomputing. (HIGH; SMALL effort)
2. **PERF-002** — Hoist `sorted(historical_data.keys())` out of per-function, per-call site. (MEDIUM; TRIVIAL effort)
3. **PERF-001** — Vectorize the rolling-vol loop in `run_monte_carlo` with `pd.Series.rolling`. (MEDIUM; MEDIUM effort; prerequisites: none, but requires fixture re-verification)
4. **PERF-004** — Move `_reject_non_finite_in_records` historical-data scan to fetch boundary. [NEEDS BEHAVIORAL VERIFICATION] (LOW; MEDIUM effort)
5. **PERF-005** — Benchmark `n_jobs=-1` vs a fixed thread count for the Optuna study under `RDBStorage`. (MEDIUM; LARGE effort; post-EOD path only)
6. **PERF-007** — Use absolute path in `calculate_historical_deviation` glob. (LOW; SMALL effort)

---

## Open Questions

- [QUESTION-01] **PERF-004 boundary verification:** Can the Alpaca daily-bar fetch path (`fetch_alpaca_history` / `synthetic_history.fetch_bars`) introduce non-finite `daily_ret` values outside the `prev_close > 0` guard? The `(curr_close - prev_close) / prev_close` arithmetic is safe when `prev_close > 0`, but Alpaca split-adjusted bars could theoretically return zero `c` for a de-listed symbol. If the answer is "no non-finite is possible", the validation scan in `_reject_non_finite_in_records` can be safely demoted to the fetch boundary. — **Non-blocking**

---

## Evidence Appendix

All findings are sourced from direct code reads of the audit worktree at SHA `8d38a434833a376d6adbcca07b42a53413ffda92`. No automated profiler output is available (read-only audit). Complexity assessments are based on static loop structure and data-size estimates derived from constants in source:

- `MC_VOL_WINDOW_DAYS = 20`, `MC_MIN_HISTORY_DAYS = 20`, raw-day minimum 39 (`math_engine.py:85-88`)
- `_WALK_FORWARD_TRADING_DAYS = 125`, `_MC_WARMUP_TRADING_DAYS = 39` (`synthetic_history.py:32-40`)
- `MC_DEFAULT_SIMULATION_PATHS = 5000`, `MC_DEFAULT_NEIGHBOR_K = 150` (`math_engine.py:89-91`)
- `_MC_REPLAY_SIMULATION_PATHS = 300` (`synthetic_history.py:220`)
- `n_trials=500` per symphony (`autotuner.py:1507`)
- 3-year Alpaca history ≈ 756 trading days raw keys (`alpha_bot_execution.py:265` — `timedelta(days=365*3+30)`)

File:line citation index:
- `math_engine.py:784-786` — PERF-004 (run_monte_carlo historical_data scan)
- `math_engine.py:797` — PERF-002 (sorted keys in run_monte_carlo)
- `math_engine.py:819-824` — PERF-001 (rolling-vol Python loop)
- `math_engine.py:909-911` — PERF-004 (calculate_20d_vol historical_data scan)
- `math_engine.py:912` — PERF-002 (sorted keys in calculate_20d_vol)
- `math_engine.py:947` — PERF-002 (sorted keys in calculate_14d_atr_pct)
- `math_engine.py:1225` — PERF-002 (sorted keys in compute_portfolio_cvar)
- `alpha_bot_execution.py:734` — PERF-003 (data-phase vol call)
- `alpha_bot_execution.py:1121` — PERF-003 (action-phase vol call, duplicates data-phase result)
- `autotuner.py:419` — PERF-006 (harmonic number sum)
- `autotuner.py:515` — PERF-007 (CWD-relative glob)
- `autotuner.py:1507` — PERF-005 (n_jobs=-1 with RDBStorage)
- `autotuner.py:1753-1784` — PERF-009 (advisor query sites)
- `app.py:2426-2455` — PERF-010 (api/advisor-observations route)
- `database.py:911-932` — PERF-010 (get_advisor_observations_for_symphony)
- `database.py:1541-1578` — PERF-009 (advisor_ro_query implementation)
- `synthetic_history.py:220` — PERF-008 (replay MC path count)
