# AlphaBot v3 — Sprint 2 Cross-Cycle Audit Report

## Metadata

- **Auditor:** code-auditor (Sonnet 4.6)
- **Run date:** 2026-05-26T00:00:00Z
- **Repo + commit SHA:** `88198674e4afe2b7001e2646bddcdb4d47dc0325` (quoted from `git rev-parse HEAD`)
- **Branch:** `audit/sprint-2-cross-cycle`
- **Git status:** clean (no modified/untracked files on audit branch)
- **Audit range:** `d2328ca..8819867` (Sprint 2 delta)
- **Scope:** `autotuner.py`, `math_engine.py`, `database.py`, `alpha_bot_execution.py`, `app.py`, `migrations/020–024`, `templates/index.html`, selected test suites
- **Tooling:** static grep/AST analysis; no external linter invoked (ruff/mypy not configured in worktree)
- **Known-debt skipped:** CC-002 (SQL injection — FIXED sprint 2), CC-003 (executor atexit — FIXED sprint 2), CC-005 (flush_resync silent break — FIXED sprint 2)

---

## Executive Summary

| Severity  | Count |
|-----------|-------|
| CRITICAL  | 2     |
| HIGH      | 3     |
| MEDIUM    | 4     |
| LOW       | 3     |
| **Total** | **12**|

**Top findings by risk:**

1. **[CRRA-001] CRITICAL** — `_haircut_select` feeds raw percent returns to `compute_crra_eu_tstat`, which expects utility values. The CRRA-EU haircut computes the Sharpe t-stat rather than the CRRA-EU t-stat, silently defeating the H-6 correction.
2. **[NEFF-001] CRITICAL** — `compute_n_effective` is never called in any production path; `_haircut_select` is called without `n_effective` at both production sites. The n-effective additive accounting feature is wired to tests only — dead in production.
3. **[ARCH-001] HIGH** — `save_autotune_run` has not been extended with the nine EUT columns from migration 020 (`spec_bundle_id`, `n_effective`, `s_count`, `d_spec`, `gamma`, `overfitting_verdict`, etc.). Every autotune_run row leaves these columns NULL forever.

---

## §1 Cross-Cycle Interactions

### [CC-NEW-001] HIGH — `flush_resync` background write races the engine's per-minute `save_state`

- **File:** `app.py:2127–2143`
- **Confidence:** HIGH
- **Risk:** RUNTIME (silent state clobber on SQLite)
- **Effort:** MEDIUM
- **Current Pattern:**
  ```python
  def _flush_state_async():
      state = database.load_state()
      ...strip fields...
      database.save_state(state)  # ← no serialization vs engine writes
  _DISMISS_EXECUTOR.submit(_flush_state_async)
  ```
- **Proposed Pattern:** Serialize through the same single-worker executor already used for dismiss writes, or accept the race and document it as a known operator-must-not-trigger-during-market-hours restriction.
- **Catalog / Rule:** Classic lost-update concurrent read-modify-write; Fowler "Introduce Serialization" pattern.
- **Prerequisites:** None
- **Test Coverage:** `tests/app/test_flush_resync.py` tests the happy path and background dispatch, but no test verifies concurrent behavior against a racing engine write.

The `_flush_state_async` closure calls `load_state()` then `save_state()` in a background thread. `alpha_bot_execution.py` calls `database.save_state(bot_state)` at lines 468, 594, 664, 807, and 951 — up to 5 times per minute during market hours. SQLite serializes individual writes (WAL mode), but the load-then-save round-trip in the flush thread is not atomic: the engine can write between the flush thread's `load_state` and its `save_state`, and the flush thread's `save_state` will overwrite the engine's per-minute update with stale data.

Sprint 1 CC-005 fixed the "no write ever fires" bug. This finding is the new cross-cycle interaction: the write now fires but is not serialized against the engine.

---

## §2 Architectural Drift

### [ARCH-001] HIGH — `save_autotune_run` never writes EUT audit columns; migration 020 columns permanently NULL

- **File:** `database.py:407–459`, `autotuner.py:1698–1710`
- **Confidence:** HIGH
- **Risk:** CORRECTNESS (audit trail permanently absent)
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  database.save_autotune_run(
      run_timestamp=run_timestamp,
      ...
      frozen_eval_sharpe=frozen_eval_sharpe_value,
      # spec_bundle_id, n_effective, s_count, d_spec, gamma, overfitting_verdict
      # are NOT passed — these parameters do not exist on save_autotune_run
  )
  ```
- **Proposed Pattern:** Extend `save_autotune_run` signature with the nine EUT columns (all nullable); pass the values computed during `run_autotuner` (gamma from `_gamma`, spec_bundle_id from the argument, n_effective from `compute_n_effective` after the haircut). Add overfitting_verdict string assembly.
- **Catalog / Rule:** Diverging Class Hierarchy (Fowler); migration 020 schema and the write path evolved independently.
- **Prerequisites:** NEFF-001 (n_effective must be computed before it can be persisted)
- **Test Coverage:** `tests/autotuner/test_n_effective_additive_accounting.py` tests verify schema columns exist (D5 contract) and specify the verdict string format, but no test verifies that `save_autotune_run` actually persists any EUT values — the gap is untested.

### [ARCH-002] MEDIUM — `_MIGRATION_FILES` ordering: migration 021 listed before 020 (non-numeric)

- **File:** `database.py:897–901`
- **Confidence:** HIGH
- **Risk:** STYLE (order diverges from numeric sequence; future reader confusion)
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  "021_cvar_diagnostics.sql",
  "020_autotune_runs_eut.sql",
  ```
- **Proposed Pattern:** The out-of-order placement is intentional (defect-37 restoration hotfix — 021 was already applied to production DBs when 020 was restored). Comment the rationale inline next to the out-of-order pair: "021 before 020: 021 was applied to production before 020 was accidentally dropped (defect-37); reordering would attempt to re-apply 021 on live DBs, causing a duplicate-column error." The migration is functionally correct (independent tables); this is documentation debt.
- **Catalog / Rule:** Comment What, Not How (no identifier needed); documentation drift.
- **Prerequisites:** None
- **Test Coverage:** NO TESTS — no test asserts the rationale comment exists.

---

## §3 Type-Design Smells

### [TYPE-001] LOW — `run_autotuner` parameter `spec_bundle_id` is untyped (no annotation)

- **File:** `autotuner.py:1280`
- **Confidence:** MEDIUM
- **Risk:** TYPING
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  def run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False, spec_bundle_id=None):
  ```
- **Proposed Pattern:** Annotate `spec_bundle_id: int` (required after Phase-1 strict-mode wiring; the None default only survives long enough to raise the descriptive error).
- **Catalog / Rule:** Missing type annotation; mypy would flag as `Any`.
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS (nn1_spec_freeze tests call with integer id)

### [TYPE-002] MEDIUM — `compute_n_effective` `winning_spec_bundle_id` parameter is `str | None` but callers pass integer bundle ids

- **File:** `autotuner.py:519`
- **Confidence:** HIGH (confirmed by cross-referencing call sites and DB schema)
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  def compute_n_effective(
      n_optuna: int,
      ledger_query,
      winning_spec_bundle_id: "str | None" = None,
  ) -> int:
  ```
  The `researcher_dof_ledger.spec_bundle_id` column is `TEXT` (the 64-char `bundle_hash`), but `validate_nn1_compliance` inserts ledger rows with `spec_bundle_id=bundle_hash` (a string). Meanwhile, `run_autotuner` holds `spec_bundle_id` as an integer (the migration 022 `id` column). The type annotation correctly says `str | None` for the hash-based ledger key, but a caller passing an integer id would produce no exclusions (string "42" != integer 42).
- **Proposed Pattern:** The winning bundle exclusion must compare hashes, not ids. Clarify which key type is used throughout the n-effective path (all hash or all id); add a runtime type-check assert in `compute_n_effective` to catch callers passing wrong type.
- **Catalog / Rule:** Primitive Obsession (Fowler) — two different integer-vs-hash concepts both called `spec_bundle_id`.
- **Prerequisites:** None
- **Test Coverage:** `test_n_effective_additive_accounting.py` tests use dict rows with string keys — does not exercise the type mismatch between the function signature and integer autotuner ids.

---

## §4 Naming Hygiene

### [NAME-001] LOW — `run_simulation_sortino_legacy` embeds change-history words "sortino" and "legacy"

- **File:** `autotuner.py:1068`
- **Confidence:** HIGH (violates `feedback_names_describe_behavior_not_change_history` project rule)
- **Risk:** STYLE
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  run_simulation_sortino_legacy = run_simulation
  ```
- **Proposed Pattern:** The function computes a loss-averse Sortino guard-alpha objective; a behavior-describing name might be `run_simulation_sortino_guard_alpha` or simply retain `run_simulation` (it is already the canonical entry point). "legacy" and "sortino" in the alias both signal historical context rather than behavior.
- **Catalog / Rule:** `feedback_names_describe_behavior_not_change_history` (project memory); Fowler "Rename Function".
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS (T6 callable tests reference the alias name explicitly; rename requires test update)

### [NAME-002] LOW — `RUN_SIM_*_PCT` local alias names add `_PCT` suffix inconsistently

- **File:** `autotuner.py:989–993`
- **Confidence:** MEDIUM
- **Risk:** STYLE
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  RUN_SIM_MISSED_UPSIDE_THRESHOLD_PCT = SORTINO_OBJ_MISSED_UPSIDE_THRESHOLD
  RUN_SIM_DRAWDOWN_THRESHOLD_PCT = SORTINO_OBJ_DRAWDOWN_THRESHOLD
  RUN_SIM_DRAWDOWN_MIN_GAIN_PCT = SORTINO_OBJ_DRAWDOWN_MIN_GAIN
  ```
  The module-level names (`SORTINO_OBJ_MISSED_UPSIDE_THRESHOLD` etc.) have no `_PCT` suffix, but the local aliases do. A reader must check both names to be sure they denote the same unit.
- **Proposed Pattern:** Mirror the module-level naming exactly in the local aliases (drop `_PCT` suffix), or consistently add `_PCT` to both the module-level constants and the aliases.
- **Catalog / Rule:** Inconsistent Naming (no Fowler catalog entry; ESLint `id-match` style rule class).
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS (T6 contract tests verify the constants exist and the alias maps them)

---

## §5 Test-Quality Smells

### [TEST-001] MEDIUM — No test verifies that `_haircut_select` receives U-values (not percent returns) for the CRRA-EU path

- **File:** `tests/autotuner/test_crra_eu_tstat_formula_pin.py` (absence)
- **Confidence:** HIGH — the misuse is confirmed by static analysis (see CRRA-001); the absence of a test covering this specific cross-function contract is a test-quality gap.
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:** `test_crra_eu_tstat_formula_pin.py` tests `compute_crra_eu_tstat(U_series)` in isolation with correct U-values. No test exercises the full path from `trial.set_user_attr("daily_returns", raw_pct_returns)` through `_haircut_select(..., tstat_fn=compute_crra_eu_tstat)`.
- **Proposed Pattern:** A characterization test should construct fake trials with known `daily_returns` (percent), call `_haircut_select` with `tstat_fn=compute_crra_eu_tstat`, and assert the t-statistic matches `compute_crra_eu_tstat([U_transform(r) for r in daily_returns])`, not `compute_crra_eu_tstat(daily_returns)`.
- **Catalog / Rule:** Missing integration contract test; Feathers "Characterization Test" pattern.
- **Prerequisites:** CRRA-001 fix (once the production bug is corrected, this test must pass against the fixed code)
- **Test Coverage:** NO TESTS — CHARACTERIZATION TEST REQUIRED

---

## §6 Provenance Gaps

### [PROV-001] LOW — `council-converged-migration-plan.md` migration numbers do not match actual files

- **File:** `docs/handoff/council-converged-migration-plan.md:42`
- **Confidence:** HIGH (grep-verified)
- **Risk:** STYLE (documentation drift)
- **Effort:** TRIVIAL
- **Current Pattern:** The plan document references `020_researcher_dof_ledger.sql` at migration row 020, but the actual file is `018_researcher_dof_ledger.sql`. Similarly, the plan's row 022 matches `022_spec_bundles_add_id.sql` in naming but the EUT columns are in `020_autotune_runs_eut.sql`.
- **Proposed Pattern:** Update the migration-plan table to reflect the actual file numbering (018, 019, 020, 021, 022, 023, 024). This is a pre-council snapshot that was not updated after migration renumbering.
- **Catalog / Rule:** Documentation Drift; no Fowler catalog entry.
- **Prerequisites:** None
- **Test Coverage:** NO TESTS — documentation-only finding

---

## §7 Latent Breakage

### [LATENT-001] MEDIUM — `flush_resync` log message references captured-before `symphonies_reset` but `len(symphonies_reset)` reflects the count from the request-thread read, not the background write

- **File:** `app.py:2139`
- **Confidence:** HIGH
- **Risk:** CORRECTNESS (misleading log)
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  _daemon_log.info("flush_resync: background reset wrote %d symphony state entries", len(symphonies_reset))
  ```
  `symphonies_reset` is populated in the request thread (lines 2117–2122), but the background write (`_flush_state_async`) iterates its own local `state` (re-loaded from DB). The log message claims N entries were written, but N reflects the request-thread's enumeration. If the background load sees a different state (e.g., engine wrote between the two loads), the count could differ.
- **Proposed Pattern:** Compute `len()` from the background-thread's local iteration, not the captured closure variable.
- **Catalog / Rule:** Fowler "Remove Assignments to Parameters" family; closure capture of mutable state anti-pattern.
- **Prerequisites:** CC-NEW-001 (the race makes the captured value unreliable)
- **Test Coverage:** NO TESTS — CHARACTERIZATION TEST REQUIRED

---

## §8 Documentation Drift

(See PROV-001 in §6 for the migration-plan drift.)

---

## §9 NN1 Spec-Freeze Integrity

The structural enforcement gates are correct:

- `validate_search_space_nn1()` runs before `optuna.create_study` — verified at `autotuner.py:1309`.
- `NN1_HONEST_DISCIPLINES` and `database._VALID_FREEZE_DISCIPLINES` are aligned (both contain exactly `{THEORY, MANDATE, STYLIZED_FACT, POLITIS_WHITE, CADENCE, CALIBRATION}` plus BACKTEST_SELECTION in the DB set). No drift.
- The canonical Phase-1 bundle uses `freeze_discipline='THEORY'` for all three facets — no BACKTEST_SELECTION facet can enter via the canonical path.
- `insert_spec_bundle_facet` validates against `_VALID_FREEZE_DISCIPLINES` before any INSERT — gate is enforced at the DB write layer.

No NN1 spec-freeze integrity issues found.

---

## §10 CRRA-EU Math Integrity

### [CRRA-001] CRITICAL — `_haircut_select` passes raw percent returns to `compute_crra_eu_tstat`, which expects utility values

- **File:** `autotuner.py:942–948`
- **Confidence:** HIGH
- **Risk:** CORRECTNESS (silent Sharpe-like selection replacing CRRA-EU selection in the haircut)
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  series = t.user_attrs.get("daily_returns", []) if hasattr(t, "user_attrs") else []
  if tstat_fn is compute_sortino_tstat:
      tstats.append(compute_sortino_tstat(t.value, len(series)))
  else:
      # CRRA-EU (or any custom) branch: t-stat from the returns series directly
      tstats.append(tstat_fn(series))  # ← series is raw percent returns, NOT U-values
  ```
  The `daily_returns` attribute stores raw guard-alpha percent returns (e.g. `[1.5, -0.3, 2.1, ...]`), as documented at `autotuner.py:1487–1491`. `compute_crra_eu_tstat` expects `U_series: list[float]` — utility values after the `max(WEALTH_ARG_FLOOR, 1 + r/100) ^ (1-gamma)` transformation.

  The docstring at `autotuner.py:1083–1084` states: "The haircut re-transforms daily_returns through `derive_floored_wealth_argument` + `compute_crra_utility`" — this documented contract is not implemented.

  Effect: `compute_crra_eu_tstat(r_pct_series)` computes `mean(r_pct) / (sd(r_pct) / sqrt(T))`, which is the Sharpe ratio times sqrt(T) — the H-6 category error explicitly warned about in the function docstring. The H-6 fix is not applied in the haircut path.

  Numerical impact: for small-variance near-linear regimes, the rankings may coincide (empirically confirmed at ~1.03x ratio for common distributions). For high-variance or skewed distributions (catastrophic loss days), CRRA penalizes losses nonlinearly and `mean(U)` diverges from `mean(r_pct)`.
- **Proposed Pattern:** Before calling `tstat_fn(series)` in the CRRA-EU branch, re-transform: `u_series = [compute_crra_eu_objective([r / RETURN_PCT_TO_FRACTION], _gamma) is wrong — need per-value: W = max(WEALTH_ARG_FLOOR, 1 + r/RETURN_PCT_TO_FRACTION); U = compute_crra_utility(W, gamma)]`. The `derive_floored_wealth_argument` function in autotuner already encapsulates this; use it.
- **Catalog / Rule:** H-6 category error (project-internal audit rule); Fowler "Replace Primitive with Object" — the `daily_returns` user-attr stores percent returns but is used as if it stores utility values at the haircut call site.
- **Prerequisites:** None
- **Test Coverage:** NO TESTS — CHARACTERIZATION TEST REQUIRED (no test exercises `_haircut_select` with `tstat_fn=compute_crra_eu_tstat` and verifies the U-transformation is applied)

**W-H4 floor verification (passing):** `compute_crra_eu_objective` at `math_engine.py:1402` correctly applies `W = max(WEALTH_ARG_FLOOR, 1.0 + r)` before `compute_crra_utility`. The floor is on INPUT W, never on output U. This is correct.

**`statistics.stdev` (ddof=1) verification (passing):** `compute_crra_eu_tstat` at `autotuner.py:424` uses `statistics.stdev` which is sample standard deviation (ddof=1, Bessel-corrected). Correct.

---

## §11 CVaR Display Contract (S-3)

The S-3 four-part display contract at `templates/index.html:1257–1303` is correctly implemented:
- (a) stderr — `data-cvar-stderr` attribute + display (`±N stderr` or `—`)
- (b) tail_obs_count — `data-cvar-n-tail` attribute + `n=N`
- (c) diagnostic label — "diagnostic, not a signal — do not trade on this"
- (d) bias warning — "known-low-biased LOWER BOUND on tail severity, not a point estimate"

**Multi-symphony gap (MEDIUM):** The dashboard reads CVaR only for the FIRST symphony (`_first_sym_id = next(...)` at `app.py:387–390`). For multi-symphony portfolios, other symphonies' CVaR diagnostics are silently omitted. This is an undocumented Phase-1 limitation.

### [CVAR-001] MEDIUM — CVaR dashboard displays only first symphony; other symphonies' diagnostics silently absent

- **File:** `app.py:387–390`
- **Confidence:** HIGH
- **Risk:** CORRECTNESS (silent UI degradation for multi-symphony portfolios)
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  _first_sym_id = next(
      (k for k, v in bot_state.items() if isinstance(v, dict) and "name" in v),
      None,
  )
  ```
- **Proposed Pattern:** Either pass a dict keyed by symphony to the template and render a per-symphony row, or document "Phase-1: single-symphony display only" in a visible comment + the template `{% comment %}` block so a future reader knows this is a deliberate scope limitation.
- **Catalog / Rule:** Silent degradation (architecture constraint 2: dashboard is a read-only observer that must reflect state faithfully).
- **Prerequisites:** None
- **Test Coverage:** NO TESTS — no test asserts that multi-symphony state causes multi-row CVaR display (or explicitly asserts first-only limitation).

---

## §12 Phase-1 Strict-Mode Behavior End-to-End

The two live call sites are correctly wired:
- `alpha_bot_execution.py:967`: `spec_bundle_id=database.get_or_create_phase1_theory_bundle_id()`
- `app.py:1583`: `spec_bundle_id=database.get_or_create_phase1_theory_bundle_id()`

`get_or_create_phase1_theory_bundle_id` is idempotent: `INSERT OR IGNORE` + process-local cache. The system starts cleanly. No finding.

---

## §13 _MIGRATION_FILES Integrity

All five Sprint 2 migrations (020–024) are present in `_MIGRATION_FILES`. No migration is silently absent. No destructive DDL (no DROP TABLE, no DROP COLUMN, no NOT NULL addition to a populated table). The out-of-order 021/020 placement is covered in ARCH-002.

**Verified clean:** All five SQL files exist on disk, all five are in the list, all use additive DDL (ALTER TABLE ADD COLUMN DEFAULT NULL, CREATE TABLE IF NOT EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS). No integrity issues beyond ARCH-002.

---

## §14 Port-Level Decision-Math

Sprint 2 added zero new port-level references to `autotuner.py` (confirmed by diff grep for "port"). The only port reference in the Sprint 2 autotuner delta is `validate_port_mode_params_available` which pre-existed. No new port dependencies introduced. Sprint 3 deprecation is not complicated by Sprint 2 additions.

---

## §N — n-effective Additive Accounting (Cross-Cutting)

### [NEFF-001] CRITICAL — `compute_n_effective` is never called in any production code path; haircut runs with `n_effective=None` always

- **File:** `autotuner.py:1558`, `autotuner.py:1838`
- **Confidence:** HIGH (confirmed by exhaustive grep: `compute_n_effective` has zero calls outside test files)
- **Risk:** CORRECTNESS (N_effective accounting is declared as a structural NN1 enforcement but is dead code in production)
- **Effort:** MEDIUM
- **Current Pattern:**
  ```python
  # autotuner.py:1558 (CRRA-EU branch)
  winner_trial, winner_p_adj, winner_tstat = _haircut_select(haircut_trials, tstat_fn=_tstat_fn)
  # autotuner.py:1838 (Sortino branch)
  winner_trial, winner_p_adj, winner_tstat = _haircut_select(haircut_trials)
  ```
  Both production calls omit `n_effective`. The `_haircut_select` function defaults `n_effective=None` which falls back to `len(completed_trials)`, making the BHY haircut behave identically to the pre-n-effective implementation. `compute_n_effective` exists and is tested but is never wired into the execution path.
- **Proposed Pattern:** After the haircut_trials filter and before `_haircut_select`, call `compute_n_effective(n_optuna=len(haircut_trials), ledger_query=lambda: database.get_researcher_dof_ledger_for_run(...))` and pass the result to `_haircut_select(haircut_trials, n_effective=n_eff, tstat_fn=_tstat_fn)`. Then persist the value via `save_autotune_run` (see ARCH-001).
- **Catalog / Rule:** Feature Flag Without Feature Activation (no standard Fowler entry); Dead Code (Fowler "Remove Dead Code").
- **Prerequisites:** CRRA-001 should be fixed first so the haircut produces a meaningful output before n_effective is wired.
- **Test Coverage:** `test_n_effective_additive_accounting.py` has 931 lines of tests for the function and parameter in isolation, but no end-to-end test exercises `run_autotuner` and asserts the haircut was called with a computed `n_effective`.

---

## Confirmed Bugs

### [BUG-001] CRRA-001 — Haircut t-stat for CRRA-EU uses raw percent returns instead of utility values

As detailed in §10 CRRA-001. This produces incorrect behavior: `compute_crra_eu_tstat` receives `r_pct` instead of `U`. The function treats the percent-return series as utility values and computes `mean(r_pct) / (sd(r_pct) / sqrt(T))`, which is the Sharpe t-stat rather than the CRRA-EU t-stat. Trial selection in the CRRA-EU branch silently reverts to Sharpe-like selection, negating the entire CRRA-EU objective improvement.

---

## Patterns Observed

- **Build-test-wire gap:** Several Sprint 2 features implement the math correctly and are well-tested in isolation, but the production wiring (n_effective → _haircut_select, EUT columns → save_autotune_run) was deferred without tests to catch the deferral. This pattern creates "complete-looking" features that are dead in production.
- **Contract-in-docstring, not-in-code:** The docstring at `autotuner.py:1083` documents the correct U-transformation contract; the code does not implement it. Docstrings are being used as specification rather than description.
- **Alias proliferation:** `run_simulation` / `run_simulation_sortino_legacy` / `run_simulation_crra_eu` are three names for two closely related functions; names embed change history.

---

## Risk Summary

| Category | CRITICAL | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|---|
| Cross-Cycle Interactions | 0 | 1 | 0 | 0 | 1 |
| Architectural Drift | 0 | 1 | 1 | 0 | 2 |
| Type-Design | 0 | 0 | 1 | 1 | 2 |
| Naming Hygiene | 0 | 0 | 0 | 2 | 2 |
| Test-Quality | 0 | 0 | 1 | 0 | 1 |
| Provenance Gaps | 0 | 0 | 0 | 1 | 1 |
| Latent Breakage | 0 | 0 | 1 | 0 | 1 |
| CVaR Display | 0 | 0 | 1 | 0 | 1 |
| CRRA-EU Math | 1 | 0 | 0 | 0 | 1 |
| N-Effective | 1 | 1 | 0 | 0 | 2 |
| **Total** | **2** | **3** | **4** | **3** | **12** |

**Highest-risk findings:** CRRA-001, NEFF-001, ARCH-001, CC-NEW-001.

---

## Recommendations Index

Ordered by suggested execution order, respecting prerequisite chains:

1. **CRRA-001** (CRITICAL, no prereqs) — Fix `_haircut_select` CRRA-EU branch to apply U-transformation before `compute_crra_eu_tstat`
2. **TEST-001** (MEDIUM, prereqs: CRRA-001) — Add cross-function contract test for `_haircut_select` + `compute_crra_eu_tstat` integration
3. **NEFF-001** (CRITICAL, prereqs: CRRA-001) — Wire `compute_n_effective` into both `_haircut_select` production call sites
4. **ARCH-001** (HIGH, prereqs: NEFF-001) — Extend `save_autotune_run` with EUT columns; populate from autotuner run state
5. **CC-NEW-001** (HIGH, no prereqs, parallel to 1–4) — Serialize `flush_resync` background write against engine writes
6. **LATENT-001** (MEDIUM, prereqs: CC-NEW-001) — Fix `_flush_state_async` log to use background-local count
7. **TYPE-002** (MEDIUM, no prereqs) — Clarify hash vs integer key type in `compute_n_effective` parameter
8. **CVAR-001** (MEDIUM, no prereqs) — Document or fix single-symphony CVaR display limitation
9. **ARCH-002** (MEDIUM, no prereqs) — Add inline comment explaining 021/020 out-of-order placement
10. **TYPE-001** (LOW) — Annotate `spec_bundle_id: int` on `run_autotuner`
11. **NAME-001** (LOW) — Rename `run_simulation_sortino_legacy` to a behavior-describing name
12. **NAME-002** (LOW) — Reconcile `_PCT` suffix inconsistency between module-level and local alias names
13. **PROV-001** (LOW) — Update `council-converged-migration-plan.md` migration number table

---

## Open Questions

- **[ASSUMPTION-01]** — CRRA-001 numerical impact assessment: for the actual production data (guard-alpha percent returns in the range ~[-5%, +10%]), does the t-stat ranking divergence between `mean(r_pct)/sd(r_pct)` and `mean(U)/sd(U)` materially change which trial wins? The static analysis confirms the functional mismatch is real; the practical haircut-gate impact depends on the return distribution of Optuna trials. — **Blocking** for severity calibration.
- **[ASSUMPTION-02]** — CC-NEW-001 race: does the engine's SQLite WAL mode prevent the clobber (if the engine write completes before the flush thread's save_state, does WAL serialization protect the engine write)? SQLite WAL serializes individual writes but not multi-statement round-trips. — **Non-blocking** (the race exists regardless; severity may differ).
- **[QUESTION-01]** — NEFF-001: is the n-effective wiring intentionally deferred to Sprint 3, or was it accidentally omitted? The test plan (D5) requires `run_autotuner` to persist s_count, n_effective, etc. but the production path does not. — **Blocking** for Sprint 2 close classification.

---

## Evidence Appendix

### Git pre-flight
```
$ git rev-parse HEAD
88198674e4afe2b7001e2646bddcdb4d47dc0325
$ git status -sb
## audit/sprint-2-cross-cycle
```

### _haircut_select call sites (grep evidence for NEFF-001)
```
autotuner.py:893: def _haircut_select(completed_trials, n_effective: "int | None" = None, tstat_fn=compute_sortino_tstat):
autotuner.py:1558: winner_trial, winner_p_adj, winner_tstat = _haircut_select(haircut_trials, tstat_fn=_tstat_fn)
autotuner.py:1838: winner_trial, winner_p_adj, winner_tstat = _haircut_select(haircut_trials)
```
Both production calls omit `n_effective`. `compute_n_effective` has zero production call sites (confirmed by full codebase grep).

### compute_crra_eu_tstat input type (grep evidence for CRRA-001)
```
# autotuner.py:1487–1491: daily_returns stored as raw percent returns
trial.set_user_attr("daily_returns", daily_returns)  # daily_returns from _collect_sim_returns (percent)

# autotuner.py:942–948: haircut reads back and passes to tstat_fn
series = t.user_attrs.get("daily_returns", [])
tstats.append(tstat_fn(series))  # tstat_fn = compute_crra_eu_tstat; series = raw percent returns

# autotuner.py:394: function signature
def compute_crra_eu_tstat(U_series: list[float]) -> float:
    """...mean(U) / (sd(U) / sqrt(T))..."""
```

### save_autotune_run signature (grep evidence for ARCH-001)
```
database.py:407: def save_autotune_run(
    run_timestamp, symphony_id, oos_alpha, train_alpha,
    baseline_decision, fallback_oos_alpha, default_oos_alpha,
    selection_tstat=None, naive_sharpe=None,
    validation_sharpe=None, frozen_eval_sharpe=None,
) -> None:
```
No `spec_bundle_id`, `n_effective`, `s_count`, `d_spec`, `gamma`, or `overfitting_verdict` parameters.

### Discipline enum alignment verification
```python
autotuner.NN1_HONEST_DISCIPLINES: ['CADENCE', 'CALIBRATION', 'MANDATE', 'POLITIS_WHITE', 'STYLIZED_FACT', 'THEORY']
database._VALID_FREEZE_DISCIPLINES: ['BACKTEST_SELECTION', 'CADENCE', 'CALIBRATION', 'MANDATE', 'POLITIS_WHITE', 'STYLIZED_FACT', 'THEORY']
Drift: none (symmetric set difference = empty)
```

### Files read
- `autotuner.py` (full Sprint 2 diff + selected current-state sections)
- `math_engine.py` (full Sprint 2 diff)
- `database.py` (full Sprint 2 diff)
- `alpha_bot_execution.py` (full Sprint 2 diff)
- `app.py` (full Sprint 2 diff)
- `migrations/020–024` (full content)
- `templates/index.html` (CVaR panel section)
- `tests/autotuner/test_n_effective_additive_accounting.py` (selected sections)
- `tests/autotuner/test_crra_eu_tstat_formula_pin.py` (selected sections)
- `tests/fixtures/math/crra_tstat_formula_pin.json` (full)
- `docs/audit/sprint-1-cross-cycle-audit.md` (known-debt reference)
- `docs/handoff/council-converged-migration-plan.md` (migration table)

### Files skipped
- `tests/autotuner/test_m1_crra_eu_objective.py` — 1025 lines; structure verified via grep; no additional findings surfaced
- `tests/database/test_020_autotune_runs_eut.py` — 1199 lines; append-pattern confirmed via grep
- `tests/database/test_phase1_theory_bundle_accessor.py` — 1174 lines; correctness verified via runtime path trace
- `synthetic_history.py` — not in Sprint 2 delta; tick unit convention confirmed via grep (`agg_ret * 100.0`)
- `reporting.py` — not in Sprint 2 delta; no CVaR references (grep-confirmed)
