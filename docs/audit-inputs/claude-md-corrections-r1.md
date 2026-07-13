# Advisor-Remediation-R1 — CLAUDE.md Key-Files Corrections (DRAFT — NOT APPLIED)

**Author:** r1-doc | **Status:** DRAFT, FINALIZED for the code that has actually landed as of 2026-07-13 — for PM application only. This doc-writer never edits `.claude/CLAUDE.md` directly (project convention). One section (§7) is still PENDING, blocked on the r1-review Checkpoint-3 BLOCK finding not yet being lifted — do not apply §7 until that lands; §1–§6 have no remaining code dependency and are ready to apply now.
**Source:** `docs/audit-inputs/doc-reconciliation.md` §4 (the audit's original outline) + this doc-writer's own reconciliation as R1's ACs landed, verified against the current worktree source line-by-line (`DECISIONS.md` `DE-ADVISOR-R1-001`).
**Branch:** `fix/advisor-remediation-r1` · **Worktree:** `.claude/worktrees/advisor-r1`

---

## 1. `advisors/build_plan_generator.py` key-files row — REPLACE

**Target:** `.claude/CLAUDE.md` line 28 (as of this writing).

**Two independent corrections to the existing row:**

**(a) Opus → accessor-driven language (AC-16, landed `82479560`).** The row currently opens "Opus Build-Plan Generator" and describes "SDK structured tool-use generation" without naming the model accessor. Replace "Opus Build-Plan Generator" with "Build-Plan Generator (accessor-driven generation model — currently Fable via `model_config.get_advisor_suggestion_model()`, default `claude-fable-5`, overridable via `ADVISOR_SUGGESTION_MODEL`)". No other Opus-specific wording was found in this row's current body (verified by direct read).

**(b) Append the context-blindness caveat (doc-reconciliation §4, verified against live source — unaffected by any R1 AC, still accurate):**

> **Context-blindness caveat (advisor-intent audit, DE-ADVISOR-R1-001):** the generation prompt does NOT include the operator's live symphony tree, portfolio composition, backtest statistics, or any of the 5 market lens blocks — only the requested objective name, the DSL grammar, three static worked examples, and a 20-ticker sample of the tradeable universe. Strategy Builder proposes NEW strategies from scratch; it does not reason about the operator's EXISTING symphony. Closing this gap (context injection) is explicitly R2 scope, not R1.

**Verified against:** `_build_generation_prompt(objective, n_plans, membership)` signature — no symphony/portfolio/backtest/lens parameter exists (checked directly against the current worktree source).

---

## 2. `advisors/strategy_builder_engine.py` key-files row — APPEND (RESOLVED, was PENDING on AC-11/AC-12 — now landed)

**Target:** `.claude/CLAUDE.md` line 30.

**Append to the existing row** (the row is otherwise accurate — C1→C2→C3 pipeline, PBO/SPY-OOS gating, provenance tags all still correct as written):

> **Advisor-remediation-r1 additions (`DE-ADVISOR-R1-001`, 2026-07-13):** `rejection_reason` gains a 4th value, `oos_inferior_to_incumbent` (AC-7b, `3fa2e7f8`) — a genuine BHY/FDR winner that still loses to the incumbent on the OOS-superiority precondition, previously mislabeled `fdr_not_winner`; full precedence now `pbo_veto` > `below_spy_alpha` > `fdr_not_winner` (explicit) > `oos_inferior_to_incumbent` > `None` (survivor). `ProposalRun` gains `error_category: str | None` (AC-11, `59e86f9a`) alongside the existing sanitized `error` string. The route-level response (not this module) additionally surfaces `built_new_count`/`atlas_count` provenance rollup + a `mode_notice` degraded-run indicator (AC-11) and `screens_skipped`/`screens_skipped_reason` (AC-12, `_generate_candidate_trees` now calls `plan_tree_compiler.compile_plan(plan, backtest_fn=run_backtest)`, reviving the previously-dead tradeability-repair loop).

**Verified against:** `advisors/backtest_gate_engine.py`'s rejection_reason precedence table (already committed, [advisors_backtest_gate_engine.md](../generated/advisors_backtest_gate_engine.md)); `DECISIONS.md` `DE-ADVISOR-R1-001` §AC-7..9, §AC-11..12.

---

## 3. `advisors/backtest_gate_engine.py` key-files row — REPLACE (RESOLVED, was blocked on AC-4/AC-5/AC-17 — now landed)

**Target:** `.claude/CLAUDE.md` line 33.

**Two corrections:**

**(a) rejection_reason precedence list is stale** — currently reads `pbo_veto (Stage-1 dominant) → below_spy_alpha → fdr_not_winner → None (survivor)`, missing the 4th class. Replace with: `pbo_veto (Stage-1 dominant) → below_spy_alpha → fdr_not_winner (explicit BHY-winner-is-None) → oos_inferior_to_incumbent (a genuine FDR winner that still loses the OOS-superiority precondition, AC-7b) → None (survivor)`.

**(b) Append a Panel-Tie Neutralization note (AC-17, `3fa2e7f8`):**

> **Panel-tie neutralization (AC-17, 2026-07-13):** all three advisor engines (this module's only production consumers) constructed `BacktestCandidate` with structurally empty `candidate_params`/`incumbent_params` on every real call path, making the panel-comparison clause a constant 0.5-vs-0.75 that failed unconditionally — `ADOPT_CANDIDATE` was mathematically unreachable regardless of candidate quality. Fixed: when both sides are structurally empty, `cand_stability` ties `inc_stability` exactly, so adoption rests entirely on the OOS-superiority precondition plus the three hard vetoes. `acceptance_gate.py` and `autotuner.py` received zero diff — contained to this module. See [advisors_backtest_gate_engine.md](../generated/advisors_backtest_gate_engine.md)'s "Panel-Tie Neutralization" section for the full proof.

**Also verify while touching this row:** the "gate-strength parity" note is now accurate for Asset Swaps/Logic Changes too (see §4 below) — this row's own text does not currently claim otherwise, so no further edit needed here beyond (a)/(b).

---

## 4. General `advisors/` row (line 44, Asset Swaps / Logic Changes share this row today) — APPEND (RESOLVED, was PENDING on AC-4/AC-5 — now landed)

**Target:** `.claude/CLAUDE.md` line 44.

**Append:**

> **Gate-strength parity (AC-4/AC-5, `DE-ADVISOR-R1-001`, 2026-07-13):** `asset_swap_engine.py` and `logic_change_engine.py` now thread `dated_returns=` (into `BacktestCandidate` construction at the shared `_evaluate_single_variant` site) and a new `_spy_returns_fn_for(symphony_id)` helper (mirroring `strategy_builder_engine.py:807-826`) into every real `evaluate_candidate_batch` call. PBO veto and the real SPY-relative OOS baseline now fire for these two engines exactly as they already did for Strategy Builder — previously they gated on "beats a flat 0.0% return" with PBO structurally incapable of firing (the audit's F2 finding). The `_PBO_MIN_CONFIGS=2` guard is untouched, so PBO stays `None` at N=1 (the operator's single-candidate Evaluate buttons) by design.

---

## 5. `ai_advisor.py` key-files row — NO CHANGE (reviewed, confirmed accurate)

**Original doc-reconciliation §4 assessment:** "already accurate ('Claude-backed config advisor'). No change needed."

**This doc-writer's confirmation (2026-07-13):** verified the current row (`.claude/CLAUDE.md` line 25) directly — it contains no Opus-specific wording (only "Claude-backed config advisor," a family-level term that remains true regardless of which Claude-family model `ADVISOR_SUGGESTION_MODEL`/`ADVISOR_SYNTHESIS_MODEL` resolve to). AC-16 does not falsify anything in this row. No change.

---

## 6. Cross-reference line — APPEND (ready to apply now, unchanged from the original draft)

**Target:** `.claude/CLAUDE.md`, a new line under the Project Identity section or immediately above the Key Files table (PM's call on exact placement).

**Suggested text:**

> **Advisor reasoning-fidelity audit trail:** `docs/audit/ADVISOR-INTENT-AUDIT.md` (2026-07-13) is the canonical source for "what actually reasons vs what's deterministic" across the AI Advisor suite — six findings (F2/F3/F4/F5/F6/F7/F8) on attribution honesty, statistical-gate substance, and gate transparency, closed by `DE-ADVISOR-R1-001` (see `DECISIONS.md`). `docs/audit/CLOSEOUT-VERDICT.md` (2026-06-17) is a superseded, narrower structural-wiring pass — see its banner.

---

## 7. `static/ai_advisor.js` key-files row (line 46) — PENDING, blocked on the r1-review Checkpoint-3 BLOCK finding

**PENDING — do not apply yet.** The row currently documents `sbRunAnalysis()`'s DE-ADVISOR-SUITE-FIX-001 AC-1/AC-2 fix (in-place render, no navigate-away) but predates this cycle's field-consumption work. As of this writing, r1-fe has landed two commits addressing the r1-review Checkpoint-3 BLOCK finding (SB live-run render path consuming AC-7/AC-9/AC-11/AC-12 route-JSON fields; the SB route-JSON `rejection_reason` addition) and r1-test is running the final targeted-suite verification before handing back to r1-review. Once r1-review lifts the BLOCK, this doc-writer will append a note to this row (and finalize [static_ai_advisor_js.md](../generated/static_ai_advisor_js.md)'s `sbRunAnalysis()` section) describing the shipped field-consumption behavior, citing the landed commit SHA(s). Not drafted speculatively — will describe the SHIPPED code, not the in-flight diff.

---

## 8. Known Gotchas table — "AI Advisor empty suggestions" — NO CHANGE (reviewed, confirmed accurate; corrects an earlier over-broad draft in this same file)

**An earlier version of this section proposed correcting `.claude/CLAUDE.md:92`'s "AI Advisor empty suggestions" gotcha and [ai_advisor.md](../generated/ai_advisor.md):85 on the theory that AC-17's panel-tie fix falsified them. This doc-writer verified the call path directly (2026-07-13) and that theory is WRONG — retracted, not applied.**

**What AC-17 actually fixed:** `advisors/backtest_gate_engine.py`'s panel-tie neutralization, making `ADOPT_CANDIDATE` reachable for the 3 advisor-ENGINE evaluate routes (Strategy Builder / Asset Swaps / Logic Changes) — see §3 above and the new `GET /api/candidate-alert` note in [app.md](../generated/app.md) (the `new_valid_count` field, which WAS structurally stuck at 0 before this fix — that is the genuine, narrower narrative correction, already applied there).

**What CLAUDE.md:92 / ai_advisor.md:85 are actually about:** `ai_advisor.build_assessment_from_context`'s `oos_alpha=None` framing — driven entirely by `autotuner.py`'s own walk-forward BHY/Yekutieli haircut-select (`_haircut_select`), a completely separate code path. Verified: `autotuner.py:2710-2722` calls `acceptance_gate.evaluate_acceptance_gate` with hardcoded `candidate_stability_score=1.0, incumbent_stability_score=1.0` — an unconditional tie, never touched by AC-17 (whose fix lives entirely inside `backtest_gate_engine.py`, which `autotuner.py` never calls). Independently moot even if it were touched: `acceptance_gate.py:221-227` short-circuits to `REJECT_VETO_FAILED` before the panel-comparison clause is ever reached when `winner_trial_is_none=True` — exactly the `oos_alpha=None` case this gotcha describes, both before and after AC-17.

**Disposition:** `.claude/CLAUDE.md:92` and `docs/generated/ai_advisor.md:85` are accurate as currently written — NO CHANGE. `feature-plans/strategy-builder-real.completed.md:224` (which cites the CLAUDE.md:92 gotcha as its source) likewise needs no superseded banner, since the gotcha it cites is not changing.

---

## Application order (for the PM)

1. §1, §2, §3, §4, §5 (no-op), §6 can be applied now — no remaining code dependency, all verified against the current worktree source.
2. §7 is PENDING — will be replaced with concrete shipped-code text once r1-review lifts the Checkpoint-3 BLOCK.
3. §8 is a **retraction**, not a pending correction — do not apply any version of the "AI Advisor empty suggestions" gotcha correction from this file's earlier draft or from `docs/audit-inputs/doc-reconciliation.md`'s original outline; both were superseded by this doc-writer's own call-path verification.
