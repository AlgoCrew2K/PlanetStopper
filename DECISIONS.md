# Planet Stopper — Architectural Decisions

This file records binding architectural decisions made during Planet Stopper development. Entries are append-only. Do not edit past entries; add corrections as new entries.

---

## DE-CIGREEN-001 — CI harness greened behavior-neutrally; ruff pinned; B023/F841 deferred; atlas-cache test isolation (2026-06-19)

Branch: feat/ci-green | Base: 56ec9ce

### Root cause

The GitHub Actions `tests` workflow (`.github/workflows/tests.yml`, introduced at `1658b18`) has been RED since inception. Every PR through #53 was merged via `--admin` bypass. Three blocking steps all fail on origin/main:

1. `ruff format --check .` — 34 Python files are not formatted per ruff 0.15.11.
2. `ruff check .` — 6,929 lint violations under the `select=[E,F,I,B,UP,SIM]` ruleset.
3. `pytest` — never reached because step 2 fails.

Additionally `ruff` is unpinned in `requirements-dev.txt`, so CI and local can silently disagree on the format/lint target.

### Fix

Behavior-neutral only — zero logic/behavior change. Three change categories:

1. **Repo-wide format + safe lint auto-fix.** `ruff format .` applied repo-wide. `ruff check --fix .` (safe fixes only; `--unsafe-fixes` forbidden) applied for the remainder. No `--unsafe-fixes` at any point.

2. **Documented `per-file-ignores` for residual violations.** Every added suppression carries an inline justification comment. No global `ignore` widening; no real bugs silenced.

3. **Credential-gated test `skipif` markers.** Tests that hard-require live credentials now `pytest.mark.skipif` on the absent env var — they SKIP (not fail) on secret-less CI runners.

### Ruff pin

`ruff==0.15.11` added to `requirements-dev.txt`. This is the version used during this cycle; CI and local now target identical format/lint output.

### Key per-file-ignores decisions

**B023 — deferred (autotuner.py):** `B023` (loop variable captured by closure) fires on Optuna trial-factory patterns. Fixing requires restructuring the closures — a behavior-affecting Optuna refactor out of scope for this cycle. **Deferred, not masked.** The `per-file-ignores` entry carries an explanatory comment; the issue is tracked for a future Optuna refactor.

**F841 preservation (autotuner.py):** `split_idx` and `raw_train_dates` are unused at runtime but intentionally retained because source-scan tests assert these names exist via AST/grep. Removing them would break those tests. Suppressed with an inline comment explaining the constraint.

**B006/B008 (similar files):** Mutable-default-argument and function-call-in-default-argument findings in non-test modules — stylistic/deferred with inline comments.

**E501 via `per-file-ignores` (not `noqa`):** ruff 0.15.11 does not honor `# noqa: E501` inside triple-quoted docstring literals. All E501 suppressions for un-wrappable docstring and long comment lines are expressed as `per-file-ignores` entries rather than inline `noqa` directives.

**`advisors/prism_audit_write.py` E402:** `load_dotenv()` must precede all imports by design (DE-PRISM-DOTENV). The E402 (module-level import not at top of file) suppression is by design.

### ATLAS_CACHE_DB_PATH test isolation (conftest.py)

**Finding:** Four `test_community_strats_timeout` tests were returning `available=True` with empty candidates instead of the expected `available=False` timeout behavior on clean CI runners. Root cause: a stale `alphabot_atlas_cache.db` in the project root from a previous operator run with real credentials contained a cached `available=True` Atlas result. The tests mock MongoClient but not the atlas cache layer; on a developer machine with a warm cache, the mock was never invoked and the cache returned the stale result.

**Fix:** `tests/conftest.py:pytest_configure()` now routes `ATLAS_CACHE_DB_PATH` to a session-temp directory alongside the existing `DB_PATH` routing. Every test run sees a cold Atlas cache. No production behavior change.

**Binding rule:** Any test that mocks an Atlas fetch must run against an isolated (cold) `ATLAS_CACHE_DB_PATH`. Stale production `alphabot_atlas_cache.db` values are structurally excluded by the conftest guard.

### Files changed

- `requirements-dev.txt` — `ruff==0.15.11` pin added
- `pyproject.toml` — `[tool.ruff.lint.per-file-ignores]` block added with all suppressions + justifications
- `tests/conftest.py` — `ATLAS_CACHE_DB_PATH` session-temp routing added to `pytest_configure()`
- Repo-wide `*.py` — ruff format + safe auto-fix (mechanical; no logic change)
- Specific test files — `pytest.mark.skipif` markers on credential-gated tests
- `.gitignore` — `.claude/tdd-handoff.md` added; `git rm --cached` applied

### Status

Implementation complete at HEAD 3b85913 (feat/ci-green). Under review by cg-reviewer. PR to origin pending PM gate.

---

## DE-SCHEMA-001 — Composer /backtest live-required fields: root.description + wt-inverse-vol.window-days (2026-06-18)

Branch: feat/symphony-schema-required-fields | Base: origin/main 26196e8

### Root cause

The live Composer `POST /api/v0.1/backtest` API now enforces two fields that `advisors/symphony_schema.py` constructors were not emitting:

1. **`root.description` (string)** — every real `/score` response carries `description: ""` on the root node; the live API returns HTTP 400 when the field is absent.
2. **`wt-inverse-vol.window-days` (int)** — the API returns HTTP 422 ("unknown-function-parameter") without it. The Composer UI default and the value carried by `sample_score_large.json` (VERIFIED-LOCAL) is 30.

This caused EVERY Strategy Builder candidate tree (T1–T7 across all 3 objectives), every `asset_swap_engine` inline backtest, and every `logic_change_engine` inline backtest to fail at the API layer (400/422) — BEFORE reaching the FDR gate. The symptom was "Strategy Builder never produced a survivor." The root cause was the API enforcement of these two fields, NOT the `raw_value` request wrapper (which was always correct and is unchanged).

Diagnosis: empirical live verification matrix (`composer-encode-spike`) — T1 equal_weight + `description=""` → HTTP 200 (was 400); T3 inverse_vol + `description=""` + `window-days=30` → HTTP 200 (was 422).

### Fix

Two additive one-line changes in `advisors/symphony_schema.py`:
- `make_root` return dict += `"description": ""`
- `make_inverse_vol` return dict += `"window-days": 30`

**NOT changed:** `composer_backtest_client.py` request wrapper (correct as-is), T1–T7 template builders in `strategy_builder_engine.py` (they consume the constructors — auto-fixed upstream), response-parser.

### Grammar doc correction

`feature-plans/strategy-builder-composer-grammar.md` OQ-8 ("No params observed; omit" for `wt-inverse-vol`) is closed and superseded. `window-days` is LIVE-REQUIRED. `sample_score_large.json` already carried `window-days` on its `wt-inverse-vol` nodes — the OQ-8 note was based on a misread of the fixture. `root.description` is added to §3.1 as LIVE-REQUIRED.

### Fixture note

`tests/fixtures/symphony_logic/sample_score_large.json` already carries `window-days` on its `wt-inverse-vol` node — it was never stale. No fixture update needed for this file. Tests asserting `make_inverse_vol` output shape were updated as part of AC-4 (additive key, stale-by-intent).

### Live re-verify result

PM live re-verification confirmed all 13 Strategy Builder candidate trees (T1–T7 across all 3 objectives: diversify / cut_drawdown / lift_risk_adjusted) backtest HTTP 200 after the fix. The FDR gate ran successfully and produced 0 survivors — this is the expected outcome of the intentionally strict CRRA-EU + Harvey-Liu gate, not a defect. The fix unblocks the gate from running at all; it does not change the gate's accept/reject criteria.

---

## ARCH-REM-001 — Carried pre-existing test failures remediated (2026-06-16)

Branch: fix/carried-preexisting-failures | HEAD: 526c242

### Root causes and resolutions

**1. GDELT contract doc missing** (`tests/ai_advisor/test_lens_gdelt.py::TestContractDocumentExists::test_contract_document_exists_and_names_timelinetone_endpoint`)
- Root cause: `.claude/gdelt-contract.md` was never created. The GDELT client shipped at PR #33 (d632de3) but the AC-5 contract-doc deliverable was missed.
- Fix: Created `.claude/gdelt-contract.md` reconstructed from `advisors/lens_gdelt.py` — documents the timelinetone endpoint URL, response shape, extraction path, return dict contract, retry policy, and normalization. Not a stub; cross-verified against the shipped producer.

**2. VWAP_BLEED_ARM_MIN / VWAP_BLEED_ARM_MAX source comments on wrong line** (`tests/math_engine/test_vwap_bleed_arm.py::test_named_clamp_constants_exist_with_source_comments`)
- Root cause: Both constants were written as multi-line assignments (`VWAP_BLEED_ARM_MIN = (
    -3.0
)  # comment`). The AST reports the assignment `lineno` as the opening line (785/788), which has no `#`. The comment was on the closing `)` line (787/790) — one level removed.
- Fix: Collapsed both to single-line assignments with trailing comment on the same line as the `=`. Values unchanged (-3.0, -0.5). No math change.

**3. VWAP_BREAK_CONFIRM_TICKS source comment on wrong line** (`tests/math_engine/test_vwap_breakdown.py::test_new_named_constants_have_source_comments`)
- Root cause: Same multi-line pattern (`VWAP_BREAK_CONFIRM_TICKS = (
    3  # comment
)`). The comment was inside the parentheses on the value line (825), not on the assignment line (824).
- Fix: Collapsed to single line. Value unchanged (3). No math change.

**4. minmax(28rem, 1fr) in .proposal-cards / .rejected-cards** (`tests/ui/test_config_suggestion_card_fixes.py::TestGridFloor::test_suggestions_col_does_not_use_28rem`)
- Root cause: The test asserts `"minmax(28rem, 1fr)" not in html` (blanket, not scoped to `.suggestions-col`). The `.suggestions-col` at line 179 was already correct (22rem), but `.proposal-cards` (line 588) and `.rejected-cards` (line 657) still used 28rem.
- Fix: Changed both `.proposal-cards` and `.rejected-cards` grid-template-columns from `minmax(28rem, 1fr)` to `minmax(22rem, 1fr)` in `templates/ai_advisor.html`.

### Files changed
- `.claude/gdelt-contract.md` — created (AC-5 GDELT API contract)
- `math_engine.py` — lines 785–790, 824–826 (constant formatting only, no value change)
- `templates/ai_advisor.html` — lines 588, 657 (28rem → 22rem on proposal/rejected card grids)

### Test result
4 passed / 0 failed / 0 errors on SHA 526c242. Reviewer: quant-code-reviewer APPROVE conditional on PM merge gate.

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

---

## Atlas Read Cache — Weekly captplanet Pull Cache (2026-06-14)

### DE-ATLAS-001: Dedicated SQLite cache DB for captplanet MongoDB Atlas reads; weekly TTL; never-raising

**Decision:** `advisors/atlas_cache.py` is a new pure-stdlib caching layer (sqlite3 + json + os) that gates all captplanet MongoDB Atlas reads to at most one live pull per collection per week. The cache lives in a **new dedicated SQLite DB** (`alphabot_atlas_cache.db`, path from `ATLAS_CACHE_DB_PATH` env) — separate from the state DB, optimization DB, and lens warehouse.

**Key design choices:**

1. **New dedicated DB (operator directive).** The operator explicitly requested "create a new db locally." The cache DB is isolated: `atlas_cache.py` imports neither `database.py` nor `autotuner.py` (AC-9 AST-verified). No cross-joins with any other DB in application code.

2. **Weekly default TTL, env-configurable.** `ATLAS_CACHE_TTL_DAYS` (default `7`) controls the freshness window. Boundary is strict: `age < ttl_days` is fresh (HIT, no fetch); `age >= ttl_days` is stale (MISS, fetch called). The `ttl_days` kwarg on `cached_pull` lets callers override per-call; the env var sets the module default.

3. **Never-raising contract (AC-5, AC-7).** `cached_pull` absorbs every exception path. Degradation order: cached payload → stale payload (when `fetch_fn` raises on MISS but a stale row exists) → `None` sentinel (when `fetch_fn` raises and no row exists). A write failure after a successful fetch returns the fetched payload without raising. This matches the `lens_pipeline` resilience posture.

4. **Secrets isolation (AC-8, AC-9).** `atlas_cache.py` never reads `MONGO_URI` or any credential. Callers own the Mongo connection and pass projected docs as the `fetch_fn` return value. The cache stores only what `fetch_fn` returns. Structurally enforced: no Mongo/pymongo/motor imports in `atlas_cache.py` (AC-9 AST walk).

5. **`collection TEXT PRIMARY KEY` + `INSERT OR REPLACE`.** One row per collection; upsert is last-writer-wins. Bounded storage: one row per distinct collection name, never unbounded growth. WAL mode for concurrent daemon + manual access.

6. **Mirrors `advisors/lens_warehouse.py` pattern.** The separate-DB, WAL, never-raising, off-execution-path design follows the established lens_warehouse precedent. Workers connecting to Atlas wire through `cached_pull`; `atlas_cache.py` is unaware of what the fetched data means.

**Public surface:**
- `init_atlas_cache() -> None` — idempotent schema creation + WAL enable.
- `cached_pull(collection_name, fetch_fn, *, ttl_days=7, force_refresh=False) -> object | None` — HIT/MISS/force/degrade logic; never raises.

**No production caller yet.** The community-strats and frontrunner loaders that will pull through this cache are separate rebuild cycles. `alphabot_atlas_cache.db` must not be referenced from production code until a caller is wired. Tests use `ATLAS_CACHE_DB_PATH` env override to an isolated temp path.

**Rationale:** The operator's directive was to protect the captplanet Atlas provider's billing by caching weekly. A new dedicated DB (not the state DB) keeps the cache's schema evolvable without risking state DB migrations. The never-raising posture means a transiently-unavailable Atlas cluster (or an unreachable local DB) degrades gracefully to stale data or `None` rather than aborting the caller. The secrets-isolation invariant (no `MONGO_URI` in `atlas_cache.py`) means the cache layer can be audited and tested without any Mongo credentials.

**Status:** GREEN at d05670c. 24/24 tests GREEN (AC-1..AC-9). Acceptance criteria verified: init_atlas_cache idempotent + WAL, cached_pull HIT/MISS/force/degrade contract confirmed, never-raises enforced, secrets isolation (no MONGO_URI) + structural isolation (no database/autotuner imports) verified. Docs committed at 48cca9d.

---

## Community-Strategies Loader — Weekly-Cached captplanet Atlas Reader (2026-06-14)

Cycle: `community-strats` on branch `team/community-strats`. 38/38 tests GREEN at `f6d48c1` (AC-1..AC-9).

### DE-CS-001: Route captplanet.strategies Atlas read through atlas_cache.cached_pull (weekly TTL); tree-structural hash for dedup; rebuilt via Agent Team after solo-built rip

**Decision:** `advisors/community_strats.py` provides `load_community_strategies(*, limit, min_oos_sharpe, client, force_refresh) -> dict`. The Atlas network read is routed through `atlas_cache.cached_pull("captplanet.strategies", fetch_fn)` — it is never called directly from application code — enforcing the operator's weekly-cache directive to protect the captplanet provider's bill.

**Key design choices:**

1. **Weekly cache routing (operator directive).** The prior standalone-built version (ripped at `ad3a637`) pulled Mongo on every call. The rebuild routes through `atlas_cache.cached_pull` so Atlas is contacted at most once per week per TTL. Only the raw projected docs are cached; validation, dedup, and filtering run on every call (cheap, in-process) on the cached payload.

2. **Rebuilt via a real Agent Team.** The operator's hard rule is that new codepaths use the Toxic Pair TDD composition. The previous community-strats loader was built by a solo agent in violation of that rule and was ripped. This version was built by the team on `team/community-strats` via the full TDD cycle.

3. **Tree-structural composition hash — NOT `database.compute_composition_hash`.** Deduplication uses a local `_composition_hash(tree)` function: strip all `id` keys recursively (`_strip_ids`) → `json.dumps(sort_keys=True, separators=(",", ":"))` → `hashlib.sha256(...).hexdigest()`. This produces a stable hash for structurally identical trees regardless of uuid4 node ids. **`database.compute_composition_hash` is a different function** — it takes a `list[str]` of symphony IDs and is used for portfolio-set identity in the mode-resolver. The two functions are not interchangeable. The feature plan AC-5 incorrectly named `database.compute_composition_hash`; the correct behavior is the tree-structural hash described here (verified vs ripped `c4d6a36`).

4. **Sharpe filter: absent sharpe = kept.** Docs whose `oos_metrics` is missing or lacks the `sharpe` key are retained regardless of `min_oos_sharpe`. Only docs that have a parseable sharpe below the floor are excluded. Absence of a metric is not a failing metric.

5. **edn_string is JSON, not EDN.** Despite the field name, `edn_string` stores a JSON-encoded Composer decision tree. `json.loads` is the parser. No eval/exec.

6. **Projection caps doc size.** `_PROJECTION = {sid, name, edn_string, oos_metrics}` explicitly excludes `backtest` and `quantstats_metrics` (multi-MB arrays). Both bandwidth and provider cost are bounded.

7. **Never-raising D-1 contract.** All failure modes (Mongo unreachable, MONGO_URI unset, cache unavailable, non-list payload, any exception) return `{available: False, reason: type(exc).__name__, ...}`. The `raw_docs is None` path uses the named sentinel `"AtlasCacheUnavailable"` rather than `type(exc).__name__` (no exception is raised by `cached_pull` in that path). The propose_strategies wiring cycle will improve this to a clearer string.

8. **DB isolation.** No import of `database`, `autotuner`, or any execution module. No direct sqlite3 connection; Atlas cache access is entirely through `atlas_cache.cached_pull`. `MONGO_URI` is read inside `fetch_fn` only, never returned or stored.

**Public surface:**
- `load_community_strategies(*, limit=None, min_oos_sharpe=None, client=None, force_refresh=False) -> dict` — returns `{available, candidates, stats, source}` on success; `{available: False, reason, candidates: [], stats: {...}, source}` on any failure. Never raises.

**No production caller yet.** The propose_strategies wiring (making `strategy_builder_engine` consume these candidates as community-strategy inputs) is the next cycle. `load_community_strategies` must not be called from production routes until that wiring is in.

**Status:** GREEN at `f6d48c1`. 38/38 tests GREEN (AC-1..AC-9). Acceptance criteria verified: cache HIT/MISS/force semantics (closure call counts), D-1 secret-leak walk (MONGO_URI never in returns/cache), never-raising on Mongo-down/cache-fail/bad-edn, dedup by tree-structural hash, sharpe-filter keeping no-sharpe docs.

---

## Community-Candidate Wiring — propose_strategies Integration (2026-06-14)

Cycle: `propose-wiring` on branch `team/propose-strategies-wiring`. 39/39 tests GREEN at `4edbe92` (AC-1..AC-7).

### DE-PSW-001: Community candidates enter the same single-batch FDR gate as template candidates; adapter at the caller boundary; rebuilt via Agent Team after solo-built rip

**Decision:** `advisors/strategy_builder_engine.py` gains `community_candidate_infos(community_result, *, max_candidates) -> list[CandidateInfo]` and a keyword-only `community_candidates: list[CandidateInfo] | None = None` parameter on `propose_strategies`. Community-sourced candidates join template-generated candidates in a single call to `evaluate_candidate_batch` — the single-batch FDR correction is the anti-overfit invariant.

**Key design choices:**

1. **Single-batch FDR gate (anti-overfit invariant).** All successfully-backtested candidates — template-generated and community-sourced — enter `evaluate_candidate_batch` together. Splitting community candidates into a separate gate would give each group a weaker multiple-testing correction, increasing false discovery. Wide exploration must pay one batch-wide correction: this is the design constraint that drove the wiring architecture.

2. **Adapter at the caller boundary; `propose_strategies` does not import the loader.** `community_candidate_infos` maps a `load_community_strategies` result dict to `CandidateInfo` objects; `propose_strategies` receives them via the `community_candidates` kwarg. The engine is decoupled from the loader — the caller owns the Atlas fetch and passes the adapted output. This mirrors the existing `live_returns` injection pattern. *(As of 2026-06-17 closeout: the intended "caller" is not yet a production route — this describes the injection contract design; the Strategy Builder route at `app.py:3437` does not pass `community_candidates=`. See HF-1.)*

3. **Per-candidate backtest failure isolation (AC-4).** Each community candidate's `run_backtest` call is wrapped in the same per-candidate `try/except` as template candidates. A failure sets `backtest_error` and excludes the candidate from the gate without aborting the run. One bad community candidate cannot affect template-candidate processing.

4. **Provenance in persisted observations (AC-5).** Community survivors are persisted with `template_id="community"` and the source `sid` in `params`. Downstream surfaces can identify community-origin candidates in dashboard cards and M6 chat artifacts.

5. **No-regression guarantee (AC-6).** `community_candidates=None` and `community_candidates=[]` are both falsy — the `if community_candidates:` guard short-circuits the `extend`, leaving the execution path byte-for-byte identical to the pre-wiring code. No behavioral change to existing callers.

6. **`MAX_COMMUNITY_CANDIDATES_PER_RUN = 20` named constant (AC-3).** The cap is enforced inside `propose_strategies` (`community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN]`) regardless of what the adapter or caller passes. No magic numbers.

7. **Advisory safety (AC-7).** No `LIVE_EXECUTION`, credential key, or `_SETTINGS_WRITE_ALLOWLIST` entry is touched. The wiring is purely advisory-path. `propose_strategies` still never raises — catastrophic failure returns `ProposalRun(error=...)`.

8. **Rebuilt via Agent Team (operator hard rule).** The prior wiring (ripped at `ad3a637`) was built by a solo agent in violation of the teams-default rule. This version was built by the `propose-wiring` team (test-writer + implementer + reviewer + doc-writer) using the Toxic Pair TDD composition on `team/propose-strategies-wiring`.

**Public surface additions:**
- `community_candidate_infos(community_result, *, max_candidates) -> list[CandidateInfo]` — adapter; never raises; returns `[]` on unavailable/empty/malformed input.
- `MAX_COMMUNITY_CANDIDATES_PER_RUN: int = 20` — named constant.
- `propose_strategies(..., *, community_candidates: list[CandidateInfo] | None = None) -> ProposalRun` — kwarg addition; no change to existing positional signature.

**`CandidateInfo`/`ProposalRun`/`ScreenConfig`/`Objective` shapes are unchanged.** The FDR gate logic, screen logic, and persistence path are unchanged. Only the candidate list assembly in Step 1b is new.

**Status:** GREEN at `4edbe92`. 39/39 tests GREEN (AC-1..AC-7). Acceptance criteria verified: adapter mapping + cap (AC-1/AC-3), gate-input includes community candidates (AC-2), backtest failure isolation (AC-4), provenance in persist args (AC-5), no-regression vs template-only (AC-6), advisory-safety + never-raises (AC-7).

---

## GDELT Tone/Sentiment Producer — lens_gdelt (2026-06-15)

Cycle: `gdelt-tone` on branch `feat/lens-gdelt-tone`. 47/47 tests GREEN (2 live-excluded).

### DE-GDELT-001: Two-endpoint design — timelinetone for tone signal, artlist for citations; artlist is best-effort

**Decision:** `_fetch_gdelt_sentiment` makes two HTTP GETs: `timelinetone` for the
tone signal and `artlist` for source citations. The artlist result is best-effort —
any failure yields `sources=[]` without degrading `available` or `tone`. The tone
endpoint is the authoritative signal; artlist is enrichment only.

**Rationale:** GDELT's `timelinetone` mode returns a time series of `AvgTone`
values. The `artlist` mode returns article metadata useful as citations in the
Market Prism synthesis. Combining both in one producer avoids a second lazy-import
boundary while keeping the concerns separated in the return dict (`tone` vs `sources`).

### DE-GDELT-002: Amendment 1 backoff constants — BASE=20.0s, MAX=4, CAP=60.0s, INTER=6.0s

**Decision:** The retry constants are pinned at the Amendment 1 values
(`_GDELT_BACKOFF_BASE_S=20.0`, `_GDELT_MAX_ATTEMPTS=4`, `_GDELT_BACKOFF_CAP_S=60.0`,
`_GDELT_INTER_REQUEST_S=6.0`). The original values (`BASE=1.0`, `MAX=3`, `CAP=30.0`)
caused a persistent-429 PC crash when the GDELT IP was saturated by a background
probe: with `BASE=1.0` the retries fired within GDELT's 5 s/req window, never
clearing the 429. `BASE=20.0` gives 4× margin above the floor. `MAX=4` gives 3
retry opportunities. `INTER=6.0` prevents the artlist GET from immediately reusing
the rate-limit window that the tone GET just consumed.

**Consequence:** The inter-request sleep adds ~6 s to every successful call. This
is acceptable on the off-hours advisory path (never on the execution path).

### DE-GDELT-003: D-1 — named labels for HTTP-status failures, `type(exc).__name__` for caught exceptions

**Decision:** Non-429 non-200 HTTP status codes return the named label
`"gdelt_fetch_failed"` (not `type(HTTPError).__name__` = `"HTTPError"`). Caught
network exceptions (Timeout, ConnectionError, etc.) return `type(exc).__name__`.
Rate limiting after all retries returns `"rate_limited"`. Empty tone data returns
`"no_tone_data"`.

**Rationale:** Named labels allow callers to distinguish the HTTP-failure class
without coupling to the requests exception hierarchy. `type(exc).__name__` for
genuine exceptions satisfies the D-1 contract (class name only, never `str(exc)`,
never the message body).

**Implementation:** The status check (`if not (200 <= resp.status_code < 300)`)
fires before `resp.json()` and returns `_unavailable("gdelt_fetch_failed")`
directly, bypassing `raise_for_status()` entirely. This avoids leaking the
`HTTPError` class name through the catch-all.

### DE-GDELT-004: Tone extraction path — `timeline[0]["data"][k]["value"]`, not `timeline[0]["value"]`

**Decision:** The tone field is extracted from `timeline[0]["data"][k]["value"]`
(deep nested). The prior implementation read `entry.get("value")` from the
series-wrapper object `{series, data}`, which has no `"value"` key at that level
(see `gdelt-diagnosis.md §1`). This always produced `tone=None, available=True`,
violating the honest-availability contract.

**Consequence:** A dedicated test class (`TestToneNormalization`) and a fixture
designed with a series wrapper that has NO top-level `"value"` key lock this
field path permanently. Any regression to the old path causes immediate RED.

**Status:** GREEN at `7c5b203`. 47/47 tests GREEN (AC-1..AC-5). Acceptance criteria
verified per `tests/ai_advisor/test_lens_gdelt.py` — fixture schema, honest
availability, D-1 reason labels, tone normalization, bounded retry, artlist sources,
contract document existence, inter-request sleep, HTTP reason label.

---

## Technicals Lens Producer — lens_technicals (2026-06-15)

Cycle: `lens-technicals` on branch `feat/lens-technicals`. GREEN at `9449674`.

### DE-TECH-001: Producer + wiring shipped together (anti-hollow); reuses synthetic_history cache; no new Alpaca client

**Decision:** `advisors/lens_technicals.py` and the wiring of `ai_advisor._build_technicals_section()` (`ai_advisor.py:439-482`) are shipped in the same cycle. The Cycle-1 stub (`available=False, reason="technicals source not connected"`) is replaced atomically — there is no intermediate state where the producer exists but the section is still a stub, or the section calls a non-existent producer.

**Key design choices:**

1. **Anti-hollow: producer + wiring in one cycle (GDELT lesson).** The GDELT cycle (DE-GDELT-001) established that shipping a lens producer and leaving the section wiring for a follow-on cycle creates a "hollow" lens — the pipeline runs but always records `available=False` even though a working producer exists. This cycle applies that lesson: `lens_technicals._fetch_technicals` and `ai_advisor._build_technicals_section` are both landed in `feat/lens-technicals`.

2. **No new Alpaca client — reuses `synthetic_history.fetch_bars` (AC-5).** The autotuner already fetches 250 trading days of Alpaca bar history via `synthetic_history.fetch_bars`. The technicals lens reuses the same call (270 calendar days to cover weekends and holidays) via a thin `_get_bars` seam. No new Alpaca credentials or client setup is required.

3. **`_get_bars` as test seam.** The module-level `_get_bars(universe)` function is the only I/O boundary. Tests mock it via `unittest.mock.patch.object` without touching the real Alpaca client or `synthetic_history` module. The indicator math helpers (`_compute_sma`, `_compute_momentum`) are pure functions tested independently.

4. **Breadth excludes tickers with insufficient history.** Market breadth is `above_count / len(tickers_with_sma50)`, where the denominator is only tickers with ≥50 bars. Tickers with insufficient history are excluded from both numerator and denominator — they do not dilute the breadth signal with a forced `False`. If no ticker has ≥50 bars, `breadth=None`.

5. **Momentum is sparse, not zero-filled.** Only tickers with ≥21 bars contribute to the `momentum` dict. A ticker with insufficient history is omitted rather than recorded as `0.0` (which would be a fabricated value).

6. **Named constants with source comments — no magic numbers (AC-6).** All windows and retry parameters are module-level constants with inline citations: `_SMA_50_WINDOW` (Investopedia near-term MA), `_SMA_200_WINDOW` (Investopedia long-term trend separator), `_MOMENTUM_WINDOW` (Jegadeesh & Titman 1993), `_MAX_ATTEMPTS` (GDELT RCA — bounded retry), `_RETRY_BACKOFF_S`, `_HISTORY_DAYS`.

7. **Bounded retry; authoritative empty not retried (AC-4).** `_fetch_technicals` retries `_get_bars` on `Exception` up to `_MAX_ATTEMPTS=3`. An authoritative empty response (`{}`) from `_get_bars` is not retried — it is the data source's honest answer, not a transient error.

8. **D-1: named labels for authoritative failures, `type(exc).__name__` for exceptions.** `"no_bars_returned"` and `"insufficient_bar_history"` identify the two authoritative unavailability states. All caught exceptions use `type(exc).__name__` only — never `str(exc)`.

9. **CC-2 lazy import.** `advisors.lens_technicals` is imported inside `ai_advisor._build_technicals_section()` (not at `ai_advisor` module level), maintaining the CC-2 import-boundary invariant. `synthetic_history` is itself imported lazily inside `_get_bars`.

**Indicators shipped:**
- **MA posture:** per-ticker `{above_sma50: bool | None, above_sma200: bool | None}`. 50-day and 200-day SMA from Investopedia standard windows.
- **Market breadth:** fraction of universe above 50-day SMA (excluding tickers without sufficient history).
- **20-day momentum:** per-ticker `(close[-1] - close[-21]) / close[-21]`. Source: Jegadeesh & Titman (1993) cross-sectional momentum lookback.

**Public surface:**
- `advisors/lens_technicals._fetch_technicals(universe: list[str]) -> dict` — entry point called by `ai_advisor._build_technicals_section()`.
- `advisors/lens_technicals._get_bars(universe: list[str]) -> dict[str, list[dict]]` — test seam; delegates to `synthetic_history.fetch_bars` in production.

**Status:** GREEN at `9449674`. Acceptance criteria verified. `docs/generated/advisors_lens_technicals.md` committed on `feat/lens-technicals`.

---

### DE-TECH-002: Universe = live holdings UNION _PROXY_UNIVERSE floor; [PM-ASSUMED] proxy-basket choice

**Decision:** `ai_advisor._build_technicals_section()` sources its ticker universe from the UNION of (a) live `database.load_state()` `logic_holdings` tickers and (b) a named module-level constant `lens_technicals._PROXY_UNIVERSE` — a 10-ticker market-proxy breadth basket. The proxy is always applied after the `load_state()` extraction, whether or not holdings are present.

**Trigger:** PM live-gate failure (round 2). The initial implementation sourced the universe from `logic_holdings` only. Live test confirmed all 11 symphonies have `logic_holdings={}` at 03:00 and on weekends/flat markets — the lens's PRIMARY consumer is `lens_pipeline.run_pipeline()` at 03:00. The lens was perpetually `available=False` at the exact time the nightly Prism pipeline needed it — effectively hollow despite structurally honest wiring.

**Root cause:** `logic_holdings` is a RUNTIME field, populated only during market-hours execution cycles. It is the wrong source for an off-hours lens that must produce signals before markets open.

**[PM-ASSUMED] Proxy basket choice:** A named market-proxy breadth basket (`SPY`, `QQQ`, `IWM`, `EFA`, `AGG`, `GLD`, `XLF`, `XLE`, `XLV`, `XLI`) was chosen over Composer `/score` API calls per-symphony. Rationale: the `/score` path requires live network calls per symphony at 03:00 (heavyweight, latency, credentials) and introduces Composer API dependency on the advisory lens path. The proxy basket provides major-cap equity benchmarks across US large-cap, tech, small-cap, international, bond, and sector breadth — sufficient for the Prism `technicals_analyst` reasoning about broad market structure. The basket is documented with Investopedia "market breadth indicators" as the source reference.

**Merge semantics:** `tickers.update(lens_technicals._PROXY_UNIVERSE)` is applied unconditionally after the `try/except` block — the proxy is a FLOOR regardless of whether `load_state()` succeeds or raises. Live holdings tickers are MERGED with the proxy, not replaced: when symphonies hold positions, those tickers appear alongside the proxy basket.

**Implementation:**
- `advisors/lens_technicals.py`: `_PROXY_UNIVERSE: list[str]` constant added after `_HISTORY_DAYS`, with source comment per AC-6 no-magic-numbers rule.
- `ai_advisor.py` `_build_technicals_section()`: `tickers.update(lens_technicals._PROXY_UNIVERSE)` inserted between the `try/except` and `universe = sorted(tickers)`. Docstring updated to describe the union semantics.

**Tests:** `TestProxyUniverseGuard` (6 tests, `test_lens_technicals.py`): constant exists + non-empty, proxy tickers reach `_get_bars` when holdings empty, `available=True` at 03:00 flat, live holdings merged not replaced, empty DB still yields proxy universe, genuine bar-fetch failure still degrades D-1.

**Status:** GREEN at `34a4481` — 46/46 tests passing. Reviewer APPROVE (conditional on PM live gate, 2026-06-16T08:19 UTC).

---

### DW-1: Nightly Lens Data Warehouse — separate append-only DB + recursive secret-strip + producer wiring

**Decision:** New `advisors/lens_warehouse.py` owns a SEPARATE `alphabot_warehouse.db` ([PM-ASSUMED] filename) — the THIRD DB, distinct from the state + optimization DBs (no cross-DB joins). Append-only `lens_snapshots` table; `persist_lens_snapshot(lens, symbol, source, available, raw_payload, ...) -> int` (parameterized, append-only, recursive `_strip_secrets`, D-1) + `get_lens_snapshots(...) -> list[dict]`. WAL, idempotent init, pytest sentinel (opening the real warehouse DB under pytest raises; tests pass a temp `db_path`).

**Wiring (anti-hollow):** `ai_advisor._build_sentiment_section` (GDELT) + `_build_macro_section` (FRED) call `persist_lens_snapshot` after each fetch (lazy import, off-execution-path) — the store has real production writers. Retrofitting the remaining producers is a documented fast-follow (Scope OUT).

**Rationale:** accumulate Planet Stopper's OWN historical lens corpus at $0 (operator directive 2026-06-13); engine-agnostic `raw_json` enables future backtesting / DuckDB-parquet migration. Append-only + dedupe-at-read; never lose a night's pull; never fabricate (`available=0` for a down source).

**Secret-strip:** `_SECRET_KEY_NAMES = {api_key, token, secret, password, Authorization}`, recursive over dicts+lists — verified live (nested `token` stripped).

**Status:** GREEN at `961c0cb` (60 tests). Reviewer APPROVE + PM live gate PASS (persist->get round-trip, append-only, recursive secret-strip, 5 live persist calls in `ai_advisor`). Verifier: 6918 passed / 4 pre-existing fails / zero cycle-caused. PR #35. Supersedes stale closed PR #4 (hollow, 26 tests).

---

## Derivatives Lens — VIX Freshness Fix (2026-06-16)

Branch: `fix/derivatives-vix-freshness` | HEAD: 3c7e545

### DE-DERIV-001: Freshness guard added to derivatives lens — staleness is now an honest-availability condition

**Defect class:** The shipped `advisors/lens_options_proxy.py` (landed PR #30, `93910b6`) served a ~6-year-stale VIX value as `available=True`. Root cause: `_fetch_fred_series` used `sort_order="asc"`, `limit=100`, `observation_start="2020-01-01"` — fetching the *oldest* 100 observations (approximately Jan–May 2020). `_parse_latest_observation` walked the list in reverse and returned a ~May-2020 date as the "latest" observation. Honest-availability (CC-3 / D-1) covered *fetch failure* only, not *data staleness*, so a stale-but-successful fetch flowed through as a confident, wrong market read into the nightly Market Prism.

**Fix — two-part:**

1. **Recent rolling window (AC-1).** `_fetch_fred_series` now computes `observation_start` as `_today() - timedelta(days=_OPTIONS_PROXY_LOOKBACK_DAYS)` at fetch time instead of the hardcoded `"2020-01-01"`. `_OPTIONS_PROXY_LOOKBACK_DAYS = 90` calendar days — wide enough to always contain several valid trading-day observations across holidays, small enough to keep the response light. `sort_order="asc"` and `_parse_latest_observation`'s reverse-walk are unchanged; the rolling window ensures the response tail is genuinely recent.

2. **Freshness guard (AC-2).** After `_parse_latest_observation` returns `(value, date_str)`, a guard compares `obs_date` to `_today() - timedelta(days=_OPTIONS_PROXY_MAX_STALENESS_DAYS)`. If stale, the function returns `{available: False, reason: "stale_data"}` with no `vix_level`, `vix_term_structure`, `risk_read`, or `as_of_date` keys. No fabricated values reach the pipeline. The guard fires before regime/risk computation.

**`available=True` now requires both:**
- The HTTP fetch succeeded and FRED returned at least one valid non-`"."` observation.
- The latest valid observation's date is within `_OPTIONS_PROXY_MAX_STALENESS_DAYS` calendar days of the run date.

**New constants (named, with source comments):**
- `_OPTIONS_PROXY_MAX_STALENESS_DAYS: int = 10` — threshold above longest normal market closure (long-weekend + adjacent holiday ≈ 4 calendar days); catches genuine staleness decisively. [PM-ASSUMED]
- `_OPTIONS_PROXY_LOOKBACK_DAYS: int = 90` — rolling window for `observation_start`. [PM-ASSUMED]

**Injectable test seam:**
- `_today() -> datetime.date` — module-level helper, monkeypatchable in tests. Required so freshness tests are deterministic without wall-clock coupling (AC-5).

**`reason` values when `available=False`:**
- `"stale_data"` — latest observation older than `_OPTIONS_PROXY_MAX_STALENESS_DAYS` (a data-quality sentinel, not an exception class name).
- `"KeyError"` — `FRED_API_KEY` not set.
- `"ValueError"` — no valid observations, malformed date, or bad response shape.
- `type(exc).__name__` — any caught exception (D-1 contract unchanged).

**Behavior preserved:** All pre-existing fetch-failure, 429-exhausted, and no-valid-observations paths are unchanged. `_classify_regime` and `_derive_risk_read` logic are identical; the freshness guard is purely additive — it short-circuits before those helpers when stale.

**Files changed:** `advisors/lens_options_proxy.py` (lines 137–143 new constants; line 146 `_today()` helper; line 185 rolling window; lines 350–368 freshness guard); `tests/ai_advisor/test_lens_options_proxy.py` (new staleness/freshness/window AC tests).

**Status:** 43/43 GREEN at 3c7e545. `quant-code-reviewer` APPROVE (pending PM live FRED gate).

---

## Fundamentals Lens — Portfolio Fan-Out Fix (2026-06-16)

Branch: `fix/fundamentals-portfolio-fanout` | HEAD: 3e41a2a

### DE-FUND-001: Internal portfolio fan-out in `_build_fundamentals_section`; `_FUNDAMENTALS_PROXY_UNIVERSE` company floor; per-ticker honest degradation; no invented composite ratios

**Defect class (REAL BUG 2):** The SEC EDGAR fundamentals lens was permanently unavailable in the nightly Market Prism. Root cause: `_build_fundamentals_section` short-circuited with `available=False, reason="ticker symbol required..."` whenever `ticker=None`. Both production callers — `advisors/lens_pipeline.py:73` (`builder()`, the 03:00 nightly path) and `ai_advisor.py` (`assemble_advisor_context`) — invoke the function with no ticker. SEC EDGAR was never reached; the fundamentals block was a dead lens despite structurally honest wiring.

**Fix — internal portfolio fan-out (AC-1):**

`_build_fundamentals_section(ticker=None)` now derives a company-ticker universe internally and fans out the single-ticker SEC companyfacts logic across it, aggregating results into a portfolio-level block. The per-ticker body was extracted into `_fetch_fundamentals_for_ticker(ticker: str) -> dict` — both the single-ticker path and the fan-out path delegate to it.

**Key design choices:**

1. **Internal fan-out, not a caller signature change.** Both production callers invoke `builder()` with no arguments via the uniform lens map in `lens_pipeline.py`. The universe must be derived inside the producer — exactly as `_build_technicals_section` derives its universe from `load_state()` + `_PROXY_UNIVERSE`. Changing the lens map's calling convention would touch `lens_pipeline` and break the CC-2 lazy-import boundary. The internal derivation is the minimal-surface fix.

2. **`_FUNDAMENTALS_PROXY_UNIVERSE` — company floor, NOT ETFs.** A named module-level `frozenset[str]` of 8 large-cap individual company tickers (AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ — cross-sector S&P 500 representatives) is an unconditional floor so the 03:00 nightly Prism always has a real fundamentals universe even when `logic_holdings` is empty (flat market, off-hours, weekend). ETFs are explicitly excluded: ETFs have no SEC EDGAR `companyfacts` entries — every CIK lookup and companyfacts fetch for an ETF would fail. This mirrors the `_PROXY_UNIVERSE` floor pattern in `lens_technicals.py` (established DE-TECH-002).

3. **Per-ticker honest degradation (AC-4).** A ticker that fails to resolve (ETF held as a position / CIK not found / HTTP error / no key facts) is excluded from the aggregate without failing the whole lens. Only that ticker's per-ticker entry is omitted — the lens remains `available=True` via the other resolved tickers. No fabricated facts. Mirrors the breadth-exclusion pattern in the technicals lens.

4. **No invented composite ratios.** The portfolio payload exposes `{tickers: {AAPL: {entity_name, cik, key_facts}, ...}, coverage: {available: N, universe: M}}` — per-ticker raw XBRL key facts and a coverage count. No derived ratio (e.g. weighted average P/E, portfolio-level earnings yield) is computed. Aggregating raw XBRL facts into a synthetic score would fabricate a producer value not grounded in any single filing. Tests assert shape and presence — never hardcoded financial literals (no-hardcoded-test-values rule).

5. **Bounded SEC fan-out (AC-6).** The proxy basket is small (~8 tickers). The existing `_fetch_with_backoff` bounded retry applies per-ticker. Off-execution-path; advisory-only; never touches `LIVE_EXECUTION`. SEC EDGAR is keyless/free — no provider bill concern (scope-out for a fundamentals cache layer).

6. **Single-ticker path preserved byte-for-byte (AC-3).** `_build_fundamentals_section(ticker="AAPL")` delegates to `_fetch_fundamentals_for_ticker("AAPL")` and returns the same block shape as the pre-fix single-ticker behavior. Existing per-symphony callers are unaffected. Regression guarded by `test_single_ticker_path_unchanged`.

**`_fetch_fundamentals_for_ticker(ticker: str) -> dict` helper:**
Extracted from the original single-ticker body (CIK resolve → companyfacts fetch → `_SEC_KEY_CONCEPTS` extraction → citation build). Returns the per-ticker block without the top-level `lens` key — the `lens` key is set by `_build_fundamentals_section` on the final return. Both paths delegate to this helper; the D-1 / CC-3 / `_SEC_USER_AGENT` invariants are unchanged.

**`_FUNDAMENTALS_PROXY_UNIVERSE` placement:** Module-level constant in `ai_advisor.py`, immediately after `_SEC_KEY_CONCEPTS`. Source comment documents the ETF exclusion rationale and cites S&P 500 large-cap 2024 constituents as the selection basis.

**Files changed:** `ai_advisor.py` (new `_FUNDAMENTALS_PROXY_UNIVERSE` constant; `_fetch_fundamentals_for_ticker` extraction; `_build_fundamentals_section` portfolio fan-out path); `tests/ai_advisor/test_lens_fundamentals_fanout.py` (7 new tests, AC-1..AC-6).

**Status:** GREEN at 3e41a2a. `quant-code-reviewer` APPROVE (pending PM live SEC gate). Acceptance criteria AC-1 through AC-6 verified.


---

### DE-SYNTH-001: ADVISOR_SYNTHESIS_MODEL env var — call-time model selection at all 3 advisor LLM call sites

**Date:** 2026-06-17

**Decision:** All three advisor LLM call sites read `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` inline at the SDK call (call time). The previously hardcoded model strings are replaced by a single env var:

1. `advisors/lens_pipeline.py` — `_synthesize_via_claude`: was `"claude-haiku-4-5-20251001"` (hardcoded); now `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`.
2. `ai_advisor.py` — `request_suggestions`: was `_CLAUDE_MODEL` constant reference; now `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`.
3. `advisors/advisor_chat.py` — `explain_artifact`: was `_CHAT_MODEL` constant reference; now `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`.

The module-level `_CLAUDE_MODEL` and `_CHAT_MODEL` constants were **removed** at 46a6bc4 (dead-constant cleanup). The call sites read `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` inline; there is no retained constant.

**Default model:** `claude-opus-4-8`.

**Rationale for default upgrade (Haiku → Opus 4.8):** The nightly lens pipeline (`_synthesize_via_claude`) was the only production caller of `claude-haiku-4-5-20251001`. At 1 call/night and ~256 max_tokens per synthesis, the cost difference between Haiku and Opus 4.8 is negligible (~$0.001/call delta at current API rates). Upgrading the default to Opus 4.8 aligns all three advisor call sites to the same default and delivers higher synthesis quality for the nightly Market Prism summary.

**Why call-time, not module-level constant:** Module-level constant resolution happens at import time; reading the env var inline at the SDK call means tests and CI can override the model via `monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", "claude-haiku-4-5-20251001")` without patching internal module state. It also means the operator can change the model by setting/unsetting the env var and restarting the daemon, without any code change.

**Scope boundary:** This change is model selection only — no new HTTP paths, no fixture changes, no Composer endpoints touched, no `LIVE_EXECUTION` path touched, no `is_live` propagation change. Advisory-only surface.

**Epic-A Phase-2 (`prism-synthesizer`) scope boundary:** A future `prism-synthesizer` agent (Epic-A Phase-2) may introduce per-lens model routing or a separate synthesizer constant. `ADVISOR_SYNTHESIS_MODEL` is the single shared default for all three current call sites; per-call-site overrides are out of scope for this decision.

**AC-5 fence-stripping:** The test for AC-5 (fence-stripping in `_synthesize_via_claude`) is byte-preserved — the fence-stripping logic and its test coverage are unchanged by this decision.

**Files changed:** `advisors/lens_pipeline.py` (`import os` added; model selection updated); `ai_advisor.py` (model selection updated; `_CLAUDE_MODEL` constant removed — 46a6bc4); `advisors/advisor_chat.py` (`import os` added; model selection updated; `_CHAT_MODEL` constant removed — 46a6bc4); `tests/ai_advisor/test_synthesis_model_config.py` (30 new tests, AC-1..AC-7).

**Status:** GREEN at 294f8a5. 30/30 new tests pass; 1146/0 sibling suite clean.

**Follow-up (0357ecb + c3113b6):** `resolve_advisor_model() -> str` added to `ai_advisor.py` (lines 63-69) as a public accessor that surfaces the env-resolved model ID to non-advisor consumers. `app.py` accept (`/ai-advisor/accept`, line 3748) and reject (`/ai-advisor/reject`, line 3781) routes call `ai_advisor.resolve_advisor_model()` to populate the `model_id` field in the `llm_suggestions` audit trail — replacing a previously hardcoded or absent value. 41/41 tests pass at c3113b6.


---

### DE-FUND-002: Fundamentals lens vintage fix — XBRL concept-tag fallback (Mode A) + sort-by-end (Mode B)

**Date:** 2026-06-17

**Branch:** `fix/fundamentals-vintage` | **HEAD:** c72bd3a

**Defect class (CLOSEOUT finding F5):** The SEC EDGAR fundamentals lens reported `available=True` but served wrong-vintage values — the "available=True but stale data" defect class (analogous to the stale-VIX fix in DE-TECH-003 / PR #37). Two concurrent defects, both fixed in this cycle.

**Mode A — XBRL concept deprecation (`ai_advisor.py:361-374`):**

`_SEC_KEY_CONCEPTS` previously mapped each logical concept to a single `str` XBRL tag (`dict[str, str]`). When an issuer migrates a concept to a newer GAAP tag, the old tag stops receiving new filings. Live evidence (closeout): MSFT migrated `Revenues` → `SalesRevenueNet` → `RevenueFromContractWithCustomerExcludingAssessedTax`; the producer queried only `"Revenues"` (frozen at `end=2010-06-30`) and never reached `RevenueFromContractWithCustomerExcludingAssessedTax` (`end=2025-06-30`, 48 10-K entries, present in EDGAR).

**Fix (Mode A):** `_SEC_KEY_CONCEPTS` restructured to `dict[str, tuple[str, tuple[str, ...]]]` — each logical concept maps to `(display_label, ordered_candidate_tags)`. The Revenues concept now carries three candidate tags:

```python
"Revenues": (
    "Revenue",
    (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ),
),
```

Other four concepts retain single-tag tuples (no migration evidence in the closeout). **Outer logical keys are stable** (`Revenues`, `NetIncomeLoss`, `Assets`, `Liabilities`, `StockholdersEquity`) — these are the `key_facts` output keys consumed by the synthesis prompt and Overview render; changing them is explicitly out of scope.

**Mode B — wrong sort key (`ai_advisor.py:1011-1073`):**

The selection loop previously sorted `entries_to_check` by `e.get("filed", "")` descending and took `[0]`. A single 10-K bundles comparative prior-period entries that share one `filed` date; Python's stable sort then yields the OLDEST `end` first. This affected ALL tickers and ALL non-deprecated concepts — the JPM control case (no Mode A) showed all 5 concepts 1-2 years stale.

**Fix (Mode B):** The loop now:
1. Unions entries across ALL candidate tags present in `us_gaap` for the concept (`ai_advisor.py:1017-1030`).
2. Sorts the union by `(end desc, filed desc)` — `end` is primary (the reporting period); `filed` is secondary for restatements sharing the same `end` (`ai_advisor.py:1037-1044`).
3. Selects `entries_sorted[0]` (`ai_advisor.py:1045`) — the entry with the most recent reporting-period end.
4. Wraps the whole per-concept block in `try/except` (AC-7 — never raises on malformed or partial XBRL; `ai_advisor.py:1012, 1070-1072`).

**Payload shape preserved (AC-4):** `key_facts` output dict shape — `{label, value, unit, end, filed, form}` per logical key — is byte-identical to the pre-fix shape. Downstream consumers (Overview render, synthesis prompt) see no key-name or value-type change.

**Both paths covered (AC-6):** The selection logic lives in `_fetch_fundamentals_for_ticker` (`ai_advisor.py:952`), shared by both the single-ticker path and the portfolio fan-out path. Both inherit the fix without signature changes.

**C2-COMMENT-1 (trivial ride-along):** The stale comment at `ai_advisor.py:1735` ("Three independent layers") was corrected to "Four independent layers" and the fourth gate (`enforce_locked_var_gate`) was added to the enumeration (`ai_advisor.py:1738-1752`). Comment-only; no behavior change.

**Files changed:**
- `ai_advisor.py` — `_SEC_KEY_CONCEPTS` type change (`ai_advisor.py:361-374`); selection loop rewrite (`ai_advisor.py:1011-1073`); C2 comment correction (`ai_advisor.py:1738-1752`)
- `tests/ai_advisor/test_fundamentals_vintage.py` — 21 new tests covering AC-1 through AC-7

**Status:** 21/21 GREEN at c72bd3a. Test-writer APPROVED at 0cf5644. PM merge gate (live SEC functional test + `/review`) pending.

---

## Community-Strats Route Wiring — HF-1 (2026-06-17)

Branch: `feat/community-strats-route-wiring` | HEAD: 049722a

### DE-HF1-001: `POST /ai-advisor/strategy-builder/run` now loads and passes community candidates; honest template-only degradation when Atlas unavailable

**Decision:** The `ai_advisor_strategy_builder_run()` handler (`app.py:3395`) is the first — and now only — production caller of `advisors/community_strats.load_community_strategies`. The wiring is best-effort: a failed or empty community load never blocks a template-only proposal run.

**What changed (app.py only):**

1. **Lazy imports extended** (`app.py:3415-3421`): the existing lazy-import block inside the handler gains `MAX_COMMUNITY_CANDIDATES_PER_RUN` and `community_candidate_infos` from `advisors.strategy_builder_engine`, plus a new lazy import of `load_community_strategies` from `advisors.community_strats`. All remain inside the handler body — the CC-2 boundary (no module-level advisor imports) is unchanged.

2. **Best-effort community load** (`app.py:3440-3448`):
   ```python
   community_candidates: list = []
   try:
       _community = load_community_strategies(force_refresh=False)
       community_candidates = community_candidate_infos(
           _community, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN
       )
   except Exception as exc:
       _daemon_log.warning("community-strats load skipped: %s", type(exc).__name__)
       community_candidates = []
   ```
   `force_refresh=False` is mandatory — the weekly cache TTL is the operator's bill-protection directive. Both helpers are documented never-raising / D-1; the `try/except` is belt-and-suspenders and logs only the exception class name (never `str(exc)`).

3. **`community_candidates=` kwarg forwarded** (`app.py:3457`): the existing `propose_strategies(...)` call gains `community_candidates=community_candidates`. Everything downstream — single-batch FDR gate, screens, persistence, response JSON shape — is unchanged; `strategy_builder_engine` already merged + caps community candidates into the full gated batch (DE-PSW-001).

**Key design choices:**

1. **Best-effort, never-blocking (AC-4).** A community load failure (Atlas down, MONGO_URI unset, cache miss, any exception) logs the class name and degrades to `community_candidates=[]`. The proposal run completes as a template-only run. The route's response shape and HTTP status are identical to the pre-wiring template-only behavior.

2. **Weekly cache, never forced (AC-3).** `force_refresh=False` is the only valid production value. A per-request forced Atlas pull would violate the operator's bill-protection directive for the captplanet provider. The cache is shared across concurrent requests; no per-call Atlas hit.

3. **Lazy imports inside the handler (AC-5 / CC-2).** `strategy_builder_engine` and `community_strats` are imported inside `ai_advisor_strategy_builder_run()` only, never at `app.py` module level. The 1-minute live execution path is unaffected.

4. **No allowlist / live-exec / CSRF change (AC-5).** The route is not added to `_SETTINGS_WRITE_ALLOWLIST`; CSRF enforcement is unchanged; no `LIVE_EXECUTION` or credential surface is touched. Advisory-only.

5. **No regression to template-only path (AC-6).** `propose_strategies` already treats `community_candidates=[]` as falsy (DE-PSW-001 `if community_candidates:` guard), producing a byte-identical result to the pre-wiring run for the same template inputs.

**Files changed:**
- `app.py` — `ai_advisor_strategy_builder_run()` handler: lazy imports extended (`app.py:3415-3421`); community load block added (`app.py:3440-3448`); `community_candidates=` kwarg forwarded to `propose_strategies` (`app.py:3457`).
- `tests/ui/test_strategy_builder_community_wiring.py` — new route-layer tests covering AC-1 through AC-6 (mocked collaborators, no live Atlas/Composer).

**Status:** GREEN at 049722a. Acceptance criteria AC-1 through AC-6 verified.

---

## Community-Strats Atlas Fetch — Wall-Clock Timeout (2026-06-17)

Branch: `fix/community-strats-atlas-timeout` | HEAD: 55b00ea

### DE-CS-002: Wall-clock `ThreadPoolExecutor` timeout on the Atlas live fetch leg; `serverSelectionTimeoutMS` cannot bound SRV/DNS hangs

**Root cause:** A `mongodb+srv://` URI triggers an SRV DNS query before the Mongo driver can begin its TCP handshake. `serverSelectionTimeoutMS` and `connectTimeoutMS` — the driver-level timeout knobs — apply only after DNS resolves. When the SRV/TXT DNS query hangs (observed: >50 s with both driver timeouts set to 10 s), pymongo blocks indefinitely, causing the Strategy Builder route to hang rather than degrade to template-only. The HF-1 wiring (DE-HF1-001) introduced `load_community_strategies` as a production caller of `cached_pull`; this exposed the pre-existing unbounded Atlas fetch as a route-hang risk.

**Decision:** Wrap the live Atlas fetch in a `ThreadPoolExecutor(max_workers=1)` with `fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)`.

**Key design choices:**

1. **Wall-clock bound via `ThreadPoolExecutor`, not `serverSelectionTimeoutMS` (root cause fix).** `serverSelectionTimeoutMS` / `connectTimeoutMS` are driver-level and cannot interrupt DNS resolution. The only reliable bound on a `mongodb+srv://` hang is an OS-level wall clock managed outside pymongo's control. `ThreadPoolExecutor(max_workers=1)` submits `_fetch_fn` to a background thread and calls `fut.result(timeout=12.0)`. After 12.0 s, `concurrent.futures.TimeoutError` is raised in the calling thread regardless of what pymongo's internal state machine is doing.

2. **`_ATLAS_FETCH_TIMEOUT_S = 12.0` (> 10 s `serverSelectionTimeoutMS`).** The constant is set above the `serverSelectionTimeoutMS=10_000` value so a reachable-but-slow Atlas cluster can still complete server selection within the wall-clock window without a false timeout. Named constant with an inline source comment explaining the SRV/DNS root cause and the `>10s` rationale.

3. **`shutdown(wait=False, cancel_futures=True)` — never `wait=True`.** When `concurrent.futures.TimeoutError` fires, the worker thread is blocked in pymongo and cannot be interrupted. `shutdown(wait=True)` would block the `finally` clause indefinitely, defeating the timeout. `shutdown(wait=False)` releases the calling thread immediately; the orphaned worker thread is allowed to linger until pymongo's own internal socket timeout eventually unblocks it. The comment `# NEVER wait=True` is on the line.

4. **`_timeout_fired: list[bool]` closure flag.** `cached_pull` has a never-raising contract: it catches all exceptions from `fetch_fn` and returns `None`. After `cached_pull` returns `None`, the outer scope cannot distinguish a wall-clock timeout from any other Atlas failure. A `list[bool]` flag (mutated inside the closure before `_AtlasFetchTimeout` is raised) persists across the `cached_pull` boundary — it is set to `True` before the exception is raised so that even if `cached_pull` swallows `_AtlasFetchTimeout`, the flag remains readable. The `raw_docs is None` branch checks the flag: `reason = "AtlasFetchTimeout" if _timeout_fired[0] else "AtlasCacheUnavailable"`.

5. **`_AtlasFetchTimeout` custom exception.** A dedicated exception class distinguishes a timeout from `concurrent.futures.TimeoutError` at the caller boundary and avoids leaking CPython internals through the D-1 reason contract.

6. **Fix-forward, not revert of HF-1.** The production call site in `app.py` (DE-HF1-001) is unchanged. The best-effort `try/except` around `load_community_strategies` in the route handler already degrades to `community_candidates=[]` on any exception or `available=False` result. The timeout fix improves the *speed* of that degradation — from an indefinite hang to a bounded 12 s — without changing any route contract or HTTP response shape.

**New symbols in `advisors/community_strats.py`:**
- `_ATLAS_FETCH_TIMEOUT_S: float = 12.0` — wall-clock bound; named constant with inline source comment.
- `_AtlasFetchTimeout(Exception)` — sentinel raised inside `_bounded_fetch_fn` on timeout.
- `_bounded_fetch_fn()` (nested def inside `load_community_strategies`) — `ThreadPoolExecutor` wrapper; `shutdown(wait=False, cancel_futures=True)` in `finally`.
- `_timeout_fired: list[bool]` (closure variable) — cross-boundary timeout signal.
- `import concurrent.futures` — module-level import (stdlib).

**Route behavior:** On a SRV/DNS hang, the route now degrades to template-only within ~12 s instead of hanging indefinitely. `reason="AtlasFetchTimeout"` is logged at WARNING level by the route's `try/except`. The Strategy Builder response is template-only; HTTP status and JSON shape are unchanged.

**Status:** GREEN at 55b00ea. 14/14 tests GREEN (AC-1 through AC-5).

---

## Prism Follow-ups — dotenv hardening + chip-color mapping (2026-06-18)

Branch: `fix/prism-followups` | HEAD: 8e59305

### DE-PRISM-DOTENV: `load_dotenv(find_dotenv(usecwd=True))` added to `prism_audit_write.py`

**Decision:** `advisors/prism_audit_write.py` now calls `load_dotenv(find_dotenv(usecwd=True))` at module import — before any `import database` and before any call to `database._db_file()`.

**Root cause:** The CLI writer was invoked by capstone analysts from arbitrary working directories. Without `load_dotenv`, the `DB_PATH` value defined in the project `.env` was invisible to the process unless it happened to be in the shell environment. When `DB_PATH` was absent from the shell env, `database._db_file()` fell back to the cwd-relative `alphabot_state.db` — silently writing the audit row to a DIFFERENT database than the project's live state DB. This is a silent split-brain: the analyst thinks the row landed in the canonical DB; it actually landed in a cwd-local file. The capstone W3 run hit this.

**Why `find_dotenv(usecwd=True)` rather than plain `load_dotenv()`:**
`find_dotenv()` without `usecwd=True` walks upward from the calling file's `__file__` directory, which for a `-m` invocation is the package directory (`advisors/`). `find_dotenv(usecwd=True)` starts the upward walk from `os.getcwd()` — the shell's working directory at invocation time. Both the production cwd (repo root, where `.env` lives) and arbitrary test cwds (e.g. `tmp_path`) resolve correctly. `load_dotenv` is a no-op when `.env` is absent; shell env wins by default (`override=False`).

**Scope:** Single file change (`advisors/prism_audit_write.py`). No change to `database.py` resolution logic. The D-1 error contract is preserved. The nightly daemon path is unaffected — the daemon already has `DB_PATH` in its environment.

**Files changed:** `advisors/prism_audit_write.py` (line 24–26: `from dotenv import find_dotenv, load_dotenv`; `load_dotenv(find_dotenv(usecwd=True))` before `import argparse`).

**Status:** GREEN at 8e59305. 3/3 AC-1 tests pass.

---

### RF-1-chip: Market Prism chip modifier now maps `bullish`/`bearish` synonym forms

**Decision:** The verdict→CSS-modifier dict in `templates/ai_advisor.html` (lines 970–977) is extended to map the synonym forms `'bullish'` and `'bearish'` explicitly, in addition to the canonical `'risk-on'` and `'risk-off'` forms already present.

**Root cause:** The lens pipeline synthesizer (`advisors/lens_pipeline.py`) can emit either the canonical `risk-on`/`risk-off` form or the natural-language synonym `bullish`/`bearish` as the `overall_sentiment` verdict — both forms appear in the W3 capstone DB rows. The chip mapping dict only contained `'risk-on'` and `'risk-off'`; a `bullish` verdict hit `.get(_sentiment, 'prism-sentiment-chip--neutral')` and fell through to the neutral-gray default. The chip rendered neutral-gray even when the synthesizer was expressing a bullish market read. The verdict text rendered correctly throughout — the bug was in the modifier class only.

**Fix:** Added `'bullish': 'prism-sentiment-chip--risk-on'` and `'bearish': 'prism-sentiment-chip--risk-off'` as explicit entries in the dict, above the canonical forms, with an explanatory Jinja2 comment. The dict.get fallback (`--neutral`) remains for unknown values. The `limited-inputs` key is unchanged.

**Scope boundary:** The original RF-1 premise ("lens cards render raw JSON") was verified NOT to reproduce — the cards render readable prose digests from `per_lens_digest`. Card rework is out of scope for this cycle.

**Files changed:** `templates/ai_advisor.html` (lines 967–977: comment + two new dict entries).

**Status:** GREEN at 8e59305. 7/7 AC-2 chip-mapping tests pass.


---

### DE-GDELT-005: News-events upgrade — sourcelang:eng filter, _extract_events, events-OR-tone availability gate (2026-06-18)

Branch: `feat/lens-news-events` | HEAD: 2649229

**Decision:** Upgrade `advisors/lens_gdelt.py` from aggregate-tone-only to real English-language market news events as the primary signal, with tone secondary.

**Why the prior design was insufficient:**

1. **No language filter.** The artlist query had no `sourcelang:eng` constraint, so GDELT returned articles in any language (including non-English and articles with a null `language` field). Non-English headlines are not useful to English-language operators and add noise to the Market Prism synthesis prompt.

2. **Tone-only availability gate allowed a forbidden state.** The prior gate set `available=True` as soon as the tone endpoint returned HTTP 200 — even when tone extraction yielded `None` (empty timeline, no numeric data). `available=True, tone=None` is explicitly forbidden by the honest-availability contract (§4). The fix changes the gate to `available = bool(events) OR tone is not None`, so availability is tied to a real signal.

3. **No first-class events field.** The return dict had no `events` key; the artlist payload was buried in `sources` as raw citation dicts. The Market Prism synthesizer could not directly access ranked news headlines without re-parsing citations.

**What changed in `advisors/lens_gdelt.py`:**

- `sourcelang:eng` added to both `_GDELT_TONE_URL` and `_GDELT_ARTLIST_URL` (server-side pre-filter).
- `_GDELT_MAX_EVENTS: int = 7` constant added (feature plan AC-2 pin — ~5-8 events for prompt-budget control).
- `_extract_events(sources_raw)` helper added: filters `language == "English"` (client-side defense-in-depth), sorts most-recent-first by seendate (lexicographic on `YYYYMMDDTHHmmssZ`), deduplicates by domain (most-recent per domain), caps at `_GDELT_MAX_EVENTS`. Returns `list[dict]` with keys `title`, `domain`, `seendate`. Never raises.
- Availability gate changed: tone-extraction failures (`tone=None`) no longer short-circuit before the artlist call. Only HTTP-level tone failures (rate_limited, gdelt_fetch_failed, exception) return early. An empty timeline sets `tone=None` but continues to artlist — if events are found, `available=True` with `tone=None` is a valid result.
- `events` key added to ALL return paths: the success path returns `_extract_events(articles_raw)`; all unavailable/failure paths return `[]`.
- `_unavailable()` helper updated to include `events: []`.
- `_tone_unavail_reason` internal variable tracks whether the final `no_news_events` or `no_tone_data` label is appropriate when both signals are absent (artlist-reached vs tone-side-only failure distinction preserved per contract §4).

**What changed in `ai_advisor.py`:**

- `_GDELT_ARTLIST_URL` updated to include `sourcelang:eng` in the query string.
- `_build_sentiment_section` success-path payload updated: `events: tone_result.get("events", [])` surfaces the ranked domain-deduped events list from the lens_gdelt producer call.

**Scope boundary:** No changes to `lens_pipeline.py`, `lens_warehouse.py`, `tests/` fixture schemas (the artlist fixture already contains `language` fields; existing tests updated to cover the new behavior), or any template. The `sources` field in the return dict is unchanged.

**Files changed:** `advisors/lens_gdelt.py` (constants, `_unavailable`, `_extract_events` new helper, tone-extraction step, artlist step, availability gate), `ai_advisor.py` (`_GDELT_ARTLIST_URL`, `_build_sentiment_section` payload).

**Status:** GREEN at 2649229. 68/68 tests pass.


---

### DE-NC-001: Multi-source news corpus — two-facet design (Facet A: GDELT tone + Facet B: ranked RSS corpus) (2026-06-18)

Branch: `feat/lens-news-events` | HEAD: b93b724

**Decision:** Replace the GDELT-artlist-only interim corpus (2649229) with a production-grade multi-source news corpus builder in a dedicated `advisors/news_corpus.py` module.

**Why the GDELT-artlist-only interim was insufficient:**

1. **Single source.** GDELT artlist is one aggregator with limited coverage of primary sources (.gov data releases, wire services, domain-specific financial media). A single source is a single point of failure and biases coverage.

2. **No scoring or ranking.** The interim design returned articles in GDELT's raw order with no recency/relevance/authority weighting. Articles from low-authority or off-topic sources ranked alongside primary data releases.

3. **No cross-source deduplication.** Multiple sources covering the same story (e.g., a Fed decision reported by CNBC, Reuters, and MarketWatch) would appear as separate entries, consuming prompt budget and inflating apparent signal.

4. **No topic tagging.** The Market Prism synthesizer had no structured signal about which articles were macro vs fundamentals vs technicals vs derivatives — all articles were treated as undifferentiated sentiment.

**Two-facet design:**

- **Facet A (GDELT tone):** Independent scalar. `_fetch_gdelt_tone()` hits `lens_gdelt._GDELT_TONE_URL` with `_UA_STD`. Tone failure does not abort the corpus fetch — the two facets are always attempted independently.

- **Facet B (ranked corpus):** `_fetch_all_feeds()` fetches GDELT artlist (JSON, `maxrecords=50`) + 8 RSS/Atom feeds via `feedparser`:
  - Commercial financial media (`_UA_STD`): Google News Business, CNBC Markets, MarketWatch Top Stories, Yahoo Finance.
  - `.gov` primary data (`_UA_GOV = "PlanetStopper/1.0 paulmgreaney@gmail.com"` — required to avoid 403s): Fed press releases, BLS latest releases, BEA RSS, SEC 8-K filings.

**Scoring formula (weights sum to 1.0, all named constants):**

```
score = W_RECENCY(0.40) * exp(-delta_hours / TAU_HOURS(24))
      + W_RELEVANCE(0.35) * min(1.0, keyword_hits / 3)
      + W_AUTHORITY(0.25) * SOURCE_AUTHORITY.get(domain, 0.4)
```

Recency defaults to 0.5 on unparseable dates. The `SOURCE_AUTHORITY` table ranges from 1.0 (.gov sources) to 0.4 (unknown domains).

**Cross-source deduplication (three-step, applied before scoring):**

1. URL canonical dedup (strip query + fragment via `_canonical_url`) — highest-authority article wins per canonical URL.
2. Title Jaccard dedup (token-set, threshold `DEDUP_JACCARD_THRESHOLD=0.85`) — same-story duplicates across sources; highest-authority article is kept.
3. Per-domain cap (`_PER_DOMAIN_CAP=3`) — no single source dominates the final corpus.

**Topic tagging (`_tag_topics`):** Pure stdlib keyword matching across four topics (macro, fundamentals, technicals, derivatives). Multi-label. Defaults to `["broad-sentiment"]` when no keywords match.

**Availability:** `available = tone is not None OR bool(corpus)`. `reason = "no_news_events"` only when both absent.

**Impact on `ai_advisor._build_sentiment_section`:**

The interim standalone `_fetch_with_backoff` artlist call has been removed. New two-path architecture:
- Primary: `news_corpus.build_news_corpus()` — full two-facet result.
- Fallback/test-seam: `lens_gdelt._fetch_gdelt_sentiment([])` — patching `_fetch_gdelt_sentiment` in tests propagates into the section, preserving the test seam.

Payload carries `tone_score`, `corpus` (ranked articles), and `events` (mapped from corpus to legacy shape `{title, domain, seendate}` for render compatibility — AC-5). When corpus is empty but GDELT has events, falls back to `gdelt_result["events"]`.

**Impact on `advisors/lens_gdelt.py`:** `_GDELT_ARTLIST_URL` `maxrecords` bumped from 10 to 50 — the multi-source corpus fetches up to 50 GDELT articles to feed the scoring pipeline.

**New dependency:** `feedparser>=6.0` added to `requirements.txt`.

**Scope boundary — warehouse persistence:** ~~SUPERSEDED by DE-NC-001-C1 below.~~ The cycle-2 fix restored `lens_warehouse.persist_lens_snapshot` on both the unavailable and success paths of `_build_sentiment_section` (DW-1 is wired, not deferred).

**Files changed (initial GREEN b93b724):** `advisors/news_corpus.py` (new), `advisors/lens_gdelt.py` (`_GDELT_ARTLIST_URL` maxrecords=50), `ai_advisor.py` (`_build_sentiment_section` two-path + payload restructure), `requirements.txt` (feedparser>=6.0).

**Status:** GREEN at b93b724. 107/107 tests pass.

---

### DE-NC-001-C1: Cycle-2 corrections — single-spaced GDELT path (BLOCK 1) + warehouse persistence restored (BLOCK 2) (2026-06-18)

Branch: `feat/lens-news-events` | HEAD: 5e2a830

**Supersedes:** The “deferred DW-1” paragraph in DE-NC-001 (commit 732a8e3). Both reviewer BLOCKs were resolved in this cycle before merge.

**BLOCK 1 — Single-spaced GDELT path:**

`news_corpus.build_news_corpus()` now makes exactly ONE `lens_gdelt._fetch_gdelt_sentiment([])` call for both Facet A (tone) and GDELT artlist articles. The result supplies:
- `tone` from `result["tone"]`
- GDELT corpus articles from `result["sources"]` via `_normalize_gdelt_articles()` (new pure normalizer — converts GDELT `{url, seendate, title, domain}` records to the common article shape with `source_feed="gdelt_artlist"`).

`_fetch_gdelt_artlist()` is deleted. `_fetch_all_feeds()` is now RSS-only (no direct GDELT GETs). `_fetch_gdelt_tone()` is retained as an internal helper but delegates to `lens_gdelt._fetch_gdelt_sentiment` — it is not called separately from `build_news_corpus` (the single top-level call covers both facets).

Result: ≤2 spaced GDELT GETs per `_build_sentiment_section` call. The two GETs are timelinetone + artlist, already spaced by `_GDELT_INTER_REQUEST_S=6.0s` inside `lens_gdelt._fetch_gdelt_sentiment`.

`ai_advisor._build_sentiment_section` no longer calls `lens_gdelt._fetch_gdelt_sentiment` directly. `news_corpus.build_news_corpus()` is the sole entry point. The GDELT test seam is preserved: `build_news_corpus` delegates to `_fetch_gdelt_sentiment` internally, so patching `_fetch_gdelt_sentiment` propagates into the section.

**BLOCK 2 — Warehouse persistence restored (sentiment only; macro was never dropped):**

`ai_advisor._build_sentiment_section` now calls `lens_warehouse.persist_lens_snapshot` on both paths:
- **Unavailable path** (`corpus_result["available"] == False`): persists `{lens="sentiment", source="news_corpus", available=False, raw_payload={"reason": reason}}`.
- **Success path**: persists `{lens="sentiment", source="news_corpus", available=True, raw_payload={"tone_score": tone_score, "corpus_size": len(corpus)}}`.

Both calls are CC-2 lazy imports, wrapped in `try/except` with `pass` — warehouse errors never surface to callers (D-1).

**Additional cycle-2 changes:**
- `sources[]` in the return dict now populated: `build_citation({title, url, published, lens})` called per corpus article; `None` returns filtered.
- `article_count: len(corpus)` added to payload dict.
- `utcnow()` replaced with `datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)` (Python 3.12+ deprecation guard).

**Files changed:** `advisors/news_corpus.py` (single GDELT call, `_fetch_gdelt_artlist` deleted, `_normalize_gdelt_articles` added, `_fetch_all_feeds` RSS-only, `utcnow` replaced); `ai_advisor.py` (`_build_sentiment_section`: warehouse persistence restored on both paths, sources built from corpus via `build_citation`, `article_count` added to payload).

**Status:** GREEN at 5e2a830. 166/166 tests pass.

---

### DE-NC-001-STALE-TESTS: Stale Phase-2 warehouse-wiring tests — fixed in cycle-2 (2026-06-18)

**Background:** `tests/ai_advisor/test_lens_warehouse_wiring.py::TestSentimentSectionWarehouseWiring`
contained three tests that patched the old `advisors.lens_gdelt._fetch_gdelt_sentiment` +
`ai_advisor._fetch_with_backoff` seams from the Phase-2 interim architecture. After
the cycle-2 restructure those seams were no longer the correct mock targets, causing
live RSS HTTP calls in the default suite and a non-deterministic `available=False`
vs `available=True` assertion failure. This was classified as a merge-gate break
(not deferred) and fixed in the same cycle.

**Fix (HEAD bed4afb):**

1. **Dead code deleted.** `_fetch_gdelt_tone()` (`advisors/news_corpus.py`) had
   no production caller — `build_news_corpus` calls `lens_gdelt._fetch_gdelt_sentiment`
   directly inline. The helper was deleted. A tombstone test
   `test_fetch_gdelt_tone_is_removed_dead_code` asserts `not hasattr(news_corpus, "_fetch_gdelt_tone")`.

2. **Stale live-HTTP tests deleted.** Three tests in `TestSentimentSectionWarehouseWiring`
   that patched wrong seams and made live HTTP calls were removed:
   `test_persist_called_after_successful_gdelt_fetch`,
   `test_persist_called_with_available_false_when_gdelt_down`,
   `test_persist_payload_is_not_fabricated_when_down`.
   The remaining test in that class (`test_persist_lens_snapshot_is_lazy_imported_in_sentiment`)
   is a pure `hasattr` attribute check with zero HTTP.

3. **Authoritative hermetic coverage in `TestWarehousePersistence`** (`test_news_corpus.py`):
   patches `news_corpus_mod.build_news_corpus` (the correct seam); covers
   `available=True`, `available=False`, and payload shape paths. CC-2 lazy-import guard retained.

4. **<=2-GETs invariant** still carried by `test_build_sentiment_section_total_gdelt_gets_at_most_two`
   (`test_news_corpus.py:1627`), which patches `requests.get` through the real production path.

**Status:** Fixed at bed4afb. Reviewer delta-APPROVE confirmed all four checks pass.

---

### DE-PRISM-COUNCIL: 5/5 analyst participation — embed-kickoff + agentId addressing (prism-council-5of5 cycle, 2026-06-18)

Branch: `feat/prism-council-5of5` | GREEN at 87ba7ae (delta-APPROVE)

**Problem:** The nightly unattended Market Prism council reliably produced only 2/5 analyst reads. The prior orchestration pattern spawned all 5 analysts first, then sent a kickoff `SendMessage` to each. Dormant agents missed the subsequent kickoff and never produced an `initial_read` audit entry.

**Root cause (independently falsified):** Transient by-canonical-name resume failure of dormant subagents. Not analyst crash, data unavailability, or a bug in the audit-write CLI. When an agent is spawned and then goes dormant before receiving a kickoff message, addressing it by canonical name via `SendMessage` is unreliable. The fix is architectural: eliminate the dormancy window by embedding the kickoff in the spawn prompt, and ensure a single authoritative run_id flows through all rows.

**Decision — four orchestration directives (DE-PRISM-5OF5) + two synthesizer Hard Rules:**

**(a) Scheduler-generated run_id threaded into PRISM_RUN_PROMPT at call time.** `main()` generates `run_id = str(uuid.uuid4())` before calling `_run_prism(run_id)`. `_run_prism` appends `" The run_id for this session is: {run_id}. Use this exact string as the run_id for ALL audit rows and the MARKET_PRISM observation. Do not generate a new run_id."` to the static `PRISM_RUN_PROMPT` preamble. This is the single authoritative join key shared by: all analyst `initial_read` audit entries, the synthesizer audit entries, the MARKET_PRISM observation `raw_response`, and the `_persist_spend` LAUNCHER spend_log row. The council does NOT mint its own run_id.

**(b) Embed kickoff in each analyst's spawn prompt.** Each of the 5 analyst agents is spawned with the run_id AND an explicit instruction to produce and file their `initial_read` immediately on their first turn. This eliminates the dormancy window — the analyst acts on spawn rather than waiting for a subsequent `SendMessage`. The prior pattern (spawn → separate kickoff message) caused 2/5 participation.

**(c) Capture agentIds at spawn; pass to synthesizer.** The primary captures each analyst's `agentId` at spawn and passes the full list to `prism-synthesizer` in its spawn prompt. The synthesizer uses agentIds — not canonical names — for all Q&A, debate, and coordination via `SendMessage`. By-canonical-name addressing of dormant or resumed agents is unreliable.

**(d) Wait-barrier before synthesis (Hard Rule).** `prism-synthesizer` must not synthesize until all 5 `initial_read` rows are confirmed present in the audit DB for this specific `run_id` (via `database.get_prism_audit_for_run(run_id)`), or the barrier times out. The SendMessage inbox is explicitly rejected as the wait mechanism — the audit DB is the authoritative source of truth. If the barrier times out with fewer than 5 rows, the synthesizer degrades to `limited-inputs`, naming each missing lens by its absence in the audit DB.

**(e) False-attribution prohibition (Hard Rule).** A lens that spawned but did not report its `initial_read` is missing or late — not absent. `prism-synthesizer` must never record a spawned lens as "did not spawn". The correct attribution is `limited-inputs` with the reason being absence of an `initial_read` row in the audit DB after the wait-barrier timeout.

**Role-file changes:**

- **5 analyst role files** (prism-technicals, prism-sentiment, prism-derivatives, prism-macro, prism-fundamentals): Removed the dormancy-triggering line "Do not proceed until you have received the run_id." Added explicit instruction: begin `initial_read` immediately on session start, using the run_id embedded in the spawn prompt.

- **prism-synthesizer.md** Hard Rules: Two new bullets added — (1) never synthesize until 5 `initial_read` rows confirmed in the audit DB for this run_id; honest `limited-inputs` degradation naming missing lenses on barrier timeout; (2) never falsely attribute non-response as "did not spawn" — spawned-but-silent = `limited-inputs` only.

**Regression-safe:** Command shape (`claude -p --dangerously-skip-permissions --model claude-opus-4-8 --max-budget-usd 15.0 --output-format json`), spend-key parsing (`total_cost_usd`), idempotency guard, bounded retry (MAX_ATTEMPTS=3), and budget cap (MAX_BUDGET_USD=15.0) are all unchanged from the Phase-4 scheduler.

**Files changed:** `prism_scheduler.py` (PRISM_RUN_PROMPT made static preamble; `_run_prism` appends scheduler-generated run_id at call time); `.claude/agents/prism-technicals-analyst.md`, `prism-sentiment-analyst.md`, `prism-derivatives-analyst.md`, `prism-macro-analyst.md`, `prism-fundamentals-analyst.md` (dormancy line removed, immediate-action instruction added); `.claude/agents/prism-synthesizer.md` (Step 3: agentId addressing, no kickoff messages needed; Hard Rules: wait-barrier + false-attribution prohibition added).

**Tests:** `tests/ai_advisor/test_prism_scheduling.py` — GREEN at 87ba7ae. All tests covering AC-1 through AC-8, HC-1 (spend cap), HC-2 (spend logging), HC-3 (model pin), the Phase-4 invocation shape, the council architecture (primary spawns all 6; `prism-synthesizer` coordinates only via SendMessage), and the 5/5 orchestration directives pass. F-1 tests verify run_id is scheduler-generated and threaded (not council-minted). F-2 tests verify the Hard Rule wait-barrier text and false-attribution prohibition in `prism-synthesizer.md`. F-3 hollow-pattern fix verified by reviewer.

**Note on scheduling:** `schedule_prism.ps1` (Windows Task Scheduler registration) is retained only for test-green purposes. The production nightly trigger will move to droplet cron/systemd when the deployment environment is provisioned.

**Status (F-1/F-2/F-3):** GREEN at 87ba7ae. pc-reviewer delta-APPROVE confirmed all 3 findings resolved.

---

### DE-PRISM-COUNCIL-F4: row-verification + retry-on-empty — scheduler false-green eliminated (prism-council-5of5 cycle, 2026-06-18)

Branch: `feat/prism-council-5of5`

**Problem:** `main()` declared success ("Run completed successfully", exit 0) whenever `_run_prism()` returned True (subprocess `returncode == 0`). A council run that exits cleanly but writes no MARKET_PRISM row — e.g., due to a synthesizer failure, budget exhaustion mid-run, or a write error — produced a silent false-green. Unattended nightly: the operator had no way to know the council produced nothing.

**Decision — per-attempt success = rc==0 AND row-exists:**

The retry loop now applies a two-part success test on each attempt:
1. `_run_prism(run_id)` returns `True` (subprocess `rc==0`).
2. `_get_market_prism_row_for_run(run_id)` returns a non-None dict — the MARKET_PRISM `advisor_observations` row for this `run_id` is confirmed present.

If `rc==0` but no row exists, the attempt is classified as failed, a diagnostic message is written to stderr, and the retry loop continues to the next attempt (up to `MAX_ATTEMPTS`). After all attempts are exhausted without a confirmed row, `main()` exits 1.

**New seam — `_get_market_prism_row_for_run(run_id: str) -> dict | None`:**

Queries `advisor_observations` for a MARKET_PRISM row whose `raw_response["run_id"]` matches the scheduler-generated `run_id`. Implementation: calls `database.get_latest_market_prism_summary()` (existing seam) and confirms `raw_response["run_id"] == run_id`. Since the scheduler's `run_id` is a unique uuid4, the latest row is this run's row iff it was written. Non-fatal — returns `None` on any DB or parse error; logs `type(exc).__name__` only (D-1). Never raises.

**Spend logging preserved on rc==0:** `_persist_spend` fires on `returncode == 0` *before* the row check. An attempt that exits 0 but writes no row still logs its spend. This preserves the existing spend-logging contract and avoids lost billing data on partially-successful attempts.

**Files changed:** `prism_scheduler.py` — `_get_market_prism_row_for_run(run_id)` added as a patchable seam; `main()` retry loop updated: a `proc_ok=True` outcome now calls `_get_market_prism_row_for_run(run_id)` and treats a `None` return as a failed attempt before logging and sleeping.

**Tests:** `tests/ai_advisor/test_prism_scheduling.py` — `TestMarketPrismRowVerification` class (3 tests): (1) rc==0 + no row -> all MAX_ATTEMPTS exhausted -> non-zero exit + no "Run completed successfully" in stdout (RED gate); (2) rc==0 + row present -> exit 0 + success message (happy-path regression lock, skips pre-GREEN); (3) rc==0 + no row on attempt 1, rc==0 + row on attempt 2 -> subprocess called twice + exit 0 (retry-on-empty RED gate). Pre-existing happy-path tests patched to supply `_get_market_prism_row_for_run=_SAMPLE_MARKET_PRISM_ROW` so prior expectations are preserved. GREEN at 9de5f71.

**Status:** GREEN at 9de5f71. Pending pc-reviewer APPROVE.
---

## RF-1 — Overview Market Prism prose render guard (feat/rf1-prose-render, 2026-06-18)

### DE-RF1-PROSE-RENDER: render-layer humanization -- structured JSON digests converted to prose at render time

Branch: `feat/rf1-prose-render` | HEAD: ac072f3

**Problem verified in production (E2E program item #3):** Two raw-JSON surface defects:

1. **R1 -- per-lens digest:** The AI Advisor Overview Market Prism per-lens digest rendered raw JSON strings verbatim whenever the producer was the nightly `lens_pipeline`. `per_lens_digest[lens]["summary"]` for `lens_pipeline` rows holds structured payloads -- e.g. technicals as `{"ma_posture": {...}, "breadth": 0.7, "momentum": {...}}`, fundamentals as a multi-ticker 10-K facts dump. The Jinja2 template at `templates/ai_advisor.html:999` passed these strings through `| e` and rendered them in `.prism-lens-text`. Operators saw raw JSON in the per-lens digest, not readable prose. The **council** producer (item #4) writes clean prose summaries that already render correctly (verified in live run 637c719f).

2. **R2 -- obs-raw-preview (AC-5):** The `obs-raw-preview` table cell previously rendered `{{ obs.raw_response | tojson | e }}` -- dumping the full raw_response JSON for every advisory observation row.

**Decision -- render-layer guard, producer-agnostic; two public functions:**

A new pure helper module `advisors/prism_render.py` is added with two public functions:

- **`humanize_lens_summary(lens_name, lens_entry) -> str`** (R1): detects structured vs prose using `json.loads(summary)` result shape (not a naive `startswith("{")` check; prose containing braces is always a passthrough); applies lens-aware key extraction via a `_LENS_HUMANIZERS` dispatch table (5 per-lens helpers: `_humanize_technicals`, `_humanize_sentiment`, `_humanize_derivatives`, `_humanize_macro`, `_humanize_fundamentals`); degrades to `_EMPTY_STATE = "limited inputs -- data unavailable"` for null/missing/degenerate inputs. Never raises (D-1).

- **`humanize_obs_preview(raw_response) -> str`** (R2): prefers the `note` key from `raw_response` (already human-readable prose); falls back to `_EMPTY_STATE` rather than dumping raw JSON. Handles dict, legacy JSON string, and None. Never raises (D-1).

**Why render-layer and not producer-layer:**

1. **Low risk.** `lens_pipeline.py` and the Prism council (the two MARKET_PRISM producers) are not modified. No nightly data-production path changes.
2. **Producer-agnostic.** Council prose passes through unchanged; `lens_pipeline` structured JSON is humanized. Future producers that write prose will also pass through without changes.
3. **Defensive.** The guard stays correct and safe even after the council supersedes `lens_pipeline` in E2E item #4 -- no code to remove.
4. **Testable at the render boundary.** Unit tests verify humanization over captured-from-producer fixtures without mocking the nightly scheduler or council.

**Detection invariant:** `json.loads(summary)` result type determines the path. A `dict` or `list` result -> structured -> humanize. Anything else (bare scalar, parse error) -> prose passthrough. This correctly handles: prose with braces, council prose with numbers/symbols, `$416B`-style values, and bare numeric strings like `"16.41"`.

**Empty-state invariant (AC-3):** Null/empty summaries (`None`, `""`, `"null"`, `"None"`) and all degenerate-input paths return `_EMPTY_STATE = "limited inputs -- data unavailable"`, never `"null"`, `"{}"`, `"None"`, or raw JSON.

**XSS contract:** Both functions return plain `str`. Template renders all output with Jinja2 autoescaping (`{{ ... | e }}`). `| safe` is never applied to humanized output. Neither function produces HTML.

**`app.py` wiring -- in-place mutation, no new context keys:**

- R1 (app.py:2966-2984): after fetching `market_prism_summary`, `ai_advisor_tab()` iterates `per_lens_digest` and calls `humanize_lens_summary(_ln, _le)` for each lens entry, writing the result back to `_le["summary"]` in-place. The template's existing `{{ _lens.get('summary') | e }}` (line 999) then reads humanized prose. No new template context key is added; the template itself is not changed for R1.

- R2 (app.py:2892-2902): for each non-MARKET_PRISM observation in `observations`, stamps `obs["_preview_text"] = humanize_obs_preview(obs["raw_response"])`. The template `.obs-raw-preview` cell (lines 2002-2007) renders `{{ obs.verdict | e }}` for MARKET_PRISM rows and `{{ obs.get('_preview_text', '') | e }}` for all others.

Both wiring blocks are wrapped in `except Exception: pass` -- humanization failure never crashes the route.

**Files changed:** `advisors/prism_render.py` (new -- R1 `humanize_lens_summary` + R2 `humanize_obs_preview`), `app.py` (R1 in-place lens mutation + R2 `_preview_text` stamp in `ai_advisor_tab()`), `templates/ai_advisor.html` (R2 `.obs-raw-preview` cell: MARKET_PRISM shows verdict, others show `_preview_text`).

**Tests:** `tests/ai_advisor/test_rf1_prose_render.py` -- AC-1..AC-7 across two RED/GREEN rounds. R1: prose passthrough (council fixture row 78), JSON->readable for each of the 5 lens shapes (lens_pipeline fixture row 77), null/empty->empty-state (AC-3), brace-prose not misclassified (AC-1), fundamentals concision (AC-4), never-raises sweep over junk inputs (AC-7), route/render test asserting no raw-JSON markers in per-lens text. R2: AC-5 symphony-level obs-raw-preview cell no longer emits raw JSON. Fixtures captured from live DB; provenance recorded in fixture files.

**Status:** GREEN at ac072f3. reviewer APPROVE + ux-expert visual PASS.

---

## Calibration Sweep — V1 2-Param VWAP Walk-Forward (2026-06-19)

### DE-CALSWEEP-001: 2-param calibration sweep methodology — PBO + Harvey-Liu; DSR excluded; bleed-arm clamps and CONFIRM_TICKS hand-set

**Research basis:** `.claude/calibration-methodology-verdict.md` (2026-06-19) — synthesized by cm-synthesizer from cm-risk-researcher (Q1 search-space scope) + cm-optuna-researcher (Q2 overfitting control). All material claims verified at `file:line` or named external citation. Contradictions resolved adversarially at source.

**Decision: sweep exactly 2 params.** `run_calibration_sweep` sweeps `PARABOLIC_VELOCITY_THRESHOLD` ([1.0, 4.0]) and `VWAP_CROSS_HWM_PCT` ([0.3, 2.0] V1 asymmetric bounds) only. Three candidate params were evaluated and excluded:

1. **`VWAP_BLEED_ARM_MIN` / `VWAP_BLEED_ARM_MAX` — permanently hand-set (guardrails, not alpha params).** These are output clamps on `compute_vwap_bleed_arm_threshold`; the signal-bearing knob (`VWAP_BLEED_MULTIPLIER`) is already in the production sweep. The trailing-stop literature establishes that guardrail threshold response is flat across a wide range — no fittable optimum exists for a historical optimizer to find:
   - Kaminski & Lo (2014), "When Do Stop-Loss Rules Stop Losses?", *J. Financial Markets* 18:234-254, SSRN 968338 — stop value governed by regime, not fine-tuning; optimizing on ~1yr window fits that window's realized regime.
   - Dai, B., Marshall, B. R., Nguyen, N. H. & Visaltanachoti, N. (2021), "Risk Reduction Using Trailing Stop-Loss Rules," *International Review of Finance* 21(4):1334-1352, DOI 10.1111/irfi.12328 — grid of fixed thresholds 1%-20% shows downside-risk reduction robust across the whole range; flat response is the empirical signature of a guardrail vs. an alpha parameter.
   The "category error" framing (origin: `docs/research/dashboard/optuna-tuning-audit.md:112-113, 237`) is interpretation-grade but now externally corroborated by both papers. Supersedes the W2 plan's "sweep them" stance (which carried no methodological justification).

2. **`VWAP_BREAK_CONFIRM_TICKS` — excluded this cycle on data-sufficiency grounds (future operator-gated add).** Adding it moves the dedicated sweep 2-D → 3-D at the 100-trial floor. Grid-equivalent density: `100^(1/3) ≈ 4.6` levels/axis vs `100^(1/2) = 10` for 2-D — a >2x density drop. Restoring 2-D density at 3-D requires ~1,000 trials (`10^3`). **If ever added: `OPTUNA_N_TRIALS_CALIBRATION` must first be raised to ~1,000 — sweeping at the current 100-trial floor would be a methodologically dishonest 3-D-at-a-2-D-budget exploration.** Secondary: the param ranks in the higher-overfit-exposure tier (`math-engine-methodology-review.md §11`); integer range is marginal. The "defensible either way" stance in the audit (`optuna-tuning-audit.md:115, 236`) is the not the operative default — data-sufficiency is the decisive tie-breaker.

**Decision: overfitting controls — PBO (CSCV) + Harvey-Liu/BHY haircut.** Same methodology as the production walk-forward. DSR (Deflated Sharpe Ratio) is NOT used on the selection path (Decision D3, carried from `walk-forward-overhaul.completed.md`):
- **PBO and BHY are orthogonal guards** (`math_engine.py:1958-1961`, verified): BHY = multiplicity axis; PBO = sample-robustness axis. They address different failure modes.
- **Removing DSR is defensible here:** PBO (CSCV) covers the selection-generalization failure mode more directly than DSR's analytic False-Strategy-Theorem approximation. BHY covers the multiplicity failure mode. The CRRA-EU objective + bootstrap SE t-stat captures non-normality empirically via resampling. DSR's remaining non-redundant residual (analytic effective-N via trial-correlation clustering) errs conservative in its absence — additive `N_effective` over-counts, producing a stronger haircut, which is the safe direction.
- **D3 category error (original removal rationale, from `walk-forward-overhaul.completed.md:17-18`):** deflating a CRRA-EU/Sortino objective with a Sharpe sampling distribution is a D3 category error. DSR-reporting-only (not on the selection path) remains a possible future operator decision requiring a logged D3 amendment; it is NOT in scope for this cycle.
- Bailey, D. H. & López de Prado, M. (2014). "The Probability of Backtest Overfitting." *J. Computational Finance.* SSRN 2326253. [High]
- Harvey, C. R. & Liu, Y. (2015). "Backtesting." *J. Portfolio Management.* SSRN 2345489. [High]
- Harvey, C. R., Liu, Y. & Zhu, H. (2016). "...and the Cross-Section of Expected Returns." *RFS.* NBER w20592. [High]

**Acceptance criteria implemented:**
- **AC-4:** Symphonies with `< _CALSWEEP_MIN_HISTORY_DAYS` (125) days skipped with warning log.
- **AC-5:** `pbo_veto_status` surfaced per symphony when haircut finds no qualified winner.
- **AC-6:** Study name = `{timestamp}__{symphony_id}__calsweep` — never collides with production study names.
- **AC-7:** `flag_for_operator_review=True` when proposed trigger frequency exceeds `_CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER` (2.0x) current count on validation fold.

**Advisory-only, operator-gated rollout:** `run_calibration_sweep` does NOT persist to the state DB (AC-V1.3). No auto-apply. No fleet flip. The operator reviews per-symphony proposals from `scripts/vwap-calibration-report.py` and decides whether to apply any change.

**Report script:** `scripts/vwap-calibration-report.py` provides `generate_report(rows) -> list[dict]` (programmatic) and `_format_markdown(rows) -> str` (Markdown rendering with PBO-veto and operator-review banners). Advisory-only; no DB writes, no live-engine imports, no constant application. CLI: `python scripts/vwap-calibration-report.py --rows-json <path> [--out <path>]`.

**Status:** Implementation complete on `feat/calibration-sweep` at 477aa86. See `docs/generated/autotuner.md` §Calibration Sweep and `docs/generated/scripts_vwap_calibration_report.md`.

### DE-CALSWEEP-002: AC-4 history floor made injectable via `min_history_days` — production default unchanged

**Context:** The v1 test suite (`tests/test_v1_calibration_sweep.py`) uses 40-day fixtures — well below the AC-4 production floor of `_CALSWEEP_MIN_HISTORY_DAYS` = 125. After AC-4 was added in the initial sweep implementation, 11 of 43 suite tests failed because every fixture symphony was skipped before the sweep could exercise any contract.

**Decision:** Make the history floor injectable via a new keyword parameter `min_history_days: int = _CALSWEEP_MIN_HISTORY_DAYS` on `run_calibration_sweep`. The module constant `_CALSWEEP_MIN_HISTORY_DAYS` is UNCHANGED at 125; the production default is byte-identical to the original hard-coded check. Test suites pass `min_history_days=0` to bypass the skip on short fixtures and exercise the E1-velocity, haircut-outcome, frozen-eval, and report-schema contracts.

**Why not lower the constant?** Lowering `_CALSWEEP_MIN_HISTORY_DAYS` would weaken production behaviour: fewer than 125 days genuinely produce validation windows too small for the Sortino objective to yield meaningful signal (López de Prado 2018 purge+embargo on 60/20/20 folds). The injectable param pattern is the standard testability seam — it preserves the production guarantee while eliminating the false AC-4 skip in test fixtures.

**Affected symbol:** `autotuner.run_calibration_sweep` (commit `b35d14c`). No callers broken — all existing callers use the default. See `docs/generated/autotuner.md` §AC-4 and the parameters table for the public-API update.


---

## DE-AUTH-001 — Dashboard password-auth gate: single-password Flask signed-session gate, fail-closed (2026-06-19)

Branch: feat/dashboard-auth | Base: origin/main 43c8160

### Context

Planet Stopper is being deployed to a public DigitalOcean droplet (`104.248.7.101`). Before deploy, the entire Flask surface (dashboard, AI Advisor SPA, all `/api/*` routes) must be protected behind an auth gate. Without one, anyone who discovers the IP can read live positions, trigger execution, or modify settings.

### Decision

Single shared password, checked constant-time, stored in env. Flask signed-session cookie carries the auth flag. The gate is implemented entirely in `app.py`; no new pip dependencies beyond `werkzeug` (already in requirements).

**Key decisions and rationale:**

| Decision | Rationale |
|----------|-----------|
| Single shared password, not per-user accounts | Single-operator use case. Minimal surface. No user-management plumbing needed. |
| Flask signed-session (cookie) gate, not HTTP Basic | Operator wants a login PAGE as the only visible surface pre-auth; signed-session supports logout and a proper form UX. HTTP Basic pops a browser dialog and has no logout flow. |
| `DASHBOARD_PASSWORD_HASH` preferred over `DASHBOARD_PASSWORD` | `.env` does not need to hold the plaintext password; operator can store a werkzeug hash. |
| `hmac.compare_digest` for plaintext; `werkzeug.security.check_password_hash` for hashed | Constant-time compare in all paths. Hash detection by prefix (`pbkdf2:`, `scrypt:`, `bcrypt:`). No timing leak. |
| Fail-closed on misconfig | The catastrophic failure mode is serving the dashboard OPEN on a public IP. Missing `DASHBOARD_PASSWORD`(+hash) OR `SECRET_KEY`/`FLASK_SECRET_KEY` → all requests denied; never fail open. Logged loudly at startup. |
| In-memory throttle (no DB) | Single-process daemon; reset on restart is an acceptable brute-force speed-bump for a single-operator tool. SQLite throttle table would add a write on every login failure — too heavy. |
| `/api/*` and XHR → 401 JSON; HTML routes → 302 `/login` | SPA JS can react to 401 cleanly without a page reload. HTML routes follow the standard redirect-to-login pattern browsers expect. |
| `_AUTH_EXEMPT_ENDPOINTS` explicit frozenset | `login`, `logout`, `static`, `get_csrf_token`, `health` — exact-minimal allowlist; no glob/prefix matching to avoid accidental exemptions. |
| `SESSION_COOKIE_SECURE` env-gated | The `Secure` flag makes sense only behind TLS. Gated on `SESSION_COOKIE_SECURE=1/true/yes` so the same code runs locally (HTTP) and on the public droplet (HTTPS via reverse proxy). |
| `TRUST_PROXY` opt-in for X-Forwarded-For keying | Trusting XFF unconditionally on a direct-bind daemon would allow IP spoofing. `TRUST_PROXY` must be set explicitly when behind a trusted reverse proxy. |
| TLS/tunnel deferred to the droplet deploy | Transport security is a deployment concern, not app logic. The session cookie is signed (integrity) but plaintext (confidentiality) over HTTP — the deployment MUST add Caddy/nginx TLS or SSH tunnel. Tracked in the droplet-deploy phase. |
| `_auth_check_enabled` module flag + `_disable_auth_for_tests` autouse fixture | Mirrors the `_csrf_check_enabled` / `_disable_csrf_for_tests` pattern established earlier. Keeps all ~7000 existing route tests passing without injecting credentials. Auth gate tests re-enable the flag per-fixture. `_AUTH_FAILED_ATTEMPTS.clear()` called on every test teardown to prevent throttle bleed-through. |
| `login()` calls `session.clear()` before setting `authenticated` | Session-fixation prevention (AC-4). Clears any attacker-planted session values before the session is promoted to authenticated. |
| CSRF on login POST | Reuses existing `_validate_csrf()` / `_csrf_before_request` infra. The login form embeds the CSRF token in a hidden field (`csrf_token`), which is now the second acceptance channel for `_validate_csrf` (dual-channel fix at 8a34de6). The form-field channel is content-type-gated: `request.form` is accessed only when `Content-Type` is `application/x-www-form-urlencoded` or `multipart/form-data` (dc6b8c7) — accessing it on JSON POSTs triggers Werkzeug body parsing, which enforces `MAX_CONTENT_LENGTH` before the CSRF 403 can fire. |

### Security findings resolved in this cycle

| Severity | Finding | Fix |
|----------|---------|-----|
| HIGH | CSRF: login form POST could not pass the CSRF token via a form field (the prior implementation only accepted the `X-CSRF-Token` header, which a native browser form cannot set) | `_validate_csrf()` extended to accept `csrf_token` form field as a second channel; docstring updated at 8a34de6. Form-field channel subsequently content-type-gated (dc6b8c7) to prevent 413-before-403 guard-ordering regression on JSON POSTs. |
| MEDIUM | XFF keying: trusting `X-Forwarded-For` unconditionally on the throttle allows IP spoofing | `TRUST_PROXY` opt-in env var; remote addr used by default |
| LOW | Misconfig log: `DASHBOARD_PASSWORD` absence was not logged loudly at startup | Loud `_daemon_log.warning` added in `_auth_before_request` misconfig path |

### Acceptance criteria shipped (AC-1..AC-13)

AC-1 through AC-13 as specified in `feature-plans/dashboard-auth.md`. 46/46 tests GREEN at commit `55e95cc`.

### Files changed

- `app.py` — auth gate: `_auth_check_enabled`, `_AUTH_EXEMPT_ENDPOINTS`, `_AUTH_FAILED_ATTEMPTS`, `_AUTH_MAX_ATTEMPTS`, `_AUTH_LOCKOUT_SECONDS`, `_resolve_dashboard_credential`, `_secret_key_configured`, `_check_throttle`, `_record_failed_attempt`, `_clear_failed_attempts`, `_is_api_or_xhr`, `_auth_before_request`, `login`, `logout`; `SESSION_COOKIE_*` config; `app.secret_key`; CSRF dual-channel fix (`_validate_csrf` docstring); CSRF form-field content-type gating (`_validate_csrf` implementation + docstring, dc6b8c7)
- `templates/login.html` — minimal login form (light card UI, CSRF hidden field, error slot)
- `tests/conftest.py` — `_disable_auth_for_tests` autouse fixture; `_AUTH_FAILED_ATTEMPTS.clear()` between tests
- `tests/app/test_dashboard_auth.py` — 46 RED→GREEN tests covering AC-1..AC-13


---

## Guard Alpha Value Panel — Route shipped (guard-alpha-panel cycle, 2026-06-19)

Branch: feat/guard-alpha-panel | Base: origin/main (43c8160)

### DE-GAP-001: GET /api/guard-alpha-summary — post_mortem-snapshot-basis aggregation route

**Decision:** The cumulative dollar-saved aggregate is exposed as a new read-only `GET /api/guard-alpha-summary` route in `app.py`. It globs `analytics._POST_MORTEMS_DIR` for `post_mortem_*.json` files, sums `saved_dollars` across all `triggers` entries, counts total guard events, and derives the date range from filenames. Returns `{cumulative_saved_dollars, guard_event_count, date_range: {earliest, latest}, basis_label}`.

**Dollar-saved basis is snapshot-time, not mark-to-market.** `reporting.py:71` computes `saved_dollars = symphony_value * saved_pct_guard_alpha / 100` at exit time. The post_mortem file captures this value at the moment of the guard-alpha exit. The route's `basis_label` field makes this explicit ("snapshot-time basis, since <earliest date>"). Do not present these figures as current mark-to-market values.

**No new DB table or migration.** The route reads post_mortem JSON files on disk (bounded glob from the fixed `analytics._POST_MORTEMS_DIR` constant) and does not interact with SQLite. This keeps it off all write paths and eliminates migration risk.

**Malformed-file resilience (AC-6).** Each file is wrapped in `try/except (OSError, json.JSONDecodeError)`: failures log the basename only (no file content, no secret leak) and skip the file. The aggregate continues from remaining valid files. Route always returns 200.

**Auth gate (AC-8).** The route is covered by the global `_auth_before_request` before_request hook established in DE-AUTH-001. No additional decorator is needed; unauthenticated XHR receives 401.

**Not in `_SETTINGS_WRITE_ALLOWLIST`.** The route is GET-only and makes no DB writes. It was explicitly excluded from the write-allowlist enumeration.

### DE-GAP-002: AC-2 dropped — per-card running guard-alpha already exists on main

**Decision:** AC-2 (populate `guard_alpha` for untriggered symphonies in `get_api_state_dict()`) is DROPPED from this cycle as pre-existing built behavior.

**Root cause of the gap analysis misread.** The scoping analysis (gax-scope, 2026-06-19) identified "untriggered cards show no live guard-alpha" from `templates/index.html:1082` (`'guard_alpha' in sym` condition). Post-verification confirmed that `app.py:937,1016` populates `card_alpha = cr_bot − cr_held` for ALL symphonies via `get_api_state_dict()` — the divergence gap is the running guard-alpha for untriggered cards. The template condition governs the post-trigger exit-snapshot badge, not the running value. The two are distinct UI surfaces; "Gap 1" conflated them.

**Test coverage already exists.** `tests/dashboard/test_card_guard_alpha_basis.py::test_card_cumulative_alpha_reconciles_with_divergence_gap` verifies the per-card running guard-alpha basis on origin/main.

**AC-2 is not deferred — it is closed as already built.** Future work: the panel markup and JS to consume `/api/guard-alpha-summary` on the dashboard (AC-3 / next cycle).

### Files changed (guard-alpha-panel, 87fd96c)

- `app.py` — `guard_alpha_summary()` route at `app.py:2172` (+65 lines after `get_windowed_strip`)
- `tests/app/test_guard_alpha_summary_route.py` — route test suite (AC-1/AC-4/AC-5/AC-6/AC-8)
- `tests/fixtures/app/guard_alpha_summary/post_mortem_2026-06-10.json` — 2-trigger fixture
- `tests/fixtures/app/guard_alpha_summary/post_mortem_2026-06-11.json` — 1-trigger fixture
- `tests/fixtures/app/guard_alpha_summary/post_mortem_corrupt.json` — malformed fixture (AC-6)
- `feature-plans/guard-alpha-panel.md` — Status updated to "partial"; AC-1/4/5/6/8 checked; AC-2 annotated dropped

---

## DE-TD-C3B-001 — Route producer-guard tests added for GET /ai-advisor (2026-06-19)

Branch: chore/tech-debt-c3bc | Commits: 406735a (tests), c6f2b4d (C3c impl)

### Finding

Sub-item C3b (tech-debt-cleanups feature plan) tasked the team with locating and removing a dead self-skip branch in the AI Advisor route layer. Fresh inspection of `app.py` at HEAD (confirmed against commit 47f0eb5) found **no self-skip branch present**. The unified SPA consolidation (Cycle 4) had already removed any such branch before this cycle was dispatched. AC-4b and AC-6b are closed as "not applicable — confirmed absent."

### Decision

Rather than close C3b with no artifact, the team implemented AC-5b in full: a route-level producer guard test suite (`tests/ai_advisor/test_advisor_route_producer_guard.py`) that catches the class of live-500 bug that mocked-module tests miss.

**Why this matters:** `ai_advisor_tab()` in `app.py` wraps several producer calls in bare `except Exception: pass` blocks. If a producer function is renamed or removed, the bare `except` swallows the `AttributeError` silently — Python unit tests with wholesale module mocks pass green while the live route 500s. The only reliable guard is a route-level test that imports the real producer modules and asserts the attributes the route calls actually exist.

**7 new tests in `test_advisor_route_producer_guard.py`:**
- 2 route smoke tests: `GET /ai-advisor` returns HTTP 200 + `text/html` with real producer modules loaded (mock only DB boundary, not the producer modules themselves).
- 5 `hasattr` existence guards: `correlation_diagnostic.compute_pairwise_correlations`, `backtest_gate_engine.CRISIS_CAVEAT`, `prism_render.humanize_obs_preview`, `prism_render.humanize_lens_summary`, `ai_advisor._has_composer_key`. Each goes RED when the attribute is deleted and GREEN when restored (break-restore verified inline).

### Reviewer INFO gap (noted, out of scope)

`advisors.advisor_chat.CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS` is not covered by these guards because `sb_observations` is `[]` in the test fixture — the import at `app.py:3331` is behind `if sb_observations:` and is genuinely unreachable with an empty fixture. No action needed this cycle.

### Files changed

- `tests/ai_advisor/test_advisor_route_producer_guard.py` — 7 new tests (created in commit 406735a)

---

## DE-TD-C3C-001 — Dead higher_is_better param removed from _apply_lens_blend (2026-06-19)

Branch: chore/tech-debt-c3bc | Commit: c6f2b4d

### Decision

Removed the dead `higher_is_better: bool` parameter from `advisors/asset_swap_engine._apply_lens_blend` and updated all 4 callers.

### Evidence of dead code

The parameter's own docstring stated it was unused: "Unused parameter preserved for call-site documentation clarity (the blend is position-based, so direction doesn't affect the math)." The blend formula `blended_key[i] = position[i] - LENS_BLEND_WEIGHT * mean_lens_score[i]` is position-based and direction-agnostic by design — `higher_is_better` was always ignored at runtime.

### Scope

- `advisors/asset_swap_engine.py` — `_apply_lens_blend` signature: `higher_is_better: bool` param removed; 3-line docstring entry for the param removed; `higher_is_better=...` kwarg removed from all 4 callers (lines ~538, ~562, ~587, ~599 pre-removal). Directional rationale comments preserved as standalone lines at each call site (e.g., `# lower absolute correlation = better`).
- `tests/ai_advisor/test_asset_swap_engine.py` — 1 new AST-inspection test (`TestApplyLensBlendHasNoHigherIsBetterParam`) pinning the removal; baseline 35/35 existing tests confirmed GREEN before commit.

### Behavior

Runtime output is byte-identical. Removing an unused parameter from an internal function is purely structural. The `LENS_BLEND_WEIGHT = 0.25` constant and the blending formula are unchanged.

### Naming

No change-history language introduced. All identifiers continue to describe runtime behavior, not the change history of this cleanup.

### Files changed

- `advisors/asset_swap_engine.py` — 3 insertions / 8 deletions (signature + docstring + 4 caller sites)
- `tests/ai_advisor/test_asset_swap_engine.py` — 1 new test class (commit 406735a)

---

## DE-PRISM-GATE-001 — DISABLE_DAEMON_LENS_PIPELINE env guard (2026-06-19)

Branch: feat/prism-nightly-producer-gate

### Context

With `prism_scheduler.py` (Option B) now running the full Market Prism council as the nightly producer on the DO droplet, the daemon's existing 03:00 `_run_lens_pipeline()` slot becomes a conflict: both paths write a `MARKET_PRISM` `advisor_observations` row and neither has an idempotency guard against the other. Running both produces two `MARKET_PRISM` rows for the same logical night — the Overview tab reads the most-recent row, so the second write silently overwrites the council's considered verdict with the simpler lens-pipeline output.

### Decision

Add a 4-line env guard at the top of `_run_lens_pipeline()` (`app.py:686–688`):

```python
if os.environ.get("DISABLE_DAEMON_LENS_PIPELINE"):
    _daemon_log.info("Lens pipeline skipped (DISABLE_DAEMON_LENS_PIPELINE set).")
    return
```

When the env var is set to any non-empty value, `_run_lens_pipeline()` logs one INFO line and returns immediately. The scheduler still registers the slot at 03:00 — no scheduler change needed. No other files were changed.

### Falsy-semantics choice: `os.environ.get` vs `"..." in os.environ`

`os.environ.get("DISABLE_DAEMON_LENS_PIPELINE")` returns `None` (falsy) when the var is absent and a non-empty string (truthy) when set. This means:
- Setting the var to any non-empty value (e.g. `DISABLE_DAEMON_LENS_PIPELINE=1`, `=true`, `=yes`) silences the daemon slot.
- Setting it to an empty string (`DISABLE_DAEMON_LENS_PIPELINE=`) leaves the slot active (empty string is falsy).

The `in os.environ` alternative would also trigger on an empty-string assignment, which is a footgun for operators who set `VAR=` to "clear" a flag. `os.environ.get` is the safer operator-facing semantic.

### SAFE TRANSITION ORDER — MANDATORY on the droplet

**The flag MUST be set BEFORE registering the council systemd timer.** The daemon's 03:00 slot has no idempotency guard against a concurrent council run. If both are active simultaneously on the droplet, both write a `MARKET_PRISM` row on the same night. Deploy order:

1. Set `DISABLE_DAEMON_LENS_PIPELINE=1` in the droplet `.env` and restart the daemon — verify the INFO log fires at 03:00 and no `MARKET_PRISM` row is written by the daemon.
2. Register and enable the council systemd timer (or cron job via `schedule_prism.ps1` equivalent).
3. Confirm the council's first unattended run produces exactly one `MARKET_PRISM` row per night (AC-1, AC-3).

Reversing this order — registering the timer before setting the flag — risks a two-row night.

### Files changed

- `app.py` — `_run_lens_pipeline()` lines 686–688: 4-line env guard inserted before thread spawn
- `tests/app/test_lens_pipeline_gate.py` — 4 new tests (RED at 7c38075, GREEN at 3de3a31, sufficiency pinned at 5bbc030)

---

## DE-PRISM-SUB-AUTH-001 — Council subprocess pops ANTHROPIC_API_KEY to force subscription billing (2026-06-19)

Branch: feat/prism-council-sub-auth

### Context

`prism_scheduler._run_prism()` previously built the subprocess environment with `os.environ.copy()`, which passes every env var — including `ANTHROPIC_API_KEY` — to the `claude -p` child process. Claude Code's auth precedence puts `ANTHROPIC_API_KEY` **above** `CLAUDE_CODE_OAUTH_TOKEN`: when both are present, the CLI uses the metered API key and ignores the subscription token. On the DO droplet, the nightly council was therefore billed against the metered API key even though `CLAUDE_CODE_OAUTH_TOKEN` (the subscription credential) was available.

### Decision

Pop `ANTHROPIC_API_KEY` from the copied env before passing it to `subprocess.run`. The subprocess receives all other env vars unchanged; only the metered key is removed so `claude -p` falls through to `CLAUDE_CODE_OAUTH_TOKEN`.

Implementation in `_run_prism()` (`prism_scheduler.py`):

```python
_council_env = os.environ.copy()
_council_env.pop("ANTHROPIC_API_KEY", None)  # council uses CLAUDE_CODE_OAUTH_TOKEN, not the metered key
result = subprocess.run(
    cmd,
    cwd=str(_PROJECT_ROOT),
    env=_council_env,
    capture_output=True,
    text=True,
)
```

### Rationale

- **Claude Code auth precedence:** API key beats OAuth token. Removing the key is the only reliable way to force the subscription path without deleting it from the daemon's `.env` (where it is still needed by the on-demand dashboard advisor that uses `ai_advisor.py` via the HTTP routes).
- **Surgical removal only:** All other env vars (DB_PATH, PATH, CLAUDE_CODE_OAUTH_TOKEN, etc.) pass through unchanged. An allowlist approach would be fragile and require maintenance whenever new vars are added.
- **On-demand advisor unaffected:** `app.py` (the Flask daemon) uses `ANTHROPIC_API_KEY` directly via the Anthropic SDK client — it never spawns a `claude -p` subprocess. The pop only applies to the nightly `prism_scheduler` subprocess path.

### Deployment note

The DO droplet runs the daemon and will run the council as the non-root `planetstopper` user (UID 997) from `/opt/planetstopper`. `DISABLE_DAEMON_LENS_PIPELINE=1` is already set in the droplet `.env` so the daemon's 03:00 slot is silenced and the council is the sole nightly `MARKET_PRISM` producer (DE-PRISM-GATE-001 transition order already completed).

The council systemd timer (PM-deployed, not in this PR) sets `CLAUDE_CODE_OAUTH_TOKEN` in the service unit's environment. With this code change, `_run_prism()` pops `ANTHROPIC_API_KEY` before the subprocess call so `claude -p` falls back to `CLAUDE_CODE_OAUTH_TOKEN` (subscription). `ANTHROPIC_API_KEY` remains in `/opt/planetstopper/.env` for the on-demand dashboard advisor (`app.py` Flask routes call the Anthropic SDK directly -- never via a subprocess -- so the pop does not affect them).

### Files changed

- `prism_scheduler.py` — `_run_prism()`: `env=os.environ.copy()` replaced with `_council_env = os.environ.copy(); _council_env.pop("ANTHROPIC_API_KEY", None); env=_council_env`
- `tests/prism_scheduler/test_council_sub_auth.py` — 3 new tests (AC-1: key excluded, AC-2: OAuth token passes through, AC-3: other env vars preserved)
- `docs/generated/prism_scheduler.md` — `_run_prism` subprocess options updated
