# Feature: CVaR Co-Signal — Two-Level Hysteresis Band + Multi-Tick Confirmation State Machine

**Phase / Lane:** Phase 2 — Finalist B, **evidence-gated**. Scaffold now; ships only if the four Phase-2 preconditions pass.
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED — adversarial) + `quant-code-reviewer` (review). Standing Trio is sufficient — this is a pure state machine; no advisor or DB surface.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.3 (live CVaR *trigger* un-validatable — therefore the CVaR layer is a **co-signal**, never a sole trigger), §5.2 (Phase-2 endpoint is a **CVaR co-signal** — narrows / vetoes / confirms an exit another layer already supports; **never solely fires** an exit; "does CVaR agreement improve an exit the engine was already going to make" is the validatable question), §5.3 (CVaR trigger design: *two-level hysteresis band + multi-tick confirmation state machine, a sibling of `compute_exit_confirmation`; abstains fail-safe when the ensemble is unavailable; operates as a co-signal*; the 6 heuristics retained as a **permanent safety floor**; **`lambda` frozen by mandate, NOT Optuna-searched**), §5.6 R-1 (system is more complex post-Phase-2; honest tuned-parameter count is ONE — gamma), §5.6 R-5 (no real `E[U(exit)]` vs `E[U(hold)]` crossover; ships as `CVaR-with-risk-aversion-shaping`).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §B.0 (the validation power of a co-signal vs a trigger is fundamentally different; co-signal validation is constructible at AlphaBot's data scale, trigger validation is not).
- `docs/handoff/council-attack-rubric.md` Family **C** ★★ (C-1 ★ **hysteresis on CVaR trigger** — "structurally analogous to the existing `EXIT_CONFIRM_TICKS = 3` and `VWAP_BREAK_CONFIRM_TICKS = 3` confirmation counters; **a hard, single-tick CVaR trigger converts tail-estimation noise directly into spurious exits — a candidate without hysteresis fails here automatically**"; C-2 ★ tail-bias direction — small-sample CVaR understates tail, "fails toward not exiting"; the co-signal must not be load-bearing for the *protective* function), Family **F** (F-4 ★ insufficient-data sentinel mirror — the co-signal abstains fail-safe), Family **G** (G-3 the 6 incumbent layers' fate is explicit — **retained as permanent safety floor**).
- Code anchors: `math_engine.py` (existing confirmation-counter patterns — `compute_exit_confirmation` uses `EXIT_CONFIRM_TICKS = 3`; `compute_vwap_state_update` uses `VWAP_BREAK_CONFIRM_TICKS = 3`); `math_engine.py:669-692` (`resolve_trigger_priority` — the resolver the co-signal feeds into, sibling plan `phase-2/priority-resolver-cvar-cosignal/plan.md` extends it); the `CVaRAssessment` frozen typed object (from M2 Phase-1 work, extended in Phase 2 with co-signal fields).

## Why (problem statement)

A live CVaR **trigger** that solely fires exits is un-validatable at AlphaBot's data scale (council synthesis §2.3 — the decisive finding of the entire council process). The Phase-2 endpoint is therefore a CVaR **co-signal**: an input that narrows, vetoes, or confirms an exit another layer already supports, but **never solely fires** one.

Two structural mechanisms make the co-signal safe:

1. **Hysteresis (C-1 ★).** A hard, single-tick CVaR breach trigger converts tail-estimation noise directly into spurious decisions. The mitigation is structural — a two-level band (entry threshold > exit threshold) plus multi-tick confirmation (analog of `EXIT_CONFIRM_TICKS = 3`). Without hysteresis, the candidate fails C-1 ★ automatically — the council attack rubric is explicit on this.
2. **Co-signal composition (§5.2).** The co-signal enters the existing `resolve_trigger_priority` as one additional boolean — the resolver is kept and extended, never replaced, never collapsed into a single condition (the layered structure IS the safety mechanism). The co-signal can confirm an exit another layer fires; it can veto an exit if its assessment says the tail risk is moving away; it cannot, alone, exit a position.

Per §5.6 R-1, the system after Phase 2 is **MORE complex**, not less — 6 heuristics + a CVaR resolver signal. The "2-parameter simplicity" of the original pitch never materializes; the honest tuned-parameter count is **one** (gamma). Per §5.6 R-5, no literal `E[U(exit)]` vs `E[U(hold)]` per-minute crossover ships — EUT enters as the `gamma` risk-aversion *shaping* of the CVaR budget.

## Deliverables

### Code

#### The state machine

- **`math_engine.py`** — new pure function:
  ```python
  def compute_cvar_cosignal_confirmation(
      current_cvar_pct: float | None,         # from CVaRAssessment.cvar_pct (Phase 2 multi-day)
      current_cvar_breach: bool,              # entry-threshold crossed THIS tick
      current_cvar_recovery: bool,            # exit-threshold crossed THIS tick (lower band)
      current_breach_ticks: int,              # confirmation counter (carried in state)
      current_recovery_ticks: int,            # de-confirmation counter (carried in state)
      cvar_assessment_available: bool,        # False -> abstain fail-safe (F-4 ★)
  ) -> tuple[int, int, bool, bool]:
      """Returns (new_breach_ticks, new_recovery_ticks, is_cosignal_active, is_cosignal_cleared).
      
      Two-level hysteresis: entry-threshold > exit-threshold. The cosignal becomes
      ACTIVE only after CVAR_COSIGNAL_CONFIRM_TICKS consecutive breach ticks. Once
      active, it stays active until CVAR_COSIGNAL_CLEAR_TICKS consecutive recovery
      ticks. This sibling-of-compute_exit_confirmation pattern is the C-1 ★ binding
      structure. A hard single-tick trigger would be a C-1 KILL.
      
      When cvar_assessment_available is False, returns (0, 0, False, True) — abstain
      fail-safe. The protective stop still fires on its own; the cosignal does NOT
      disable the safety floor.
      """
  ```
- Module-scope named constants (no-magic-numbers):
  - `CVAR_COSIGNAL_CONFIRM_TICKS = 3` — analog of `EXIT_CONFIRM_TICKS`. Source comment: council synthesis §5.3 "sibling of `compute_exit_confirmation`."
  - `CVAR_COSIGNAL_CLEAR_TICKS = 3` — symmetric de-confirmation counter. Source comment: hysteresis-band exit confirmation.
  - `CVAR_COSIGNAL_ENTRY_THRESHOLD` — the entry band (a percentage of position value or a normalized z-score; the exact unit is part of the M2 → Phase-2 CVaR contract). **NN1-frozen by mandate**.
  - `CVAR_COSIGNAL_EXIT_THRESHOLD` — the exit band, strictly < entry threshold. **NN1-frozen by mandate**.

#### Frozen typed object — `CVaRAssessment` extended

Phase-1 M2 introduced `CVaRAssessment(cvar_pct, n_tail, stderr, insufficient_reason)`. Phase 2 extends it (additive only; M2-Phase-1 consumers untouched):

```python
@dataclass(frozen=True)
class CVaRAssessment:
    # Phase-1 M2 fields (unchanged):
    cvar_pct: float | None              # None = insufficient (F-4 ★)
    n_tail: int
    stderr: float | None
    insufficient_reason: str | None
    # Phase-2 additions:
    breach: bool                         # entry-threshold crossed THIS evaluation
    recovery: bool                       # exit-threshold crossed THIS evaluation
    tail_obs_count: int                  # genuine independent tail observations (for T in any future test; NEVER the path count)
```

- `breach` is always `False` when `cvar_pct is None` (fail-safe; F-4 ★).
- `recovery` is always `False` when `cvar_pct is None`.
- The dataclass is `frozen=True` — replay-parity invariant (F-2 ★).

#### Wiring into the per-cycle path

- The cosignal computation runs in Tier 2 (per-cycle, in-band) — see `phase-2/simulate-forward-paths/plan.md` for the Tier-1/Tier-2 split. It reads the path bank from the file cache + the manifest, calls the CVaR estimator (Rockafellar-Uryasev, same family as M2), produces the `CVaRAssessment`, and feeds it to `compute_cvar_cosignal_confirmation`.
- The cosignal boolean is passed to the extended `resolve_trigger_priority` (5th argument — sibling plan).
- **`lambda` is frozen by mandate, NOT Optuna-searched** (synthesis §5.3 binding). The system has exactly ONE tuned parameter — `gamma`. A `lambda` in the Optuna search space is forbidden.

#### EUT shaping (R-5)

- The CVaR thresholds are SHAPED by the CRRA `gamma` risk-aversion parameter that M1 already tunes — a higher `gamma` produces a more conservative threshold. **No separate `E[U(exit)]` vs `E[U(hold)]` crossover layer ships.** The shaping is a static function of `gamma` evaluated once per spec bundle, NOT a per-tick utility evaluation.
- The shaping function is documented in the spec bundle (`facets_json` includes the `gamma → threshold` map) and is NN1-frozen.

### Tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| **C-1 ★ hysteresis is structural** | A test that asserts a single-tick breach does NOT activate the cosignal; only `CVAR_COSIGNAL_CONFIRM_TICKS = 3` consecutive ticks activate it. A single-tick test failure = C-1 ★ KILL. |
| **C-1 ★ hysteresis band asymmetry** | A test asserts the entry threshold strictly exceeds the exit threshold. Equal thresholds = no hysteresis = C-1 KILL. |
| **Multi-tick confirmation correctness** | Sequences of (breach, breach, breach) → activation; (breach, breach, no-breach) → counter resets to 0 (not 2). |
| **De-confirmation (recovery)** | Active cosignal + (recovery, recovery, recovery) ticks → cleared. (recovery, no-recovery, recovery) → counter resets. |
| **F-4 ★ abstain fail-safe** | `cvar_assessment_available=False` → returns `(0, 0, False, True)`. Active state is wiped — the cosignal CANNOT survive an ensemble unavailability. |
| **A-2 ★ NaN/Inf closure** | NaN inputs raise `ValueError` at entry. |
| **`CVaRAssessment.breach` is `False` when `cvar_pct is None`** | A test asserts the typed object's invariant — fail-safe. |
| **`recovery` and `breach` are mutually exclusive within one tick** | Both `True` at once would mean entry and exit thresholds crossed simultaneously — structural invariant. |
| **Layered-structure invariant (G-3 safety floor)** | A test asserts the 6 incumbent layers (vol-scaling, time-squeeze, parabolic ratchet, breakeven, VWAP×2, MC) are NOT removed when the cosignal is added. A scan over `math_engine.py` enumerates the 6 functions and asserts presence. |
| **Cosignal does NOT solely fire an exit (§5.2 binding)** | An integration test: cosignal `is_cosignal_active=True` AND all 4 incumbent exit booleans `False` → `resolve_trigger_priority_extended` returns `(None, [])`. Validates the cosignal is NOT a sole trigger. |
| **lambda not in Optuna search (★ load-bearing)** | A regression test scans the autotuner search space and asserts `lambda` is NOT among the searched parameters. Synthesis §5.3 binding. |
| **`gamma` is the only Phase-2 tuned parameter** | The same scan asserts `gamma` is the only addition. Honest tuned-parameter count = 1 (R-1). |
| **Replay-determinism (F-2 ★)** | Same input sequence → bit-identical output sequence + counter states. |
| **EUT shaping is static per spec bundle (R-5)** | A test asserts the `gamma → threshold` map is evaluated ONCE per spec bundle, NOT per tick. No per-tick utility crossover. |

### Documentation

- Module docstring quotes synthesis §5.3 verbatim: "two-level hysteresis band + multi-tick confirmation state machine, a sibling of `compute_exit_confirmation`."
- Source comments on the four constants citing the council synthesis.
- The R-1 honest framing in the PR commit message: *"After Phase 2 the system has 6 heuristics + 1 cosignal (more complex, not less). The honest tuned-parameter count is ONE — gamma. `lambda` is mandate-frozen, NOT Optuna-searched."*

## Dependencies

- **Blocks:** the priority resolver extension plan (`phase-2/priority-resolver-cvar-cosignal/plan.md`) — that plan extends `resolve_trigger_priority` to accept the cosignal boolean.
- **Blocked by:** `phase-2/simulate-forward-paths/plan.md` — the forward-path simulator produces the `ForwardPathBundle` the CVaR estimator consumes.
- **Blocked by:** `phase-2/tier1-seed-determinism/plan.md` — replay parity (F-2 ★) requires the tier1_seed pipeline.
- **Blocked by:** the four Phase-2 preconditions (synthesis §5.1).
- **Soft dependency:** the Phase-2 extension of the M2 `CVaRAssessment` dataclass (additive — Phase-1 M2 consumers untouched).

## Definition of Done

- All RED tests above land first; GREEN: every test passes.
- Full-tree pytest with HEAD SHA + count + zero errors.
- C-1 ★ hysteresis structurally enforced (single-tick breach DOES NOT activate; the test KILLS a single-tick implementation).
- `compute_cvar_cosignal_confirmation` is a pure function (no I/O, no global state).
- All four constants are named module-scope, source-commented.
- `CVaRAssessment` Phase-2 extensions are additive (Phase-1 M2 consumers byte-identical).
- `lambda` is NOT in the Optuna search space (regression test asserts).
- `gamma` is the only Phase-2 addition to the search space; the honest tuned-parameter count = 1.
- The 6 incumbent layers are preserved (G-3 ★ scan passes).
- The cosignal CANNOT solely fire an exit (integration test asserts).
- No EUT crossover layer ships (R-5 binding; the threshold is `gamma`-shaped, static per spec bundle).
- The four Phase-2 preconditions have been authorized as PASS by the user / PM in writing before this plan executes.

## Risk callouts / hazards

- **C-1 ★ (hysteresis structural; single-tick = KILL).** Binding. The two constants + the state-machine structure are non-negotiable. A reviewer must be able to read the test and see the single-tick assertion fail loudly.
- **§5.2 binding (cosignal never solely fires).** Structurally enforced via the resolver extension (sibling plan). The integration test in this plan covers the function-level invariant; the resolver test covers the wired invariant.
- **F-4 ★ (abstain fail-safe).** When `cvar_assessment_available=False`, the cosignal returns to inactive; active state is wiped. The protective stop still fires on its own. The new core NEVER disables the safety floor.
- **C-2 ★ (small-sample CVaR understates tail).** Acknowledged. Because the cosignal is non-load-bearing (the protective stop is the floor), an understated CVaR does NOT silently disable protection — the floor catches it.
- **G-3 (6 layers retained).** Binding. The cosignal is an ADDITION, never a replacement. The G-3 ★ scan test asserts the 6 functions remain in `math_engine.py`.
- **R-1 (system is more complex).** Acknowledged. The honest framing is documented in the commit message.
- **R-5 (no E[U(exit)] vs E[U(hold)] crossover).** No per-tick utility evaluation. The `gamma → threshold` shaping is static per spec bundle.
- **`lambda` NOT searched.** Binding. The Optuna search-space scan test asserts.
- **NN1 (★ load-bearing).** Entry/exit thresholds, confirm/clear tick counts, and the `gamma → threshold` shaping function are ALL mandate-frozen. None enters the Optuna search.
- **Replay-determinism (F-2 ★).** Pure function; deterministic; bit-identical replay.
- **Two-DB boundary (E-2 ★).** This plan touches `math_engine.py` only; no DB writes from inside the cosignal function.
- **`is_live` explicit (F-1 ★).** Pure function; no path to a broker call.

## Out of scope

- A live CVaR **trigger** that solely fires exits. Forbidden by §5.2.
- A `lambda` in the Optuna search space. Forbidden by §5.3.
- A literal `E[U(exit)]` vs `E[U(hold)]` per-tick crossover. Forbidden by R-5.
- Removing or re-ordering the 6 incumbent layers. Forbidden by G-3 and synthesis §5.3.
- The forward-path simulator. Sibling plan.
- The priority resolver extension. Sibling plan.
- The CVaR estimator (Rockafellar-Uryasev) — shared with M2; Phase-2 reuses the same estimator on the multi-day path bank output.
- LLM-authored advisor commentary on cosignal decisions. Phase-2 `advisor_observations` is for the Phase-2 Advisor roles, not this state machine.
