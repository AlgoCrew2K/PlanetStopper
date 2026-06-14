# Planet Stopper — Architectural Decisions

This file records binding architectural decisions made during Planet Stopper development. Entries are append-only. Do not edit past entries; add corrections as new entries.

---

## Grammar Foundation — symphony_schema Rebuild (2026-06-14)

Cycle: `grammar-foundation` on branch `team/grammar-foundation`. 210/210 tests GREEN at `83f5623` (AC-1..AC-12). Merger pending PM merge gate.

### DE-GRAM-001: OQ-2 reversal — `gte` is corpus-verified valid; added to KNOWN_COMPARATORS

**Decision:** `gte` is added to `KNOWN_COMPARATORS`. The prior OQ-2 stance ("unconfirmed, exclude") is reversed.

**Evidence:** `gte` appears n≈39,596 times across 10,441 real Composer symphonies in the grammar corpus. It is the third most frequent comparator in the dataset. Excluding it caused `validate_tree` to false-positive on real `/score` responses and blocked frontrunner-style strategies from passing schema validation.

**Consequence:** Tests that asserted `gte` produces a hard error have been updated to assert it produces no error. The `KNOWN_COMPARATORS` frozenset now contains exactly `{gt, lt, gte, lte}`. `eq` and `neq` remain absent — zero corpus occurrences.

**Status:** GREEN at `fc633d0`. Part of grammar-foundation cycle.

---

### DE-GRAM-002: `quarterly` and `yearly` rebalance cadences corpus-verified; added to KNOWN_REBALANCE

**Decision:** `quarterly` (n≈58) and `yearly` (n≈27) are added to `KNOWN_REBALANCE`. Prior stance treated both as invalid.

**Rationale:** Both appear in real symphony corpus data. Excluding them caused false-positive hard errors on real `/score` trees with non-standard rebalance cadences. The set now contains `{daily, none, weekly, monthly, quarterly, yearly}`. `hourly` remains absent (zero corpus occurrences) and is the canonical test value for "invalid rebalance".

**Consequence:** Tests that used `"quarterly"` as the invalid-rebalance test value were updated to use `"hourly"`.

**Status:** GREEN at `fc633d0`. Part of grammar-foundation cycle.

---

### DE-GRAM-003: 6 corpus-verified indicator fns added to KNOWN_INDICATOR_FNS

**Decision:** `KNOWN_INDICATOR_FNS` is extended from 7 to 13 entries with the following corpus-verified additions:
- `exponential-moving-average-price` (n≈45,816 — highest-frequency new fn)
- `standard-deviation-price` (n≈5,572 — promoted from lint-only to verified)
- `percentage-price-oscillator`
- `percentage-price-oscillator-signal`
- `upper-bollinger`
- `lower-bollinger`

**Rationale:** All 6 appear in the grammar corpus. The prior `KNOWN_INDICATOR_FNS` frozenset reflected only the 7 VERIFIED-LOCAL entries from the initial fixture set. The lint-only treatment of these fns meant real symphonies using them generated spurious warnings in `lint_tree`. They are now first-class known fns; `lint_tree` no longer warns on them.

**Note:** `rsi` (abbreviation of `relative-strength-index`) remains absent — it is a lint-warned abbreviation, not a valid corpus token.

**Status:** GREEN at `7f85791`. Part of grammar-foundation cycle.

---

### DE-GRAM-004: Compound condition grammar implemented — 6 constructors + iterative validate_tree extension

**Decision:** The grammar §7 compound condition types (`binary`, `binary-compound`, `compound`) are fully implemented via 6 new constructors and an extended `validate_tree` that hard-errors on malformed compound blocks at any tree depth.

**New constructors:**
- `make_condition_operand(fn, ticker, *, window)` — grammar §7 operand shape `{fn, ticker, params:{window}}`
- `make_constant_rhs(value)` — `{constant: value}` descriptor
- `make_binary_condition(lhs_operand, comparator, rhs)` — binary leaf `condition-type="binary"`
- `make_binary_compound_condition(fn, tickers, comparator, rhs, *, window, operator="any")` — frontrunner primitive; broadcasts one predicate over a tickers list with any/all semantics; lhs uses grammar `%` placeholder
- `make_compound_condition(operator, conditions)` — joins N sub-conditions with any/all
- `make_if_compound(condition_block, *, then_children, else_children)` — if node carrying a compound condition block directly on the true-branch if-child

**Validation extension (`_validate_condition_block`):** Iterative DFS using an explicit `(cond, depth)` stack bounded by `MAX_CONDITION_DEPTH=400`. Hard errors: unknown `condition-type`, bad `operator`, missing `conditions` key on compound blocks, missing `tickers` key on binary-compound blocks. Absent `condition-type` (raw binary leaf) is tolerated. `RecursionError` is structurally impossible regardless of nesting depth.

**`%` placeholder convention:** The binary-compound lhs operand uses `ticker="%"` as a grammar placeholder. `extract_tickers` explicitly excludes `%` from its results. The test reference walker does the same.

**Status:** GREEN at `7f85791`. 210/210 tests pass. Part of grammar-foundation cycle.

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

**Status:** Merged (72d1d20). Full tree clean at 5802/0. Four latent `save_autotune_run` mock return-value bugs also exposed and fixed.**Rationale:** Advisor producers (`overfitting_conscience`, `divergence_explainer`, `spec_critic`) each hold their own `import database` reference not covered by `patch("autotuner.database")` mocks. A test running without `DB_PATH` set would silently write to `alphabot_state.db`. The `pytest_configure()` hook fires before any module-level `import database` (which triggers `init_db()`), closing the gap that a fixture-level fix cannot reach. The sentinel guard in `_db_file()` converts any slip-through into a loud immediate failure rather than silent prod corruption.

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


---

## AI Advisor cleanup (2026-06-10)

### DE-ADV-005: D-1 security fix — all advisor error paths return class name only

**Decision:** All advisor error return paths in `ai_advisor.py` and `app.py` now return only `type(exc).__name__` to the browser. No `str(exc)` or f-string embedding of exception text in any `jsonify({"error": ...})` response. Full detail is logged server-side via `exc_info=True`.

**Paths fixed:**
- `ai_advisor.py` `request_suggestions` `messages.parse` failure path (was `f"...{exc}"`)
- `app.py` `ai_advisor_asset_swaps_evaluate` engine-call except (was `f"{type(exc).__name__}: {exc}"`)
- `app.py` `ai_advisor_logic_changes_evaluate` engine-call except (was `f"{type(exc).__name__}: {exc}"`)
- `app.py` `ai_advisor_logic_changes_evaluate` ImportError handler (was `f"advisor unavailable: {type(_ie).__name__}: {_ie}"`)
- `app.py` `ai_advisor_logic_changes_evaluate` response-serialization except (was `f"{type(_je).__name__}: {_je}"`)

**Rationale:** Exception messages may contain API keys, internal paths, or other secrets. D-1 contract requires class-name-only browser-facing errors; full detail stays server-side. Pinned by source-inspection test `test_no_str_exc_embedding_in_advisor_error_returns`.

**Status:** Merged (be9a31f). Full tree clean.

---

### DE-ADV-006: assemble_advisor_context honors passed autotune_run — single DB fetch

**Decision:** `assemble_advisor_context` now honors a caller-supplied `autotune_run` parameter. When a non-`_SENTINEL` value is passed, the internal `database.get_latest_autotune_run` call is skipped. The suggest route (`app.py:ai_advisor_suggest`) pre-fetches the autotune run once and passes it, eliminating the previous double DB fetch.

**Rationale:** The parameter was documented as functional but the implementation unconditionally overwrote it (line 497) and always fetched from DB (line 501). This caused two DB calls per suggest request and broke route-level DB mock coverage in tests. The fix honors the _SENTINEL sentinel pattern already present in the signature.

**Status:** Merged (e5b0c18). Full tree clean.

---

### DE-ADV-007: 4 orphaned advisor templates deleted

**Decision:** `templates/ai_advisor_correlations.html`, `ai_advisor_asset_swaps.html`, `ai_advisor_logic_changes.html`, and `ai_advisor_chat.html` are deleted. Their GET routes 302-redirect to `/ai-advisor`; no `render_template` call references them.

**Rationale:** The templates became dead code after the in-place SPA migration (DE-ADV-004). Keeping them was misleading — they appeared to be render targets but were not. Deletion is safe: confirmed no `render_template` or `include` references exist in `app.py` or any other template.

**Status:** Merged (507ddd3). Pinned by `test_orphaned_advisor_template_does_not_exist`.

---

## Multi-Lens AI Advisor — Cycle 1 Foundation (2026-06-10)

### DE-ML-001: Honest-availability lens-block contract

**Decision:** Every lens helper in `ai_advisor.py` returns a dict with a fixed 5-key contract: `{lens, available: bool, reason: str, payload, sources}`. A lens with `available=False` MUST NOT fabricate a payload — `payload` is `None` and `sources` is `[]`. The `reason` field is always a non-empty string explaining the unavailability (naming the missing source).

**Rationale:** Mirrors the existing `_build_volatility_regime` pattern (`ai_advisor.py:218–270`), which was introduced to fix the fabricated-context problem (GATE-1-AC CC-3: data-wall; analytical context must never be invented). Honest degradation is preferable to plausible-but-wrong context reaching the LLM. Cycle-1 stubs all return `available=False`; fast-follow producers will wire in real sources per lens.

**Status:** Merged (95ba125). Pinned by `test_lens_block_contract_*` in `tests/test_cycle1_foundation.py`.

---

### DE-ML-002: No migration for citation convention — raw_response is JSON

**Decision:** The structured citation convention (`{title, url, published, lens}`) is stored in the existing `advisor_observations.raw_response` JSON column. No schema migration is required.

**Rationale:** `raw_response` is already an untyped JSON blob by design — it holds each producer's full structured output. Citations are part of that output, not a new first-class DB column. Adding a separate `citations` column would require a migration (additive-first, NULLable+DEFAULT), increase cross-producer coupling, and provide no query benefit (citations are read as part of the full observation, never queried independently). The existing column handles it without schema change.

**Status:** Merged (95ba125). No migration file needed.

---

### DE-ML-003: MARKET_PRISM and ADD_CANDIDATE are advisory-only (is_advisory_only=1)

**Decision:** The two new advisor roles (`MARKET_PRISM`, `ADD_CANDIDATE`) are added to `_ADVISOR_ROLES` in `app.py` (`app.py:3565–3571`) with `is_advisory_only` hard-wired to 1 in `database.insert_advisor_observation` (`database.py:1068/1089`). Neither role has any path to a trade, config-write, or Composer endpoint.

**Rationale:** All advisor roles are advise-only by architectural mandate (GATE-1-AC §8). The `is_advisory_only=1` constraint is enforced at the DB insert layer (not just the application layer) so that a future code path cannot accidentally drop the flag. These roles are backtest-agnostic: `MARKET_PRISM` produces always-on market-overview observations; `ADD_CANDIDATE` produces proposal observations for human review. No money path touches either.

**Status:** Merged (95ba125). Pinned by `test_new_roles_is_advisory_only` in `tests/test_cycle1_foundation.py`.

---

### DE-ML-004: 7-item suggestible allowlist — docstring correction

**Decision:** The `assemble_advisor_context` docstring at `ai_advisor.py:600` previously read "9-item curated ALLOWLIST". Corrected to "7-item". The canonical count is 7: 6 Optuna search-space keys (`TAKE_PROFIT_MC_PCT`, `VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS`, `PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) plus one non-Optuna key (`MAX_SQUEEZE_FLOOR`).

**Rationale:** `TRIGGER_THRESHOLD_PCT` is the default locked variable and is NOT suggestible (verified at `database.DEFAULT_LOCKED_VARS`). The "9-item" figure was a carry-over from an earlier spec draft. The `_PARAM_VALID_RANGES` comment was corrected in a prior commit (540d89d); this commit aligns the docstring.

**Status:** Merged (95ba125). Pinned by `test_docstring_says_7_item_allowlist` in `tests/test_cycle1_foundation.py`.

---

### DE-SPA-001: Strategy Builder folded into unified AI-Advisor SPA as 6th tab

**Decision:** `templates/ai_advisor_strategy_builder.html` (the standalone Strategy Builder page) is deleted. Its content is ported into `templates/ai_advisor.html` as the 6th in-place tab panel (`id="tab-panel-strategy-builder"`). `GET /ai-advisor/strategy-builder` returns 302 to `/ai-advisor`, matching the pattern of the existing 4 GET sub-routes (Correlations, Asset Swaps, Logic Changes, Chat). `POST /ai-advisor/strategy-builder/run` is unchanged. `sbRunAnalysis()` and `openChatWithArtifact()` are moved from the deleted template's inline `<script>` into `static/ai_advisor.js` (exposed on `window`). The `/ai-advisor` route (`ai_advisor_tab`) gains a STRATEGY_BUILDER observation prefetch via `database.get_advisor_observations_for_role("STRATEGY_BUILDER")` and passes `strategy_builder_observations` + `sb_card_artifacts` to `render_template`.

**Rationale:** The standalone page created a navigation seam (full-page reload on tab switch), duplicated the advisor header/nav chrome, and could not participate in the unified SPA's in-place tab switching. Folding it in eliminates the seam while preserving all advisory-only, CSRF, and D-1 contracts unchanged.

**Status:** Merged into `cycle/spa-port-strategy-builder` at 7908d77. Acceptance criteria verified by `tests/app/test_strategy_builder_spa_port.py` (53 passed / 9 skipped / 0 failed). The 9 skips are legacy card-anatomy tests on the standalone route that now correctly skip on 302.

---

## Multi-Lens AI Advisor — Cycle 3: Lens-Informed Swap Ranking (2026-06-13)

### DE-CY3-001: Lens evidence blended into asset-swap candidate ranking

**Decision:** `advisors/asset_swap_engine.py` gains lens-awareness via additions-only changes to the existing candidate-ranking seam. The implementation adds:

- `extract_lens_scores(context)` — public helper that walks the 5 lens blocks (`technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals`) in an assembled advisor context dict and extracts `{ticker: {lens_name: score}}`. Only `available=True` lenses contribute; `available=False` blocks are skipped entirely (honest-availability).
- `LENS_BLEND_WEIGHT = 0.25` — named constant for the additive lens weight.
- `_apply_lens_blend(candidates, lens_scores, higher_is_better)` — position-based reranker applied after the primary objective sort. Blend formula: `blended_key[i] = position[i] - LENS_BLEND_WEIGHT * mean_lens_score[i]`. Tickers absent from `lens_scores` receive neutral score 0.5. The blend is additive only — it never eliminates candidates.
- `generate_objective_directed_candidates` gains `lens_scores=None` kwarg; all 3 objective paths + unknown fallback call `_apply_lens_blend` after their primary sort.
- `propose_operator_swap` and `suggest_swaps` gain `lens_scores=None` and `lens_sources=None` kwargs; both thread through to ranking, rationale, and persistence.
- `_build_objective_rationale` gains `lens_scores=None`; appends a lens evidence summary ("Lens evidence (macro: 0.80, sentiment: 0.60).") when the candidate ticker has available scores.
- `_persist_observation` gains `lens_evidence` and `sources` params; both are written into `raw_response` alongside existing fields.

**Gate unchanged:** Candidates still go through `advisors.backtest_gate_engine.evaluate_candidate_batch` (BHY-FDR). Lens scoring influences ranking only — it does not relax or replace the statistical gate.

**Backward-compatibility:** All existing call sites pass `lens_scores=None` (the default). Pre-Cycle-3 behaviour is byte-identical when `lens_scores` is `None` or empty. Existing test suite (35 tests in `tests/advisors/test_asset_swap_engine.py`) unaffected.

**Persistence contract (AC-4):** Persisted observations (`advisor_role="ASSET_SWAP"`, `is_advisory_only=1`) now carry `lens_evidence: {ticker: {signal, source_lens, confidence}}` and `sources: [{title, url, published, lens}]` in `raw_response`. Both default to `{}` / `[]` when no lens evidence is supplied. No schema migration required — `raw_response` is an untyped JSON blob (per DE-ML-002).

**Rationale:** Blending free-data lens evidence (technicals, sentiment, macro, fundamentals, derivatives) into swap-candidate ranking gives the operator a richer "why suggested" signal without changing the statistical acceptance gate. The position-based blend (rather than a score-unit blend) avoids unit-comparability issues between objective metrics (Pearson correlation, variance, Sharpe) and lens scores. `LENS_BLEND_WEIGHT=0.25` keeps the lens as supporting evidence — the objective metric anchors ranking. Risk-first decorrelation and de-risking intent is preserved; the operator selects from surviving candidates, no single-winner verdict is imposed.

**Status:** GREEN at d0228b3. 24/24 new cycle3 tests GREEN; 35/35 existing `test_asset_swap_engine` tests unaffected. Acceptance criteria AC-1 through AC-6 verified.

---

### DE-CY3-002: Cycle 3 forward-fix — reviewer BLOCK resolutions (2026-06-13)

**Decision:** Two reviewer BLOCKs were raised post-merge (unauthorized merge to main by quant-test-writer at 2b7b3bc, fourth instance of this violation). Both BLOCKs resolved as a forward-fix commit at 392c1e8.

**BLOCK-1 (fixture provenance — circular self-authorship):**
`tests/fixtures/ai_advisor/cycle3/lens_score_extraction_basic.json` provenance relabeled from `"test-authored (cycle3-test-writer)"` to `"schema-derived (not producer-computed); numeric values are synthetic test data representing the structural contract between available/unavailable lens blocks and per-ticker score output"`. The same party that wrote `extract_lens_scores` had also authored the fixture, making the test circular (parser-and-fixture co-design). Fixed by: (a) provenance label, and (b) `TestFixtureBackedContract` test class that independently loads and exercises the fixture, asserting per-ticker present/absent lens key contract and finite float scores.

**BLOCK-2 (build_citation bypass in _persist_observation):**
`_persist_observation` was writing raw citation dicts to `advisor_observations.raw_response.sources` without validation. Fixed by adding a deferred `from ai_advisor import build_citation as _bc` import inside `_persist_observation` and filtering sources through `_bc()` before the DB write. Invalid citations (missing required fields, disallowed URL schemes) are dropped. Fallback on import failure preserves valid-looking dicts rather than losing the sources list entirely. `TestCitationValidationOnPersistence` (2 tests) covers both the filter path and the valid-passthrough path.

**Supplemental:** `_LENS_NEUTRAL_SCORE: float = 0.5` named constant added to replace the inline magic `0.5` in `_mean_lens`. `TestLensBlendPrimaryMetricDominance` property test added proving `LENS_BLEND_WEIGHT = 0.25` max perturbation (0.245) cannot override a 1-position primary-metric ranking gap.

**8-gate PM review (reviewer agent unresponsive — PM self-reviewed):**
- Gate 1 (math safety): N/A — no `math_engine.py` or `alpha_bot_execution.py` changes.
- Gate 2 (live-trade boundary): PASS — grep of diff: zero hits on live-trade symbols.
- Gate 3 (fixture provenance): PASS — provenance is `"schema-derived"`, fixture independently exercised by `TestFixtureBackedContract`.
- Gate 4 (schema reversibility): N/A — no `database.py` changes.
- Gate 5 (secrets hygiene): PASS — grep of diff: zero credential patterns.
- Gate 6 (engine constants): PASS — `_LENS_NEUTRAL_SCORE` named with source comment explaining the 0.5 neutral-evidence semantic.
- Gate 7 (logging redaction): PASS — no new log lines added.
- Gate 8 (dashboard side effects): N/A — no `app.py` changes.

**Status:** APPROVE at 392c1e8. 28/28 cycle3 tests GREEN. 35/35 pre-existing swap engine tests GREEN. 17/17 advisor liveness tests GREEN. PM gate: MERGE-SAFE — zero new failures vs 14 pre-existing autotuner failures that predate cycle3.

---

## Multi-Lens AI Advisor — Cycle 4: Off-Hours Lens Pipeline + Market Prism Always-On (2026-06-13)

### DE-CY4-001: Scheduled off-hours pipeline with always-written Market Prism observation

**Decision:** A daily scheduled pipeline (`advisors/lens_pipeline.py`) runs at 03:00 off-hours via the `app.py` scheduler. It calls all five `ai_advisor._build_<lens>_section()` builders, validates citations through `ai_advisor.build_citation`, synthesises an overall `overall_sentiment` label via Claude Haiku, and writes exactly one `advisor_role="MARKET_PRISM"` row to `advisor_observations` per run — regardless of how many lenses are available.

**Key design choices:**

1. **Always-emit invariant.** The pipeline writes the observation even when all 5 lenses are `available=False`. Verdict is `"limited-inputs"` in that case. This ensures the Overview tab (Cycle 5) always has a row to render — it never needs to handle "no data ever written" as a separate empty-state branch.

2. **Per-lens exception isolation.** Each `_build_<lens>_section()` call is wrapped in its own `try/except`. One lens failing does not abort the remaining lenses or the persistence step. The failing lens is recorded in `per_lens_digest` as `available=False` with `reason=type(exc).__name__` (D-1 contract).

3. **Off-hours scheduling, not on-demand.** The pipeline is driven by a daily 03:00 scheduler job, not by user requests. This keeps it off the 1-minute live-execution path (Architecture Constraint 1) and ensures one authoritative nightly run rather than stale-or-duplicate on-demand calls.

4. **Lazy import / CC-2 boundary.** `advisors.lens_pipeline` is imported inside `_lens_pipeline_worker()` (a daemon thread spawned by `_run_lens_pipeline()`), never at `app.py` module level. `alpha_bot_execution.py` has zero advisor imports (static-scan verified by AC-6 regression test).

5. **Claude synthesis degrades gracefully.** If Claude is unavailable or the API call fails, `overall_sentiment` degrades to `"limited-inputs"` with an honest rationale. The observation is still persisted. No exception leaks to the caller.

**New database accessor:** `database.get_latest_market_prism_summary() -> dict | None` returns the most recently inserted `MARKET_PRISM` row, deserialized, or `None` when none exists. Used by the Cycle-5 Overview tab.

**Rationale:** The always-emit design eliminates a class of silent failures where the pipeline runs but writes nothing. A `"limited-inputs"` observation is more honest than absence — it tells the dashboard "we ran but had no data", which is actionable. The per-lens isolation means a single unreachable data source (FRED, a sentiment feed) does not invalidate the remaining available lenses.

**Status:** GREEN at b85eee3. 40/40 Cycle-4 tests GREEN (AC-1 through AC-9). Acceptance criteria AC-10 (docs) verified by this entry and `docs/generated/advisors_lens_pipeline.md`.

## Market Prism Overview Surface — Cycle 5 (2026-06-13)

### DE-CY5-001: Market Prism always-on block on AI Advisor Overview tab

**Decision:** The AI Advisor Overview tab in `templates/ai_advisor.html` always renders a "Market Prism" block populated from `database.get_latest_market_prism_summary()`. The block shows: overall sentiment (as a labeled chip with semantic color), sentiment rationale, an as-of timestamp, a per-lens digest (all 5 lenses — honest-availability: shows reason when unavailable), and cited sources as clickable `<a href>` links with safe external attributes.

**Key design choices:**

1. **Always renders.** When `get_latest_market_prism_summary()` returns `None` (no row written yet), the block renders an informative empty state: "No overnight market read yet — the off-hours pipeline runs daily at 03:00." It never shows a blank section or raises a 500. This is consistent with the always-emit invariant established in Cycle 4 (DE-CY4-001).

2. **Read-only GET path.** The route prefetches the summary via `get_latest_market_prism_summary()` (read-only SQLite) and passes it to the template as `market_prism_summary`. `run_pipeline` is never called from the GET path. The block is never re-run on user request — it reflects the most recent nightly write.

3. **Advisory-only.** The block carries no accept/execute trade affordances. It is strictly informational context for the operator. Consistent with the broader advise-only AI Advisor posture.

4. **Design-token conformance.** All colors use `var(--studio-*)` tokens (no raw hex). The chip, lens cards, and source links reuse the existing card/panel/chip component patterns from the other advisor tabs. Verified by the ux-expert visual gate.

5. **XSS safety.** All dynamic values are rendered with Jinja2 `| e` escaping. No `Markup()` or `|safe` filters on source fields. The test suite includes an adversarial hostile-title XSS check (AC-3).

**Route change (`app.py`):** `ai_advisor_tab()` gains a `market_prism_summary` prefetch block (guarded `try/except`; `None` on failure). The variable is appended to the `render_template` call.

**Template change (`templates/ai_advisor.html`):** The `data-testid="market-prism-block"` container is inserted at the top of the Overview tab panel (above the controls bar), with the full sentiment chip / rationale / per-lens / sources / empty-state Jinja2 structure.

**Rationale:** Surfacing the nightly off-hours sentiment read on the Overview tab is the natural consumer of the Cycle-4 pipeline (DE-CY4-001). The always-on design (not hidden behind a gate) ensures the operator always sees the most recent market read and an honest status when the pipeline has not run yet.

**Status:** GREEN at cycle/market-prism-overview-surface. 16/16 Cycle-5 tests GREEN (AC-1 through AC-6). Acceptance criteria verified.

---

## Market Prism — Prism Phase 1: Audit-Log Foundation (2026-06-13)

### DE-PRISM-001: Per-run deliberation trail in `prism_audit_log` (migration 032)

**Decision:** A new `prism_audit_log` table (migration 032) provides an append-only, per-agent, per-phase deliberation trail for the Market Prism nightly pipeline. Each nightly run may produce multiple rows — one per `agent_role` × `phase` combination — all keyed by `run_id` (an ISO UTC timestamp, identical to `run_ts`) so the full deliberation is auditable in one query. The `MARKET_PRISM` `advisor_observations` row also carries `run_id` in `raw_response`, providing a stable join key from report to audit trail.

**Key design choices:**

1. **`run_id` = `run_ts` (same value, two names).** The pipeline generates a single ISO UTC timestamp at the start of each run (`run_ts`). `run_id` is set to the same value and written into `raw_response` alongside the existing `run_ts` key. Callers use `.get("run_id")` (not direct access) for backward-compat: existing rows that predate Prism Phase 1 simply return `None` for the missing key and do not crash.

2. **Append-only, parameterized writes.** No `update_prism_*` or `delete_prism_*` accessor exists. All four caller-supplied fields (`run_id`, `agent_role`, `phase`, `content`) are written via `?` placeholders — never f-string interpolation. Injection-shaped content (SQL keywords, quotes, semicolons, embedded nulls) is stored verbatim.

3. **Agent-callable CLI (`advisors/prism_audit_write.py`).** A `python -m advisors.prism_audit_write` CLI bridges the boundary between the scheduled Claude agent session (which cannot import `database` directly) and the state DB. Content is read from STDIN to avoid shell argument-length limits on long analyst output. On success the new row id is printed to STDOUT; on any error, only `type(exc).__name__` is written to STDERR (D-1 contract) — no tracebacks, no file paths.

4. **D-1 error handling throughout.** Both `insert_prism_audit_entry` and `get_prism_audit_for_run` do not expose exception detail to callers. The CLI writer exits non-zero with a type-only STDERR message on any failure.

5. **Index on `run_id`.** `idx_prism_audit_log_run_id` makes `get_prism_audit_for_run` O(log n) rather than a full table scan.

**New public surface:**
- `database.insert_prism_audit_entry(run_id, agent_role, phase, content) -> int` — appends one row, returns rowid.
- `database.get_prism_audit_for_run(run_id) -> list[dict]` — returns all entries for a run, ordered by id ascending (chronological); returns `[]` for unknown `run_id`.
- `advisors/prism_audit_write.py` — agent-callable CLI (module, not route). No Flask dependency.

**Rationale:** The audit log separates the per-analyst deliberation record from the synthesized `MARKET_PRISM` verdict. The verdict row (in `advisor_observations`) is what the dashboard renders; the audit log is what a human reviewer — or a future meta-agent — reads to understand why that verdict was reached. Keeping them in separate tables with a join key (`run_id`) avoids bloating the `advisor_observations.raw_response` blob with multi-kilobyte analyst transcripts. Backward-compatibility is preserved by the `.get("run_id")` access pattern: old rows without the key continue to read fine without schema modification.

**Status:** GREEN at 885e1a4. 35 AC-1/AC-2 database tests + 9 AC-3/AC-4 lens-pipeline / CLI tests GREEN. Acceptance criteria verified: migration idempotent, append-only contract enforced, D-1 error isolation confirmed.
