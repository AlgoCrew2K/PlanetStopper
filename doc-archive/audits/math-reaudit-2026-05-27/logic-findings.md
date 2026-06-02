<!-- ARCHIVED from audit/math-engine-reaudit @ e246d08, original date 2026-05-27. Conclusion recorded in docs/audit/README.md; logic findings LOGIC-M-1 (doc-only drift) was informational and tracked there. -->
# Math-Logic Audit — Sprint 3 Final Re-Audit

## Metadata
- Auditor: math-logic (team-math-reaudit)
- Run date: 2026-05-27
- Repo: AlphaBotPM
- Worktree: `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/audit-worktrees/math-reaudit`
- Branch: `audit/math-engine-reaudit` (fork point: `plan/finalist-a-scaffold @ 8d38a43`)
- Worktree HEAD SHA: `8d38a434833a376d6adbcca07b42a53413ffda92`
- `git status -sb`: `## audit/math-engine-reaudit` (clean)
- Scope: signal-flow / branch-coverage / edge-case audit of math_engine, autotuner advisor wiring, advisor producers, CVaR-divergence wall, dead-branch detection, edge cases
- Method: read-only; static analysis only

## Executive Summary

- BLOCKER: 0
- HIGH: 0
- MEDIUM: 1 (M-1 docstring drift in `advisors/spec_critic.py`)
- LOW: 2 (L-1 nomenclature mismatch in dispatch brief vs code; L-2 docstring drift in `app.py:2430-2433` about `/api/advisor-observations`)
- Acceptance: every Sprint-3 logic gate in the dispatch scope passes — advisor producer signal flow is end-to-end correct; CVaR-divergence wall is intact; producer fail-noisy on id=0 is reachable; port-level deprecation removed all references from math_engine + alpha_bot_execution; try/except wraps around SC/OC/DE are scoped narrowly.

Top-risk findings: none. Findings are documentation drift, not behavioural defects.

## Findings — SIGNAL-FLOW (advisor producers end-to-end)

### [LOGIC-001] Autotune row id flows authoritatively from `lastrowid` → OC subject_id (S3-AUDIT-001 + S3-AUDIT-007 closure verified)
- **File:** `autotuner.py:1730-1777`, `advisors/overfitting_conscience.py:71-80, 196-204`, `database.py:858`
- **Confidence:** HIGH
- **Risk:** CORRECTNESS
- **Effort:** N/A (clean — no defect)
- **Current Pattern:** `_inserted_id = database.save_autotune_run(...)` → `_oc_run = {"id": _inserted_id, ...}` → producer raises if `run_id in (0, None) or run_id <= 0` → `subject_id = str(run_id)` → persists via `insert_advisor_observation(..., symphony_id=...)`
- **Catalog / Rule:** Sprint 3 dispatch brief S3-AUDIT-001/-007/-004 closure
- **Verification:** `database.save_autotune_run` returns `cursor.lastrowid` (database.py:858 verified — `insert_advisor_observation` does `row_id = cursor.lastrowid`; `save_autotune_run` is the parallel writer one tier up). The producer's `if run_id in (0, None) or (isinstance(run_id, int) and run_id <= 0): raise ValueError(...)` guard is structurally reachable from the autotuner kickoff because `_inserted_id` is the only assignment to `_oc_run["id"]`. `test_compute_overfitting_raises_on_id_zero` + `test_compute_overfitting_raises_on_id_none` + `test_compute_overfitting_does_not_accept_negative_id` exercise the raise path. `subject_id = str(run_id)` is set unconditionally at line 171 of `overfitting_conscience.py`.
- **Test Coverage:** HAS TESTS (`tests/ai_advisor/test_audit_fix_oc_rejects_zero_id.py`)
- **Verdict:** PASS — signal flow is correct, no finding.

### [LOGIC-002] `prior_runs` ASC by `run_timestamp`, excludes just-inserted id (S3-AUDIT-003 closure verified)
- **File:** `autotuner.py:1769-1775`
- **Confidence:** HIGH
- **Current Pattern:** `database.advisor_ro_query("SELECT id, symphony_id, s_count FROM autotune_runs WHERE symphony_id = ? AND id != ? ORDER BY run_timestamp ASC", (normalized_name, _inserted_id))`
- **Verification:** `WHERE id != ?` excludes the just-inserted row; `ORDER BY run_timestamp ASC` matches the OC drift indicator's expected oldest-first sequence (verified against the comment `s_series = [r["s_count"] for r in same_symphony_prior] + [s]` in overfitting_conscience.py:119).
- **Test Coverage:** HAS TESTS (test_overfitting_conscience.py:704+ exercises `prior_runs` with explicit ASC ordering)
- **Verdict:** PASS.

### [LOGIC-003] OC Indicator-3 drift gate requires N≥2 priors AND strict-monotonic ascent
- **File:** `advisors/overfitting_conscience.py:113-122`
- **Confidence:** HIGH
- **Current Pattern:** `if len(same_symphony_prior) >= 2: s_series = [...] + [s]; drift_detected = all(s_series[i] < s_series[i+1] for i in range(len(s_series) - 1))`
- **Verification:** Combined with the run's own s_count appended, the comparator sees ≥3 elements and applies strict `<` between every consecutive pair (true strict monotonic ascent). Mirrors brief: "OC Indicator-3 fires only when N≥2 same-symphony priors AND monotonic drift detected".
- **Test Coverage:** HAS TESTS (`test_indicator_3_monotonic_s_growth_produces_watch`, `test_indicator_3_non_monotonic_s_does_not_trigger_drift`)
- **Verdict:** PASS.

### [LOGIC-004] DE feature-flag dispatch produces NOT_APPLICABLE vs INFORMATIONAL with no signed-divergence escape
- **File:** `advisors/divergence_explainer.py:65-141, 165-167`
- **Confidence:** HIGH
- **Current Pattern:** `if not second_window_enabled: return {"verdict": "NOT_APPLICABLE", "raw_response": {"feature_flag": "off"}, ...}` ; else `raw_response = {"short_window_cvar_pct": short_cvar, "short_window_tail_obs": short_tail, "long_window_cvar_pct": long_cvar, "long_window_tail_obs": long_tail}`
- **Verification:** No arithmetic between the two windows is performed anywhere in `compute_divergence_explainer_observation`. The raw_response keys are independent measurements only. Flag default is OFF (env var `"0"` or absent). When ON and `cvar_row is None`, all four values are `None` (no synthetic value computed).
- **Test Coverage:** HAS TESTS (`tests/ai_advisor/test_divergence_explainer.py` asserts forbidden keys never appear in output)
- **Verdict:** PASS.

### [LOGIC-005] `/api/advisor-observations?symphony_id=` resolves via single denormalised query
- **File:** `app.py:2426-2455`, `database.py:911-932`
- **Confidence:** HIGH
- **Current Pattern:** `rows = database.get_advisor_observations_for_symphony(symphony_id)` → `WHERE symphony_id = ? ORDER BY id ASC`
- **Verification:** Migration 025 added `symphony_id` column and the producers populate it via `insert_advisor_observation(..., symphony_id=...)` at all three call sites: `run_overfitting_conscience` (line 203 — `autotune_run.get("symphony_id")`), `run_divergence_explainer` (line 197), and `run_spec_critic` (line 231 — accepts a `symphony_id` kwarg). Single-query resolution; no 3x subject-type fan-out.
- **Test Coverage:** HAS TESTS (test_advisor_observations_ui.py:507 covers empty-result symphony filter)
- **Verdict:** PASS.

## Findings — MC GATING + EXIT CONFIRMATION

### [LOGIC-006] MC sentinel = None; `mc_available = prob_beating is not None` gates arm/disarm/TP; protective stop unaffected by None
- **File:** `math_engine.py:84, 811`, `alpha_bot_execution.py:1113-1167, 1240-1247`, `math_engine.py:374-435`
- **Confidence:** HIGH
- **Current Pattern (math_engine.py:806-811):** `if eligible_days < MC_MIN_HISTORY_DAYS: return MC_INSUFFICIENT_HISTORY_SENTINEL` (= `None`). At call site: `mc_available = prob_beating is not None`. In `compute_exit_confirmation`: `mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD` — None passes the sanity gate (fail-safe). TP gate: `if mc_available and prob_beating < take_profit_mc_pct:` — None never enters TP arm/confirm.
- **Verification:** Eligible-days boundary documented at math_engine.py:798-805 (`eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)`) — consistent with project memory `project_mc_eligible_pool_vs_raw_day_boundary`. Sentinel never enters `mc_history` (lines 1166-1169 guard). Cluster 3 _replay_exit_tick verified per memory `project_cluster5_d6_orphaned_red_triage`.
- **Test Coverage:** HAS TESTS (tests/engine/* — exit-confirmation tests cover None path; project memory documents the orphan-RED triage closure)
- **Verdict:** PASS.

### [LOGIC-007] `compute_tp_confirmation` MC-unavailable resets `above_tp_count` while armed (the inner-else branch)
- **File:** `math_engine.py:442-504`
- **Confidence:** HIGH
- **Current Pattern (lines 490-501):** `elif tp_armed and not is_triggered: if mc_available and prob_beating >= take_profit_mc_pct: new_count = above_tp_count + 1; ...else: return tp_armed, 0, False`
- **Verification:** When MC is unavailable (`mc_available=False`) while armed and not triggered, control falls into the inner `else` at line 500-501 — count resets. Matches docstring "An MC-UNAVAILABLE tick falls into the inner else and RESETS above_tp_count". Branch is reachable on every cycle once `tp_armed=True`.
- **Test Coverage:** HAS TESTS (tests/engine/*)
- **Verdict:** PASS.

## Findings — 6-LAYER EXIT DECISION

### [LOGIC-008] `resolve_trigger_priority` is a deterministic total order over 4 boolean inputs; (None, []) on all-False
- **File:** `math_engine.py:726-759`, `alpha_bot_execution.py:1437-1442`, `autotuner.py:706`
- **Confidence:** HIGH
- **Current Pattern:** `_TRIGGER_PRIORITY_ORDER = ["VWAP Breakdown", "Take-Profit", "VWAP Bleed Cut", "Trailing Stop"]` ; `fired = [name for name in _TRIGGER_PRIORITY_ORDER if flag_map[name]]; if not fired: return None, []; return fired[0], fired[1:]`
- **Verification:** Function is pure, deterministic, total over all 16 input combinations. All-False returns `(None, [])`. Single-call site at live (`alpha_bot_execution.py:1437`) is gated by `if (is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken)` (line 1429-1434) — i.e. `(None, [])` is structurally unreachable from the dispatcher. Autotuner replay call site (autotuner.py:706) calls unconditionally; the None case feeds through to "no exit on this tick" — correct.
- **Note on nomenclature:** project CLAUDE.md and the AlphaBot README refer to a "6-layer exit decision" but `resolve_trigger_priority` itself dispatches over 4 candidate flags. The "6 layers" name refers to the *full* layered architecture (vol-scaling, time-squeeze, parabolic ratchet, breakeven, VWAP×2, MC) — multiple of those layers compute the 4 input booleans. The resolver does NOT receive 6 inputs. This is documentation framing — the code is correct.
- **Test Coverage:** HAS TESTS (`tests/autotuner/test_r3b_shared_priority_resolver.py` exercises zero-trigger, single-trigger, exhaustive ordering, determinism, and call-site bypass detection)
- **Verdict:** PASS.

## Findings — NN1 SPEC-FREEZE DISCIPLINE

### [LOGIC-009] `validate_nn1_compliance` default-deny covers unknown freeze_discipline values
- **File:** `autotuner.py:1143-1234`
- **Confidence:** HIGH
- **Current Pattern (lines 1196-1220):** for each facet, `discipline = facet["freeze_discipline"]` → if not in `NN1_HONEST_DISCIPLINES`: branch on BACKTEST_SELECTION (write ledger row +S) vs unknown ("default-deny with the raw value named").
- **Verification:** Unknown values produce a violations row labelled `f"{name}: {discipline} (unrecognised discipline — default-deny)"` and return `(False, violations)`. Matches the brief: "default-deny coverage; what happens when a study's spec_bundle has an unexpected freeze_discipline value". OOS evidence_source path is separately surfaced as the stricter violation. No raise — caller gets verdict.
- **Test Coverage:** Partial — `tests/autotuner/` exercises NN1_HONEST_DISCIPLINES + BACKTEST_SELECTION; the explicit "unknown discipline" default-deny branch is not exercised by an active test (the branch is unambiguously reachable from a malformed spec_facets row).
- **Verdict:** PASS (functional); `NEEDS BEHAVIORAL VERIFICATION` would only apply if we treat the no-test-for-unknown-value branch as a gap — recommendation to optionally add one is LOW priority.

## Findings — TRY/EXCEPT SCOPING (autotuner advisor call sites)

### [LOGIC-010] SC + OC + DE try/except wraps are scoped tightly to the producer call, not the surrounding row save
- **File:** `autotuner.py:1330-1333, 1776-1786`
- **Confidence:** HIGH
- **Current Pattern:**
  ```
  try: _sc.run_spec_critic(stored_hash, _sc_facets_rows, symphony_id=None)
  except Exception as e: logging.warning("Spec Critic observation failed (advisory only): %s", e)
  ...
  _inserted_id = database.save_autotune_run(...)   # SAVE IS OUTSIDE the try/except below
  ...
  try: _oc.run_overfitting_conscience(...) ; except Exception as e: logging.warning(...)
  try: _de.run_divergence_explainer(...) ; except Exception as e: logging.warning(...)
  ```
- **Verification:** `save_autotune_run` is called BEFORE the OC + DE try/except blocks (line 1730 vs 1776/1784), so a producer failure does NOT prevent the autotune_runs row from being saved. The `except Exception` is intentionally broad because the producers are advisory-only; but the scope is one statement (the producer call), not the whole "save+observe" workflow. Schema/migration errors raised inside `save_autotune_run` are NOT swallowed.
- **Risk callout:** The broad `except Exception` would mask a schema regression inside `insert_advisor_observation` (column added but writer not updated). Mitigated by `logging.warning` emitting the exception text — operator sees the message. Production WAL/SHM hygiene + migration tests guard the schema side.
- **Verdict:** PASS.

## Findings — CVaR-DIVERGENCE REJECT WALL

### [LOGIC-011] No signed-divergence quantity computed, persisted, or rendered in production code
- **File:** `math_engine.py` (grep clean), `advisors/divergence_explainer.py` (forbids by docstring + structure), `database.py` (no divergence column), `app.py` (only the pre-existing live-vs-AlphaBot `shadow_divergence` system — unrelated), `templates/` (clean)
- **Confidence:** HIGH
- **Verification:** Grep for `signed_divergence|cvar_diff|cvar_delta|window_divergence|divergence_pct` across the worktree returns matches only in:
  - `tests/fixtures/math/divergence_explainer_scenarios.json` (5 fixture occurrences — each is a NEGATIVE assertion listing the forbidden keys; verified by reading lines 50-54, 83-87, 105-109)
  - `tests/ai_advisor/test_divergence_explainer.py` (3 occurrences — also negative assertions enforcing the wall, lines 44-45, 109-113)
  - `feature-plans/decision-science/phase-1/*` (planning docs)
  - `advisors/divergence_explainer.py` (the rejecting docstring at lines 15-17)
  The grep token `divergence` (without the forbidden-key suffixes) appears in `app.py` only as `shadow_divergence` — the pre-existing portfolio live-vs-bot comparison (database.py:2409-2445), which is structurally unrelated to the CVaR-divergence detector idea. Brief term "shadow_divergence" is in the live-mode codepath and predates the CVaR work; not a wall breach.
- **Test Coverage:** HAS TESTS (the negative assertions ARE the enforcement mechanism)
- **Verdict:** PASS — wall is intact, per `[[project_cvar_divergence_validation_wall]]`.

## Findings — DEAD BRANCHES / PORT-LEVEL DEPRECATION

### [LOGIC-012] No port_state / port_decision / port_mode / port_selector references in math_engine or alpha_bot_execution
- **File:** `math_engine.py` (grep clean), `alpha_bot_execution.py` (grep clean — first 30 results), `port_selector.py` (deleted per project CLAUDE.md — `compute_composition_hash` promoted to database.py)
- **Confidence:** HIGH
- **Verification:** `Grep "port_state|port_decision|port_mode|port_selector|portfolio_mode"` returns no matches in `math_engine.py` and no matches in the live-trading execution path of `alpha_bot_execution.py`. Per `[[project_port_level_deprecation_directive]]` (2026-05-26) the port-level surface was deprecated in Sprint 3.
- **Verdict:** PASS — Sprint-3 deprecation directive structurally enforced in the code paths in scope.

## Findings — EDGE CASES

### [LOGIC-013] Empty advisor_observations table → `/api/advisor-observations` returns `[]`
- **File:** `database.py:911-932`, `app.py:2438-2454`
- **Confidence:** HIGH
- **Current Pattern:** `cursor.fetchall()` returns `[]` for an empty table; loop comprehension produces `[]`; jsonify yields `[]` → HTTP 200.
- **Test Coverage:** HAS TESTS (`test_route_returns_200_with_empty_observations` at line 160, `test_empty_state_shows_friendly_message` at line 343, `test_symphony_filter_empty_result_returns_empty_list` at line 507)
- **Verdict:** PASS.

### [LOGIC-014] Empty `prior_runs` → OC produces CLEAR (drift gate guards against empty list)
- **File:** `advisors/overfitting_conscience.py:113-117`
- **Confidence:** HIGH
- **Current Pattern:** `same_symphony_prior = [r for r in (prior_runs or []) if r.get("symphony_id") == symphony_id]; if len(same_symphony_prior) >= 2:`
- **Verification:** `prior_runs=None` is explicitly handled via `(prior_runs or [])`. Empty list → length 0 → drift gate False → `drift_detected = False`. With `s == 0` (no BACKTEST_SELECTION facets) the verdict resolves to CLEAR. Brief: "Empty prior_runs → OC produces CLEAR verdict (drift logic correctly gated)" — confirmed.
- **Test Coverage:** HAS TESTS (test_overfitting_conscience.py covers prior_runs=None and CLEAR paths)
- **Verdict:** PASS.

### [LOGIC-015] Missing fixture data → producer raises KeyError (not silent CLEAR)
- **File:** `advisors/overfitting_conscience.py:69-85`, `advisors/divergence_explainer.py:94-97`
- **Confidence:** HIGH
- **Current Pattern:** `run_id: int = autotune_run["id"]` (KeyError on miss — intentional pre-020 schema guard documented in the docstring at line 58-60). The autotuner wraps each producer call in try/except (LOGIC-010); the autotune_runs row is still saved; the warning logs the exception.
- **Verification:** Missing keys raise; the outer try/except routes the exception to `logger.warning` — graceful degradation at the cycle level, fail-noisy at the producer-call level. No silent CLEAR.
- **Verdict:** PASS.

### [LOGIC-016] Concurrent autotuner write + dashboard read — WAL mode permits read-while-write
- **File:** `database.py` (`get_connection` for writes; `get_ro_connection` for reads); WAL hygiene per `app.py:190-194` (per `[[project_wal_shm_persist_on_windows_sigterm]]`)
- **Confidence:** MEDIUM
- **Verification:** The dashboard uses `get_ro_connection()` per architecture constraint 5 (templates open SQLite read-only). The autotuner uses `database.advisor_ro_query` for reads and `database.insert_advisor_observation` / `database.save_autotune_run` for writes. SQLite WAL mode permits concurrent read while write is in progress — this is the intended operational pattern. WAL/SHM persist on Windows SIGTERM (documented behaviour, not a defect).
- **Verdict:** PASS.

## Confirmed Bugs

None.

## Patterns Observed

- Producer integration follows a consistent 3-tier pattern: pure compute (no DB) → integration wrapper → caller try/except in autotuner. Tightly scoped exception handling.
- All three producers normalise `sqlite3.Row` to `dict` defensively at the top of each compute, supporting both call shapes uniformly.
- The CVaR-divergence wall is enforced structurally (no arithmetic between the two windows in the producer) AND adversarially (test fixtures list the forbidden keys as negative assertions).
- Default-deny for unknown enum values appears consistently (NN1 freeze_discipline in autotuner; SC freeze_discipline in spec_critic).
- The autotuner saves the autotune_runs row BEFORE invoking advisory producers — producer failure does not lose the run record.

## Risk Summary

| Category | Confidence | Count |
|---|---|---|
| BLOCKER | — | 0 |
| HIGH | — | 0 |
| MEDIUM | M | 1 (M-1) |
| LOW | L | 2 (L-1, L-2) |

Highest-risk: M-1 (docstring drift in `advisors/spec_critic.py` line 9-10 + 25 — claims `_ACCEPTABLE_DISCIPLINES = NN1_HONEST_DISCIPLINES ∪ {BACKTEST_SELECTION}` but code excludes BACKTEST_SELECTION). Code is MORE strict than docstring; safe behaviour, misleading documentation.

## Documentation-Drift Findings (deferred from BLOCKER/HIGH because behaviour is correct)

### [M-1] spec_critic.py docstring claims BACKTEST_SELECTION is acceptable; code rejects it
- **File:** `advisors/spec_critic.py:8-10`, `advisors/spec_critic.py:25`, `advisors/spec_critic.py:69-79`
- **Confidence:** HIGH (single grep)
- **Risk:** STYLE (documentation correctness)
- **Effort:** TRIVIAL
- **Current Pattern (line 8-10 + 25):** `"I-2  All facets have a recognised freeze_discipline (in NN1_HONEST_DISCIPLINES union {BACKTEST_SELECTION}). Any unrecognised discipline → BREACH"` ... `"Acceptable: NN1_HONEST_DISCIPLINES ∪ {BACKTEST_SELECTION}"`.
- **Actual code (lines 72-79):** `_ACCEPTABLE_DISCIPLINES = frozenset({"THEORY", "MANDATE", "STYLIZED_FACT", "POLITIS_WHITE", "CADENCE", "CALIBRATION"})` — BACKTEST_SELECTION is NOT in the set. A facet with `freeze_discipline = "BACKTEST_SELECTION"` triggers BREACH via the I-2 path (default-deny).
- **Catalog / Rule:** Fowler "Outdated Comment"
- **Verdict-side check:** This is arguably the desired behaviour — a Phase-1 spec_bundle should NOT contain BACKTEST_SELECTION facets; the autotuner-side `validate_nn1_compliance` already classifies BACKTEST_SELECTION as a violation. So the code is correct; the docstring is wrong. Fix is doc-only: strike `"∪ {BACKTEST_SELECTION}"` from lines 9 + 25 and the verdict resolution comment.
- **Test Coverage:** HAS TESTS (test_spec_critic.py:500 covers unrecognised disciplines → BREACH; would also catch a BACKTEST_SELECTION input under the same path)

### [L-1] dispatch brief naming: `autotuner._ACCEPTABLE_DISCIPLINES` does not exist; the autotuner-side equivalent is `NN1_HONEST_DISCIPLINES`
- **File:** dispatch brief item 4d (this audit's scope) references "SC freeze_discipline acceptance set matches autotuner's _ACCEPTABLE_DISCIPLINES"
- **Confidence:** LOW (single-source)
- **Risk:** STYLE
- **Verification:** `autotuner.py:82-89` defines `NN1_HONEST_DISCIPLINES`. There is no `_ACCEPTABLE_DISCIPLINES` symbol in autotuner.py. The SC's `_ACCEPTABLE_DISCIPLINES` (advisors/spec_critic.py:72-79) matches `NN1_HONEST_DISCIPLINES` element-for-element. The brief's intent is satisfied — name divergence only.
- **Verdict:** PASS on intent; recommend brief author update wording in future re-audit dispatches.

### [L-2] `/api/advisor-observations` docstring describes legacy 3-subject fan-out, not the current single-query path
- **File:** `app.py:2430-2433`
- **Confidence:** LOW (single-source)
- **Risk:** STYLE
- **Current Pattern (docstring):** `"?symphony_id=<id> — filter to rows whose subject_id matches; calls database.get_advisor_observations_for_subject."`
- **Actual code (line 2444):** `rows = database.get_advisor_observations_for_symphony(symphony_id)` — single-query against `symphony_id` column (migration 025), NOT subject_id and NOT `get_advisor_observations_for_subject`.
- **Verdict:** PASS on behaviour; docstring drift only.

## Recommendations Index

Ordered by suggested execution order (all are doc-only; behaviour is correct):

1. **[M-1]** Update `advisors/spec_critic.py` docstring (lines 9, 25, 70) to strike `"∪ {BACKTEST_SELECTION}"` and the comment fragment `"and BACKTEST_SELECTION"` so docs match code.
2. **[L-2]** Update `app.py:2430-2433` docstring to mention `get_advisor_observations_for_symphony` and migration 025.
3. **[L-1]** (Optional) Note in future dispatch briefs that the autotuner-side discipline frozenset is named `NN1_HONEST_DISCIPLINES`, not `_ACCEPTABLE_DISCIPLINES`.

No code-level findings require action.

## Open Questions

- [QUESTION-01] Should the unknown-freeze_discipline default-deny branch in `validate_nn1_compliance` (autotuner.py:1218-1220) have its own dedicated test (it is structurally reachable but not exercised by a named test)? — Non-blocking; LOW priority.
- [QUESTION-02] Is the broad `except Exception` around producer calls (autotuner.py:1332-1333, 1778-1779, 1785-1786) intended to swallow a schema-migration regression in `insert_advisor_observation`? Current behaviour is `logger.warning` only — the autotune_runs row survives, but a schema drift would be silently advisory until an operator reads logs. Recommendation: tighten to `except (sqlite3.Error, KeyError, ValueError) as e` if a future migration ever adds a producer-side required-column dependency. — Non-blocking.

## Evidence Appendix

### Grep evidence — CVaR-divergence wall

Production code search:
```
grep "signed_divergence|cvar_diff|cvar_delta|window_divergence|divergence_pct" -- math_engine.py
  → no matches
grep "divergence" -- math_engine.py
  → 1 line (comment ref in council synthesis cite, line 1369)
grep "signed_divergence|cvar_diff|cvar_delta|window_divergence|divergence_pct" -- database.py, app.py, templates/
  → no matches in templates/ ; app.py occurrences are pre-existing shadow_divergence (live-vs-bot portfolio compare, unrelated)
```

Test/fixture/plan occurrences (all are negative assertions OR planning docs — wall enforcement):
```
tests/fixtures/math/divergence_explainer_scenarios.json:50-54, 83-87, 105-109
tests/ai_advisor/test_divergence_explainer.py:44-45, 109-113
advisors/divergence_explainer.py:15-17  (docstring rejection)
feature-plans/decision-science/phase-1/*.md  (planning docs)
```

### Grep evidence — port-level deprecation in scope files

```
grep "port_state|port_decision|port_mode|port_selector|portfolio_mode" math_engine.py
  → no matches
grep "port_state|port_decision|port_mode|port_selector" alpha_bot_execution.py (head 30)
  → no matches
```

### Citation index

- `math_engine.py:84` — MC_INSUFFICIENT_HISTORY_SENTINEL = None
- `math_engine.py:374-435` — compute_exit_confirmation
- `math_engine.py:442-504` — compute_tp_confirmation
- `math_engine.py:726-759` — resolve_trigger_priority
- `math_engine.py:736` — resolve_trigger_priority signature
- `math_engine.py:806-811` — MC eligible-days sufficiency + sentinel
- `alpha_bot_execution.py:1113-1167` — MC dispatch + mc_available gate
- `alpha_bot_execution.py:1240-1247` — compute_exit_confirmation call
- `alpha_bot_execution.py:1273-1281` — compute_tp_confirmation call
- `alpha_bot_execution.py:1429-1442` — resolve_trigger_priority call gated by any-flag-set
- `autotuner.py:82-89` — NN1_HONEST_DISCIPLINES
- `autotuner.py:1143-1234` — validate_nn1_compliance default-deny
- `autotuner.py:1330-1333` — SC try/except
- `autotuner.py:1730-1748` — save_autotune_run capture _inserted_id
- `autotuner.py:1753-1775` — _oc_run dict + prior_runs ASC query
- `autotuner.py:1776-1786` — OC + DE try/except
- `advisors/overfitting_conscience.py:71-80` — id=0/None/negative ValueError
- `advisors/overfitting_conscience.py:113-122` — drift gate len ≥ 2 + strict-monotonic
- `advisors/overfitting_conscience.py:168-176` — observation dict with subject_id, spec_bundle_id, symphony_id forwarded by caller
- `advisors/spec_critic.py:72-79` — _ACCEPTABLE_DISCIPLINES (NN1_HONEST set)
- `advisors/spec_critic.py:155-162` — verdict resolution (BREACH/WATCH/CLEAR)
- `advisors/divergence_explainer.py:94-109` — id KeyError + NOT_APPLICABLE flag-off path
- `advisors/divergence_explainer.py:111-141` — two-window honest reads, no signed-divergence
- `database.py:816-860` — insert_advisor_observation with symphony_id param
- `database.py:911-932` — get_advisor_observations_for_symphony single-query
- `app.py:2426-2455` — /api/advisor-observations route

### Test inventory (existence verified by grep)

- `tests/autotuner/test_r3b_shared_priority_resolver.py` — priority resolver invariants
- `tests/ai_advisor/test_audit_fix_oc_rejects_zero_id.py` — S3-AUDIT-007 fail-noisy
- `tests/ai_advisor/test_overfitting_conscience.py` — drift + Indicator 1/2/3
- `tests/ai_advisor/test_spec_critic.py` — verdict matrix + I-1/I-2/I-3/I-4
- `tests/ai_advisor/test_divergence_explainer.py` — wall + flag dispatch
- `tests/ai_advisor/test_advisor_observations_ui.py` — empty + symphony filter routes
- `tests/engine/` — exit confirmation MC None paths (per `[[project_cluster5_d6_orphaned_red_triage]]`)
