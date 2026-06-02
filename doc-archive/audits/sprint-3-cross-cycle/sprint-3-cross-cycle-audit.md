<!-- ARCHIVED from audit/sprint-3-cross-cycle @ c072c56, original date 2026-05-27. Conclusion: S3-AUDIT-001 BLOCKER + S3-AUDIT-002/003/004 HIGH all closed in Sprint 3 audit-fix cycle (7b47376, be74f4f); recorded in DECISIONS.md DE-S3-001/002 and memory/project_sprint_3_complete.md. -->
# Sprint 3 Cross-Cycle Audit
**Branch:** `audit/sprint-3-cross-cycle` (forked from `plan/finalist-a-scaffold` @ `9d36031`)
**Date:** 2026-05-27
**Auditor:** critic (Sprint 3 cross-cycle gatekeeper)
**Scope:** 14 categories (8 base + 6 Sprint-3-specific)
**Method:** Static analysis with call-path verification per `feedback_audit_findings_need_call_path_verification`. Each grep finding traced to its consumers before classification.

---

## Executive verdict

| Severity | Count |
|----------|-------|
| BLOCKER  | 1     |
| HIGH     | 4     |
| MEDIUM   | 4     |
| LOW      | 2     |
| **Total**| **11**|

**Overall gate decision: BLOCK.**

Sprint 3 Stream A (port-decision-math deprecation) is COMPLETE and CORRECT — the apex execution path, autotuner port-mode branch, and engine module decision functions are gone; only display helpers remain. Sprint 3 Stream B (advisor producers + UI) is INCOMPLETE: the Overfitting Conscience producer is wired in autotuner.py but its `subject_id` is permanently broken because the autotune_runs row id is never propagated end-to-end (S3-AUDIT-001 BLOCKER). The Divergence Explainer producer module exists with full tests but is never invoked from production code (S3-AUDIT-002 HIGH). The Indicator-3 monotonic-drift detection in Overfitting Conscience is dead because `prior_runs` is never supplied at the call site (S3-AUDIT-003 HIGH). The /api/advisor-observations symphony filter cannot find any OC/DE rows because the route filters by user-supplied symphony name but producers persist autotune_runs.id (an INTEGER PK) (S3-AUDIT-004 HIGH). Three orphaned port-decision modules (port_aggregator.py, port_selector.py, engine/multi_cycle.py) remain in the tree with no production consumers (S3-AUDIT-005 MEDIUM).

The CVaR-divergence REJECT wall is INTACT — no signed-divergence quantity is computed, persisted, or rendered anywhere in production code; the only `divergence`-named field on advisor observations is a defensive forbidden-key list in tests. The port-level decision absence is FULLY VERIFIED across alpha_bot_execution.py, autotuner.py, math_engine.py, app.py, and engine/. Spec Critic propagation works because its subject_id is the bundle_hash (which the user-supplied filter does not interrogate, so the path is structurally consistent within its own subject_type).

---

## Finding index

| ID | Severity | Category | Subject |
|----|----------|----------|---------|
| S3-AUDIT-001 | **BLOCKER** | 11 Advisor propagation E2E | OC observations persisted with `subject_id="0"` because autotune_runs.id never flows to the producer |
| S3-AUDIT-002 | HIGH | 10 Advisor producer invocation | `run_divergence_explainer` exists but is NEVER invoked from any production call site |
| S3-AUDIT-003 | HIGH | 10 Advisor producer invocation | OC Indicator-3 (monotonic drift detection) is inert in production — `prior_runs` is never supplied |
| S3-AUDIT-004 | HIGH | 11 Advisor propagation E2E | `/api/advisor-observations?symphony_id=<id>` filter never returns OC/DE rows (subject_id schema mismatch) |
| S3-AUDIT-005 | MEDIUM | 9 Port-decision absence | `port_aggregator.py`, `port_selector.py`, `engine/multi_cycle.py` survive as dead modules |
| S3-AUDIT-006 | MEDIUM | 10 Advisor producer invocation | Spec Critic and Overfitting Conscience calls in autotuner are not wrapped — a producer exception crashes the autotune cycle |
| S3-AUDIT-007 | MEDIUM | 6 Error-state propagation | `_oc_run["id"] = 0` silent fallback corrupts audit trail when `get_latest_autotune_run` would return None |
| S3-AUDIT-008 | MEDIUM | 11 Advisor propagation E2E | `_ADVISOR_ROLES` includes `"NARRATOR"` but Narrator is deferred per Sprint 3 scope — adds a permanently-empty role to every UI fetch |
| S3-AUDIT-009 | LOW | 1 Test hygiene | xfail tests `tests/execution/test_port_dispatch_removal.py:661–704` for port_aggregator / port_selector remain XPASS — modules still exist, xfails do not trigger |
| S3-AUDIT-010 | LOW | 11 Advisor propagation E2E | `/api/advisor-observations` issues 3 separate DB queries per symphony filter (autotune_run, spec_bundle, cvar_diagnostic) where 1 OR clause would suffice |
| S3-AUDIT-011 | LOW | 8 Worker dispatch correctness | `tests/portmode/` retains 8 test files post-removal; only `test_drawdown_degenerate_inputs` and `test_port_state_schema` exercise surviving surfaces |

---

## Findings

### S3-AUDIT-001 [BLOCKER] [11 Advisor propagation E2E] OC observations persisted with broken subject_id

**File:** `autotuner.py:1747-1758` + `database.py:407-484` + `database.py:507-546`
**Reproducer:**
1. The autotuner calls `database.save_autotune_run(...)` at `autotuner.py:1713-1736` — this function does NOT return the inserted row's id (no `cursor.lastrowid` capture; `save_autotune_run` returns `None` at `database.py:484`).
2. The autotuner then constructs `_oc_run = {"id": 0, ...}` at `autotuner.py:1747-1754` and attempts to repair it via `get_latest_autotune_run(normalized_name)` at line 1755.
3. `get_latest_autotune_run` uses `_AUTOTUNE_RUNS_SELECT` (`database.py:507-513`) which projects ONLY 14 columns: `run_timestamp, symphony_id, oos_alpha, train_alpha, baseline_decision, fallback_oos_alpha, default_oos_alpha, selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe, math_mode, account_id, sortino_sentinel_pct`. **The PK `id` column is NEVER selected.**
4. `_autotune_run_row_to_dict` at `database.py:487-504` maps those 14 columns to keys — no `"id"` key is produced.
5. At `autotuner.py:1757`: `_oc_run["id"] = _latest_run.get("id", 0)` — `.get("id", 0)` falls through to the default `0` because `_latest_run` has no `"id"` key.
6. `run_overfitting_conscience(_oc_run, ...)` → `compute_overfitting_conscience_observation` reads `run_id = autotune_run["id"]` (= 0) and emits `subject_id = str(0) = "0"`.
7. Every OC observation row persisted to `advisor_observations` has `subject_id = "0"` — independent of the autotune_run it describes.

**Consequence:** The audit trail is structurally broken end-to-end. Multiple OC observations for different autotune_runs collapse onto the same logical key (`subject_type="autotune_run", subject_id="0"`). The /api/advisor-observations filter-by-symphony-id case (`app.py:2440-2452`) calls `get_advisor_observations_for_subject(subject_type="autotune_run", subject_id=symphony_id)` — but the stored subject_id is "0", never `symphony_id`. **The producer→reader→UI path is broken; the operator cannot trace an OC observation back to the autotune_run it describes.**

**Suggested fix scope:** Two-line repair:
- Modify `save_autotune_run` (`database.py:407-484`) to capture `cursor.lastrowid` and return it.
- Modify `autotuner.py:1713-1758` so the return value is captured (`_inserted_id = database.save_autotune_run(...)`) and propagated directly into `_oc_run["id"] = _inserted_id`, eliminating the read-after-write lookup entirely.
- Add a regression test that asserts `subject_id != "0"` after a real `run_autotuner` cycle on a fresh DB.

**Confidence:** HIGH. Verified by reading every link in the chain (insert path, read path, accessor projection, dict mapping, producer compute, route filter).

---

### S3-AUDIT-002 [HIGH] [10 Advisor producer invocation] Divergence Explainer is never invoked from production

**File:** `advisors/divergence_explainer.py:148-198` (module) + grep across all production files
**Reproducer:** Running `grep -rn "run_divergence_explainer\|divergence_explainer" --include="*.py"` in the repo finds only:
- `advisors/divergence_explainer.py` (the module itself)
- `tests/ai_advisor/test_divergence_explainer.py` (unit tests)

`grep -n "divergence\|SECOND_WINDOW" autotuner.py alpha_bot_execution.py app.py` returns only documentation comments and pre-existing `shadow_divergence` references (unrelated M1F feature). **There is no production call site for `run_divergence_explainer`.** Compare to OC (called at `autotuner.py:1758`) and SC (called at `autotuner.py:1324`).

**Consequence:** Per project memory `[[project_sprint_3_scope_and_team]]`, the Divergence Explainer is one of three Phase-1 producers required by Sprint 3 Stream B. The module is fully implemented, the feature flag (SECOND_WINDOW_CVAR_ENABLED) is wired, the tests are present, but the producer never runs in production. Even with the feature flag off, the spec dictates that a NOT_APPLICABLE row should be written per-cycle for the audit trail (`divergence_explainer.py:100-109`). No rows of any kind are written because the module is never called. Sprint 3 Stream B is INCOMPLETE.

**Suggested fix scope:** Add a call site. Two candidate locations:
- After OC at `autotuner.py:1758` — write one DE observation per autotune_run (mirrors OC cadence; simplest).
- Per-cycle in `alpha_bot_execution.py` after CVaR diagnostic write — matches the dispatch brief language "per-cycle on alpha_bot_execution".
The dispatch brief is ambiguous on placement (states "autotuner.run_autotuner ... AND/OR per-cycle on alpha_bot_execution"). PM decision required on cadence, but a call site MUST exist somewhere. Recommend the autotuner site for consistency with the other two producers and to avoid blocking the 1-minute execution path.

**Confidence:** HIGH. Negative-grep verified; no aliasing of the function name observed.

---

### S3-AUDIT-003 [HIGH] [10 Advisor producer invocation] OC Indicator-3 (operator drift) is inert in production

**File:** `autotuner.py:1758` + `advisors/overfitting_conscience.py:103-113, 174-187`
**Reproducer:** `advisors/overfitting_conscience.py:174-187` defines `run_overfitting_conscience(autotune_run, ledger_rows, prior_runs=None)`. The autotuner call site at `autotuner.py:1758` is `_oc.run_overfitting_conscience(_oc_run, _oc_ledger_rows)` — only 2 positional args; `prior_runs` defaults to None.

Inside the compute function (`overfitting_conscience.py:103-113`):
```python
same_symphony_prior = [
    r for r in (prior_runs or [])
    if r.get("symphony_id") == symphony_id
]
if len(same_symphony_prior) >= 2:
    ...
    drift_detected = all(...)
```

When `prior_runs is None`, `same_symphony_prior = []`, the `if len(...) >= 2` guard never fires, and `drift_detected` stays `False`. The Indicator-3 WATCH floor at line 125 (`if drift_detected and verdict == "CLEAR": verdict = "WATCH"`) is never reached.

**Consequence:** Per the producer docstring and the Sprint 3 dispatch brief (`overfitting_conscience.py:10-13` Indicator-3), monotonically growing S_count across consecutive same-symphony runs is a documented Sprint-3 advisory signal. It is structurally inert. The operator gets no early warning of drift — exactly the failure mode the Conscience exists to detect (`[[project_cvar_divergence_validation_wall]]` lineage: detector-style indicators are the entire premise here).

**Suggested fix scope:** At `autotuner.py:1755-1758` fetch prior runs via `advisor_ro_query` before calling the producer:
```python
_oc_prior = database.advisor_ro_query(
    "SELECT id, symphony_id, s_count FROM autotune_runs "
    "WHERE symphony_id = ? AND id != ? ORDER BY run_timestamp ASC",
    (normalized_name, _oc_run["id"]),
)
_oc.run_overfitting_conscience(_oc_run, _oc_ledger_rows, prior_runs=_oc_prior)
```
Tie this to the S3-AUDIT-001 fix (prior_runs must filter out the current row, which requires the just-inserted id to be known). Add a test that constructs N prior runs with monotonically growing s_count and asserts verdict is WATCH (not CLEAR).

**Confidence:** HIGH. Verified at producer call site and producer compute body.

---

### S3-AUDIT-004 [HIGH] [11 Advisor propagation E2E] Symphony-filter route returns zero rows for autotune_run observations

**File:** `app.py:2438-2460` + `advisors/overfitting_conscience.py:162` + `advisors/divergence_explainer.py:104`
**Reproducer:** The route `/api/advisor-observations?symphony_id=<id>` filters by `subject_id == symphony_id`:
```python
rows = database.get_advisor_observations_for_subject(
    subject_type="autotune_run",
    subject_id=symphony_id,
)
rows += database.get_advisor_observations_for_subject(
    subject_type="spec_bundle",
    subject_id=symphony_id,
)
rows += database.get_advisor_observations_for_subject(
    subject_type="cvar_diagnostic",
    subject_id=symphony_id,
)
```

But:
- OC writes `subject_id=str(run_id)` where `run_id` is `autotune_runs.id` (INTEGER PK), not the symphony name.
- DE (when invoked) writes `subject_id=str(run_id)` likewise.
- SC writes `subject_id=str(spec_bundle_id)` which is the bundle_hash, not the symphony name.
- No producer writes `subject_type="cvar_diagnostic"`.

The user passes a symphony name (e.g. `"DefensiveAlpha"`). The filter looks for rows where subject_id equals that symphony name. **No producer-written row will ever match.**

**Consequence:** The symphony-filter view in /ai-advisor is silently empty even when observations exist. Sprint 3 dispatch brief states "New section displays advisor_observations rows for the selected symphony (or globally if no symphony filter)." The "for the selected symphony" path is structurally non-functional. Note this is independent of S3-AUDIT-001 — even if subject_id were repaired to the correct integer id, filtering by symphony name still wouldn't match.

**Suggested fix scope:** Two-step join required. The route must (a) resolve `symphony_id → list[autotune_runs.id]` and `symphony_id → list[spec_bundles.bundle_hash]`, then (b) query `advisor_observations` with those resolved keys. Alternative: extend `advisor_observations` schema (migration 025+) with a denormalized `symphony_id` column that producers populate, making symphony-filter a one-shot WHERE. Recommend the denormalized column — keeps reads simple and matches the operator's mental model. Add a regression test that POSTs N producer observations across 2 symphonies then asserts the route returns the correct subset.

**Confidence:** HIGH. Verified by tracing every producer's subject_id and the route's filter.

---

### S3-AUDIT-005 [MEDIUM] [9 Port-decision absence] Orphaned port-decision modules survive

**File:** `port_aggregator.py` (12,741 bytes), `port_selector.py` (13,212 bytes), `engine/multi_cycle.py` (full file)
**Reproducer:** Running `grep -rn "from port_aggregator\|from port_selector\|import port_aggregator\|import port_selector" --include="*.py"` in the repo finds:
- `engine/multi_cycle.py:17: from port_selector import select_symphony_with_mc_gate, composition_hash as _composition_hash`
- Various test files under `tests/portmode/`

Running `grep -rn "from engine.multi_cycle\|import multi_cycle\|engine.multi_cycle" --include="*.py"` finds zero production imports of `multi_cycle`. The chain `engine/multi_cycle.py → port_selector.py → port_aggregator.py` is provably dead: no production code outside the chain imports the chain's head.

**Consequence:** Per `[[project_port_level_deprecation_directive]]`: "Sprint 3 collapses to symphony-level only. ... Port-mode autotuner objective ... exit_triggers_port decision math ... Autonomous perform_account_liquidation ... port_state consumers driving decisions ... Portmode-aware branches in alpha_bot_execution.py and math_engine.py" are listed for removal. While the Sprint-3 manifest (`docs/audit/sprint-3-port-removal-manifest.md` §3 SITE-C1) explicitly removed `engine/dual_altitude.py` and four functions in `engine/exit_authority.py`, it did NOT name `multi_cycle.py`, `port_aggregator.py`, or `port_selector.py`. They remain as pure-function modules with no production consumer. Sprint 3 audit category #9 mandates "no autonomous port-level decision math". These modules carry the decision-math primitives (`aggregate_to_port`, `select_symphony_with_mc_gate`) even though no caller invokes them.

This is MEDIUM rather than HIGH because:
- No execution path reaches the code — there is no autonomous trigger.
- Tests still exercise the surfaces, so deletion would require coordinated test cleanup.
- The modules carry no runtime hazard in their current dormant state.

But MEDIUM rather than LOW because:
- Surface area for re-introduction. A future contributor reading `port_selector.py` may reflexively wire it back in. The deprecation directive's spirit is that these modules should not exist.
- The xfail tests at `tests/execution/test_port_dispatch_removal.py:661-704` document the intent that port_aggregator and port_selector "will not be importable after SITE-A2 removal" — they remain importable today.

**Suggested fix scope:** Sequential REMOVE cycle:
1. Delete `engine/multi_cycle.py` (zero production consumers).
2. Delete `port_selector.py` and `port_aggregator.py` (only multi_cycle imports them; that's gone after step 1).
3. Delete or migrate tests under `tests/portmode/` that exercise removed surfaces.
4. The xfails at `tests/execution/test_port_dispatch_removal.py:661–704` then trigger correctly (importerror → xfail passes).

**Confidence:** HIGH. Verified by negative-grep on all imports of the modules in question.

---

### S3-AUDIT-006 [MEDIUM] [10 Advisor producer invocation] Unwrapped advisor calls can crash the autotune cycle

**File:** `autotuner.py:1324` (Spec Critic), `autotuner.py:1758` (Overfitting Conscience)
**Reproducer:** Both call sites are bare:
```python
_sc.run_spec_critic(stored_hash, _sc_facets_rows)
...
_oc.run_overfitting_conscience(_oc_run, _oc_ledger_rows)
```
No try/except. Both producers document KeyError on missing mandatory keys (`spec_critic.py` reading row fields, `overfitting_conscience.py:71` reading `autotune_run["id"]`). Both also call `database.insert_advisor_observation` which opens a connection — sqlite3 errors (lock, full disk) propagate.

**Consequence:** Per Sprint 3 dispatch brief, advisor observations are "advisory only — never moves money". Their failure should never break the autotune cycle. Today, a malformed row, a transient DB lock during the OC/SC call, or a producer-side KeyError will raise out of `run_autotuner` and abort the cycle. The autotuner is invoked weekly (not the 1-minute execution path), so the blast radius is bounded — but a cycle abort means no fresh autotune_runs row, no spec_bundle freshness, and the dashboard's "Recent Autotune Runs" stays stale until manual intervention.

**Suggested fix scope:** Wrap both calls:
```python
try:
    _sc.run_spec_critic(stored_hash, _sc_facets_rows)
except Exception as e:
    logger.warning("Spec Critic observation failed (advisory only): %s", e)
```
Same pattern for OC at line 1758. Add a regression test injecting a malformed `_oc_run` and asserting the autotune cycle still saves the autotune_run and proceeds.

**Confidence:** HIGH. Direct read of call sites.

---

### S3-AUDIT-007 [MEDIUM] [6 Error-state propagation] Silent fallback `_oc_run["id"] = 0` corrupts the audit trail

**File:** `autotuner.py:1747-1757`
**Reproducer:** The construction at line 1747 starts with `"id": 0` — a sentinel. The lookup at line 1755 (`_latest_run = database.get_latest_autotune_run(normalized_name)`) is wrapped in `if _latest_run is not None`. If the lookup returns None (e.g. transient DB issue, schema drift, future race condition), the sentinel `0` survives. The producer at `overfitting_conscience.py:71` reads `autotune_run["id"]` without validating it's non-zero. Observations are silently written with `subject_id="0"`.

**Consequence:** Distinct from S3-AUDIT-001 (which observes that the lookup ALWAYS yields 0 today because of a missing column projection). Even if S3-AUDIT-001 is fixed, the construct-then-overwrite pattern still has a silent failure mode: the producer accepts `id=0` as valid. Defense-in-depth requires the producer to reject `id=0`.

**Suggested fix scope:** Pair with S3-AUDIT-001 fix (return `lastrowid` from `save_autotune_run`). Additionally, raise in `compute_overfitting_conscience_observation` when `autotune_run.get("id", 0) in (0, None)` — fail-noisy instead of fail-silent. This protects against future regressions of the same shape.

**Confidence:** HIGH. Read of fallback logic and producer.

---

### S3-AUDIT-008 [MEDIUM] [11 Advisor propagation E2E] `_ADVISOR_ROLES` includes deferred `"NARRATOR"`

**File:** `app.py:2415-2420`
**Reproducer:**
```python
_ADVISOR_ROLES = [
    "OVERFITTING_CONSCIENCE",
    "SPEC_CRITIC",
    "DIVERGENCE_EXPLAINER",
    "NARRATOR",
]
```

Per `[[project_sprint_3_scope_and_team]]` Stream B §4: "Regime & Decision Narrator — DEFERRED to a future cycle. Phase-1 has no parameter drift to narrate". No producer writes `advisor_role="NARRATOR"`. Every loop over `_ADVISOR_ROLES` (`app.py:2201-2206`, `app.py:2463-2468`) issues a DB query that returns zero rows.

**Consequence:** Two redundant queries per /ai-advisor page render and per /api/advisor-observations call. Pre-fetch performance overhead is low; the issue is the encoded promise — a contributor reading this list expects a NARRATOR producer exists. It does not. Documentation drift.

**Suggested fix scope:** Either:
- (a) Remove `"NARRATOR"` from `_ADVISOR_ROLES` until a producer ships, OR
- (b) Add an inline comment: `# NARRATOR — deferred per Sprint 3 scope; entry preserved so the role enum stays stable across DB rows when the producer ships in a later sprint`.

Recommend (b) — keeping the enum stable across schema lifetime is mildly preferable to a removal-and-readd churn.

**Confidence:** HIGH. Direct read.

---

### S3-AUDIT-009 [LOW] [1 Test hygiene] Pre-deletion xfails remain XPASS after Sprint-3 close

**File:** `tests/execution/test_port_dispatch_removal.py:661-704`
**Reproducer:** Four xfails declare imports will fail once port_aggregator / port_selector / dual_altitude / exit_authority decision-funcs are removed:
```python
@pytest.mark.xfail(
    reason="port_aggregator.aggregate_to_port will not be importable after SITE-A2 removal",
    strict=False,
)
def test_port_aggregator_import_fails_after_removal(self):
    from port_aggregator import aggregate_to_port  # noqa: F401
    pytest.fail(...)
```

Today:
- `port_aggregator.aggregate_to_port` is still importable (module still exists per S3-AUDIT-005) → import succeeds → `pytest.fail(...)` triggers → xfail catches → XPASS recorded.
- `engine.dual_altitude.initialize_port_state_if_absent` is NOT importable (module deleted) → ImportError → xfail catches → PASS recorded.
- `engine.exit_authority.get_exit_authority` is NOT importable (function removed) → ImportError → xfail catches → PASS recorded.

So 2 of 4 are now correct GREEN xfails; 2 are XPASS placeholders awaiting S3-AUDIT-005 fix.

**Consequence:** Test suite passes (`strict=False` permits XPASS), but the xfail-as-deletion-tripwire pattern is half-armed. When S3-AUDIT-005 is fixed, these tests should auto-flip to PASS without source edits — that is the design. Currently the pattern works as documented; no action needed until S3-AUDIT-005 is resolved.

**Suggested fix scope:** Resolved automatically when S3-AUDIT-005 is fixed. Mark this finding INFORMATIONAL — no standalone action needed.

**Confidence:** HIGH.

---

### S3-AUDIT-010 [LOW] [11 Advisor propagation E2E] Symphony-filter route issues 3 DB queries instead of 1

**File:** `app.py:2440-2452`
**Reproducer:**
```python
rows = database.get_advisor_observations_for_subject(
    subject_type="autotune_run", subject_id=symphony_id,
)
rows += database.get_advisor_observations_for_subject(
    subject_type="spec_bundle", subject_id=symphony_id,
)
rows += database.get_advisor_observations_for_subject(
    subject_type="cvar_diagnostic", subject_id=symphony_id,
)
```

Three RO connections opened, three SELECTs, three close()s. Plus a Python-side dedup pass over `seen: set`.

**Consequence:** Minor cost overhead per request (3x query latency, 3x connection churn). At /api/advisor-observations volumes (operator-triggered, low frequency) this is not a runtime hazard. Code clarity suffers — the intent (a UNION) is hidden in the imperative `+=` accumulation. A future contributor adding a fourth subject_type will likely paste in a fourth block rather than refactoring.

**Suggested fix scope:** Coordinate with S3-AUDIT-004 fix. When the symphony-filter is repaired to actually return rows (via a denormalized symphony_id column or two-step resolution), the underlying query should be ONE SELECT with an IN clause or single denormalized predicate. Defer pure-style improvement until S3-AUDIT-004 is in flight.

**Confidence:** HIGH.

---

### S3-AUDIT-011 [LOW] [8 Worker dispatch correctness] `tests/portmode/` retains 8 files post-removal

**File:** `tests/portmode/__init__.py`, `tests/portmode/conftest.py`, `tests/portmode/test_api_state_route_additive_fields.py`, `tests/portmode/test_drawdown_degenerate_inputs.py`, `tests/portmode/test_port_settings_cleanup.py`, `tests/portmode/test_port_state_schema.py`, `tests/portmode/test_settings_exit_authority_route.py`, `tests/portmode/test_settings_restart_notice.py`
**Reproducer:** The Sprint-3 manifest §6 enumerated 22 portmode test files; 14 are correctly deleted (test_autotuner_portmode, test_dual_altitude_state, test_exit_authority, test_port_aggregator, test_port_selector, test_port_signal, test_mc_sanity_gate, test_port_state_exit_lifecycle, test_composition_change_reset, test_per_account_params, test_port_telemetry, test_dual_altitude_dashboard, test_derive_target_reduction_unknown_reason, test_tie_epsilon_docstring, test_multi_cycle_convergence). 8 survive. Of those 8:

- `test_api_state_route_additive_fields.py` — KEEP (verifies SITE-D1/D2 display)
- `test_drawdown_degenerate_inputs.py` — KEEP (pure-math display layer)
- `test_port_state_schema.py` — KEEP (schema verification for retained table)
- `test_settings_exit_authority_route.py` — KEEP per AX-1/AX-2 resolution
- `test_settings_restart_notice.py` — KEEP per AX-2 resolution
- `test_port_settings_cleanup.py` — Sprint-3 NEW test (port-settings cleanup confirmation)

That accounts for 6 of 8 — `__init__.py` and `conftest.py` are infrastructure. So no orphan test files; this is INFORMATIONAL.

**Consequence:** None — Sprint-3 manifest §6 closure is complete.

**Suggested fix scope:** None. Mark INFORMATIONAL.

**Confidence:** HIGH.

---

## Scope coverage — files audited in this review

- `alpha_bot_execution.py` — full grep for port_state / exit_authority / autonomous calls; clean
- `autotuner.py` — full grep for portmode + advisor invocation sites; lines 78-90, 815-905, 1146-1220, 1310-1330, 1700-1760 read in detail
- `app.py` — full grep for engine.exit_authority / engine.dual_altitude / port-decision; lines 645-720, 1800-1910, 2040-2070, 2180-2475 read in detail
- `math_engine.py` — confirmed clean of any signed-divergence field via target grep
- `database.py` — lines 407-620, 803-928, 1495-1620 read in detail; `_AUTOTUNE_RUNS_SELECT` confirmed to omit `id`
- `engine/exit_authority.py` — full file read; decision functions confirmed removed
- `engine/dual_altitude.py` — confirmed deleted
- `engine/multi_cycle.py`, `port_aggregator.py`, `port_selector.py` — confirmed orphaned (no production consumers)
- `advisors/overfitting_conscience.py`, `advisors/spec_critic.py`, `advisors/divergence_explainer.py` — full reads
- `templates/ai_advisor.html` — full structural read; Jinja2 escaping confirmed
- `migrations/` — index review; migration 017 (advisor_observations) read in full; 020 column write closure verified against ARCH-001
- `tests/ai_advisor/` — directory inventory and target grep on subject_id assertions
- `tests/portmode/` — directory inventory vs Sprint-3 manifest §6
- `tests/execution/test_port_dispatch_removal.py` — xfail pattern verified

## Source-of-truth corroboration

- `[[project_cvar_divergence_validation_wall]]` — CVaR-divergence REJECT wall intact; verified no production code computes or persists a signed divergence quantity.
- `[[project_port_level_deprecation_directive]]` — Port-level decision math removal verified across alpha_bot_execution.py / autotuner.py / app.py / engine/; orphaned modules flagged at S3-AUDIT-005.
- `[[project_sprint_3_scope_and_team]]` — Stream A complete; Stream B INCOMPLETE per S3-AUDIT-001/002/003/004.
- `[[project_eut_cvar_migration_council_verdict]]` — Harden-don't-migrate posture intact; CRRA-EU + CVaR diagnostic surfaces verified.
- `docs/audit/sprint-2-cross-cycle-audit.md` — CRRA-001 (U-transform applied at `autotuner.py:868-893`), NEFF-001 (`compute_n_effective` wired at `autotuner.py:1555`), ARCH-001 (EUT columns passed to `save_autotune_run` at `autotuner.py:1730-1735`), ARCH-002 (inline comment present at `database.py:916-921`) — all CLOSED, no regression.

## Verdict summary

**BLOCK Sprint 3 close.** Repair S3-AUDIT-001 + S3-AUDIT-002 + S3-AUDIT-003 + S3-AUDIT-004 before declaring the build complete. The other 7 findings are MEDIUM/LOW and can be addressed in a small fix-pass cycle alongside the BLOCKER repair. The Sprint 3 port-decision deprecation work (Stream A) is correctly executed; the advisor producer wiring (Stream B) is structurally broken in 4 places and a paper deliverable until those repairs land.
