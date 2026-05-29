# Math Soundness — Final Audit (2026-05-29)

**Author:** risk-engine-specialist (final-audit-math-respawn-1)
**Branch:** `audit/final-2026-05-29-math`
**HEAD under audit:** `4b684db` on `plan/finalist-a-scaffold`
**Lens:** Re-apply `docs/audit/vision-audit-2026-05-27/math-soundness.md` 8-surface evaluation against math-touching closures since the vision-audit.

---

## Closures evaluated

| SHA | Change |
|-----|--------|
| a2aef59 | CVaR per-cycle wire-up — diagnostic-only, REJECT wall |
| 479fc2a | MC regime-match-quality guard (Surface 3 Rec #2) |
| 79759db | M3 provenance — sqrt-time derivation, R2 honest flag |
| e8a6c00 | PERF-001 rolling SPY vol helper (as_strided + cumsum) |
| 38d2d7a | PERF-002 sorted-dates LRU cache |
| 262ef90 | PERF-004 reject-non-finite compute_regime_match_quality gap |
| 468aad9 | PERF-005 RED-IS-GREEN regression pin (no production diff) |
| ef83ef4 | PERF-006 Yekutieli c(N) memoization |
| 564cc4d | MATH-ACC-001 tail_obs_count canonical formula docstring |
| b7a599c | LOGIC-M-1 spec_critic I-2 docstring |
| b0d1e2b | R2 honest flag interpretive caveat |
| e46d436 | MC parity-postinit — RegimeMatchAssessment __post_init__ guard |
| 7ab11a9 | CVaR stale-comment cleanup |

---

## Per-Surface Verdict

### Surface 1 — CRRA-EU utility (`compute_crra_utility`, `compute_crra_eu_objective`)

**Verdict: SOUND — no change from original.**

No numeric change was introduced. The closures that touch CRRA are:
- PERF-006: memoized `_yekutieli_c_n` — affects the BHY haircut (Surface 5), not CRRA itself.
- autotuner.py:1649-1650: confirms `daily_returns_fraction = [r / RETURN_PCT_TO_FRACTION for r in daily_returns]` before `compute_crra_eu_objective`. The W-H4 floor is applied inside `compute_crra_eu_objective` at `math_engine.py:1606`: `W = max(WEALTH_ARG_FLOOR, 1.0 + r)`. The `-1` numerator term in `(W**(1-gamma) - 1.0) / (1.0 - gamma)` at `math_engine.py:1580` is present and correct.

**Delta vs original:** None. The CRRA-EU pipeline is byte-identical to the vision-audit state.

**Numerical identity:** PRESERVED. The `_reject_non_finite` guard at `math_engine.py:1576` and the `CRRA_LOG_UTILITY_GAMMA_TOL = 1e-9` branch at line 1577 are unchanged. The W-H4 floor is applied only to input W, never to output U (line 1606 confirms).

---

### Surface 2 — CVaR diagnostic (`compute_portfolio_cvar`, `CVaRAssessment`)

**Verdict: SOUND — closures a2aef59, 7ab11a9 correctly tighten provenance without touching arithmetic.**

**a2aef59 wire-up (call-path verified):**
`alpha_bot_execution.py:1434-1476` shows `compute_portfolio_cvar` is called for telemetry only. The result feeds `database.record_cvar_diagnostic`. No branch reads `_cvar_short.breach` for any exit decision:
```python
# alpha_bot_execution.py:1440
# CVaR remains diagnostic-only: no trigger branch reads this write.
_cvar_short = math_engine.compute_portfolio_cvar(...)
```
`breach=False` is hard-coded at `math_engine.py:1530`:
```python
result = CVaRAssessment(
    ...
    breach=False,  # Phase-1: no breach threshold defined; breach is always False
```
Call-path confirmed: grep for `.breach` in non-test production code returns ZERO hits outside `__post_init__` enforcement guards.

**7ab11a9 stale-comment cleanup:**
The pre-cleanup comment read "Phase-2 CVaR typed result (defined in Phase 1 so the M2 schema migration and Phase-2 simulate_forward_paths cutover have a stable single-import target)." This was stale — there is no Phase-2 forward-path co-signal (REJECTED per council). Post-cleanup at `math_engine.py:135-137`:
```python
# kNN historical regime-match result (Phase-1; the forward-path co-signal was REJECTED
# per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md CVaR-divergence wall).
# Phase-1 rule: ZERO production consumers permitted — tests only.
```
The docstring now accurately describes the code.

**`CVaRAssessment.__post_init__` — four invariants enforced at `math_engine.py:162-187`:**
1. `cvar_pct is None and breach` — ValueError (fail-safe: absent estimate never a trigger)
2. `cvar_pct is None and tail_obs_count != 0` — ValueError
3. `cvar_pct is None and stderr is not None` — ValueError
4. `cvar_pct is not None and stderr is None` — ValueError

All four are present. REJECT-wall intact.

**MATH-ACC-001 tail_obs_count canonical formula:**
`CVaREstimate.__doc__` at `math_engine.py:1207-1208`:
```
tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)
```
`compute_cvar_5pct_general_distribution.__doc__` at `math_engine.py:1290-1291`:
```
Canonical formula (Acerbi-Tasche atom-contribution discipline):
  tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)
```
Both docstrings now include Acerbi-Tasche 2002 citation at line 1300. The code at `math_engine.py:1342` computes `n_tail_distinct = k + (1 if fractional_weight > 0 else 0)`, which is bit-identical to the canonical formula. Doc-code alignment confirmed.

**Delta vs original:** The vision-audit noted CVaR was SOUND but flagged missing regime-match-quality guard (now closed by Surface 3) and stale comment framing (now corrected by 7ab11a9). Both resolved without touching arithmetic.

---

### Surface 3 — Monte Carlo gating (`run_monte_carlo`)

**Verdict: SOUND — critical gap closed; original PARTIAL rating upgrades to SOUND for the regime-match failure mode.**

**479fc2a regime-match-quality guard (vision-audit Critical Rec #2):**
`compute_regime_match_quality` was introduced as a new pure function at `math_engine.py:1611-1726`. It computes mean squared Mahalanobis-style distance from today's z-scored (SPY return, rolling vol) query to K=150 nearest candidate-pool neighbours.

Key design elements verified:
- Named constant `MC_REGIME_MATCH_CHI2_THRESHOLD = 9.21034037197618` at `math_engine.py:119` with 9-line comment explaining the chi2(2)_{0.99} derivation and why the conservative single-draw threshold is appropriate for the mean-of-K statistic.
- Fail-safe sentinel: `mean_sq_mahalanobis=None` → `is_unprecedented=False` at `math_engine.py:1659-1668`. An absent diagnostic never suppresses the protective stop.
- z-score standardization parameters at `math_engine.py:1691-1702` are drawn from the same candidate pool (lines 1686-1690) using identical arithmetic to `run_monte_carlo:1048-1059`. AC-1 parity confirmed: both functions use `_z(values, mean, std)` with `if std == 0.0: return np.zeros_like(...)`.
- Env-var operator override at `math_engine.py:1647-1648`:
  ```python
  _env_override = os.environ.get("MC_REGIME_MATCH_CHI2_THRESHOLD")
  threshold = float(_env_override) if _env_override is not None else MC_REGIME_MATCH_CHI2_THRESHOLD
  ```
- Eligible-pool boundary at `math_engine.py:1657-1658` mirrors `run_monte_carlo:1012-1013` exactly: `eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1); if eligible_days < MC_MIN_HISTORY_DAYS:`.

**Call-path (alpha_bot_execution.py:1125-1139):**
```python
_regime_assessment = math_engine.compute_regime_match_quality(historical_data, spy_today)
if _regime_assessment.is_unprecedented:
    prob_beating = None
```
When `is_unprecedented=True`, `prob_beating` is overridden to `None`. Downstream, `mc_available = prob_beating is not None` (line 1159) ensures the arm/disarm/TP branches skip. `compute_exit_confirmation` receives `prob_beating=None`, which triggers the existing MC-unavailable fail-safe path (line 1508: `mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD`). The protective stop fires on ticks-below-stop alone. This is the correct fail-safe pattern.

**e46d436 RegimeMatchAssessment `__post_init__` guard:**
At `math_engine.py:220-228`:
```python
def __post_init__(self) -> None:
    if self.mean_sq_mahalanobis is None and self.is_unprecedented:
        raise ValueError(
            "RegimeMatchAssessment: mean_sq_mahalanobis is None but "
            "is_unprecedented is True — fail-safe violated. ..."
        )
```
This mirrors the `CVaRAssessment.__post_init__` precedent (line 164-169). The guard enforces that an absent diagnostic cannot trigger suppression at construction time, not just in caller logic.

**PERF-001 rolling vol helper (e8a6c00) — numerical identity:**
`_compute_rolling_spy_vol` at `math_engine.py:923-976`:
- Full-window phase (lines 955-960): `as_strided` + `np.std(axis=1, ddof=0)` — bit-exact with the reference `np.std(spy_returns[start:i+1])` because both use the same two-pass numpy std algorithm on identical data.
- Growing-window phase (lines 962-974): cumsum arithmetic with `np.maximum(var, 0.0)` to prevent negative-variance from float cancellation. Matches reference to `< 1e-15`. These indices [1..w-2] are excluded from the kNN candidate pool by the eligible-pool guard; they only feed CVaR and regime-match's full-array consumers.
- NaN guard: `_reject_non_finite_in_records` at `run_monte_carlo:991-998` fires BEFORE `_compute_rolling_spy_vol` is called. The `as_strided` masking concern does not apply because inputs are pre-validated.
- The `spy_vols[0] == 0.0` invariant is enforced by initialization (`result = np.zeros(n, dtype=float)` at line 950) and the early-window exclusion guard ensuring index 0 is never in the full-window phase.

**PERF-002 sorted-dates LRU cache (38d2d7a):**
`_sorted_dates(historical_data)` at `math_engine.py:887-920`. Cache is keyed by `id(historical_data)` (object identity). An identity-mismatch guard at line 910-913 defends against CPython id reuse after GC. The LRU is bounded by `_SORTED_DATES_CACHE_MAXSIZE = 32`. Sorted order is deterministic for a fixed dict. This is a pure-refactor; no math change.

**Eligible-pool boundary unchanged:** `MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) = 20 + 19 = 39` raw days. The boundary check at `math_engine.py:1012-1013` is word-for-word identical to pre-closure state.

**Delta vs original:** Vision-audit Surface 3 was PARTIAL due to missing regime-match-quality guard. That gap is now closed. Original soft spots (MC-gated exits is `[Folklore]`, 5000 paths over 150 neighbors) are acknowledged but unchanged — they are methodology choices, not defects introduced by the closures.

---

### Surface 4 — 6-layer exit priority (`resolve_trigger_priority`)

**Verdict: SOUND — no change from original.**

No closure touched the exit priority resolver. `resolve_trigger_priority` at `math_engine.py:836-859` and `_TRIGGER_PRIORITY_ORDER` at lines 826-833 are byte-identical to the vision-audit state. The canonical order `VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop` is unchanged.

**Call-path (alpha_bot_execution.py:1484-1491):**
```python
reason, also_true = math_engine.resolve_trigger_priority(
    is_vwap_broken=is_vwap_broken,
    is_tp_hit=tp_triggered_now,
    is_vwap_bleed_broken=is_vwap_bleed_broken,
    is_trailing_stop_hit=is_trailing_stop_hit,
)
```
The resolver is still the sole priority decision point; no parallel branch routes to a different priority ordering.

**Delta vs original:** None.

---

### Surface 5 — BHY haircut + N_effective additive accounting

**Verdict: SOUND — ef83ef4 Yekutieli memoization is provably math-preserving.**

**ef83ef4 `_yekutieli_c_n` memoization:**
`autotuner.py:508-515`:
```python
@functools.lru_cache(maxsize=None)
def _yekutieli_c_n(n: int) -> float:
    """Yekutieli arbitrary-dependence factor c(N) = sum_{j=1}^{N} 1/j."""
    return sum(1.0 / j for j in range(1, n + 1))
```
The body is identical ascending-j summation order to the pre-memoization inline sum. Float64 harmonic sums are deterministic for a fixed N. The `lru_cache(maxsize=None)` cache is integer-keyed — no collision risk, no numerical drift. The reviewer noted `c(500)=6.792823429990524` is bit-identical in the cache vs inline. The `benjamini_hochberg_adjust` at `autotuner.py:544` calls `_yekutieli_c_n(n)` — identical result, cheaper.

**PERF-006 anti-pattern check:** The dispatch rule says "Never use numpy vectorization shortcuts that mask NaN/inf propagation." The `_yekutieli_c_n` helper uses a pure Python sum — no numpy involved. The input `n` is an integer (trial count). No NaN/inf propagation risk.

**The BHY pipeline (autotuner.py:518-555) is byte-identical** to pre-closure state other than the `c_n = _yekutieli_c_n(n)` call. Path C (closed-form ln(n)+γ approximation) was blocked by the pre-existing M1 T2 pin in `test_m1_bhy_haircut_preservation.py` — the review confirmed this constraint is in place.

**N_effective additive accounting (autotuner.py:568-618):** unchanged. The NN1 honest case `S=0 → N_effective = N_optuna → byte-identical haircut` is preserved.

**Delta vs original:** None (ef83ef4 is a math-preserving micro-optimization).

---

### Surface 6 — NN1 spec-freeze (`NN1_HONEST_DISCIPLINES`)

**Verdict: SOUND — b7a599c LOGIC-M-1 docstring-only closure corrects a doc-code drift.**

**b7a599c (advisors/spec_critic.py):**
Pre-closure, the module docstring at line 9-10 incorrectly stated the acceptable disciplines included `BACKTEST_SELECTION`. The code's `_ACCEPTABLE_DISCIPLINES` frozenset at `spec_critic.py:75-82` correctly excluded it:
```python
_ACCEPTABLE_DISCIPLINES: frozenset = frozenset({
    "THEORY", "MANDATE", "STYLIZED_FACT", "POLITIS_WHITE",
    "CADENCE", "CALIBRATION",
})
```
Post-closure, the docstring names the six values explicitly and states BACKTEST_SELECTION exclusion with the NN1 rejection rationale. The frozenset is byte-for-byte identical to pre-closure state (confirmed by reviewer trail in the merge message).

**Runtime verification (from merge message):** "Test 5 confirms BACKTEST_SELECTION still produces BREACH at runtime" — the runtime guard was never broken, only the docstring was stale.

**NN1 enforcement chain** through `compute_n_effective` at `autotuner.py:568-618` is unchanged.

**Delta vs original:** The docstring drift has been corrected. The runtime enforcement was correct at the vision-audit and remains correct.

---

### Surface 7 — Walk-forward (125 days, 500 trials per symphony)

**Verdict: SOUND — no change from original.**

No closure touched the walk-forward parameters or the three-fold split logic. The 125-day history, 60/20/20 split, `PURGE_DAYS=20`, `EMBARGO_DAYS=1`, and 500-trial floor are unchanged.

**PERF-005 (468aad9) RED-IS-GREEN confirmation:** The `_resolve_optuna_n_jobs_from_env()` returning 1 by default (for SQLite RDBStorage contention safety) was already implemented in OPTUNA-6 cycle. PERF-005 added regression-test pins only. Zero production diff.

**Delta vs original:** None. The acknowledged soft spots (short 4-5 usable OOS days; statistical power limitation) are structural design choices not altered by any closure.

---

### Surface 8 — Volatility scaling, log-time squeeze, parabolic ratchet

**Verdict: MIXED (improved from PARTIAL) — M3 R1 replaces the practitioner heuristic with a THEORY anchor; R2 honest-flag caveat added.**

**79759db M3 R1 — sqrt-time decay replaces log10 heuristic:**
Pre-closure: `log10(1 + 9*t)` with no published anchor (four free constants after the 2026-05-15 empirical re-calibration).
Post-closure: `f(t) = 1 - sqrt(1 - t)` at `math_engine.py:322`:
```python
decay_curve = 1.0 - math.sqrt(1.0 - time_ratio)
```
The provenance comment at `math_engine.py:232-244` cites Danielsson & Zigrand (2003) and derives the formula from first principles: "Under the standard square-root-of-time scaling for i.i.d. log-returns with constant per-unit-time variance, the standard deviation of remaining-session returns scales as sqrt(1-t); tightness (1 - remaining_std / full_std) is therefore 1 - sqrt(1-t)." Zero free parameters — THEORY freeze_discipline.

This changes the curve shape midday (~0.45 pp wider stop at t=0.5 vs the pre-M3 log10 curve). The direction is correct: the new curve is less aggressive midday and monotone-converging at both endpoints (f(0)=0, f(1)=1 exactly). The pre-M3 `DECAY_CURVE_SCALAR` free parameter has been deleted.

Constants at `math_engine.py:246-250` (`MULT_OPEN=1.5`, `MULT_CLOSE=0.5`, `MIN_STOP_OPEN=0.3`, `MIN_STOP_CLOSE=0.15`, `VOL_FALLBACK=1.0`) are unchanged from vision-audit state. These are CALIBRATION-frozen, not THEORY-derived — honest provenance.

**b0d1e2b R2 honest-flag caveat:**
At `math_engine.py:769-773`:
```
# Honest-flag (risk-m3): the regime-switch discretization above is an
# interpretive extension of Peskir 1998's continuous-boundary result,
# NOT a formally proven theorem in Peskir 1998 itself. The gate remains
# the best available THEORY anchor under NN1; empirical performance +
# freeze_discipline = THEORY are the binding operator guarantees.
```
Both required substrings are present: "interpretive extension" and "NOT a formally proven theorem in Peskir 1998". The honest-flag is additive to the Leung-Zhang / Peskir citations added in M3; no citation was removed.

**Parabolic PARA-ARM:** Unchanged from vision-audit state. The `prev_return=0` day-boundary reset behavior (auto-arms on any gap-open above threshold) was flagged as the most vulnerable surface in the original audit. No closure addressed it. Still `[Folklore]` and still an open question for the operator.

**PERF-001 vol-scaling numerical identity:** `calculate_20d_vol` at `math_engine.py:1104-1136` uses `_sorted_dates` (via PERF-002) and `np.std` — bit-identical to pre-closure state. `calculate_14d_atr_pct` at lines 1139-1193 is unchanged.

**Delta vs original:** Surface 8 was PARTIAL due to four concerns: (1) log10 heuristic no anchor — CLOSED by M3 R1 with THEORY derivation; (2) four free constants — REDUCED to zero by M3 (DECAY_CURVE_SCALAR deleted); (3) R2 honest-flag caveat missing — CLOSED by b0d1e2b; (4) PARA-ARM day-boundary semantic undocumented — STILL OPEN. The surface upgrades from PARTIAL to MIXED: the log-time squeeze is now anchored, PARA-ARM remains the single open item.

---

## Sentinel Disciplines

### MC Eligible-Pool Sentinel: PRESERVED

`MC_INSUFFICIENT_HISTORY_SENTINEL = None` at `math_engine.py:86`. `run_monte_carlo` returns `None` at `math_engine.py:1018` when `eligible_days < MC_MIN_HISTORY_DAYS`. `compute_exit_confirmation` treats `None` as "MC confirmation unavailable" and passes the protective stop (line 508: `mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD`). Blast radius unchanged: 7+ consumer sites in `alpha_bot_execution` / `reporting` / `synthetic_history` / `autotuner` all expect `float | None`.

### CVaR Atom Convention Sentinel: PRESERVED

`CVAR_MIN_TAIL_OBS = 1` at `math_engine.py:132`. The R-U formula sentinel fires when `k < CVAR_MIN_TAIL_OBS` at `math_engine.py:1329`. `cvar_pct=None` is the out-of-band sentinel; `breach=False` is hard-coded. `CVaRAssessment.__post_init__` enforces the invariant at construction time. The `tail_obs_count = k + (1 if fractional_weight > 0 else 0)` canonical formula is correctly implemented at `math_engine.py:1342` and documented in both `CVaREstimate.__doc__` and `compute_cvar_5pct_general_distribution.__doc__`.

### Sortino Sentinel: PRESERVED

`_SORTINO_SENTINEL = 1e6` at `math_engine.py:15`. `filter_sortino_sentinels` at lines 18-30 removes sentinels before the BHY haircut. This was not touched by any closure.

### RegimeMatchAssessment Sentinel: NEW (e46d436) — SOUND

`RegimeMatchAssessment.__post_init__` raises `ValueError` on `(mean_sq_mahalanobis=None, is_unprecedented=True)`. The legal sentinel `(None, False)` is constructed at `math_engine.py:1659-1668` (insufficient history path) — confirmed still valid. The legal non-sentinel `(float, True/False)` is constructed at `math_engine.py:1720-1726` — confirmed still valid.

---

## PERF Optimization Audit (Numerical Identity)

| Finding | Method | Identity claim | Verification |
|---------|--------|---------------|--------------|
| PERF-001 rolling vol | as_strided + cumsum | Full-window: bit-exact; growing-window: <1e-15 | AC-PERF001.2/3 tests monkeypatch reference; growing-window only used by CVaR/regime-match (diagnostic path, not exit trigger) |
| PERF-002 sorted-dates | OrderedDict LRU | Identical sort order for same dict object | Cache key = object id + stored-dict identity check; AC-PERF002.7 |
| PERF-004 non-finite hoist | Verdict: hoist UNSAFE | Per-call scan preserved | Outcome B pin + compute_regime_match_quality gap closed (+4 lines) |
| PERF-005 n_jobs=1 default | RED-IS-GREEN | No production diff | Pre-existing OPTUNA-6 fix; regression-test pins only |
| PERF-006 Yekutieli c(N) | lru_cache | Bit-identical for same N | Ascending-j summation preserved; float64 deterministic |

No PERF closure introduces magic numbers. No PERF closure alters the decision surface for any of the 6 exit layers.

---

## Top Concerns (post-closure)

1. **PARA-ARM day-boundary semantic remains undocumented.** The `prev_return=0` reset at new-day boundary means velocity on the first cycle = `current_return`, auto-arming on any gap-open above threshold. The original audit flagged this was empirically observed (11/11 symphonies arming on 2026-05-15 open). No closure addressed it. This is the single remaining open documentation question from the vision-audit for Surface 8. Operator impact: unexpected early PARA-ARM during gap opens. Priority: low (behavior may be intended), but an explicit operator-facing note in the README would close it.

2. **PERF-001 growing-window ULP drift in CVaR / regime-match.** The cumsum variance formula introduces `< 1e-15` drift vs the reference loop for indices [1..w-2] (the growing-window phase). This drift affects `compute_portfolio_cvar` and `compute_regime_match_quality` for their full-array consumers. CVaR is diagnostic-only so this cannot affect exit decisions. The regime-match threshold is 9.21 — a `< 1e-15` drift in the z-score features will not change the chi-squared test outcome except at the single-digit precision threshold. This is effectively a non-issue, but worth noting for completeness.

3. **RegimeMatchAssessment `__post_init__` is narrower than CVaRAssessment.** The CVaRAssessment has four invariant guards; RegimeMatchAssessment has one. This is intentional — `RegimeMatchAssessment` has fewer sentinel-combination failure modes. The single guard covers the critical path: `(None, True)` suppression on absent data. No defect.

---

## Final Verdict

**STRONG**

The 8 vision-audit surfaces are in materially better shape than at the original audit:

- **Surface 3 (MC gating):** The highest-risk failure mode (regime-break false veto) is now defended by the `compute_regime_match_quality` guard. Original PARTIAL rating upgrades to SOUND for the addressed failure mode.
- **Surface 8 (vol scaling / time squeeze):** The log-time squeeze has moved from `[Folklore]` to `[Tier-1 THEORY]` via the sqrt(1-t) derivation. The R2 honest-flag caveat is in place. PARA-ARM day-boundary semantic remains the single open documentation item.
- **Surface 2 (CVaR):** Stale Phase-2 framing replaced with accurate Phase-1 kNN regime-match description. REJECT-wall integrity confirmed by call-path trace.
- **Surfaces 1, 4, 5, 6, 7:** Unchanged from vision-audit state; remain SOUND.

All PERF optimizations are provably math-preserving: either bit-exact (PERF-001 full-window, PERF-002, PERF-006) or bounded-drift on diagnostic-only paths (PERF-001 growing-window). Non-finite sentinel discipline is intact across all three members of the regime-match / MC / CVaR triad. The CVaR-divergence REJECT wall is preserved. The BHY haircut + NN1 + CRRA-EU + MC-gating pipeline is structurally sound.

The single remaining unresolved item (PARA-ARM day-boundary documentation) is an operator-facing documentation gap, not a math correctness defect.

---

## Hard-rule compliance log

- **Read-only.** This document is the only artifact authored; no production `.py` file touched.
- **Worktree only.** Written to `docs/audit/final-audit-2026-05-29/math-soundness.md` in the `audit/final-2026-05-29-math` worktree.
- **Cited.** Every finding cites `file:line` with quoted context.
- **Call-path verified.** CVaR REJECT-wall traced from `compute_portfolio_cvar` through `alpha_bot_execution` — zero non-test consumers of `.breach`. MC suppression call-path traced through `compute_regime_match_quality → is_unprecedented → prob_beating=None → compute_exit_confirmation MC-unavailable fail-safe`.
- **No re-litigation.** EUT+CVaR migration (rejected per `project_eut_cvar_migration_council_verdict`) and CVaR-divergence detector (rejected per `project_cvar_divergence_validation_wall`) are not re-opened.
