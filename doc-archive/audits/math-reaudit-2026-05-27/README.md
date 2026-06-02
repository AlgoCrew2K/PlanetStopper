<!-- ARCHIVED from audit/math-engine-reaudit @ e246d08, original date 2026-05-27. Conclusion recorded in docs/audit/README.md (OPTUNA-1/6 tracked as open findings); PERF-005/OPTUNA-6 closed by walk-forward overhaul cycles; see DECISIONS.md DE-WF-001/002/003 and memory/project_walk_forward_overhaul_complete.md. -->
# Math Engine Re-Audit — Sprint 3 Final (2026-05-27)

**Branch:** audit/math-engine-reaudit (forked from plan/finalist-a-scaffold @ 8d38a43)
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/audit-worktrees/math-reaudit`
**Worktree HEAD audited:** `8d38a434833a376d6adbcca07b42a53413ffda92`
**Auditors:** math-accuracy, math-performance, math-logic, math-optuna
**Auditor scope:** 3 binding gates — ACCURACY, PERFORMANCE, LOGIC — plus Optuna methodology

---

## Executive verdict

**Total findings: 25**

| Severity | Count |
|---|---|
| BLOCKER | 0 |
| HIGH | 3 |
| MEDIUM | 9 |
| LOW | 13 |

**Per-gate:**

| Gate | Status | BLOCKER | HIGH | MEDIUM | LOW |
|---|---|---|---|---|---|
| Accuracy | PASS | 0 | 0 | 1 | 2 |
| Performance | PASS (with action items) | 0 | 1 | 3 | 6 |
| Logic | PASS | 0 | 0 | 1 | 2 |
| Optuna methodology | PASS (with action items) | 0 | 2 | 4 | 3 |

**Overall: PASS** — no BLOCKER and no HIGH finding describes a correctness defect on the production action path. The three HIGH findings are (a) a hot-path redundant computation that does not change results (PERF-003), (b) Optuna sampler choice being implicit-default rather than pinned (OPTUNA-1), and (c) `n_jobs=-1` hardcoded with implicit `seed=None` violating project rule 5 (OPTUNA-6). All three are scheduled engine-audit lanes (scaffolded, not landed) or one-line ALPHA_BOT_EXECUTION edits — none block PM-complete on the Sprint-3 binding decisions.

Recommendation: PM-complete may close on the binding-decision surface. The three HIGH findings should be tracked as the head of the post-Sprint-3 engine-audit backlog (the scaffolded engine-audit plans already enumerate the fixes).

---

## Cross-gate themes

Patterns that surfaced in 2+ specialist gates:

### Theme 1 — Implicit Optuna constants at the main `create_study` call site (optuna + performance)
The optuna and performance gates both flag `autotuner.py:1500-1507` as the home of multiple un-pinned methodology constants: sampler family (TPE) is Optuna's implicit default, pruner is the implicit `MedianPruner` (silently inactive because no `trial.report` calls exist), `n_jobs=-1` is hardcoded, `seed` is unspecified (= None), and `n_trials=500` is an unnamed literal. Behaviour today is correct; the risk surface is forward-looking — a future PR could silently swap the TPE sampler (mis-calibrating the BHY c(N) Yekutieli correction) or activate the pruner (censoring the trial set). Engine-audit lanes `sampler-choice/`, `pruner-choice/`, `parallelism-reproducibility/`, `trial-floor-justification/` are scaffolded but not landed. The `run_calibration_sweep` site is correctly explicit on these knobs — main path inconsistent.

### Theme 2 — Sprint-3 producer surface (logic + performance + optuna)
The three Sprint-3 advisor producers (Overfitting Conscience, Spec Critic, Divergence Explainer) are confirmed to be (a) called only post-walk-forward from the EOD/Friday path (not on the 1-minute action loop — architecture constraint #1 intact, per logic + performance), (b) each issues exactly 1–2 parameterised SELECTs per symphony (no N+1, per performance), (c) wired correctly to the `lastrowid` flow with fail-noisy id=0/None guards (per logic LOGIC-001), (d) the `/api/advisor-observations?symphony_id=` route is a single denormalised SELECT after migration 025 (S3-AUDIT-004/010 closure verified by logic + performance). The N_effective additive accounting + BHY haircut wiring is correct (optuna §5, accuracy §"N_effective additive accounting").

### Theme 3 — CVaR-divergence REJECT wall (accuracy + logic)
Both gates independently confirm the wall holds. No signed-divergence number is computed, persisted, or surfaced anywhere on the production path. `advisors/divergence_explainer.py` outputs two independent CVaR window values (`short_window_cvar_pct`, `long_window_cvar_pct` + their `tail_obs` companions) with no arithmetic between them. The forbidden-key list (`signed_divergence`, `cvar_diff`, `cvar_delta`, `window_divergence`, `divergence_pct`) is enforced structurally in production and adversarially via negative-assertion test fixtures. `shadow_divergence` symbol in `app.py` is the pre-existing live-vs-AlphaBot comparison stream (unrelated to the rejected CVaR-divergence detector). Project memory `[[project_cvar_divergence_validation_wall]]` is structurally honoured.

### Theme 4 — Port-level deprecation completion (logic + accuracy implicit)
`math_engine.py` and the live-trading path of `alpha_bot_execution.py` contain zero references to `port_state | port_decision | port_mode | port_selector | portfolio_mode`. The 2026-05-26 user mandate (project memory `[[project_port_level_deprecation_directive]]`) is structurally complete on the math-engine surface. `compute_composition_hash` correctly promoted to `database.py`.

### Theme 5 — Documentation drift, code is the stricter / correct truth (logic + accuracy)
Two of the three logic findings (M-1, L-2) and the single accuracy MEDIUM (MATH-ACC-001) are documentation-drift items where the code behaves correctly and the docstring/brief telescopes or misstates the binding. Pattern: `advisors/spec_critic.py` docstring overstates the acceptable freeze-discipline set; the `/api/advisor-observations` docstring describes the legacy 3-subject fan-out; the dispatch brief's `tail_obs_count = floor(α·N)` phrasing omits the atom contribution. Each is doc-only; no behaviour to fix.

### Theme 6 — Hot-path defensive scans (performance)
Three performance findings (PERF-002, PERF-003, PERF-004) cluster around `historical_data` being processed redundantly on every per-cycle hot-path call: the dict-keys are sorted 4+ times per symphony per cycle, `_reject_non_finite_in_records` walks the full O(D × T) entries every call, and `calculate_20d_vol` is invoked twice per symphony per cycle (once in data phase, once in action phase, on the same cached data). Architecture constraint #1 ("no blocking I/O on the 1-minute path") is not breached, but redundant CPU on the hot path is the surface.

---

## All findings (sorted by severity)

| ID | Severity | Gate | Subject | File:line |
|---|---|---|---|---|
| OPTUNA-1 | HIGH | Optuna | Sampler is Optuna implicit default `TPESampler(seed=None)` at main call site; explicit in V1 sweep — c(N) calibrated to TPE dependency is un-pinned | `autotuner.py:1500-1507` |
| OPTUNA-6 | HIGH | Optuna | `n_jobs=-1` hardcoded (violates project rule 5); seed implicit `None` → non-deterministic by construction | `autotuner.py:1507` |
| PERF-003 | HIGH | Performance | Double `calculate_20d_vol` call per symphony per action-phase cycle; data-phase result discarded | `alpha_bot_execution.py:734, 1121` |
| OPTUNA-2 | MEDIUM | Optuna | Pruner implicit default; silently inactive today, would silently activate on future `trial.report` | `autotuner.py:1506` |
| OPTUNA-4 | MEDIUM | Optuna | OOS-fold-collapse v2 — ~4-5 day usable validation/frozen-eval window after 20-day purge on 125-day history | `autotuner.py:1255-1261` |
| OPTUNA-7 | MEDIUM | Optuna | `n_trials=500` hardcoded literal, not named-constant-pinned; project floor is 100 | `autotuner.py:1507` |
| MATH-ACC-001 | MEDIUM | Accuracy | Brief phrasing "tail_obs_count = floor(α·N)" omits atom contribution; code correctly returns `k + (1 if fractional_weight > 0 else 0)` | `math_engine.py:1136` |
| PERF-001 | MEDIUM | Performance | O(N) Python rolling-vol loop in `run_monte_carlo` on full 3-year history; vectorisable with `pd.Series.rolling` | `math_engine.py:819-824` |
| PERF-002 | MEDIUM | Performance | `sorted(list(historical_data.keys()))` repeated 4+ times per symphony per cycle | `math_engine.py:797, 912, 947, 1225` |
| PERF-005 | MEDIUM | Performance | `study.optimize(n_jobs=-1)` with SQLite `RDBStorage` — write-contention not benchmarked (post-EOD path only) | `autotuner.py:1507` |
| LOGIC-M-1 | MEDIUM | Logic | `advisors/spec_critic.py` docstring claims BACKTEST_SELECTION is acceptable; code correctly excludes it | `advisors/spec_critic.py:8-10, 25, 72-79` |
| OPTUNA-9a | LOW | Optuna | Search-space bound asymmetry between main path and V1 sweep (`VWAP_CROSS_HWM_PCT`) | `autotuner.py:101-102, 117-118` |
| OPTUNA-3 | LOW (verify) | Optuna | Study persistence + microsecond-resolution uniqueness — CORRECT | `autotuner.py:1500-1506, 207-209` |
| OPTUNA-5 | LOW (verify) | Optuna | BHY haircut Yekutieli c(N) + N_effective additive accounting — CORRECT | `autotuner.py:243-493` |
| OPTUNA-8 | LOW (verify) | Optuna | CRRA-EU objective + S-2 t-stat routing + W-H4 floor — CORRECT (audit-fix CRRA-001 landed) | `autotuner.py:321-354, 1337-1359, 1467-1495, 1546-1577` |
| OPTUNA-9 | LOW (verify) | Optuna | NN1 spec-freeze enforcement at autotuner entry — CORRECT | `autotuner.py:45-89, 1120-1234` |
| OPTUNA-10 | LOW (verify) | Optuna | Synthetic-history walk-forward floor + replay-determinism anchor — CORRECT | `synthetic_history.py:30-73, 281-363` |
| MATH-ACC-002 | LOW | Accuracy | Time-squeeze decay curve `log10(1 + 9·t)` is practitioner heuristic, self-flagged in code; Phase-1.5 M3 scheduled | `math_engine.py:155-162` |
| MATH-ACC-003 | LOW | Accuracy | VWAP-cross HWM gate is practitioner heuristic, self-flagged; Phase-1.5 M3 scheduled | `math_engine.py:667-673` |
| PERF-004 | LOW | Performance | `_reject_non_finite_in_records` walks O(D × T) every call; consider hoisting to fetch boundary (needs behavioural verification) | `math_engine.py:784-786, 909-911` |
| PERF-006 | LOW | Performance | `c_n = sum(1/j for j in range(1, n+1))` is O(N) per call (N=500; non-issue at current scale) | `autotuner.py:419` |
| PERF-007 | LOW | Performance | `glob.glob("post_mortem_*.json")` uses CWD-relative path — silent zero-file on cwd mismatch | `autotuner.py:515` |
| PERF-008 | LOW (verify) | Performance | `_MC_REPLAY_SIMULATION_PATHS = 300` — confirmed deliberate tradeoff | `synthetic_history.py:220` |
| PERF-009 | LOW (verify) | Performance | OC + DE advisor queries — single parameterised SELECT per call, no N+1 | `autotuner.py:1753-1784`; `advisors/overfitting_conscience.py:196`; `advisors/divergence_explainer.py:175-183` |
| PERF-010 | LOW (verify) | Performance | `/api/advisor-observations?symphony_id=` — single SELECT, S3-AUDIT-004/010 closure verified | `app.py:2426-2455`; `database.py:911-932` |
| LOGIC-L-1 | LOW | Logic | Dispatch brief names `autotuner._ACCEPTABLE_DISCIPLINES`; the symbol is `NN1_HONEST_DISCIPLINES` | brief item 4d |
| LOGIC-L-2 | LOW | Logic | `/api/advisor-observations` docstring describes legacy 3-subject fan-out; code uses single symphony_id query | `app.py:2430-2433` |

(Logic gate emits 16 numbered LOGIC-### items; LOGIC-001 through LOGIC-016 are PASS verify-only entries with no severity. The three drift items above (M-1, L-1, L-2) are the only logic findings carrying a severity. Verify-only logic IDs are preserved verbatim in the detailed section below.)

---

## Detailed findings

The four specialist reports are preserved verbatim alongside this synthesis in the same directory:

- `accuracy-findings.md` — math-accuracy (3 findings: 0/0/1/2)
- `performance-findings.md` — math-performance (10 findings: 0/1/3/6)
- `logic-findings.md` — math-logic (3 severity findings + 16 verify-only LOGIC-### entries: 0/0/1/2)
- `optuna-findings.md` — math-optuna (9 findings: 0/2/4/3)

Original IDs are preserved verbatim; severities are unchanged.

### Cross-gate annotations

- **OPTUNA-1, OPTUNA-2, OPTUNA-6, OPTUNA-7, PERF-005** — same code region (`autotuner.py:1500-1507`). The optuna gate frames these as methodology / determinism / project-rule-5 concerns; the performance gate frames OPTUNA-6 / PERF-005 as parallel-SQLite write contention. The fixes (named constants + env-driven helpers per the scaffolded engine-audit plans) land at one edit point.
- **PERF-003** — unique to performance gate; logic gate did not flag because the two calls produce the same result (no correctness defect, only redundant CPU). One-line fix: read `bot_state[symphony_id]["symphony_vol"]` in the action phase.
- **LOGIC-M-1 + accuracy MATH-ACC-001 + LOGIC-L-2** — common documentation-drift pattern. Code is the stricter/correct truth in all three cases. None require code-level edits.
- **Logic LOGIC-011 + accuracy "CVaR-divergence REJECT wall — verification"** — independent confirmation of the wall from two angles (structural absence + adversarial test fixtures).

### Accuracy gate (math-accuracy)

Three findings, all documentation-or-provenance items; no numerical-correctness defect.

- **MATH-ACC-001 [MEDIUM]** — `tail_obs_count` brief phrasing telescopes the canonical contract (`floor(α·N) + atom`); the code at `math_engine.py:1136` correctly returns `k + (1 if fractional_weight > 0 else 0)`. Risk surface: a future doc-derived test could mis-pin the contract.
- **MATH-ACC-002 [LOW]** — Time-squeeze `log10(1 + 9·t)` is a tuned practitioner heuristic, self-flagged at `math_engine.py:155-162`. Phase-1.5 M3 redrive-provenance-gaps plan owns the re-derivation under the S-1 two-stage parity gate.
- **MATH-ACC-003 [LOW]** — VWAP-cross HWM gate is a tuned practitioner heuristic, self-flagged at `math_engine.py:667-673`. Same Phase-1.5 M3 disposition.

All nine numerical surfaces verify against synthesis bindings + first principles:
- CRRA closed-form spot checks (γ=2 gain, catastrophic loss + W-H4 floor, γ→1 log limit, γ<1 power law)
- CRRA-EU one-sample t-stat formula pin (sample stdev, ddof=1)
- CVaR Acerbi-Tasche / Rockafellar-Uryasev convention with atom contribution
- MC eligible-pool boundary (raw 39 = 20 + 19, per project memory)
- N_effective additive accounting (`N_optuna + S`)
- NN1 spec-freeze three-gate layering
- Parabolic ratchet one-way arming
- Breakeven layer latching + floor
- Volatility scaling (per-day, no √252 silent annualisation)

### Performance gate (math-performance)

One HIGH (PERF-003), three MEDIUM (PERF-001, PERF-002, PERF-005), six LOW.

- **PERF-003 [HIGH]** — Double `calculate_20d_vol` on action-phase hot path. Same holdings, same cached `historical_data`, identical result. One-line fix: promote the data-phase result. (No behaviour change.)
- **PERF-001 [MEDIUM]** — `run_monte_carlo` O(N) Python rolling-vol loop; vectorisable with `pd.Series.rolling`.
- **PERF-002 [MEDIUM]** — `sorted(list(historical_data.keys()))` called 4+ times per symphony per cycle; hoist invariant.
- **PERF-005 [MEDIUM]** — `n_jobs=-1` + SQLite `RDBStorage` write contention not benchmarked (post-EOD path; non-blocking).
- **PERF-004 [LOW]** — Defensive `_reject_non_finite_in_records` walks O(D × T) every call; candidate for hoist to fetch boundary. [NEEDS BEHAVIORAL VERIFICATION]
- **PERF-006 [LOW]** — Harmonic-number sum O(N) at N=500; not a meaningful concern.
- **PERF-007 [LOW]** — CWD-relative glob in `calculate_historical_deviation`; silent zero-file risk.
- **PERF-008, PERF-009, PERF-010 [LOW verify-only]** — confirm `_MC_REPLAY_SIMULATION_PATHS = 300` is deliberate; advisor producer queries are O(1) per symphony; `/api/advisor-observations` is single SELECT.

Architecture constraint #1 ("no blocking I/O on the 1-minute path") is intact. All three Sprint-3 advisor producers are EOD/Friday-only.

### Logic gate (math-logic)

Sixteen logic checks, all PASS:

- LOGIC-001 — Autotune `lastrowid` → OC `subject_id` flow; S3-AUDIT-001/-007 closure verified
- LOGIC-002 — `prior_runs` ASC by `run_timestamp`, excludes just-inserted id; S3-AUDIT-003 closure
- LOGIC-003 — OC Indicator-3 drift gate requires N≥2 priors + strict-monotonic ascent
- LOGIC-004 — DE feature-flag dispatch produces NOT_APPLICABLE vs INFORMATIONAL with no signed-divergence escape
- LOGIC-005 — `/api/advisor-observations?symphony_id=` single denormalised query
- LOGIC-006 — MC sentinel = None; `mc_available` gates arm/disarm/TP; protective stop fail-safe
- LOGIC-007 — `compute_tp_confirmation` MC-unavailable resets `above_tp_count` while armed
- LOGIC-008 — `resolve_trigger_priority` deterministic total order; `(None, [])` on all-False
- LOGIC-009 — `validate_nn1_compliance` default-deny on unknown `freeze_discipline`
- LOGIC-010 — Try/except scoping: producer try-blocks scoped tightly; `save_autotune_run` outside the swallow
- LOGIC-011 — CVaR-divergence wall intact (production-side absence + adversarial test enforcement)
- LOGIC-012 — Port-level deprecation: no port_state/decision/mode/selector refs in math_engine or alpha_bot_execution
- LOGIC-013 — Empty `advisor_observations` returns `[]`
- LOGIC-014 — Empty `prior_runs` → OC CLEAR (drift gate guards against empty list)
- LOGIC-015 — Missing fixture data → producer raises KeyError (not silent CLEAR)
- LOGIC-016 — Concurrent autotuner write + dashboard read — WAL mode permits read-while-write

Three drift items emit a severity:

- **M-1 [MEDIUM]** — `advisors/spec_critic.py` docstring claims BACKTEST_SELECTION is acceptable; code (`_ACCEPTABLE_DISCIPLINES` at lines 72-79) correctly excludes it. Doc-only fix.
- **L-1 [LOW]** — Dispatch brief item 4d references `autotuner._ACCEPTABLE_DISCIPLINES`; the symbol is `NN1_HONEST_DISCIPLINES`.
- **L-2 [LOW]** — `/api/advisor-observations` docstring (`app.py:2430-2433`) describes the legacy 3-subject fan-out; code uses `get_advisor_observations_for_symphony` (single query, migration 025).

### Optuna methodology gate (math-optuna)

Two HIGH (OPTUNA-1 sampler implicit, OPTUNA-6 parallelism/seed), four MEDIUM (OPTUNA-2 pruner, OPTUNA-4 OOS-fold-collapse, OPTUNA-7 n_trials literal, OPTUNA-9a search-space asymmetry — note 9a is LOW, the MEDIUMs are §2/§4/§7), three LOW verify-only (OPTUNA-3 study persistence, OPTUNA-5 BHY+N_eff, OPTUNA-8 CRRA-EU, OPTUNA-9 NN1, OPTUNA-10 synthetic history).

All Phase-1 binding decisions (NN1 spec-freeze, BHY+N_effective, S-2 t-stat, W-H4 floor, MC sentinel, two-DB boundary, 125-day walk-forward, 100-trial floor, replay-determinism anchor) are traceable to landed code. The five open methodology lanes are all scaffolded engine-audit plans (sampler-choice, pruner-choice, parallelism-reproducibility, trial-floor-justification, walk-forward-fold-structure).

---

## PM-complete disposition

Per the dispatch brief: "PASS / BLOCK-PM-COMPLETE / NEEDS-FIX-PASS".

**Verdict: PASS** — zero BLOCKER, zero correctness HIGH on the production action path. The three HIGH findings are scheduled methodology lanes (OPTUNA-1, OPTUNA-6) or a one-line hot-path edit (PERF-003) — none gates closure of the Sprint-3 binding decisions.

Sprint-3 surface specifics:
- Advisor producer wiring (OC, SC, DE) — VERIFIED end-to-end (logic LOGIC-001..005, LOGIC-010, performance PERF-009, PERF-010)
- `/api/advisor-observations` denormalised query — VERIFIED (logic LOGIC-005, performance PERF-010)
- Port-level deprecation completion — VERIFIED (logic LOGIC-012)
- CVaR-divergence REJECT wall — VERIFIED (accuracy + logic LOGIC-011)
- NN1 spec-freeze + BHY + N_effective + CRRA-EU + W-H4 binding decisions — VERIFIED (accuracy + optuna §5/§8/§9)
- MC sentinel discipline + 39-raw-day boundary — VERIFIED (accuracy + logic LOGIC-006/007 + optuna §10)

Recommended next-step backlog (post-PM-complete):
1. PERF-003 — one-line fix in `alpha_bot_execution.py` action phase (small effort, high-value hot-path cleanup)
2. Land scaffolded engine-audit plans `sampler-choice/`, `pruner-choice/`, `parallelism-reproducibility/`, `trial-floor-justification/` together (shared code region, zero behaviour change today, protective tripwires forward)
3. Documentation cleanup: LOGIC-M-1 (spec_critic docstring), LOGIC-L-2 (api docstring), MATH-ACC-001 (brief phrasing for future re-audit dispatches)
4. PERF-001 + PERF-002 — `run_monte_carlo` vectorisation pass
5. Phase-1.5 M3 redrive-provenance-gaps plan owns MATH-ACC-002 + MATH-ACC-003

No BLOCKER. No correctness HIGH. PM-complete may close on Sprint-3.
