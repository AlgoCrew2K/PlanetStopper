# advisors/frontrunner_builder

> Orchestrates the Frontrunner Builder pipeline: detect the incumbent frontrunner overlay, generate a candidate via Fable, splice it into the symphony, independently re-backtest and gate both sides, apply Calmar acceptance, and queue survivors for operator approval.

**Source:** `advisors/frontrunner_builder.py`
**Last updated:** 2026-07-11 (Wave-1 backend, frreview-APPROVED, P2-1 iterative-traversal hardening landed at `26c1364`)

## Overview

`advisors/frontrunner_builder.py` is the orchestration layer of the Frontrunner Builder (feature-plans/frontrunner-builder.md). Per live symphony, the pipeline is:

**detect** (`frontrunner_detector`) → **gather Atlas patterns** (`community_strats`, 7-day cache, once per run) → **generate** a candidate overlay via **Fable** (`claude-fable-5`) → **compile** (`plan_tree_compiler`) → **splice** into the incumbent symphony → **independently re-backtest BOTH incumbent and candidate** (`composer_backtest_client`) → **gate** (`backtest_gate_engine.evaluate_candidate_batch`, mandatory, never bypassed) → **Calmar acceptance** (`frontrunner_acceptance`) → **queue for operator approval** (`database.insert_frontrunner_proposal`).

This is wave-1 **backend**. The pipeline is already wired into the existing **weekly** scheduler (`advisors/strategy_builder_scheduler.run_weekly_build` calls `run_frontrunner_build()` over all live symphonies after the four Strategy-Builder objectives complete, AC-1) and the `propose_strategies` **retrofit** already queues its own accepted candidates onto the same `frontrunner_proposals` table (`proposal_source="strategy_builder_retrofit"`, AC-10). What's still missing (wave-2): the on-demand `POST /ai-advisor/frontrunner-builder/run` route, the `/approve`/`/reject` routes, and the new Advisor-tab UI that surfaces pending proposals and lets the operator actually click Approve/Reject — see "Not Yet Built" below.

**Review status:** `frreview` (quant-code-reviewer) reviewed the full `0bcbd1a..4daf0fe` wave-1 backend diff (28 commits, 32 files, +7304/-2) and returned **APPROVE** — no P0/P1 findings. Three non-blocking P2 items were dispositioned before doc-writing: P2-1 (iterative-traversal hardening, landed `26c1364`, see Internal Mechanics below), P2-2 (a landmine comment on the Atlas-hoist call site, landed `07bdc8c`, see Internal Mechanics below), and a pre-existing unrelated test-hygiene item (2 stale skips in `test_community_strats.py`, out of scope for this cycle).

Off-execution-path, advisory-only. Never raises anywhere on the module's public surface (D-1) — a per-symphony or per-candidate failure is logged and skipped, never aborting the batch.

### No-auto-trade boundary (structural)

This module never calls `composer_draft_client.save_symphony` from the unattended build/run path (`run_frontrunner_build` → `_run_build_for_symphony`) — **only** `approve_frontrunner_proposal`, the operator-driven approval function, may do that. It does not implement, import, or reference `invest_in_symphony` or any `/deploy/` endpoint. Enforced both structurally (by omission — see `advisors/composer_draft_client.py`) and by an adversarial source-scan security suite, `tests/security/test_frontrunner_no_trade_boundary.py` (10 tests), which fails if a future edit reintroduces an invest/deploy-shaped symbol, URL fragment, or a run-path call to `save_symphony`. **This boundary already matters today**, not just once a route exists: the weekly scheduler is live-wired and calls `run_frontrunner_build()` unattended every week — a candidate reaching the approval queue is the pipeline's actual current ceiling; nothing uploads to Composer without a human calling `approve_frontrunner_proposal` directly (currently only reachable via tests/a Python shell, since no route exists yet).

## Named Constants

| Name | Value | Purpose |
|------|-------|---------|
| `FABLE_MODEL` | `"claude-fable-5"` | Model used for candidate generation — operator directive |
| `MAX_OUTPUT_TOKENS` | `8192` | SDK call token ceiling; deliberately smaller than `build_plan_generator`'s (a single overlay is a small DSL fragment vs a full-symphony plan) |
| `MAX_GENERATION_ATTEMPTS` | `3` | Bounded retry for a truncated (`stop_reason="max_tokens"`) or rejected/degenerate candidate (AC-11) |
| `MAX_CASCADES_PER_SYMPHONY_RUN` | `40` | AC-12 Fable-call budget cap per symphony run. Verified against the 11 real trees (observed cascade counts: `{26, 12, 8, 4, 4, 3, 2, 1, 1, 1, 0}`, max=26); 40 gives ~1.5x headroom above the real max while still bounding a pathological/mis-parsed detection. Cascades beyond the cap are skipped with a logged reason, never silently dropped |
| `MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW` | `25` | AC-12 self-imposed runaway-creation safety valve on the approval→Composer-create path. **Not a Composer limit** — Composer documents no per-account symphony-count cap or create-time quota (Tier-1 OpenAPI + Tier-2 MCP + help-center triangulation, `composer-api-researcher`, 2026-07-11); `fetch_symphony_stats` is DEPLOYED-scoped and cannot see the undeployed symphonies this feature creates, so it cannot serve as the guard's denominator |
| `_DOF_LEDGER_SPEC_BUNDLE_SENTINEL` | `"frontrunner_builder"` | Belt-and-suspenders audit-legibility marker on DoF-ledger rows — **not** the isolation mechanism (see DoF-Ledger Isolation below) |
| `_TREE_SPLICE_PANEL_PARAMS_SENTINEL` | `{"tree_splice_candidate": 1.0}` | Identical non-empty param dict passed as candidate/incumbent/theory-prior params to the shared gate's discretionary panel — see Gate-Reachability Fix below |

## Public Types

### `GenerationResult` (dataclass)

Returned by `generate_candidate_overlay`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `candidate` | `dict \| None` | The accepted build-plan-DSL overlay node (`kind="if"`/`"if_compound"`), or `None` if rejected/failed |
| `error` | `str \| None` | Reason string on rejection/failure (D-1: `type(exc).__name__` on an internal error). `None` on success |
| `compiled_tree` | `dict \| None` | The compiled Composer tree via `plan_tree_compiler.compile_plan`, when compilation succeeded |

### `ApprovalResult` (dataclass)

Returned by `approve_frontrunner_proposal`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `success` | `bool` | `True` only when the Composer create succeeded AND the created symphony verified zero-allocation |
| `symphony_id` | `str \| None` | The newly-created (or previously-created, on idempotent re-approve) Composer symphony id |
| `error` | `str \| None` | Reason string on failure (D-1) |

## API Reference

### `generate_candidate_overlay(signal_context: dict, *, n_attempts=MAX_GENERATION_ATTEMPTS) -> GenerationResult`

Calls Fable (tool-use, `emit_frontrunner_overlay` tool, forced `tool_choice`) to compose one candidate frontrunner overlay DSL node, enforces AC-4's post-generation hard constraints, and compiles it. Never trusts the model's raw output.

**Enforced post-generation (AC-4):**
- **(a) VIX presence:** `_has_vix_ticker_in_fire_branch` — the candidate's `then` (fire) branch, or a nested tier's own `then` branch, must contain >=1 VIX-family ticker (`frontrunner_detector.VIX_FAMILY_TICKERS`, imported not duplicated). A candidate that fails this check is rejected and retried (bounded).
- **(c) Mergeable-rung collapse:** `_collapse_mergeable_rungs` walks nested `if` chains sharing an identical `(fn, comparator, rhs_fixed, window)` signature AND identical fire content, differing only in `lhs_ticker`, and collapses a chain of length >=2 into one `if_compound` node with a `binary_compound` `"any"` condition. A genuine scale-in tier (different threshold or different fire content per level) never matches this signature and is left untouched.
- **(d) Scale-in tiers preserved:** guaranteed by construction — the collapse function only touches structurally-identical chains.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `signal_context` | `dict` | `watched_tickers` (list, the incumbent cascade's core signal tickers) + optional `atlas_patterns` (list, AC-3) |
| `n_attempts` | `int` | Bounded retry count (default `MAX_GENERATION_ATTEMPTS`) |

**Returns:** `GenerationResult` — never raises (D-1).

**Degradation paths:** `"max_tokens: response truncated"` (all attempts truncated) · `"NoToolUseBlock"` · `"InvalidToolUsePayload"` · `"candidate fire branch contains no VIX-family ticker"` (AC-4a rejection, retried) · `type(exc).__name__` on any unexpected exception.

---

### `splice_candidate_into_symphony(incumbent_symphony: dict, incumbent_cascade, candidate: dict) -> dict | None`

Replaces the detected incumbent cascade subtree (identified by `incumbent_cascade.overlay_tree`'s node `id`) with the candidate, inside a full copy of the incumbent symphony (AC-5).

Accepts either a raw build-plan-DSL node (`"kind"` key — compiled here via `plan_tree_compiler.compile_plan`) or an already-compiled Composer node (`"step"` key — used as-is). Re-validates the spliced result via `symphony_schema.validate_tree` before returning.

**Returns:** the full spliced symphony dict, or `None` on any structural failure (node not found, compile failure, validation errors). Never raises.

---

### `run_frontrunner_build(symphony_ids: list[str] | None = None) -> None`

**D-1 never-raises entry point.** Called by `strategy_builder_scheduler.run_weekly_build()` (AC-1, weekly, already wired) and available for the not-yet-built on-demand route. Detects → generates → splices → gates → accepts → queues, for each live symphony. Never calls `composer_draft_client.save_symphony`.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `symphony_ids` | `list[str] \| None` | `None` (default): resolves the roster from live `bot_state` at run time via `_resolve_live_symphony_roster`. Supplied: restricts the build to those ids (used by the on-demand route / tests) |

A per-symphony exception is logged and skipped — it never aborts the batch.

---

### `approve_frontrunner_proposal(proposal_id: int) -> ApprovalResult`

**Operator-approved.** The ONLY function in the whole frontrunner surface that may call `composer_draft_client.save_symphony` (AC-9). Intended to be invoked exclusively from the operator-driven `/approve` route (wave-2, not yet built) — never from the unattended weekly build path. Today it is only reachable directly (tests / a Python shell) since no route calls it yet.

Sequence: look up the `frontrunner_proposals` row → idempotent no-op if already `uploaded` → **AC-12 local-count guard** (`database.count_uploaded_frontrunner_proposals()` against `MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW`; fails **closed** — refuses to create — both when the cap is reached and when the count itself can't be determined) → `composer_draft_client.save_symphony(...)` → **AC-9 belt-and-suspenders**: `composer_draft_client.verify_undeployed(symphony_id)` before marking `'uploaded'` → persists an `advisor_observations` audit row on success.

On ANY failure (proposal not found, cap reached, Composer 4xx/5xx, `verify_undeployed` returning `False`), the proposal is left un-marked `'uploaded'` and the failure reason is written to `frontrunner_proposals.error_message` (AC-11: "do NOT mark uploaded, do NOT retry blindly").

**Idempotent:** re-approving an already-`uploaded` proposal is a no-op that echoes the recorded `symphony_id` — no duplicate Composer symphony is created, via `save_symphony`'s own `already_uploaded_symphony_id` seam.

**Returns:** `ApprovalResult`. Never raises.

**Shared by both proposal sources.** `frontrunner_proposals.proposal_source` distinguishes `'frontrunner_builder'` rows (this module's own pipeline) from `'strategy_builder_retrofit'` rows (`advisors/strategy_builder_engine.py::_persist_survivor`, AC-10) — both flow through this exact same function, so there is one approval→create code path for the whole feature, not two.

## Internal Mechanics

### Batch-composition fix (2026-07-11)

The inherited WIP had put BOTH the incumbent and the candidate into the same `evaluate_candidate_batch` call — making them rivals for the single BHY/FDR-winner slot instead of candidate-vs-baseline (both landed `fdr_not_winner`, gate structurally dead). Fixed in `_gate_and_accept_candidate`: the batch (`bt_candidates`) carries **only** the candidate; the incumbent's own fresh backtest supplies `incumbent_oos_alpha` as the scalar `KEEP_INCUMBENT` baseline — the same shape as `strategy_builder_engine.propose_strategies`'s established usage.

### Gate-reachability fix — `_TREE_SPLICE_PANEL_PARAMS_SENTINEL`

`backtest_gate_engine`'s discretionary panel (`_compute_parameter_stability_score`/`_compute_prior_anchor_score`) was designed to compare an Optuna-tuned candidate's parameter vector against the incumbent's. A tree-splice candidate has none — passing **empty** `candidate_params`/`incumbent_params` was structurally disadvantageous, not neutral: the incumbent's own `inc_stability` is hardcoded to `1.0` ("stable against itself") while an empty-input pair falls back to the `0.5` neutral-prior short-circuit, giving the incumbent an unwinnable floor (`0.5` candidate panel score vs `0.75` incumbent, `margin 0.5>=0.75` mathematically impossible regardless of return quality — verified via a direct `evaluate_candidate_batch` probe). The feature would have shipped hollow (gate rejects all candidates in prod), caught pre-ship by `frtest`.

**Fix:** pass an **identical non-empty** dict (`_TREE_SPLICE_PANEL_PARAMS_SENTINEL`) for `candidate_params`/`incumbent_params`/`theory_prior_params` — every parameter-distance sub-score resolves to a genuine 1.0/1.0 N/A-tie, so the panel becomes a neutral pass-through for tree-splice candidates. The real vetoes (BHY/FDR significance, PBO, OOS-alpha-beats-both-baselines) remain fully load-bearing and unaffected. **PM-verified byte-unchanged for shared code:** `git diff f51cffe 8d0b18d -- acceptance_gate.py advisors/backtest_gate_engine.py autotuner.py` was empty — zero impact on the autotuner/strategy_builder call sites. `frreview` independently traced the same code path and confirmed `ADOPT_CANDIDATE` was provably unreachable pre-fix and that the sentinel produces a genuine tie, not a weakening. See "Gate-Reachability Fix" in `DECISIONS.md`.

### DoF-ledger isolation — `evidence_source="OVERLAY_BACKTEST_SELECTION"`

**This is the actual isolation mechanism** (not the `spec_bundle_id` sentinel). An earlier design attempted isolation via `spec_bundle_id` alone; that was proven false — `database.get_researcher_dof_ledger_for_run` excludes only rows matching the *current run's own winning bundle*, so any other `spec_bundle_id` (including a sentinel) still swept into every symphony's real N_effective, silently inflating the autotuner's BHY/Yekutieli overfitting haircut on every weekly frontrunner search. The real, verified isolation mechanism: every consumer that aggregates `researcher_dof_ledger` (`database.count_dof_backtest_selections`, `database.get_researcher_dof_ledger_for_run` — the production N_effective feed at `autotuner.py:2487`) filters on the literal string `evidence_source='BACKTEST_SELECTION'`. Writing frontrunner rows with the distinct value `"OVERLAY_BACKTEST_SELECTION"` excludes them from every such consumer by construction — zero schema/query change. `_DOF_LEDGER_SPEC_BUNDLE_SENTINEL` is kept as belt-and-suspenders audit legibility only. Verified by a real-DB (non-mocked) integration suite; `frreview` independently re-traced the SQL filters at review and confirmed the exclusion. See "DoF-Ledger Isolation" in `DECISIONS.md`.

### Atlas corpus — once-per-run hoist (AC-3/AC-12)

`_gather_atlas_frontrunner_patterns` loads the shared weekly-cached Atlas corpus (`community_strats.load_community_strategies()`), reuses `frontrunner_detector.detect_frontrunner_cascades` structurally against each Atlas candidate's tree, and extracts pattern dicts (`vix_tickers`, `rsi_thresholds`, `watched_tickers`, `basket_node_count`) — **never** `oos_metrics`/`sharpe` (AC-3: "never trusts incoming oos_metrics.sharpe"). Called **once per symphony run**, not once per cascade — an earlier version called it inside the per-cascade loop, which made every unmocked test attempt up to `MAX_CASCADES_PER_SYMPHONY_RUN` live Atlas/Mongo fetches (the "hitting Mongo" failure mode this hoist fixes; also a correct production efficiency win regardless of test exposure).

`watched_tickers=[]` is passed at the current (hoisted) call site — **intentional, not a placeholder**: the function's `watched_tickers` param is currently unused (no ticker-relevance filtering implemented). **Landmine flagged at review (P2-2, landed `07bdc8c`, documented in-source):** because the call is now run-scoped rather than cascade-scoped, if ticker-relevance filtering is ever wired against that param, this call site is the one that must be updated to pass real tickers — otherwise filtering silently no-ops forever with an empty list.

### `_gate_and_accept_candidate` — the AC-6/AC-7 decision function

Independently re-backtests both incumbent and candidate (never trusts the incumbent's stored `oos_metrics`), runs the candidate through `evaluate_candidate_batch`, records the search-breadth DoF row regardless of verdict, and — only for the gate's `ADOPT_CANDIDATE` survivor — applies `frontrunner_acceptance.evaluate_calmar_acceptance`. Returns `(accepted: bool, metrics: dict)`; on any reject path the metrics dict still carries the incumbent-vs-candidate CAGR/MDD/node-count deltas (when available) so AC-11's "rejected item w/ reason+deltas" can be persisted as an audit observation — a pre-gate backtest failure has no valid comparison data and stays a log-only skip.

### Traversal style (P2-1, landed `26c1364`)

`_count_tree_nodes` (this module) is **iterative** (explicit-stack), mirroring `symphony_schema.py`'s established pattern and the equivalent fix in `frontrunner_detector.py` (`_count_nodes`/`_collect_tickers`/`_find_cascade_roots` — see that module's doc). This closed a real gap found at code review, not a hypothetical: `frtest`'s empirical probe (`2df4ca6`) confirmed the pre-fix recursive version raised `RecursionError`, uncaught, on a synthetic 3,000-deep tree — its sole production caller (`_gate_and_accept_candidate`) happened to wrap the whole call in try/except, so the pre-fix blast radius was a logged reject with `reason="RecursionError"`, but the function itself provided no safety net of its own. Behavior-preserving; verified against the full frontrunner test surface with zero regressions.

## Testing

- `tests/advisors/test_frontrunner_builder.py` — 8 tests (generation constraints, splice, run-entrypoint behavior)
- `tests/advisors/test_frontrunner_gate_wiring.py` — 13 tests (gate-reachability fix, cascade-cap, AC-11 rejected-observation, batch composition)
- `tests/advisors/test_frontrunner_atlas_patterns.py` — 9 tests (AC-3 Atlas wiring, once-per-run hoist)
- `tests/advisors/test_frontrunner_dof_isolation.py` — 4 tests (real-DB, non-mocked: all 4 polluting `researcher_dof_ledger` consumers proven to exclude frontrunner rows / include autotuner rows unchanged)
- `tests/advisors/test_frontrunner_approval.py` — 8 tests (`approve_frontrunner_proposal` orchestration, AC-12 upload-cap guard, idempotency)
- `tests/advisors/test_frontrunner_deep_tree_hardening.py` — 5 tests (P2-1: 3 depth-hardening tests against a 3,000-deep synthetic tree + 2 regression guards on the public D-1 boundary/skip-reason contract; shared with `frontrunner_detector` — see that module's doc)
- `tests/security/test_frontrunner_no_trade_boundary.py` — 10 tests (adversarial source-scan: no invest/deploy symbol or URL fragment anywhere in the frontrunner surface, `run_frontrunner_build` never calls `save_symphony`)
- `tests/test_no_live_mongo_guard.py` — 4 tests (session-autouse guard: any live `pymongo.MongoClient` construction under pytest fails loud, real-money-critical — composes suite-wide with `community_strats`/`atlas_cache`)

**PM-authoritative gate run** (quiet window, `-n0`, unique `DB_PATH`, process table checked clear before running) on `26c1364`: **207 passed, 2 skipped, 44.31s** across the 9 frontrunner files + `test_no_live_mongo_guard.py` + `test_community_strats.py` + `test_atlas_cache.py` (+5 vs the prior 202 = the new deep-tree hardening file). The 2 skips are pre-existing/stale (`test_community_strats.py` claiming the module doesn't exist — it does; unrelated to this cycle, not fixed here). Warnings are benign quantstats divide-by-zero on zero-max-drawdown edges.

## Internal Dependencies

- `advisors.plan_tree_compiler`, `advisors.symphony_schema` — module-level imports (compile + validate)
- `advisors.frontrunner_detector` — `VIX_FAMILY_TICKERS` (module-level import) + `detect_frontrunner_cascades` (CC-2 lazy import inside `_run_build_for_symphony`)
- `advisors.community_strats`, `advisors.frontrunner_detector` — CC-2 lazy imports inside `_gather_atlas_frontrunner_patterns`
- `advisors.backtest_gate_engine` (`BacktestCandidate`, `evaluate_candidate_batch`), `advisors.composer_backtest_client` (`run_backtest`), `advisors.frontrunner_acceptance` (`evaluate_calmar_acceptance`), `analytics` (`compute_quantstats_metrics`) — CC-2 lazy imports inside `_gate_and_accept_candidate`
- `database` — CC-2 lazy imports throughout (`load_state`, `insert_dof_ledger_row`, `insert_advisor_observation`, `insert_frontrunner_proposal`, `get_frontrunner_proposal`, `count_uploaded_frontrunner_proposals`, `update_frontrunner_proposal_status`)
- `symphony_logic` — CC-2 lazy import inside `_run_build_for_symphony` (`fetch_symphony_score`)
- `advisors.composer_draft_client` — imported at module scope inside `approve_frontrunner_proposal` only (never referenced from the build/run path)
- `anthropic` SDK — lazy-imported inside `_build_client` (factory seam, mirrors `build_plan_generator._build_client`)

**Reverse dependencies (who calls into this module):**
- `advisors/strategy_builder_scheduler.py::run_weekly_build` — calls `run_frontrunner_build()` unconditionally after the four Strategy-Builder objectives complete (AC-1, weekly, isolated in its own try/except so a frontrunner failure never blocks the objective loop above it)
- `advisors/strategy_builder_engine.py::_persist_survivor` — does NOT call anything in this module directly; it writes its own `frontrunner_proposals` row via `database.insert_frontrunner_proposal(proposal_source="strategy_builder_retrofit")`, which later flows through THIS module's `approve_frontrunner_proposal` on operator approval (AC-10)

## Not Yet Built (wave-2)

- `POST /ai-advisor/frontrunner-builder/run` on-demand trigger route (`app.py`) — `run_frontrunner_build` itself is ready to be called from it
- `POST /ai-advisor/frontrunner-builder/approve` / `/reject` routes (`app.py`) — `approve_frontrunner_proposal` itself is ready to be called from it; a reject path (`database.update_frontrunner_proposal_status(..., approval_status="rejected")`) has no dedicated helper function yet either
- The new Frontrunner-builder Advisor tab (`templates/ai_advisor.html`, `static/ai_advisor.js`) — no template/JS references `frontrunner` anywhere yet; pending proposals are only queryable via `database.get_pending_frontrunner_proposals`
- Operator-gated task-zero live `save_symphony` test (one real create against the operator's Composer account, verify-undeployed, delete) — required before `approve_frontrunner_proposal` is exercised against the real Composer API for the first time

**Already wired (not wave-2, contrary to the feature plan's own phrasing which reads as if these were still pending):** the weekly scheduler cadence (AC-1) and the `propose_strategies` retrofit's proposal-queuing (AC-10, the "closes an existing gap" summary-line goal). Both call the exact same backend this doc describes; what's missing for them is purely the UI surface (AC-8) that lets the operator act on what they already queue.
