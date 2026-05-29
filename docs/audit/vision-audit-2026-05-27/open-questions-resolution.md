# Open Provenance Questions — Resolution (OQ-1..OQ-10)

**Resolved by:** oq-docs worker, cycle `cycle/oq-1-to-10`
**Source of truth:** [`logic-trace.md §6`](logic-trace.md) (OQ table) and [`SYNTHESIS.md §B-B2`](SYNTHESIS.md) (treatment instruction)
**Protocol:** Read actual source at cited file:line; classify as CITE / TAG-OPEN / CROSS-LINK; no production code modified.

**Score: 4 CITE / 5 TAG-OPEN / 1 CROSS-LINK**

---

## OQ-1 — `_TRIGGER_PRIORITY_ORDER`: Take-Profit ahead of VWAP Bleed Cut

**Constant:** `_TRIGGER_PRIORITY_ORDER` at `math_engine.py:826-831`

**Classification:** TAG-OPEN

**In-code comment** (`math_engine.py:824-825`):
> "Canonical priority order: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop. Order matches H2 acceptance criteria (alpha_bot_execution.py:1081 comment) and the math audit."

**Resolution:** The H2 acceptance-criteria document is not present on this branch. The reference at `alpha_bot_execution.py:1081` does not contain acceptance-criteria text in the current file. No first-principles argument for the specific relative position of Take-Profit ahead of VWAP Bleed Cut exists in `DECISIONS.md`, `feature-plans/`, or in-file comments beyond the stale H2 citation. The council synthesis (SYNTHESIS.md §G-3) retains it as the incumbent order without arguing the specific pairwise comparison.

[open question — pending H2 acceptance-criteria recovery or first-principles argument for TP > Bleed Cut priority position]

---

## OQ-2 — `n_trials=500` for the per-symphony walk-forward

**Constant:** `OPTUNA_N_TRIALS_PRODUCTION = 500` at `autotuner.py:153`

**Classification:** CITE

**In-code comment** (`autotuner.py:139-153`):
> "Reducing production_n_trials BELOW the 5x-headroom adequacy line (production >= 5 * floor = 5 * 100 = 500) weakens the haircut materially."
> "Statistical-stability floor (project rule — see CLAUDE.md Known Gotchas): Minimum n_trials for the TPE sampler to adequately explore the 6-D search space is 100. Below 100 the sampler under-explores and the BHY c(N) factor is materially weaker (c(50) ≈ 4.50 vs c(100) ≈ 5.19). OPTUNA_N_TRIALS_CALIBRATION equals the floor exactly — the calibration sweep IS the floor. OPTUNA_N_TRIALS_PRODUCTION is 5x that floor."
> "BHY \ Yekutieli c(N) rationale: c(100) ≈ 5.19, c(500) ≈ 6.79 (≈30% larger at production floor)"

**Resolution:** 500 = 5 × the 100-trial statistical-stability floor. The 5x multiplier yields a materially stronger BHY haircut (c(500)/c(100) ≈ 1.30 — approximately 30% more conservative). This is documented in `autotuner.py:139-153` as the explicit rationale. Not an arbitrary choice; the floor of 100 is the TPE adequacy minimum for the 6-dimensional search space, and 500 is the defensible production headroom above it.

---

## OQ-3 — `MC_DEFAULT_NEIGHBOR_K = 150`

**Constant:** `MC_DEFAULT_NEIGHBOR_K = 150` at `math_engine.py:92-93`

**Classification:** TAG-OPEN

**In-code comment** (`math_engine.py:92-93`):
> "Default kNN regime locality — smaller=tighter regime match, larger=smoother estimate"

**Resolution:** The comment documents the qualitative tradeoff (tighter locality vs smoother estimate) but names no calibration source, simulation study, or peer-reviewed anchor for the specific value 150. No record of whether 150 was arrived at by empirical evaluation or copied from an upstream source. The math review (SYNTHESIS.md Theme 3) notes the absence of a regime-match-quality guard as the highest-leverage open gap; the value of K is upstream of that gap.

[open question — pending calibration study or regime-locality literature citation for K=150]

---

## OQ-4 — `MC_DEFAULT_SIMULATION_PATHS = 5000`

**Constant:** `MC_DEFAULT_SIMULATION_PATHS = 5000` at `math_engine.py:91`

**Classification:** TAG-OPEN

**In-code comment** (`math_engine.py:91`):
> "Default MC path count — CLT stability vs runtime tradeoff"

**Resolution:** The comment acknowledges the tradeoff but provides no runtime-budget figure or convergence analysis anchoring 5000 over 1000 or 10000. The CLT argument implies the goal is a stable estimator variance, but no convergence criterion (e.g., SE of the MC probability estimate < ε) is stated. May matter for Phase-2 latency audit as the per-cycle path approaches tighter timing constraints.

[open question — pending CLT-convergence analysis or runtime-budget anchor for paths=5000]

---

## OQ-5 — `PARABOLIC_VELOCITY_THRESHOLD` default + `MAX_PARABOLIC_SQUEEZE`

**Constants:** `PARABOLIC_VELOCITY_THRESHOLD = 2.0`, `MAX_PARABOLIC_SQUEEZE = 0.50` at `alpha_bot_execution.py:91-92`

**Classification:** TAG-OPEN

**In-code source** (`alpha_bot_execution.py:90-92`):
```
# --- PARABOLIC PARAMETERS ---
PARABOLIC_VELOCITY_THRESHOLD = float(os.getenv("PARABOLIC_VELOCITY_THRESHOLD", "2.0"))
MAX_PARABOLIC_SQUEEZE = float(os.getenv("MAX_PARABOLIC_SQUEEZE", "0.50"))
```

No comment accompanies the default values. Both are env-overridable and operator-tunable via `acc_params`. The `prev_return=0` reset at the start of each new day means any symphony opening above 2% from its previous close auto-arms PARA on the first cycle — may be intended ("any large overnight gap arms the squeeze") or unintended. The SYNTHESIS.md Tier-B backlog item B-B4 documents this day-boundary semantic as an open operator decision.

**Resolution:** Default values have no documented calibration provenance. The day-boundary auto-arm behavior (observed 2026-05-15: 11/11 symphonies armed on open) is also undocumented as intended vs unintended.

[open question — pending operator decision on default values + day-boundary auto-arm intent; see SYNTHESIS.md B-B4]

---

## OQ-6 — `VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS`

**Constants:** `VWAP_CROSS_HWM_PCT = 1.0` at `alpha_bot_execution.py:81`; `VWAP_BLEED_MULTIPLIER` and `VWAP_BLEED_TICKS` are Optuna-searched parameters within the walk-forward surface.

**Classification:** CROSS-LINK

**In-code source** (`math_engine.py:748-771`): The System A (VWAP Breakdown) comment cites Leung & Zhang 2019 ("Optimal Trading with a Trailing Stop," Applied Mathematics & Optimization 80, 669-698) and Peskir 1998 (Annals of Probability 26(4), 1614-1640) as the THEORY anchor for the **regime-switch structure**. The comment explicitly notes: "the regime-switch discretization above is an interpretive extension of Peskir 1998's continuous-boundary result, NOT a formally proven theorem in Peskir 1998 itself. The gate remains the best available THEORY anchor under NN1; empirical performance + freeze_discipline = THEORY are the binding operator guarantees."

**Resolution:** The structural choice of a regime switch is THEORY-anchored (Leung & Zhang 2019, Peskir 1998). The **specific threshold values** (`vwap_cross_hwm_pct`, bleed multiplier, bleed ticks) remain Optuna-searched parameters within the BHY haircut surface. Re-derivation of the threshold values with published provenance is the Phase-1.5 M3 R2 target. This is the one OQ already on a documented remediation track.

Cross-link: Phase-1.5 M3 R2 re-derivation of VWAP×2 thresholds. See SYNTHESIS.md Tier-C backlog item B-C1.

---

## OQ-7 — `VWAP_OPEN_WINDOW_GRACE_MINUTES = 15`

**Constant:** `VWAP_OPEN_WINDOW_GRACE_MINUTES = 15` at `alpha_bot_execution.py:73`

**Classification:** TAG-OPEN

**In-code comment** (`alpha_bot_execution.py:71-73`):
> "Suppress VWAP-Breakdown and VWAP-Bleed-Cut for this many minutes after EXECUTION_START_TIME to avoid open-volatility false exits (V2, AC-V2.1). TP and Trailing Stop are unaffected."

**Resolution:** The rationale for the grace-window concept is documented (suppress open-volatility false exits per V2 / AC-V2.1). The specific value 15 minutes is not justified in code, feature-plans, or DECISIONS.md. The env-override mechanism (`os.getenv("VWAP_OPEN_WINDOW_GRACE_MINUTES", "15")`) indicates it is operator-tunable, consistent with empirical origin. Why 15 and not 10 or 30 is unrecorded.

[open question — pending calibration study or operator-empirics citation for grace window = 15 minutes]

---

## OQ-8 — 60/20/20 walk-forward ratio

**Constants:** `TRAIN_RATIO = 0.60`, `VALIDATION_RATIO = 0.20`, `FROZEN_EVAL_RATIO = 0.20` at `autotuner.py:290-297`

**Classification:** CITE

**In-code comment** (`autotuner.py:291-294`):
> "Three-fold walk-forward ratios: 60% train / 20% validation / 20% frozen-eval. Selection is on validation; frozen-eval is consumed once post-selection for honest performance reporting. Purge + embargo applied at BOTH fold boundaries. 60/20/20 split is an operator choice for AlphaBot's data scale (125 trading days); the held-out frozen-eval invariant derives from LdP 2018 Ch. 7.4 (not the specific ratio)."

**Resolution:** The in-code comment is an explicit, honest provenance statement. The 60/20/20 ratio is acknowledged as an operator calibration choice for the 125-day data budget. The theoretical mandate — that the held-out frozen-eval fold exists and is consumed exactly once post-selection — derives from López de Prado 2018 Ch. 7.4. The specific split is not theoretical; it is the practical operator compromise to extract any honest frozen-eval slice from a 125-day window after purge=20. This is honest provenance, not a gap.

---

## OQ-9 — `HARVEY_LIU_FDR_Q = 0.05`

**Constant:** `HARVEY_LIU_FDR_Q = 0.05` at `autotuner.py:373`

**Classification:** CITE

**In-code comment** (`autotuner.py:368-373`):
> "Benjamini-Hochberg false-discovery-rate level for the selection haircut. A trial is deployable only if its BHY-adjusted p-value is <= this q. Conventional 0.05 (Harvey & Liu 2015 use FDR control for best-of-N strategy selection; BHY rather than Bonferroni because Bonferroni at N~500 is brutally over-conservative). Policy dial — the operator may tighten/loosen the selection strictness here."

**Resolution:** q=0.05 is the field-standard false-discovery-rate threshold from Harvey & Liu (2015) "Backtesting," Journal of Portfolio Management 42(1), 13-28. The in-code comment explicitly names it as a policy dial — not a black-box default. An operator who wants stricter rejection of overfit parameter sets can lower q; an operator in a data-poor regime who is comfortable with looser acceptance can raise it. Honest provenance.

---

## OQ-10 — `_SORTINO_SENTINEL = 1e6`

**Constant:** `_SORTINO_SENTINEL = 1e6` at `math_engine.py:15`

**Classification:** TAG-OPEN

**In-code comment** (`math_engine.py:9-15`):
> "Sentinel returned by compute_sortino_ratio (autotuner.py) when downside_deviation==0 (all trial returns beat the target). The value is finite and looks like a valid trial result to Optuna's TPE, but its magnitude (~1e6) would dominate the cross-trial distribution that the Harvey & Liu selection haircut counts over. Filtering before the haircut prevents a degenerate trial from masquerading as a genuine signal."

**Resolution:** The design requirement is documented: the sentinel must be (a) finite (Optuna TPE receives it without `NaN/Inf` rejection), (b) large enough to be detectable by `filter_sortino_sentinels` without colliding with legitimate trial values. The **specific magnitude 1e6** satisfies both requirements but was not calibrated against the empirical distribution of non-sentinel Sortino values to prove it cannot collide. An alternative such as `float('inf')` would be cleaner semantically but Optuna TPE behavior with infinite values is implementation-dependent.

[open question — pending confirmation that 1e6 cannot collide with legitimate trial Sortino values in the production search space, or replacement with a semantically cleaner sentinel]

---

*No production code modified. Doc-only cycle. Branch: `cycle/oq-1-to-10`.*
