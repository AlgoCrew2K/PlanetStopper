# Feature: Frontrunner Simplify Path
Status: ready
Created: 2026-08-24

## Summary
The Frontrunner Builder's Calmar acceptance gate has two admission paths: IMPROVE, or PRESERVE+SIMPLIFY. The SIMPLIFY path is structurally unreachable: `MATERIAL_SIMPLIFICATION_MAX_RATIO = 0.50` (`advisors/frontrunner_acceptance.py:71`, applied `:266-269`) demands the candidate be ≤50% of the incumbent's node count, but the node counts fed to the gate (`advisors/frontrunner_builder.py:1721-1722`) are WHOLE-TREE counts — and a spliced candidate is always ~98-100% of the incumbent's size (empirically measured across all 11 real trees, 2026-08-20; the constant was calibrated against OVERLAY size but compared against WHOLE-SYMPHONY size). Found by the /code-review pass on PR #126; deferred then, fixed now. The fix re-scopes the comparison to the honest delta: the generated overlay's node count vs the node count of the incumbent cascade it replaces. `[PM-ASSUMED]` design ruling: delta-scoped ratio (overlay ≤ 50% of the replaced cascade) is the semantically correct reading of "materially simplifies" for a single-cascade splice — whole-tree ratios cannot express it; both quantities are already computable at the real call site (the builder holds the compiled overlay and the detected cascade, cf. the `replaced_node_id` plumbing from `DE-FR-PROPOSAL-IDENTITY-001`).

## Acceptance Criteria
- [ ] AC-1: `evaluate_calmar_acceptance`'s SIMPLIFY clause compares OVERLAY node count against REPLACED-CASCADE node count (ratio ≤ `MATERIAL_SIMPLIFICATION_MAX_RATIO`), via additive parameters — the whole-tree counts are no longer what this clause consumes.
- [ ] AC-2: the builder call site passes the real delta-scoped counts (overlay = the compiled pre-graft overlay node; cascade = the detected cascade subtree being replaced), counted by one shared tree-node-counting helper (reuse the existing counter if one exists — search all name variants before writing a new one).
- [ ] AC-3: reachability proof — a candidate with preserved Calmar and a small overlay replacing a large cascade is ACCEPTED via the SIMPLIFY path (impossible pre-fix); pinned with realistic fixture magnitudes (e.g. overlay ~10-30 nodes vs cascade ≥100 nodes, drawn from the real-tree scale).
- [ ] AC-4: IMPROVE path behavior byte-unchanged (its inputs, order, and thresholds untouched); a candidate qualifying under BOTH paths admits exactly as before.
- [ ] AC-5: fail-closed degradation — replaced-cascade count of zero/None/absent → the SIMPLIFY clause declines (no ZeroDivisionError, never a fabricated acceptance); overlay count larger than cascade → declines.
- [ ] AC-6: the dashboard/persisted whole-tree `node_count_delta` display metric is UNCHANGED (display semantics are not this gate's semantics).
- [ ] AC-7: zero diff to `frontrunner_detector.py`, splice mechanics, `backtest_gate_engine.py`, Calmar math, and `tests/security/test_frontrunner_no_trade_boundary.py` stays green.

  **AC-7-SUPERSEDED (Revise 4, 2026-08-24, `[PM-ASSUMED]`/team-lead ruling, documented by fps-doc 2026-08-25):** the "zero diff to `frontrunner_detector.py`" clause was superseded mid-cycle. Round-4 `/code-review` on PR #128 found Revise 3's cascade-side stub-marker search (`frontrunner_builder._contains_stub_marker`) disprovable on multi-tier cascades — both if-children of a multi-tier cascade's root can carry a stub marker when the fire branch itself contains an already-compacted nested tier, making "which side is the placeholder" genuinely ambiguous from OUTSIDE the detector. The team-lead's ruling: relocate signal-logic counting INTO the detector, where the pre-stub original subtree is still available and the ambiguity cannot arise — architecturally superior to patching the marker-search fallback yet again. Revise 4 (`d9983434`) added ~197 lines to `frontrunner_detector.py` (two new fields on `Cascade` — `signal_logic_node_count`/`fire_is_else_branch` — plus `_select_fire_and_continuation`, `_count_clause_aware_signal_logic`, `_compute_signal_logic_node_count`, `_compute_fire_is_else_branch`), a literal, direct violation of AC-7's original wording.

  **AC-7's INTENT is still met, even though its literal wording was superseded:** the original intent was "detection behavior does not change as a side effect of this cycle's fix" — not "the file's line count stays at zero." `detect_frontrunner_cascades`'s cascade-RECOGNITION logic (which trees qualify, which nodes get flagged, `overlay_tree`/`rsi_thresholds`/`vix_tickers`/`group_name`) is byte-unchanged across Revise 4 — the new fields are ADDITIVE dataclass fields and the new functions are ADDITIVE, never touching the existing detection walk. This is pinned directly by `test_detection_output_unchanged_for_existing_fields_across_all_real_fixtures`, run against all 11 of the operator's real trees, confirmed passing across both Revise 4 and Revise 5. See `DE-FR-SIMPLIFY-001`'s "Revise 4" section in `DECISIONS.md` for the full architectural rationale (why relocating into the detector, rather than continuing to patch the builder-side marker search, was the correct fix) and `docs/generated/advisors_frontrunner_detector.md`/`docs/generated/advisors_frontrunner_acceptance.md` for the current mechanism.

## Architecture
- `advisors/frontrunner_acceptance.py`: `evaluate_calmar_acceptance` gains additive keyword params (e.g. `overlay_node_count=None`, `replaced_cascade_node_count=None`); the SIMPLIFY clause consumes them with the AC-5 guards; constant value 0.50 unchanged; docstring updated to state the delta-scoped semantic and its calibration basis.
- `advisors/frontrunner_builder.py` (~`:1721-1722` call region): compute both counts at the existing gate call site from objects already in scope; thread them into the call. Whole-tree counts remain for the display metrics only.
- Tests: extend `tests/advisors/` frontrunner acceptance/builder suites; golden-fixture style per project rule for math-adjacent changes.

## Design-System Mapping
No UI changes (gate logic only).

## Edge Cases
- Cascade smaller than overlay → decline (AC-5).
- Compound/if_compound overlays → counted like any node subtree (no special-casing).
- Legacy callers of `evaluate_calmar_acceptance` not passing the new kwargs → SIMPLIFY clause declines (fail-closed default), IMPROVE unaffected.
- Zero-node/malformed subtree inputs → decline, never raise (D-1 house style).

## Security Considerations
No new I/O, routes, credentials, or rendered fields. Advisory-only pipeline, off the execution path; no `LIVE_EXECUTION` surface. The only risk class is gate laxity: mitigated by AC-5 fail-closed defaults and AC-4's IMPROVE-path pin.

## Testing Strategy
- Unit: SIMPLIFY clause truth table (ratio at/below/above 0.50 boundary with an epsilon-adjacent case; AC-5 guards; kwargs-absent default).
- Integration: builder gate call passes real counts (mirror the existing signal-wiring test pattern — real `_run_build_for_symphony` with external seams mocked).
- Reachability: AC-3's end-to-end admission via SIMPLIFY.
- Regression: IMPROVE-path pins (AC-4), no-trade-boundary suite, existing acceptance tests.
- Execution: touched suites only, `-n0`, never the full tree (host constraint).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Delta-scoped ratio (overlay vs replaced cascade) `[PM-ASSUMED]` | Whole-tree ratio is mathematically incapable of firing for a splice (98-100% measured); the delta is what "simplify" means for a one-cascade swap; both operands already in scope at the call site. |
| Keep 0.50 constant value | It was calibrated against overlay size; the comparison, not the constant, was wrong. |
| Additive kwargs with fail-closed default | Legacy/partial callers must not gain accidental admissions. |

## Scope Boundaries
- **IN**: the SIMPLIFY clause's operands + builder call-site threading + tests + docs (DECISIONS `DE-FR-SIMPLIFY-001`).
- **OUT**: IMPROVE-path logic; detector/splice/gate-engine; asset_class defect (next backlog item); any UI change; renaming the acceptance constant.
