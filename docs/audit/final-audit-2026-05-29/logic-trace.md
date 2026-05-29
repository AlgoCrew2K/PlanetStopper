# Logic Narrative Audit — Final (2026-05-29)

## Metadata
- **Auditor:** final-audit-logic-respawn-1 (vision-audit lens)
- **Branch:** `audit/final-2026-05-29-logic`
- **HEAD under audit:** `4b684db` (plan/finalist-a-scaffold)
- **Source audit:** `docs/audit/vision-audit-2026-05-27/logic-trace.md`
- **OQ resolution:** `docs/audit/vision-audit-2026-05-27/open-questions-resolution.md`
- **Scope:** Narrative coherence · logic chain · OQ provenance · CVaR docstrings

---

## §1. Logic Chain Trace: 6 math layers → 4 exit triggers → resolve_trigger_priority

**Verdict: COHERENT (narrative) with LINE-NUMBER DRIFT (citations)**

The README's narrative description of the logic chain is accurate to the code. The six math layers are correctly identified as feeding four exit triggers via `resolve_trigger_priority`. The per-symphony loop structure, the fail-safe pattern (MC None → gate passes), and the resolver co-fire telemetry are all correct. The logic itself is sound.

However, the README and original `logic-trace.md` carry stale line-number references for `math_engine.py` functions that have moved since the logic-trace was authored. These are citation-accuracy failures, not logic failures.

### Confirmed drift in code-anchor citations

| Document | Cited location | Cited purpose | Actual current location |
|---|---|---|---|
| README §2, §4.2, §5 Step 6, `logic-trace.md` §2 | `math_engine.py:736-759` | `resolve_trigger_priority` function | `math_engine.py:836-859` |
| README §4.2, §5 Step 6 | `math_engine.py:728-733` | `_TRIGGER_PRIORITY_ORDER` constant | `math_engine.py:826-833` |
| README §5 Step 6 | `alpha_bot_execution.py:1428-1441` | Resolver guard + `resolve_trigger_priority` call | `alpha_bot_execution.py:1478-1491` |
| README §5 Step 7 | `alpha_bot_execution.py:1459-1469` | `execution_queue.append` | `alpha_bot_execution.py:1509-1530` |

Lines 736-759 in the current `math_engine.py` are inside the body of `compute_vwap_breakdown_update` (the VWAP gate logic), not `resolve_trigger_priority`. Lines 728-733 are inside that same function's docstring. The resolver is 100 lines further down than cited.

Lines 1428-1441 in the current `alpha_bot_execution.py` are chart-data append code and the beginning of CVaR computation — not the resolver guard. The resolver guard `if (is_trailing_stop_hit or tp_triggered_now ...)` is at lines 1478-1491.

**Evidence (resolver actual location):**
```python
# math_engine.py:826-833 (actual)
# Canonical priority order: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop.
# Order matches H2 acceptance criteria (alpha_bot_execution.py:1081 comment) and the math audit.
_TRIGGER_PRIORITY_ORDER: list[str] = [
    "VWAP Breakdown",
    "Take-Profit",
    "VWAP Bleed Cut",
    "Trailing Stop",
]
```

```python
# math_engine.py:836-859 (actual)
def resolve_trigger_priority(
    is_vwap_broken: bool,
    is_tp_hit: bool,
    is_vwap_bleed_broken: bool,
    is_trailing_stop_hit: bool,
) -> tuple[str | None, list[str]]:
    ...
```

The narrative description of what these symbols do is accurate throughout. Only the line numbers are stale.

---

## §2. OQ Verification (OQ-1..OQ-10)

### OQ-1 — `_TRIGGER_PRIORITY_ORDER`: TP before Bleed Cut — **PASS**

**Classification in resolution:** TAG-OPEN. Correctly identified as lacking a first-principles argument. The in-code comment (`math_engine.py:826-827`) is quoted accurately:
> "Canonical priority order: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop. Order matches H2 acceptance criteria (alpha_bot_execution.py:1081 comment) and the math audit."

The OQ-resolution correctly notes the H2 acceptance-criteria document is absent from this branch. TAG-OPEN classification is honest. Line reference in the resolution (`math_engine.py:826-831`) is accurate to the actual location (826-833). Minor range imprecision, not a false claim.

### OQ-2 — `OPTUNA_N_TRIALS_PRODUCTION = 500` — **PASS**

**Classification:** CITE. Source at `autotuner.py:139-153` confirmed accurate. The resolution quotes:
> "Reducing production_n_trials BELOW the 5x-headroom adequacy line (production >= 5 * floor = 5 * 100 = 500) weakens the haircut materially."
> "OPTUNA_N_TRIALS_PRODUCTION is 5x that floor."

Cross-checked against actual `autotuner.py:144-153` — the text matches verbatim. The CITE classification is correct: 500 is explicitly documented as 5× the 100-trial TPE stability floor with a BHY c(N) rationale (c(500)/c(100) ≈ 1.30).

### OQ-3 — `MC_DEFAULT_NEIGHBOR_K = 150` — **PASS**

**Classification:** TAG-OPEN. Source at `math_engine.py:92-93` confirmed accurate:
> "Default kNN regime locality — smaller=tighter regime match, larger=smoother estimate"

No calibration citation present. TAG-OPEN is honest.

### OQ-4 — `MC_DEFAULT_SIMULATION_PATHS = 5000` — **PASS**

**Classification:** TAG-OPEN. Source at `math_engine.py:91` confirmed accurate:
> "Default MC path count — CLT stability vs runtime tradeoff"

No convergence criterion or runtime-budget anchor present. TAG-OPEN is honest.

### OQ-5 — `PARABOLIC_VELOCITY_THRESHOLD = 2.0`, `MAX_PARABOLIC_SQUEEZE = 0.50` — **PASS**

**Classification:** TAG-OPEN. Source at `alpha_bot_execution.py:90-92` confirmed accurate:
```python
# --- PARABOLIC PARAMETERS ---
PARABOLIC_VELOCITY_THRESHOLD = float(os.getenv("PARABOLIC_VELOCITY_THRESHOLD", "2.0"))
MAX_PARABOLIC_SQUEEZE = float(os.getenv("MAX_PARABOLIC_SQUEEZE", "0.50"))
```
No provenance comment on default values. The day-boundary `prev_return=0` auto-arm concern is also valid (the `prev_return` reset is present in the live loop). TAG-OPEN is honest.

### OQ-6 — VWAP threshold values — **PASS**

**Classification:** CROSS-LINK. Source at `math_engine.py:748-771` (the VWAP breakdown function body with Leung & Zhang 2019 + Peskir 1998 citations). The regime-switch structure is confirmed THEORY-anchored by those citations. The resolution correctly identifies that the specific threshold VALUES (`vwap_cross_hwm_pct`, bleed multiplier, bleed ticks) remain Optuna-searched. CROSS-LINK to Phase-1.5 M3 R2 is appropriate and the only OQ with a documented remediation track.

### OQ-7 — `VWAP_OPEN_WINDOW_GRACE_MINUTES = 15` — **PASS**

**Classification:** TAG-OPEN. Source at `alpha_bot_execution.py:71-73` confirmed accurate:
> "Suppress VWAP-Breakdown and VWAP-Bleed-Cut for this many minutes after EXECUTION_START_TIME to avoid open-volatility false exits (V2, AC-V2.1). TP and Trailing Stop are unaffected."

The grace-window concept is justified; the specific value 15 has no calibration citation. TAG-OPEN is honest.

### OQ-8 — 60/20/20 walk-forward ratio — **PASS with minor note**

**Classification:** CITE. Source quoted as `autotuner.py:291-294`. The actual comment block begins at line 290 ("Three-fold walk-forward ratios..."), making the citation `291-294` a minor off-by-one. The quoted content is accurate to lines 290-294. The CITE classification is correct — the comment explicitly documents honest provenance ("operator choice for AlphaBot's data scale"). No substantive error.

### OQ-9 — `HARVEY_LIU_FDR_Q = 0.05` — **PASS**

**Classification:** CITE. Source at `autotuner.py:368-373` confirmed accurate. The resolution quotes:
> "Benjamini-Hochberg false-discovery-rate level for the selection haircut. A trial is deployable only if its BHY-adjusted p-value is <= this q. Conventional 0.05 (Harvey & Liu 2015 use FDR control for best-of-N strategy selection; BHY rather than Bonferroni because Bonferroni at N~500 is brutally over-conservative). Policy dial — the operator may tighten/loosen the selection strictness here."

This matches `autotuner.py:368-372` verbatim. CITE classification is correct.

### OQ-10 — `_SORTINO_SENTINEL = 1e6` — **PASS**

**Classification:** TAG-OPEN. Source at `math_engine.py:9-15` confirmed accurate. The resolution quotes the design requirement correctly (finite, detectable, can't collide with legitimate values) and honestly acknowledges the magnitude is not calibrated against the empirical trial distribution. TAG-OPEN is honest.

---

## §3. CVaR Docstring Verification

### `math_engine.py:135-136` — **PASS**

```python
# kNN historical regime-match result (Phase-1; the forward-path co-signal was REJECTED
# per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md CVaR-divergence wall).
```

Correctly describes Phase-1 kNN regime-match. No Phase-2 forward-path framing. The REJECT-wall reference is explicit.

### `math_engine.py:142-144` — **PASS**

```python
class CVaRAssessment:
    """Typed result for the kNN historical regime-match (Phase-1; the forward-path
    co-signal was REJECTED per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md
    CVaR-divergence wall).
```

Correctly describes Phase-1 kNN regime-match. The REJECT-wall reference is explicit. The `__post_init__` fail-safe invariant (`cvar_pct is None → breach is False`) is present at `math_engine.py:139-146` (as a module-level comment before the class) and enforced in code.

### `math_engine.py:1202-1203` — **PASS**

```python
    Distinct from CVaRAssessment: carries .stderr (H-2 binding) and is the return
    type of the pure-math kNN-pool estimator. CVaRAssessment is the typed result
    for the kNN historical regime-match (Phase-1; the forward-path co-signal was REJECTED
    per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md CVaR-divergence wall).
```

The `CVaREstimate` docstring correctly cross-references the Phase-1 regime-match framing and the REJECT-wall. No Phase-2 forward-path framing in any of the three docstrings.

### README CVaR description vs diagnostic-only + REJECT-wall reality — **MIXED**

The README describes CVaR correctly in §3.2, §4.4, and §8:
- §3.2: "CVaR is never a live trigger — it is operator instrumentation only." ACCURATE.
- §4.4: "CVaR is live. The per-cycle live path calls `compute_portfolio_cvar` for each managed symphony and writes the result to `cvar_diagnostic`... CVaR is never a live trigger." ACCURATE to the actual code at `alpha_bot_execution.py:1441-1476`.

**Finding — README §12 CVaR wire-up note is stale and contradictory:**

README §12 states:
> "The per-cycle live path writes all-`None` sentinels to `cvar_diagnostic` ([`alpha_bot_execution.py:1417-1426`](alpha_bot_execution.py)) instead of calling `compute_portfolio_cvar`. The dashboard CVaR panel renders the framing labels but the numeric cells are empty."

This contradicts §3.2, §4.4, and §8 — and the actual code. At `alpha_bot_execution.py:1441-1446` the live path **does** call `compute_portfolio_cvar` and passes the real result to `record_cvar_diagnostic` at lines 1465-1476. The all-None-sentinel deferral described in §12 does not exist in the current code. The §12 note appears to be a deferral comment that was written before the live wire-up was completed but was not removed afterward.

The cited line range `1417-1426` in §12 points to chart-data append code inside the per-symphony loop — not the CVaR write path.

---

## §4. Outstanding Provenance Gaps

### Gap 1 — README line-number drift (systematic, not a logic error)

All `math_engine.py:736-759` and `math_engine.py:728-733` citations in the README (and in `logic-trace.md`) should be updated to `math_engine.py:836-859` and `math_engine.py:826-833` respectively. The drift is ~100 lines and is consistent with code having been inserted between the original audit and the current state. The `alpha_bot_execution.py` citations are off by approximately 50-80 lines in the same direction. These are doc-maintenance gaps, not logic failures.

Affected README sections: §2 (dashboard description line 99), §4.2, §5 Steps 6-7, §10 architecture table. Affected source: `logic-trace.md` §2.b.

### Gap 2 — README §12 CVaR wire-up note (stale deferral)

The §12 note claiming CVaR writes all-None sentinels contradicts the live code and the §3.2/§4.4 narrative. This is a stale deferral comment that should be removed or updated to reflect that the Phase-1 live wire-up is complete. The claim that "the numeric cells are empty" is not verifiable from code alone (dashboard rendering is a separate concern) but the "all-None sentinels" characterization of the code path is demonstrably wrong.

### Gap 3 — OQ-11 (gamma default) absent from OQ-resolution

The README §12 OQ table includes OQ-11 (γ default, spec_bundles THEORY facet) that does not appear in `open-questions-resolution.md`. OQ-11 was added to the README's OQ table during the documentation cycle but was not included in the OQ-resolution document's scope (which covers OQ-1..OQ-10 per its header). This is a scope boundary, not a false-provenance claim. OQ-11 is tagged in the README as open and is honest.

---

## §5. Final Verdict

**Logic coherence: COHERENT**

The six-layer → four-trigger → resolver narrative is structurally accurate. The fail-safe pattern, co-fire telemetry, diagnostic-only CVaR, and REJECT-wall are all correctly represented in both the README and the CVaR docstrings. The OQ classification (4 CITE / 5 TAG-OPEN / 1 CROSS-LINK) is honest — every CITE quotes its source faithfully, every TAG-OPEN identifies a real absence of provenance, and the CROSS-LINK correctly points to the Phase-1.5 M3 R2 remediation track.

**Citation accuracy: DRIFTED**

Line-number citations for `resolve_trigger_priority` and `_TRIGGER_PRIORITY_ORDER` in `math_engine.py`, and for the resolver guard in `alpha_bot_execution.py`, are stale. The functions are present and correct; they have simply moved. This is a doc-maintenance gap requiring a line-number sweep, not a logic or provenance finding.

**One stale narrative claim:** README §12 CVaR wire-up note incorrectly states the live path writes all-None sentinels. The live path calls `compute_portfolio_cvar` and writes real results. §3.2 and §4.4 are correct; §12 is outdated. Severity: LOW (§3.2 and §4.4 are the normative descriptions; §12 is a "known limits" section note).

**Summary table:**

| Check | Result |
|---|---|
| 6-layer → 4-trigger → resolver narrative coherence | COHERENT |
| OQ-2 CITE: `OPTUNA_N_TRIALS_PRODUCTION=500` at `autotuner.py:139-153` | PASS — quoted verbatim and accurate |
| OQ-8 CITE: 60/20/20 at `autotuner.py:291-294` | PASS — minor off-by-one (comment starts line 290), content accurate |
| OQ-9 CITE: `HARVEY_LIU_FDR_Q=0.05` at `autotuner.py:368-373` | PASS — quoted verbatim and accurate |
| OQ-1 TAG-OPEN: no first-principles arg for TP > Bleed Cut | PASS — honestly tagged |
| OQ-3 TAG-OPEN: K=150 no calibration citation | PASS — honestly tagged |
| OQ-4 TAG-OPEN: paths=5000 no convergence criterion | PASS — honestly tagged |
| OQ-5 TAG-OPEN: parabolic defaults no provenance | PASS — honestly tagged |
| OQ-7 TAG-OPEN: grace window=15 no calibration | PASS — honestly tagged |
| OQ-10 TAG-OPEN: sentinel 1e6 magnitude not calibrated | PASS — honestly tagged |
| OQ-6 CROSS-LINK: VWAP thresholds → Phase-1.5 M3 R2 | PASS — correct cross-link |
| CVaR docstrings Phase-1 kNN framing | PASS — no Phase-2 forward-path framing at 135-136, 142-144, 1202-1203 |
| README CVaR diagnostic-only + REJECT-wall alignment | MIXED — §3.2/§4.4/§8 accurate; §12 wire-up note stale |
| `math_engine.py` line citations for `resolve_trigger_priority` | DRIFTED — cited as 736-759, actual 836-859 |
| `alpha_bot_execution.py` line citations for resolver guard | DRIFTED — cited as 1428-1441, actual 1478-1491 |

---

*Committed: 2026-05-29. Auditor: final-audit-logic-respawn-1. HEAD: 4b684db.*
