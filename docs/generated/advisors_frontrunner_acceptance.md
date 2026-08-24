# advisors/frontrunner_acceptance

> Calmar acceptance gate for the Frontrunner Builder — a candidate is admitted only if it improves risk-adjusted return or preserves it while materially simplifying the tree.

**Source:** `advisors/frontrunner_acceptance.py`
**Last updated:** 2026-08-24 (`DE-FR-SIMPLIFY-001` — the SIMPLIFY path's node-count comparison is re-scoped from whole-symphony node counts, which stay ~98-100% of each other for any single-cascade splice and were structurally unreachable, to a delta-scoped comparison of the generated overlay against the incumbent cascade subtree it replaces. See "SIMPLIFY-Path Delta-Scoping" below and `DE-FR-SIMPLIFY-001` in `DECISIONS.md`.) Prior: 2026-07-11 (Wave-1 backend, frreview-APPROVED, unchanged by the subsequent P2-1/P2-2 hardening — those touched `frontrunner_detector.py`/`frontrunner_builder.py` only)

## Overview

`advisors/frontrunner_acceptance.py` implements AC-7 of the Frontrunner Builder plan: the final acceptance decision applied to a candidate that has already survived the shared overfitting guardrail (`backtest_gate_engine.evaluate_candidate_batch`). Gate survival is **necessary but not sufficient** — a candidate must also demonstrably improve the strategy, or simplify it without cost.

Acceptance is **two independent paths**, either of which admits a candidate:

1. **IMPROVE** — the candidate's Calmar ratio (`CAGR / |max_drawdown|`) is strictly higher than the incumbent's, AND the candidate's own absolute drawdown does not breach a floor (a candidate cannot buy an improved ratio via extreme leverage that also posts a catastrophic drawdown).
2. **PRESERVE + SIMPLIFY** — the candidate's Calmar is within tolerance of the incumbent's (neither meaningfully better nor worse) AND the candidate's generated overlay is materially smaller than the incumbent cascade subtree it replaces (`DE-FR-SIMPLIFY-001`, delta-scoped — **not** the whole-symphony node counts), same drawdown-floor guard applies.

**Sharpe and volatility are always reported** on the result (for the Advisor-tab card, once built) but **never gate acceptance either way** — a terrible Sharpe cannot sink an otherwise-accepted candidate, and a great Sharpe cannot rescue an otherwise-rejected one (AC-7 explicit).

This module never trusts an incoming pre-computed Calmar figure — it derives Calmar itself from the caller-supplied CAGR + max_drawdown metrics dict fields (mirroring the project-wide "never trust incoming oos_metrics" posture used by `build_plan_generator`/`strategy_builder_engine`). Fails **closed** (rejects) on any missing/None metric or malformed input — never raises, never fabricates an accept on incomplete data.

Off-execution-path. Pure function, no I/O, no network, no DB access.

## Named Constants

| Name | Value | Rationale |
|------|-------|-----------|
| `CALMAR_PRESERVE_TOLERANCE` | `0.02` (2% relative) | Tight enough that a genuine performance improvement is never miscategorized as "merely preserved", while tolerating floating-point noise between two independent re-backtests of structurally-identical trees |
| `MATERIAL_SIMPLIFICATION_MAX_RATIO` | `0.50` | A candidate's overlay qualifies as "materially simpler" than the cascade it replaces at ≤50% of the replaced cascade's node count — deliberately a large threshold, since the grounding note describes collapsing "hundreds of flat RSI-gt rungs" via any/all (an order-of-magnitude reduction, not a marginal trim). **Value unchanged by `DE-FR-SIMPLIFY-001` (2026-08-24)** — the constant was always calibrated against overlay-vs-cascade scale; the pre-fix code compared it against whole-symphony node counts instead, which is what made the comparison structurally unreachable, not the threshold value itself |
| `MAX_ABSOLUTE_DRAWDOWN_FLOOR` | `0.40` (40%) | A candidate's own max drawdown must never exceed this regardless of Calmar improvement — prevents accepting a candidate that "improves" Calmar only by pairing an extreme CAGR with an extreme, separately unacceptable drawdown. Generous ceiling — well above any of the operator's real incumbent symphonies' historical drawdowns, so it only fires on a genuinely pathological candidate |

## Public Types

### `AcceptanceResult` (dataclass)

Returned by `evaluate_calmar_acceptance`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `accepted` | `bool` | `True` if either acceptance path admitted the candidate |
| `tags` | `set[str]` | Subset of `{"performance", "simplification"}` — which path(s) admitted the candidate. Always empty when `accepted` is `False` |
| `node_count_delta` | `int` | `candidate_node_count - incumbent_node_count` (negative = simpler) |
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

The AC-7 acceptance gate.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `incumbent_metrics`, `candidate_metrics` | `dict` | Shaped like `analytics.compute_quantstats_metrics`'s output: `annualized_return` (CAGR), `max_drawdown` (<= 0), optionally `sharpe`/`volatility` |
| `incumbent_node_count`, `candidate_node_count` | `int` | Total node counts of the incumbent and candidate SYMPHONY trees — used ONLY for the `node_count_delta` display metric (AC-6, unrelated to acceptance). Never consulted by the SIMPLIFY clause |
| `overlay_node_count`, `replaced_cascade_node_count` | `int \| None` | **New, `DE-FR-SIMPLIFY-001` (2026-08-24).** Keyword-only, additive, default `None`. The delta-scoped SIMPLIFY-path operands — the candidate's own small generated overlay vs. the incumbent cascade subtree it replaces. Omitting them makes SIMPLIFY structurally unreachable (fail-closed), never a silent fallback to the old whole-tree comparison |

**Returns:** `AcceptanceResult`. Never raises (D-1).

**Decision logic (in order):**

1. **Fail-closed on missing data.** Any of `incumbent_cagr`/`incumbent_mdd`/`candidate_cagr`/`candidate_mdd` being `None`/non-numeric → immediate reject (no accept on incomplete data).
2. **Fail-closed on undefined Calmar.** Either side's `compute_calmar` returning `None` (zero drawdown) → reject.
3. **Drawdown-floor guard (applies to BOTH paths).** `abs(candidate_mdd) > MAX_ABSOLUTE_DRAWDOWN_FLOOR` → reject regardless of Calmar.
4. **Path 1 — IMPROVE:** `candidate_calmar > incumbent_calmar` → tag `"performance"`. Reads neither of the new `DE-FR-SIMPLIFY-001` params — byte-unchanged by this cycle (AC-4).
5. **Path 2 — SIMPLIFY (`DE-FR-SIMPLIFY-001`, delta-scoped):** `candidate_calmar` is not worse than the incumbent's (either `>=`, or within `CALMAR_PRESERVE_TOLERANCE` relative tolerance) AND `_is_delta_scoped_material_simplification(overlay_node_count, replaced_cascade_node_count)` is `True` → tag `"simplification"`. Note: a candidate that is simpler AND has a *better* Calmar earns both tags — the phrasing "preserve within tolerance while materially simplifying" is a floor, not a ceiling; it does not require Calmar be exactly unchanged when it also happens to be better.
6. **Accepted iff `tags` is non-empty.**

**Example:**
```python
from advisors.frontrunner_acceptance import evaluate_calmar_acceptance

result = evaluate_calmar_acceptance(
    incumbent_metrics,   # {"annualized_return": 0.11, "max_drawdown": -0.14, "sharpe": 0.9, "volatility": 0.16}
    candidate_metrics,   # {"annualized_return": 0.13, "max_drawdown": -0.12, "sharpe": 1.0, "volatility": 0.15}
    incumbent_node_count=812,
    candidate_node_count=790,
    overlay_node_count=18,               # the small generated overlay
    replaced_cascade_node_count=140,      # the incumbent cascade subtree it replaces
)
if result.accepted:
    print("accepted:", result.tags, result.candidate_calmar, "vs", result.incumbent_calmar)
```

---

### `_is_delta_scoped_material_simplification(overlay_node_count, replaced_cascade_node_count) -> bool`

**New, `DE-FR-SIMPLIFY-001` (2026-08-24).** Private. The fail-closed SIMPLIFY-clause check `evaluate_calmar_acceptance` delegates to. Compares the candidate's OWN OVERLAY against the REPLACED CASCADE it swaps out — never the whole-symphony node counts, which stay ~98-100% of each other for any single-cascade splice (empirically measured across all 11 real trees, 2026-08-20) and can never signal a genuine simplification.

**Parameters:** `overlay_node_count`, `replaced_cascade_node_count` — untyped at the call boundary (accepts any value that may reach `_safe_float`); real callers pass `int | None`.

**Returns:** `bool`. Never raises.

**Declines (`False`) when:**
- either operand is `None` or non-numeric (`_safe_float` returns `None`),
- either operand is `<= 0` — a real compiled overlay/cascade always has ≥1 node, so `0` can only mean "count unavailable upstream," treated identically to `None`/absent rather than as a legitimately tiny value that would trivially satisfy the ratio,
- the overlay is literally bigger than the cascade it replaces.

**Otherwise:** `overlay <= cascade * MATERIAL_SIMPLIFICATION_MAX_RATIO`.

A caller omitting both operands (the legacy invocation shape — `evaluate_calmar_acceptance`'s two new params both default `None`) always declines here: SIMPLIFY becomes structurally unreachable for an un-migrated call site rather than silently keeping the old whole-tree comparison's (broken) behavior.

## SIMPLIFY-Path Delta-Scoping (`DE-FR-SIMPLIFY-001`, 2026-08-24)

**The defect.** `MATERIAL_SIMPLIFICATION_MAX_RATIO=0.50` demands the candidate be ≤50% of some reference node count. Pre-fix, that reference was `incumbent_node_count`/`candidate_node_count` — the WHOLE-SYMPHONY node counts. Since a Frontrunner Builder candidate is always a full standalone copy of the incumbent with exactly one detected cascade replaced by a generated overlay (`frontrunner_builder.splice_candidate_into_symphony`), `candidate_node_count` is always ~98-100% of `incumbent_node_count` for any real tree — the ratio could essentially never clear 0.50 in practice. Found by PR #126's independent `/code-review` pass (deferred then, see `DE-FR-PROPOSAL-IDENTITY-001`'s "Deferred — explicitly OUT of scope" section in `DECISIONS.md`), fixed in this cycle.

**The fix.** Re-scopes the comparison to the honest delta: the generated overlay's node count vs. the node count of the incumbent cascade it replaces — both already computable at the real call site (`frontrunner_builder._run_build_for_symphony`), since the builder holds the compiled overlay (`result.compiled_tree`/`result.candidate`) and the detected cascade (`cascade.overlay_tree`) in the same loop iteration. `[PM-ASSUMED]` design ruling (team-lead, undisputed): the delta-scoped ratio is the semantically correct reading of "materially simplifies" for a single-cascade splice — a whole-tree ratio structurally cannot express it, and the plan's own architecture doc names both operands as already in scope. The `0.50` constant value is unchanged — it was always calibrated against overlay-scale, never whole-symphony scale; the COMPARISON was the bug, not the threshold.

**Node-count decoupling (AC-6).** `incumbent_node_count`/`candidate_node_count` are retained on the function signature ONLY for the unrelated `node_count_delta` display metric on `AcceptanceResult` — that field's whole-tree semantics are byte-unchanged by this cycle. The SIMPLIFY clause never reads those two params anymore.

**Where the builder-side counts come from** — see `docs/generated/advisors_frontrunner_builder.md`'s `_count_overlay_node_count` entry for the overlay side (three-tier fallback: reuse an already-compiled tree, an already-step-shaped candidate, or a fresh pure-compile) and the `_run_build_for_symphony` note for the cascade side (`_count_tree_nodes(cascade.overlay_tree)`, direct — the detected cascade subtree is already raw_value-shaped, no compile needed).

## Internal Dependencies

- `dataclasses`, `logging` — stdlib only. No imports from `database`, `math_engine`, or any network/execution module. Pure math over caller-supplied dicts.

## Consumers

- `advisors/frontrunner_builder.py::_gate_and_accept_candidate` — calls `evaluate_calmar_acceptance` only for a candidate that has already survived `backtest_gate_engine.evaluate_candidate_batch`'s `ADOPT_CANDIDATE` verdict (AC-6 gate is necessary-but-not-sufficient; this module is the sufficiency check). A rejection here (`acceptance.accepted is False`) is persisted as an AC-11 rejected-observation with the incumbent-vs-candidate deltas. Since `DE-FR-SIMPLIFY-001`, this call site also threads `overlay_node_count`/`replaced_cascade_node_count` — see `docs/generated/advisors_frontrunner_builder.md`.

## Testing

`tests/advisors/test_frontrunner_acceptance.py` — 32 tests (independently re-collected and re-run at HEAD `62366d3c`, 2026-08-24: 17 pre-existing + 15 `DE-FR-SIMPLIFY-001` additions/updates), covering: Calmar math (CAGR/MaxDD, zero-drawdown `None` handling), the delta-scoped simplification-with-preserved-Calmar path, the golden-fixture ratio boundary (`tests/fixtures/math/frontrunner_simplify_ratio_boundary.json`, 7 rows), the AC-5 fail-closed guards (`None`/non-numeric/`<=0`/overlay-bigger-than-cascade, including the `overlay_node_count=0` edge case), a real `if_compound` overlay counted via the real counter, the drawdown-floor guard, Sharpe/volatility non-gating (reported but never decisive), tag correctness, and fail-closed behavior on missing/malformed metrics. `tests/advisors/test_frontrunner_simplify_path_wiring.py` (new, 5 tests, independently re-collected and re-run at the same HEAD) covers the builder-side integration: the real `_run_build_for_symphony` threads real overlay/cascade counts into this module's call, a reachability proof (a small overlay replacing a large cascade with preserved Calmar is ACCEPTED via SIMPLIFY — impossible pre-fix), and `_count_overlay_node_count`'s reuse-the-compiled-tree / fresh-compile-fallback / fallback-failure paths. Independently verified together with `tests/security/test_frontrunner_no_trade_boundary.py` (AC-7, zero diff to detector/splice/gate-engine/Calmar math): **47 passed, 0 failed** (`-n0`). A wider run of the full frontrunner test surface (`tests/advisors -k frontrunner` + the no-trade-boundary suite + the proposal-identity template suite, `-n0`): **350 passed, 1157 deselected, 1 xfailed** (the xfail is the same pre-existing, unrelated `test_real_looking_core_tickers_do_not_leak_into_watched_tickers` documented in `DE-FR-SIGNALS-001`) — zero new failures. Both ruff gates (`check` + `format --check`) independently re-verified clean on all 4 touched files.
