<!-- ARCHIVED from audit/math-engine-reaudit @ e246d08, original date 2026-05-27. Conclusion recorded in docs/audit/final-audit-2026-05-29/math-soundness.md (PERF-005 closed; accuracy findings verified). -->
# Math Engine Re-Audit — Accuracy Findings (2026-05-27)

**Auditor:** math-accuracy (read-only)
**Branch:** audit/math-engine-reaudit (forked from plan/finalist-a-scaffold @ 8d38a43)
**Scope:** numerical correctness of the nine surfaces enumerated in the dispatch brief.

---

## Executive verdict

- BLOCKERs: 0
- HIGH: 0
- MEDIUM: 1
- LOW: 2

The nine numerical-correctness surfaces verify against the council synthesis bindings, the v3 + divergence-evaluation references, and first-principles math. No silent-error path was discovered; every sentinel branch is fail-safe. The CVaR-divergence REJECT wall is intact — no signed-divergence number is computed, persisted, or surfaced anywhere on the production path.

The single MEDIUM is a documentation-only inconsistency (the dispatch brief's "tail_obs_count canonical = floor(α·N)" phrasing telescopes two distinct council bindings; the code is correct on both — surfaced so a future doc edit does not regress it). The LOWs are practitioner-heuristic provenance gaps already self-flagged in the code.

---

## Finding index

| ID | Severity | Subject | File:line |
|----|----------|---------|-----------|
| MATH-ACC-001 | MEDIUM | Brief phrasing "tail_obs_count = floor(α·N)" understates the actual canonical contract; code correctly returns the distinct-genuine count k + atom | math_engine.py:1136 |
| MATH-ACC-002 | LOW | Time-squeeze decay curve has no formal literature provenance; self-flagged in code | math_engine.py:155-162 |
| MATH-ACC-003 | LOW | VWAP-cross HWM gate (System A) is a tuned practitioner heuristic; self-flagged in code | math_engine.py:667-673 |

---

## Verification cases run (all PASS)

### CRRA closed-form spot checks

| Case | Inputs | Expected (first-principles) | Code output | Pass |
|---|---|---|---|---|
| Standard γ=2 gain | r=0.05, γ=2 | W=1.05, U=(1.05⁻¹−1)/(−1) = +0.047619 | compute_crra_eu_objective([0.05], 2) → 0.047619... | ✓ |
| Catastrophic loss + W-H4 floor | r=−0.999, γ=2 | W_raw=0.001 → floor=0.001 → U=(1000−1)/(−1) = −999 | compute_crra_eu_objective([−0.999], 2) = −999 (bounded, finite) | ✓ |
| γ→1 log-utility limit | r=0.05, γ=1.0 | U = ln(1.05) = 0.048790... (L'Hôpital limit) | abs(γ−1)<1e-9 branch → math.log(1.05) | ✓ |
| Power-law γ<1 | r=0.05, γ=0.5 | U = (1.05^0.5 − 1)/0.5 = 0.049390... | (W^(1-γ)−1)/(1−γ) closed form | ✓ |

The `−1` numerator term is **present** in `compute_crra_utility` (math_engine.py:1376). The doctring at math_engine.py:1355-1359 correctly names why it matters: mean(U) is the haircut t-stat numerator, not just an argmax target. Omitting `−1` would shift mean(U) by −1/(1−γ) — a real bias, not a monotone-equivalence.

**γ scaling:** matches Merton 1969 / Samuelson 1969 closed form.
**Log-domain stability:** tolerance `CRRA_LOG_UTILITY_GAMMA_TOL = 1e-9` (math_engine.py:77) guards the 1/(1−γ) blow-up by switching to the ln(W) branch.
**Wealth-argument derivation:** `derive_floored_wealth_argument` (autotuner.py:305-318) applies W = max(WEALTH_ARG_FLOOR, 1 + r); floor is on **input W**, never on output U. W-H4 binding (README §0.1 row 87) honored.

### CRRA-EU t-stat formula pin

`compute_crra_eu_tstat(U_series)` at autotuner.py:321-354 implements the one-sample t-stat `mean(U) / (sd(U) / √T)`:

- Uses `statistics.stdev` (sample, ddof=1, Bessel-corrected) — explicitly documented at line 338-339 as guarding against the pstdev sqrt(T/(T-1)) inflation.
- Returns 0.0 for T ≤ 1 and for sd=0 — both fail-safe (zero numerator → BHY argmin selects elsewhere).
- This replaces `compute_sortino_tstat` (which returns `sortino * √T` — the H-6 category error for a mean-valued objective). The discriminator at autotuner.py:1552-1555 routes correctly by `_objective_kind`.

**Cross-check against H-6 category-error precedent (synthesis §4 / autotuner.py:266-271):** the function is mean-valued, the t-stat is the canonical one-sample form, not `effect_size·√T`. PASS.

### CVaR — Acerbi-Tasche / Rockafellar-Uryasev convention

`compute_cvar_5pct_general_distribution` (math_engine.py:1068-1182) implements the R-U general-distribution formula:

  CVaR = (1/α)·(1/N)·(sum_below + fractional_weight · VaR_atom)

Verified by hand for two cases:

| Case | Inputs | First-principles CVaR | R-U result |
|---|---|---|---|
| α=0.5, N=10, no atom contribution (αN integer) | sorted [-10,-8,-6,-4,-2,1,2,3,4,5] | naive mean of below-VaR 5 values = −6 | (1/0.5)(1/10)(−30 + 0·1) = −6 ✓ |
| α=0.05, N=150, αN=7.5 (atom contributes) | k=7, fractional=0.5 | naive estimator (mean of 7) ≈ 4% upward biased vs R-U truth | (1/0.05)(1/150)(sum_7 + 0.5·VaR) ✓ |

**Tail sign convention:** loss = negative return. `compute_portfolio_cvar` returns cvar_pct that is **negative for a loss** (consistent with the rest of the engine; never an absolute value).

**Tail-obs canonical field:** `CVaRAssessment.tail_obs_count` is the canonical name (synthesis §2.6). The SQL column `cvar_n_tail` projects to this Python field. Verified no consumer reads a `n_tail` Python attribute (Grep result clean).

**Breach (a)-latched condition:** `CVaRAssessment.__post_init__` (math_engine.py:139-152) enforces the fail-safe invariant `cvar_pct is None ⇒ breach must be False` AND `cvar_pct is None ⇒ tail_obs_count must be 0`. The (a)-latched HYSTERESIS state-machine output that fills `breach=True` lives in the Phase-2 `compute_cvar_cosignal_confirmation` plan; at Phase-1 (current code), `compute_portfolio_cvar` always sets `breach=False` (math_engine.py:1327) with a binding-comment "Phase-1: no breach threshold defined; breach is always False." This is consistent with the Phase-1 binding (synthesis §0.2: CVaR is **diagnostic-only**, never a calibrated live budget) and consistent with `cvar-cosignal-hysteresis-trigger/plan.md:121-122` (the LATCHED (a) semantic is a Phase-2 deliverable).

**ε-mixing for ties:** the R-U formula's atom contribution `fractional_weight · VaR_atom` is the documented R-U treatment for general (possibly discrete-with-atoms) distributions; no separate ε-mixing primitive is needed and none is implemented. The atom handling is the only correct mechanism here. PASS.

**Degenerate-tail guards:** sentinel returns when (a) empty pool, (b) k < CVAR_MIN_TAIL_OBS (=1), (c) tail has < 2 distinct observations (ddof=1 undefined), (d) variance == 0 (stderr-positive sentinel discipline). Each guard returns CVaREstimate with cvar_pct=None — consistent with the four-part S-3 contract (README row 85).

**Stderr binding (H-2):** `compute_cvar_stderr_distinct_tail` uses `n_tail_distinct = k + 1 if fractional_weight > 0 else k` as denominator — NOT the resample count 5000. Math_engine.py:1141-1142 explicitly comments "H-2 stderr on distinct tail count (never the resample count)". PASS.

### Monte Carlo eligible-pool boundary

math_engine.py:805-811:

```
eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)
if eligible_days < MC_MIN_HISTORY_DAYS:
    return MC_INSUFFICIENT_HISTORY_SENTINEL
```

With MC_MIN_HISTORY_DAYS=20 and MC_VOL_WINDOW_DAYS=20: minimum raw history = 20 + (20-1) = **39 raw days**, as the dispatch brief specifies. The early-window exclusion (first MC_VOL_WINDOW_DAYS−1=19 rows, whose rolling-vol estimate is downward-biased on a short sample) is reflected in `candidate_idx = np.arange(MC_VOL_WINDOW_DAYS - 1, len(spy_returns))` at line 838. The same eligible-pool boundary is duplicated in `compute_portfolio_cvar` at math_engine.py:1229. Boundary matches project memory `project_mc_eligible_pool_vs_raw_day_boundary`.

**mc_available gate (raw production path):** at alpha_bot_execution.py:1137 `mc_available = prob_beating is not None`, used to gate every MC-driven branch (arm at line 1139, disarm at line 1156, mc_history append at line 1166, TP confirmation at line 1274). No path admits an in-band probability of None. The protective trailing stop still fires on the ticks-below-stop magnitude condition alone when MC is unavailable (compute_exit_confirmation at math_engine.py:423-425: `mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD`). Fail-safe PASS.

**MC sentinel discipline:** `MC_INSUFFICIENT_HISTORY_SENTINEL = None` (math_engine.py:84) is out-of-band, never an in-band probability. Mirrored by `CVaRAssessment.cvar_pct = None` and by the autotuner replay-side gating. Project memory `project_cluster5_d6_orphaned_red_triage` records that the merged Cluster-3 `_replay_exit_tick` gates on `mc_available` for the raw None production path; consistent with the current code.

### Walk-forward objective — N_effective additive accounting

`compute_n_effective(n_optuna, ledger_query, winning_spec_bundle_id=None)` at autotuner.py:443-493:

  N_effective = N_optuna + S

where S is the sum of `n_configs_searched` over researcher_dof_ledger rows that:
- exclude `touched_frozen_eval` rows (handled by OOS_PEEK alarm path),
- exclude the winning bundle's own contribution (already counted in n_optuna).

NN1-honest case: every facet is THEORY/MANDATE/STYLIZED_FACT/POLITIS_WHITE/CADENCE/CALIBRATION-frozen → no row has evidence_source='BACKTEST_SELECTION' → S=0 → N_effective = N_optuna → byte-identical to the pre-wiring haircut (plan D2 backward-compatibility contract).

**Wiring at the call site** (autotuner.py:1564-1577) is correct: `compute_n_effective` is invoked BEFORE `_haircut_select`, and its result is passed as `n_effective=n_eff` to the haircut. Padding with `[1.0] * S` copies at autotuner.py:898-906 ensures the Yekutieli c(N) sees the honest N. PASS.

### NN1 spec-freeze at autotuner entry

Three layered gates run at autotuner entry:

1. `validate_search_space_nn1()` (autotuner.py:1120-1140, called at line 1266) — refuses to start if `OPTUNA_SEARCH_SPACE_KEYS` contains any of `{gamma, utility_family, wealth_argument, generator_family, horizon_convention, lambda, regime_bucket_thresh}`.
2. Explicit `spec_bundle_id is None` rejection (line 1270-1275): NN1 Phase-1 strict.
3. `validate_nn1_compliance(spec_bundle_id)` (line 1143-1234, called at line 1307): default-deny on facet `freeze_discipline ∉ NN1_HONEST_DISCIPLINES`. The honest set is the synthesis §2.5 enumeration:
   - THEORY, MANDATE, STYLIZED_FACT, POLITIS_WHITE, CADENCE, CALIBRATION

`BACKTEST_SELECTION` is the named violation tripwire (autotuner.py:78), not silently usable as a fallback for unclassifiable rows; any unrecognized discipline is also treated as a violation ("unrecognised discipline — default-deny" at line 1220). This matches the binding "Adding a NEW name here without classifying it in this block is a Gate-1 review fail" (autotuner.py:64-65). PASS.

### Log time-squeeze decay

`compute_time_squeeze_decay(time_ratio)` at math_engine.py:211-242:

  decay_curve = log10(1 + 9·t)

verified:
- t=0: log10(1)=0 → multiplier=MULT_OPEN=1.5, min_stop=0.3
- t=1: log10(10)=1 → multiplier=MULT_CLOSE=0.5, min_stop=0.15
- Concavity: d²/dt²[log10(1+9t)] < 0 for t>0 — confirms tighter early-session, slower late-session, matching the documented intent.
- Reject-don't-coerce: ValueError on t<0 or t>1 (line 235-238).

**Provenance:** self-flagged at math_engine.py:155-162 as a tuned practitioner heuristic with no formal literature provenance, marked for follow-up empirical review against realized intraday vol term-structure. See MATH-ACC-002 below.

### Parabolic ratchet

The "parabolic ratchet" reference covers two pieces:

1. **Arming** — `compute_para_arm_decision` (math_engine.py:185-208):
   - velocity = current_return − prev_return (no scaling, no clamping)
   - should_arm = (velocity ≥ threshold) AND (not currently_armed)
   - **One-way transition** (once armed, never re-arms) — caller is responsible for state mutation.

2. **Stop tightening when armed** — `compute_active_trailing_stop` (math_engine.py:245-289):
   - `active *= parabolic_squeeze_multiplier` when `para_armed or breakeven_locked`.
   - Reject-don't-coerce: `parabolic_squeeze_multiplier ≤ 0` raises ValueError (line 280-284), preventing the stop from collapsing to 0 or going negative.

**Monotonicity:** there is no monotonicity violation. Once armed, the position remains armed for the rest of the day (one-way arming); the squeeze multiplier tightens the stop monotonically (the multiplier is fixed per-trial — it does not relax back to >1.0).

**Escalation curve:** the squeeze does NOT escalate further once armed (it is binary on/off scaled by a single multiplier). This is the intended Phase-1 behavior; no curve is meant to compound. PASS.

### Breakeven layer (resolve_trigger_priority + compute_breakeven_update)

Two pieces:

1. **State update** — `compute_breakeven_update` (math_engine.py:292-365):
   - `dynamic_activation = clamp(symphony_vol, BREAKEVEN_ACTIVATION_MIN, BREAKEVEN_ACTIVATION_MAX)`
   - Counts qualifying ticks via `current_return ≥ dynamic_activation − BREAKEVEN_ACTIVATION_DEADBAND`.
   - **Latches** at `HWM_HOLD_TICKS_THRESHOLD = 5` consecutive ticks.
   - **Latching invariant** (documented + verified at line 330-332): `currently_breakeven_locked=True ⇒ new_breakeven_locked=True` regardless of other inputs. The one-way transition cannot be reset.
   - **Breakeven floor** (documented + verified at line 334-338): once latched, `stop_trigger_level = max(base_stop_level, 0.0)` — "lock gains hard"; resolved stop may still move down between post-lock ticks but never below 0.0.
   - **Triggered override** (line 363-364): `is_triggered=True ⇒ stop = TRIGGERED_OVERRIDE_LEVEL (−999.0)` suppresses re-exit.

2. **Trigger priority** — `resolve_trigger_priority` (math_engine.py:736-759):

   Canonical order: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop (math_engine.py:728-733). Order matches synthesis H2 acceptance criteria and the math audit. The breakeven layer is NOT in `_TRIGGER_PRIORITY_ORDER` — it is correctly modeled as a STATE that modifies the trailing-stop level, not a peer trigger. Sign and priority correctness PASS.

   **Reference cited in code:** Fu, M.C. & Zhang, H. (2012), Int. J. Operations Research 9(3), 129-140 — a primary source for trailing-stop construction.

### Volatility scaling

`calculate_20d_vol(holdings, historical_data)` at math_engine.py:903-935:
- Window: `LOOKBACK_DAYS = 20`.
- Computation: portfolio daily-returns matrix × allocation weights × PCT_SCALAR, then `np.std(.)` (population, ddof=0).
- Returns 0.0 if fewer than 20 days available.

**Window-size matching:** `LOOKBACK_DAYS = 20` matches `MC_VOL_WINDOW_DAYS = 20`. Both pull the same vol horizon — they are consistent across the volatility-driven trailing-stop and the MC kNN-distance feature.

**Annualization:** the function name says "20d" not "annualized". There is **no √252 annualization step**. This is correct: every consumer in the trailing-stop layer treats `symphony_vol` as a per-day (percentage-points) scale — `compute_active_trailing_stop` uses `active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)` directly, and `compute_vwap_bleed_arm_threshold` uses `raw = -(symphony_vol * bleed_multiplier)` directly. An annualization step here would silently inflate the trailing-stop distance by ~16×. Cross-check PASS.

**ATR fallback** (`calculate_14d_atr_pct`): uses ATR_LOOKBACK_DAYS=15 = "14 TR periods + 1 prior close required to compute the first TR" — the standard Wilder ATR period. Falls back to `calculate_20d_vol` when high/low data is missing (math_engine.py:949, 982, 989). PASS.

### CVaR-divergence REJECT wall — verification

Per dispatch brief hard rule 4 and project memory `project_cvar_divergence_validation_wall`, this is a permanent BLOCKER if violated. Verified:

1. **No signed-divergence number computed in math_engine.py.** Grep confirms only one match (a docstring reference to the v3 evaluation §A.1 H-1).
2. **`advisors/divergence_explainer.py` is compliant.** Docstring lines 7-17 enumerate the forbidden raw_response keys (`divergence`, `signed_divergence`, `cvar_diff`, `cvar_delta`, `window_divergence`, `divergence_pct`, `delta`, etc.). Body at lines 111-131 surfaces only two independent CVaR window values (`short_window_cvar_pct`, `short_window_tail_obs`, `long_window_cvar_pct`, `long_window_tail_obs`) under their own S-3 contracts. No arithmetic difference, ratio, or threshold-shaped affordance is computed or persisted.
3. **No schema column for `cvar_divergence`** anywhere (Grep confirms zero matches outside the binding statement in plans/README.md and the forbidden-keys list in divergence_explainer.py).
4. **No production consumer reads or writes a signed-divergence quantity.** The `shadow_divergence` and `divergence_detected` symbols in `app.py`, `analytics.py`, `database.py`, `alpha_bot_execution.py` refer to a completely different mechanism — the shadow_history live-vs-AlphaBot comparison stream (PA-M1F-14), NOT the CVaR-divergence detector that was rejected. Inspected each call site; none touches a CVaR signed difference.

**Verdict: WALL HOLDS.** No BLOCKER.

---

## Findings

### MATH-ACC-001 [MEDIUM] Dispatch brief phrasing "tail_obs_count canonical = floor(α·N)" telescopes two distinct council bindings; the code is correct on both

**File:** math_engine.py:1136 (and 1051, 1115, 1142)

**Reproducer:** The dispatch brief asserts the tail_obs_count canonical formula is `floor(α·N)`. Tracing this against synthesis §2.6 and the R-U general-distribution implementation:

The actual canonical contract is two-part:
- **Name binding (synthesis §2.6 verbatim, README §1 row 85):** the typed-field name on `CVaRAssessment` is `tail_obs_count` (NOT `n_tail`; the SQL column `cvar_n_tail` projects to this Python field). The same name is reused by `ForwardPathBundle.tail_obs_count` (Phase-2 simulate-forward-paths plan §65).
- **Count binding (H-2, synthesis §2.6):** the count is the **distinct genuine tail observations** = `k_below + (1 if atom_contributes else 0)`, where `k_below = floor(α·N)`. This count is the H-2 stderr denominator — never the resample count 5000 (which would understate the stderr by ~√(5000/k) ≈ 25× per math_engine.py:1026).

**Expected:** `tail_obs_count = floor(α·N) + (1 if fractional_weight > 0 else 0)`.

**Observed:** The code matches the expected (math_engine.py:1136: `n_tail_distinct = k + (1 if fractional_weight > 0 else 0)`). The dispatch brief's phrasing "canonical = floor(α·N)" is a telescoped restatement that omits the atom contribution. If a future doc edit propagates the brief's wording verbatim into a contract test (e.g. asserting `tail_obs_count == k`), the test would mis-pin the contract and fail the legitimate atom case.

**Reference:** decision-science-council-synthesis.md §2.6; feature-plans/decision-science/README.md §1 row 85 ("(a) stderr on the distinct-tail-observation count (~7-8, NEVER the resample count 5000 per H-2); (b) tail_obs_count (canonical Python field per synthesis §2.6...)"); feature-plans/decision-science/phase-2/cvar-cosignal-hysteresis-trigger/plan.md:122; math_engine.py:1023-1066 (stderr helper) and 1136 (estimator).

**Confidence:** HIGH — the binding is sourced from synthesis §2.6 verbatim and the existing tests already pin the atom-contributes case (per fixture provenance in `tests/fixtures/math/n_effective_additive_accounting.json` and the M2-CVaR-known-pool fixture). The MEDIUM severity is documentation-hardening only; the production code is correct.

---

### MATH-ACC-002 [LOW] Time-squeeze decay curve `log10(1 + 9·t)` is a tuned practitioner heuristic without formal literature provenance

**File:** math_engine.py:155-162 (constants), 211-242 (function)

**Reproducer:** The intraday trailing-stop tightening uses `log10(1 + 9·t)` to map session-fraction t∈[0,1] to a decay parameter ∈[0,1]. The concave shape is correct for the documented intent (tighter early, looser late). However, the specific functional form `log10(1 + 9·t)` has no formal literature provenance — it is an Alpha­Bot operator choice.

**Expected:** A primary-source citation OR a self-flagged "follow-up empirical review" anchor.

**Observed:** Self-flagged at math_engine.py:160-162: "The shape has no formal literature provenance and is flagged for a follow-up empirical review against realized intraday vol term-structure." The Phase-1.5 `m3-redrive-provenance-gaps` plan (decision-science roadmap §1.5) carries the re-derivation task with the S-1 two-stage parity gate. This is the prescribed pathway; the LOW severity reflects that the gap is acknowledged + scheduled + governed by a downstream plan, not silently buried.

**Reference:** feature-plans/decision-science/phase-1.5/m3-redrive-provenance-gaps/plan.md; math_engine.py inline comment.

**Confidence:** HIGH — the gap is named in code and scheduled in plans. No action needed in this audit.

---

### MATH-ACC-003 [LOW] VWAP-cross HWM gate `safe_hwm >= vwap_cross_hwm_pct` is a tuned practitioner heuristic

**File:** math_engine.py:667-673 (System A profit-protection gate)

**Reproducer:** Inside `compute_vwap_breakdown_update`, System A (profit-protection VWAP break) requires `safe_hwm >= vwap_cross_hwm_pct AND current_return < safe_hwm`. The HWM threshold is an Optuna-tuned parameter, not a derived quantity.

**Expected:** Same as MATH-ACC-002 — a primary-source citation OR a self-flagged "follow-up empirical review" anchor.

**Observed:** Self-flagged at math_engine.py:667-673: "the gate `safe_hwm >= vwap_cross_hwm_pct` is a tuned practitioner heuristic with no formal literature provenance ... the threshold itself is an Optuna-tuned parameter, not a derived quantity." Like MATH-ACC-002, the Phase-1.5 `m3-redrive-provenance-gaps` plan carries the re-derivation under the S-1 parity gate.

**Reference:** feature-plans/decision-science/phase-1.5/m3-redrive-provenance-gaps/plan.md; math_engine.py inline comment.

**Confidence:** HIGH — same disposition as MATH-ACC-002.

---

## Summary

The Sprint-3-final math engine numerical surfaces are correct against:
- the council synthesis binding decisions (README §0–§1),
- first-principles math for CRRA (Merton/Samuelson), R-U CVaR, one-sample t-stat, and BHY+Yekutieli haircut,
- the v3 + divergence-evaluation hard gates (NN1 spec-freeze; W-H4 wealth-floor; H-2 distinct-tail stderr; CVaR-divergence REJECT wall),
- the project's own no-magic-numbers and reject-don't-coerce rules.

No blockers. No high-severity findings. The single MEDIUM is a doc-only telescoping risk surfaced so a future plan edit cannot regress the existing-and-correct atom handling. Both LOWs are practitioner-heuristic provenance gaps that the Phase-1.5 M3 plan is already scheduled to close under the S-1 two-stage parity gate.

The CVaR-divergence REJECT wall is **intact**. No signed-divergence quantity is computed, persisted, or surfaced anywhere on the production path.
