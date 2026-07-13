# Advisor-Remediation-R1 — CLAUDE.md Key-Files Corrections (DRAFT — NOT APPLIED)

**Author:** r1-doc | **Status:** DRAFT, IN PROGRESS — for PM application only. This doc-writer never edits `.claude/CLAUDE.md` directly (project convention). Sections marked `PENDING` are blocked on an R1 AC that has not landed yet and are intentionally left unfilled rather than guessed — do not apply this file until every section below reads a concrete correction, or apply only the non-PENDING sections and revisit the rest at cycle-complete.
**Source:** `docs/audit-inputs/doc-reconciliation.md` §4 (the audit's original outline) + this doc-writer's own reconciliation as R1's ACs landed (`DECISIONS.md` `DE-ADVISOR-R1-001`).
**Branch:** `fix/advisor-remediation-r1` · **Worktree:** `.claude/worktrees/advisor-r1`

---

## 1. `advisors/build_plan_generator.py` key-files row — APPEND (ready to apply now)

**Target:** `.claude/CLAUDE.md`, the `advisors/build_plan_generator.py` row in the Key Files table.

**Append to the existing row text** (do not replace — the existing row is accurate on mechanism, this only adds a completeness caveat per doc-reconciliation §4):

> **Context-blindness caveat (advisor-intent audit, DE-ADVISOR-R1-001):** the generation prompt does NOT include the operator's live symphony tree, portfolio composition, backtest statistics, or any of the 5 market lens blocks — only the requested objective name, the DSL grammar, three static worked examples, and a 20-ticker sample of the tradeable universe. Strategy Builder proposes NEW strategies from scratch; it does not reason about the operator's EXISTING symphony. Closing this gap (context injection) is explicitly R2 scope, not R1.

**Verified against:** `_build_generation_prompt(objective, n_plans, membership)` signature — no symphony/portfolio/backtest/lens parameter exists (checked directly against the current worktree source, not re-derived from the audit's citation alone).

**Caveat for the PM (do not apply the "Opus" framing below unmodified — see §4):** this row currently opens "Opus Build-Plan Generator" — see §4 below on the AC-16 model-routing risk to that specific word before finalizing this row.

---

## 2. `advisors/strategy_builder_engine.py` key-files row — REPLACE (PENDING, blocked on AC-11/AC-12)

**PENDING.** doc-reconciliation §4 asked for an append noting the silent-degradation gap (6 D-1 failure branches in `build_plan_generator` can reduce a run to Atlas-only with `run.error=None`, no run-level indicator; `live_returns=[]` hardcoded on the route skipping drawdown/Pearson screens; the tradeability-repair loop dead at `sbe.py:379` for lack of a `backtest_fn`). R1's AC-11/AC-12 are the code fixes for exactly this gap (run-level mode banner, `backtest_fn` wiring, `live_returns` population or explicit skip-indicator). Appending the PRE-fix language would misdescribe the row the moment AC-11/12 ship. Once they land, this doc-writer will REPLACE (not append to) the existing silent-degradation note with the honest post-fix state, citing the landed diff's file:line.

---

## 3. General `advisors/` row (Asset Swaps / Logic Changes share this row today) — PENDING, blocked on AC-4/AC-5

**PENDING.** doc-reconciliation §4 suggested that if these engines ever get individual CLAUDE.md rows, they should state "PBO veto structurally cannot fire; gates on beats-a-flat-0.0%-return, not SPY-relative." R1's AC-4/AC-5 are the fixes that make this statement FALSE the moment they ship (both engines gain `dated_returns=`/`spy_returns_fn=` wiring mirroring Strategy Builder). Once AC-4/5 land, this doc-writer will add a line to the general `advisors/` row (or a new dedicated row, if the PM wants one split out) noting PBO veto + SPY-OOS baseline now fire for these two engines too, closing the gap — never the stale "structurally cannot fire" framing.

---

## 4. `ai_advisor.py` key-files row — PENDING, blocked on AC-16 (new scope added mid-cycle)

**Original doc-reconciliation §4 assessment:** "already accurate ('Claude-backed config advisor'). No change needed."

**Superseding note (this doc-writer, 2026-07-13):** `feature-plans/advisor-remediation-r1.md` gained AC-16 after this cycle started (operator directive: route suggestion-producing LLM calls — `ai_advisor.request_suggestions` and `build_plan_generator`'s generation call — to Fable via an env-overridable accessor, default `claude-fable-5`). Fable is still a Claude-family model, so "Claude-backed" survives as accurate. But if this row (or the `build_plan_generator.py` row in §1 above) names **Opus** specifically anywhere, that becomes stale once AC-16 ships. **PENDING** until AC-16 lands: this doc-writer will sweep both rows for any Opus-specific (not just Claude-specific) wording and switch it to accessor-driven/model-neutral phrasing ("via `ADVISOR_SUGGESTION_MODEL`, default Fable"), per AC-16's own "ATTRIBUTION COHERENCE" requirement (never a hardcoded model name in copy).

---

## 5. Cross-reference line — APPEND (ready to apply now)

**Target:** `.claude/CLAUDE.md`, a new line under the Project Identity section or immediately above the Key Files table (PM's call on exact placement).

**Suggested text** (per doc-reconciliation §4's "General" recommendation):

> **Advisor reasoning-fidelity audit trail:** `docs/audit/ADVISOR-INTENT-AUDIT.md` (2026-07-13) is the canonical source for "what actually reasons vs what's deterministic" across the AI Advisor suite — six findings (F2/F3/F4/F5/F6/F7/F8) on attribution honesty, statistical-gate substance, and gate transparency, closed by `DE-ADVISOR-R1-001` (see `DECISIONS.md`). `docs/audit/CLOSEOUT-VERDICT.md` (2026-06-17) is a superseded, narrower structural-wiring pass — see its banner.

---

## 6. Known Gotchas table — "AI Advisor empty suggestions" narrative correction — PENDING, blocked on AC-17

**PENDING.** AC-17 (added mid-cycle 2026-07-13, plan @ ad9b1629, [PM-ASSUMED] — operator may overrule) proved `ADOPT_CANDIDATE` was mathematically unreachable on every advisor engine's real production call path (constant 0.5-vs-0.75 panel comparison from structurally empty params + hardcoded incumbent stability — see `DECISIONS.md` `DE-ADVISOR-R1-001` §AC-17 for the full proof + fix). This falsifies the CURRENT Known Gotchas table entry as a COMPLETE explanation:

**Current text (`.claude/CLAUDE.md`, Known Gotchas table):**
> AI Advisor empty suggestions (most symphonies) | Expected. The CRRA-EU + Harvey-Liu FDR gate is intentionally strict. `build_assessment_from_context` explains why — `oos_alpha=None` means all trials were haircut-rejected, not an error.

**Defect:** gate strictness is real and remains true, but it was NOT the dominant cause of the all-zero survivor history — a structural bug made `ADOPT_CANDIDATE` unreachable regardless of how strong any candidate's performance was. This entry told workers (and, transitively, the operator) that empty suggestions were solely a strictness artifact, when the primary cause was a defect now fixed by AC-17.

**Corrected direction (drafted once AC-17's implementation is confirmed GREEN — NOT drafted yet, this doc-writer will not guess the post-fix behavior ahead of the landed diff):** the corrected entry needs to (a) note that `ADOPT_CANDIDATE` is now reachable (post-AC-17) where it previously was not, (b) retain the true and still-relevant strictness/FDR framing as a SECONDARY factor, not the sole explanation, and (c) avoid overcorrecting into implying survivors are now common — the gate is still genuinely strict, just no longer structurally impossible to pass.

**Also flagged (see `DECISIONS.md` `DE-ADVISOR-R1-001` §AC-17's doc-tree sweep for the full inventory, not duplicated here):** `docs/generated/ai_advisor.md:85` carries the identical "intentionally strict" framing (this doc-writer's own lane, corrected directly, not via this CLAUDE.md draft file) and `feature-plans/strategy-builder-real.completed.md:224` cites this CLAUDE.md gotcha as its source (gets a superseded pointer note once this entry is corrected, not a rewrite).

---

## Application order (for the PM)

1. §1 and §5 can be applied now — no code dependency, already verified against live source.
2. §2, §3, §4, §6 are PENDING — this doc-writer will replace their `PENDING` markers with concrete corrected text as AC-11/12, AC-4/5, AC-16, and AC-17 respectively land on `fix/advisor-remediation-r1`, and will notify the PM when the full file has no `PENDING` markers left.
3. Do not apply §2/§3/§4/§6 from the doc-reconciliation.md original text or this file's PENDING placeholders (superseded — see the divergence note in `DECISIONS.md` `DE-ADVISOR-R1-001` §AC-15/§AC-17) — those would document the pre-R1 broken state R1 is fixing.
