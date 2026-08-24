# advisors/frontrunner_acceptance

> Calmar acceptance gate for the Frontrunner Builder — a candidate is admitted only if it improves risk-adjusted return or preserves it while materially simplifying the tree's actual signal logic.

**Source:** `advisors/frontrunner_acceptance.py`
**Last updated:** 2026-08-24 (`DE-FR-SIMPLIFY-001` Revise 3 — PR #128's independent `/code-review` found a CRITICAL defect (F1) in the just-shipped delta-scoped fix: the "replaced cascade" operand still counted the detector's stub-padded compact cascade, not the honest signal logic, which could INFLATE the denominator arbitrarily and invert SIMPLIFY from unreachable to admitting oversized overlays. The SIMPLIFY clause now requires THREE independent gates — Calmar-not-worse, a signal-logic-only delta-scoped ratio, and a whole-tree no-growth check (RULING 2) — plus stricter node-count-operand coercion (F9/A3). See "SIMPLIFY-Path Semantics" below and `DE-FR-SIMPLIFY-001`'s "Revise 3" section in `DECISIONS.md`.) Prior: 2026-08-24, original cycle (the SIMPLIFY path's node-count comparison re-scoped from whole-symphony node counts to a delta-scoped comparison — see `DE-FR-SIMPLIFY-001`'s main section in `DECISIONS.md`). Prior: 2026-07-11 (Wave-1 backend, frreview-APPROVED, unchanged by the subsequent P2-1/P2-2 hardening — those touched `frontrunner_detector.py`/`frontrunner_builder.py` only)

## Overview

`advisors/frontrunner_acceptance.py` implements AC-7 of the Frontrunner Builder plan: the final acceptance decision applied to a candidate that has already survived the shared overfitting guardrail (`backtest_gate_engine.evaluate_candidate_batch`). Gate survival is **necessary but not sufficient** — a candidate must also demonstrably improve the strategy, or simplify it without cost.

Acceptance is **two independent paths**, either of which admits a candidate:

1. **IMPROVE** — the candidate's Calmar ratio (`CAGR / |max_drawdown|`) is strictly higher than the incumbent's, AND the candidate's own absolute drawdown does not breach a floor (a candidate cannot buy an improved ratio via extreme leverage that also posts a catastrophic drawdown). Byte-unchanged by `DE-FR-SIMPLIFY-001` (both the original cycle and Revise 3) — this path reads none of the SIMPLIFY-path operands.
2. **PRESERVE + SIMPLIFY** (`DE-FR-SIMPLIFY-001`, Revise 3 — three independent gates, ALL required) —
   - the candidate's Calmar is within tolerance of the incumbent's (neither meaningfully better nor worse), AND
   - the generated overlay's **signal logic** (its condition + real fire/then branch, EXCLUDING its placeholder-else) is materially smaller than the replaced cascade's own **signal logic** (its condition + real fire branch, EXCLUDING its stub-padded continuation) — never the whole-symphony node counts (stay ~98-100% of each other for any single-cascade splice) and never the whole compacted/compiled subtree including its stub/placeholder branch (RULING 1, the CRITICAL fix — see below), AND
   - the whole-symphony tree did NOT grow (`node_count_delta <= 0` — RULING 2, an independent invariant the delta-scoped ratio alone cannot see).

   Same drawdown-floor guard applies as IMPROVE.

**Sharpe and volatility are always reported** on the result (for the Advisor-tab card, once built) but **never gate acceptance either way** — a terrible Sharpe cannot sink an otherwise-accepted candidate, and a great Sharpe cannot rescue an otherwise-rejected one (AC-7 explicit).

This module never trusts an incoming pre-computed Calmar figure — it derives Calmar itself from the caller-supplied CAGR + max_drawdown metrics dict fields (mirroring the project-wide "never trust incoming oos_metrics" posture used by `build_plan_generator`/`strategy_builder_engine`). Fails **closed** (rejects) on any missing/None metric or malformed input — never raises, never fabricates an accept on incomplete data.

Off-execution-path. Pure function, no I/O, no network, no DB access.

## Named Constants

| Name | Value | Rationale |
|------|-------|-----------|
| `CALMAR_PRESERVE_TOLERANCE` | `0.02` (2% relative) | Tight enough that a genuine performance improvement is never miscategorized as "merely preserved", while tolerating floating-point noise between two independent re-backtests of structurally-identical trees |
| `MATERIAL_SIMPLIFICATION_MAX_RATIO` | `0.50` | The overlay's SIGNAL-LOGIC-ONLY count qualifies as "materially simpler" than the replaced cascade's SIGNAL-LOGIC-ONLY count at ≤50% — deliberately a large threshold, since the grounding note describes collapsing "hundreds of flat RSI-gt rungs" via any/all (an order-of-magnitude reduction, not a marginal trim). **Value unchanged across both the original `DE-FR-SIMPLIFY-001` cycle and Revise 3 (2026-08-24)** — the calibration basis was always overlay-scale (a handful to a few dozen nodes), never whole-symphony scale and never stub-padded-cascade scale; every fix in this feature corrected WHICH operands the ratio is compared against, never the ratio's own value |
| `MAX_ABSOLUTE_DRAWDOWN_FLOOR` | `0.40` (40%) | A candidate's own max drawdown must never exceed this regardless of Calmar improvement — prevents accepting a candidate that "improves" Calmar only by pairing an extreme CAGR with an extreme, separately unacceptable drawdown. Generous ceiling — well above any of the operator's real incumbent symphonies' historical drawdowns, so it only fires on a genuinely pathological candidate |

## Public Types

### `AcceptanceResult` (dataclass)

Returned by `evaluate_calmar_acceptance`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `accepted` | `bool` | `True` if either acceptance path admitted the candidate |
| `tags` | `set[str]` | Subset of `{"performance", "simplification"}` — which path(s) admitted the candidate. Always empty when `accepted` is `False` |
| `node_count_delta` | `int` | `candidate_node_count - incumbent_node_count` (negative = simpler). Whole-symphony semantics, byte-unchanged across both `DE-FR-SIMPLIFY-001` cycles. Serves TWO roles since Revise 3: the pre-existing display metric, AND (RULING 2) a live SIMPLIFY-gate input — `node_count_delta <= 0` is the third, independent no-growth gate |
| `candidate_sharpe` | `float \| None` | Reported for the Advisor-tab card; never gates acceptance |
| `candidate_volatility` | `float \| None` | Reported for the Advisor-tab card; never gates acceptance |
| `incumbent_calmar` | `float \| None` | The incumbent's derived Calmar, or `None` if undefined (zero drawdown) or unavailable |
| `candidate_calmar` | `float \| None` | The candidate's derived Calmar, or `None` if undefined/unavailable |

## API Reference

### `compute_calmar(cagr: float, max_drawdown: float) -> float | None`

Returns `cagr / abs(max_drawdown)`. Returns `None` (never raises, never `inf`) when `max_drawdown` is exactly `0`.

`max_drawdown` follows the quantstats convention (`<= 0`; a negative fraction, e.g. `-0.08` = 8% drawdown) — the absolute value is used as the denominator so a valid negative `max_drawdown` always yields a signed Calmar matching the sign of `cagr`.

---

### `evaluate_calmar_acceptance(incumbent_metrics: dict, candidate_metrics: dict, *, incumbent_node_count: int, candidate_node_count: int, overlay_node_count: int | None = None, replaced_cascade_node_count: int | None = None) -> AcceptanceResult`

The AC-7 acceptance gate. Signature unchanged since the original `DE-FR-SIMPLIFY-001` cycle — Revise 3 changed the SIMPLIFY clause's internal decision logic and the CALLER-SIDE derivation of `overlay_node_count`/`replaced_cascade_node_count` (see `docs/generated/advisors_frontrunner_builder.md`), not this function's parameters.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `incumbent_metrics`, `candidate_metrics` | `dict` | Shaped like `analytics.compute_quantstats_metrics`'s output: `annualized_return` (CAGR), `max_drawdown` (<= 0), optionally `sharpe`/`volatility` |
| `incumbent_node_count`, `candidate_node_count` | `int` | Total node counts of the incumbent and candidate SYMPHONY trees — feed the `node_count_delta` display metric (AC-6) AND (RULING 2, Revise 3) the SIMPLIFY path's whole-tree no-growth gate. Never consulted by the delta-scoped ratio itself |
| `overlay_node_count`, `replaced_cascade_node_count` | `int \| None` | **`DE-FR-SIMPLIFY-001`, Revise 3 (2026-08-24): now SIGNAL-LOGIC-ONLY counts** (caller-derived — condition + real fire/then branch on each side, excluding the overlay's placeholder-else and the cascade's stub-padded continuation), not whole-subtree counts. Keyword-only, additive, default `None`. Omitting them makes SIMPLIFY structurally unreachable (fail-closed), never a silent fallback to an older, broken comparison |

**Returns:** `AcceptanceResult`. Never raises (D-1).

**Decision logic (in order):**

1. **Fail-closed on missing data.** Any of `incumbent_cagr`/`incumbent_mdd`/`candidate_cagr`/`candidate_mdd` being `None`/non-numeric → immediate reject (no accept on incomplete data).
2. **Fail-closed on undefined Calmar.** Either side's `compute_calmar` returning `None` (zero drawdown) → reject.
3. **Drawdown-floor guard (applies to BOTH paths).** `abs(candidate_mdd) > MAX_ABSOLUTE_DRAWDOWN_FLOOR` → reject regardless of Calmar.
4. **Path 1 — IMPROVE:** `candidate_calmar > incumbent_calmar` → tag `"performance"`. Reads none of the SIMPLIFY-path params — byte-unchanged across both `DE-FR-SIMPLIFY-001` cycles (AC-4).
5. **Path 2 — SIMPLIFY, THREE independent gates, ALL required (Revise 3):**
   - **Calmar-not-worse:** `candidate_calmar` is not worse than the incumbent's (either `>=`, or within `CALMAR_PRESERVE_TOLERANCE` relative tolerance).
   - **Delta-scoped signal-logic ratio:** `_is_delta_scoped_material_simplification(overlay_node_count, replaced_cascade_node_count)` is `True`.
   - **Whole-tree no-growth (RULING 2):** `node_count_delta <= 0` — the SAME whole-symphony `candidate_node_count - incumbent_node_count` computation `AcceptanceResult` already carries as a display metric, now also load-bearing here. Restores an invariant the delta-scoped ratio alone structurally cannot see: `frontrunner_builder._graft_incumbent_core` re-inserting the incumbent's full core into the candidate's else branch can make the OVERALL spliced tree bigger even though the signal logic genuinely shrank — tagging "simplification" on a tree that grew would be an absurdity the ratio alone cannot catch, so this is a third, independent gate, never traded off against the other two.

   All three true → tag `"simplification"`. Note: a candidate that is simpler AND has a *better* Calmar earns both tags — the phrasing "preserve within tolerance while materially simplifying" is a floor, not a ceiling; it does not require Calmar be exactly unchanged when it also happens to be better.
6. **Accepted iff `tags` is non-empty.**

**Example:**
```python
from advisors.frontrunner_acceptance import evaluate_calmar_acceptance

result = evaluate_calmar_acceptance(
    incumbent_metrics,   # {"annualized_return": 0.11, "max_drawdown": -0.14, "sharpe": 0.9, "volatility": 0.16}
    candidate_metrics,   # {"annualized_return": 0.13, "max_drawdown": -0.12, "sharpe": 1.0, "volatility": 0.15}
    incumbent_node_count=812,
    candidate_node_count=790,             # <= incumbent_node_count -> RULING 2 gate satisfied
    overlay_node_count=15,                # the overlay's SIGNAL-LOGIC-ONLY count (condition + real fire branch)
    replaced_cascade_node_count=49,       # the replaced cascade's SIGNAL-LOGIC-ONLY count (excludes stub padding)
)
if result.accepted:
    print("accepted:", result.tags, result.candidate_calmar, "vs", result.incumbent_calmar)
```

---

### `_safe_node_count_float(value) -> float | None`

**New, `DE-FR-SIMPLIFY-001` Revise 3 (F9).** Private. Stricter float coercion for the SIMPLIFY-clause node-count operands ONLY — never used for CAGR/MDD/sharpe/volatility parsing (that stays on the general-purpose `_safe_float`; deliberately not touching that shared helper keeps this hardening scoped to exactly the operands it targets).

A node count is never legitimately: a `bool` (Python's `bool` is an `int` subtype — `float(True) == 1.0` would silently coerce a caller bug into a plausible-looking tiny count), a numeric string (`float("20") == 20.0` — same silent-coercion risk), or non-finite (`inf` trivially satisfies any ratio comparison against a finite counterpart; `nan` comparisons are always `False`, an ambiguous fall-through rather than an honest decline). A huge integer (e.g. `10**400`) raises `OverflowError` from `float()` — caught here (A3) so the caller's OTHER reporting fields (Sharpe/volatility/both Calmar values/`node_count_delta`, all independently computable) are never lost to an unhandled exception escaping to `evaluate_calmar_acceptance`'s own outer catch-all, which nulls everything.

**Returns:** `float | None`. Declines (`None`, never raises) on any of: non-`(int, float)` type (including `bool`), `TypeError`/`ValueError`/`OverflowError` from `float()`, or `math.isnan`/`math.isinf`.

**Fabrication-risk asymmetry (documented in the TDD handoff, not in source comments — recorded here for completeness):** the risk is not symmetric between the two operands. A `bool`/numeric-string on the OVERLAY (numerator) side is genuinely dangerous — it coerces to a small, plausible-looking node count (e.g. `True→1.0` looks like a real tiny overlay). A `bool`/`nan` on the CASCADE (denominator) side is comparatively self-defeating — it coerces to something tiny, which the EXISTING overlay-bigger-than-cascade guard already declines on its own. Only `inf` on the CASCADE side is independently dangerous (trivially satisfies `overlay <= cascade * 0.5` for any finite overlay). `_safe_node_count_float` applies the same symmetric type/finiteness checking to both operands anyway — defense-in-depth, matching this function's own docstring promise, not because every failure mode is equally exploitable.

---

### `_is_delta_scoped_material_simplification(overlay_node_count, replaced_cascade_node_count) -> bool`

Private. The fail-closed SIMPLIFY-clause ratio check `evaluate_calmar_acceptance` delegates to (one of the SIMPLIFY path's three required gates — see the decision-logic table above). Compares the candidate's OWN OVERLAY's signal-logic count against the REPLACED CASCADE's signal-logic count it swaps out — since Revise 3, both operands are ALREADY signal-logic-only by the time they reach this function (the caller in `frontrunner_builder.py` performs the exclusion, via `_count_signal_logic_nodes` — see that module's doc); this function itself does no tree-walking, only the ratio arithmetic and its fail-closed guards.

**Parameters:** `overlay_node_count`, `replaced_cascade_node_count` — untyped at the call boundary; real callers pass `int | None`.

**Returns:** `bool`. Never raises.

**Declines (`False`) when:**
- either operand is `None`, non-numeric (`bool`/string/other — F9), or non-finite (`inf`/`nan` — F9), via `_safe_node_count_float`,
- either operand is `<= 0` — a real compiled overlay always has ≥1 node and a real replaced cascade always has ≥1 node, so `0` can only mean "count unavailable" upstream — treated identically to `None`/absent, never as a legitimately tiny value that would trivially satisfy the ratio,
- the overlay is literally bigger than the cascade it replaces (F12: this is a "ratio >= 1" tripwire, kept as an explicit self-documenting guard — the ratio comparison below would already reject this case on its own, so it is not a co-equal condition alongside the `<=0` checks, just a clarity aid).

**Otherwise:** `overlay <= cascade * MATERIAL_SIMPLIFICATION_MAX_RATIO`.

A caller omitting both operands (the legacy invocation shape — `evaluate_calmar_acceptance`'s two new params both default `None`) always declines here: SIMPLIFY becomes structurally unreachable for an un-migrated call site rather than silently keeping an older comparison's behavior.

## SIMPLIFY-Path Semantics (`DE-FR-SIMPLIFY-001`)

### RULING 1 (CRITICAL, F1, Revise 3) — signal-logic-only, not whole-subtree

**The original `DE-FR-SIMPLIFY-001` cycle's defect and fix.** `MATERIAL_SIMPLIFICATION_MAX_RATIO=0.50` demands the overlay be ≤50% of some reference node count. Pre-fix, that reference was `incumbent_node_count`/`candidate_node_count` — the WHOLE-SYMPHONY node counts. Since a Frontrunner Builder candidate is always a full standalone copy of the incumbent with exactly one detected cascade replaced by a generated overlay, `candidate_node_count` is always ~98-100% of `incumbent_node_count` for any real tree — the ratio could essentially never clear 0.50. Found by PR #126's `/code-review`, fixed by re-scoping the comparison to `overlay_node_count`/`replaced_cascade_node_count` — the generated overlay's node count vs. the incumbent cascade subtree it replaces.

**The load-bearing gap that fix still had, found by PR #128's `/code-review` (CRITICAL F1).** `frontrunner_detector._build_cascade_overlay`'s cascade output (`cascade.overlay_tree`, the object the original fix counted via a plain `_count_tree_nodes` call) is NOT honest signal logic — it is the detector's own STUB-PADDED COMPACT CASCADE. The detector splits an if-node's two branches into "fire" (the smaller-by-node-count side, copied verbatim — real content) and "continuation" (the larger side, REPLACED with synthetic stub leaf placeholders sized to stay larger than fire, so downstream size comparisons remain internally consistent). Counting the WHOLE compacted subtree — as the original fix did — counts core-sized stub padding as "replaced logic." This didn't just understate simplification; it could INFLATE the denominator ARBITRARILY (proven by A4's evidence below), which INVERTED the SIMPLIFY path a second time — not back to unreachable, but to a state where it could admit overlays LARGER than the signal logic they genuinely replace, exactly the mistaken-acceptance failure mode the original `DE-FR-SIMPLIFY-001` cycle set out to prevent.

**The fix.** Both SIMPLIFY operands are now SIGNAL-LOGIC-ONLY — condition + real fire/then branch, EXCLUDING the placeholder-else on the overlay side and the stub-padded continuation on the cascade side. This module's own contract is unaffected — `evaluate_calmar_acceptance`'s signature and `_is_delta_scoped_material_simplification`'s ratio arithmetic are identical; the exclusion logic lives entirely on the CALLER side, in `frontrunner_builder._count_signal_logic_nodes` (new) and its use at the `_run_build_for_symphony` threading site — see `docs/generated/advisors_frontrunner_builder.md`'s "SIMPLIFY-Path Node-Count Threading" section for the full mechanism (how the stub-padded branch is identified via a marker search, and the freshly-generated overlay's placeholder-else via the existing `is-else-condition?` convention). `MATERIAL_SIMPLIFICATION_MAX_RATIO=0.50` is unchanged across BOTH cycles — the calibration basis was always overlay-scale, never whole-symphony scale and never stub-padded-cascade scale.

**Empirical evidence (A4, cross-module pin).** A dedicated test proves `replaced_cascade_node_count` is now INSENSITIVE to stub-padding size — the same real fire content with two wildly different continuation-placeholder sizes produces the SAME resulting signal-logic count. Confirmed maximally sensitive against the PRE-fix code first (small padding=500 → denominator 551, large padding=5000 → denominator 5051 — a 4500-node difference exactly matching the 4500-leaf padding delta), then confirmed fixed.

### RULING 2 (F2, Revise 3) — an independent whole-tree no-growth gate

The delta-scoped signal-logic ratio, however correctly scoped, cannot see the WHOLE-SYMPHONY tree's own size. `frontrunner_builder._graft_incumbent_core` (part of the splice mechanics, unrelated to this module) re-inserts the incumbent's full core into the candidate's else branch — this can make the overall spliced tree BIGGER even though the signal logic genuinely shrank. A candidate whose signal logic is smaller but whose whole tree grew is not a "simplification" in any honest sense. The fix: SIMPLIFY additionally requires `node_count_delta <= 0` (the pre-existing whole-tree `candidate_node_count - incumbent_node_count` computation, already computed for the `AcceptanceResult.node_count_delta` display field) as a THIRD, independent gate — never traded off against the ratio or the Calmar check. The boundary is `<=0`, not `<0` — a candidate whose whole tree is EXACTLY the same size as the incumbent's still qualifies.

### RULING 3 (F3+F10, Revise 3) — node-counting completeness (in `frontrunner_builder.py`, affects this module's inputs)

`_count_tree_nodes` (in `advisors/frontrunner_builder.py`, the shared node-walker feeding both `node_count_delta` and, indirectly via `_count_signal_logic_nodes`, the SIMPLIFY ratio's numerator/denominator) previously only walked a node's `children` list — a compound condition's clause list (`make_compound_condition`'s `conditions` field, a DATA field on an if-child, not under `children`) was invisible to the walk. A 2-clause and a 12-clause compound condition, identical then/else content, previously counted as the SAME total. Fixed by also descending into a `condition` dict field and a `conditions` clause list when present — making clause count genuinely load-bearing on every count this module ultimately receives. See `docs/generated/advisors_frontrunner_builder.md` for the implementation.

### F9/A3 — node-count-operand type/finiteness hardening

See `_safe_node_count_float` above. Declines on `bool`/numeric-string/non-finite/`OverflowError`-inducing inputs to either SIMPLIFY operand, never letting a malformed count silently pass as a plausible one, and never letting an `OverflowError` propagate up to `evaluate_calmar_acceptance`'s outer catch-all (which would otherwise null every OTHER independently-computable reporting field on the result).

## Internal Dependencies

- `dataclasses`, `logging`, `math` (Revise 3, for `math.isnan`/`math.isinf`) — stdlib only. No imports from `database`, `math_engine`, or any network/execution module. Pure math over caller-supplied dicts.

## Consumers

- `advisors/frontrunner_builder.py::_gate_and_accept_candidate` — calls `evaluate_calmar_acceptance` only for a candidate that has already survived `backtest_gate_engine.evaluate_candidate_batch`'s `ADOPT_CANDIDATE` verdict (AC-6 gate is necessary-but-not-sufficient; this module is the sufficiency check). A rejection here (`acceptance.accepted is False`) is persisted as an AC-11 rejected-observation with the incumbent-vs-candidate deltas — since the Revise 3 addendum (A2), the raw `overlay_node_count`/`replaced_cascade_node_count` operands themselves are ALSO persisted verbatim on every outcome (accept, gate-reject, calmar-reject), not just the accepted path — the admission/rejection basis is never lost. See `docs/generated/advisors_frontrunner_builder.md`.

## Testing

`tests/advisors/test_frontrunner_acceptance.py` + `tests/advisors/test_frontrunner_simplify_path_wiring.py` + `tests/security/test_frontrunner_no_trade_boundary.py` — **independently re-run at HEAD `70f832b3`, 2026-08-24: 67 passed, 0 failed** (`-n0`), covering (across both cycles of `DE-FR-SIMPLIFY-001`): Calmar math, the three-gate SIMPLIFY truth table (Calmar-not-worse / signal-logic ratio / whole-tree-no-growth, individually and combined — including `test_simplify_declines_when_whole_tree_grew_even_though_ratio_passes`, `test_simplify_accepts_when_whole_tree_node_count_delta_is_exactly_zero` boundary, `test_simplify_still_declines_on_growth_even_with_a_dramatic_ratio` adversarial pin), the golden-fixture ratio boundary, `_count_tree_nodes`'s compound-condition-clause descent (RULING 3), the F9/A3 node-count-operand hardening (bool/numeric-string/infinite/huge-int-`OverflowError`, with the `OverflowError` test additionally asserting all 5 OTHER reporting fields survive un-nulled), a real `if_compound` overlay counted via the real counter, the drawdown-floor guard, Sharpe/volatility non-gating, tag correctness, IMPROVE-path byte-unchanged pins, and (in the wiring file) the builder-side integration proving `_run_build_for_symphony` threads real signal-logic-only counts end to end, a SIMPLIFY reachability proof against REAL detector output (`_build_real_cascade_via_detector`, never a hand-built `Cascade`), the A4 stub-padding-insensitivity pin, and the redundant-compile regression guards (`test_no_redundant_compile_anywhere_in_the_flow_including_splice`, covering the whole chain including `splice_candidate_into_symphony`). A wider run of the full frontrunner test surface (`tests/advisors -k frontrunner` + the no-trade-boundary suite + the proposal-identity template suite, `-n0`): **370 passed, 1157 deselected, 1 xfailed** — the xfail is the same pre-existing, unrelated `test_real_looking_core_tickers_do_not_leak_into_watched_tickers` documented in `DE-FR-SIGNALS-001`, zero new failures, up from 350 at the original `DE-FR-SIMPLIFY-001` cycle's own HEAD. Both ruff gates (`check` + `format --check`) independently re-verified clean on all touched files.
