# Feature: Priority Resolver Extension — Add CVaR Co-Signal as 5th Signal

**Phase / Lane:** Phase 2 — Finalist B, **evidence-gated**. Scaffold now; ships only if the four Phase-2 preconditions pass.
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED) + `quant-code-reviewer` (review). Trio sufficient — small surgical change on a pure function with a strict invariant set.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §5.2 (CVaR enters as a co-signal — narrows / vetoes / confirms, **never solely fires**), §5.3 (*"the CVaR co-signal enters the **existing** `resolve_trigger_priority` as one additional boolean — the resolver is **kept and extended, never replaced, never collapsed into a single condition**. The 6 heuristics are retained as a **permanent safety floor**."*), §5.6 R-1 (system after Phase 2 is MORE complex — 6 heuristics + a CVaR resolver signal).
- `docs/handoff/council-attack-rubric.md` Family **G** (G-3 the 6 incumbent layers' fate is explicit — **retained as permanent safety floor**; the deterministic priority resolver and its named order `_TRIGGER_PRIORITY_ORDER` must have a stated successor; baseline §2.2: layers do NOT conflict today — `_TRIGGER_PRIORITY_ORDER` is a total deterministic order, the architecture's safety mechanism).
- Code anchors:
  - `math_engine.py:659-666` (`_TRIGGER_PRIORITY_ORDER` — the canonical priority list: `["VWAP Breakdown", "Take-Profit", "VWAP Bleed Cut", "Trailing Stop"]`).
  - `math_engine.py:669-692` (`resolve_trigger_priority` — the function being extended). Pure function; returns `(winner: str|None, co_fired: list[str])`.
  - `phase-2/cvar-cosignal-hysteresis-trigger/plan.md` (sibling — produces the `is_cosignal_active` boolean this resolver consumes).

## Why (problem statement)

A CVaR co-signal is, by §5.2 definition, a signal that **narrows, vetoes, or confirms** an exit another layer already supports, but **never solely fires** one. The composition mechanism is the existing `resolve_trigger_priority`:

- **Kept and extended** — the resolver is the architecture's safety mechanism (a single total deterministic order, no conflict among layers).
- **Never replaced** — replacing the resolver with a different composition body would mean inventing a second arbiter; critic's spine is explicit on no-new-arbiter.
- **Never collapsed into a single condition** — the layered structure IS the safety mechanism (project risk-engine charter anti-pattern: never simplify the layered exit logic into a single condition).

The co-signal is the 5th input. Its semantics — *confirms / vetoes / narrows but never solely fires* — translate to a placement decision: where in the priority order does the co-signal sit?

**The answer is structural, not heuristic:**

- The co-signal cannot be the winner when it is the ONLY active signal. If `is_cosignal_active=True` and all 4 incumbent booleans are `False`, the resolver must return `(None, [])` — there is no exit. This is the structural enforcement of "never solely fires."
- When at least one incumbent signal is active, the co-signal **co-fires** — it appears in the `co_fired` list, never as the winner. The winner is still the highest-priority incumbent. The co-signal's presence in `co_fired` is the *audit trail* that the CVaR layer confirmed the exit.
- The co-signal's veto / narrow semantics are NOT in the resolver — those belong upstream (in the cosignal state machine, which can choose to set `is_cosignal_active=False` when its CVaR assessment says the tail risk is moving away, providing implicit veto). The resolver only sees the boolean.

## Deliverables

### Code

#### Extend `resolve_trigger_priority`

- **`math_engine.py:669-692`** — new signature (additive 5th argument, default-False for back-compat with any caller that does not yet pass it; Phase-1 and Phase-1.5 callers untouched):
  ```python
  def resolve_trigger_priority(
      is_vwap_broken: bool,
      is_tp_hit: bool,
      is_vwap_bleed_broken: bool,
      is_trailing_stop_hit: bool,
      is_cvar_cosignal_active: bool = False,   # Phase-2 addition; default-False preserves Phase-1/1.5 behavior
  ) -> tuple[str | None, list[str]]:
  ```
- **`_TRIGGER_PRIORITY_ORDER`** updated with the cosignal placed at the **end** of the list — its position is the structural enforcement that it cannot be the winner when no incumbent fires:
  ```python
  _TRIGGER_PRIORITY_ORDER: list[str] = [
      "VWAP Breakdown",
      "Take-Profit",
      "VWAP Bleed Cut",
      "Trailing Stop",
      "CVaR Co-Signal",   # Phase-2 addition; permanently last — never wins when only it is active
  ]
  ```
- **Special-case guard:** within the function, after building the fired list, if the only fired entry is `"CVaR Co-Signal"`, return `(None, [])` — the co-signal cannot solely fire an exit (§5.2 binding). Equivalently: filter the cosignal out of the candidates for "winner" but keep it in the `co_fired` list when at least one incumbent fires. Either implementation is acceptable; both are tested.

```python
# Conceptual implementation (one valid form):
flag_map = {
    "VWAP Breakdown": is_vwap_broken,
    "Take-Profit": is_tp_hit,
    "VWAP Bleed Cut": is_vwap_bleed_broken,
    "Trailing Stop": is_trailing_stop_hit,
    "CVaR Co-Signal": is_cvar_cosignal_active,
}
fired = [name for name in _TRIGGER_PRIORITY_ORDER if flag_map[name]]
if not fired:
    return None, []
incumbent_fired = [n for n in fired if n != "CVaR Co-Signal"]
if not incumbent_fired:
    # CVaR Co-Signal cannot solely fire an exit (§5.2 binding)
    return None, []
winner = incumbent_fired[0]   # highest-priority incumbent
co_fired = [n for n in fired if n != winner]
return winner, co_fired
```

### Tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| **Back-compat regression (Phase-1/1.5)** | Existing callers that pass 4 booleans get byte-identical results. The 5th argument defaults `False`. |
| **§5.2 binding — cosignal alone cannot exit** | `(False, False, False, False, True)` → `(None, [])`. The single most important assertion in this plan. |
| **Cosignal co-fires when an incumbent fires** | `(True, False, False, False, True)` → `("VWAP Breakdown", ["CVaR Co-Signal"])`. |
| **Cosignal placed LAST in co-fired** | `(True, True, True, True, True)` → `("VWAP Breakdown", ["Take-Profit", "VWAP Bleed Cut", "Trailing Stop", "CVaR Co-Signal"])` — the order in `co_fired` matches `_TRIGGER_PRIORITY_ORDER` post-winner. |
| **All False** | `(False, False, False, False, False)` → `(None, [])`. |
| **Incumbent-only fires** | `(True, False, False, False, False)` → `("VWAP Breakdown", [])`. Pre-Phase-2 behavior preserved. |
| **Trailing Stop alone + cosignal** | `(False, False, False, True, True)` → `("Trailing Stop", ["CVaR Co-Signal"])`. Validates the cosignal co-fires even with the lowest-priority incumbent. |
| **G-3 ★ — 6 incumbent layers preserved** | A scan over `math_engine.py` enumerates the 6 incumbent functions (`compute_vwap_state_update`, `compute_tp_confirmation`, `compute_breakeven_update`, `compute_active_trailing_stop`, `compute_exit_confirmation`, `run_monte_carlo` consumer site) — all present, untouched in this PR. |
| **`_TRIGGER_PRIORITY_ORDER` content** | The list contains exactly 5 entries in the named order; `"CVaR Co-Signal"` is the last entry. |
| **NaN/Inf closure (A-2 ★)** | The function accepts only booleans — passing a non-bool (e.g. a float NaN coerced to bool) is documented as undefined; the project's existing `_reject_non_finite` policy does NOT cover bool inputs by design (`math_engine.py:30-54` policy comment). |
| **Replay-determinism (F-2 ★)** | Pure function; same inputs → same outputs. Verified by a property-based test (Hypothesis or equivalent) that runs 1000 random Boolean tuples twice and asserts equality. |

### Documentation

- The function docstring is updated to quote synthesis §5.2: *"The cosignal narrows, vetoes, or confirms an exit another layer already supports; it never solely fires one. Its placement at the end of `_TRIGGER_PRIORITY_ORDER` is the structural enforcement: when it is the only active signal, the resolver returns `(None, [])`."*
- A comment block above `_TRIGGER_PRIORITY_ORDER` documents the 5th-entry rationale.
- The PR commit message: *"Resolver extended (not replaced) — synthesis §5.3 binding. CVaR Co-Signal added as 5th input; placed last; structurally cannot solely fire an exit. G-3 ★ 6 incumbent layers preserved."*

## Dependencies

- **Blocks:** any Phase-2 wiring that consumes the resolver's output with the cosignal (the Tier-2 per-cycle path in `alpha_bot_execution.py`).
- **Blocked by:** `phase-2/cvar-cosignal-hysteresis-trigger/plan.md` — produces the `is_cvar_cosignal_active` boolean this resolver consumes.
- **Blocked by:** the four Phase-2 preconditions (synthesis §5.1).
- **Coordinated with:** `engine-audit/priority-resolver-ordering-audit/plan.md` — the audit lane evaluates whether the named order is structurally optimal. **The audit MUST run BEFORE this extension lands.** If the audit recommends a re-ordering, the PM resolves before this plan executes. (NN1 binding: any re-ordering must be safety-criterion-derived, NOT P&L-driven.)

## Definition of Done

- All RED tests above land first; GREEN: every test passes.
- Full-tree pytest with HEAD SHA + count + zero errors.
- The 5th argument is additive with a `False` default — Phase-1/1.5 callers byte-identical.
- The **§5.2 binding test** (cosignal alone → `(None, [])`) passes. This is the single most important assertion in this plan.
- `_TRIGGER_PRIORITY_ORDER` has exactly 5 entries, cosignal last.
- G-3 ★ scan passes: the 6 incumbent layers are present and untouched.
- The function remains pure (no I/O, no global state, no wall-clock).
- The priority-resolver-ordering audit (sibling engine-audit plan) has run and the PM has resolved any re-ordering recommendation.
- The four Phase-2 preconditions have been authorized as PASS in writing.

## Risk callouts / hazards

- **§5.2 binding (cosignal NEVER solely fires).** The single most important invariant. Tested directly with the all-False-but-cosignal case → `(None, [])`. Tested transitively via the cosignal state machine's integration test.
- **G-3 ★ (6 layers retained).** Binding. The scan test enforces it.
- **No new arbiter (critic's spine).** The resolver is **the** composition body. No second arbiter ships. The cosignal feeds into the existing resolver — does not bypass it.
- **NN1 (★ load-bearing).** The position of the cosignal in `_TRIGGER_PRIORITY_ORDER` is set by **structural reasoning** (it cannot win alone — last position enforces this), NEVER by P&L tuning. The ordering audit is the upstream check.
- **Layered structure preserved (project risk-engine charter).** This plan is a pure ADDITION — does not collapse the layered exit logic into a single condition.
- **Back-compat.** Default-False 5th argument preserves Phase-1/1.5 callers byte-identical. A regression test asserts a 4-argument call equals the historical reference.
- **Replay-determinism (F-2 ★).** Pure function. Property-based test verifies bit-identical replay.
- **Two-DB boundary (E-2 ★).** No DB writes from inside the resolver.
- **`is_live` explicit (F-1 ★).** Pure function; no path to a broker call.

## Out of scope

- Changing the order of the 4 incumbent triggers. The ordering-audit plan evaluates whether the canonical order is structurally optimal; any change goes through the audit + PM resolution.
- A "veto" boolean (i.e. an explicit `is_cosignal_veto`). Veto semantics live in the cosignal state machine — the resolver only sees the boolean.
- A "narrow" semantic (e.g. confidence-weighted resolution). The cosignal is binary; narrowing happens upstream.
- A 6th, 7th, ... signal. The resolver is extended to 5; future extensions are out of scope.
- Collapsing the layered structure into a single condition. Forbidden by project risk-engine charter anti-pattern.
- Replacing the resolver with a new composition body. Forbidden by §5.3 + critic's spine.
- Removing or re-ordering `_TRIGGER_PRIORITY_ORDER` entries. The cosignal is APPENDED last.
- Wiring the resolver into the Tier-2 per-cycle path. That wiring lives in `alpha_bot_execution.py` and is a sibling plan.
