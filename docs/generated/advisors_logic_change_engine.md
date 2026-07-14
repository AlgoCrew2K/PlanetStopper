# advisors/logic_change_engine

> M4 Logic-Change proposal engine: operator-initiated (plain-text steering hint or explicit `LogicTweak`) and advisor-suggested (objective-directed) parameter-tweak proposals for a symphony's decision tree, gated through the shared M2 BHY/FDR batch gate; advise-only, never auto-applies. **R2-2 (2026-07-14):** the candidate generator is now LLM-reasoned, not a fixed-multiplier script.

**Source:** `advisors/logic_change_engine.py`
**Last updated:** 2026-07-14 (R2-2, `DE-ADVISOR-R2-2-001` — LLM-reasoned generator replaces the fixed-multiplier scripts; `validate_tree` guard; `run_id`/`provenance` contract)

## Overview

`advisors/logic_change_engine.py` implements the two Logic-Change modes from `feature-plans/ai-advisor.md` §M4 (AC-3.*):

1. **Operator-initiated** (`propose_operator_logic_change`) — the operator supplies either an explicit `LogicTweak` (`node_path` + `param_key` + `old_value` + `new_value`) or a plain-text `change_description` (e.g. `"Reduce window from 20d to 16d"`). **R2-2:** a `change_description` is no longer parsed deterministically — it is routed through the LLM-reasoned generator (`generate_reasoned_logic_candidates`, bounded to one candidate) as a steering hint, alongside the objective and (when the route supplies it) real operator context. The resulting tweak is applied to a deep copy of the score tree, structurally re-validated, backtested, gated as a single-element batch, and persisted.
2. **Advisor-suggested** (`suggest_logic_changes`) — given a `LogicChangeObjective`, `generate_reasoned_logic_candidates` produces a bounded, LLM-reasoned, objective-directed set of `LogicTweak` candidates; all are backtested and submitted as ONE `evaluate_candidate_batch` call so the BHY/Yekutieli FDR correction applies across the FULL set (AC-3.2 — never gate candidates individually).

**R2-2 — LLM-reasoned generation + provenance (`DE-ADVISOR-R2-2-001`, 2026-07-14):** this is the module's headline change this cycle. Prior to R2-2, both modes' candidates were produced by fixed-percentage scripts (`generate_objective_directed_candidates` scaled by five named per-objective factors; a `change_description` with no explicit numbers fell back to a flat +/-20% via `_fallback_direction_factor`/`_parse_change_description_to_tweak`) — the tab was honestly labelled "Deterministic — no AI reasoning" for exactly this reason. **All of that fixed-multiplier machinery — `generate_objective_directed_candidates`, `generate_objective_directed_logic_candidates`, `_parse_change_description_to_tweak`, `_fallback_direction_factor`, and every scaling/direction constant — was DELETED in R2-2**, replaced by a single new generator:

- **`generate_reasoned_logic_candidates`** (see API Reference below) makes a real Anthropic tool-use call: the LLM is shown the objective, an optional operator-context block, and a bounded listing of the tree's actual tweakable parameters (`node_path`/`param_key`/`current_value`), and proposes edits via an `emit_logic_edits` tool schema. This is the reasoned path's only candidate source now — there is no deterministic fallback generator left in the module.
- **The `evidence_injected` manifest — R2's thesis, not a side effect** (same framing as `DE-ADVISOR-R2-1-001`): when the caller (the route) supplies `reasoning_context`/`reasoning_manifest` from `ai_advisor.build_reasoning_context`, the EXACT SAME per-source honesty manifest that gated what was injected into the LLM prompt is the exact same dict surfaced to the operator on the response JSON (`provenance["evidence_injected"]`) and persisted on every observation this run writes. Omitted (e.g. the weekly scheduler's call site, which does not build reasoning context) → `ai_advisor._EMPTY_MANIFEST` (all 7 keys `"absent"`), never a fabricated placeholder.
- **`validate_tree` guard — net-new safety over `apply_logic_tweak`.** `apply_logic_tweak` only checks that the target node/`param_key`/`old_value` exist and match — a NAVIGATION check, with no opinion on whether the resulting tree is still a structurally valid Composer tree. An LLM is a less-trusted editor than a fixed-percentage constant: a reasoned edit can navigate to a real node (so `apply_logic_tweak` succeeds) yet still corrupt a structural field the constant-based generator could never have touched. `_evaluate_single_variant` now calls `advisors.symphony_schema.validate_tree` on every edited tree before any backtest; a variant that fails is dropped with an honest, distinctly-worded reason (never fabricated, never backtested) — this is exactly *why* the guard exists, not incidental hardening.
- **`run_id`/`provenance` — the same 4-key cross-cutting contract `DE-ADVISOR-R2-1-001` established on Strategy Builder** (`{generation_model, mode, evidence_injected, run_id}`), minted unconditionally at the top of both `propose_operator_logic_change` and `suggest_logic_changes` so every return path — including the earliest error returns — carries it. `run_id` is additionally threaded into every `_persist_observation` call this run makes, so a persisted proposal traces back to its run and evidence manifest. **R2-3 (Asset Swaps) reuses this same assembler + provenance shape** — this is not a Logic-Changes-only feature.
- Every reuse point is verbatim, byte-unchanged this cycle: `ai_advisor.py`, `advisors/symphony_schema.py`, and `advisors/backtest_gate_engine.py` carry ZERO diff in R2-2 — the module calls into them, it does not modify them.

**Advisor-rewire cycle (2026-07-12, Workstream C.1): first production caller.** `suggest_logic_changes` had a complete implementation and test suite but, before that cycle, no scheduled or automatic caller anywhere in the codebase — it was reachable only by manual/operator invocation. `advisors.weekly_suggestions_scheduler.run_weekly_logic_change_suggestions()` now enumerates every live symphony weekly and calls `suggest_logic_changes(symphony_id, score_tree, objective)` once per symphony (per-symphony D-1 isolation — one symphony's failure never blocks the others), with `objective_type="reduce_drawdown"` as the unattended-sweep default. **R2-2 does not touch `advisors/weekly_suggestions_scheduler.py` at all (confirmed: zero diff)** — the scheduler's call site is unchanged, still omits `reasoning_context`/`reasoning_manifest`/`run_id`; those all default to `None`, so its candidates are still LLM-reasoned (the generator swap is engine-internal) but without injected live operator context — same "reasoned, not context-injected" shape R2-1 established for Strategy Builder's own from-scratch/weekly path. See `docs/generated/advisors_weekly_suggestions_scheduler.md`.

**Off-execution-path (AC-X2):** this module is not imported from `alpha_bot_execution.py`. It is an advise-only, offline, post-backtest decision layer.

**No write endpoints (AC-X1):** only `GET /score` and stateless `POST /api/v0.1/backtest` are called (via `advisors.composer_backtest_client.run_backtest`), plus the Anthropic `messages.create` tool-use call for candidate generation. No Composer write, mutate, or trade-placement call of any kind.

**Never auto-applies (AC-3.4):** every survivor's `apply_guidance` is a plain-text `ADVISE_ONLY_APPLY_TEMPLATE` instruction ("open {symphony} in Composer and manually adjust…") — never a button, never a write call.

**Verdict-agnostic persistence (RC-4):** every gated proposal — survivor, `KEEP_INCUMBENT`, or `REJECT_VETO_FAILED` — is persisted as an `advisor_observation` with `is_advisory_only=1`, so the operator sees the engine ran even on the common non-ADOPT path. A persistence failure is surfaced via `LogicChangeRunResult.persistence_error`, never swallowed to a warning (RC-5).

## Public Types

### `LogicTweak` (dataclass)

One concrete numeric parameter change to apply to a symphony logic tree. Unchanged in R2-2 — both the operator-supplied and LLM-reasoned paths produce the same typed shape.

| Field | Type | Description |
|-------|------|-------------|
| `node_path` | `list[str \| int]` | Navigation keys from root to the target node (e.g. `["children", 0, "children", 2]`); `[]` means the root node |
| `param_key` | `str` | The key within the target node whose value is being changed |
| `old_value` | `Any` | Current value at `param_key` — validated against the live tree before applying |
| `new_value` | `Any` | Proposed replacement value (numeric) |
| `node_description` | `str` | Human-readable node/param description for operator-facing apply guidance (default `""`) |

### `LogicChangeObjective` (dataclass)

Typed objective driving a logic-change search (Gate-1 Resolution #2 — every change must be objective-directed, never a vibe tweak). Unchanged in R2-2.

| Field | Type | Description |
|-------|------|-------------|
| `objective_type` | `str` | One of `"reduce_drawdown"`, `"lift_risk_adjusted"`, `"reduce_turnover"`, `"improve_momentum_timing"`, `"reduce_whipsaw"`, or any other named objective |
| `measured_value` | `float` | Display-only value; does NOT influence tweak generation, ranking, or gate decisions. Current production callers (app.py's operator-evaluate route) pass `0.0`. The "measured X" phrase was permanently removed from `_build_objective_rationale`'s output in `DE-ADVISOR-R1-001` (AC-10). **R2-2 resolution:** the one remaining leftover instance of that fabricated-phrase pattern this doc previously flagged (`generate_objective_directed_logic_candidates`'s `change_description` builder) is now moot — that entire function was deleted in R2-2 along with the rest of the fixed-multiplier generator family. No production or dead-code path fabricates a "measured X" phrase anywhere in this module as of this cycle. |
| `rationale` | `str` | Human-readable explanation surfaced alongside every survivor (AC-3.3); default `""` |

### `LogicChangeProposalResult` (dataclass) — alias `LogicProposalResult`

Result for one evaluated logic-change candidate. Unchanged in R2-2.

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | `"{symphony_id}:{param_key}@{path}:{old}->{new}"` traceability ID |
| `symphony_id` | `str` | Composer symphony UUID |
| `tweak` | `LogicTweak \| None` | `None` when the tweak is structurally invalid or unparseable |
| `objective` | `LogicChangeObjective` | The objective driving this proposal (AC-3.3) |
| `objective_rationale` | `str` | Human-readable explanation of how this change addresses the objective |
| `gate_result` | `CandidateGateResult \| None` | From `backtest_gate_engine.evaluate_candidate_batch`; `None` if backtest failed pre-gate |
| `baseline_stats` / `variant_stats` | `dict \| None` | Stats from the unchanged / changed tree backtests |
| `caveats` | `list[str]` | Propagated from `gate_result.caveats`; `SURVIVOR_OVERFITTING_CAVEAT` mandatory on every `ADOPT_CANDIDATE` |
| `apply_guidance` | `str` | Plain-text operator instruction (AC-X1 / AC-3.4) |
| `backtest_error` | `str \| None` | Descriptive failure string (AC-X5) — never aborts the batch. **R2-2:** now also carries the distinct `validate_tree` structural-invalidity message (see `_evaluate_single_variant` below) alongside the pre-existing "old_value not found" wording |
| `data_warnings` | `list[str]` | Ticker-level data-availability warnings from the Composer API |

### `LogicChangeRunResult` (dataclass)

Top-level result of a `propose_operator_logic_change` or `suggest_logic_changes` run.

| Field | Type | Description |
|-------|------|-------------|
| `gate_batch` | `GatedBatch` | Always non-`None`, even for zero candidates — carries `n_candidates`/`fdr_q` audit trail |
| `proposals` | `list[LogicChangeProposalResult]` | All evaluated proposals (survivors + rejected + failed) |
| `survivors` | `list` | Subset with `gate_result.verdict.decision == "ADOPT_CANDIDATE"` |
| `rejected_candidates` | `list` | Subset gated-out or backtest-failed |
| `message` | `str` | Human-readable run summary; `NO_SURVIVORS_MESSAGE` on zero survivors |
| `objective` | `LogicChangeObjective \| None` | The objective that drove this run |
| `no_api_key` | `bool` | `True` when the Composer API key is absent — proposals empty (AC-X4) |
| `persistence_error` | `str \| None` | Non-`None` when the `advisor_observation` write failed (RC-5) |
| `run_id` | `str` | **NEW (R2-2, AC-7).** A UUID4 minted once per call (or a caller-supplied override), present on EVERY return path including every early return — a correlation id for the call itself, traced into every persisted `advisor_observations` row this run writes |
| `provenance` | `dict \| None` | **NEW (R2-2, AC-5).** `{"generation_model", "mode", "evidence_injected", "run_id"}` — a REAL 4-key dict on every return path, never `None`, never fabricated. `generation_model`/`mode`/`run_id` are cheap, non-fabricated facts about the call itself, never nulled on an error path — only `evidence_injected`'s own per-source values carry the honesty signal |

## Constants

### Public

| Constant | Value | Description |
|----------|-------|-------------|
| `LOGIC_CHANGE_SURVIVOR_CAVEAT` | re-export of `backtest_gate_engine.SURVIVOR_OVERFITTING_CAVEAT` | Mandatory caveat on every survivor |
| `NO_SURVIVORS_MESSAGE` | `"no logic change cleared the gate this run"` | Zero-survivors is a valid, non-error outcome |
| `MAX_SUGGESTED_CANDIDATES` | `30` | Upper bound on advisor-suggested candidates per run — keeps the FDR correction effective (`feature-plans/ai-advisor.md` §Gate-1 Resolutions #4); also the default `max_candidates=` bound on `generate_reasoned_logic_candidates` |
| `ADVISE_ONLY_APPLY_TEMPLATE` | `"To apply: open {symphony_name} in Composer and manually adjust {node_description} from {old_value} to {new_value}."` | Operator-facing apply-manually template (AC-X1 / AC-3.4) |

### R2-2 reasoned-generator constants (`generate_reasoned_logic_candidates`)

Every value is a named module-level constant with a source comment (project coding standard — no magic numbers). These constants **replace** the deleted objective-scaling and fallback-direction tables below.

| Constant | Value | Description |
|----------|-------|-------------|
| `_MAX_PARAMS_LISTED_IN_PROMPT` | `40` | Upper bound on the tweakable-parameter listing in the generation prompt — bounded regardless of tree size; a 2000+-numeric-param tree must not blow the prompt's input budget |
| `_MAX_OUTPUT_TOKENS` | `2048` | Output budget for the generator's structured tool-use response (a bounded list of `node_path`/`param_key`/`new_value`/`rationale` edits). **Disambiguation:** this is `logic_change_engine`'s OWN constant, unrelated to `advisors/build_plan_generator.MAX_OUTPUT_TOKENS=16384` (a different module's output ceiling for a much larger 12-plan generation call) — same naming pattern as the `_MAX_TREE_RENDER_CHARS` vs. `MAX_OUTPUT_TOKENS` disambiguation `DE-ADVISOR-R2-1-001` already documented for `ai_advisor.py`; the two constants have never been coupled in code |
| `_REQUEST_TIMEOUT_SECONDS` | `30.0` | Explicit client-side timeout on the Anthropic call — never relies on the SDK/urllib3 default |
| `_EMIT_LOGIC_EDITS_TOOL` | tool schema dict | The `emit_logic_edits` structured tool-use schema: `edits: [{node_path, param_key, new_value, rationale?}]`. Tool choice is forced (`tool_choice={"type": "tool", "name": "emit_logic_edits"}`) — the model cannot decline to call it |

### REMOVED in R2-2 — DE-LOGIC-CHANGE-DIRECTION-001's constants

The two constant tables previously documented here (`generate_objective_directed_candidates`'s five per-objective scaling factors — `_REDUCE_DRAWDOWN_TIGHTEN_FACTOR` etc. — and `_parse_change_description_to_tweak`'s fallback direction factors — `_FALLBACK_INCREASE_FACTOR`/`_FALLBACK_DECREASE_FACTOR`/the direction-keyword tuples) no longer exist. Every constant in both tables was deleted along with the functions that consumed them. See the superseded-banner on the Bug Fix section below for the historical context this removal affects.

## API Reference

### `propose_operator_logic_change(symphony_id, score_tree, tweak=None, objective=None, *, change_description=None, incumbent_oos_alpha=None, default_oos_alpha=0.0, reasoning_context=None, reasoning_manifest=None, run_id=None) → LogicChangeRunResult`

Evaluate one operator-specified logic change (AC-3.1 operator-initiated mode). Exactly one of `tweak` or `change_description` must be supplied. **R2-2:** when `change_description` is given, it is now routed through `generate_reasoned_logic_candidates` (bounded to a single candidate via `max_candidates=1`) as a steering hint — directionally informed by `objective` and, when the caller supplies it, `reasoning_context` — rather than the deleted deterministic parser. `objective` is required (raises `ValueError` if `None`).

`run_id`/`provenance` are minted unconditionally before any other logic runs, so every return path — including the two early-exit branches (neither `tweak` nor `change_description` supplied; no Composer API key) and the LLM-returns-nothing branch — carries the same, non-fabricated `run_id`/`provenance` (AC-5/AC-7).

Gates as a single-element batch (`evaluate_candidate_batch([bt_candidate], ...)`) — the BHY/FDR machinery runs even for N=1, satisfying AC-3.2 structurally. No Composer API key → `no_api_key=True`, writes nothing (AC-X4). Never raises on backtest, LLM, or gate failure (AC-X5/AC-6) — an LLM outage or a malformed tool-use payload degrades to a clean `NO_SURVIVORS_MESSAGE` result, not a crash.

**AC-X4 ordering (re-gate fix, `6e1eabcd`):** the Composer-key check runs BEFORE the `change_description` → `generate_reasoned_logic_candidates` resolution, not after. In the first GREEN pass this was reversed — a valid `ANTHROPIC_API_KEY` with a missing/invalid Composer key billed a real Anthropic call for a run that was always going to discard it and return `no_api_key=True`. `suggest_logic_changes` (below) already had this ordering correct; this function did not until the re-gate fix. Order now: (1) the neither-`tweak`-nor-`change_description` no-op branch (touches neither credential nor the LLM seam), (2) the Composer-key check, (3) `change_description` resolution via the reasoned generator. `run_id`/`provenance` are unaffected by the reorder — still minted unconditionally at the very top, before step (1).

**Incumbent OOS alpha (H5/H6/RC-1):** when `incumbent_oos_alpha` is not supplied, it is derived from a fold-matched baseline backtest (`_fold_transform_single` on the unchanged tree) rather than a full-history sum — this avoids biasing the gate toward `KEEP_INCUMBENT`. An explicit `incumbent_oos_alpha=0.0` is honored; only `None` triggers the fallback.

**New keyword-only parameters (R2-2):**
| Name | Type | Description |
|------|------|-------------|
| `reasoning_context` | `str \| None` | Ready-to-inject operator-context text block (see `ai_advisor.build_reasoning_context`), threaded verbatim to `generate_reasoned_logic_candidates`. The engine never calls `build_reasoning_context` itself — the caller (the route) builds it |
| `reasoning_manifest` | `dict \| None` | The honest per-source manifest paired with `reasoning_context`; stamped into `provenance["evidence_injected"]` and persisted on the observation this run writes |
| `run_id` | `str \| None` | Optional caller-supplied run id, used verbatim instead of minting a fresh UUID4 |

### `suggest_logic_changes(symphony_id, score_tree, objective, *, incumbent_oos_alpha=None, default_oos_alpha=0.0, baseline_stats=None, reasoning_context=None, reasoning_manifest=None, run_id=None) → LogicChangeRunResult`

Evaluate advisor-suggested objective-directed candidates (AC-3.1 + AC-3.2). **R2-2:** generates candidates via `generate_reasoned_logic_candidates` (LLM-reasoned, objective-directed — replaces the deleted fixed-multiplier `generate_objective_directed_candidates`), backtests each independently (AC-X5 — one candidate's failure never aborts the batch), then submits ALL successfully-backtested candidates as ONE `evaluate_candidate_batch` call.

**Live production caller (advisor-rewire cycle, Workstream C.1):** `advisors.weekly_suggestions_scheduler.run_weekly_logic_change_suggestions()` calls this once per live symphony, weekly, WITHOUT `reasoning_context`/`reasoning_manifest` (that call site is unchanged by R2-2, confirmed zero diff) — candidates are still LLM-reasoned, just without injected live operator context on that particular caller.

**AC-3.2 critical invariant:** never gate candidates individually — that silently disables the multiple-testing correction (raising N must raise the adjusted-p-value bar every candidate must clear). No Composer API key → `no_api_key=True` (AC-X4). Zero candidates or zero survivors are valid non-error outcomes.

**New keyword-only parameters (R2-2):** same `reasoning_context`/`reasoning_manifest`/`run_id` shape as `propose_operator_logic_change` above.

### `generate_reasoned_logic_candidates(symphony_id, raw_value, objective, *, reasoning_context=None, change_description=None, max_candidates=MAX_SUGGESTED_CANDIDATES) → list[LogicTweak]`

**NEW (R2-2) — the module's headline addition.** Generates a bounded set of LLM-REASONED `LogicTweak` candidates, replacing the deleted fixed-multiplier `generate_objective_directed_candidates` on the reasoned path. Makes a real Anthropic `messages.create` tool-use call (model from `model_config.get_advisor_suggestion_model()`, forced `emit_logic_edits` tool choice) with a prompt built by `_build_reasoned_generation_prompt` — the objective, an optional `reasoning_context` block, an optional `change_description` steering hint, and a bounded (`_MAX_PARAMS_LISTED_IN_PROMPT=40`) listing of the tree's actual tweakable `node_path`/`param_key`/`current_value` entries. Never a raw `json.dumps()` of the tree.

**SECURITY-CRITICAL resolution against the real tree:** each proposed edit's `node_path`/`param_key` is resolved via `_navigate_to_node` against the REAL `raw_value` tree — an edit that does not resolve to a real dict/key is DROPPED, never fabricated into a `LogicTweak`. The resulting `LogicTweak.old_value` is always read from the real tree at that path, **never** trusted from any `old_value` field the LLM's edit dict happens to include (the tool schema does not even define an `old_value` input field — an LLM-supplied one, if present, is silently ignored by construction).

D-1: never raises. `_build_client()` raising (no `ANTHROPIC_API_KEY`, SDK not installed), the SDK call raising, a response with no `tool_use` block, or a malformed `edits` payload (missing/non-list) all degrade to `[]` — surfaced upstream as a clean `NO_SURVIVORS_MESSAGE` result, never an exception.

### `apply_logic_tweak(raw_value, tweak) → dict | None`

Deep-copies `raw_value` and applies `tweak`. Returns `None` (invalid variant, never mutates the input) when the `node_path` cannot be navigated, the target node lacks `param_key`, or the current value does not match `tweak.old_value`. Unchanged in R2-2 — this is a NAVIGATION check only; it has no opinion on whether the resulting tree is a structurally valid Composer tree. See the `validate_tree` guard note under `_evaluate_single_variant` below for the net-new structural check R2-2 adds AFTER this one.

### `extract_numeric_params(raw_value) → list[dict]`

Recursively collects every numeric parameter node in the tree as `{"node_path", "param_key", "value"}`. Booleans are excluded (flags, not tunable parameters — checked before the numeric branch since `bool` is an `int` subclass). A current value of `0` or `1` does NOT make a param a flag — only genuine booleans are excluded. Unchanged in R2-2. **R2-2 new consumer:** this is also the parameter listing `generate_reasoned_logic_candidates` shows the LLM (bounded to the first `_MAX_PARAMS_LISTED_IN_PROMPT` entries).

## Internal Helpers

### `_build_client()`

**NEW (R2-2).** Constructs the `anthropic` SDK client. Factory seam: tests patch `logic_change_engine._build_client` — mirrors `ai_advisor._build_client` / `build_plan_generator._build_client`. Raises `RuntimeError` (caught by `generate_reasoned_logic_candidates`'s D-1 wrapper — never propagates) when `ANTHROPIC_API_KEY` is unset or the `anthropic` SDK is not installed (lazy `import anthropic`, CC-2, off-execution-path).

### `_build_reasoned_generation_prompt(objective, candidate_params, *, reasoning_context, change_description) → str`

**NEW (R2-2).** Assembles the LLM prompt for a reasoned generation call: `OBJECTIVE:` / optional `OBJECTIVE RATIONALE:` / optional `OPERATOR STEERING HINT:` (the `change_description`) / optional `reasoning_context` block verbatim / a bounded `CANDIDATE TWEAKABLE PARAMETERS` listing (`_MAX_PARAMS_LISTED_IN_PROMPT=40` entries max) / an instruction to call `emit_logic_edits` citing only listed `node_path`/`param_key` values. Bounded regardless of the real tree's size (AC-10) — never scales with tree size.

### `_navigate_to_node(raw_value, node_path) → Any`

Walks `node_path` from the tree root; returns `None` on any invalid step (`KeyError`/`IndexError`/`TypeError`). Unchanged in R2-2. **R2-2 new consumer:** the security-critical resolve-against-real-tree step in `generate_reasoned_logic_candidates`.

### `_build_objective_rationale(tweak, objective) → str`

Per-objective human-readable sentence explaining why the tweak addresses the stated objective. Unchanged in R2-2 — still dispatches on `objective.objective_type` across the same five named branches plus a generic fallback; still never reads `measured_value` (AC-10).

### `_make_candidate_id(symphony_id, tweak) → str`

Builds `"{symphony_id}:{param_key}@{path}:{old}->{new}"` (`"root"` when `node_path` is empty). Unchanged in R2-2.

### `_evaluate_single_variant(raw_value, symphony_id, tweak, objective, symphony_name="") → tuple`

Backtests one variant. Returns `(BacktestCandidate | None, LogicChangeProposalResult, baseline_stats | None, baseline_returns_pct)` — a 4-tuple (the trailing `baseline_returns_pct` element lets callers reuse the already-computed baseline daily-returns series instead of a second, redundant baseline backtest, AC-13; this doc previously under-documented the signature as a 3-tuple — corrected here against the actual current source, not a change introduced this cycle).

**R2-2 (AC-3) — the `validate_tree` guard, a net-new safety check over `apply_logic_tweak`:** immediately after `apply_logic_tweak` succeeds (a NAVIGATION check only), the function calls `advisors.symphony_schema.validate_tree(variant_tree)`. If `validate_tree` returns any HARD structural errors, the variant is dropped BEFORE any backtest call, with a `backtest_error` message deliberately distinct from the "old_value not found" wording — `"...produced a structurally invalid tree (validate_tree: ...)"` — so an operator (or a log reader) can tell the two failure classes apart. This is genuinely net-new: `apply_logic_tweak` alone was sufficient when the generator was a fixed-percentage script that could only ever produce well-formed numeric substitutions; an LLM-reasoned edit can navigate to a real node yet still emit a value or shape that corrupts Composer grammar, so the guard exists specifically because the trust model of the generator changed.

`candidate` is `None` when the tweak is structurally invalid (old-value mismatch OR the new `validate_tree` guard) or the variant backtest failed (AC-X5 — isolated to this candidate, never aborts the batch).

### `_spy_returns_fn_for(symphony_id)`

Sources a real SPY OOS-fold baseline once per run/batch (AC-5/AC-25), mirroring `strategy_builder_engine.py:807-826`. Unchanged in R2-2.

### `_backtest_returns_from_tree(raw_value, symphony_id) → list`

Runs a backtest and returns the log-returns list; `[]` on failure. Unchanged in R2-2.

### `_persist_observation(symphony_id, proposal, gate_result, *, run_id="", evidence_injected=None) → None`

Writes one `advisor_observation` row (`advisor_role="LOGIC_CHANGE"`, `observation_type="logic_change_proposal"`, `is_advisory_only=1`) carrying the ACTUAL gate verdict — regardless of ADOPT/KEEP/REJECT (RC-4). **R2-2:** gains keyword-only `run_id=`/`evidence_injected=` (AC-7) — both additive traceability keys written into `raw_response` (a free-form JSON blob column, no schema migration needed), mirroring `strategy_builder_engine.py`'s identical persistence pattern. `raw_response` carries the tweak, objective, gate decision, validation days, OOS alpha, caveats, `run_id`, and `evidence_injected` for audit.

### `_has_composer_key() → bool`

Local import of `alpha_bot_execution.COMPOSER_KEY_ID` / `COMPOSER_SECRET` (deferred import — not a module-level dependency on the execution engine, satisfying AC-X2). Returns `False` on any exception. Unchanged in R2-2.

### `_empty_gate_batch() → GatedBatch`

Sentinel empty `GatedBatch` (zero candidates, zero survivors) for the no-API-key / empty-candidate paths. Unchanged in R2-2.

## Bug Fix — Direction-Aware Fallback (DE-LOGIC-CHANGE-DIRECTION-001)

> **SUPERSEDED BY DELETION (R2-2, `DE-ADVISOR-R2-2-001`, 2026-07-14).** Both functions this historical fix touched — `_parse_change_description_to_tweak` and `_fallback_direction_factor` — no longer exist; they were deleted along with the rest of the fixed-multiplier generator family when the reasoned path replaced them (see the Overview above). The narrative below is preserved for historical/audit traceability (it documents a real production bug and its fix, and is still the accurate record of what happened in that cycle) but no longer describes any code that ships. The class of defect it fixed — a flat, direction-blind fallback multiplier misreading "reduce" as an increase — is now structurally different in kind: `generate_reasoned_logic_candidates` reads the operator's `change_description` and the tree's REAL current values directly into an LLM prompt rather than pattern-matching a fixed keyword list against a hardcoded multiplier, so there is no equivalent "flat fallback direction" step left to regress in the same way. This does not mean the reasoned path cannot get direction wrong in some other way (an LLM's output is not formally verified for directional correctness) — only that the SPECIFIC fixed-multiplier defect class documented below is moot because its code is gone.

**The bug:** `_parse_change_description_to_tweak`'s Phase 3 (preferred-key fallback) and Phase 4 (first-numeric-parameter fallback) both applied a flat `old_val * 1.20` (unconditional +20% increase) whenever the operator's `change_description` had no explicit `"from X to Y"` numbers — regardless of what the description said. Live-verified regression: `"reduce the window size"` on `old_value=10` produced `new_value=12`, an **increase** despite the word "reduce". This reached the operator-initiated live path through `propose_operator_logic_change(change_description=...)` whenever the operator described a change in words without giving explicit numbers.

**The fix (at the time):** `_fallback_direction_factor(desc_lower)` scanned the full description for reduce/lower/decrease/shrink → a 0.80 factor, or increase/raise/grow → a 1.20 factor, applied identically in both Phase 3 and Phase 4. `old_value=10` with `"reduce the window size"` yielded `new_value=8` (`round(10 * 0.80)`), not `12`.

**Blast radius (at the time):** confined to the plain-text-description fallback paths. `generate_objective_directed_candidates` (the advisor-suggested candidate generator, itself since deleted) was never affected — its five named scaling factors already carried objective-correct signs. Phases 1–2 of the parser (explicit `"from X to Y"` numbers) were never affected — direction was inherent to the operator's stated values.

## Design Invariants

### M4 invariants (`feature-plans/ai-advisor.md` §M4) — unchanged in R2-2

| Code | Invariant |
|------|-----------|
| AC-3.1 | Both modes (operator-initiated tweak; advisor-suggested candidates) diagnose → propose → backtest → gate → surface survivors |
| AC-3.2 | N backtested candidates → ONE FDR/multiple-testing correction across the FULL set; per-candidate gating is a test FAIL |
| AC-3.3 | Every surfaced logic-change carries `SURVIVOR_OVERFITTING_CAVEAT` + post-correction gate verdict |
| AC-3.4 | Never auto-applies — `apply_guidance` is plain text; writes only an advisory observation |
| AC-X1 | No Composer write endpoint call — only `GET /score` + stateless `POST /api/v0.1/backtest` |
| AC-X2 | `alpha_bot_execution.py` does not import this module; this module's own top-level imports never include `alpha_bot_execution` directly (local import inside `_has_composer_key` only) — see the R2-2 accepted-transitive-import note below for the one caveat this precise wording exists to scope |
| AC-X3 | Every surfaced recommendation persists as an `advisor_observation` with `is_advisory_only=1` + `symphony_id` |
| AC-X4 | No Composer API key → `no_api_key=True`, writes nothing, never an unhandled error |
| AC-X5 | Per-candidate backtest failure produces a failure marker; never aborts the batch |
| RC-4 | Persistence is verdict-agnostic — a `KEEP_INCUMBENT`/`REJECT_VETO_FAILED` proposal is still persisted |
| RC-5 | A persistence failure is surfaced via `LogicChangeRunResult.persistence_error`, never swallowed |

### R2-2 invariants (`feature-plans/advisor-r2-2-logic-changes.md`) — new this cycle

| Code | Invariant |
|------|-----------|
| AC-1 | Real operator context (rendered tree + live stats + 5 lens blocks) injected into the reasoning prompt when the caller supplies it — never a raw JSON dump |
| AC-2 | The reasoned generator's proposals are directionally consistent with the stated objective — a generator that ignores `objective` is a test FAIL (adversarial: two objectives on the same tree must produce different edits) |
| AC-3 | Every proposed edit is applied to a deep copy and re-validated via `validate_tree` before backtest; a structurally-invalid edit is dropped with an honest reason, never fabricated or backtested |
| AC-4 | Gate byte-unchanged and batch-corrected — all successfully-backtested variants gated as ONE `evaluate_candidate_batch` call, never per-candidate |
| AC-5 | `provenance {generation_model, mode, evidence_injected, run_id}` present on every return path, real 4-key dict, never `None`, never fabricated |
| AC-6 | Honest degradation — a tree-fetch failure, cold/stale lens cache, or LLM unavailability degrades the manifest/result honestly; never raises (D-1) |
| AC-7 | `run_id` minted once per run, returned in `provenance`, AND written into every persisted `advisor_observations.raw_response` for that run |
| AC-8 | Provenance + attribution rendered in the tab (`lc-live-generation-provenance` testid); the stale "Deterministic — no AI reasoning" tab label is gone for the reasoned path — see `docs/generated/app.md` and `templates/ai_advisor.html` |
| AC-9 | Advisory-only + off-execution-path + CSRF unchanged + not in `_SETTINGS_WRITE_ALLOWLIST` + D-1 error tokens stay `type(exc).__name__` — preserved |
| AC-10 | Credential-less mocked-green (all cred vars `""`, not unset) + bounded prompt (`_MAX_PARAMS_LISTED_IN_PROMPT=40` regardless of tree size) |

## R2-2 accepted transitive import (mirrors `DE-ADVISOR-R2-1-001` Finding-2)

`logic_change_engine.py` gained a new module-level `import ai_advisor` in R2-2 (for `build_reasoning_context`'s default-manifest constant, `ai_advisor._EMPTY_MANIFEST`, used as the fallback `evidence_injected` value when the caller supplies no manifest). This transitively imports `alpha_bot_execution` at module-load time via the SAME chain `DE-ADVISOR-R2-1-001` already found and accepted for `strategy_builder_engine.py`: `ai_advisor.py`'s own `import symphony_logic` → `symphony_logic.py`'s `from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers`. ACCEPTED for the identical three reasons R2-1 already established (import-only/no cycle — verified independently here too: `alpha_bot_execution.py` does not import `logic_change_engine` anywhere; not a new dependency, only a new path to an existing one; Architecture Constraint #1 stays intact because `logic_change_engine` itself is lazy-imported inside the route handler, CC-2). This is now a SECOND module carrying the same accepted transitive path — the AC-X2 wording above ("this module's own top-level imports never include `alpha_bot_execution` directly") is scoped precisely to remain true despite it.

## Internal Dependencies

- `ai_advisor` — **NEW (R2-2), module-level import.** `_EMPTY_MANIFEST` (fallback `evidence_injected` default). The engine never calls `ai_advisor.build_reasoning_context` itself — the route builds `reasoning_context`/`reasoning_manifest` and passes them in
- `model_config` — **NEW (R2-2), module-level import.** `get_advisor_suggestion_model()` — the `provenance["generation_model"]` value and the model used by the reasoned generator's `messages.create` call
- `uuid` — **NEW (R2-2), stdlib.** `run_id` minting
- `database` — `insert_advisor_observation`
- `advisors.backtest_gate_engine` — `HARVEY_LIU_FDR_Q`, `SURVIVOR_OVERFITTING_CAVEAT`, `BacktestCandidate`, `CandidateGateResult`, `GatedBatch`, `_fold_transform_single`, `evaluate_candidate_batch`
- `advisors.composer_backtest_client` — `run_backtest`
- `advisors.symphony_schema` — **NEW call site (R2-2).** `validate_tree` — the net-new structural guard on every edited tree (`make_root`/`make_weight_equal`/`make_asset` are also used, unchanged since before R2-2, inside `_spy_returns_fn_for`'s synthetic SPY tree)
- `alpha_bot_execution` — `COMPOSER_KEY_ID`, `COMPOSER_SECRET` (local import inside `_has_composer_key`, AC-X2 boundary) — see the R2-2 accepted-transitive-import note above for the SEPARATE, already-accepted path this module now also carries via `ai_advisor`
- `advisors.weekly_suggestions_scheduler` — the sole live production caller of `suggest_logic_changes` (Workstream C.1); its call site is unaffected by R2-2 (confirmed zero diff)
- `anthropic` (third-party SDK) — **NEW (R2-2), lazy-imported inside `_build_client`** (CC-2, off-execution-path)

No import of `app`; off-execution-path; advisory-only.
