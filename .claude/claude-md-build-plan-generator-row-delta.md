# CLAUDE.md key-files row delta — advisors/build_plan_generator.py

**FOR PM:** Draft key-files row update for CLAUDE.md. Apply during the cycle-end CLAUDE.md pass.
**Do NOT apply this file directly — it is a draft for PM review.**

---

## Proposed update to the `advisors/build_plan_generator.py` row in CLAUDE.md key-files table

The current row does not exist yet (build_plan_generator.py is a new module this cycle).
Add the following row to the Key Files table:

```
| `advisors/build_plan_generator.py` | Opus Build-Plan Generator (Strategy Builder Component 2+2b): SDK tool-use generation of `N_PLANS_PER_OBJECTIVE=12` diverse objective-shaped build-plans in the build-plan DSL; `MAX_OUTPUT_TOKENS=16384` (raised from 4096 — DE-SB-GEN-TRUNCATION; empirical worst-case 5,015 tokens/12-plan run; 16384 ≈ 3.3x headroom); `MAX_GENERATION_ATTEMPTS=3` bounded retry fires on `stop_reason=="max_tokens"`, degrades D-1 on exhaustion; `_build_generation_prompt` seam (5 layers: DSL grammar + flat if.condition + compound if_compound union + THREE compiler-verified examples + `_OBJECTIVE_SIGNATURES`); full Composer condition grammar (binary/binary_compound/compound) generation-reachable and schema-constrained; tightened `_EMIT_BUILD_PLANS_TOOL` (kind/scheme/condition-union enum-constrained); AC-8 via `plan_matches_objective()`; AC-9 membership prune + zero-ticker guard; AC-12 objective-matched Atlas admission with `admit_community_candidates` (cut_drawdown=lowest drawdown, vol_mit=lowest vol, lift_ra=best sharpe, diversify=low Jaccard overlap); AC-13 explicit provenance tags (built-new/atlas-suggested); D-1 never-raises; off-execution-path; advisory-only |
```

---

## Reconciliation notes for PM (stale claims in existing CLAUDE.md)

The following claim in the existing CLAUDE.md key-files table is STALE and should be corrected in the cycle-end pass:

**Stale claim (in `advisors/strategy_builder_engine.py` row):**
> "Called post-walk-forward from autotuner.py"

**Correct statement:**
`propose_strategies` is NOT called from `autotuner.py`. The sole production callers are `app.py:3816` (the on-demand Strategy Builder route) and `advisors/strategy_builder_scheduler.py` (the weekly scheduler). This was an early doc claim that predated the production wiring; it was flagged and corrected in the C4 doc pass (see DE-SB-C4-001 in DECISIONS.md, section "Design decisions").

**Recommended replacement text for that row (just the caller clause):**
Replace "Called post-walk-forward from autotuner.py" with:
> sole production callers: `app.py:3816` (route) + `strategy_builder_scheduler.py` (weekly); `autotuner.py` does NOT call `propose_strategies`

---

## Additional stale claims to sweep (found during doc reconciliation pass)

None found in the docs/generated/ tree that are not already covered by the cycle's updates. The INDEX.md `advisors/build_plan_generator` entry has been updated in this cycle (DE-SB-GEN-TRUNCATION markers filled, 2a1787e).
