# AlphaBot v3 — Sprint 1 Cross-Cycle Audit Report

## Metadata

- **Auditor:** code-auditor (claude-sonnet-4-6)
- **Run date:** 2026-05-25T11:00:00Z
- **Repo HEAD (worktree):** `3e0b83a35c2c92149a8110b534bb3868ca0baabd`
- **Branch:** `plan/finalist-a-scaffold`
- **`git status -sb`:** `## plan/finalist-a-scaffold...origin/main [ahead 59, behind 12]`
- **Scope:** `git diff 0735b61..3e0b83a` — 54 files, 11 190 insertions, 928 deletions
- **Audit range:** 0735b61 (Sprint 1 starting tip) → 3e0b83a (Sprint 1 merged tip)
- **Tooling:** static read of diff + `git show`; no lint runner invoked (project lint config not read-only-safe from audit branch)

---

## §0 Summary Table

| Severity | Count |
|----------|-------|
| CRITICAL | 2     |
| HIGH     | 5     |
| MEDIUM   | 5     |
| LOW      | 3     |
| **Total**| **15** |

**Top 3 highest-risk findings:**

- **[CC-001] CRITICAL** — `advisor_ro_query` writes to `advisor_observations` which has no creation path in the sprint scope. The `_write_wall_breach_observation` call silently fails at runtime when migration 017 has not been applied, destroying the audit record that must survive before the exception raises.
- **[CC-002] CRITICAL** — `write_telemetry_row` constructs SQL via an f-string over the caller-supplied `table_name` and `row_dict` keys (both unvalidated strings). This is a SQL injection vector new to this sprint.
- **[CC-003] HIGH** — `_DISMISS_EXECUTOR` (ThreadPoolExecutor) has no `atexit` shutdown registration. On daemon exit an in-flight dismiss write can be silently abandoned, producing a dismissed UI state with no DB record.

---

## §1 Cross-Cycle Interactions

### [CC-001] CRITICAL — `_write_wall_breach_observation` silent failure: `advisor_observations` table not guaranteed to exist

- **File:** `database.py:1044`
- **Confidence:** HIGH (static: `_write_wall_breach_observation` always INSERTs into `advisor_observations`; the table is only referenced in test fixture setup at `tests/database/test_019_fold_role_columns.py:85-110` — it is created inline by the test, not by any migration in scope)
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  conn.execute(
      "INSERT INTO advisor_observations "
      "(advisor_role, subject_type, subject_id, verdict, raw_response, is_advisory_only) "
      "VALUES (?, ?, ?, ?, ?, ?)",
  ```
- **Proposed Pattern:** `_write_wall_breach_observation` must guard with a `CREATE TABLE IF NOT EXISTS advisor_observations (...)` or the table must be created by a migration that is present in `_MIGRATION_FILES`. Migration 017 exists on a separate branch (commit `3c751b5`) but is absent from the sprint-tip `_MIGRATION_FILES` list, which jumps directly from `015` to `016` to `019`.
- **Catalog / Rule:** Additive-first schema rule (project CLAUDE.md §Architecture Constraints item 6); missing prerequisite migration is a structural correctness gap (analogous to Feathers' characterization test requirement for untested code).
- **Prerequisites:** None
- **Test Coverage:** The test fixture manually creates `advisor_observations` inline (`tests/database/test_019_fold_role_columns.py:94`). The production table creation path is absent. NO TESTS for the production table existence path — CHARACTERIZATION TEST REQUIRED.

---

### [CC-004] MEDIUM — `dismissed_at_et` timestamp format changed from `strftime` to `isoformat`, no format contract test

- **File:** `app.py:1511`
- **Confidence:** HIGH (grep-confirmed: prior handler used `strftime("%Y-%m-%dT%H:%M:%S")` producing tz-naive strings; new handler uses `datetime.now(_ET).isoformat()` producing tz-aware strings with UTC-offset suffix `+HH:MM`)
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  row["dismissed_at_et"] = datetime.now(_ET).isoformat()
  # produces "2026-05-25T10:48:27.906780-04:00"
  ```
  Prior contract (pre-sprint, `app.py` old handler):
  ```python
  now_et = datetime.now(_ET).strftime("%Y-%m-%dT%H:%M:%S")
  # produces "2026-05-25T10:48:27"
  ```
- **Proposed Pattern:** Either adopt `isoformat()` consistently for all `_et` fields (including `tripped_at_et` written by `alpha_bot_execution.py:537`) or keep the `strftime` format to maintain consistency. The comment at `alpha_bot_execution.py:493` says "tripped_at_et is stored tz-naive ET; strip tz from now_et for comparison" — `dismissed_at_et` now writes a tz-aware string while `tripped_at_et` remains tz-naive, breaking any code that parses both fields for comparison.
- **Catalog / Rule:** Adopt Existing Contracts rule (CLAUDE.md §Scope Discipline); field format change without updating all consumers is an "invent rather than adopt" anti-pattern.
- **Prerequisites:** None
- **Test Coverage:** No test asserts the timestamp format of `dismissed_at_et`; `test_fleet_banner.py` only asserts `is not None`. NO TESTS for format consistency — CHARACTERIZATION TEST REQUIRED.

---

### [CC-005] MEDIUM — `flush_resync` Phase 2 downgraded to enumeration-only with no background dispatch: AC-FR.2 is silently broken

- **File:** `app.py:2088–2101`, `tests/app/test_flush_resync.py:229`
- **Confidence:** HIGH (sprint diff removes `save_state` call; test now asserts `save_state.assert_not_called()` — but the docstring at `app.py:2055` still states "reset per-symphony bot_state entries" and the test's AC-FR.2 comment says "NEW CONTRACT (side-effect ban): database.save_state must NOT be called directly from the Flask request thread. The state-reset is dispatched to a background worker")
- **Risk:** CORRECTNESS
- **Effort:** MEDIUM
- **Current Pattern:**
  ```python
  # Dashboard side-effect ban: save_state is engine-exclusive.  This phase reads the
  # current state to report which symphonies would be reset, but does NOT persist changes.
  symphonies_reset.append(_sym_val.get("name", _sym_id))
  ```
  The fleet-dismiss cycle dispatched writes to `_DISMISS_EXECUTOR`. The flush-resync cycle removed the write without dispatching it to any background executor; the state reset never fires.
- **Proposed Pattern:** If the intent is a deferred reset, create a named background executor analogous to `_DISMISS_EXECUTOR` or route to the scheduler. If the intent is "read-only reporting only," update AC-FR.2 and the endpoint contract documentation.
- **Catalog / Rule:** Cross-cycle interaction (audit category 1); a removed side-effect without a replacement dispatch is a silent behavior change. Refactoring catalog: "Replace Method with Method Object" applies if a background executor is warranted.
- **Prerequisites:** None
- **Test Coverage:** The test now explicitly asserts `save_state.assert_not_called()` and passes — but there is no test asserting the reset actually happens in any context. NO TESTS for actual reset execution — CHARACTERIZATION TEST REQUIRED.

---

## §2 Architectural Drift

### [CC-002] CRITICAL — `write_telemetry_row`: SQL injection via f-string over caller-supplied `table_name` and column names from `row_dict`

- **File:** `database.py:1516`
- **Confidence:** HIGH (three-occurrence pattern: `table_name`, `col_names`, and `placeholders` all derive from unvalidated caller inputs)
- **Risk:** SECURITY
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
  ```
  `table_name` is a positional `str` argument with no validation or allowlist. `col_names` is derived from `row_dict.keys()` — also caller-controlled strings. Only `values` (the tuple of actual values) is parameterized.
- **Proposed Pattern:** Validate `table_name` against a frozenset of permitted table names (analogous to `_VALID_FREEZE_DISCIPLINES` at `database.py:821`) before constructing the f-string. Column names from `row_dict` should be similarly validated against the target table's known columns.
- **Catalog / Rule:** OWASP A03:2021 — Injection; project CLAUDE.md §Coding Standards "Validate at system boundaries"; `_VALID_FREEZE_DISCIPLINES` pattern already in this file is the canonical precedent.
- **Prerequisites:** None
- **Test Coverage:** No test attempts an injected `table_name`. NO TESTS for this boundary — CHARACTERIZATION TEST REQUIRED.

---

### [CC-003] HIGH — `_DISMISS_EXECUTOR` has no `atexit` shutdown; in-flight writes can be silently abandoned on daemon exit

- **File:** `app.py:65`
- **Confidence:** HIGH (single occurrence, confirmed by grep: `atexit` appears at lines 3, 179, 180 only; none register `_DISMISS_EXECUTOR.shutdown`)
- **Risk:** CORRECTNESS
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  _DISMISS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)
  # ... no atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True) anywhere
  ```
- **Proposed Pattern:** Add `atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True)` immediately after `_DISMISS_EXECUTOR` is declared at `app.py:65`. `wait=True` ensures any queued write completes before the process exits.
- **Catalog / Rule:** Python `concurrent.futures` documentation §Executor Objects: "It is safe to call this method several times. If wait is True then shutdown will not return until all pending futures are done executing." Missing shutdown registration is a resource-management smell (Fowler: "Introduce Assertion").
- **Prerequisites:** None
- **Test Coverage:** `test_fleet_dismiss_background_dispatch.py` tests behavior during normal operation but does not cover the daemon-exit path. NO TESTS for executor shutdown on process exit — CHARACTERIZATION TEST REQUIRED.

---

### [CC-006] HIGH — `force_eod` route spawns `autotuner.run_autotuner` on a background thread — architecture constraint 2 violation

- **File:** `app.py:1581`
- **Confidence:** MEDIUM (pre-existing behavior not introduced in this sprint; however, the dashboard-side-effect-ban cycle explicitly strengthened arch constraint 2, and the new `test_dashboard_routes_never_call_engine_mutators.py` does not cover `/api/force_eod`)
- **Risk:** RUNTIME
- **Effort:** MEDIUM
- **Current Pattern:**
  ```python
  def run_eod_tasks():
      ...
      autotuner_changes = autotuner.run_autotuner(...)  # engine mutator
  threading.Thread(target=run_eod_tasks, daemon=True).start()
  ```
- **Proposed Pattern:** Note this for the test-coverage gap rather than a code fix — the dispatch is to a background thread, which is consistent with the `_DISMISS_EXECUTOR` pattern. The finding is that the dashboard-side-effect-ban cycle tests don't enumerate `/api/force_eod` in their route scan, leaving a documented exception untested.
- **Catalog / Rule:** Architecture constraint 2 (project CLAUDE.md: "Dashboard is a read-only operator surface — never an action surface for live trades"); the test suite for the ban cycle explicitly lists a `_ALLOWED_ORDER_ROUTES` frozenset but `/api/force_eod` is not in it nor tested.
- **Prerequisites:** None
- **Test Coverage:** `test_dashboard_routes_never_call_engine_mutators.py` does not assert on `/api/force_eod`. HAS TESTS for other routes; NO TESTS for this route's engine-spawn path.

---

## §3 Type-Design Smells

### [CC-007] HIGH — `advisor_ro_query` bare `except Exception` in `_write_wall_breach_observation` swallows write failure silently, defeating the "audit row must survive" contract

- **File:** `database.py:1054`
- **Confidence:** HIGH (direct quote from sprint delta)
- **Risk:** CORRECTNESS
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  except Exception as exc:  # noqa: BLE001
      logging.error("_write_wall_breach_observation: failed to write audit row: %s", exc)
  ```
  The docstring states: "the audit row is best-effort; the breach detection is not." The `# noqa: BLE001` suppresses the blind-except lint rule. However, swallowing this error means an operator never knows the audit trail is broken — the only signal is an `ERROR` log that may not be monitored.
- **Proposed Pattern:** This is a documented intentional design decision. The finding is that the `noqa` suppression is correct, but there is no test asserting that the exception is logged when the write fails. The design should be noted as accepted debt if the PM concurs.
- **Catalog / Rule:** BLE001 (flake8-bugbear "Do not catch blind exceptions"); project convention suppresses it here intentionally but this is the only such suppression in the sprint delta.
- **Prerequisites:** CC-001 (if `advisor_observations` doesn't exist, this path always fires)
- **Test Coverage:** `test_019_fold_role_columns.py` exercises the breach detection path only when `fold_db_with_advisor_observations` fixture is present. NO TESTS for the exception path within `_write_wall_breach_observation`. CHARACTERIZATION TEST REQUIRED.

---

### [CC-008] MEDIUM — `CVaRAssessment.__post_init__` missing inverse check: `cvar_pct is not None` with `tail_obs_count == 0` is not enforced

- **File:** `math_engine.py:116–127`
- **Confidence:** MEDIUM (single-occurrence; the contract only enforces `None → count==0`, not the inverse `count > 0 → not None`)
- **Risk:** CORRECTNESS
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  if self.cvar_pct is None and self.tail_obs_count != 0:
      raise ValueError(...)
  ```
  `CVaRAssessment(cvar_pct=-4.2, breach=True, tail_obs_count=0, insufficient_reason=None)` is constructible — a valid estimate with zero tail observations, which is contradictory.
- **Proposed Pattern:** Add the inverse guard: `if self.cvar_pct is not None and self.tail_obs_count == 0: raise ValueError(...)`. Reject any construction that claims a real CVaR estimate from zero tail observations.
- **Catalog / Rule:** Fowler "Introduce Assertion" (refactoring.com/catalog/introduceAssertion.html); invariant completeness — the "None implies 0" direction is enforced without its mirror.
- **Prerequisites:** None
- **Test Coverage:** `test_cvar_assessment_dataclass_contract.py` test 4 constructs `cvar_pct=-8.5, tail_obs_count=7` (valid case) and does not probe `cvar_pct=valid float, tail_obs_count=0`. NO TESTS for this illegal construction — CHARACTERIZATION TEST REQUIRED.

---

## §4 Naming Hygiene

### [CC-009] LOW — `_BARE_FOLD_ROLE_PREDICATES` constant name describes the smell, not the runtime behavior

- **File:** `database.py:967`
- **Confidence:** LOW (single occurrence; naming preference)
- **Risk:** STYLE
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  _BARE_FOLD_ROLE_PREDICATES = ("fold_role !=", "fold_role <>")
  ```
- **Proposed Pattern:** `_FOLD_ROLE_NULL_TRAP_PREDICATES` or `_DISALLOWED_FOLD_ROLE_PREDICATES` — names the runtime hazard, not the syntactic form.
- **Catalog / Rule:** Global hard rule (CLAUDE.md §Promoted Rules): "Names describe runtime behavior, not change history." Using "bare" describes the text pattern, not why it's prohibited.
- **Prerequisites:** None
- **Test Coverage:** N/A — constant renaming.

---

### [CC-010] LOW — `_dismiss_async` inner function name is generic; describes the mechanism, not what is being dismissed

- **File:** `app.py:1506`
- **Confidence:** LOW (single occurrence; naming preference)
- **Risk:** STYLE
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```python
  def _dismiss_async():
  ```
- **Proposed Pattern:** `_write_fleet_alert_dismissed` or `_apply_fleet_alert_dismiss` — names the domain action.
- **Catalog / Rule:** Global hard rule (CLAUDE.md §Promoted Rules): "Names describe runtime behavior, not change history."
- **Prerequisites:** None
- **Test Coverage:** N/A — inner function rename.

---

## §5 Test-Quality Smells

### [CC-011] MEDIUM — `test_fleet_dismiss_background_dispatch.py`: timing-dependent assertions use wall-clock 50 ms ceiling without CI-environment guard

- **File:** `tests/app/test_fleet_dismiss_background_dispatch.py:107–155`
- **Confidence:** HIGH (three test methods use `max_ms = _FIXTURE["handler_latency_ms"]["max_ms"]` which is 50 ms; the tests run `time.monotonic()` wall-clock on a test-client HTTP call; no `@pytest.mark.perf` or `skipif slow` marker guards these from normal CI)
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  max_ms = _FIXTURE["handler_latency_ms"]["max_ms"]   # 50 ms
  assert elapsed_ms < max_ms, ...
  ```
  These tests are in `tests/app/` (not `tests/database/`) and have no `pytest.mark.perf` annotation unlike `test_telemetry_helper_per_cycle_latency.py` which does.
- **Proposed Pattern:** Either add `@pytest.mark.perf` + exclude from the default run (consistent with `test_telemetry_helper_per_cycle_latency.py` precedent) or convert to a structural test that asserts the executor submit call occurs rather than measuring latency.
- **Catalog / Rule:** Project memory `feedback_full_suite_means_genuine_full_tree`: a timing test without CI guard is a latent flake (slow CI machines, test-parallelism scheduling).
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS — this IS the smell in the test file.

---

### [CC-012] MEDIUM — `test_019_fold_role_columns.py`: `fold_db_with_advisor_observations` fixture creates `advisor_observations` inline, masking the missing migration gap at the production table level

- **File:** `tests/database/test_019_fold_role_columns.py:85–111`
- **Confidence:** HIGH (direct quote from sprint delta: the fixture calls `CREATE TABLE IF NOT EXISTS advisor_observations` manually, bypassing `run_migrations()`)
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  conn.execute("""
      CREATE TABLE IF NOT EXISTS advisor_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ...
      )
  """)
  ```
  This approach means tests pass even when migration 017 is absent from `_MIGRATION_FILES`, because the table is created outside the migration path.
- **Proposed Pattern:** The fixture should call `run_migrations()` (which includes migration 017 once it is added to `_MIGRATION_FILES`) rather than creating the table manually. This turns the test into a structural check of the full migration stack.
- **Catalog / Rule:** Fowler "Replace Magic Literal with Symbolic Constant" (for inline schema DDL in tests); project CLAUDE.md §Coding Standards "API calls must be testable from a fixture."
- **Prerequisites:** CC-001 (the fix is the missing migration 017 in `_MIGRATION_FILES`)
- **Test Coverage:** HAS TESTS — the test suite is the structural proof of the gap.

---

## §6 Provenance Gaps

### [CC-013] HIGH — `spec_facets.bundle_hash` is a soft FK with no referential integrity; orphaned facet rows cannot be detected at the DB layer

- **File:** `migrations/016_spec_bundles.sql:18`, `database.py:910–944`
- **Confidence:** HIGH (direct quote from migration SQL: `bundle_hash TEXT NOT NULL, -- soft FK to spec_bundles.bundle_hash`; no `REFERENCES`, no `PRAGMA foreign_keys = ON` anywhere in the sprint delta)
- **Risk:** CORRECTNESS
- **Effort:** MEDIUM
- **Current Pattern:**
  ```sql
  bundle_hash  TEXT NOT NULL,    -- soft FK to spec_bundles.bundle_hash
  ```
  `insert_spec_bundle_facet` at `database.py:910` does not verify that the `bundle_hash` references a real bundle before inserting. An orphaned facet row (facets for a non-existent bundle) is possible and undetectable at the DB level.
- **Proposed Pattern:** Either enable `PRAGMA foreign_keys = ON` at connection open and add `REFERENCES spec_bundles(bundle_hash)` to the DDL, or add an application-layer existence check in `insert_spec_bundle_facet` before the INSERT. The codebase has precedent for app-layer FK validation (the `freeze_discipline` enum check at `database.py:926`).
- **Catalog / Rule:** "Introduce Foreign Key" (SQL design; analogous to Fowler "Introduce Assertion" for relational constraints); project architecture constraint: "Validate at system boundaries."
- **Prerequisites:** None
- **Test Coverage:** `test_spec_bundles.py` covers FK walk-back (test 4: "spec_facets.bundle_hash references a real bundle — app-level soft-FK: walking from a facet row back to its parent bundle returns a non-empty row") but does NOT test inserting a facet for a non-existent bundle. CHARACTERIZATION TEST REQUIRED.

---

## §7 Latent Breakage

### [CC-014] MEDIUM — `test_run_monte_carlo_consumers_enumerated.py`: call-count baseline `_BASELINE_CALL_SITES` is a hardcoded dict that will silently pass if a new call site is added to a file not in `_PRODUCTION_FILES`

- **File:** `tests/integration/test_run_monte_carlo_consumers_enumerated.py:56`
- **Confidence:** MEDIUM (single occurrence but the pattern recurs from the test_016 hotfix cycle)
- **Risk:** CORRECTNESS
- **Effort:** SMALL
- **Current Pattern:**
  ```python
  _BASELINE_CALL_SITES = {
      "alpha_bot_execution.py": 1,
      "synthetic_history.py": 1,
      "autotuner.py": 0,
      "reporting.py": 0,
      "engine/dual_altitude.py": 0,
  }
  ```
  A new production file calling `run_monte_carlo(` would not be detected because only the five enumerated files are scanned. The baseline count for the new file would default to 0 (not in the dict) and the test would not catch the addition.
- **Proposed Pattern:** The test should also assert that `run_monte_carlo(` does not appear in any production `.py` file NOT in `_PRODUCTION_FILES` (i.e., glob all `.py` files in the project root and assert zero occurrences outside the explicitly listed files). This is the "is_last_entry" pattern cited in the dispatch.
- **Catalog / Rule:** Project memory `feedback_audit_findings_need_call_path_verification`: the file-scope grep overstates coverage if a new call site exists in an unlisted file.
- **Prerequisites:** None
- **Test Coverage:** HAS TESTS — the call-count check IS the existing test; the gap is the unlisted-file case.

---

## §8 Documentation Drift

### [CC-015] LOW — `flush_resync` docstring says "reset per-symphony bot_state entries" but Phase 2 now only enumerates (read-only)

- **File:** `app.py:2047–2057`
- **Confidence:** HIGH (direct quote from sprint diff: docstring was updated but outer summary still says "reset per-symphony bot_state")
- **Risk:** STYLE
- **Effort:** TRIVIAL
- **Current Pattern:**
  ```
  Phase 2 — Symphony state enumeration (read-only): loads bot_state and records which
    symphonies would be reset for reporting purposes.  No write is issued on the request
    thread; state mutation is engine-exclusive.
  ```
  The outer one-line route docstring at `app.py:2047` still reads: "Delete synthetic post_mortem backfill files, reset per-symphony bot_state, resync Composer." The word "reset" implies mutation that no longer occurs.
- **Proposed Pattern:** Update the one-line docstring to "Delete synthetic post_mortem backfill files, enumerate symphony state entries, resync Composer." This is consistent with Phase 2's new enumeration-only behavior.
- **Catalog / Rule:** Documentation drift (project CLAUDE.md §Documentation: "Every public function gets a docstring (what + why, not how)").
- **Prerequisites:** None
- **Test Coverage:** N/A — documentation only.

---

## Patterns Observed

1. The `advisor_observations` table dependency is the most critical cross-cycle gap: migration 017 was developed on a parallel branch and is not in `_MIGRATION_FILES` at the sprint tip, leaving `_write_wall_breach_observation` with a broken INSERT target in production.
2. The `write_telemetry_row` f-string SQL pattern (table_name, col_names both unvalidated) is newly introduced in this sprint. The pre-existing `port_state` f-string pattern (`database.py:1214`) only interpolates column names from an internally controlled list — the new pattern takes both table name and column names from caller-controlled inputs, which is a step up in injection surface.
3. The fleet-dismiss / flush-resync cycles share the "background dispatch" pattern but implement it differently: fleet-dismiss uses a persistent `ThreadPoolExecutor`; flush-resync removed its write with no background replacement. The inconsistency is a maintenance hazard.
4. The test_016 hotfix cycle established the precedent of broadening an assertion to "order-only" rather than "pin last entry." The `_BASELINE_CALL_SITES` dict in test_run_monte_carlo_consumers_enumerated.py is the next incarnation of the same pattern.
5. Timing assertions in `test_fleet_dismiss_background_dispatch.py` are not marked `@pytest.mark.perf` unlike `test_telemetry_helper_per_cycle_latency.py`, creating a latent flake risk in constrained CI environments.

---

## Risk Summary

| Category | HIGH | MEDIUM | LOW |
|----------|------|--------|-----|
| CRITICAL |   2  |   —    |  —  |
| HIGH     |   5  |   —    |  —  |
| MEDIUM   |   —  |   5    |  —  |
| LOW      |   —  |   —    |  3  |

**Highest-risk findings in execution order (prerequisite chain respected):**

1. CC-001 → CC-007 → CC-012 (advisor_observations chain)
2. CC-002 (standalone SQL injection)
3. CC-003 (executor shutdown)
4. CC-013 (soft FK orphan detection)
5. CC-004 (timestamp format drift)
6. CC-005 (flush-resync write gap)
7. CC-006 (force_eod test coverage gap)
8. CC-008 (CVaRAssessment inverse invariant)
9. CC-011 (timing test flake risk)
10. CC-014 (call-count baseline gap)

---

## Recommendations Index

*(Finding IDs grouped by suggested execution order; prerequisites respected)*

**Wave 1 — Production correctness (dispatch immediately):**
- CC-001 — Add migration 017 to `_MIGRATION_FILES`
- CC-002 — Add `table_name` allowlist to `write_telemetry_row`
- CC-003 — Register `_DISMISS_EXECUTOR.shutdown(wait=True)` via `atexit`
- CC-004 — Standardise `dismissed_at_et` timestamp format across all writers

**Wave 2 — Structural completeness (next cycle):**
- CC-005 — Clarify flush-resync Phase 2: dispatch reset to background or drop from AC-FR.2
- CC-013 — Add FK guard to `insert_spec_bundle_facet`
- CC-008 — Add inverse `__post_init__` guard to `CVaRAssessment`

**Wave 3 — Test hardening:**
- CC-007 — Add test for `_write_wall_breach_observation` failure path
- CC-011 — Add `@pytest.mark.perf` to timing tests in `test_fleet_dismiss_background_dispatch.py`
- CC-012 — Route `fold_db_with_advisor_observations` through `run_migrations()` once CC-001 lands
- CC-014 — Extend call-count baseline to scan all unlisted `.py` files

**Wave 4 — Documentation and naming:**
- CC-006 — Add `/api/force_eod` to side-effect-ban test's route scan
- CC-015 — Update `flush_resync` one-line docstring
- CC-009 — Rename `_BARE_FOLD_ROLE_PREDICATES`
- CC-010 — Rename `_dismiss_async`

---

## Open Questions

- **[ASSUMPTION-01]** — Migration 017 (`advisor_observations` DDL) was authored on branch `cycle/017-advisor-observations` (commits `3c751b5`, `2f41e50`). Was it intentionally excluded from the sprint-1 merge, or is its absence a dispatch oversight? — **Blocking** for CC-001 / CC-007 / CC-012.
- **[ASSUMPTION-02]** — The `flush_resync` Phase 2 write removal (CC-005): is the intent "read-only diagnostic only" (no reset ever fires), or "reset fires later via a different mechanism not yet built"? — **Blocking** for CC-005 remediation scope.
- **[QUESTION-03]** — `write_telemetry_row` table_name injection (CC-002): current callers are only `record_cvar_diagnostic` (hardcoded `"cvar_diagnostics"`). Is the generic API intended to be caller-open, or is `record_cvar_diagnostic` the intended sole wrapper? If sole wrapper, the fix is to make `write_telemetry_row` private (rename to `_write_telemetry_row`) and remove the `table_name` parameter. — **Non-blocking** (remediation approach question).

---

## Evidence Appendix

### File:Line Citation Index

| Finding | File | Line(s) |
|---------|------|---------|
| CC-001 | database.py | 1044–1059 |
| CC-002 | database.py | 1516 |
| CC-003 | app.py | 65 |
| CC-004 | app.py | 1511 |
| CC-005 | app.py | 2088–2101 |
| CC-006 | app.py | 1581 |
| CC-007 | database.py | 1054 |
| CC-008 | math_engine.py | 116–127 |
| CC-009 | database.py | 967 |
| CC-010 | app.py | 1506 |
| CC-011 | tests/app/test_fleet_dismiss_background_dispatch.py | 107–155 |
| CC-012 | tests/database/test_019_fold_role_columns.py | 85–111 |
| CC-013 | migrations/016_spec_bundles.sql | 18 |
| CC-014 | tests/integration/test_run_monte_carlo_consumers_enumerated.py | 56 |
| CC-015 | app.py | 2047 |

### Scope Coverage

**Files read in full (sprint delta):**
- `database.py` (full diff + targeted `git show` reads)
- `app.py` (full diff + targeted `git show` reads)
- `alpha_bot_execution.py` (full diff)
- `math_engine.py` (full diff)
- `migrations/016_spec_bundles.sql`
- `migrations/019_fold_role_columns.sql`
- `tests/app/test_fleet_dismiss_background_dispatch.py` (150-line sample + targeted sections)
- `tests/app/test_dashboard_no_order_path.py` (150-line sample)
- `tests/app/test_flush_resync.py` (delta + targeted reads)
- `tests/database/test_spec_bundles.py` (200-line sample)
- `tests/database/test_019_fold_role_columns.py` (200-line sample)
- `tests/database/test_telemetry_helper_connection_pattern.py` (full)
- `tests/database/test_telemetry_helper_per_cycle_latency.py` (100-line sample)
- `tests/math_engine/test_cvar_assessment_dataclass_contract.py` (full)
- `tests/math_engine/test_cvar_assessment_fail_safe_invariant.py` (80-line sample)
- `tests/math_engine/test_mc_sentinel_blast_radius_coverage.py` (150-line sample)
- `tests/integration/test_run_monte_carlo_consumers_enumerated.py` (120-line sample)
- `tests/integration/test_composer_alpaca_client_log_redaction.py` (100-line sample)
- `tests/integration/test_h4_helper_log_redaction.py` (full header + key fixtures)
- `tests/dashboard/test_fleet_banner.py` (delta + 80-line sample)
- `tests/fixtures/app/fleet_dismiss_background_dispatch.json`
- `docs/handoff/run-monte-carlo-consumer-map.md` (referenced, not read — path confirmed to exist via test assertion)
- `tests/database/conftest.py`

**Files skipped (not in sprint delta or not producing new findings):**
- `tests/analytics/test_analytics.py`, `test_drawdown_sign_convention.py` — delta was 1-2 lines each; trivial import/skip additions
- `tests/engine/test_fleet_alert_state_table.py` — delta was cosmetic test cleanup
- `tests/execution/test_execute_sell_to_cash.py` — delta was mock-pattern cleanup
- `tests/app/test_dashboard_advisor_render_is_read_only.py`, `test_dashboard_routes_never_call_engine_mutators.py`, `test_dashboard_routes_read_only_db.py` — new test suites; no production code changes; quality checked via test-quality pass
- All fixture JSON files except `fleet_dismiss_background_dispatch.json` — structural fixtures, no code path findings
- `docs/feature-plans/` plan.md files — documentation only; not production artifacts
- `.claude/agents/*.md` — 2-line delta for tooling fix; out of scope
- `pyproject.toml` — 3-line dependency change; no code path findings
