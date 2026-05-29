# Feature: M3 — Re-derive Time-Squeeze Decay Curve + VWAP System-A HWM Gate (under S-1)

**Phase / Lane:** Phase 1.5 — fast-follow on its own TDD cycle. **Live-exit-logic change.** Ships **only** under the S-1 two-stage parity gate (sibling plan `phase-1.5/s1-two-stage-parity-gate/plan.md`).
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED + S-1 attribution table review) + `quant-risk-researcher` (empirical re-derivation methodology) + `quant-code-reviewer` (review). Standing Quad with the researcher swapped in for the math-derivation surface.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M3 is **NOT** in Phase-1 floor — Phase 1.5; the two layers it touches are flagged by code self-comments as having no literature provenance), §3.2 (HARDEN does not *delete* layers — M3 *re-derives* two of them), §4 binding condition **S-1**.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.5 hole **H-5** — *§3.3's "removes three provenance gaps" overstates the floor*; the Phase-1 floor removes **R3 only** (via M1); **R1** (time-squeeze decay curve) and **R2** (VWAP System-A HWM gate) require M3 (Phase 1.5) under S-1.
- `docs/handoff/council-attack-rubric.md` Family **A** (A-1 ★, A-2 ★, A-3 utility-horizon — N/A here because M3 is heuristic re-derivation, not a utility function), Family **D** ★ (D-1 golden fixture; D-2 non-circular provenance; **D-2 is load-bearing for M3**), Family **F** ★ (F-2 ★ bit-identical replay under fixed seed — the foundation S-1 builds on), Family **H** (H-1 ★ spec-freeze: M3's re-derivation MUST be by model-free stylized-fact on the return series, **NEVER** by strategy P&L — NN1).
- Code anchors:
  - **R1 (time-squeeze decay curve)** — `math_engine.py:88-94` (the `DECAY_CURVE_SCALAR = 9` constant + the `log10(1 + 9*t)` curve, flagged: *"The shape has no formal literature provenance and is flagged for a follow-up empirical review against realized intraday vol term-structure."*). Used in `compute_time_squeeze_decay` and `compute_active_trailing_stop`.
  - **R2 (VWAP System-A HWM gate)** — `math_engine.py:601-606` (the `safe_hwm >= vwap_cross_hwm_pct` gate, flagged: *"PROVENANCE (AC-8): the gate `safe_hwm >= vwap_cross_hwm_pct` is a tuned practitioner heuristic with no formal literature provenance."*). Used in `compute_vwap_state_update` System-A branch.
  - **Companion test surface** — `tests/math_engine/test_exit_math_provenance_and_grace.py` (the existing provenance-tests file documenting AC-7/AC-8).

## Why (problem statement)

The current production code self-flags two layers as **having no literature provenance**:

1. The time-squeeze decay curve `log10(1 + 9*t)` (R1) — a concave shape that tightens the stop FASTER early in the session and SLOWER near the close. The shape is a tuned practitioner heuristic, not a derivation.
2. The VWAP System-A HWM gate `safe_hwm >= vwap_cross_hwm_pct` (R2) — encodes the practitioner judgement that profit-protection should arm only after a position has banked a HWM above a tuned threshold.

These are **R1** and **R2** in the council synthesis's residual ledger. The Phase-1 floor only removes **R3** (the loss-aversion multipliers — via M1). The synthesis explicitly puts R1 / R2 in **Phase 1.5** because they are **live-exit-logic changes**: re-deriving them changes what the engine does on real cycles. A change to a live exit path is the highest-stakes math change AlphaBot can ship outside of the planned Phase-2 cutover. The S-1 two-stage parity gate (sibling plan) is the harness that makes M3 shippable; this plan ships the curves themselves.

Per **H-5 binding correction:** v3 §3.3 must read *"the Phase-1 floor removes R3 (the hand-tuned loss-aversion multipliers) via M1; R1 and R2 removal is the Phase-1.5 recommendation, contingent on M3 shipping under the S-1 two-stage parity gate."* The doc edit is in scope of the documentation sweep, not this code cycle — but the plan explicitly cites the correction so future readers see the precise phasing.

## Deliverables

### Empirical derivation methodology — NN1-binding (read FIRST)

Both re-derivations MUST follow the council's NN1 spec-freeze discipline (synthesis §2.5):

- **The data source is the RETURN SERIES, never strategy P&L.** Realized intraday volatility term-structure (for R1) and realized HWM-relative-to-VWAP-crossing distributions (for R2) are computed off the price-and-return history — never off historical AlphaBot exit decisions or P&L.
- **The derivation MUST be model-free** — a stylized-fact match, a calibration against the autocorrelation structure of intraday squared returns, or a kernel-density fit. **NOT** an Optuna search on backtest outcomes.
- The chosen calibration approach is documented in a research note alongside the M3 PR. The note states the calibration window, the input series, the method, and the resulting curve / threshold values.
- The new R1 curve and R2 threshold(s) are recorded in `spec_bundles` (migration 015 from Phase 1) with `freeze_discipline = 'STYLIZED_FACT'` (or `THEORY` / `MANDATE` if a closed-form derivation exists). **NEVER** `BACKTEST_SELECTION`. A `BACKTEST_SELECTION` provenance would make the BHY haircut a lie by omission (NN1 violation).

### Code

#### R1 — time-squeeze decay curve re-derivation

- **`math_engine.py:88-94`** — replace the `DECAY_CURVE_SCALAR = 9` magic constant with a re-derived family of constants under named module-scope identifiers. Possible shapes (the research note picks one):
  - Same `log10(1 + k*t)` family with a re-fitted `k` from the realized intraday vol term-structure.
  - A piecewise-linear curve from kernel-density-fitted decay points.
  - A different functional family (e.g. `(1 - t^α)` with `α` from the calibration).
- The chosen family ships with named constants — every new constant gets a source comment citing the research note (project no-magic-numbers rule).
- `compute_time_squeeze_decay` is updated to call the re-derived curve. Function signature **unchanged** (caller wiring untouched).
- The provenance flag at `math_engine.py:88-94` is removed (R1 closed) and replaced with a new comment block citing the research note + the calibration method.
- The `tests/math_engine/test_exit_math_provenance_and_grace.py` companion test file's AC-7 / AC-8 assertions are updated to reference the new provenance comment.

#### R2 — VWAP System-A HWM gate re-derivation

- **`math_engine.py:601-606`** — the `safe_hwm >= vwap_cross_hwm_pct` gate becomes a re-derived condition. The Optuna-tuned `vwap_cross_hwm_pct` parameter **remains an Optuna-searched parameter** (it is a tuned threshold; Optuna-searched parameters were never the provenance gap — the *gate shape* was). The re-derivation can:
  - Confirm the current gate shape is correct (and the provenance flag is closed by citing the new derivation),
  - Adjust the gate shape (e.g. `safe_hwm >= max(vwap_cross_hwm_pct, hwm_floor)` with a new derived floor), OR
  - Replace the gate with a fundamentally different but equivalent-purpose construction.
- The research note documents the choice and the calibration.
- The provenance flag at `math_engine.py:601-606` is removed (R2 closed) and replaced with a new comment block citing the research note.
- The Optuna search space for `vwap_cross_hwm_pct` is **unchanged**. The parameter stays inside the BHY haircut surface — no NN1 disturbance.

### Tests

M3 ships under S-1 (sibling plan). The full test set is:

- **The S-1 Stage 1 + Stage 2 harness** (from the sibling plan) — pre-M3 replays bit-identical to the current frozen reference; post-M3 replay enumerates every divergent cycle in the committed attribution table, each attributed to either `time_squeeze_decay_curve_v2` or `vwap_system_a_hwm_gate_v2`, each in the **intended direction** declared in the spec bundle. **Prose summary fails K-1; the attribution table passes it.**
- **Golden-fixture test for the new R1 curve:** a captured-from-producer fixture of `(time_ratio, expected_decay)` pairs derived from the calibration; the new `compute_time_squeeze_decay` returns within tolerance.
- **Golden-fixture test for the new R2 gate:** a captured-from-producer fixture of `(safe_hwm, vwap_cross_hwm_pct, expected_arm)` triples; the new gate matches.
- **Range / domain rejection tests** — both functions still raise `ValueError` on out-of-range inputs (M-1 / M-2 invariants from `feature-plans/exit-decision-math-fixes.md` AC-5, AC-6 are preserved).
- **Spec-bundle persistence test:** the new R1 + R2 facets exist in `spec_bundles` / `spec_facets` (or the JSON column if the team collapsed `spec_facets`) with `freeze_discipline ∈ {STYLIZED_FACT, THEORY, MANDATE}` — assertion that `BACKTEST_SELECTION` is NEVER the discipline for these facets (NN1 enforcement).
- **Replay-determinism (F-2 ★):** same `cycle_id` run twice post-M3 yields bit-identical decision records. (S-1 Stage 1 is the structural assertion of this; this is the explicit single-cycle test.)
- **Direction declaration test:** the M3 spec bundle declares `intended_direction` for each facet (e.g. "the new R1 curve is tighter at midday by ~X bps; the new R2 gate arms ~Y% less often"); the attribution table's `direction_check_passed` field validates the actual deltas match. A direction mismatch (the curve was supposed to tighten but actually loosened) fails the gate.
- **`compute_time_squeeze_decay` + `compute_active_trailing_stop` regression suite:** the existing tests at `tests/math_engine/test_stop_monotonicity.py` and friends continue to pass — the HWM-anchored ratchet behavior from `feature-plans/exit-decision-math-fixes.md` AC-3 is preserved. Any test pinning the old `DECAY_CURVE_SCALAR = 9` curve output is updated to the new curve and the change is called out in the PR diff (intentional behavior change, gated by S-1).

### Documentation

- **`docs/research/m3-time-squeeze-derivation-<date>.md`** — research note documenting R1's calibration method, input series, calibration window, and the resulting constants. Cites the empirical evidence on intraday vol term-structure.
- **`docs/research/m3-vwap-system-a-derivation-<date>.md`** — same for R2.
- Updated comment blocks at `math_engine.py:88-94` and `math_engine.py:601-606` citing the research notes.
- `spec_bundles` row for the M3 facets with `frozen_at`, the bundle hash, and the `facets_json` (or `spec_facets` child rows).

## Dependencies

- **Blocked by:** the S-1 sibling plan (`phase-1.5/s1-two-stage-parity-gate/plan.md`). S-1's harness must be in place before M3 lands.
- **Blocked by:** Phase-1 M1 GREEN (so the engine is in a Phase-1-stable state when the S-1 frozen reference is captured).
- **Blocked by:** `spec_bundles` / `spec_facets` migrations (Phase 1) — the new R1 + R2 facets must be persistable with content hash + `frozen_at` (NN1 binding). If the team collapsed `spec_facets` into a `facets_json` column, the constraint still holds.
- **Soft dependency:** the `quant-risk-researcher` agent for the empirical re-derivation methodology. The research note is its deliverable; the code change consumes it.
- **NOT dependent on M2.** M2 and M3 are independent — M2 is a passive diagnostic; M3 changes live exit logic.

## Golden-fixture tests required (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| S-1 Stage 1 | Pre-M3 replays bit-identical to the captured frozen reference. (Inherited from S-1 sibling plan.) |
| S-1 Stage 2 | Post-M3 replay enumerates every divergent cycle in the committed attribution table; every divergence attributed to `time_squeeze_decay_curve_v2` or `vwap_system_a_hwm_gate_v2`; every direction validated. |
| R1 curve golden fixture | `(time_ratio, expected_decay)` pairs from the calibration; tolerance-equality. |
| R2 gate golden fixture | `(safe_hwm, vwap_cross_hwm_pct, expected_arm)` triples; exact-equality. |
| Range / domain rejection | M-1, M-2, AC-5, AC-6 invariants from `exit-decision-math-fixes.md` preserved. |
| Spec-bundle freeze discipline | M3 facets persisted with `freeze_discipline ∈ {STYLIZED_FACT, THEORY, MANDATE}`; `BACKTEST_SELECTION` rejected. |
| Direction declaration | `intended_direction` declared in the spec bundle BEFORE M3 ships; attribution table validates actual deltas match. |

**Fixture provenance (D-2 ★, load-bearing):** the R1 + R2 fixtures are captured-from-producer from the calibration process (not from the new curve / gate code itself). A fixture hand-authored from the same code under test is circular and an automatic Gate-1 fail. The research note's calibration is the **producer**; the fixture is its **artifact**.

## Definition of Done

- All RED tests above land first (including S-1 Stage 1 + Stage 2).
- GREEN: every test passes; full-tree pytest with HEAD SHA + count + zero errors per `feedback_full_suite_means_genuine_full_tree`.
- The S-1 committed per-cycle attribution table exists; every divergent cycle is attributed; every direction validated; **post-M3 output is promoted to the new committed frozen reference** in the same PR.
- The provenance flags at `math_engine.py:88-94` and `math_engine.py:601-606` are CLOSED — comment blocks now cite research notes, not "no formal literature provenance."
- R1 + R2 facets are persisted in `spec_bundles` with the correct `freeze_discipline` and `frozen_at`; the bundle hash matches the persisted `facets_json` (immutability invariant).
- All new constants in `math_engine.py` carry source comments citing the research notes.
- `docs/research/m3-*-derivation-<date>.md` notes exist and are referenced from the M3 PR description.
- v3 doc-sweep correction H-5: synthesis §3.3 reads "the Phase-1 floor removes R3 only" — applied in the documentation sweep PR alongside this code cycle.

## Risk callouts / hazards

- **NN1 (★ load-bearing, the single largest risk).** R1 + R2 re-derivations MUST be model-free / stylized-fact / theory — **NEVER** strategy-P&L-selected. A P&L-driven re-derivation is the exact mechanism that turns the BHY haircut into a lie by omission. The spec-bundle freeze discipline assertion is the structural enforcement.
- **S-1 K-1 ★ (prose fails the gate; the table passes).** The PR reviewer must SEE the per-cycle attribution table and read every row. A summary paragraph is insufficient.
- **Direction validation.** The spec bundle declares the intended direction **BEFORE M3 ships**. An after-the-fact direction declaration is circular. The order is: (1) derive new curve / gate, (2) declare the intended direction in the spec bundle, (3) commit the spec bundle, (4) run the S-1 Stage 2 harness, (5) validate the attribution table.
- **D-2 ★ (non-circular fixture provenance).** The R1 + R2 fixtures are captured from the **calibration** (the producer), not from the new curve / gate code. If a future contributor regenerates the fixture from the post-M3 code's own output, the fixture becomes a tautology and the gate is meaningless.
- **F-2 ★ (replay determinism).** The new curves / gates are pure functions. No global RNG, no wall-clock, no unordered-dict iteration affecting numerics. S-1 Stage 1 is the structural verification.
- **Live-exit-logic blast radius.** M3 changes what the engine does on real cycles. Every divergent cycle in the historical replay is a cycle where the new engine would have produced a different decision. The S-1 attribution table is the human-review surface. **A divergence in the WRONG direction is a hard fail.** A correctly-directed but inexplicably-large divergence on a specific cycle is a reviewer flag — the reviewer escalates to the PM rather than waving it through.
- **Phase-1 anchor count.** Unchanged at 1 (M2's CVaR off the `cycle_id`-seeded kNN pool). M3 does not add an anchor — it changes deterministic curves that consume the same seeded state.
- **Two-DB boundary (E-2 ★).** R1 + R2 facets are in the **state DB** via `spec_bundles`. No optimization-DB migration.
- **Subprocess scheduler (`app.py` :00 spawn).** M3 changes `math_engine.py` functions but does NOT change `alpha_bot_execution.py` execution paths or `app.py` scheduling. No blocking I/O added. Architecture-constraint-1 untouched.

## Out of scope

- Re-deriving the parabolic ratchet, the breakeven lock, or any layer other than R1 and R2. The synthesis is explicit: HARDEN does not delete layers; M3 re-derives **two**.
- A new Optuna-searched parameter for the R1 curve shape. The CURVE FAMILY is frozen by theory/stylized-fact; the existing in-search parameters (`vwap_cross_hwm_pct`) stay in-search.
- Removing or re-ordering the priority resolver. The resolver is unchanged in M3.
- Removing the existing AC-7 / AC-8 provenance test file. The file is updated to reflect the closed provenance, not deleted.
- The Optuna search space size (still 6-D; gamma frozen via M1, no new tuned parameters).
- HAC / Newey-West correction for the M3 calibration's confidence interval (W-H5 is M1's residual and is explicitly out of scope across all of Phase 1 + 1.5).
- A live shadow-mode comparison of pre-M3 vs post-M3 in production. The S-1 replay-parity gate (Gate 1) is the binding evidence. Gate 2 (live shadow N-weeks-clean) for M3 is **inherited from the Phase-1 baseline** — the post-M3 engine becomes the new normal once S-1 passes; no separate M3-only shadow run.
- LLM-authored AI Advisor commentary on the M3 attribution table. The attribution table is a human-review artifact (NN1 binding).
