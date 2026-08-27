# Feature: Retirement Approval Polish (Cycle 2c — epic completion)
Status: ready
Created: 2026-08-27

## Summary
Cycle-2c completes the Retirement Recommender epic by closing the 7 non-blocking findings from PR #139's second independent `/code-review`. The recommender (2a) and approval lifecycle (2b) are shipped and live, but the operator-facing surface still renders opaque Composer hashes instead of symphony display names (in the card, the LLM explanation, and the checklist), the nightly 03:45 tick re-bills the LLM for a persistently-flagged pair with no dedup/cap, and there are several minor robustness/cleanup gaps. This cycle makes the feature genuinely usable and cost-safe — advisory-only, still NO exec/trade primitive, LLM still strictly OUT of the approve/reject/checklist action path.

## Acceptance Criteria
- [ ] **AC-1 (display name in card, F1):** the retirement recommendation card renders each symphony's resolved DISPLAY NAME (from `database.load_state()` / `bot_state[id]["name"]`) as the primary label, with the Composer hash available but secondary (e.g. tooltip/muted). Honest fallback to the raw hash when the name is unresolvable (symphony absent from `bot_state`). Resolution happens in `ai_advisor_tab()`'s existing prefetch (one `load_state()` per request, not per row).
- [ ] **AC-2 (display name in the persisted explanation, F1):** the 03:45 tick worker enriches each recommendation dict with `candidate_name`/`sibling_name` (resolved from `bot_state`) BEFORE calling `explain_recommendation`, so the fable-5 prompt is grounded in readable names and the persisted `raw_response.explanation` names the symphonies. Honest fallback to the hash in the prompt when a name is unresolvable. `retirement_recommender.py` stays byte-frozen (LLM-free); enrichment lives in the app-layer producer, not `build_recommendations`.
- [ ] **AC-3 (display name in checklist, F7):** the checklist block renders the `candidate_name` that `build_checklist` already resolves (currently computed and discarded) — no more dead lookup; honest fallback when unresolvable.
- [ ] **AC-4 (nightly-explain spend control, F2):** the 03:45 tick does NOT re-generate (re-LLM) + re-persist an explanation for a flagged pair whose recommendation is materially unchanged from the most recent prior explanation for that same pair — it reuses the prior explanation. Bounded, non-recurring metered spend for a persistently-flagged pair. A genuinely new/changed pair is still explained. Fail-open: if the reuse-lookup fails, fall back to generating (never silently drop the explanation).
- [ ] **AC-5 (decided_at semantic, F3):** an idempotent re-approve/re-reject PRESERVES the original `decided_at` (the first decision time) and only bumps `updated_at` — `decided_at` records the original decision, not the last write. UPSERT `ON CONFLICT` no longer re-stamps `decided_at`.
- [ ] **AC-6 (API decision live-join parity, F4):** `GET /api/retirement-recommendations` applies the same approval-status live-join the panel does, so each returned recommendation carries `approval_status` — programmatic consumers see decision state. Read-only, honest default (`"pending"`/absent) when no decision row exists.
- [ ] **AC-7 (JS button robustness, F6):** `retDispatchDecision` acts on the CLICKED element (event target / the button that fired), not a card-scan match on `candidate_id`, so the clicked button always disables and gives feedback even under a DOM/id edge case. Mirrors `frDispatchProposalAction`'s direct-element pattern.
- [ ] **AC-8 (dead accessor removal, F5):** the unused singular `database.get_retirement_decision` accessor is removed (the render/API paths use the plural batch `get_retirement_decisions`); its test is removed or repointed. No production caller exists (verify via grep before deleting).

## Architecture
- **`app.py`:** `_retirement_recommender_tick_worker` (~958) resolves names from `bot_state` and enriches each rec before `explain_recommendation` (AC-2) + the dedup/reuse check (AC-4). `ai_advisor_tab()` (~6180) resolves names in the existing prefetch for the card (AC-1). `_fetch_retirement_recommendations`/`api_retirement_recommendations` (~3872/3923) gain the approval-status live-join (AC-6, share the panel's join helper).
- **`templates/ai_advisor.html`:** card renders name-primary/hash-secondary (AC-1); checklist renders `candidate_name` (AC-3). All HTML-escaped, no `| safe`, design tokens only.
- **`static/ai_advisor.js`:** `retDispatchDecision` reworked to the clicked-element pattern (AC-7).
- **`database.py`:** migration is NOT needed (columns exist); `upsert_retirement_decision` `ON CONFLICT` preserves `decided_at` (AC-5); delete `get_retirement_decision` (AC-8).
- **`advisors/retirement_checklist.py`:** already returns `candidate_name` — no change unless the fallback needs hardening (AC-3 is template-side).
- **`advisors/retirement_recommender.py`:** BYTE-FROZEN (LLM-free, AC-2 constraint). Guard with the existing byte-unchanged golden test (now LF-normalized).

## Edge Cases
- Symphony flagged but absent from live `bot_state` (renamed/removed since the night it was flagged) → honest hash fallback everywhere (card, explanation prompt, checklist), never a crash or blank.
- `bot_state` load failure during the tick or render → degrade to hashes, never abort the rec.
- A pair flagged for the first time tonight → explained fresh (AC-4 reuse must not suppress a genuinely new pair).
- Re-approve after a reject (status transition) → `decided_at` semantic: preserve the FIRST decision time or the first time this status was set? Ruling: preserve the original row's `decided_at` (first-ever decision on this candidate); `updated_at` tracks the latest change.
- API consumer with zero decisions → every rec shows `approval_status: "pending"` (or the honest default), not missing/erroring.

## Security Considerations
- Still advisory-only: NO exec/trade/liquidation primitive added anywhere; the no-trade-boundary transitive-AST test (`tests/security/test_retirement_action_no_trade_boundary.py`) must stay green and cover any new call paths (the tick enrichment must not reach an exec seam).
- LLM stays OUT of the approve/reject/checklist action path (AC-4/AC-7 touch producer + display only).
- All new display fields (names) HTML-escaped (XSS) — names come from `bot_state`, still escape.
- Spend control (AC-4) must fail-open (never suppress a legitimate explanation) but also never loop/duplicate metered calls.

## Testing Strategy
- **Unit/route (quant-test-writer, RED-first):** name-resolution + honest hash fallback (AC-1/2/3); tick reuse-vs-regenerate logic incl. new-pair-still-explained + fail-open (AC-4); `decided_at` preserved on re-approve + `updated_at` bumped (AC-5); `/api/retirement-recommendations` carries `approval_status` (AC-6); dead-accessor removed / no prod caller (AC-8).
- **Template render:** card shows name (not bare hash) + hash-secondary; checklist shows name; XSS-escaped; empty-state intact.
- **JS:** `retDispatchDecision` clicked-element behavior via the repo's JS-source-assertion pattern + `node --check`.
- **Safety:** the no-trade-boundary suite stays green over the new call paths.
- **Byte-frozen guard:** `retirement_recommender.py` golden-hash (LF-normalized) stays green.
- **Full-tree CI (`-n2 --dist loadfile`) on the exact SHA is the authoritative gate** — local `-n0` cannot catch loadfile leaks.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Names resolved at render time (card/checklist) AND produce time (explanation) | The explanation is LLM-generated + persisted at 03:45, so the prompt needs names then; the card/checklist render live, so resolve from current `bot_state`. |
| `retirement_recommender.py` stays byte-frozen; enrichment in the app producer | Preserves the LLM-free 2a-core invariant (AC-2 guard); the golden-hash test enforces it. |
| AC-4 reuse keyed on (candidate_id, sibling_id) + material-change check | Bill-protection for a persistently-flagged pair without suppressing genuinely new/changed recommendations. |
| No new migration | `retirement_decisions` columns already exist; AC-5 is an UPSERT-clause change only. |

## Scope Boundaries
- **IN:** the 7 second-`/code-review` findings (AC-1..AC-8), all advisory/display/producer/cleanup.
- **OUT:** any exec/trade/liquidation primitive (permanently out); changing the strict recommender gates or the retention floor (operational, operator's knob); the `retirement_recommender.py` 2a math core (byte-frozen); reworking the approve-only-while-pending design (AC-7-pinned in 2b); frontrunner proposals / legacy Composer draft renames (separate, operator-only).
