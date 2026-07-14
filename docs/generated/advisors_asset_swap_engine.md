# advisors/asset_swap_engine

> Offline asset-swap proposal engine: objective-directed candidate generation, lens-informed rationale/persistence, BHY-FDR gating, and audit-trail persistence — advise-only, never executes. **R2-3 (2026-07-14):** the candidate generator is now LLM-reasoned, not a fixed statistical sort.

**Source:** `advisors/asset_swap_engine.py`
**Last updated:** 2026-07-14 (R2-3, `DE-ADVISOR-R2-3-001` — LLM-reasoned swap-pair generator replaces the deleted fixed-statistical-sort generator; `validate_tree` guard; `run_id`/`provenance` contract; closes the R2 program 3-of-3)

## Overview

`asset_swap_engine.py` implements the two swap-proposal modes of the AI Advisor's M3 surface:

1. **Operator-initiated** (`propose_operator_swap`): **R2-3 — now TWO modes**, branched on whether both `incumbent_asset`/`candidate_asset` are supplied. EXPLICIT-PAIR (both supplied): the operator's exact pair is applied, backtested, and gated — byte-preserved pre-R2-3 behavior (AC-12). REASONED (either/both omitted): the LLM-reasoned generator proposes the pair over the operator's real holdings + the real tradeable universe, then evaluates it exactly like the explicit-pair path once resolved.

2. **Advisor-suggested** (`suggest_swaps`): given a swap objective, the engine calls `generate_reasoned_swap_candidates` (R2-3 — replaces the deleted `generate_objective_directed_candidates`) to produce a bounded set of OBJECTIVE-DIRECTED (incumbent, candidate) pairs, then backtests all of them and gates the FULL batch together (AC-3.2/R2-3 AC-4 — never per-candidate).

**R2-3 — LLM-reasoned generation + provenance (`DE-ADVISOR-R2-3-001`, 2026-07-14), the module's headline change this cycle, mirrors R2-2's shape on `logic_change_engine.py` where the modes align:** prior to R2-3, `suggest_swaps`' candidates were produced by `generate_objective_directed_candidates` — a fixed statistical sort (`reduce_correlation` → ascending abs Pearson vs `correlation_data`; `reduce_drawdown` → ascending return-series variance; `lift_risk_adjusted` → descending pseudo-Sharpe; unknown → unchanged), with an incumbent chosen by a separate deterministic `_select_incumbent_asset` helper. Neither reasoned about the operator's actual tree, live stats, or market context — the Asset Swaps tab was honestly labelled "Deterministic — no AI reasoning" for exactly this reason. **`generate_objective_directed_candidates` and `_select_incumbent_asset` are both DELETED in R2-3** (Q4/Q2 — verified by grep against the final `asset_swap_engine.py`: neither name exists as a definition anywhere in the file), replaced by a single new generator:

- **`generate_reasoned_swap_candidates`** (see API Reference below) makes a real Anthropic tool-use call: the LLM is shown the objective, an optional operator-context block, optional correlation-data evidence, and a bounded sample of the real tradeable universe, and proposes (incumbent, candidate) PAIRS via an `emit_swap_candidates` tool schema — a genuine change in kind from R2-2's single-value edits, since choosing which held ticker to swap OUT is itself a reasoning act (Q2). This is the reasoned path's only candidate source now — there is no deterministic fallback generator left in the module.
- **Both operator modes AND the advisor-suggested mode route through the SAME generator** (Q1) — a divergence from R2-2, where only `suggest_logic_changes`'s advisor-suggested path used the new generator directly and the operator path used it as a bounded single-candidate steering-hint resolver. R2-3's `propose_operator_swap` REASONED branch calls `generate_reasoned_swap_candidates(..., max_candidates=1)` and hands the resolved pair to the SAME `_evaluate_explicit_pair` helper (new, R2-3) that the EXPLICIT-PAIR branch also calls — one shared evaluation core for both modes.
- **The `evidence_injected` manifest — R2's thesis, reused not re-derived**, same framing as `DE-ADVISOR-R2-1-001`/`DE-ADVISOR-R2-2-001`: when the caller (the route) supplies `reasoning_context`/`reasoning_manifest` from `ai_advisor.build_reasoning_context`, the EXACT SAME per-source honesty manifest that gated what was injected into the LLM prompt is the exact same dict surfaced as `provenance["evidence_injected"]` and persisted on every observation this run writes. Omitted (the weekly scheduler's call site, which does not build reasoning context) → `ai_advisor._EMPTY_MANIFEST` (all 7 keys `"absent"`), never a fabricated placeholder.
- **`validate_tree` guard — net-new safety over `apply_ticker_swap`, mirrors R2-2's identical guard placement.** `apply_ticker_swap` only substitutes a ticker STRING — a structurally valid input tree stays valid, but the guard is added for the same defense-in-depth reasoning R2-2 established: the generator's trust model changed from a fixed sort to a less-trusted LLM. `_evaluate_single_variant` now calls `advisors.symphony_schema.validate_tree` on the swapped tree immediately after `apply_ticker_swap`, BEFORE any backtest call (including the baseline) — a tree that fails is dropped with an honest, distinctly-worded reason, never fabricated, never backtested. Composer `/backtest` remains the real tradeability arbiter; this is cheap insurance ahead of any spend.
- **`run_id`/`provenance` — the SAME 4-key cross-cutting contract `DE-ADVISOR-R2-1-001` established and `DE-ADVISOR-R2-2-001` confirmed**, minted unconditionally at the top of both `propose_operator_swap` and `suggest_swaps` so every return path — including the earliest error returns — carries it. `run_id` is additionally threaded into every `_persist_observation` call this run makes.
- **Candidate universe validated, never trusted — Q3.** Each proposed `candidate_asset` is checked against the real tradeable set (`advisors.universe_provider.get_tradeable_set()`, or a caller-supplied `tradeable_universe` override) intersected with any caller-supplied `available_assets`; the full ~12.7k-symbol set is never injected into the prompt (bounded to `_MAX_ASSETS_LISTED_IN_PROMPT=40`). Each proposed `incumbent_asset` is checked against the REAL tree (`extract_tickers`) — an LLM claim that doesn't resolve to either is silently DROPPED, never fabricated into a `SwapCandidate`.
- **`correlation_data` retained as prompt EVIDENCE, no longer drives a ranking — Q4.** Its entity keys are surfaced to the LLM as a sorted list ("entities with return-series data available"); the LLM is never shown the raw series and the values never programmatically sort anything anymore.
- Every reuse point is verbatim, byte-unchanged this cycle: `ai_advisor.py`, `advisors/symphony_schema.py`, and `advisors/backtest_gate_engine.py` carry ZERO diff in R2-3 (confirmed via `git diff fe3d9754..248469a5`) — the module calls into them, it does not modify them. `advisors/weekly_suggestions_scheduler.py` also carries ZERO diff (its call site is unaffected — see the Lens Blend section below for what that means for its `_apply_lens_blend`-reachability claims, which this cycle's engine change breaks even though the scheduler file itself never changed).

**R2-3 closes the R2 program (3 of 3).** R2-1 (Strategy Builder, `DE-ADVISOR-R2-1-001`) established `ai_advisor.build_reasoning_context` + the 4-key provenance contract → R2-2 (Logic Changes, `DE-ADVISOR-R2-2-001`) confirmed it as genuinely cross-cutting → **R2-3 (this entry)** is the third confirmation, on a module whose shape (two operator modes, a shared evaluation core, an already-existing lens-evidence side-channel) differs the most from the other two — and the contract still ported verbatim.

**Advisor-rewire cycle (2026-07-12, Workstream D): first production caller history — superseded by R2-3.** The Cycle-3 blend formula was fixed (mathematically inert → genuinely reordering, see "Lens Blend" below) and wired to the real weekly production path via `weekly_suggestions_scheduler.py`'s `_fetch_lens_scores()`. **As of R2-3, this reachability chain is HISTORICAL, not current** — see the superseded banner in "Lens Blend — How Ranking Works" below.

**Live-E2E follow-up (DE-LENS-SCORE-SHAPE-001, 2026-07-12):** `extract_lens_scores` was rewritten to parse the REAL producer shapes (`technicals.payload["momentum"]`) rather than a fabricated `payload["ticker_scores"]` key no real lens producer ever emits. **Unaffected by R2-3** — `extract_lens_scores` is unchanged this cycle and still feeds the rationale/persistence side-channel described below.

**Hard constraints:**
- This module MUST NOT be imported from `alpha_bot_execution.py` — it is an offline advise-only post-backtest layer.
- Only read + inline-backtest Composer endpoints are called (`GET /score`, stateless `POST /backtest`). No write, mutate, or trade-placement calls.
- Every evaluated proposal is persisted as an `advisor_observation` with `is_advisory_only=1` regardless of gate verdict (RC-4).
- Zero survivors is a valid non-error outcome.
- **R2-3:** the `anthropic` SDK import stays lazy inside `_build_client` (CC-2, off-execution-path); `generate_reasoned_swap_candidates` never raises (D-1) — LLM outage/malformed output degrades to `[]`, never a silent fallback to the deleted deterministic sort.

## Constants

| Name | Value | Purpose |
|------|-------|---------|
| `LENS_BLEND_WEIGHT` | `0.25` | Weight for lens evidence in `_apply_lens_blend`'s cumulative-gap formula. **R2-3: this function has no production call site** (candidate selection is now the LLM's, Q4) — preserved byte-unchanged (AC-12) with its own dedicated test coverage only. |
| `SWAP_SURVIVOR_CAVEAT` | (from backtest_gate_engine) | Caveat attached to every ADOPT_CANDIDATE survivor. |
| `NO_SURVIVORS_MESSAGE` | `"no swap cleared the gate this run"` | Message in `SwapRunResult` when zero candidates pass the gate. |
| `_LENS_NEUTRAL_SCORE` | `0.5` | Neutral lens value: the deviation baseline in the (now call-site-orphaned) blend formula AND the momentum-squash midpoint (`_squash_momentum_to_unit_interval(0.0) == 0.5`) — the latter is still live, feeding the rationale/persistence side-channel. |
| `_MOMENTUM_SQUASH_SCALE` | `0.10` | Scale constant in `_squash_momentum_to_unit_interval`'s `0.5 + 0.5*tanh(momentum/_MOMENTUM_SQUASH_SCALE)` transform. Unaffected by R2-3. |
| `MAX_SUGGESTED_CANDIDATES` | `30` | **New (R2-3), mirrors `logic_change_engine.MAX_SUGGESTED_CANDIDATES`.** Upper bound on advisor-suggested candidates per `suggest_swaps` run — keeps the FDR correction effective; also the default `max_candidates=` bound on `generate_reasoned_swap_candidates`. |
| `_MAX_ASSETS_LISTED_IN_PROMPT` | `40` | **New (R2-3).** Bounds the candidate-universe listing rendered into the generation prompt regardless of the real tradeable set's size (~12.7k symbols, Q3) — the LLM proposes freely; the full set is never injected verbatim. |
| `_MAX_OUTPUT_TOKENS` | `2048` | **New (R2-3).** Output budget for the reasoned generator's structured tool-use response (a bounded list of incumbent/candidate/rationale pairs). Disambiguation: this is `asset_swap_engine`'s OWN constant, unrelated to `logic_change_engine._MAX_OUTPUT_TOKENS` (same value, same naming pattern, different module — never coupled in code) or `build_plan_generator.MAX_OUTPUT_TOKENS=16384` (a different, much larger generation call). |
| `_REQUEST_TIMEOUT_SECONDS` | `30.0` | **New (R2-3).** Explicit client-side timeout on the Anthropic call — never relies on the SDK/urllib3 default. |
| `_EMIT_SWAP_CANDIDATES_TOOL` | tool schema dict | **New (R2-3).** The `emit_swap_candidates` structured tool-use schema: `candidates: [{incumbent_asset, candidate_asset, rationale?}]`. Tool choice is forced (`tool_choice={"type": "tool", "name": "emit_swap_candidates"}`) — the model cannot decline to call it. |

**Removed in R2-3:** `generate_objective_directed_candidates` (the deterministic generator — ascending-Pearson / ascending-variance / descending-pseudo-Sharpe per-objective sort) and `_select_incumbent_asset` (the deterministic held-ticker picker) are both deleted; no per-objective scaling-factor constants existed for either (unlike `logic_change_engine`'s five named factors) — the deterministic sort used inline computation, so there is no equivalent constant table to remove.

## API Reference

### `extract_lens_scores(context: dict) → dict`

**Unchanged in R2-3.** Extracts per-ticker lens scores from an assembled advisor context dict.

Real per-lens payload shapes, verified directly against the producers (not re-derived from a fixture):

| Lens | Real payload shape | Per-ticker signal? |
|------|---------------------|---------------------|
| `technicals` | `{"ma_posture": {ticker: {above_sma50, above_sma200}}, "breadth": float, "momentum": {ticker: float}}` (`ai_advisor.py:542-552`, `advisors/lens_technicals.py:265-272`) | YES — `momentum` is an unbounded raw 20-day return per ticker |
| `sentiment` | `{tone_score, corpus, events, article_count}` (`ai_advisor.py:673-684`) | No — market-wide scalar |
| `derivatives` | `{vix_level, vix_term_structure, risk_read, as_of_date}` | No — market-wide scalar |
| `macro` | `{"series": {series_id: {...}}}` | No — FRED-series-keyed, market-wide |
| `fundamentals` | `{"tickers": {ticker: key_facts_dict}, "coverage": {...}}` (`ai_advisor.py:1242-1253`) | Per-ticker-KEYED, but values are raw financials, not a clean scalar — excluded from v1 by design |

**Only `technicals.payload["momentum"]` is used.** Each raw momentum value is squashed onto `(0.0, 1.0)` via `_squash_momentum_to_unit_interval` before being returned. Only an `available=True` `technicals` block contributes (AC-6 honest-availability checked before payload content).

**R2-3 consumer change:** the result of this function still flows into `_build_objective_rationale` (rationale text, AC-5) and `_build_candidate_lens_evidence`/`_persist_observation` (persisted audit trail, AC-4) — those call sites are unchanged. It no longer flows into any candidate SELECTION or RANKING step, because the only function that ever consumed it that way (`generate_objective_directed_candidates` → `_apply_lens_blend`) is deleted/orphaned this cycle — see "Lens Blend" below.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `context` | `dict` | Dict returned by `ai_advisor.assemble_advisor_context`, or any dict with lens-block values keyed by lens name. Missing keys, None payload, and malformed blocks are handled gracefully. |

**Returns:** `{ticker: {"technicals": score_in_0_1}, ...}`. Returns `{}` when `technicals` is absent/unavailable/has no `momentum` data. Never raises.

---

### `_squash_momentum_to_unit_interval(momentum: float) → float` (internal helper)

**Unchanged in R2-3.** Maps an unbounded raw 20-day momentum return onto the open interval `(0.0, 1.0)` via `0.5 + 0.5 * math.tanh(momentum / _MOMENTUM_SQUASH_SCALE)`.

---

### `SwapCandidate` (dataclass) — **NEW (R2-3)**

One LLM-reasoned incumbent→candidate swap pair proposed by `generate_reasoned_swap_candidates`.

| Field | Type | Description |
|-------|------|-------------|
| `incumbent_asset` | `str` | The held ticker to replace. Always verified present in the real symphony tree (`extract_tickers`) before this object is constructed — never a raw, untrusted LLM claim. |
| `candidate_asset` | `str` | The proposed replacement ticker. Always verified a member of the real tradeable universe before this object is constructed. |
| `rationale` | `str` | The LLM's own free-text rationale for this pair, carried through for traceability. Never used as a trust signal for incumbent/candidate validity — those are independently verified. Default `""`. |

---

### `generate_reasoned_swap_candidates(symphony_id, raw_value, objective, *, reasoning_context=None, correlation_data=None, available_assets=None, tradeable_universe=None, max_candidates=MAX_SUGGESTED_CANDIDATES) → list[SwapCandidate]` — **NEW (R2-3), the module's headline addition**

Generates a bounded set of LLM-REASONED `SwapCandidate` pairs, replacing the deleted `generate_objective_directed_candidates` (Q4). Makes a real Anthropic `messages.create` tool-use call (model via `model_config.get_advisor_suggestion_model()`, forced `emit_swap_candidates` tool choice, `_MAX_OUTPUT_TOKENS=2048`, `_REQUEST_TIMEOUT_SECONDS=30.0`) with a prompt built by `_build_reasoned_swap_generation_prompt`: the objective, an optional `reasoning_context` block (verbatim, when the caller supplies one), optional `correlation_data` entity-key evidence, and a bounded (`_MAX_ASSETS_LISTED_IN_PROMPT=40`) sample of the real tradeable universe. Never a raw `json.dumps()` of the tree — the operator's real holdings reach the LLM only via `reasoning_context`, never independently rendered here.

**SECURITY-CRITICAL resolution against real ground truth (Q2/Q3):** each proposed pair's `incumbent_asset` is resolved against the REAL `raw_value` tree via `extract_tickers` — a pair whose incumbent does not resolve to a real holding is DROPPED, never fabricated into a `SwapCandidate`. Each `candidate_asset` is independently validated against the real tradeable universe (`get_tradeable_set()`, or a caller-supplied `tradeable_universe` override, intersected with `available_assets` when supplied) — an LLM's own claim of tradeability in free-text rationale is NEVER trusted. A candidate equal to its own incumbent (swap-into-self) is dropped as a wasted-backtest no-op.

D-1: never raises. `_build_client()` raising (no `ANTHROPIC_API_KEY`, SDK not installed), the SDK call raising, a response with no `tool_use` block, or a malformed `candidates` payload (missing/non-list) all degrade to `[]` — surfaced upstream as a clean `NO_SURVIVORS_MESSAGE` result, never an exception.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | Composer symphony UUID (traceability only). |
| `raw_value` | `dict` | The real symphony decision tree. |
| `objective` | `SwapObjective` | Drives generation. |
| `reasoning_context` | `str \| None` | Optional operator-context text block (see `ai_advisor.build_reasoning_context`), injected verbatim into the prompt when truthy. |
| `correlation_data` | `dict \| None` | Optional `{entity_id: [float]}` return series — Q4: retained as prompt EVIDENCE (entity keys surfaced), never used for a programmatic ranking anymore. |
| `available_assets` | `list \| None` | Optional caller-supplied candidate pool; narrows (never widens) the tradeable-universe membership check — the effective set is the INTERSECTION. |
| `tradeable_universe` | `frozenset \| None` | Optional caller-supplied override of the real tradeable set. When supplied, `get_tradeable_set()` is NEVER called (a genuine bypass, not an additional filter layer). |
| `max_candidates` | `int` | Upper bound on returned candidates. Default `MAX_SUGGESTED_CANDIDATES=30`; `propose_operator_swap`'s REASONED branch passes `1`. |

**Returns:** A bounded list of `SwapCandidate` objects. Empty when there are no real holdings, the LLM proposes nothing usable, or any failure occurs.

---

### `_build_reasoned_swap_generation_prompt(objective, universe, *, reasoning_context, correlation_data) → str` (internal helper) — **NEW (R2-3)**

Assembles the LLM prompt: `OBJECTIVE:` / optional `reasoning_context` block verbatim / optional `CORRELATION EVIDENCE:` (sorted entity keys only, never raw series) / a bounded `SAMPLE OF THE TRADEABLE CANDIDATE UNIVERSE` (`_MAX_ASSETS_LISTED_IN_PROMPT=40` entries) / an instruction to call `emit_swap_candidates` with a genuinely-held `incumbent_asset`. Bounded regardless of the real universe's size (AC-1/Q3) — never scales with the ~12.7k-symbol tradeable set.

---

### `_build_client()` (internal helper) — **NEW (R2-3)**

Constructs the `anthropic` SDK client. Factory seam: tests patch `asset_swap_engine._build_client`. Mirrors `logic_change_engine._build_client` / `ai_advisor._build_client` / `build_plan_generator._build_client`. Raises `RuntimeError` (caught by `generate_reasoned_swap_candidates`'s D-1 wrapper — never propagates) when `ANTHROPIC_API_KEY` is unset or the `anthropic` SDK is not installed (lazy `import anthropic`, CC-2, off-execution-path).

---

### `propose_operator_swap(symphony_id, score_tree, objective, *, incumbent_asset=None, candidate_asset=None, incumbent_oos_alpha=None, default_oos_alpha=0.0, lens_scores=None, lens_sources=None, reasoning_context=None, reasoning_manifest=None, run_id=None) → SwapRunResult`

**R2-3 signature change:** `objective` moved from the 5th to the 3rd positional parameter, and `incumbent_asset`/`candidate_asset` moved from required positional parameters to optional keyword-only parameters — this is what makes the REASONED mode possible (omit both to let the generator propose). Existing callers passing all five positionally will break; the sole production caller (`app.py`'s route) was updated in the same commit.

Evaluate an operator-initiated asset swap (AC-2.1). **Two modes (R2-3):**
- **EXPLICIT-PAIR** (both `incumbent_asset`/`candidate_asset` truthy): the operator's exact pair is backtested + gated — BYTE-PRESERVED pre-R2-3 behavior (AC-12). `generate_reasoned_swap_candidates` and `get_tradeable_set` are NEVER called on this path. No Composer-key gate on this branch either — pre-R2-3 behavior never had one here, and byte-preservation includes that omission.
- **REASONED** (either/both omitted): `generate_reasoned_swap_candidates` (bounded to `max_candidates=1`) proposes the pair over the operator's real holdings + the real tradeable universe, then the resolved pair is evaluated via the SAME `_evaluate_explicit_pair` helper (below) the explicit-pair mode uses.

`run_id`/`provenance` are minted unconditionally at the very top, before any other logic, so every return path — including both early-exit branches (no Composer key on the REASONED path only; the LLM-returns-nothing branch) — carries the same, non-fabricated `run_id`/`provenance` (AC-5/AC-7).

**AC-X4 ordering (matches R2-2's re-gate fix, correct from first GREEN this cycle):** on the REASONED path, the Composer-key check runs BEFORE `generate_reasoned_swap_candidates` is ever called — a valid `ANTHROPIC_API_KEY` with no Composer credentials must never bill a live Anthropic call for a run guaranteed to be discarded.

Never raises on backtest, gate, or generation failure (AC-X5/D-1).

**Parameters (R2-3 additions marked):**

| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | Composer symphony UUID. |
| `score_tree` | `dict` | The raw Composer score tree. |
| `objective` | `SwapObjective` | Drives the swap and surfaces alongside the result (AC-2.3). **Moved to 3rd positional (R2-3).** |
| `incumbent_asset` | `str \| None` | **R2-3: now optional, keyword-only.** Ticker to replace. Both this and `candidate_asset` must be truthy to select EXPLICIT-PAIR mode. |
| `candidate_asset` | `str \| None` | **R2-3: now optional, keyword-only.** Replacement ticker (open universe). |
| `lens_scores` | `dict \| None` | Per-ticker lens evidence from `extract_lens_scores`. Enriches rationale (AC-5) and persistence (AC-4) on BOTH modes — never affects REASONED-mode candidate selection (that is the LLM's). Not passed by the operator-clicked route today (see "Reachability caveat" below). |
| `reasoning_context` | `str \| None` | **New (R2-3).** Ready-to-inject operator-context text block, threaded to `generate_reasoned_swap_candidates` on the REASONED path only. |
| `reasoning_manifest` | `dict \| None` | **New (R2-3).** The honest per-source manifest paired with `reasoning_context`; stamped into `provenance["evidence_injected"]` and persisted on the observation this run writes. |
| `run_id` | `str \| None` | **New (R2-3).** Optional caller-supplied run id, used verbatim instead of minting a fresh UUID4. |

**Returns:** `SwapRunResult` — always returned, never raises.

---

### `_evaluate_explicit_pair(symphony_id, score_tree, incumbent_asset, candidate_asset, objective, *, symphony_name, incumbent_oos_alpha, default_oos_alpha, lens_scores, lens_sources, run_id, provenance) → SwapRunResult` (internal helper) — **NEW (R2-3)**

Evaluate ONE `(incumbent, candidate)` pair as a single-element gated batch: backtests via `_evaluate_single_variant`, computes the fold-matched baseline OOS alpha (H5/H6/RC-1), gates via `evaluate_candidate_batch` with a real SPY-OOS baseline (`_spy_returns_fn_for`), persists the observation regardless of verdict (RC-4, surfacing a persistence failure via `persistence_error` rather than swallowing it — RC-5), and returns the `SwapRunResult`.

The byte-preserved core shared by `propose_operator_swap`'s EXPLICIT-PAIR mode (AC-12 — this is exactly the pre-R2-3 gating logic, factored out unchanged) and its REASONED mode once the reasoned generator has resolved a single pair. Both callers pass the SAME already-minted `run_id`/`provenance` through.

---

### `suggest_swaps(symphony_id, score_tree, objective, correlation_data, available_assets, *, incumbent_oos_alpha=None, default_oos_alpha=0.0, lens_scores=None, lens_sources=None, reasoning_context=None, reasoning_manifest=None, run_id=None) → SwapRunResult`

Evaluate advisor-suggested objective-directed swap candidates (AC-2.2). **R2-3:** generates candidates via `generate_reasoned_swap_candidates` (LLM-reasoned, replaces the deleted `generate_objective_directed_candidates`), backtests each independently (AC-X5 — one candidate's failure never aborts the batch), then submits ALL successfully-backtested candidates as ONE `evaluate_candidate_batch` call for honest `n_effective=N` BHY-FDR gating.

`run_id`/`provenance` minted unconditionally, same shape as `propose_operator_swap`. No Composer API key → `no_api_key=True`, writes nothing (AC-X4), checked BEFORE the reasoned generator is ever reached.

**Live production caller (advisor-rewire cycle, Workstream C.2, unaffected by R2-3 — confirmed zero diff on the call site):** `advisors.weekly_suggestions_scheduler.run_weekly_asset_swap_suggestions()` calls this once per live symphony, weekly, WITHOUT `reasoning_context`/`reasoning_manifest` (that call site doesn't build reasoning context) — candidates are still LLM-reasoned, just without injected live operator context. It DOES still pass a real `lens_scores` dict sourced from `_fetch_lens_scores()` — see "Lens Blend" below for what that lens evidence now does (and no longer does) post-R2-3.

**Parameters (R2-3 additions marked):**

| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | Composer symphony UUID. |
| `score_tree` | `dict` | Raw Composer score tree. |
| `objective` | `SwapObjective` | Drives candidate generation. |
| `correlation_data` | `dict` | `{entity_id: [float]}` return series. **R2-3 (Q4): retained as prompt EVIDENCE surfaced to the LLM — no longer drives a programmatic ranking.** |
| `available_assets` | `list` | Candidate pool. **R2-3: an ADDITIONAL constraint intersected with the real tradeable universe inside the reasoned generator — never widens beyond it.** |
| `lens_scores` | `dict \| None` | Per-ticker lens evidence. Threaded into rationale (AC-5) and persistence (AC-4). **R2-3: does NOT affect the LLM-reasoned selection/ranking** (that is the LLM's) — a behavior change from the pre-R2-3 blend-affects-ranking contract, see "Lens Blend" below. |
| `reasoning_context` | `str \| None` | **New (R2-3).** Threaded straight through to `generate_reasoned_swap_candidates`. Omitted at the weekly scheduler's call site. |
| `reasoning_manifest` | `dict \| None` | **New (R2-3).** Stamped into `provenance["evidence_injected"]` and persisted on every observation this run writes. |
| `run_id` | `str \| None` | **New (R2-3).** Optional caller-supplied run id. |

**Returns:** `SwapRunResult` — always returned, never raises. Zero survivors is a valid outcome (AC-2.5).

---

### `apply_ticker_swap(raw_value, from_ticker, to_ticker) → dict`

**Unchanged in R2-3 (AC-12).** Deep-copies `raw_value` and substitutes `ticker == from_ticker` → `to_ticker` at every matching node. Never mutates the input; zero substitutions is a valid no-op. **New consumer (R2-3):** immediately followed by the `validate_tree` structural guard inside `_evaluate_single_variant` (see below) — a NAVIGATION-and-substitution primitive only; it has no opinion on structural validity of the result.

### `extract_tickers(raw_value) → set`

**Unchanged in R2-3 (AC-12).** Collects all ticker values in the tree. Returns `set()` on malformed input. **New consumer (R2-3):** the security-critical incumbent-resolution check inside `generate_reasoned_swap_candidates`.

### `_evaluate_single_variant(raw_value, symphony_id, incumbent_asset, candidate_asset, objective, symphony_name="", lens_scores=None) → tuple` (internal helper)

**R2-3 signature change:** gains `incumbent_asset`/`candidate_asset` as explicit required positional parameters (the pre-R2-3 single-incumbent-then-iterate shape is gone — `suggest_swaps` now loops over `SwapCandidate` pairs directly, each carrying its own incumbent).

Returns `(BacktestCandidate | None, SwapProposalResult, baseline_stats | None, baseline_returns_pct)` — a 4-tuple; `baseline_returns_pct` lets callers reuse the already-computed baseline daily-returns series instead of a second, redundant baseline backtest (AC-13).

**R2-3 (AC-3) — the `validate_tree` guard, net-new safety over `apply_ticker_swap`, placed identically to `logic_change_engine`'s guard:** immediately after `apply_ticker_swap` and BEFORE any backtest call (including the baseline), the swapped tree is passed through `symphony_schema.validate_tree`. A variant that fails is dropped BEFORE any backtest call, with a `backtest_error` message deliberately distinct from the "incumbent not in tree" wording — `"...the swapped tree failed structural validation (...)"` — so an operator or log reader can tell the two failure classes apart. Composer `/backtest` remains the real tradeability arbiter; this is a cheap structural pre-check.

`candidate` is `None` when the incumbent is absent from the tree, the swapped tree fails `validate_tree`, or the variant backtest failed (AC-X5 — isolated to this candidate, never aborts the batch).

## Types

### `SwapObjective`

**Unchanged in R2-3.**

```python
@dataclass
class SwapObjective:
    objective_type: str   # "reduce_correlation" | "reduce_drawdown" | "lift_risk_adjusted"
    target_pair: tuple[str, str] | None  # Symphony IDs/tickers for correlation objectives
    measured_value: float  # Display-only; does NOT influence candidate generation, ranking,
                            # or gate decisions. Every current production caller (app.py:4457-4461)
                            # passes 0.0 -- never a live measurement. _build_objective_rationale
                            # never reads this field (AC-10, unaffected by R2-3). See
                            # docs/generated/advisors_logic_change_engine.md for the identical
                            # pattern on the Logic Changes engine.
```

### `SwapCandidate` — **NEW (R2-3)** — see API Reference above.

### `SwapProposalResult`

**Unchanged in R2-3.** Per-candidate result. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | `"{symphony_id}:{incumbent}->{candidate}"` |
| `objective_rationale` | `str` | Why this candidate addresses the objective. Includes lens evidence summary when `lens_scores` is provided (AC-5). |
| `gate_result` | `CandidateGateResult \| None` | Gate verdict, `validation_days`, `oos_alpha`, `caveats`. `None` on backtest failure. |
| `caveats` | `list` | Propagated from `gate_result.caveats` — always non-empty for ADOPT_CANDIDATE survivors (AC-3.3). |
| `apply_guidance` | `str` | "To apply: open {symphony_name} in Composer and swap {from} → {to} manually." Always present, never a button (AC-X1). |
| `backtest_error` | `str \| None` | Descriptive string on backtest failure — R2-3 adds the distinct `validate_tree` structural-invalidity wording alongside the pre-existing "incumbent not in tree" message; `None` on success. |

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
| `run_id` | `str` | **NEW (R2-3, AC-7).** A UUID4 minted once per call (or a caller-supplied override), present on EVERY return path, including every early return. Traced into every persisted `advisor_observations` row this run writes. |
| `provenance` | `dict \| None` | **NEW (R2-3, AC-5).** `{"generation_model", "mode": "asset-swap", "evidence_injected", "run_id"}` — a REAL 4-key dict on every return path, never `None`, never fabricated. |

## Lens Blend — How Ranking Works

> **SUPERSEDED IN PRODUCTION BY R2-3 (`DE-ADVISOR-R2-3-001`, 2026-07-14).** `_apply_lens_blend` itself is preserved byte-unchanged (AC-12 — see the formula below, still accurate as a description of what the function computes) but has **zero production call sites** as of this cycle: its only caller, `generate_objective_directed_candidates`, is deleted. `lens_scores` is still fetched and threaded through `suggest_swaps`/`propose_operator_swap` (both routes and the weekly scheduler are unchanged), but it now ONLY enriches `objective_rationale` text (`_build_lens_evidence_summary`) and the persisted `lens_evidence` audit field (`_build_candidate_lens_evidence`) — it no longer reorders, reranks, or otherwise influences which candidates are proposed or survive. Candidate SELECTION is now entirely the LLM's (Q4) — the entire "reachability chain" narrative below (fixed 2026-07-12, closed 2026-07-12) describes a chain that no longer exists past its `generate_objective_directed_candidates` link. The narrative is preserved below for historical/audit traceability of a real bug and its real fix — it is not a description of current runtime behavior.

**Prior design (Cycle-3, position-based — REPLACED before R2-3, was mathematically inert):**

```
blended_key[i] = position[i] - LENS_BLEND_WEIGHT * mean_lens_score[i]
```

This never worked: for any two adjacent 0-based `position` values the integer gap is always `>= 1`, and `LENS_BLEND_WEIGHT = 0.25 < 1`, so lens evidence could not change the order for **any** input — a closed-form proof lives in `tests/ai_advisor/test_lens_blend_efficacy.py`'s module docstring.

**Design fixed pre-R2-3 (cumulative absolute score-distance), now orphaned by R2-3's generator deletion:**

```
cum_gap[0] = 0
cum_gap[i] = cum_gap[i-1] + |score[i] - score[i-1]|     (walked in the caller's
                                                           own pre-sorted order,
                                                           index 0 = best)
blended_key[i] = cum_gap[i] - LENS_BLEND_WEIGHT * (mean_lens[i] - _LENS_NEUTRAL_SCORE)
```

`_apply_lens_blend` keeps this exact formula and its own dedicated test coverage (AC-12) — it is a correct, tested, standalone function; it simply has no production caller left. See `advisors/asset_swap_engine.py`'s module-level `_apply_lens_blend` docstring for the full mechanics if this function is ever re-wired to a future candidate-generation step.

**Historical reachability chain (advisor-rewire cycle, 2026-07-12 — no longer current):** `_build_base_candidate_pool` (lens-covered universe) → `_fetch_lens_scores` (real technicals momentum) → `extract_lens_scores`/`_squash_momentum_to_unit_interval` → `generate_objective_directed_candidates`/`_apply_lens_blend` (reordering formula, **link deleted by R2-3**) → `evaluate_candidate_batch` → `insert_advisor_observation`. This chain was proven end-to-end non-empty by a live droplet-DB E2E test before R2-3 shipped; R2-3 intentionally breaks the 4th link as the direct, documented consequence of making candidate selection genuinely LLM-reasoned (Q4) rather than a fixed sort the lens evidence could nudge.

## Persistence (AC-4)

Every evaluated proposal is persisted via `database.insert_advisor_observation` with:

- `advisor_role="ASSET_SWAP"`
- `is_advisory_only=1`
- `observation_type="asset_swap_proposal"`
- `verdict`: the actual gate decision (ADOPT_CANDIDATE / KEEP_INCUMBENT / REJECT_VETO_FAILED)
- `raw_response`: includes `lens_evidence` (`{ticker: {signal, source_lens, confidence}}`) and `sources` (citation dicts) when `lens_scores`/`lens_sources` are provided, plus **R2-3 (AC-7): `run_id` and `evidence_injected`** — additive traceability keys, always present (never a schema migration — `raw_response` is a free-form JSON blob column), mirroring `logic_change_engine.py`'s identical R2-2 persistence pattern.

Persistence is verdict-agnostic (RC-4) — the operator sees the engine ran even on KEEP_INCUMBENT outcomes. A persistence failure is surfaced in `SwapRunResult.persistence_error` and never swallowed (RC-5).

## Reachability caveat (advisor-intent audit, 2026-07-13, updated R2-3 2026-07-14)

(a) The operator-clicked evaluate route (`POST /ai-advisor/asset-swaps/evaluate`, `app.py:4338`) never passes `lens_scores`/`lens_sources` to `propose_operator_swap` (both default `None`) — on that surface, `lens_evidence` persists as `{}` on both the EXPLICIT-PAIR and REASONED modes. **R2-3 update:** this claim is now also trivially true of `_apply_lens_blend` for a stronger reason than before — the function is unreachable from EITHER route regardless of whether `lens_scores` is populated, since its only caller no longer exists (see "Lens Blend" above). This is unaffected by R2-3's own scope (candidate generation) either way.

(b) Even where `lens_scores` IS wired (the weekly scheduler path only, via `weekly_suggestions_scheduler.py`), the evidence reads a SINGLE lens (`technicals.momentum` only). **R2-3 update:** as of this cycle that evidence affects ONLY the persisted `lens_evidence` audit field and the rationale text on `suggest_swaps`' survivors — it has zero influence on which candidates are generated, ranked, or gated (that is entirely `generate_reasoned_swap_candidates` + the LLM's job now). Before R2-3, the same evidence could reorder `generate_objective_directed_candidates`'s output via `_apply_lens_blend`; that influence path is gone.

## Internal Dependencies

- `advisors.backtest_gate_engine` — `evaluate_candidate_batch`, `BacktestCandidate`, `GatedBatch`, `CandidateGateResult`, `_fold_transform_single`, `HARVEY_LIU_FDR_Q` — ZERO diff this cycle.
- `advisors.composer_backtest_client` — `run_backtest`
- `advisors.symphony_schema` — **NEW call site (R2-3).** `validate_tree` — the net-new structural guard on every edited tree; also used unchanged inside `_spy_returns_fn_for`'s synthetic SPY tree (`make_root`/`make_weight_equal`/`make_asset`, predates R2-3).
- `advisors.universe_provider` — **NEW (R2-3).** `get_tradeable_set` — the real tradeable-membership check for every LLM-proposed `candidate_asset` (Q3).
- `database` — `insert_advisor_observation`
- `ai_advisor` — **NEW module-level import (R2-3), mirrors `logic_change_engine.py`'s R2-2 pattern.** `_EMPTY_MANIFEST` (fallback `evidence_injected` default) — ZERO diff to `ai_advisor.py` itself this cycle, pure reuse. Transitively reaches `alpha_bot_execution` via the SAME accepted chain `DE-ADVISOR-R2-1-001`/`DE-ADVISOR-R2-2-001` already established (`ai_advisor.py`'s own `import symphony_logic` → `symphony_logic.py`'s `from alpha_bot_execution import ...`) — ACCEPTED for the identical reasons (import-only, no cycle, `asset_swap_engine.py` itself is lazy-imported inside the route handler, CC-2).
- `model_config` — **NEW (R2-3).** `get_advisor_suggestion_model()` — the `provenance["generation_model"]` value and the model used by `generate_reasoned_swap_candidates`'s `messages.create` call.
- `uuid` — **NEW (R2-3), stdlib.** `run_id` minting.
- `math` — stdlib, `_squash_momentum_to_unit_interval` (`math.tanh`, unaffected by R2-3).
- `anthropic` (third-party SDK) — **NEW (R2-3), lazy-imported inside `_build_client`** (CC-2, off-execution-path).
- `advisors.weekly_suggestions_scheduler` — the sole live production caller of `suggest_swaps` (Workstream C.2); its call site is unaffected by R2-3 (confirmed zero diff) — see `docs/generated/advisors_weekly_suggestions_scheduler.md` for the corresponding update to its own now-stale lens-reachability narrative.
