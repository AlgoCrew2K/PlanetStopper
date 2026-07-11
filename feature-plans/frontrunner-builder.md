# Feature: Frontrunner Builder
Status: ready
Created: 2026-07-10

## Summary
A weekly, all-symphonies Advisor-tab tool that keeps the operator's Composer book's frontrunner overlays sharp. On the existing weekly scheduler cadence, for **every** live symphony, it uses **Fable** to build a *candidate* frontrunner overlay — the leading `RSI-overbought → VIX/hedge basket` cascade that gates before the strategy logic — informed by the Atlas frontrunner corpus and the operator's own frontrunner patterns. It splices the candidate into the symphony in place of the incumbent frontrunner, independently re-backtests incumbent vs. candidate through the existing culling gates (overfitting guardrail), and — only for candidates that **improve Calmar (profit up / drawdown down)** or preserve Calmar while materially simplifying via Composer's `any/all` — queues them on a new Advisor tab **for the operator's approval**. On approval, it **uploads the frontrunner'd symphony version to the operator's Composer account as a new undeployed symphony** (Composer's create endpoint; what the operator reviews/deploys manually). Nothing auto-trades and nothing uploads without per-candidate approval.

**Shared write capability (closes an existing gap):** the same approval→Composer-create path is retrofitted onto the existing `strategy_builder_engine.propose_strategies` output, so its advisory candidates can also be approved and pushed to Composer as undeployed symphonies — not just the new frontrunner candidates.

Grounding (verified on the operator's 11 real live `/score` trees, own re-parse — not fixtures): frontrunners are **cascades** of `RSI(ticker) gt ~77–82.5 → VIX/hedge basket` if-nodes (one per parallel sub-strategy), with scale-in escalation (RSI>80→VIX blend, >82.5→heavier UVXY); fire baskets always contain ≥1 VIX-family instrument but **not always VIXY** (top rungs fire UVXY+VIXM/VXX), blended with BTAL/GLD/TAIL/EUM/BIL; the cascade→core boundary is a clean **size cliff** (baskets ≤~16 nodes vs 8,000+ core); hundreds of flat RSI-gt rungs are collapsible to `any/all`.

## Acceptance Criteria

**Progress (2026-07-11, wave-1 backend — frreview-APPROVED, no P0/P1; see `DE-FRONTRUNNER-001` in `DECISIONS.md` and the four module pages in `docs/generated/`):** the detect→generate→splice→gate→accept→queue pipeline (AC-2/3/4/5/6/7/11/12) is built and tested end-to-end at the backend level, the weekly scheduler hook (AC-1) and the `propose_strategies` retrofit's proposal-queuing (AC-10) are already wired, and the approval→Composer-create backend (AC-9) is built. **Not yet built (wave-2):** the new Advisor-tab UI (AC-8) and its `/run`, `/approve`, `/reject` routes — without them, `run_frontrunner_build` and `approve_frontrunner_proposal` are reachable only via tests/a Python shell, not the operator. The operator-gated task-zero live Composer create test (feature plan §Architecture "Build task ZERO") is also still pending. **This plan stays `Status: ready` and is NOT marked shipped/complete** — the feature only ships once wave-2 lands and the whole thing passes the ship gate (PR → `/review` → PM live E2E → merge, operator word).

- [x] AC-1: On the weekly scheduler cadence (reusing the `strategy_builder_scheduler` hook), the builder runs against **all** live symphonies (roster resolved at run time from `bot_state`), not a fixed set. ~~Also invokable on demand via a route.~~ **Scheduler hook DONE** (`strategy_builder_scheduler.run_weekly_build` calls `frontrunner_builder.run_frontrunner_build()` after the four Strategy-Builder objectives, isolated in its own try/except). **On-demand route NOT built** — wave-2.
- [x] AC-2: For each symphony, locates its incumbent frontrunner **cascade(s)** — the leading chain of hedge/VIX-routing `if`-nodes per sub-strategy, delimited from the core by the size-cliff boundary (last hedge-firing else whose fall-through is large/non-hedge); recurses into parallel sub-strategy groups; excludes internal inverse-VIX timing sub-strategies. If none / ambiguous → records a skip with reason; never guesses. **DONE** — `advisors/frontrunner_detector.py`, validated on all 11 real captured trees, 10 detector tests + 5 deep-tree hardening tests (P2-1, iterative traversal).
- [x] AC-3: Loads the Atlas frontrunner corpus through the shared 7-day cache (first real population; `force_refresh` optional), identifies frontrunner-shaped strategies (structural detection + name), extracts patterns (watched tickers, VIX/hedge instruments, RSI thresholds, basket shapes). Never trusts incoming `oos_metrics.sharpe`. **DONE** — `_gather_atlas_frontrunner_patterns`, hoisted to once-per-symphony-run (not per-cascade), 9 tests.
- [x] AC-4: **Fable** composes a candidate frontrunner overlay (build-plan DSL). Hard constraints, enforced post-generation: (a) ≥1 VIX-family ticker; (b) VIX instrument varied across builds (not defaulting to VIXY); (c) mergeable flat `RSI-gt` rungs collapsed to `any/all` (`binary-compound`/`compound`); (d) scale-in tiers preserved as tiered conditions, never flattened to one OR. **DONE** — `generate_candidate_overlay` + `_has_vix_ticker_in_fire_branch` + `_collapse_mergeable_rungs`.
- [x] AC-5: Candidate DSL compiles to a valid Composer tree (`plan_tree_compiler` + `validate_tree` + bounded tradeability-repair) and is spliced into the symphony replacing the incumbent overlay → a full valid symphony that backtests via `/score`. **DONE** — `splice_candidate_into_symphony`.
- [x] AC-6: Incumbent and candidate symphonies are **independently re-backtested** and run through `backtest_gate_engine.evaluate_candidate_batch` (mandatory attach point) as the overfitting guardrail (FDR / PBO / purge-embargo / OOS / screens); the builder's search breadth is recorded to the DoF ledger. **DONE** — `_gate_and_accept_candidate`, including the gate-reachability fix (`_TREE_SPLICE_PANEL_PARAMS_SENTINEL`) and the DoF-ledger isolation fix (`evidence_source="OVERLAY_BACKTEST_SELECTION"`) — both required a correction mid-cycle; see `DE-FRONTRUNNER-001`.
- [x] AC-7: Acceptance = candidate **improves Calmar** (CAGR ÷ max-drawdown) vs. the incumbent on out-of-sample folds (profit ↑ and/or drawdown ↓, net Calmar ↑) AND doesn't worsen max drawdown past a floor; OR preserves Calmar within tolerance while **materially simplifying** (node/depth reduction). Sharpe/vol reported, never gating. Tagged `performance` and/or `simplification`. **DONE** — `advisors/frontrunner_acceptance.py`, 17 tests.
- [ ] AC-8: Accepted candidates queue as **pending-approval** items on a new Frontrunner-builder Advisor tab, each showing the detected incumbent overlay, the candidate overlay diff, and incumbent-vs-candidate Calmar/CAGR/MDD + node-count deltas. The operator **approves or rejects** each. **NOT BUILT — wave-2.** Backend persistence exists (`database.get_pending_frontrunner_proposals` and friends, migration 033) but there is no template/JS surface and no route to read it; pending proposals are only queryable directly against the DB today.
- [x] AC-9: **On approval**, the builder creates a NEW undeployed symphony in the operator's Composer account (the incumbent tree with the candidate frontrunner spliced in) via `POST /api/v0.1/symphonies`, using the existing Composer creds. It then verifies the created symphony reads back as **zero-allocation / undeployed** (`GET /symphonies/{id}/score` or holdings) before marking the approval "uploaded". It never deploys/invests and never places an order. Nothing uploads without approval — including on unattended weekly runs. **Backend DONE** — `advisors/composer_draft_client.py` + `approve_frontrunner_proposal`, adversarial no-trade-boundary suite (10 tests). **`/approve` route NOT built** (wave-2) — `approve_frontrunner_proposal` is currently reachable only via tests/a Python shell, so no operator has actually triggered a create yet. **Operator-gated task-zero live test still pending** (one real create against the operator's account, verify-undeployed, delete).
- [x] AC-10: **Retrofit** — `propose_strategies`' accepted candidates use the same approval→Composer-create path (same `composer_draft_client`, same approval UX), closing the gap where the strategy builder could never persist a proposal back to Composer. **Proposal-queuing DONE** — `strategy_builder_engine._persist_survivor` queues every accepted candidate onto `frontrunner_proposals` (`proposal_source="strategy_builder_retrofit"`), flowing through the same `approve_frontrunner_proposal` on approval. **Shared approval UX NOT built** (wave-2, same AC-8 UI gap) — "same approval UX" cannot exist yet since no UX exists.
- [x] AC-11: Error/empty states: no incumbent FR → skip w/ reason; ambiguous boundary → skip + surface for manual review; Atlas down → stale-cache degrade → skip (never crash); Fable returns no-VIX/invalid candidate → reject + bounded retry; fails gates or fails to improve Calmar → rejected item w/ reason+deltas; Composer create fails (4xx/5xx) → surface the error on the approval item, do NOT mark uploaded, do NOT retry blindly. **Backend DONE** — every path above is implemented and D-1/never-raises at the module level (rejected-candidate observations persisted with deltas, `frontrunner_proposals.error_message` on create failure). "Surface for manual review" is currently a DB-level skip reason / observation row, not yet a UI affordance (depends on AC-8).
- [x] AC-12: Cost/rate bounded — N candidates/symphony with a Fable API budget cap; Atlas via the 7-day cache only; Composer `/score` + `/backtest` + create calls rate-limited; a per-account symphony-count guard (skip create if near any Composer limit). **DONE** — `MAX_CASCADES_PER_SYMPHONY_RUN=40` (Fable budget, calibrated against the 11 real trees), `MAX_FRONTRUNNER_UPLOADS_PENDING_REVIEW=25` (self-imposed local-count guard — Composer documents no account-wide symphony cap or quota, confirmed by `composer-api-researcher`; `fetch_symphony_stats` is deployed-scoped and can't see undeployed symphonies, so it cannot serve as the guard's denominator), Atlas via the existing weekly cache only, `composer_draft_client`/`composer_backtest_client` retry/backoff bounded.

## Architecture
Reuses the existing strategy-builder pipeline; adds frontrunner detection/generation, Calmar acceptance, and a shared Composer write path.

**VALIDATED create contract (Medium-High confidence; two independent third-party `composer-trade-mcp` mirrors agree exactly + match the OpenAPI doc):**
- `POST https://api.composer.trade/api/v0.1/symphonies`
- Headers: `{"x-api-key-id": <COMPOSER_KEY_ID>, "Authorization": "Bearer <COMPOSER_SECRET>"}` (Content-Type application/json auto-set).
- Body: `{"name": str, "asset_class": "EQUITIES"|"CRYPTO" (default EQUITIES), "description": str, "color": <hex swatch>, "hashtag": str, "symphony": {"raw_value": <full validated Root tree, by_alias, exclude_none>}}`.
- Creates an **UNDEPLOYED** symphony (no capital, no trade). Deploying is a SEPARATE explicit call — `invest_in_symphony` → `POST /deploy/accounts/{uuid}/symphonies/{id}/invest` — which this feature MUST NEVER call.
- NB the MCP tool named `create_symphony` is LOCAL VALIDATION ONLY; the actual write is `save_symphony`. Distinct ops: save (`POST /symphonies`), copy (`POST /symphonies/{id}/copy`), update (`PUT /symphonies/{id}`), invest/deploy (separate).

**Build task ZERO (before wiring the write into the approval flow):** a single GUARDED live test — one `save_symphony` create against the operator's account, then immediately `GET /symphonies/{id}/score` (or holdings) to confirm zero-allocation/undeployed, then delete the throwaway symphony. This confirms the residual unknowns the mirrors could NOT (exact response shape `{symphony_id, version_id}`; whether `description`/`color`/`hashtag` are truly API-required vs MCP-tool-strict; whether an `x-origin` header is needed; `tags`/`benchmarks`/`share_with_everyone` acceptance). Do not wire the real approval→create path until this passes.

**New modules:**
- `advisors/frontrunner_detector.py` — walk a `/score` tree, find leading `RSI-overbought → VIX/hedge` cascades per sub-strategy, delimit the cascade→core boundary via the size-cliff + hedge-ticker + RSI-`gt` signature; exclude internal inverse-VIX timing subtrees; return incumbent overlay(s) + boundary + confidence; fail-loud on ambiguity. Validated against all 11 live trees.
- `advisors/frontrunner_builder.py` — orchestrates detect → gather Atlas patterns → Fable generate → compile → splice → backtest incumbent+candidate → gate → Calmar accept → queue for approval. Model = **Fable** (`claude-fable-5`) via the Anthropic SDK (mirrors `build_plan_generator`).
- `advisors/composer_draft_client.py` — **shared** Composer write client: `save_symphony(name, description, color, hashtag, raw_value, asset_class="EQUITIES") -> resp` (`POST /api/v0.1/symphonies`, the VALIDATED contract above), reusing `get_composer_headers`/`COMPOSER_BASE_URL`; plus a post-create `verify_undeployed(symphony_id)` safety read (`GET /symphonies/{id}/score`). **It exposes NO invest/deploy method** — the deploy endpoint (`/deploy/.../invest`) is deliberately not implemented here so no code path can trade. Used by both the frontrunner builder and `propose_strategies`.
- Splice helper — replace the detected incumbent cascade with the candidate overlay; re-id nodes; `validate_tree`.

**Reused (extended):**
- `advisors/strategy_builder_scheduler.py::run_weekly_build` — extended to also invoke the frontrunner builder each week.
- `advisors/strategy_builder_engine.py` — retrofit `propose_strategies`' accepted candidates onto the shared approval→create path.
- `advisors/community_strats.py` + `advisors/atlas_cache.py` — pull + cache the frontrunner corpus (first prod caller).
- `advisors/plan_tree_compiler.py` — DSL → validated tree + tradeability repair.
- `advisors/composer_backtest_client.py` — independent re-backtest (existing `raw_value` round-trip is the model for the create client).
- `advisors/backtest_gate_engine.py::evaluate_candidate_batch` → `acceptance_gate.py` — overfitting guardrail (mandatory; never bypass).
- `advisors/symphony_schema.py` — `make_binary_compound_condition` / `make_if_compound` for candidate construction + any/all simplification.
- `symphony_logic.py::fetch_symphony_score` — fetch live trees.

**API/routes:**
- `POST /ai-advisor/frontrunner-builder/run` — trigger a build over all live symphonies (mirror `/ai-advisor/strategy-builder/run`).
- `POST /ai-advisor/frontrunner-builder/approve` + `/reject` — approve → invoke `composer_draft_client.create_symphony` + persist the result; reject → record only. Shared by the propose_strategies retrofit (a generic `POST /ai-advisor/proposal/approve` keyed by observation id may be cleaner — decide at build time).
- Weekly scheduler hook (above).

**Persistence:** `advisor_observations` rows, new `observation_type = "frontrunner_proposal"`, with an approval-status field (pending/approved/rejected/uploaded) + the created `symphony_id` on upload. Audit the create action (mirror `record_llm_suggestion`).

## Design-System Mapping
No formal primitive library; the Advisor tab uses existing dashboard CSS in `templates/ai_advisor.html` + `static/ai_advisor.js`. New **Frontrunner-builder tab panel** mirrors the Strategy-Builder panel (`templates/ai_advisor.html:1412-1805`): a `data-tab="frontrunner-builder"` entry in the tab bar (~866-926); a `tab-panel-frontrunner-builder` block; per-symphony **pending-approval cards** reusing the suggestion-card styling, each with Approve/Reject buttons (reuse the existing accept/dismiss button classes + gate-badge styling) and the incumbent-vs-candidate Calmar/CAGR/MDD/node-count deltas + overlay diff. No new color tokens.

## Edge Cases
- No detectable frontrunner cascade in a symphony → skip w/ reason (some symphonies are pure alpha).
- Ambiguous cascade→core boundary → fail-loud, surface for manual review (advisory framing → operator confirms the detected overlay before approving).
- Parallel sub-strategies each with a frontrunner → one candidate per detected cascade, surfaced individually.
- Scale-in tiers that can't cleanly collapse → keep the tiered nest.
- Atlas cache empty (first run) → populate; unavailable → stale → skip w/ health note.
- Fable invalid/no-VIX/degenerate → reject + bounded retry → skip.
- Candidate tradeability failure → compiler repair loop prunes/retries (bounded) → drop if unrepairable.
- Backtest insufficient history → gates fail-closed (existing), no false accept.
- **Composer create failure** (4xx malformed `raw_value`, quota, auth) → surface on the approval item, do NOT mark uploaded, no blind retry.
- **Duplicate uploads** — approving the same candidate twice must not create two symphonies (idempotency key / disable button after upload).
- Large trees (8,000+ nodes) → operate on the relevant subtree; bound compute.

## Security Considerations
- **No auto-trade boundary (key control, now structural):** `save_symphony` (`POST /symphonies`) yields an **undeployed** symphony; deployment is a *separate* `invest_in_symphony` (`POST /deploy/.../invest`) call that `composer_draft_client` DOES NOT IMPLEMENT — so no code path in this feature can invest/trade. A test asserts the `/deploy` / invest endpoint is never constructed or called anywhere in the builder, and that every created symphony verifies zero-allocation post-create.
- **Approval-gated write:** no Composer create happens without an explicit per-candidate operator approval — including on unattended weekly runs.
- **Prompt-injection / data-as-instructions:** Atlas corpus + tree data feed a Fable prompt as untrusted DATA; the output is a structured DSL validated by `plan_tree_compiler` + `validate_tree` (closed-enum grammar, no eval/exec) before any use or upload.
- **Composer write scope:** reuse existing creds (already write-capable — the engine POSTs `go-to-cash`); no new secret. Rate-limit + symphony-count guard to prevent runaway creation.
- **Provider cost (Atlas):** reads only through the 7-day cache; no per-build fresh Mongo pulls.
- **Idempotency:** approvals are idempotent (no duplicate symphony creation).
- **Data exposure:** proposals + created symphonies are the operator's own data; panel behind the existing dashboard auth gate.
- **Overfitting integrity (correctness-as-safety):** search breadth recorded to the DoF ledger; incumbent `oos_metrics` never trusted (independent re-backtest).

## Testing Strategy
- **Unit — detector** (`test_frontrunner_detector.py`): on the 11 captured real trees as fixtures — delimits each leading RSI→VIX cascade + boundary; excludes internal inverse-VIX subtrees; fail-loud on a synthetic ambiguous tree; recurses parallel sub-strategies.
- **Unit — builder constraints** (Fable mocked): candidate always ≥1 VIX; instrument varies across seeds; mergeable rungs collapse to any/all; scale-in tiers preserved; splice yields a `validate_tree`-valid symphony.
- **Unit — acceptance**: Calmar math (CAGR/MaxDD); simplification-with-preserved-Calmar path; drawdown-floor guard; Sharpe/vol non-gating; tagging.
- **Unit — `composer_draft_client`** (HTTP mocked): correct method/path/headers/body per the pinned contract (task zero); post-create undeployed-verification; 4xx/5xx surfaced not swallowed; idempotency.
- **Integration** (Fable + backtest + Atlas + Composer-create all mocked): full detect→build→splice→backtest→gate→accept→queue→**approve→create→verify** path; propose_strategies retrofit uses the same approval→create; DoF-ledger search-breadth recorded; reject/no-improvement/no-incumbent/create-fail paths persist the right state.
- **Security tests** (`describe('security')`): no deploy/trade path reachable from the builder; no create without approval; created symphony verified undeployed; Atlas via cache; DSL validated before upload; approval idempotent.
- **Behavioral / e2e:** panel renders pending-approval cards with deltas + overlay diff; run button triggers the route; approve triggers a (mocked) create + moves the card to uploaded; error states render; responsive spot-check.
- Reuse the existing gate-engine suite unchanged — this feature must not weaken it.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Weekly, all live symphonies (reuse `strategy_builder_scheduler`) | Operator directive: builder always targets all his symphonies once a week. |
| Approval workflow → Composer create on approval | Operator directive: suggest → approve → upload the frontrunner'd version to Composer. |
| Upload = `save_symphony` → `POST /api/v0.1/symphonies` (create undeployed) | VALIDATED via two agreeing third-party MCP mirrors + OpenAPI (Med-High). Body {name, asset_class, description, color, hashtag, symphony:{raw_value}}. Composer has no "drafts" state — "created undeployed" is the reviewable handoff. Residual gaps (response shape, required-ness of description/color, x-origin, tags/benchmarks) confirmed by the guarded live test (task zero). |
| Deploy/invest is a SEPARATE endpoint the feature NEVER calls | `invest_in_symphony` → `POST /deploy/.../invest` is the only thing that funds/trades a symphony; not implementing it in `composer_draft_client` makes the no-auto-trade boundary structural, not just policy. |
| Shared draft-writer; retrofit `propose_strategies` | Operator directive: close the strategy-builder write-gap while in there. |
| Approval required even on unattended weekly runs; verify-undeployed post-create | No auto-trade; nothing reaches Composer without operator approval; belt-and-suspenders against an unexpected deployed state. |
| Existing creds for the write | No key scopes in Composer; creds already write (`go-to-cash`) — no new token. |
| Objective = **Calmar**; gates = overfitting guardrail | Operator: profit up / drawdown down; Sharpe/vol non-gating. "Real, not curve-fit" (gates) vs "better frontrunner" (Calmar) are separate. |
| Generation via **Fable** | Operator directive. |
| FR = **cascade** of RSI-overbought→VIX if-nodes; size-cliff detector | Verified on the operator's 11 real trees. |
| Simplification (any/all collapse) a first-class accepted outcome | Operator: post-any/all many hand-built cascades can be simplified. |
| ≥1 VIX always, instrument varied (not always VIXY) | Operator directive + verified in real trees. |

## Scope Boundaries
- **IN:** weekly all-symphonies frontrunner detection + Fable candidate generation (with VIX + any/all-simplification + scale-in constraints); independent incumbent-vs-candidate re-backtest through the existing gates; Calmar acceptance; approval workflow on a new Advisor tab; **on approval, create an undeployed symphony in the operator's Composer account** (`POST /symphonies`) via a shared `composer_draft_client`; **retrofit `propose_strategies`** onto the same approval→create path; Atlas-corpus first-population through the 7-day cache.
- **OUT:** deploying/investing a created symphony (operator does that manually in Composer); auto-approval or auto-upload; auto-trading; changing the gate engine internals; touching the sleeves/execution path; a per-node PATCH/edit-in-place flow (create-new is the chosen path; `/copy`+PATCH is a fallback only if create proves unworkable); non-frontrunner strategy generation beyond the existing strategy builder.
