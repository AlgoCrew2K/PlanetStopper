# advisors/frontrunner_acceptance

> Calmar acceptance gate for the Frontrunner Builder — a candidate is admitted only if it improves risk-adjusted return or preserves it while materially simplifying the tree.

**Source:** `advisors/frontrunner_acceptance.py`
**Last updated:** 2026-07-11 (Wave-1 backend, frreview-APPROVED, unchanged by the subsequent P2-1/P2-2 hardening — those touched `frontrunner_detector.py`/`frontrunner_builder.py` only)

## Overview

`advisors/frontrunner_acceptance.py` implements AC-7 of the Frontrunner Builder plan: the final acceptance decision applied to a candidate that has already survived the shared overfitting guardrail (`backtest_gate_engine.evaluate_candidate_batch`). Gate survival is **necessary but not sufficient** — a candidate must also demonstrably improve the strategy, or simplify it without cost.

Acceptance is **two independent paths**, either of which admits a candidate:

1. **IMPROVE** — the candidate's Calmar ratio (`CAGR / |max_drawdown|`) is strictly higher than the incumbent's, AND the candidate's own absolute drawdown does not breach a floor (a candidate cannot buy an improved ratio via extreme leverage that also posts a catastrophic drawdown).
2. **PRESERVE + SIMPLIFY** — the candidate's Calmar is within tolerance of the incumbent's (neither meaningfully better nor worse) AND the candidate tree is materially smaller (a genuine any/all collapse), same drawdown-floor guard applies.

**Sharpe and volatility are always reported** on the result (for the Advisor-tab card, once built) but **never gate acceptance either way** — a terrible Sharpe cannot sink an otherwise-accepted candidate, and a great Sharpe cannot rescue an otherwise-rejected one (AC-7 explicit).

This module never trusts an incoming pre-computed Calmar figure — it derives Calmar itself from the caller-supplied CAGR + max_drawdown metrics dict fields (mirroring the project-wide "never trust incoming oos_metrics" posture used by `build_plan_generator`/`strategy_builder_engine`). Fails **closed** (rejects) on any missing/None metric or malformed input — never raises, never fabricates an accept on incomplete data.

Off-execution-path. Pure function, no I/O, no network, no DB access.

## Named Constants

| Name | Value | Rationale |
|------|-------|-----------|
| `CALMAR_PRESERVE_TOLERANCE` | `0.02` (2% relative) | Tight enough that a genuine performance improvement is never miscategorized as "merely preserved", while tolerating floating-point noise between two independent re-backtests of structurally-identical trees |
| `MATERIAL_SIMPLIFICATION_MAX_RATIO` | `0.50` | A candidate qualifies as "materially simpler" at ≤50% of the incumbent's node count — deliberately a large threshold, since the grounding note describes collapsing "hundreds of flat RSI-gt rungs" via any/all (an order-of-magnitude reduction, not a marginal trim) |
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

### `evaluate_calmar_acceptance(incumbent_metrics: dict, candidate_metrics: dict, *, incumbent_node_count: int, candidate_node_count: int) -> AcceptanceResult`

The AC-7 acceptance gate.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `incumbent_metrics`, `candidate_metrics` | `dict` | Shaped like `analytics.compute_quantstats_metrics`'s output: `annualized_return` (CAGR), `max_drawdown` (<= 0), optionally `sharpe`/`volatility` |
| `incumbent_node_count`, `candidate_node_count` | `int` | Total node counts of the incumbent and candidate symphony trees — the AC-7 "materially simplifying" signal |

**Returns:** `AcceptanceResult`. Never raises (D-1).

**Decision logic (in order):**

1. **Fail-closed on missing data.** Any of `incumbent_cagr`/`incumbent_mdd`/`candidate_cagr`/`candidate_mdd` being `None`/non-numeric → immediate reject (no accept on incomplete data).
2. **Fail-closed on undefined Calmar.** Either side's `compute_calmar` returning `None` (zero drawdown) → reject.
3. **Drawdown-floor guard (applies to BOTH paths).** `abs(candidate_mdd) > MAX_ABSOLUTE_DRAWDOWN_FLOOR` → reject regardless of Calmar.
4. **Path 1 — IMPROVE:** `candidate_calmar > incumbent_calmar` → tag `"performance"`.
5. **Path 2 — SIMPLIFY:** `candidate_calmar` is not worse than the incumbent's (either `>=`, or within `CALMAR_PRESERVE_TOLERANCE` relative tolerance) AND `candidate_node_count <= incumbent_node_count * MATERIAL_SIMPLIFICATION_MAX_RATIO` → tag `"simplification"`. Note: a candidate that is simpler AND has a *better* Calmar earns both tags — the phrasing "preserve within tolerance while materially simplifying" is a floor, not a ceiling; it does not require Calmar be exactly unchanged when it also happens to be better.
6. **Accepted iff `tags` is non-empty.**

**Example:**
```python
from advisors.frontrunner_acceptance import evaluate_calmar_acceptance

result = evaluate_calmar_acceptance(
    incumbent_metrics,   # {"annualized_return": 0.11, "max_drawdown": -0.14, "sharpe": 0.9, "volatility": 0.16}
    candidate_metrics,   # {"annualized_return": 0.13, "max_drawdown": -0.12, "sharpe": 1.0, "volatility": 0.15}
    incumbent_node_count=812,
    candidate_node_count=790,
)
if result.accepted:
    print("accepted:", result.tags, result.candidate_calmar, "vs", result.incumbent_calmar)
```

## Internal Dependencies

- `dataclasses`, `logging` — stdlib only. No imports from `database`, `math_engine`, or any network/execution module. Pure math over caller-supplied dicts.

## Consumers

- `advisors/frontrunner_builder.py::_gate_and_accept_candidate` — calls `evaluate_calmar_acceptance` only for a candidate that has already survived `backtest_gate_engine.evaluate_candidate_batch`'s `ADOPT_CANDIDATE` verdict (AC-6 gate is necessary-but-not-sufficient; this module is the sufficiency check). A rejection here (`acceptance.accepted is False`) is persisted as an AC-11 rejected-observation with the incumbent-vs-candidate deltas.

## Testing

`tests/advisors/test_frontrunner_acceptance.py` — 17 tests, covering: Calmar math (CAGR/MaxDD, zero-drawdown `None` handling), the simplification-with-preserved-Calmar path, the drawdown-floor guard, Sharpe/volatility non-gating (reported but never decisive), tag correctness, and fail-closed behavior on missing/malformed metrics.
