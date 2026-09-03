# Feature: Retirement Approval Polish 2d (fast-follow)
Status: ready
Created: 2026-08-28

## Summary
Closes the 7 findings from PR #140's second `/code-review` pass. All are fail-safe + currently latent (the droplet produces ~0 recommendations today — a shadow_history retention artifact, not a code defect, so none of these manifest yet). Advisory/display/producer/cleanup only — **no exec/trade/liquidation primitive, LLM stays out of the approve/reject/checklist action path, `advisors/retirement_recommender.py` byte-frozen, and the no-trade-boundary AST scan (Group E parent `_fetch_retirement_recommendations` entry point + Group F) stays green over every change.**

> **[Framing correction, `DE-PERF-WINDOW-TRUTH-001`, 2026-09-03]** "a shadow_history retention artifact" below and in the Scope Boundaries bullet is superseded — the `PERF-WINDOW-TRUTH-2026-09-03.md` audit independently verified `SHADOW_HISTORY_RETENTION_DAYS` (default 180d) is not binding (prune cutoff ~2026-03-07, well before the 2026-06-22 go-live floor). The real cause is the production **go-live floor**, not retention pruning — "extend retention" would not have added any recommendations. Row-count estimates here were approximately right; only the causal attribution was wrong. See `DECISIONS.md`'s `DE-PERF-WINDOW-TRUTH-001` entry, "Corrected framing" section, for the full account. This plan's own AC-1..AC-6 scope is unaffected by the correction — left as-shipped below, not rewritten.

## Acceptance Criteria
- [ ] **AC-1 (name-display fallback, finding 1 — SELF-INTRODUCED in 2c):** `_refresh_retirement_display_names` must NOT overwrite a rec's display name with the raw hash when the symphony has left `bot_state`. Fallback chain: **freshly-resolved name → the persisted tick-time `candidate_name`/`sibling_name` → the raw hash** (only as last resort). Preserves the known display name for a retired/removed symphony while still resolving fresh on a rename. The reuse gate's persisted-blob comparison (a separate read path) is unaffected.
- [ ] **AC-2 (reuse honesty/spend consistency, findings 2+3):** eliminate the within-rounding-tolerance stale citation AND restore reuse for stable pairs, by making the precision the explanation CITES match the precision the reuse gate COMPARES. Recommended mechanism (team to pin the exact contract): in the tick worker, round the rec's numeric evidence to `_MATERIAL_CHANGE_ROUND_NDIGITS` (2dp, the existing 2c constant) BEFORE `explain_recommendation` sees it AND before/at the snapshot comparison, so (a) the explanation can never cite a value more precise than the gate checks (closes finding 3's ≤0.005 stale window), and (b) reuse fires whenever the 2dp evidence is unchanged night-to-night (restores finding 2's spend savings for a stable pair). Invariant: **a reused explanation never cites a number that differs from the current (rounded) evidence.** A genuinely-changed pair still regenerates (honest). Fail-open preserved.
- [ ] **AC-3 (decided_at pending invariant, finding 4):** `upsert_retirement_decision` must hold `pending ⇒ decided_at IS NULL` — a write transitioning an already-decided row back to `approval_status='pending'` resets `decided_at` to NULL (while approved/rejected still preserve the original decided_at per 2c AC-5). `updated_at` unchanged. No migration.
- [ ] **AC-4 (load_state dedup on panel path, finding 5):** on the AI Advisor panel render with an approved card, `database.load_state()` must run at most once — thread `_refresh_retirement_display_names`'s bot_state need onto the SAME shared `_ensure_ai_advisor_bot_state()` lazy closure the checklist/frontrunner blocks use, instead of a second independent `load_state()`.
- [ ] **AC-5 (empty-state early return, finding 6):** `_join_retirement_approval_status` must NOT issue `database.get_retirement_decisions()` when `recs` is empty — early-return on `[]` (the honest-empty-state common case) before the DB read, mirroring `_refresh_retirement_display_names`'s own empty skip.
- [ ] **AC-6 (API docstring accuracy, finding 7):** `api_retirement_recommendations`'s docstring 'Response shape' must document the three keys the route actually returns now — `approval_status`, `candidate_name`, `sibling_name` — noting `candidate_name`/`sibling_name` are fresh request-time overlays, not part of the persisted `raw_response` schema.

## Architecture
- `app.py`: `_refresh_retirement_display_names` (AC-1 fallback, AC-4 shared closure), `_retirement_recommender_tick_worker` + the reuse snapshot (AC-2 rounding), `_join_retirement_approval_status` (AC-5 early-return), `api_retirement_recommendations` docstring (AC-6).
- `database.py`: `upsert_retirement_decision` ON CONFLICT decided_at CASE gains a `pending`-resets-to-NULL branch (AC-3). No migration.
- Byte-frozen: `retirement_recommender.py` / `retirement_explainer.py` / `retirement_checklist.py`.

## Edge Cases
- AC-1: symphony in bot_state (fresh wins) / renamed (fresh wins) / removed-from-bot_state (persisted name preserved, not hash) / never-had-a-name (hash, honest).
- AC-2: 2dp-stable pair → reuse; any 2dp field drifts → regenerate; the explanation's cited numbers always equal the rounded persisted evidence (card + API + prose all consistent). Confirm the card display is unaffected/consistent (it already renders 2dp).
- AC-3: pending→approved (stamp), approved→approved (preserve), approved→pending (reset to NULL), brand-new pending (NULL). 
- AC-5: empty recs (no query) vs non-empty (query as before).

## Security Considerations
- No exec/trade primitive added; LLM stays out of the action path (all changes are producer/display/DB-cleanup). The no-trade-boundary AST scan (Group E `_fetch_retirement_recommendations` parent entry point covers `_refresh_retirement_display_names` + `_join_retirement_approval_status`) must stay green + non-vacuous over the refactors — mutation-test if any call graph shifts.
- Display fields stay `| e`-escaped (no `| safe`).

## Testing Strategy
- RED-first (quant-test-writer): AC-1 fallback matrix (4 cases incl. removed-symphony preserves persisted name); AC-2 explanation-precision == gate-precision + reuse-on-2dp-stable + regenerate-on-drift + a card/API/prose consistency assertion; AC-3 the 4 decided_at transitions incl. the new pending-reset; AC-4 a differential load_state call-count on the approved-card panel path (== the frontrunner-only baseline, not +1); AC-5 empty-recs makes zero get_retirement_decisions calls; AC-6 docstring lists the 3 keys.
- Non-regression: the full retirement suite stays green; the byte-frozen golden guard + the no-trade-boundary scan stay green.
- Full-tree CI (`-n2 --dist loadfile`) on the exact SHA is the authoritative gate.

## Decisions
| Decision | Rationale |
|----------|-----------|
| AC-2 rounds the rec evidence to the same 2dp the reuse gate uses | The only way to guarantee a reused explanation never cites a number the gate didn't verify is to make citation-precision == comparison-precision; 2dp matches the card's existing display precision, so no visible regression. |
| AC-1 falls back to the persisted name, not straight to the hash | The tick already resolved + persisted the real name; discarding it for a hash when the symphony later leaves bot_state loses known information. |
| No schema migration | AC-3 is an UPSERT-clause change; all columns exist. |

## Scope Boundaries
- **IN:** the 7 PR#140 2nd-`/code-review` findings (AC-1..AC-6).
- **OUT:** any exec/trade primitive (permanently); the `retirement_recommender.py` math core (byte-frozen); the recommender's gates/thresholds; **shadow_history retention** (originally attributed here to "~49-day retention starving the conservative gates" — see the framing-correction note above the Summary: the real cause is the go-live floor, not a retention knob, so raising `SHADOW_HISTORY_RETENTION_DAYS` was never the fix; still correctly OUT of this cycle's scope either way, NOT a code change here).
