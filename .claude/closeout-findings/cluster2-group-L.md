# Cluster 2 — Group L: Community Strategies + Proposal/Gate Infra (F35–F37)
Auditor: closeout-audit-suite
Date: 2026-06-17
Evidence standard: file:line + runnable result per finding

---

## F35 — HF-1: community-strategies HOLLOW in production (FINDING)

**FINDING — OPERATOR-GATED (AC-17)**

### (a) Engine layer is fully built — PASS

Static cite `advisors/strategy_builder_engine.py`:
- `:195`: `def community_candidate_infos(community_result: dict, *, max_candidates: int = MAX_COMMUNITY_CANDIDATES_PER_RUN) -> list[CandidateInfo]:`
- `:864`: `community_candidates: list[CandidateInfo] | None = None` (kwarg on `propose_strategies`)
- `:921-922`: `if community_candidates: candidate_infos.extend(community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN])`
- `:44`: `MAX_COMMUNITY_CANDIDATES_PER_RUN: int = 20`

Static cite `advisors/community_strats.py`:
- `:98`: `def load_community_strategies(*, limit, min_oos_sharpe, client, force_refresh) -> dict:`
- `:156`: `atlas_cache.cached_pull(...)` — weekly-TTL cache routes Atlas reads (operator bill-protection directive)
- `:63`: `_composition_hash` via `_strip_ids → json.dumps(sort_keys=True) → sha256` — structural-hash dedup (NOT `database.compute_composition_hash`)
- Engine layer is fully built, validated, and tested. It is reachable from direct Python import and from tests.

### (b) PRODUCTION ROUTE has NO community wiring — CONFIRMED HOLLOW

**Runnable result**:
```
$ grep -c "community_candidates|load_community_strategies|community_candidate_infos" app.py
0
```
Result: **0** — confirmed via Bash probe (pre-flight) and re-confirmed via `src.count('community_candidates')` Python probe.

Static cite `app.py:3437-3443`:
```python
run = propose_strategies(
    objective=objective,
    universe=universe,
    screen_config=ScreenConfig(),
    live_returns=[],
    symphony_id=symphony_id,
)
```
NO `community_candidates` argument. Nothing in `app.py` calls `load_community_strategies` or `community_candidate_infos`. The Strategy Builder route runs **template-only in production** (T1–T7 only).

### (c) Doc contradiction — stale claim in CLAUDE.md

**FINDING (AC-17/AC-18)**:
- **CLAUDE.md `community_strats.py` row**: "first production caller: `propose_strategies` via the `community_candidate_infos` adapter (injected at the route boundary)" — **FALSE**. No route injection exists.
- **DECISIONS.md:633**: "No production caller yet... must not be called from production routes until that wiring is in" — **TRUE**.
- **DECISIONS.md:651**: "the caller owns the Atlas fetch and passes the adapted output" — **implies a caller that does not exist** at the route boundary.

This contradiction must be adjudicated with the operator (AC-17):
- **Option A (build gap)**: Route injection is intended — schedule a dedicated TDD cycle to add it. CLAUDE.md claim is a forward-dated doc that got ahead of the code.
- **Option B (deferred-by-design)**: Route injection is not in current scope — correct CLAUDE.md to "no production route caller; community_strats is available for future use" and reconcile DECISIONS.md:651.

**Adjudication brief for closeout-doc**: Update CLAUDE.md `community_strats.py` row from "first production caller: propose_strategies via ... (injected at the route boundary)" to "NO production route caller currently exists; the engine layer (strategy_builder_engine.community_candidate_infos + propose_strategies kwarg) is fully built and available for a future wiring cycle. DECISIONS.md:633 is the accurate status." Remove or correct DECISIONS.md:651's implication of an existing caller. This correction is deferred to closeout-doc after operator adjudicates Option A vs B.

**[ASSUMPTION-L-1]**: operator leans BUILD per PM-ACTIVE-WORK notes — but this audit does NOT build the wiring. The adjudication brief is delivered here; the decision is the operator's.

---

## F36 — BHY-FDR gate across FULL batch (anti-overfit invariant)

**PASS**

Static cite `advisors/backtest_gate_engine.py`:
- `:462-567`: `def evaluate_candidate_batch(candidates, ...)`:
  - `:476-480` (docstring): "The FDR correction is applied across the FULL batch of N candidates: `n_effective = len(candidates)` is the honest multiple-testing count".
  - `:519`: `n = len(candidates)` — gate input N = total candidate count, not a pre-filtered subset.
  - `:549-563`: BHY/Yekutieli correction applied over `n_effective = n` (full batch), then `p_adj = p_adj_all[:n]`.
- `advisors/strategy_builder_engine.py:964-969`: `gate_batch = evaluate_candidate_batch(bt_candidates, ...)` — receives `bt_candidates` (all successfully-backtested candidates), NOT post-screened.
- `:971-980` (Step 4): screens applied to `gate_batch.survivors` ONLY — after the gate, not before.

**Anti-overfit invariant confirmed**: screens never shrink the gate input (`:871-875` docstring: "Screens apply only to gate survivors (post-gate presentation filter)"). Raising N raises the bar each candidate must clear — the FDR correction penalizes multiple testing correctly.

**Runnable result**: `advisors/backtest_gate_engine.py:73` imports `from acceptance_gate import evaluate_acceptance_gate, AcceptanceVerdict` — same module used by the autotuner. Module identity confirmed (F37).

---

## F37 — acceptance_gate shared between autotuner + advisor suite

**PASS**

Runnable result (direct Python import):
```
F37 acceptance_gate module id (direct): 2429880657264
F37 acceptance_gate module id (via backtest_gate_engine): 2429880657264
F37 Same module object: True -> PASS
```

Static cite:
- `autotuner.py:14`: `import acceptance_gate as _acceptance_gate`
- `autotuner.py:2685`: `_gate_verdict = _acceptance_gate.evaluate_acceptance_gate(...)`
- `advisors/backtest_gate_engine.py:73`: `from acceptance_gate import evaluate_acceptance_gate, AcceptanceVerdict`
- `advisors/asset_swap_engine.py:47`, `advisors/logic_change_engine.py:62`, `advisors/strategy_builder_engine.py:22-28`: all import `evaluate_candidate_batch` from `backtest_gate_engine`, which itself re-exports `evaluate_acceptance_gate` from `acceptance_gate`.

**One shared contract**: the autotuner uses `evaluate_acceptance_gate` directly; the advisor suite uses `evaluate_candidate_batch` (which wraps the same gate via `backtest_gate_engine.py:73`). Same `acceptance_gate` module, no divergent duplicate implementation.

---

## Summary — Group L

| Feature | Status | Confidence |
|---------|--------|------------|
| F35 community-strats engine layer built | PASS | HIGH |
| F35 HF-1 production route HOLLOW | FINDING — OPERATOR-GATED | HIGH (grep=0 confirmed) |
| F35 CLAUDE.md doc contradiction (AC-17/AC-18) | FINDING | HIGH |
| F36 BHY-FDR gate across FULL batch | PASS | HIGH |
| F37 acceptance_gate shared module | PASS | HIGH (runnable) |

**OPERATOR-GATED (AC-17)**: HF-1 adjudication brief delivered. The closeout does NOT build the route injection. The operator decides build-gap vs deferred-by-design; closeout-doc lands the doc correction after that decision.

**Doc-accuracy findings for closeout-doc (AC-18)**:
1. CLAUDE.md `community_strats.py` row: "injected at the route boundary" is FALSE → correct to "no production route caller".
2. DECISIONS.md:651: implies an existing caller → reconcile to match DECISIONS.md:633 ("no production caller yet").
