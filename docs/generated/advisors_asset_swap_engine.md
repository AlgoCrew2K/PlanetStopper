# advisors/asset_swap_engine

> Offline asset-swap proposal engine: objective-directed candidate generation, lens-informed ranking, BHY-FDR gating, and audit-trail persistence — advise-only, never executes.

**Source:** `advisors/asset_swap_engine.py`
**Last updated:** 2026-07-13 (advisor-remediation-r1 — reachability caveat added, DE-ADVISOR-R1-001)

## Overview

`asset_swap_engine.py` implements the two swap-proposal modes of the AI Advisor's M3 surface:

1. **Operator-initiated** (`propose_operator_swap`): the operator specifies an incumbent ticker and a candidate replacement for a named symphony. The engine backtests the variant, gates it through the BHY-FDR acceptance layer, and returns a `SwapRunResult`.

2. **Advisor-suggested** (`suggest_swaps`): given a swap objective and an available asset pool, the engine calls `generate_objective_directed_candidates` to shortlist candidates ranked by the stated objective, then backtests and gates the full batch together.

**Cycle-3 addition (lens-informed ranking):** `generate_objective_directed_candidates` now accepts an optional `lens_scores` dict. When provided, multi-lens evidence is blended into candidate ranking via `_apply_lens_blend`. Lens scoring influences ranking only — the BHY-FDR gate is unchanged. Both entry points (`propose_operator_swap`, `suggest_swaps`) accept `lens_scores` and `lens_sources` kwargs; the pre-Cycle-3 call paths remain byte-identical when `lens_scores=None`.

**Reachability caveat (advisor-intent audit, 2026-07-13 — two parts, both required for an honest picture; DE-ADVISOR-R1-001 §AC-15, F2/F4):**
(a) The operator-clicked evaluate route (`POST /ai-advisor/asset-swaps/evaluate`, `app.py:4240`→`4312`) never passes `lens_scores`/`lens_sources` to `propose_operator_swap` (both default `None`, `asset_swap_engine.py:987`) — on that surface, `_apply_lens_blend` is a permanent no-op and `lens_evidence` persists as `{}`. Zero lens influence on any operator-clicked swap. This is unaffected by the R1 remediation cycle — it is R2 (context-injection) scope, not an R1 acceptance criterion.
(b) Even where `lens_scores` IS wired (the weekly scheduler path only, via `weekly_suggestions_scheduler.py`), the blend reads a SINGLE lens (`technicals.momentum` only — sentiment/derivatives/macro are excluded as market-wide scalars, fundamentals excluded by design; see `extract_lens_scores` below), weighted `LENS_BLEND_WEIGHT=0.25`, and never affects the gate itself (ranking-influence only).

**Advisor-rewire cycle (2026-07-12, Workstream D):** the Cycle-3 blend formula was **mathematically inert in production** — see "Lens Blend — How Ranking Works" below for the closed-form proof and the fix. As of this cycle the blend genuinely reorders candidates, AND (Workstream C.2, `advisors/weekly_suggestions_scheduler.py`) the fixed math is reachable from the real weekly production path via a new `_fetch_lens_scores()` helper.

**Live-E2E follow-up (DE-LENS-SCORE-SHAPE-001, 2026-07-12):** even after the above, `extract_lens_scores` itself read a fabricated `payload["ticker_scores"]` key that NO real lens producer emits — 441 mocked tests stayed green because every fixture fabricated that shape, but a live droplet-DB E2E run against a real, fresh `MARKET_LENS_CACHE` row (all 5 lenses genuinely available) returned `{}`. The D-workstream lens-blend fix and its C.2 wiring were both mathematically/structurally correct but DEAD on real data until this fix. Rewritten to parse the REAL producer shapes — see "API Reference" below. This is a parser/fixture-provenance class of bug (fixtures encoded an assumption never verified against the actual producer), the exact failure mode the project's fixture-provenance hard rule exists to prevent — caught only because the E2E ran against real production data, not because any unit test caught it.

**Hard constraints:**
- This module MUST NOT be imported from `alpha_bot_execution.py` — it is an offline advise-only post-backtest layer.
- Only read + inline-backtest Composer endpoints are called (`GET /score`, stateless `POST /backtest`). No write, mutate, or trade-placement calls.
- Every evaluated proposal is persisted as an `advisor_observation` with `is_advisory_only=1` regardless of gate verdict (RC-4).
- Zero survivors is a valid non-error outcome.

## Constants

| Name | Value | Purpose |
|------|-------|---------|
| `LENS_BLEND_WEIGHT` | `0.25` | Weight for lens evidence in `_apply_lens_blend`'s cumulative-gap formula. Keeps lens signal as supporting evidence — the primary objective metric anchors ranking. |
| `SWAP_SURVIVOR_CAVEAT` | (from backtest_gate_engine) | Caveat attached to every ADOPT_CANDIDATE survivor. |
| `NO_SURVIVORS_MESSAGE` | `"no swap cleared the gate this run"` | Message in `SwapRunResult` when zero candidates pass the gate. |
| `_LENS_NEUTRAL_SCORE` | `0.5` | Neutral lens value: the deviation baseline in the blend formula AND the momentum-squash midpoint (`_squash_momentum_to_unit_interval(0.0) == 0.5`). |
| `_MOMENTUM_SQUASH_SCALE` | `0.10` | **New (DE-LENS-SCORE-SHAPE-001).** Scale constant in `_squash_momentum_to_unit_interval`'s `0.5 + 0.5*tanh(momentum/_MOMENTUM_SQUASH_SCALE)` transform. Not a pinned formula — chosen so a typical ~5% momentum lands at a clearly non-neutral, non-saturated ~0.73, and an extreme ~15% momentum approaches but never reaches the (0,1) bounds. |

**Removed (DE-LENS-SCORE-SHAPE-001):** `_LENS_CONTEXT_KEYS` (the 5-lens-key iteration tuple) — deleted as dead code once `extract_lens_scores` was rewritten to read only `technicals` (see below); no longer iterated anywhere.

## API Reference

### `extract_lens_scores(context: dict) → dict`

Extracts per-ticker lens scores from an assembled advisor context dict.

**Rewritten 2026-07-12 (DE-LENS-SCORE-SHAPE-001 — live-E2E-caught fix).** The prior implementation walked all 5 lens blocks looking for a `payload["ticker_scores"]` sub-dict — a key **no real lens producer ever emits** (0 real occurrences outside the stale fixture and this function itself). On a live droplet-DB E2E run against a real, fresh `MARKET_LENS_CACHE` row it returned `{}`, silently no-opping the entire lens-blend feature end-to-end even though the blend math (Workstream D) and its wiring (Workstream C.2) were both correct.

Real per-lens payload shapes, verified directly against the producers (not re-derived from a fixture):

| Lens | Real payload shape | Per-ticker signal? |
|------|---------------------|---------------------|
| `technicals` | `{"ma_posture": {ticker: {above_sma50, above_sma200}}, "breadth": float, "momentum": {ticker: float}}` (`ai_advisor.py:542-552`, `advisors/lens_technicals.py:265-272`) | YES — `momentum` is an unbounded raw 20-day return per ticker |
| `sentiment` | `{tone_score, corpus, events, article_count}` (`ai_advisor.py:673-684`) | No — market-wide scalar |
| `derivatives` | `{vix_level, vix_term_structure, risk_read, as_of_date}` | No — market-wide scalar |
| `macro` | `{"series": {series_id: {...}}}` | No — FRED-series-keyed, market-wide |
| `fundamentals` | `{"tickers": {ticker: key_facts_dict}, "coverage": {...}}` (`ai_advisor.py:1242-1253`) | Per-ticker-KEYED, but values are raw financials, not a clean scalar — excluded from v1 by design (a fundamentals-derived score is a distinct design problem, out of this parser's scope) |

**Only `technicals.payload["momentum"]` is used.** `ma_posture` (also per-ticker) exists but is NOT read — momentum alone is sufficient signal; folding `ma_posture` in is a documented future enhancement, not required for correctness. `sentiment`/`derivatives`/`macro`/`fundamentals` contribute NOTHING even when `available=True` — fabricating a per-ticker score from a market-wide scalar (or an unrelated raw-financials blob) would violate the honest-availability contract.

Each raw momentum value is squashed onto `(0.0, 1.0)` via `_squash_momentum_to_unit_interval` (see below) before being returned, because `_apply_lens_blend` expects an already-normalized `[0,1]` favorability with `0.5` as neutral, while real momentum is an unbounded return.

Only an `available=True` `technicals` block contributes; `available=False` is honored regardless of what the payload nominally contains (AC-6 honest-availability is checked BEFORE payload content).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `context` | `dict` | Dict returned by `ai_advisor.assemble_advisor_context`, or any dict with lens-block values keyed by lens name. Missing keys, None payload, and malformed blocks are handled gracefully. |

**Returns:** `{ticker: {"technicals": score_in_0_1}, ...}`. Returns `{}` when `technicals` is absent/unavailable/has no `momentum` data. Never raises.

---

### `_squash_momentum_to_unit_interval(momentum: float) → float` (internal helper, new 2026-07-12)

Maps an unbounded raw 20-day momentum return onto the open interval `(0.0, 1.0)` via `0.5 + 0.5 * math.tanh(momentum / _MOMENTUM_SQUASH_SCALE)`.

Required because `_apply_lens_blend` treats lens scores as an already-normalized favorability on `[0, 1]` with `_LENS_NEUTRAL_SCORE` (0.5) as neutral, but technicals' real per-ticker signal (`payload["momentum"]`) is an unbounded raw return, not pre-normalized. Satisfies four pinned invariant properties (the exact formula/constant is an implementation choice, not itself pinned): `momentum == 0.0` → exactly `0.5` (neutral, matches the "no evidence" default so a flat ticker never silently nudges the blend); `momentum > 0.0` → score `> 0.5`, strictly monotonic; `momentum < 0.0` → score `< 0.5`, strictly monotonic; any finite input → strictly within `(0.0, 1.0)`, never exactly 0 or 1.

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
| `available_assets` | `list` | Candidate pool. Since the advisor-rewire cycle's live production caller, this is the lens-covered universe built by `weekly_suggestions_scheduler._build_base_candidate_pool` — see `docs/generated/advisors_weekly_suggestions_scheduler.md`. The function itself accepts an open universe (no allowlist). |
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
| `lens_scores` | `dict \| None` | Per-ticker lens evidence from `extract_lens_scores`. **Not passed by the operator-clicked route today** (see "Reachability caveat" above) — always `None` in the only reachable production caller of this function. Enriches rationale (AC-5) and is written to the persisted observation (AC-4) when provided. Ranking unaffected in operator mode (operator chose the candidate). Default `None`. |
| `lens_sources` | `list \| None` | Citation dicts `{title, url, published, lens}` for news-backed evidence. Written to `raw_response.sources` in the persisted observation. Default `None`. |

**Returns:** `SwapRunResult` — always returned, never raises.

---

### `suggest_swaps(symphony_id, score_tree, objective, correlation_data, available_assets, *, incumbent_oos_alpha=None, default_oos_alpha=0.0, lens_scores=None, lens_sources=None) → SwapRunResult`

Evaluate advisor-suggested objective-directed swap candidates (AC-2.2). Generates candidates via `generate_objective_directed_candidates` (with lens blend when `lens_scores` is provided), backtests the full batch together for honest n_effective BHY-FDR gating, and returns survivors. An absent Composer API key returns `no_api_key=True` and writes nothing (AC-X4). Never raises.

**Live production caller (advisor-rewire cycle, Workstream C.2):** `advisors.weekly_suggestions_scheduler.run_weekly_asset_swap_suggestions()` calls this once per live symphony, weekly, passing a real `lens_scores` dict sourced from the nightly `MARKET_LENS_CACHE` row via `_fetch_lens_scores()` (now genuinely non-empty on real data — DE-LENS-SCORE-SHAPE-001) and a candidate pool sourced from the lens-covered universe (DE-LENS-CANDIDATE-POOL-001 — see `docs/generated/advisors_weekly_suggestions_scheduler.md`). This is the first production caller this function has ever had.

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
    measured_value: float  # Corrected 2026-07-13 (AC-10, df4e1eee + 7420b33f,
                            # advisor-intent audit F7). Display-only; does NOT
                            # influence candidate generation, ranking, or gate
                            # decisions. AC-10's shipped remediation REMOVED
                            # the "measured X" phrase from _build_objective_
                            # rationale's output entirely (not merely marked
                            # display-only) -- the field is no longer rendered
                            # anywhere in this engine's rationale text. Every
                            # current production caller (app.py:4308) passes
                            # 0.0 -- never a live measurement. See docs/
                            # generated/advisors_logic_change_engine.md for
                            # the identical pattern + a residual gap this
                            # doc-writer found (logic_change_engine.py's
                            # generate_objective_directed_logic_candidates
                            # still fabricates the phrase; asset_swap_engine.py
                            # has no equivalent second fabrication site).
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

## Lens Blend — How Ranking Works (fixed 2026-07-12, DE-ADVISOR-REWIRE-D)

**Prior design (Cycle-3, position-based — now REPLACED, was mathematically inert):**

```
blended_key[i] = position[i] - LENS_BLEND_WEIGHT * mean_lens_score[i]
```

This never worked: for any two adjacent 0-based `position` values the integer gap is always `>= 1`, and `LENS_BLEND_WEIGHT = 0.25 < 1`, so the maximum possible lens contribution could never exceed the minimum possible position gap. Lens evidence could not change the order for **any** input — a closed-form proof of this inertness lives in `tests/ai_advisor/test_lens_blend_efficacy.py`'s module docstring. This was live and shipped for the entire Cycle-3 cycle without being caught, because the existing `test_lens_scores_reranks_candidates` test never actually asserted a reorder occurred.

**Current design (cumulative absolute score-distance):**

```
cum_gap[0] = 0
cum_gap[i] = cum_gap[i-1] + |score[i] - score[i-1]|     (walked in the caller's
                                                           own pre-sorted order,
                                                           index 0 = best)
blended_key[i] = cum_gap[i] - LENS_BLEND_WEIGHT * (mean_lens[i] - _LENS_NEUTRAL_SCORE)
```

- `score[i]` is each candidate's CONTINUOUS primary `"score"` field (already present on every candidate dict from `generate_objective_directed_candidates`) — never the discrete `enumerate()` position.
- `cum_gap` accumulates the RAW absolute distance between neighbours, deliberately NOT a per-batch min-max normalization. Min-max would always rescale a 2-candidate gap to fill `[0, 1]` regardless of true magnitude (a 0.0001 gap and a 0.90 gap would look identical after min-max) — that would defeat the "small gap can move, large gap cannot invert" invariant. Raw absolute-gap accumulation preserves magnitude information.
- A near-tied primary pair (small `cum_gap` increment) sits within `LENS_BLEND_WEIGHT`'s max possible swing and CAN be reordered by strong lens evidence.
- A commanding primary lead (large `cum_gap` increment) CANNOT be overcome by any lens evidence, no matter how extreme — lens evidence is supporting, never overriding.
- Tickers absent from `lens_scores` fall back to `_LENS_NEUTRAL_SCORE` (0.5) via `_primary_score`'s missing/non-numeric-score guard — no penalty, no bonus.
- Ties in `blended_key` break on original index (stable sort) for determinism.
- The blend does NOT eliminate candidates. Only the BHY-FDR gate eliminates candidates.

**Gate order-independence (AC-D3, `advisors/backtest_gate_engine.py`):** fixing the blend surfaced a second, pre-existing bug — `evaluate_candidate_batch` seeded its Sortino bootstrap with `seed=idx` (the candidate's list position), so re-sorting the SAME candidate set into a different submission order produced a different bootstrap seed per candidate, hence a different t-stat/p-value for the identical candidate. This violated the "gate output is unchanged for a fixed candidate set" invariant the new blend now actually exercises (a reordering blend needs the gate to be truly order-independent downstream). Fixed by seeding from a stable SHA-256 hash of the candidate's own `candidate_id` instead of its batch position — see `docs/generated/advisors_backtest_gate_engine.md`.

**Full end-to-end reachability chain (as of DE-LENS-CANDIDATE-POOL-001, closing the last E2E-caught gap):** `_build_base_candidate_pool` (lens-covered universe) → `_fetch_lens_scores` (real technicals momentum, correctly parsed) → `extract_lens_scores`/`_squash_momentum_to_unit_interval` → `generate_objective_directed_candidates`/`_apply_lens_blend` (reordering formula) → `evaluate_candidate_batch` (order-independent gate) → `insert_advisor_observation` (persisted `lens_evidence`). Every link in this chain was independently correct at some point in the cycle but the chain as a whole was proven end-to-end non-empty ONLY by a live droplet-DB E2E test — see `docs/generated/advisors_weekly_suggestions_scheduler.md`. **This chain is exclusively the weekly-scheduler path — see the Reachability caveat above for the operator-route gap.**

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
- `advisors.weekly_suggestions_scheduler` — the sole live production caller of `suggest_swaps` (Workstream C.2), including the `_fetch_lens_scores()` and `_build_base_candidate_pool()` wiring that makes this module's lens blend reachable AND non-empty on real data
- `math` — stdlib, `_squash_momentum_to_unit_interval` (`math.tanh`, new DE-LENS-SCORE-SHAPE-001)
