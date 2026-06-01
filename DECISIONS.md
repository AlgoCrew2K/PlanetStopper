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
1. `X-CSRF-Token` header added to all 7 dashboard POST JavaScript files (including the sell_account panic button), resolving the 403 on chat POST.
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
