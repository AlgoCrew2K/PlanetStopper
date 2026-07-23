# Advisor R2-3 — Asset Swaps: genuinely reasoned, objective-directed swap generation

**Status:** ready
**Owner:** PM (Gate-1 + Gate-2 resolved [PM-ASSUMED] under vacation-autonomy)
**Cycle:** R2-3 (third and final sub-cycle of the R2 "make the AI Advisor genuinely reason" program)
**Branch:** `feature/advisor-r2-3-asset-swaps` (off origin/main `fe3d9754`)
**Precedent:** R2-1 (Strategy Builder, shipped) + R2-2 (Logic Changes, shipped @ origin/main `fe3d9754`)
**Advisory-only, off-execution-path.** Ships FF-direct to origin/main after the PM gate.
**ALL-NEW LLM CODEPATH → Toxic Pair (Quint) team.**
**Investigated at:** fe3d9754

## PM-ASSUMED resolutions (vacation-autonomy — operator may redirect)

- **[PM-ASSUMED Q1] Reason the OPERATOR route (Option a).** The reasoned generator lives on
  `propose_operator_swap` — the surface the operator actually sees (the tab + the label at
  `templates/ai_advisor.html:1054`) — so flipping that label off "Deterministic — no AI reasoning"
  is HONEST (R2's north star). The route gains an objective-directed reasoned-suggestion mode:
  operator selects symphony + objective; the LLM proposes incumbent→candidate swap PAIRS over the
  real holdings + a validated candidate universe; the gate is unchanged. The existing explicit
  `from_ticker`/`to_ticker` pair is RETAINED as an optional constraint/steering hint (both tickers
  supplied → that exact pair is evaluated, byte-preserved; objective-only → LLM proposes) — mirrors
  R2-2 retaining `change_description` as a hint. Both entry points (operator route + weekly
  `suggest_swaps`) route through the reasoned generator. *(Rationale: option b would make the 1054
  label flip DISHONEST — the operator-facing surface would still be deterministic. Operator can
  redirect to keep operator mode explicit-pair-only.)*
- **[PM-ASSUMED Q2] LLM proposes full (incumbent→candidate) swap PAIRS** — the closest R2-2 mirror
  (choosing which held ticker to swap OUT is itself a reasoning act). `_select_incumbent_asset`'s
  deterministic pick is superseded on the reasoned path.
- **[PM-ASSUMED Q3] Candidate universe = LLM-proposes-then-VALIDATE.** The LLM proposes candidate
  tickers informed by the injected reasoning context; each proposed ticker is VALIDATED against
  `advisors.universe_provider.get_tradeable_set()` membership (drop non-members — never inject the
  full ~12.7k-symbol set into the prompt) + Composer `/backtest` as the final tradeability arbiter.
  Caller-supplied `available_assets`/scheduler `base_pool` honored as an additional constraint when
  present. Mirrors R2-2's "never trust LLM-supplied values; validate against ground truth."
- **[PM-ASSUMED Q4] Keep `correlation_data` as prompt EVIDENCE; DELETE the deterministic generator.**
  `correlation_data` stays computed + injected (real evidence informing the LLM's reasoning), but
  SELECTION is the LLM's, not the fixed statistical sort. `generate_objective_directed_candidates`
  is DELETED (mirror R2-2's full replacement of its fixed-multiplier generator; coding standard =
  delete unused, no dead deterministic path). LLM-unavailable → honest zero-survivor degradation
  (AC-6), NEVER a silent fallback to the deterministic sort (a deterministic fallback under a
  "reasons" label is the exact anti-pattern R2 targets).
- **[PM-ASSUMED Q5] Scheduler passes `reasoning_context=None`** (mirror R2-2): weekly `suggest_swaps`
  candidates are still LLM-reasoned, but without per-symphony `build_reasoning_context` fan-out
  across the unattended nightly sweep (cost bound).

## Summary

The Asset Swaps tab is the last AI-Advisor capability still honestly labelled
**"Deterministic — no AI reasoning"** (`templates/ai_advisor.html:1054`). R2-3 makes it
genuinely reason, mirroring the R2-2 (Logic Changes) shape byte-for-byte where the shapes align.

**What Asset Swaps does today (the deterministic path):**

* `advisors/asset_swap_engine.py:525` `generate_objective_directed_candidates(symphony_id,
  objective, correlation_data, available_assets, lens_scores=None)` is the deterministic generator:
  ranks `available_assets` by hand-computed statistics vs `correlation_data`
  (`reduce_correlation` → ascending abs Pearson; `reduce_drawdown` → ascending variance;
  `lift_risk_adjusted` → descending pseudo-Sharpe; unknown → unchanged). Lens evidence blended via
  `_apply_lens_blend` (`LENS_BLEND_WEIGHT=0.25`). No LLM.
* `_select_incumbent_asset` (`:1402`) deterministically picks which held ticker to swap out.
* The generator is consumed ONLY by `suggest_swaps` (`:1193`) + the weekly scheduler
  (`advisors/weekly_suggestions_scheduler.py:385`).
* The **operator** route (`propose_operator_swap`, `:1029`) does NOT call the generator — the
  operator supplies both `incumbent_asset` and `candidate_asset`; the engine backtests+gates that pair.

**What R2-3 changes (mirror of R2-2, per [PM-ASSUMED Q1]):**

* Introduce an LLM-reasoned, objective-directed candidate generator that proposes swap PAIRS over
  the operator's REAL holdings + a validated tradeable candidate universe, consuming
  `ai_advisor.build_reasoning_context` (R1 shared enabler).
* Route it into BOTH the operator route (new objective-directed mode) and the `suggest_swaps`/weekly
  path; retain explicit-pair as an optional constraint/hint on the operator route.
* Keep the FDR/PBO/SPY-OOS acceptance gate (`backtest_gate_engine.evaluate_candidate_batch`,
  `_spy_returns_fn_for`, `_fold_transform_single`) BYTE-UNCHANGED.
* Add the Design-B 4-key `provenance` + `run_id` to the run result + route JSON, on EVERY return path.
* Add a NEW distinct provenance/attribution UI block (`data-testid="as-live-generation-provenance"`)
  and flip the tab-attribution label at `templates/ai_advisor.html:1054` — leaving 1064/1074/1084 intact.
* Mock the LLM + Composer seams; ZERO live API calls (verified by an execution-level seam detector).

## Exact surfaces R2-3 touches (re-verified at fe3d9754)

| Surface | Location |
|---|---|
| Deterministic generator to replace (DELETE per Q4) | `advisors/asset_swap_engine.py:525` `generate_objective_directed_candidates` |
| Advisor-suggested entry point (consumes generator) | `advisors/asset_swap_engine.py:1193` `suggest_swaps` |
| Operator-initiated entry point (gains reasoned mode) | `advisors/asset_swap_engine.py:1029` `propose_operator_swap` |
| Tree-mutation helper | `advisors/asset_swap_engine.py:379` `apply_ticker_swap` |
| Single-variant backtest+shell | `advisors/asset_swap_engine.py:903` `_evaluate_single_variant` |
| Gate (must stay byte-unchanged) | `advisors/backtest_gate_engine.py` `evaluate_candidate_batch`; `_spy_returns_fn_for` (`asset_swap_engine.py:880`); `_fold_transform_single` |
| Operator route (tab UI + provenance target) | `app.py:4337` `POST /ai-advisor/asset-swaps/evaluate` → `propose_operator_swap` |
| Weekly scheduler call site | `advisors/weekly_suggestions_scheduler.py:313` → `suggest_swaps` (`:385`) |
| Asset-swaps JS render (add provenance block) | `static/ai_advisor_asset_swaps.js` (`renderResults` `:298`, `renderSwapCard` `:112`) |
| Tab attribution label to flip | `templates/ai_advisor.html:1054` |
| Candidate universe source (per Q3) | `advisors/universe_provider.py` `get_tradeable_set()` |
| Shared enabler (reused verbatim) | `ai_advisor.build_reasoning_context` (`ai_advisor.py:1700`); `ai_advisor._EMPTY_MANIFEST` (`ai_advisor.py:70`); `model_config.get_advisor_suggestion_model()` |

## Key de-risking findings

1. **`asset_swap_engine` already edits REAL trees over REAL holdings** (like logic_change's Option B):
   both routes fetch the real `score_tree` via `symphony_logic.fetch_symphony_score`, deep-copy it,
   mutate ticker strings, backtest. The "reason over the operator's actual tree" premise holds.
2. **"apply edit + validate_tree" analog exists but is weaker.** `apply_ticker_swap` only substitutes
   a ticker STRING → a structurally valid input tree stays valid; the genuine validity arbiter is
   TRADEABILITY (Composer `/backtest`, per `universe_provider`). A `symphony_schema.validate_tree`
   guard is included as the R2-2-consistent structural mirror (cheap insurance), NOT relied on for
   tradeability.
3. **The gate is intact and must stay byte-unchanged** — only CANDIDATE GENERATION upstream changes.
4. **`build_reasoning_context` passes cleanly with a `SwapObjective`** — its `objective` param is a
   documented no-op (`ai_advisor.py:1729-1731`); no enabler change needed.
5. **The operator route returns NO provenance today** (`app.py:4409-4495`) — R2-3 adds it
   (route-minted default on every early/error return + engine's real 4-key on success behind
   `getattr+isinstance(dict)`), mirroring the R2-2 route.

## Acceptance Criteria

AC-1 — **Real reasoning context injected.** The reasoned generator consumes
`build_reasoning_context(symphony_id, objective, composer_symphony_id=composer_hash)` and injects the
bounded context (real rendered tree + live stats + 5 lens blocks) into the LLM prompt. NEVER a raw
`json.dumps` of the tree; bounded regardless of tree size. Test: prompt contains injected context +
stays under a bounded size for a pathologically large tree.

AC-2 — **LLM proposes objective-directed swap PAIRS over REAL holdings + a REAL candidate universe.**
For a given `SwapObjective`, the LLM (structured tool-use, mocked) proposes (incumbent→candidate)
pairs where the incumbent is a ticker actually present in the operator's tree and the candidate is
validated against the real tradeable set; different objectives can produce different pairs (an
objective-ignoring generator is a test FAIL). Selection is no longer a fixed statistical sort.

AC-3 — **Structural re-validation of the swapped tree.** After `apply_ticker_swap`, the variant tree
is passed through `symphony_schema.validate_tree` before backtest; a variant that fails validation is
dropped with an honest, distinct reason, never fabricated. (Tradeability remains Composer-enforced.)

AC-4 — **Gate byte-unchanged + batch-corrected.** `evaluate_candidate_batch`, `_spy_returns_fn_for`,
`_fold_transform_single`, the BHY-FDR `n_effective=N` batch call, and the PBO/SPY-OOS veto wiring are
unchanged. All backtested candidates in a run are gated as ONE batch; a test asserts they are never
gated individually.

AC-5 — **Provenance object on the run result, every path.** `SwapRunResult` gains a 4-key
`provenance {generation_model (from `model_config.get_advisor_suggestion_model()` at call time),
mode="asset-swap", evidence_injected (the manifest, default `ai_advisor._EMPTY_MANIFEST`), run_id}`
plus a `run_id`, minted once per call, present on EVERY return path (no-api-key, empty-candidates,
backtest-fail, success). `provenance` never None, never fabricated.

AC-6 — **Honest degradation, never fabricated.** LLM unavailable / malformed / no resolvable
candidate → zero survivors + `NO_SURVIVORS_MESSAGE` + populated provenance/run_id; never a crash,
never a fabricated swap, never a silent fallback to the deleted deterministic sort. D-1: the reasoned
generator never raises (degrades to `[]`).

AC-7 — **`run_id` persisted + traceable.** Every persisted `advisor_observation` this run writes
carries `run_id` + `evidence_injected` in its `raw_response`. No schema migration (free-form JSON).

AC-8 — **Route threads provenance on every return path.** `POST /ai-advisor/asset-swaps/evaluate`
mints a route-level `_default_provenance` (`evidence_injected = dict(ai_advisor._EMPTY_MANIFEST)`,
fresh `run_id`) on every early/error branch, and on success reads the engine's real `provenance`
behind `getattr + isinstance(dict)` (fallback to default, never None). Mirrors the R2-2 route.

AC-9 — **Provenance + attribution rendered with a NEW distinct testid.** The asset-swaps JS emits a
run-level provenance block `data-testid="as-live-generation-provenance"` (distinct from `sb-`/`lc-`)
showing model + injected-evidence manifest + run_id, on survivors AND zero-survivor paths. The label
at `templates/ai_advisor.html:1054` flips off "Deterministic — no AI reasoning" to an
`advisor_suggestion_model`-driven attribution (consistent with line 1064); 1064/1074/1084 left
byte-unchanged.

AC-10 — **Invariants preserved.** Advisory-only + off-execution-path (NO `asset_swap_engine` import
from `alpha_bot_execution.py`; NO `math_engine`/`alpha_bot_execution` import on the reasoned path);
observations `is_advisory_only=1`; D-1 never-raises; route echoes only `type(exc).__name__`, never
`str(exc)`; no credential leak.

AC-11 — **Credential-less mocked-green + execution-seam-detector-clean.** Full new/changed test
surface passes with NO Anthropic/Composer creds: the LLM seam (`_build_client`-style) + Composer seam
(`run_backtest`) mocked → ZERO live API calls. An execution-level seam detector patching
`anthropic.Anthropic.__init__` (record-then-raise) confirms no live client is constructed on any
covered path.

AC-12 — **Explicit-pair operator contract byte-preserved; deterministic helpers intact.** When the
operator supplies both tickers, the exact-pair evaluation is byte-preserved. Non-generation helpers
(`apply_ticker_swap`, `extract_tickers`, `_apply_lens_blend`, `extract_lens_scores`, persistence
keying) remain behaviorally unchanged. The deterministic `generate_objective_directed_candidates` is
DELETED (Q4) — a test asserts no production caller remains.

## Architecture (current-state constraints R2-3 must preserve — HOW detail is Gate-2)

* Off-execution-path lazy-import boundary (CC-2) stays; the reasoned generator keeps the anthropic
  SDK import lazy inside the off-path module.
* The gate is a hard contract boundary; only candidate generation changes. `BacktestCandidate`
  shapes (`daily_returns_pct` + `dated_returns`) fixed.
* `build_reasoning_context` reused verbatim (no enabler change; its known double-`/score`-fetch cost
  at `app.py:4630-4640` is an accepted pre-existing R2 follow-up, out of R2-3 scope).
* Provenance = the Design-B 4-key contract already shipped on SB + Logic-Change; reuse verbatim with
  `mode="asset-swap"`.
* Candidate universe via `universe_provider.get_tradeable_set()` (weekly-cached, D-1) — LLM proposes,
  we validate membership; never inject the full set into the prompt.

## Edge Cases

* LLM candidate NOT in the tradeable set → dropped, never backtested (never invent a ticker).
* LLM incumbent NOT present in the tree → dropped by `extract_tickers` presence check.
* Swap-into-self (candidate already held) → skipped (existing guard `:1297-1299`).
* LLM outage / malformed / empty → `[]` → zero survivors + populated provenance (AC-6).
* Every candidate dropped before backtest → gate never invoked; no wasted SPY baseline call.
* No Composer key → `no_api_key=True`, writes nothing, provenance still populated.
* Name→hash resolution fails on the route → loud error + `_default_provenance` (no silent pass).
* Persistence write fails → `persistence_error`, survivor still returned, never swallowed.
* Pathologically large tree → bounded prompt (AC-1).
* `build_reasoning_context` returns `("", _EMPTY_MANIFEST)` → generator still runs, honest all-absent manifest.

## Security Considerations

* **Never trust LLM-supplied tickers/holdings.** Incumbent read from the REAL tree
  (`extract_tickers`); candidate constrained to the REAL tradeable set — never fabricated.
* **No credential leak** — route echoes only `type(exc).__name__`; generator D-1 logs only the class.
* **No live API in tests** — LLM + Composer seams mocked; execution-seam detector enforces (AC-11).
* **Advisory-only / off-execution-path** — no write/trade Composer calls; no `alpha_bot_execution`
  import; `is_advisory_only=1`.
* **Bounded cost** — prompt bounded (AC-1); API-key check precedes any billed LLM call on both route
  and engine (mirror the R2-2 ordering fix).

## Testing Strategy

* Toxic-Pair TDD (adversarial test-writer ⇄ minimalist implementer) + `quant-code-reviewer` +
  `composer-alpaca-integration` (LLM/Composer seam) + `flask-dashboard-specialist` (route/JS slice) +
  doc-writer. Real Agent Team, shared worktree/branch, SendMessage handoffs, mode=plan.
* RED per AC-1..AC-12; the execution-seam detector (patch `anthropic.Anthropic.__init__`
  record-then-raise, real creds, assert zero) is a MANDATORY gate item.
* Credential-less (7 cred vars = `""`, NOT unset) + mocked LLM/Composer/real-tree-fetch seams; fixture
  provenance a Gate-1 hard rule (captured-from-producer or schema-derived; assert manifest SHAPE, not
  producer-computed values).
* JS syntax via the existing parametrized `tests/js_syntax/test_js_syntax.py` (do NOT add per-file
  `node --check`).
* Run `-n0` + `ALPHABOT_TEST_MEM_CAP_GB=24` + scratch `DB_PATH`. PM gate = full route-touching
  superset BOTH cred modes + execution-seam detector + CI `-n2` + **first-hand render** + local
  `ruff format --check` + `ruff check` before FF-ship (the R2-2 CI-bounce lesson).

## Scope Boundaries

* IN: reasoned swap-pair generation consuming `build_reasoning_context`; the `validate_tree` guard;
  provenance + `run_id` on result/route/persisted rows; the `as-live-generation-provenance` UI block +
  label flip at :1054; mock-seam tests + the execution-seam detector; delete the deterministic generator.
* OUT: any `backtest_gate_engine`/FDR/PBO/SPY-OOS math change; any `build_reasoning_context` change
  (incl. the double-fetch follow-up); the other tabs' labels (1064/1074/1084); a from-scratch/no-symphony
  asset-swap mode beyond today's contract; wiring a live `measured_value` into `SwapObjective` (stays
  0.0, known follow-up); any TRADE-touching/live-execution surface; changing the weekly scheduler's
  objective defaults; SSE streaming (request-response + "Running…").
