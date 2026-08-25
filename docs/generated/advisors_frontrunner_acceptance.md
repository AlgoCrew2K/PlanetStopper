# advisors/frontrunner_acceptance

> Calmar acceptance gate for the Frontrunner Builder — a candidate is admitted only if it improves risk-adjusted return or preserves it while materially simplifying the tree's actual signal logic.

**Source:** `advisors/frontrunner_acceptance.py`
**Last updated:** 2026-08-25 (`DE-FR-SIMPLIFY-001` Revise 5 — a further PR #128 `/code-review` pass against the Revise-4 GREEN returned 7 findings, F1-F7, 3 of which land in this module. **F1 (real behavior change):** the inverted-polarity decline (`fire_is_else_branch=True`) now covers the WHOLE acceptance decision (neither IMPROVE/"performance" nor SIMPLIFY/"simplification"), not just SIMPLIFY — Revise 4 had left IMPROVE reachable unconditionally regardless of polarity, so a genuinely-better-Calmar inverted-polarity candidate could still be accepted and queued for a real Composer draft creation. **F2/F4 (BINDING, docstring-only, zero counting-logic change):** confirms and documents, never unifies, that the cascade-side and overlay-side SIMPLIFY operand counters use genuinely different, intentionally-asymmetric exclusion policies — a trust difference, not a bug; unifying them would REINTRODUCE the Revise-3 CRITICAL fail-open regression. **F3:** `_safe_node_count_int` gains `OverflowError` in its except tuple — `int(float("inf"))` was escaping to the outer catch-all and nulling every other reporting field. See "SIMPLIFY-Path Semantics" below and `DE-FR-SIMPLIFY-001`'s "Revise 5" section in `DECISIONS.md`.) Prior: 2026-08-24 (`DE-FR-SIMPLIFY-001` Revise 4 — a PR #128 `/code-review` re-run found Revise 3's cascade-side stub-marker search (living in `frontrunner_builder.py`) disprovable on multi-tier cascades. Fix: the cascade-side SIMPLIFY operand is relocated to `frontrunner_detector.Cascade.signal_logic_node_count`, computed once at detection time on the pre-stub subtree (`advisors_frontrunner_detector.md` has the mechanism) — this module reads it verbatim via the caller, never re-derives it. `evaluate_calmar_acceptance` gains a new `fire_is_else_branch: bool = False` keyword-only param (Revise 4's final pin, widened to cover both acceptance paths in Revise 5's F1 above); `_safe_node_count_float` gains an `is_integer()` check and a new sibling `_safe_node_count_int` hardens RULING 2's own whole-tree operands (R4-5/B2). Zero change to this module's own SIMPLIFY-ratio arithmetic (`_is_delta_scoped_material_simplification` itself, RULING 1/2's logic) — only the CALLER-supplied operand's provenance and one new gating param changed.) Prior: 2026-08-24 (`DE-FR-SIMPLIFY-001` Revise 3 — PR #128's independent `/code-review` found a CRITICAL defect (F1) in the just-shipped delta-scoped fix: the "replaced cascade" operand still counted the detector's stub-padded compact cascade, not the honest signal logic, which could INFLATE the denominator arbitrarily and invert SIMPLIFY from unreachable to admitting oversized overlays. The SIMPLIFY clause gained THREE independent gates — Calmar-not-worse, a signal-logic-only delta-scoped ratio, and a whole-tree no-growth check (RULING 2) — plus stricter node-count-operand coercion (F9/A3). **The counting mechanism this entry originally described (a builder-side stub-marker search) is superseded by Revise 4 above — see "SIMPLIFY-Path Semantics" below for current truth.** See `DE-FR-SIMPLIFY-001`'s "Revise 3" section in `DECISIONS.md` for the historical record.) Prior: 2026-08-24, original cycle (the SIMPLIFY path's node-count comparison re-scoped from whole-symphony node counts to a delta-scoped comparison — see `DE-FR-SIMPLIFY-001`'s main section in `DECISIONS.md`). Prior: 2026-07-11 (Wave-1 backend, frreview-APPROVED, unchanged by the subsequent P2-1/P2-2 hardening — those touched `frontrunner_detector.py`/`frontrunner_builder.py` only)

## Overview

`advisors/frontrunner_acceptance.py` implements AC-7 of the Frontrunner Builder plan: the final acceptance decision applied to a candidate that has already survived the shared overfitting guardrail (`backtest_gate_engine.evaluate_candidate_batch`). Gate survival is **necessary but not sufficient** — a candidate must also demonstrably improve the strategy, or simplify it without cost.

Acceptance is **two independent paths**, either of which admits a candidate — **both gated by a shared polarity precondition (Revise 5, F1):**

1. **IMPROVE** — the candidate's Calmar ratio (`CAGR / |max_drawdown|`) is strictly higher than the incumbent's, AND the candidate's own absolute drawdown does not breach a floor (a candidate cannot buy an improved ratio via extreme leverage that also posts a catastrophic drawdown). Reads none of the SIMPLIFY-path operands.
2. **PRESERVE + SIMPLIFY** (`DE-FR-SIMPLIFY-001` — three independent gates, ALL required) —
   - the candidate's Calmar is within tolerance of the incumbent's (neither meaningfully better nor worse), AND
   - the generated overlay's **signal logic** (its condition + real fire/then branch, EXCLUDING its placeholder-else) is materially smaller than the replaced cascade's own **signal logic** (its condition + real fire branch, EXCLUDING its stub-padded continuation) — never the whole-symphony node counts (stay ~98-100% of each other for any single-cascade splice) and never the whole compacted/compiled subtree including its stub/placeholder branch (RULING 1, the CRITICAL fix — see below), AND
   - the whole-symphony tree did NOT grow (`node_count_delta <= 0` — RULING 2, an independent invariant the delta-scoped ratio alone cannot see).

   Same drawdown-floor guard applies as IMPROVE.

**Polarity precondition (Revise 5, F1 — evaluated FIRST, wraps BOTH paths above).** When `fire_is_else_branch=True` (the detected cascade's fire/signal content sits on the untrustworthy `is-else-condition?==True` side), the WHOLE acceptance declines unconditionally — `accepted=False`, `tags=set()` — regardless of what either path's own gates would otherwise say. Revise 4 introduced this precondition scoped to SIMPLIFY only; Revise 5's F1 widened it to cover IMPROVE too, since a genuinely-better-Calmar inverted-polarity candidate was previously still reachable via IMPROVE and could be queued for a real Composer draft creation. No early return — every other reporting field (`candidate_sharpe`/`candidate_volatility`/both Calmars/`node_count_delta`) stays genuinely computed. **Note (`DE-FR-SPLICE-POLARITY-001`, 2026-08-25):** at the time Revise 4/5 landed, `_graft_incumbent_core`'s core-preservation logic assumed normal polarity and would silently drop the real core on this shape — this precondition was the ONLY firewall against that. That construction-level defect is now fixed independently (see `docs/generated/advisors_frontrunner_builder.md`'s `_graft_incumbent_core` subsection); this acceptance-side decline is UNCHANGED and remains an independent, still-required safeguard regardless.

**Sharpe and volatility are always reported** on the result (for the Advisor-tab card, once built) but **never gate acceptance either way** — a terrible Sharpe cannot sink an otherwise-accepted candidate, and a great Sharpe cannot rescue an otherwise-rejected one (AC-7 explicit).

This module never trusts an incoming pre-computed Calmar figure — it derives Calmar itself from the caller-supplied CAGR + max_drawdown metrics dict fields (mirroring the project-wide "never trust incoming oos_metrics" posture used by `build_plan_generator`/`strategy_builder_engine`). Fails **closed** (rejects) on any missing/None metric or malformed input — never raises, never fabricates an accept on incomplete data.

Off-execution-path. Pure function, no I/O, no network, no DB access.

## Named Constants

| Name | Value | Rationale |
|------|-------|-----------|
| `CALMAR_PRESERVE_TOLERANCE` | `0.02` (2% relative) | Tight enough that a genuine performance improvement is never miscategorized as "merely preserved", while tolerating floating-point noise between two independent re-backtests of structurally-identical trees |
| `MATERIAL_SIMPLIFICATION_MAX_RATIO` | `0.50` | The overlay's SIGNAL-LOGIC-ONLY count qualifies as "materially simpler" than the replaced cascade's SIGNAL-LOGIC-ONLY count at ≤50% — deliberately a large threshold, since the grounding note describes collapsing "hundreds of flat RSI-gt rungs" via any/all (an order-of-magnitude reduction, not a marginal trim). **Value unchanged across every revision of `DE-FR-SIMPLIFY-001` (original cycle through Revise 5, 2026-08-25)** — the calibration basis was always overlay-scale (a handful to a few dozen nodes), never whole-symphony scale and never stub-padded-cascade scale; every fix in this feature corrected WHICH operands the ratio is compared against (and, as of Revise 4/5, WHERE those operands are computed and under what exclusion policy), never the ratio's own value |
| `MAX_ABSOLUTE_DRAWDOWN_FLOOR` | `0.40` (40%) | A candidate's own max drawdown must never exceed this regardless of Calmar improvement — prevents accepting a candidate that "improves" Calmar only by pairing an extreme CAGR with an extreme, separately unacceptable drawdown. Generous ceiling — well above any of the operator's real incumbent symphonies' historical drawdowns, so it only fires on a genuinely pathological candidate |

## Public Types

### `AcceptanceResult` (dataclass)

Returned by `evaluate_calmar_acceptance`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `accepted` | `bool` | `True` if either acceptance path admitted the candidate |
| `tags` | `set[str]` | Subset of `{"performance", "simplification"}` — which path(s) admitted the candidate. Always empty when `accepted` is `False` |
| `node_count_delta` | `int \| None` | `candidate_node_count - incumbent_node_count` (negative = simpler), or `None` if either whole-tree operand fails `_safe_node_count_int` coercion (R4-5, Revise 4 — never a fabricated `0`). Whole-symphony semantics, byte-unchanged across every `DE-FR-SIMPLIFY-001` revision. Serves TWO roles since Revise 3: the pre-existing display metric, AND (RULING 2) a live SIMPLIFY-gate input — `node_count_delta <= 0` is the third, independent no-growth gate |
| `candidate_sharpe` | `float \| None` | Reported for the Advisor-tab card; never gates acceptance |
| `candidate_volatility` | `float \| None` | Reported for the Advisor-tab card; never gates acceptance |
| `incumbent_calmar` | `float \| None` | The incumbent's derived Calmar, or `None` if undefined (zero drawdown) or unavailable |
| `candidate_calmar` | `float \| None` | The candidate's derived Calmar, or `None` if undefined/unavailable |

## API Reference

### `compute_calmar(cagr: float, max_drawdown: float) -> float | None`

Returns `cagr / abs(max_drawdown)`. Returns `None` (never raises, never `inf`) when `max_drawdown` is exactly `0`.

`max_drawdown` follows the quantstats convention (`<= 0`; a negative fraction, e.g. `-0.08` = 8% drawdown) — the absolute value is used as the denominator so a valid negative `max_drawdown` always yields a signed Calmar matching the sign of `cagr`.

---

### `evaluate_calmar_acceptance(incumbent_metrics: dict, candidate_metrics: dict, *, incumbent_node_count: int, candidate_node_count: int, overlay_node_count: int | None = None, replaced_cascade_node_count: int | None = None, fire_is_else_branch: bool = False) -> AcceptanceResult`

The AC-7 acceptance gate. **Signature gains `fire_is_else_branch` in Revise 4** (2026-08-24) — the other 6 params are unchanged since the original `DE-FR-SIMPLIFY-001` cycle. Revise 3 changed the SIMPLIFY clause's internal decision logic; Revise 4 changed the CALLER-SIDE provenance of `overlay_node_count`/`replaced_cascade_node_count` (now `overlay_node_count` from `frontrunner_builder._count_overlay_node_count`, `replaced_cascade_node_count` read verbatim off `frontrunner_detector.Cascade.signal_logic_node_count` — see `docs/generated/advisors_frontrunner_builder.md`/`advisors_frontrunner_detector.md`) plus added the polarity param; Revise 5's F1 widened `fire_is_else_branch`'s effect from SIMPLIFY-only to the whole function (this function's own decision-logic restructure, not a caller-side/signature change).

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `incumbent_metrics`, `candidate_metrics` | `dict` | Shaped like `analytics.compute_quantstats_metrics`'s output: `annualized_return` (CAGR), `max_drawdown` (<= 0), optionally `sharpe`/`volatility` |
| `incumbent_node_count`, `candidate_node_count` | `int` | Total node counts of the incumbent and candidate SYMPHONY trees — feed the `node_count_delta` display metric (AC-6) AND (RULING 2) the SIMPLIFY path's whole-tree no-growth gate. Never consulted by the delta-scoped ratio itself. Coerced via `_safe_node_count_int` (R4-5, Revise 4) — a bool/non-numeric-string/`OverflowError`-inducing value (F3, Revise 5) declines rather than raising, making `node_count_delta` itself `None` rather than fabricating `0` |
| `overlay_node_count`, `replaced_cascade_node_count` | `int \| None` | SIGNAL-LOGIC-ONLY counts (caller-derived — condition + real fire/then branch on each side, excluding the overlay's placeholder-else and the cascade's stub-padded continuation), not whole-subtree counts. **Computed via genuinely DIFFERENT, intentionally-asymmetric exclusion policies on each side (Revise 5, F2/F4 — see "SIMPLIFY-Path Semantics" below; do not expect symmetry).** Keyword-only, additive, default `None`. Omitting them makes SIMPLIFY structurally unreachable (fail-closed), never a silent fallback to an older, broken comparison |
| `fire_is_else_branch` | `bool` | **New, Revise 4; scope widened, Revise 5 F1.** `True` when the cascade's fire (signal) content sits on the untrustworthy `is-else-condition?==True` side (inverted polarity). Declines the WHOLE acceptance unconditionally when `True` (Revise 4 had scoped this to SIMPLIFY only). Default `False` (never declining by default on an omitted value). An independent, still-required safeguard — see the "Polarity precondition" note above (`DE-FR-SPLICE-POLARITY-001`, 2026-08-25) |

**Returns:** `AcceptanceResult`. Never raises (D-1).

**Decision logic (in order):**

1. **Fail-closed on missing data.** Any of `incumbent_cagr`/`incumbent_mdd`/`candidate_cagr`/`candidate_mdd` being `None`/non-numeric → immediate reject (no accept on incomplete data).
2. **Fail-closed on undefined Calmar.** Either side's `compute_calmar` returning `None` (zero drawdown) → reject.
3. **Drawdown-floor guard (applies to BOTH paths).** `abs(candidate_mdd) > MAX_ABSOLUTE_DRAWDOWN_FLOOR` → reject regardless of Calmar.
4. **Both paths' gate inputs computed unconditionally** (`calmar_not_worse`, `is_materially_simpler`, `whole_tree_did_not_grow`) — needed either way below; only whether a tag ever gets ADDED is conditional on step 5.
5. **Polarity gate (Revise 5, F1 — evaluated FIRST, short-circuits both paths).** `fire_is_else_branch=True` → log a WARNING, add NO tags (neither "performance" nor "simplification"); the `AcceptanceResult` construction is still reached with every reporting field genuinely populated (no early return). `fire_is_else_branch=False` → proceed to 5a/5b:
   - **5a. Path 1 — IMPROVE:** `candidate_calmar > incumbent_calmar` → tag `"performance"`. Reads none of the SIMPLIFY-path params (AC-4).
   - **5b. Path 2 — SIMPLIFY, THREE independent gates, ALL required (Revise 3, unchanged since):**
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
    fire_is_else_branch=False,            # normal polarity -> both paths reachable
)
if result.accepted:
    print("accepted:", result.tags, result.candidate_calmar, "vs", result.incumbent_calmar)
```

---

### `_safe_node_count_float(value) -> float | None`

**New, `DE-FR-SIMPLIFY-001` Revise 3 (F9).** Private. Stricter float coercion for the SIMPLIFY-clause node-count operands ONLY — never used for CAGR/MDD/sharpe/volatility parsing (that stays on the general-purpose `_safe_float`; deliberately not touching that shared helper keeps this hardening scoped to exactly the operands it targets).

A node count is never legitimately: a `bool` (Python's `bool` is an `int` subtype — `float(True) == 1.0` would silently coerce a caller bug into a plausible-looking tiny count), a numeric string (`float("20") == 20.0` — same silent-coercion risk), or non-finite (`inf` trivially satisfies any ratio comparison against a finite counterpart; `nan` comparisons are always `False`, an ambiguous fall-through rather than an honest decline). A huge integer (e.g. `10**400`) raises `OverflowError` from `float()` — caught here (A3) so the caller's OTHER reporting fields (Sharpe/volatility/both Calmar values/`node_count_delta`, all independently computable) are never lost to an unhandled exception escaping to `evaluate_calmar_acceptance`'s own outer catch-all, which nulls everything.

**Returns:** `float | None`. Declines (`None`, never raises) on any of: non-`(int, float)` type (including `bool`), `TypeError`/`ValueError`/`OverflowError` from `float()`, or `math.isnan`/`math.isinf`.

**B2 (Revise 4) — non-whole floats also decline.** After the finiteness check, a genuinely non-integral result (`not result.is_integer()`, e.g. `3.7`) also declines — a node count is never legitimately fractional. A whole-number float (e.g. `20.0`, which a caller-side arithmetic operation might plausibly produce) is still accepted.

**Fabrication-risk asymmetry (documented in the TDD handoff, not in source comments — recorded here for completeness):** the risk is not symmetric between the two operands. A `bool`/numeric-string on the OVERLAY (numerator) side is genuinely dangerous — it coerces to a small, plausible-looking node count (e.g. `True→1.0` looks like a real tiny overlay). A `bool`/`nan` on the CASCADE (denominator) side is comparatively self-defeating — it coerces to something tiny, which the EXISTING overlay-bigger-than-cascade guard already declines on its own. Only `inf` on the CASCADE side is independently dangerous (trivially satisfies `overlay <= cascade * 0.5` for any finite overlay). `_safe_node_count_float` applies the same symmetric type/finiteness checking to both operands anyway — defense-in-depth, matching this function's own docstring promise, not because every failure mode is equally exploitable.

---

### `_safe_node_count_int(value) -> int | None`

**New, Revise 4 (R4-5/B2).** Private. Stricter int coercion for RULING 2's own whole-tree operands (`incumbent_node_count`/`candidate_node_count`, feeding `node_count_delta`) — the same hardening class as `_safe_node_count_float`, applied to the OTHER pair of node-count inputs.

`bool` is rejected explicitly (`int(True) == 1` would silently coerce — the same class of caller-bug risk every other operand in this module guards against). `None`/a non-numeric string/other malformed input degrades via the natural `int(value)` `TypeError`/`ValueError`, caught here rather than allowed to escape to `evaluate_calmar_acceptance`'s outer catch-all (which would null every OTHER reporting field, not just `node_count_delta`). A genuinely numeric string (e.g. `"50"`) is still accepted — `int()` already handles that correctly and no test requires rejecting it, unlike `_safe_node_count_float`'s ratio operands, which reject ALL strings.

**F3 (Revise 5).** `int(float("inf"))` raises `OverflowError`, not `TypeError`/`ValueError` — confirmed directly against the interpreter, and identical for `numpy.float64("inf")` and `decimal.Decimal("Infinity")`. Uncaught, this escaped to `evaluate_calmar_acceptance`'s outer catch-all, nulling every OTHER reporting field too (candidate_sharpe/candidate_volatility/both Calmars) AND fabricating `node_count_delta=0` (the outer catch-all's `_rejected()` default, not an honest `None`). Now caught here, mirroring `_safe_node_count_float`'s existing `OverflowError` handling. No separate `isnan`/`isinf` check is needed on the int path — `int(NaN)` already raises `ValueError` (already caught).

**Returns:** `int | None`. Declines (`None`, never raises) on any of the above.

---

### `_is_delta_scoped_material_simplification(overlay_node_count, replaced_cascade_node_count) -> bool`

Private. The fail-closed SIMPLIFY-clause ratio check `evaluate_calmar_acceptance` delegates to (one of the SIMPLIFY path's three required gates — see the decision-logic table above). Compares the candidate's OWN OVERLAY's signal-logic count against the REPLACED CASCADE's signal-logic count it swaps out — both operands are ALREADY signal-logic-only by the time they reach this function; this function itself does no tree-walking, only the ratio arithmetic and its fail-closed guards. **Provenance of the two operands (Revise 4, current):** the caller (`frontrunner_builder._run_build_for_symphony`) supplies `replaced_cascade_node_count` read VERBATIM off `frontrunner_detector.Cascade.signal_logic_node_count` (computed once at detection time, on the pre-stub subtree — see `docs/generated/advisors_frontrunner_detector.md`) and `overlay_node_count` via `frontrunner_builder._count_overlay_node_count(compiled_tree)` (see `docs/generated/advisors_frontrunner_builder.md`). **The two operands use genuinely DIFFERENT exclusion policies by design (Revise 5, F2/F4) — see "SIMPLIFY-Path Semantics" below; this function's own arithmetic is symmetric (`overlay <= cascade * ratio`), the asymmetry lives entirely in how each operand is counted before it reaches here.**

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

**The fix (Revise 3, as originally shipped — see "Revise 4" below for the current mechanism).** Both SIMPLIFY operands became SIGNAL-LOGIC-ONLY — condition + real fire/then branch, EXCLUDING the placeholder-else on the overlay side and the stub-padded continuation on the cascade side. This module's own contract was unaffected — `evaluate_calmar_acceptance`'s signature and `_is_delta_scoped_material_simplification`'s ratio arithmetic stayed identical; the exclusion logic lived entirely on the CALLER side, in `frontrunner_builder._count_signal_logic_nodes` (a stub-marker search identifying the stub-padded branch from outside the detector). `MATERIAL_SIMPLIFICATION_MAX_RATIO=0.50` is unchanged across every revision — the calibration basis was always overlay-scale, never whole-symphony scale and never stub-padded-cascade scale. **This exact mechanism (the stub-marker search, `_count_signal_logic_nodes`, `_contains_stub_marker`) is GONE as of Revise 4 — see below.**

**Empirical evidence (A4, cross-module pin, still current).** A dedicated test proves `replaced_cascade_node_count` is INSENSITIVE to stub-padding size — the same real fire content with two wildly different continuation-placeholder sizes produces the SAME resulting signal-logic count. Confirmed maximally sensitive against the PRE-Revise-3 code first (small padding=500 → denominator 551, large padding=5000 → denominator 5051 — a 4500-node difference exactly matching the 4500-leaf padding delta), then confirmed fixed. This test still runs and still passes post-Revise-4/5, since Revise 4's relocation preserves the same insensitivity property by construction (the count is now derived from the pre-stub subtree directly, never touching the padded overlay at all).

### RULING 2 (F2, Revise 3, unchanged since) — an independent whole-tree no-growth gate

The delta-scoped signal-logic ratio, however correctly scoped, cannot see the WHOLE-SYMPHONY tree's own size. `frontrunner_builder._graft_incumbent_core` (part of the splice mechanics, unrelated to this module) re-inserts the incumbent's full core into the candidate's else branch — this can make the overall spliced tree BIGGER even though the signal logic genuinely shrank. A candidate whose signal logic is smaller but whose whole tree grew is not a "simplification" in any honest sense. The fix: SIMPLIFY additionally requires `node_count_delta <= 0` (the pre-existing whole-tree `candidate_node_count - incumbent_node_count` computation, already computed for the `AcceptanceResult.node_count_delta` display field) as a THIRD, independent gate — never traded off against the ratio or the Calmar check. The boundary is `<=0`, not `<0` — a candidate whose whole tree is EXACTLY the same size as the incumbent's still qualifies.

### RULING 3 (F3+F10, Revise 3) — node-counting completeness — **[SUPERSEDED, Revise 4, 2026-08-24]**

`_count_tree_nodes` (in `advisors/frontrunner_builder.py`) previously only walked a node's `children` list — a compound condition's clause list (`make_compound_condition`'s `conditions` field, a DATA field on an if-child, not under `children`) was invisible to the walk. A 2-clause and a 12-clause compound condition, identical then/else content, previously counted as the SAME total. Revise 3 fixed this by making `_count_tree_nodes` itself descend into a `condition` dict field and a `conditions` clause list. **Revise 4 REVERTED this** — `_count_tree_nodes` is children-only again. `_count_tree_nodes`'s only remaining consumer (RULING 2's whole-symphony `node_count_delta` gate) never needed clause-awareness in the first place (a coarse did-it-grow-at-all check); clause-aware counting for the operands that DO need it (this module's own SIMPLIFY-ratio operands) now lives exclusively in the new, dedicated `frontrunner_detector._count_clause_aware_signal_logic` — see "Revise 4" below and `docs/generated/advisors_frontrunner_builder.md`. This historical bullet is left unedited per the project's non-destructive-annotation convention; see `DE-FR-SIMPLIFY-001`'s "Revise 4" section in `DECISIONS.md` for the full account.

### Revise 4 (2026-08-24) — architectural relocation: the cascade-side count moves into the detector

**Trigger.** A further `/code-review` pass on PR #128 found RULING 1's stub-marker search (`frontrunner_builder._contains_stub_marker`) disprovable on multi-tier cascades — both if-children of a multi-tier cascade's root can carry a stub marker when the fire branch itself contains an already-compacted nested tier, making "which side is the placeholder" genuinely ambiguous from OUTSIDE the detector.

**The fix.** `frontrunner_detector.Cascade` gains two additive fields — `signal_logic_node_count: int | None`, `fire_is_else_branch: bool` — computed ONCE at detection time, on the PRE-STUB original subtree (never the padded compact overlay), via two new detector functions (`_compute_signal_logic_node_count`, `_compute_fire_is_else_branch`; see `docs/generated/advisors_frontrunner_detector.md`). `evaluate_calmar_acceptance`'s CALLER (`frontrunner_builder._run_build_for_symphony`) now reads `cascade.signal_logic_node_count` VERBATIM — this module's own `replaced_cascade_node_count` parameter and `_is_delta_scoped_material_simplification`'s ratio arithmetic are UNCHANGED; only the caller-side PROVENANCE of the value changed. `frontrunner_builder._count_signal_logic_nodes`/`_contains_stub_marker` (RULING 1's Revise-3 fix) are DELETED. `evaluate_calmar_acceptance` gains the new `fire_is_else_branch: bool = False` keyword-only param (see the API Reference above) — declines SIMPLIFY (only, at this stage) unconditionally on inverted polarity.

### Revise 5 (2026-08-25) — F1 widens the polarity gate; F2/F4 document (never unify) the counter asymmetry; F3 hardens `_safe_node_count_int`

**F1 (real behavior change).** Root cause: the Revise-4 polarity check only gated SIMPLIFY — `if candidate_calmar > incumbent_calmar: tags.add("performance")` ran UNCONDITIONALLY before it. An inverted-polarity candidate with a merely-better Calmar could still be `accepted=True, tags={"performance"}` and queued for a real Composer draft creation despite `_graft_incumbent_core` silently dropping the real core for that polarity at the time (that construction-level defect was fixed independently by `DE-FR-SPLICE-POLARITY-001`, 2026-08-25 — see the "Polarity precondition" note above). Fixed by restructuring so the polarity check wraps BOTH tag-setting blocks (see the Decision-Logic table's step 5 above) — no early return, all 5 other reporting fields still genuinely computed.

**F2/F4 (BINDING — DO NOT UNIFY THE TWO COUNTERS; docstring-only, zero counting-logic change).** The cascade-side counter (`frontrunner_detector._compute_signal_logic_node_count`) and the overlay-side counter (`frontrunner_builder._count_overlay_node_count` → `frontrunner_detector._count_clause_aware_signal_logic`) use GENUINELY DIFFERENT exclusion policies — intentional, not an inconsistency:

- **Cascade side:** excludes a nested qualifying tier's own continuation ENTIRELY, at EVERY nesting level — never counted, regardless of size.
- **Overlay side:** counts EVERYTHING under the fire child, EXCLUDING ONLY the OUTERMOST placeholder-else — a nested tier's own else IS counted.

**Why this is correct (trust asymmetry, not an oversight):**
- **Overlay (candidate) side** — `frontrunner_builder._find_terminal_else_child`'s own docstring documents a MANUFACTURING-TIME GUARANTEE this module controls: tiers nest ONLY inside `'then'`; a nested tier's own `'else'` is real lower-intensity hedge content, NEVER a placeholder. Verified against real fixtures and the flagship 2-tier worked example — counting it is structurally safe, no core-content smuggling is possible on a self-generated candidate.
- **Cascade (incumbent) side** — `frontrunner_detector._is_internal_hedge_subgate`'s own docstring documents REAL core-leak risk on the DETECTED, untrusted incumbent: a nested continuation can genuinely be unrelated core-strategy bulk (thousands of nodes) — the exact defect class this feature exists to prevent (Revise 3's stub-padding-bulk regression). Excluding at every qualifying nesting level is the conservative, fail-closed choice.
- **Risk asymmetry:** over-excluding a genuine small nested-tier continuation on the cascade side costs a few nodes (bounded, small). Under-excluding (counting it) risks unboundedly inflating the denominator with real core bulk if the leak risk materializes (unbounded, large). Bounded-small-cost beats unbounded-large-risk for untrusted input — a different risk profile than the candidate side's structural guarantee.

**Unifying the two counters would REINTRODUCE the Revise-3 CRITICAL fail-open regression** (untrusted core bulk inflating the "replaced logic" denominator → admits oversized overlays) — explicitly, deliberately NOT done.

**Hand-derived fixtures (both pinned).** Both fixtures use IDENTICAL tier structure/tickers/wrappers (UVXY fire, VIXM+BIL nested-tier-else, a large outer-continuation/placeholder) so the diff isolates cleanly to the ONE structural difference: cascade-side `signal_logic_node_count == 6` (excludes the nested tier's own else — 4 nodes — plus the outer's 202-node real core); overlay-side `_count_overlay_node_count(...) == 10` (includes those same 4 nodes, excludes only the 3-node outer placeholder). **Diff = exactly 4** — the nested tier's own else subtree.

**F4's `condition`/`conditions` fold-in confirmed NOT a real gap.** `_compute_signal_logic_node_count`'s general branch reads only `current.get("condition")` (singular) — grepping all 6 real fixtures under `tests/fixtures/advisors/frontrunner/` for the literal string `"conditions"` confirmed every occurrence is reached exclusively via a wrapping `"condition"` key (matching `symphony_schema.make_compound_condition`'s own construction), and the walk delegates the ENTIRE subtree to `frontrunner_detector._count_clause_aware_signal_logic` as soon as it finds ANY `.get("condition")` dict — dead code under the real schema, not a functional undercount. Pinned positively (hand-derived `7 + n_clauses` formula, N∈{2,12}), not just negatively.

**F3 — `_safe_node_count_int` hardening.** See the `_safe_node_count_int` entry above — `OverflowError` added to its except tuple.

## Internal Dependencies

- `dataclasses`, `logging`, `math` (Revise 3, for `math.isnan`/`math.isinf`) — stdlib only. No imports from `database`, `math_engine`, or any network/execution module. Pure math over caller-supplied dicts.

## Consumers

- `advisors/frontrunner_builder.py::_gate_and_accept_candidate` — calls `evaluate_calmar_acceptance` only for a candidate that has already survived `backtest_gate_engine.evaluate_candidate_batch`'s `ADOPT_CANDIDATE` verdict (AC-6 gate is necessary-but-not-sufficient; this module is the sufficiency check). A rejection here (`acceptance.accepted is False`) is persisted as an AC-11 rejected-observation with the incumbent-vs-candidate deltas — since the Revise 3 addendum (A2), the raw `overlay_node_count`/`replaced_cascade_node_count` operands themselves are ALSO persisted verbatim on every outcome (accept, gate-reject, calmar-reject), not just the accepted path — the admission/rejection basis is never lost. `replaced_cascade_node_count` is sourced from `frontrunner_detector.Cascade.signal_logic_node_count` (Revise 4) and `overlay_node_count` from `frontrunner_builder._count_overlay_node_count` — see `docs/generated/advisors_frontrunner_builder.md` and `docs/generated/advisors_frontrunner_detector.md`.

## Testing

`tests/advisors/test_frontrunner_acceptance.py` + `tests/advisors/test_frontrunner_simplify_path_wiring.py` + `tests/security/test_frontrunner_no_trade_boundary.py` — **independently re-run at HEAD `37ab39ed` (Revise 5 GREEN), 2026-08-25 by fps-doc: 87 passed, 0 failed** (`-n0`), covering: Calmar math, the polarity precondition (F1, Revise 5 — declines the WHOLE acceptance on `fire_is_else_branch=True`, both a "was reachable via IMPROVE pre-fix" regression pin and a "still reachable via IMPROVE when polarity is normal" contrast pin), the three-gate SIMPLIFY truth table (Calmar-not-worse / signal-logic ratio / whole-tree-no-growth, individually and combined), the golden-fixture ratio boundary, the F9/B2/F3 node-count-operand hardening across BOTH `_safe_node_count_float` and `_safe_node_count_int` (bool/numeric-string/non-integral-float/infinite/huge-int-`OverflowError`, with the `OverflowError` tests additionally asserting all 5 OTHER reporting fields survive un-nulled), a real `if_compound` overlay counted via the real counter, the drawdown-floor guard, Sharpe/volatility non-gating, tag correctness, IMPROVE-path pins, and (in the wiring file) the builder-side integration proving `_run_build_for_symphony` threads `cascade.signal_logic_node_count`/`cascade.fire_is_else_branch` and `_count_overlay_node_count(compiled_tree)`'s real signal-logic-only counts end to end, a SIMPLIFY reachability proof against REAL detector output (`_build_real_cascade_via_detector`, never a hand-built `Cascade`), the A4 stub-padding-insensitivity pin, and the redundant-compile regression guards. Adding `tests/advisors/test_frontrunner_detector_r4_signal_logic.py` (the detector-side signal-logic tests, including the Revise-5 F2/F4 cascade-side exclusion-policy pin and the F5 call-count spy — see `docs/generated/advisors_frontrunner_detector.md`) brings the 4-file targeted suite to **103 passed, 0 failed**, matching fps-test-writer's own reported figure exactly. A wider run of the full frontrunner test surface (`tests/advisors -k frontrunner` + the no-trade-boundary suite + the proposal-identity template suite, `-n0`): **406 passed, 1157 deselected, 1 xfailed** — the xfail is the same pre-existing, unrelated `test_real_looking_core_tickers_do_not_leak_into_watched_tickers` documented in `DE-FR-SIGNALS-001`, zero new failures, up from 370 at the Revise-3 HEAD (`70f832b3`). Both ruff gates (`check` + `format --check`) independently re-verified clean on all 3 touched production files at HEAD `37ab39ed`.
