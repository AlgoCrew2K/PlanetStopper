# Planet Stopper — Architectural Decisions

This file records binding architectural decisions made during Planet Stopper development. Entries are append-only. Do not edit past entries; add corrections as new entries.

---

## Sprint 3 Close (2026-05-27)

These decisions were made during Sprint 3 (port-level deprecation + AI Advisor) and its audit-fix pass.

### DE-S3-001: Divergence Explainer call site — post-OC in autotuner.py

**Decision:** Divergence Explainer is invoked after Overfitting Conscience in `autotuner.py`, as the final producer in the post-walk-forward advisor sequence (OC → Spec Critic → DE).

**Rationale:** Audit recommendation (S3-AUDIT-004 HIGH). OC and Spec Critic observations on the spec bundle are available inputs DE may reference in its explanation context. Invoking DE last makes downstream data available without requiring a separate query pass.

**Status:** Implemented in Cycle A audit-fix (7b47376).

---

### DE-S3-002: advisor_observations.symphony_id — NULLable additive column, migration 025

**Decision:** `symphony_id` is added to `advisor_observations` as a NULLable column with no default, via migration 025 (`025_advisor_observations_symphony_id.sql`). All three Phase-1 producers populate it. Existing rows without `symphony_id` remain valid (NULL = unknown/legacy).

**Rationale:** Audit finding S3-AUDIT-002 HIGH. Keying observations to `symphony_id` enables per-symphony query in `get_advisor_observations_for_symphony` and powers the `/ai-advisor` UI filter. Additive-first policy (NULLable, no destructive step) is non-negotiable per project coding standards.

**Status:** Implemented in migration 025; `insert_advisor_observation` updated; `get_advisor_observations_for_symphony` added (7b47376).

---

### DE-S3-003: NARRATOR enum retained with deferral comment

**Decision:** The `NARRATOR` advisor role enum value is retained in the codebase with an inline deferral comment. No code is deleted.

**Rationale:** Removal would be a premature destructive action. Narrator is deferred, not cancelled. Retaining the enum makes its future activation a one-line change rather than a schema migration + enum re-add.

**Status:** Binding for all future workers. Do not remove the NARRATOR enum value without a new decision entry.

---

### DE-S3-004: Port-level decision-math deprecation — complete; display surfaces preserved

**Decision:** All autonomous port-level decision math is removed from production code. Deleted: `engine/multi_cycle.py`, `engine/port_selector.py`, `engine/port_aggregator.py`, `engine/dual_altitude.py`. `engine/exit_authority.py` is retained as display-only badge helpers (AX-2 badge + restart_notice). Port state schema rows (e.g., `port_state` table) are preserved per additive-first policy.

**Rationale:** User mandate (2026-05-26, per `[[project-port-level-deprecation-directive]]`). Management surface collapses to symphony-level only. Display surfaces are retained because they provide operator observability without executing decision logic. Schema rows stay because dropping them in a live DB is a destructive step that violates project coding standards.

**Status:** Implemented across Sprint 3 Stream A cycles (port-settings-cleanup, port-engine-module-removal, orphan-module-removal; merged through be74f4f).

---

### DE-S3-005: CVaR-divergence REJECT — no signed-divergence number in production

**Decision:** No signed CVaR divergence scalar is persisted, displayed, or passed to any downstream consumer. This REJECT from cycle planning is a hard constraint on all future Divergence Explainer changes.

**Rationale:** The CVaR-divergence detector idea was rejected at the decision-science council level (see `[[project-cvar-divergence-validation-wall]]`). Relocating the validation problem onto a ~5–15 independent regime-shift count does not escape the data wall. The Divergence Explainer writes only natural-language explanations keyed to CVaR window comparison; it never surfaces a scalar divergence value.

**Status:** Binding. Verified intact through Sprint 3 audit close. Any future proposal to add a divergence scalar requires a new council-level decision and a new entry here before implementation.

---

### DE-S3-006: compute_composition_hash promoted to database.py

**Decision:** `compute_composition_hash` is promoted from the deleted `engine/port_selector.py` into `database.py` as a stable public function.

**Rationale:** The function is a pure deterministic hash over symphony composition data. Its natural home is alongside the database accessors that consume it. Promoting it to `database.py` avoids orphaning the callers and eliminates the engine-layer import dependency.

**Status:** Implemented in Sprint 3 audit-fix Cycle B (be74f4f).

---

## Post-Sprint-3 / AI Advisor Hardening (2026-05-31)

These decisions were made during the advisor hardening session (autotuner remediation, M5 chat security audit, M5 hardening, README rewrite, and re-tune scheduling).

### DE-S4-001: Autotuner pre-existing-failure remediation — 0-failure tree achieved

**Decision:** The 14 carried autotuner test failures were resolved as a dedicated remediation phase before any new feature work. They were not carried forward as known-failing. Per-failure verdict: 13 were stale-contract TEST updates (post-refactor author error — stale `compute_sortino_tstat(returns, seed)` bootstrap-SE call signature and the NN1 `spec_bundle_id` strict-guard contract); 1 was a genuine dead-code deletion (`warn_port_mode_replay_blind_spot`). No hidden production bugs were uncovered.

**Rationale:** Pre-existing failures may mask new regressions and create ambiguous merge-safety verdicts. A 0-failure tree is a pre-condition for trustworthy test gates in all subsequent cycles. Per `[[feedback-no-preexisting-failures-carried]]`: pre-existing failures get a dedicated remediation phase, never perpetual carriage.

**Status:** Complete. Independent full tree: 4967 passed / 0 failed at a4de6b2 (merged 540b6e5). Binding: future merge gates must be evaluated against a 0-failure baseline.

---

### DE-S4-002: M5 chat security audit — advise-only posture SOUND; 2 HIGH egress findings + CSRF-JS break

**Decision:** The M5 `/chat` endpoint's advise-only/explain-only posture is architecturally sound and requires no structural change. Two HIGH egress findings were identified and require remediation: (1) the Anthropic API call was unauthenticated and uncapped — any visitor could trigger paid LLM calls with no rate limit; (2) client-supplied artifact text was serialized into the LLM prompt unfiltered — a prompt-injection vector. A functional break was also found: the chat POST JS did not send `X-CSRF-Token`, causing every chat request to 403 under the existing CSRF guard.

**Rationale:** Security audit result dated 2026-05-31; report at `feature-plans/security-review-m5-chat.md`. 0 CRITICAL findings confirmed the advise-only boundary intact. The 2 HIGH egress findings both have clear mitigations (rate limiting + server-side artifact validation). The CSRF break was a mechanical omission, not a design flaw.

**Status:** Findings documented. Remediation addressed in DE-S4-003. The `DISCORD_WEBHOOK_URL` and `ACCOUNT_UUIDS` flagged as S-1 were confirmed PLACEHOLDERS (`https://discord.com/api/webhooks/...`, `act1,act2,act3`) — S-1 was a false positive; no rotation required (see DE-S4-005).

---

### DE-S4-003: M5 chat hardening — CSRF fix, cost-DoS guards, artifact allowlist

**Decision:** Three hardening changes were applied to the M5 chat surface:
1. `X-CSRF-Token` header added to all 8 dashboard POST JavaScript files (including the sell_account panic button and settings-modal.js added after initial hardening), resolving the 403 on chat POST.
2. Cost-DoS guards added before the paid Anthropic LLM call: per-message length cap, per-artifact size cap, request body size cap, and a bounded per-IP rate limiter — all as named constants.
3. `validate_artifact()` server-side function added: allowlists accepted artifact types and truncates oversized content before prompt serialization. Advise-only boundary preserved throughout.

**Rationale:** Addresses both HIGH egress findings from DE-S4-002 audit and the CSRF functional break, using minimum-complexity mitigations. Named constants (not magic numbers) per project coding standards. Server-side validation cannot be bypassed by a client that omits the JS-side guard.

**Status:** Complete. Independent full tree: 5010 passed / 0 failed at 827c241 (merged 067d904).

---

### DE-S4-004: README comprehensive rewrite — newcomer-friendly current-state guide

**Decision:** README.md was rewritten from scratch as a newcomer-friendly guide covering the full current state of the system: Guard Alpha engine, all M1–M5 AI Advisor suite components, TOC, setup/run instructions. All change-history cruft removed.

**Rationale:** User directive (2026-05-31, per `[[project-readme-comprehensive-rewrite-deliverable]]`). The prior README reflected the codebase at an earlier sprint; the full AI Advisor suite (M1–M5) was not represented. A rewrite-from-scratch was the only way to avoid retrofitting stale structure.

**Status:** Merged d6e9366.

---

### DE-S4-005: Security audit S-1 false positive — placeholder credentials confirmed, no rotation

**Decision:** The `DISCORD_WEBHOOK_URL` and `ACCOUNT_UUIDS` values flagged by the M5 security audit as potential exposed secrets are confirmed PLACEHOLDERS (`https://discord.com/api/webhooks/...` and `act1,act2,act3`). No secret rotation is required.

**Rationale:** Direct inspection confirmed the values are syntactically obvious placeholders that do not correspond to any live credential. Rotating non-secrets would be unnecessary operational noise and could disrupt live integrations if placeholder-detection logic is not perfectly scoped.

**Status:** Binding. If live credentials are ever stored in config files, they must move to environment variables or a secrets manager — never committed to source.

---

### DE-S4-006: Phase-4 re-tune deferred — concurrent autotuner conflict risk; go/no-go is a business decision

**Decision:** The Phase-4 walk-forward re-tune was not run autonomously. Two conditions block autonomous execution: (a) the live daemon runs its own weekend autotune; concurrent autotuners writing `optuna_studies.db` risk study corruption; (b) the offline history-cache path requires the live `bot_state` holdings to hash to the same cache key as the 2026-05-29 snapshot — this cannot be verified without inspecting the live daemon state. The `external_data/` directory (daily-only bars) cannot substitute for the intraday bars the autotuner needs.

**Rationale:** Concurrent SQLite writers on the Optuna study DB is a data-integrity risk, not a performance concern. The go/no-go on re-tune timing is a business decision (operator controls when the live autotune window is clear). A paper Alpaca key with the IEX free feed would suffice if an online run is desired outside the live daemon's schedule.

**Status:** Deferred pending operator go/no-go. No automated re-tune should be dispatched without explicit clearance that the live daemon's autotune is not running concurrently.


---

## Walk-forward overhaul (2026-06-01)

### DE-WF-001: Walk-forward window expanded to 250 trading days (Phase 1)

**Decision:** `_WALK_FORWARD_TRADING_DAYS` increased from 125 to 250 (). `_REQUIRED_FETCH_TRADING_DAYS` updated to 299 (250 + MC warmup 39 + buffer 10). All fold arithmetic follows; ~29 usable validation days (vs. ~4 prior).

**Rationale:** The 125-day window collapsed to ~4 usable OOS validation days after the 60/20/20 split + purge/embargo — statistically too thin for meaningful CRRA-EU t-statistics. The 250-day extension is a council Amendment; the cost is one more month of Alpaca history per symphony.

**Status:** Merged (Phase 1). Full tree clean.

---

### DE-WF-002: CPCV folds added to Optuna walk-forward (Phase 2)

**Decision:** Each Optuna trial now scores across N=6 combinatorial purged-cross-validation groups, k=2 held out per split, C(6,2)=15 splits, yielding φ=5 complete OOS backtest paths. Each trial persists `cscv_date_returns` in its Optuna user attributes for use by the Phase-3 PBO gate. Canonical mlfinlab first-available-slot path assembly; path membership is VARIABLE-length (permissive `len>=1`).

**Rationale:** A single validation fold is the minimum honest check; CPCV provides the distribution of OOS performance across all combinatorial splits, exposing selection-process overfitting that the BHY correction alone cannot see. Partition/completeness is the primary invariant; modulo round-robin was evaluated and rejected as categorically wrong for k>=2.

**Status:** Merged (b3775e0). Full tree clean at 5089/0.

---

### DE-WF-003: PBO acceptance gate (STAGE-1 veto, CSCV S=8) added (Phase 3)

**Decision:** After BHY, the top-`_CSCV_TOP_K` (=20) pre-BHY configs are scored by `compute_pbo` (math_engine.py; Bailey & López de Prado 2014). PBO > `PBO_REJECT_THRESHOLD` (=0.5) triggers a STAGE-1 veto — the run deploys nothing. CSCV uses S=8 sub-matrix partitions. `cscv_date_returns` persisted per trial (migration 028); `pbo` column on `autotune_runs` (migration 028). DSR (Deflated Sharpe Ratio, D3) was evaluated and not adopted.

**Rationale:** PBO is a sample-robustness check orthogonal to BHY (which addresses multiplicity). PBO > 0.5 means the IS-best config generalizes OOS less than half the time across the CPCV partitions — a straightforward disqualifying signal. The combination of BHY + PBO closes two independent overfitting axes.

**Status:** Merged (849a11e). Full tree clean at 5146/0.

---

## Test infrastructure (2026-06-09)

### DE-TEST-001: pytest-sentinel guard in database._db_file() — prod-DB write leak closed

**Decision:** `database._db_file()` raises `RuntimeError` when `"pytest" in sys.modules` AND the resolved path basename is `alphabot_state.db`. `tests/conftest.py` adds a `pytest_configure()` hook (earliest pytest lifecycle point, before collection) that sets `DB_PATH` to a `tempfile.TemporaryDirectory` session path if not already set. The guard is completely inert in the live daemon (pytest is never imported there).

**Rationale:** Advisor producers (`overfitting_conscience`, `divergence_explainer`, `spec_critic`) each hold their own `import database` reference not covered by `patch("autotuner.database")` mocks. A test running without `DB_PATH` set would silently write to `alphabot_state.db`. The `pytest_configure()` hook fires before any module-level `import database` (which triggers `init_db()`), closing the gap that a fixture-level fix cannot reach. The sentinel guard in `_db_file()` converts any slip-through into a loud immediate failure rather than silent prod corruption.

**Status:** Merged (72d1d20). Full tree clean at 5802/0. Four latent `save_autotune_run` mock return-value bugs also exposed and fixed.

---

## AI Advisor liveness + SPA migration (2026-06-09 / 2026-06-10)

### DE-ADV-001: Advisor suggest route passes Composer hash to get_condensed_logic

**Decision:** `assemble_advisor_context` accepts a `composer_symphony_id` parameter (the Composer hash). The route resolves the Composer hash from `bot_state` and passes it as `composer_symphony_id`; `assemble_advisor_context` uses this hash when calling `symphony_logic.get_condensed_logic`. The prior behaviour — passing the normalized name — caused HTTP 400 from the Composer `/score` API and an all-empty logic struct for every symphony.

**Rationale:** Composer's `/score` API requires the opaque hash identifier, not a human-readable name. All other Composer API call sites in the codebase already use the hash. The normalized name is the correct key for internal DB lookups; these are now kept separate. The parameter is optional and backward-compatible (`None` falls back to `symphony_id`).

**Status:** Merged (2039f62, 7fbbe04). Full tree clean.

---

### DE-ADV-002: build_assessment_from_context — per-symphony informative empty state

**Decision:** A new function `ai_advisor.build_assessment_from_context(context)` derives a per-symphony assessment dict from the assembled context. The `/ai-advisor/suggest` route includes this as `assessment` in every response. The client renders the assessment block instead of a generic placeholder when the suggestions list is empty.

**Rationale:** Most symphonies produce an empty suggestion list because the CRRA-EU + Harvey-Liu FDR gate is intentionally strict — this is correct behaviour, not a bug. Previously the result box showed "No suggestions — the advisor did not find a well-supported edit at this time." for every symphony regardless of tuning state, giving the operator no actionable information. The assessment block surfaces `baseline_decision`, `oos_alpha`, `fallback_oos_alpha`, and a differentiated `summary` string so the operator understands why no edit is suggested.

**Open question (non-blocking):** Whether the CRRA-EU + Harvey-Liu FDR gate combination is too strict for the current 250-day walk-forward window — most symphonies show `oos_alpha = None` (all trials haircut-rejected). This is mathematically defensible (the gate is intentionally high-bar) but may mean the advisor produces meaningful suggestions only after the autotuner has accumulated substantially more trial history. No change to the gate is proposed without a council-level review.

**Status:** Merged (9c0e246). Full tree clean at 5766/0.

---

### DE-ADV-003: _build_volatility_regime honest availability

**Decision:** `_build_volatility_regime` returns `available: False` with a human-readable `reason` string when `symphony_vol` and `atr_pct_14d` are absent from the autotune run row. The prior code returned `available: True` with all-null fields, fabricating regime availability context for Claude and defeating the data-wall contract.

**Rationale:** Claude's `data_sufficiency` gate is only meaningful if the regime context is honest. Fabricating `available: True` with null vol/atr gave Claude false confidence that regime data was present. The columns do not yet exist in the autotune_runs schema; when the schema gains them the function will naturally start returning `available: True` (forward-compatible).

**Status:** Merged (da44f69). Binding: the function must never report `available: True` unless both `symphony_vol` and `atr_pct_14d` are non-null.

---

### DE-ADV-004: AI Advisor — in-place 5-tab SPA replacing 5 separate MPA templates

**Decision:** The AI Advisor is consolidated from 5 separate MPA templates (`ai_advisor.html`, `ai_advisor_correlations.html`, `ai_advisor_asset_swaps.html`, `ai_advisor_logic_changes.html`, `ai_advisor_chat.html`) into a single `templates/ai_advisor.html` with in-place JS tab switching (`initTabSwitcher` in `static/ai_advisor.js`). The GET sub-routes for the 4 tab pages now 302-redirect to `/ai-advisor`. The POST action routes (suggest, evaluate, accept, reject, chat/send) are unchanged. Chat is now an always-in-DOM right-side slide-in panel. The Overview tab is retained as the 5th tab (operator decision — no content was deleted).

**Rationale:** Each of the 5 templates was independently building overlapping context data for its single panel, and navigation between tabs required full page loads. Consolidating into a single server-side render eliminates duplicate DB queries (correlation matrix, symphony list, API-key check) and reduces round-trips. JS in-place switching matches the `.active-toggle` pattern already used in `static/index.js`. ARIA tab semantics are maintained throughout.

**Status:** Merged (d392a6c). Full tree clean.

**Orphaned templates (deletion candidates):** The 4 per-tab templates are now dead code. Their routes redirect to `/ai-advisor` and the templates are no longer rendered. They may be deleted in a future cleanup cycle:
- `templates/ai_advisor_correlations.html`
- `templates/ai_advisor_asset_swaps.html`
- `templates/ai_advisor_logic_changes.html`
- `templates/ai_advisor_chat.html`

---

### DE-DATA-001: State DB cleanup — ~1,643 test-fixture rows removed from production DB

**Decision:** ~1,643 test-fixture / seed rows were removed from the live `alphabot_state.db`: `autotune_runs` reduced from 1,689 to 66 rows; fake `symphony_strategies`, `port_state`, and `advisor_observations` rows also removed. Backups retained as `alphabot_state.db.pre-seed-cleanup-*` files.

**Rationale:** The rows were written by tests that ran without DB isolation before the pytest-sentinel guard (DE-TEST-001). They polluted the autotune run history visible in the dashboard and inflated advisor observation counts with non-production data. The cleanup was performed with backups in place per additive-first / conservative data-handling norms.

**Status:** Complete. Data integrity restored. DE-TEST-001 prevents recurrence.
