# advisors/asset_swap_engine

> Offline asset-swap proposal engine: objective-directed candidate generation, lens-informed ranking, BHY-FDR gating, and audit-trail persistence — advise-only, never executes.

**Source:** `advisors/asset_swap_engine.py`
**Last updated:** 2026-06-13

## Overview

`asset_swap_engine.py` implements the two swap-proposal modes of the AI Advisor's M3 surface:

1. **Operator-initiated** (`propose_operator_swap`): the operator specifies an incumbent ticker and a candidate replacement for a named symphony. The engine backtests the variant, gates it through the BHY-FDR acceptance layer, and returns a `SwapRunResult`.

2. **Advisor-suggested** (`suggest_swaps`): given a swap objective and an available asset pool, the engine calls `generate_objective_directed_candidates` to shortlist candidates ranked by the stated objective, then backtests and gates the full batch together.

**Cycle-3 addition (lens-informed ranking):** `generate_objective_directed_candidates` now accepts an optional `lens_scores` dict. When provided, multi-lens evidence (technicals, sentiment, derivatives, macro, fundamentals) is blended into candidate ranking via `_apply_lens_blend`. Lens scoring influences ranking only — the BHY-FDR gate is unchanged. Both entry points (`propose_operator_swap`, `suggest_swaps`) accept `lens_scores` and `lens_sources` kwargs; the pre-Cycle-3 call paths remain byte-identical when `lens_scores=None`.

**Hard constraints:**
- This module MUST NOT be imported from `alpha_bot_execution.py` — it is an offline advise-only post-backtest layer.
- Only read + inline-backtest Composer endpoints are called (`GET /score`, stateless `POST /backtest`). No write, mutate, or trade-placement calls.
- Every evaluated proposal is persisted as an `advisor_observation` with `is_advisory_only=1` regardless of gate verdict (RC-4).
- Zero survivors is a valid non-error outcome.

## Constants

| Name | Value | Purpose |
|------|-------|---------|
| `LENS_BLEND_WEIGHT` | `0.25` | Additive weight for lens evidence in `_apply_lens_blend`. Keeps lens signal as supporting evidence — the primary objective metric anchors ranking. |
| `SWAP_SURVIVOR_CAVEAT` | (from backtest_gate_engine) | Caveat attached to every ADOPT_CANDIDATE survivor. |
| `NO_SURVIVORS_MESSAGE` | `"no swap cleared the gate this run"` | Message in `SwapRunResult` when zero candidates pass the gate. |
| `_LENS_CONTEXT_KEYS` | `("technicals", "sentiment", "derivatives", "macro", "fundamentals")` | Ordered tuple of lens block keys expected in the assembled advisor context. |

## API Reference

### `extract_lens_scores(context: dict) → dict`

Extracts per-ticker lens scores from an assembled advisor context dict.

Walks the 5 standard lens blocks. Only `available=True` lenses contribute scores — `available=False` blocks are skipped entirely (honest-availability contract, AC-6). A lens contributes ticker scores when its `payload` dict contains a `"ticker_scores"` sub-dict mapping `ticker → float`. Lenses whose payload lacks `ticker_scores` (e.g. sentiment blocks carrying only article counts) are skipped without error.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `context` | `dict` | Dict returned by `ai_advisor.assemble_advisor_context`, or any dict with lens-block values keyed by lens name. Missing keys, None payload, and malformed blocks are handled gracefully. |

**Returns:** `{ticker: {lens_name: score, ...}, ...}`. Returns `{}` when no available lens carries per-ticker scores. Never raises.

---

### `generate_objective_directed_candidates(symphony_id, objective, correlation_data, available_assets, lens_scores=None) → list`

Generate a shortlist of swap candidates ranked by the stated objective, with optional lens-informed re-ranking.

Candidate-generation strategy by `objective.objective_type`:

- `reduce_correlation`: ranks by ascending absolute Pearson correlation vs the first element of `objective.target_pair` in `correlation_data`. Assets with no data receive a neutral score of 0.5.
- `reduce_drawdown`: ranks by ascending return-series variance (proxy for drawdown risk). Assets with no data ranked last.
- `lift_risk_adjusted`: ranks by descending pseudo-Sharpe (mean/std from return series). Assets with insufficient data score 0.0.
- Unknown objective: returns all `available_assets` in original order.

After the primary sort, `_apply_lens_blend` is called with `lens_scores`. When `lens_scores` is `None` or empty, the function is byte-identical to the pre-Cycle-3 implementation.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | Composer symphony UUID. |
| `objective` | `SwapObjective` | The objective driving candidate generation. |
| `correlation_data` | `dict` | `{entity_id: [float]}` return series for ranking. |
| `available_assets` | `list` | Candidate pool. Open universe — no allowlist. |
| `lens_scores` | `dict \| None` | Optional per-ticker lens evidence from `extract_lens_scores`. When provided, mean lens score is blended into post-primary-sort ranking. Lens scoring influences ranking only — never bypasses the gate. Default `None`. |

**Returns:** Ordered list of `{"ticker": ..., "score": ...}` dicts, top-ranked first. Never plain strings.

---

### `propose_operator_swap(symphony_id, score_tree, incumbent_asset, candidate_asset, objective, *, incumbent_oos_alpha=None, default_oos_alpha=0.0, lens_scores=None, lens_sources=None) → SwapRunResult`

Evaluate one operator-specified asset swap (AC-2.1). Backtests the variant, gates via `evaluate_candidate_batch`, persists the observation regardless of verdict (RC-4), and returns a `SwapRunResult`. Never raises.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | Composer symphony UUID. |
| `score_tree` | `dict` | Raw Composer score tree from `GET /api/v0.1/symphonies/{id}/score`. |
| `incumbent_asset` | `str` | Ticker to replace. |
| `candidate_asset` | `str` | Replacement ticker (open universe). |
| `objective` | `SwapObjective` | Drives the swap and surfaces alongside the result (AC-2.3). |
| `incumbent_oos_alpha` | `float \| None` | Live incumbent OOS alpha. `None` → computed from fold-matched baseline backtest. Explicit `0.0` is respected (H5). |
| `default_oos_alpha` | `float` | Global-default params OOS alpha. |
| `lens_scores` | `dict \| None` | Per-ticker lens evidence from `extract_lens_scores`. Enriches rationale (AC-5) and is written to the persisted observation (AC-4). Ranking unaffected in operator mode (operator chose the candidate). Default `None`. |
| `lens_sources` | `list \| None` | Citation dicts `{title, url, published, lens}` for news-backed evidence. Written to `raw_response.sources` in the persisted observation. Default `None`. |

**Returns:** `SwapRunResult` — always returned, never raises.

---

### `suggest_swaps(symphony_id, score_tree, objective, correlation_data, available_assets, *, incumbent_oos_alpha=None, default_oos_alpha=0.0, lens_scores=None, lens_sources=None) → SwapRunResult`

Evaluate advisor-suggested objective-directed swap candidates (AC-2.2). Generates candidates via `generate_objective_directed_candidates` (with lens blend when `lens_scores` is provided), backtests the full batch together for honest n_effective BHY-FDR gating, and returns survivors. An absent Composer API key returns `no_api_key=True` and writes nothing (AC-X4). Never raises.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | Composer symphony UUID. |
| `score_tree` | `dict` | Raw Composer score tree. |
| `objective` | `SwapObjective` | Drives candidate generation. |
| `correlation_data` | `dict` | `{entity_id: [float]}` return series for objective-directed ranking. |
| `available_assets` | `list` | Candidate pool. Open universe. |
| `incumbent_oos_alpha` | `float \| None` | Incumbent OOS alpha for gate comparison. |
| `default_oos_alpha` | `float` | Global-default params OOS alpha. |
| `lens_scores` | `dict \| None` | Per-ticker lens evidence. Blended into ranking (AC-2) and written to persistence (AC-4). `None` → byte-identical pre-Cycle-3 behaviour. |
| `lens_sources` | `list \| None` | Citation dicts for persistence. |

**Returns:** `SwapRunResult` — always returned, never raises. Zero survivors is a valid outcome (AC-2.5).

---

## Types

### `SwapObjective`

```python
@dataclass
class SwapObjective:
    objective_type: str   # "reduce_correlation" | "reduce_drawdown" | "lift_risk_adjusted"
    target_pair: tuple[str, str] | None  # Symphony IDs/tickers for correlation objectives
    measured_value: float  # Measured input driving this objective — never hardcoded wisdom
```

### `SwapProposalResult`

Per-candidate result. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | `"{symphony_id}:{incumbent}->{candidate}"` |
| `objective_rationale` | `str` | Why this candidate addresses the objective. Includes lens evidence summary when `lens_scores` is provided (Cycle-3 AC-5). |
| `gate_result` | `CandidateGateResult \| None` | Gate verdict, `validation_days`, `oos_alpha`, `caveats`. `None` on backtest failure. |
| `caveats` | `list` | Propagated from `gate_result.caveats` — always non-empty for ADOPT_CANDIDATE survivors (AC-3.3). |
| `apply_guidance` | `str` | "To apply: open {symphony_name} in Composer and swap {from} → {to} manually." Always present, never a button (AC-X1). |
| `backtest_error` | `str \| None` | Descriptive string on backtest failure; `None` on success. |

### `SwapRunResult`

Top-level result of a swap pipeline run.

| Field | Type | Description |
|-------|------|-------------|
| `gate_batch` | `GatedBatch` | Full BHY-FDR batch result (always non-None, even on zero candidates). |
| `survivors` | `list` | Proposals where `gate_result.verdict.decision == "ADOPT_CANDIDATE"`. |
| `rejected_candidates` | `list` | Gated-out or backtest-failed proposals. |
| `message` | `str` | Run summary. `NO_SURVIVORS_MESSAGE` when zero survive. |
| `no_api_key` | `bool` | `True` when Composer credentials are absent. |
| `persistence_error` | `str \| None` | Non-None when the `advisor_observation` write failed (RC-5). The survivor is still returned. |

## Lens Blend — How Ranking Works (Cycle-3)

The lens blend is additive and position-based, applied after the primary objective sort:

```
blended_key[i] = position[i] - LENS_BLEND_WEIGHT * mean_lens_score[i]
```

- `position` is the 0-based rank from the primary objective sort.
- `mean_lens_score` is the mean of all available lens scores for the ticker, in `[0, 1]`.
- Tickers absent from `lens_scores` receive a neutral `0.5` (no data → no penalty, no bonus).
- Subtracting the lens contribution moves high-lens-score tickers toward position 0 regardless of whether the primary sort is ascending or descending.
- `LENS_BLEND_WEIGHT = 0.25` keeps the lens as supporting evidence — the primary objective metric anchors the ranking.
- The blend does NOT eliminate candidates. Only the BHY-FDR gate eliminates candidates.

## Persistence (AC-4)

Every evaluated proposal is persisted via `database.insert_advisor_observation` with:

- `advisor_role="ASSET_SWAP"`
- `is_advisory_only=1`
- `observation_type="asset_swap_proposal"`
- `verdict`: the actual gate decision (ADOPT_CANDIDATE / KEEP_INCUMBENT / REJECT_VETO_FAILED)
- `raw_response`: includes `lens_evidence` (`{ticker: {signal, source_lens, confidence}}`) and `sources` (citation dicts) when `lens_scores` / `lens_sources` are provided.

Persistence is verdict-agnostic (RC-4) — the operator sees the engine ran even on KEEP_INCUMBENT outcomes. A persistence failure is surfaced in `SwapRunResult.persistence_error` and never swallowed (RC-5).

## Internal Dependencies

- `advisors.backtest_gate_engine` — `evaluate_candidate_batch`, `BacktestCandidate`, `GatedBatch`, `CandidateGateResult`, `_fold_transform_single`, `HARVEY_LIU_FDR_Q`
- `advisors.composer_backtest_client` — `run_backtest`
- `database` — `insert_advisor_observation`
- `ai_advisor` — `assemble_advisor_context` (callers pass the assembled context; `extract_lens_scores` consumes it)
