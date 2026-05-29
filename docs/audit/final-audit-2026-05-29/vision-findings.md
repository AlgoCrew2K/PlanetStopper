# Final Vision Audit — Post-Closure Pass (2026-05-29)

**Auditor:** vision-auditor (respawn-1)
**Branch:** `audit/final-2026-05-29-vision`
**HEAD under audit:** `4b684db` on `plan/finalist-a-scaffold`
**Prior vision audit merge:** `1eea876` (vision-audit branch merged into plan/finalist-a-scaffold)
**Read-only audit.** No source files modified.

---

## Audit methodology

Re-applied the original seven-rationale lens from
`docs/audit/vision-audit-2026-05-27/vision-findings.md`.
For each rationale, code state was verified at `4b684db` against specific
file:line citations. The git log between `1eea876` and `4b684db` was read in
full to understand which closures addressed which original findings.

Original Drift findings addressed by the closure pass:

| Original Drift | Addressed by |
|---|---|
| Drift 1 — CVaR live wire-up missing | `a2aef59` (fix-cvar-wireup) |
| Drift 2 — "6-layer" language | README rewrite at `a2aef59` area, §4.2 |
| Drift 3 — DE writes NOT_APPLICABLE in default config | README §7.3 now discloses this explicitly |
| Drift 4 — Dashboard CVaR shows first symphony only | README §12 "CVaR live wire-up" documents CVAR-001 scope limit |
| Drift 6 — M3 provenance not shipped | `55d0204` (fix-m3-provenance) + `79759db` |

---

## Per-rationale findings

### Rationale 1 — Symphony-level only, not portfolio-level

**Verdict: VERIFIED**

Evidence at `4b684db`:

- `DECISIONS.md:41-45` (DE-S3-004): "All autonomous port-level decision math is removed from production code. Deleted: `engine/multi_cycle.py`, `engine/port_selector.py`, `engine/port_aggregator.py`, `engine/dual_altitude.py`."
- `engine/` directory at HEAD: contains only `__init__.py`, `exit_authority.py`, `params.py` — the four autonomous decision files are absent.
- `README.md:284-292` (§4.1): "Every decision AlphaBot makes is keyed to a single Composer symphony, not to a portfolio aggregate." The port-level architecture and the rationale for removal are both documented.
- `README.md:8-9` (What the bot does NOT do): "AlphaBot does not produce a portfolio-level decision. Every decision is symphony-level."
- `alpha_bot_execution.py`: per-symphony iteration is the top-level loop; no cross-symphony aggregation exists in the call path.

No closure broke this rationale. Symphony-level scoping is the only in-production path at HEAD.

---

### Rationale 2 — Four canonical triggers fed by six upstream layers

**Verdict: VERIFIED**

Original finding was PARTIAL (drift in claimed-vs-actual language). The closure pass calibrated the language; the code was always correct.

Evidence at `4b684db`:

- `math_engine.py:826-833`: `_TRIGGER_PRIORITY_ORDER` lists four entries: `VWAP Breakdown`, `Take-Profit`, `VWAP Bleed Cut`, `Trailing Stop`.
- `math_engine.py:836-868`: `resolve_trigger_priority` takes four boolean flags and returns `(winner, also_true)` — unchanged.
- `README.md:294-302` (§4.2): language now reads "four canonical exit triggers" and the §4.2 callout box explicitly disambiguates "six layers vs four triggers." The prior "6-layer exit decision" overshoot is corrected.
- `README.md:398-415` (§5, Step 4 walkthrough): "Walk the six math layers" followed by "Step 5 — Compute the four exit-trigger flags" — the six-feeds-four architecture is now the narrative backbone.

The closure correctly documents the architectural truth. Code is unchanged and was always correct.

**One residual OQ (not a vision breach):**
`math_engine.py:826-827`: `_TRIGGER_PRIORITY_ORDER` comment cites "H2 acceptance criteria" as justification for Take-Profit ahead of VWAP Bleed Cut. That document is not on this branch. Classified as `TAG-OPEN` in `docs/audit/vision-audit-2026-05-27/open-questions-resolution.md` (OQ-1). The pairwise TP > Bleed Cut order has no first-principles argument on file. This is an open provenance question, not a vision rationale breach — the four-trigger architecture itself is sound.

---

### Rationale 3 — Observability-only dashboard

**Verdict: VERIFIED**

Evidence at `4b684db`:

- `app.py:1550-1554`: `/api/trigger` POST handler returns `"Manual trigger disabled — use the scheduler."` — unchanged.
- `database.py:76-80`: `get_ro_connection()` opens via `?mode=ro` URI — unchanged. Dashboard read handlers use this path; driver-level enforcement is intact.
- `app.py:1552` and `app.py:389`: arch constraint 2 cited directly in comments at both call sites.
- `README.md:306-318` (§4.3): three-layer enforcement documented (architecture constraint, driver-level `?mode=ro`, disabled trigger route).

No closure touched the dashboard write path. The observability-only invariant holds at HEAD.

---

### Rationale 4 — Diagnostic-only CVaR with REJECT wall

**Verdict: VERIFIED** (significant improvement over original)

The original audit found this rationale at PARTIAL — the live path was writing all-None sentinels instead of calling `compute_portfolio_cvar`. The closure pass fixed the wire-up and updated the README to accurately describe the operational state. The REJECT wall is intact.

Evidence at `4b684db`:

**CVaR live wire-up (fix-cvar-wireup, commit `a2aef59`):**
- `alpha_bot_execution.py:1434-1476`: per-cycle path now calls `math_engine.compute_portfolio_cvar(...)` unconditionally at `alpha_bot_execution.py:1441-1446`. Result is written to `database.record_cvar_diagnostic(...)` at line 1465-1476 with `cvar_5pct=_cvar_short.cvar_pct`, `cvar_5pct_stderr=_cvar_short.stderr`, `cvar_n_tail=_cvar_short.tail_obs_count`.
- The original all-None stub at the old `alpha_bot_execution.py:1417-1426` is gone.

**CVaR remains diagnostic-only (no trigger):**
- `alpha_bot_execution.py:1439-1440`: comment: "CVaR remains diagnostic-only: no trigger branch reads this write."
- `math_engine.py:136-137`: `CVaRAssessment` dataclass docstring: "Phase-1 rule: ZERO production consumers permitted — tests only."
- `README.md:322-332` (§4.4): "CVaR is never a live trigger — it is operator instrumentation only."

**REJECT wall intact:**
- `advisors/divergence_explainer.py:7-17`: hard wall documented in module docstring — forbidden keys list includes `divergence`, `signed_divergence`, `cvar_diff`, `cvar_delta`, `window_divergence`, `divergence_pct`, `delta`.
- `DECISIONS.md:51` (DE-S3-005): "CVaR-divergence REJECT — no signed-divergence number in production."
- `README.md:326-330` (§4.4): rationale for divergence-detector rejection (data wall relocation, not escape) is documented.

**Fail-safe invariant intact:**
- `math_engine.py:162-184`: `CVaRAssessment.__post_init__` raises `ValueError` if `cvar_pct is None and breach is True`, or if `cvar_pct is None and stderr is not None` — multi-part fail-safe contracts enforced structurally.

**CVAR-001 scope limit (multi-symphony gap):**
- `README.md:759-761` (§12): "the panel currently shows the first symphony only (CVAR-001 scope limit) — multi-symphony portfolios silently omit other symphonies' rows pending a future expansion." Disclosed honestly as a Phase-1 deferral.

No signed divergence quantity was introduced by any closure commit. The REJECT wall is intact.

---

### Rationale 5 — NN1 spec-freeze and BHY haircut

**Verdict: VERIFIED**

Evidence at `4b684db`:

- `autotuner.py:73-97`: `FREEZE_DISCIPLINE_*` constants; `FREEZE_DISCIPLINE_BACKTEST_SELECTION = "BACKTEST_SELECTION"  # NN1 VIOLATION` — unchanged.
- `autotuner.py:139-154`: `OPTUNA_N_TRIALS_PRODUCTION = 500` with full provenance comment (5x the 100-trial TPE stability floor; BHY c(500)/c(100) ≈ 1.30 stronger). Verified by OQ-2 closure at `a5ae75b`.
- `autotuner.py:1180-1186` (per README cited line): `_assert_search_space_no_theory_facets` runs at module-load; import raises `RuntimeError` if THEORY-frozen facets appear in the search space.
- `autotuner.py:1189-1279` (per README): `validate_nn1_compliance` — default-deny on unknown disciplines, OOS-peek violations labelled distinctly.

No closure weakened the NN1 spec-freeze. The `OPTUNA-7` named-constant pin (commit `76035ef`) actually tightened the contract: `n_trials` is now pinned to the named constant `OPTUNA_N_TRIALS_PRODUCTION`, not a magic integer. No search-space changes that could admit a THEORY-frozen facet are present in the closure log.

---

### Rationale 6 — Three-not-one advisors (Overfitting Conscience, Spec Critic, Divergence Explainer)

**Verdict: VERIFIED**

Evidence at `4b684db`:

- `advisors/overfitting_conscience.py:1-19`: three Phase-1 indicators (I-1/I-2/I-3) on researcher DoF ledger. Unchanged.
- `advisors/spec_critic.py:1-19`: four indicators (I-1/I-2/I-3/I-4) on spec_bundles integrity. Unchanged.
- `advisors/divergence_explainer.py:1-29`: flag-gated second-window CVaR observation, hard wall in docstring. Unchanged.
- `README.md:340-350` (§4.6): "Three Advisors, not one — wall integrity rationale" documented.
- `DECISIONS.md:31-35` (DE-S3-003): Narrator deferred, enum retained as a deferred slot.

The `LOGIC-M-1` closure (commit `35e5654`) corrected a docstring drift in `spec_critic.py` regarding `_ACCEPTABLE_DISCIPLINES` — the I-2 docstring now correctly names the six accepted disciplines. This was a correctness fix, not a behavioral change. The three-not-one architecture is intact.

**Divergence Explainer dormancy:**
DE writes `verdict=NOT_APPLICABLE` in default config. The original vision audit Drift 3 classified this as a documentation gap, not a code-vision breach. The README §7.3 at HEAD now discloses the dormancy explicitly: "DE is dormant in the default configuration. Until an operator turns on `SECOND_WINDOW_CVAR_ENABLED`, every autotune cycle writes a no-op NOT_APPLICABLE row... This is intentional." The disclosure is honest and complete.

---

### Rationale 7 — Fail-safe floor

**Verdict: VERIFIED**

Evidence at `4b684db`:

- `math_engine.py:70-73`: `MC_INSUFFICIENT_HISTORY_SENTINEL = None` with explicit comment: "compute_exit_confirmation treats None as 'MC confirmation unavailable' — it does NOT veto the protective stop, which still fires on the ticks-below-stop condition alone (fail-safe)."
- `math_engine.py:162-184`: `CVaRAssessment.__post_init__` enforces `cvar_pct is None → breach = False` — structurally enforced. The post_init guard for the `RegimeMatchAssessment` dataclass was added in commit `553f4b4` (fix-mc-regime-match-quality): `math_engine.py:220-228` — `mean_sq_mahalanobis is None and is_unprecedented is True → ValueError`.
- `alpha_bot_execution.py:1125-1139`: regime-match-quality guard added (vision-audit Critical Rec #2) — when `_regime_assessment.is_unprecedented`, `prob_beating` is overridden to `None` so the MC gate cannot veto the protective stop during unprecedented regimes.
- `README.md:352-358` (§4.7): fail-safe floor documented, including the `CVaRAssessment.__post_init__` invariant and `math_engine.py:425-428` MC gate behavior.

Two closure cycles directly strengthened this rationale:
1. `a2aef59` (fix-cvar-wireup): `CVaRAssessment.stderr` pairing added to the `__post_init__` multi-part contract — if `cvar_pct is None and stderr is not None`, raises `ValueError`. The fail-safe envelope is now stricter.
2. `e46d436` (fix-mc-regime-match-quality): `RegimeMatchAssessment.__post_init__` guard added — `mean_sq_mahalanobis is None` cannot produce `is_unprecedented = True`.

The fail-safe floor is stronger at HEAD than at the vision-audit baseline.

---

## OQ TAG-OPEN disclosure audit

The original vision audit produced 10 open provenance questions (OQ-1 through OQ-10). The closure pass (`a5ae75b`) classified all 10 and added `TAG-OPEN` markers to 5, `CITE` to 4, and `CROSS-LINK` to 1. The README §12 table at HEAD correctly discloses all 10.

Verification that the 5 TAG-OPEN items have in-code disclosure:

| OQ | Constant | Code location at HEAD | TAG-OPEN in README? |
|---|---|---|---|
| OQ-1 | `_TRIGGER_PRIORITY_ORDER` priority | `math_engine.py:826-827` | Yes — `README.md:725-727` |
| OQ-3 | `MC_DEFAULT_NEIGHBOR_K = 150` | `math_engine.py:92-93` | Yes — `README.md:727-729` |
| OQ-4 | `MC_DEFAULT_SIMULATION_PATHS = 5000` | `math_engine.py:91` | Yes — `README.md:729-730` |
| OQ-5 | `PARABOLIC_VELOCITY_THRESHOLD` | `alpha_bot_execution.py:91-92` | Yes — `README.md:731-732` |
| OQ-7 | `VWAP_OPEN_WINDOW_GRACE_MINUTES = 15` | `alpha_bot_execution.py:73` | Yes — `README.md:732-733` |

All five TAG-OPEN items have honest in-code comments (verified above). The README §12 table matches. No TAG-OPEN item was silently promoted to CITE without supporting in-code documentation.

---

## M3 provenance work (original Drift 6)

The original vision audit found that Phase 1.5 (M3) time-squeeze re-derivation had NOT shipped at the vision-audit baseline. This was addressed by commit `55d0204` + `79759db` (fix-m3-provenance cycle).

Evidence at `4b684db`:

- `math_engine.py:231-244`: time-squeeze curve now uses `f(t) = 1 - sqrt(1 - t)` with explicit THEORY provenance comment citing Danielsson & Zigrand (2003). `DECAY_CURVE_SCALAR` removed — "closed by the sqrt-time derivation (M3)".
- `math_engine.py:294-323`: `compute_time_squeeze_decay` uses `decay_curve = 1.0 - math.sqrt(1.0 - time_ratio)` — zero free parameters.
- `README.md:253-276` (§3.7): "Vol-scaling, time squeeze, parabolic ratchet — practitioner heuristics with provenance gaps" — the time-squeeze curve is now described as having "first-principles THEORY provenance (M3 redrive shipped)" while the **parabolic ratchet** is clearly called out as remaining practitioner-grade.

The section heading in §3.7 still uses "practitioner heuristics with provenance gaps" — accurate, because the parabolic ratchet (PARA-ARM) provenance gap remains. The README is honest: it distinguishes what shipped (time-squeeze THEORY redrive) from what remains open (PARA-ARM day-boundary semantics, OQ-5).

The vision tenet "each layer carries its own theoretical/empirical justification" is now **closer to complete** than at the vision-audit baseline:
- Vol-scaling: THEORY (Andersen & Bollerslev 1997, RiskMetrics 1996).
- Time-squeeze: THEORY (Danielsson & Zigrand 2003 — M3 redrive shipped).
- Breakeven: THEORY (Fu & Zhang 2012).
- VWAP regime-switch: THEORY anchor (Leung & Zhang 2019, Peskir 1998 — with honest-flag caveat).
- Parabolic ratchet: practitioner heuristic — no formal provenance, OQ-5 open.
- MC sanity gate: practitioner-grade (kNN bootstrap, no formal proof).

The only remaining provenance gap that touches a design rationale is the parabolic ratchet. The README discloses this gap explicitly in §3.7 and §12.

---

## Stale-comment cleanup (commit `7ab11a9`)

Commit `7ab11a9` removed "Phase-2 forward-path framing" from `math_engine.py` docstrings that had described kNN regime-match as a "Phase-2 co-signal." The code was always Phase-1 kNN regime-match; the stale comment was the error.

Impact on vision rationales: zero. This was a comment-only fix that brought documentation into alignment with code. It did not weaken any design rationale and should not be read as a semantic change to the CVaR-diagnostic architecture.

---

## Concerns (1 minor)

**Concern: CVAR-001 scope limit is honestly disclosed but not time-bounded.**
`README.md:759-761`: "the panel currently shows the first symphony only... pending a future expansion." There is no milestone, Phase designation, or OQ number for this expansion. It is a known gap in operator-facing observability for multi-symphony portfolios. An operator with 3+ symphonies sees only one symphony's CVaR diagnostic on the dashboard. The gap is disclosed but not tracked.

Assessment: minor. The fix is display-layer only (query expansion in `app.py`). The underlying math is not affected. The disclosure is honest. Not a vision rationale breach — the observability-only rationale (Rationale 3) is about preventing trade execution through the dashboard, not about completeness of the observability surface.

---

## Final verdict

**Vision-fit: STRONG**

| Design rationale | Verdict | Evidence anchor |
|---|---|---|
| Symphony-level only | VERIFIED | `engine/` directory + `DECISIONS.md:DE-S3-004` |
| 4 canonical triggers fed by 6 layers | VERIFIED | `math_engine.py:826-833` + `README.md:§4.2` |
| Observability-only dashboard | VERIFIED | `app.py:1550-1554` + `database.py:76-80` |
| Diagnostic-only CVaR + REJECT wall | VERIFIED | `alpha_bot_execution.py:1441-1476` + `advisors/divergence_explainer.py:7-17` |
| NN1 spec-freeze + BHY haircut | VERIFIED | `autotuner.py:1180-1186` + `autotuner.py:73-97` |
| Three-not-one advisors | VERIFIED | `advisors/` directory + `DECISIONS.md:DE-S3-003` |
| Fail-safe floor | VERIFIED | `math_engine.py:162-184` + `alpha_bot_execution.py:1125-1139` |

All seven design rationales are realized in code at `4b684db`. The closure pass between `1eea876` and `4b684db` addressed all five original Drift findings (CVaR wire-up, language calibration, DE dormancy disclosure, CVAR-001 scope limit disclosure, M3 provenance) without breaking any design rationale. The fail-safe floor rationale is materially stronger at HEAD than at the vision-audit baseline, due to the `RegimeMatchAssessment.__post_init__` guard and the enhanced `CVaRAssessment.stderr` pairing.

One residual open question (OQ-1: pairwise TP > Bleed Cut priority justification) remains unresolved with a TAG-OPEN marker. It is a provenance gap in one sub-decision of Rationale 2, not a vision rationale breach.

---

*End of final vision audit findings. Auditor: vision-auditor (respawn-1). Read-only audit on `4b684db`. No source files modified.*
