<!-- ARCHIVED from research/consensus-exit @ e402980, original date 2026-05-30. Engine integration map: blast radius, N-confirms placement, H-3 nuance (arming vs scoring layer distinction), resolve_consensus_exit placement spec. Phase 3c regime-conditional response merged @ c23153c. -->
# Task #3 — Engine Integration Map + Blast Radius

**Author:** engine-integrator (Agent Team `consensus-exit-research`)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2 (read-only)
**Scope:** Exactly how a weighted-consensus exit core grafts onto the existing engine; full blast radius; N-confirms placement; honesty signal; H-3 confirmation.
**Method:** static read of `math_engine.py`, `alpha_bot_execution.py`, `autotuner.py`, `synthetic_history.py`, `advisors/`, `app.py`. Every concrete claim carries a `file:line`. Builds on `audit/00-SYNTHESIS.md` + `findings/pillar4-runtime.md` (H-3) + `findings/pillar2-mathimpl.md`.

Owned by sibling tracks (cross-referenced, NOT duplicated here): warm-start prior derivation → `prior-researcher`; permission-to-tune statistics + H-1 fix → `tuning-methodologist` (task #2); design space + validatability → task #1.

---

## 0. The single most important structural fact

**The exit decision exists in TWO tick-for-tick mirrored copies that are pinned together by parity tests.** Any consensus core must be built as ONE new shared `math_engine` primitive called from BOTH, or the parity guarantee — and with it the meaning of the autotuner objective — breaks.

- **Live exit core:** `alpha_bot_execution.py:1393-1663` — computes `is_trailing_stop_hit`, `tp_triggered_now`, `is_vwap_broken`, `is_vwap_bleed_broken`, then calls `math_engine.resolve_trigger_priority(...)` (`alpha_bot_execution.py:1619-1624`).
- **Replay exit core:** `autotuner._replay_exit_tick` (`autotuner.py:909-1070`) — computes the same four flags from the same `math_engine` primitives, then calls the same `math_engine.resolve_trigger_priority(...)` (`autotuner.py:1066-1070`).
- **The pin:** `tests/autotuner/test_c3_replay_exit_parity.py` asserts `_replay_exit_tick` matches a `_production_exit_sequence` reference tick-for-tick; `tests/autotuner/test_c3_replay_internal_lockstep.py` drives all three replay callers (`run_simulation`, `_collect_sim_returns`, `replay_exit_sequence`) over parity fixtures to block a divergent re-inline (`autotuner.py:915-922` docstring). `tests/autotuner/test_r3b_shared_priority_resolver.py` + `tests/engine/test_trigger_priority_dispatch.py` pin the resolver call at both sites.

**Consequence for the consensus design:** the weighted-consensus scorer is a NEW pure function in `math_engine.py` (call it `resolve_consensus_exit(...)`), and it must REPLACE `resolve_trigger_priority` at BOTH `alpha_bot_execution.py:1619` AND `autotuner.py:1066` in the same change. The per-criterion boolean flags it consumes (`is_trailing_stop_hit`, `is_tp_hit`, `is_vwap_broken`, `is_vwap_bleed_broken`) are still produced upstream by the existing `compute_*` primitives — the consensus layer sits where `resolve_trigger_priority` sits today, downstream of all four signal computations.

---

## 1. INTEGRATION MAP — where each piece grafts

### 1a. `math_engine.py` — what gets replaced/refactored

**Replaced:** `resolve_trigger_priority(is_vwap_broken, is_tp_hit, is_vwap_bleed_broken, is_trailing_stop_hit) -> (str|None, list[str])` at `math_engine.py:836-859`. Today it is a pure fixed-priority picker over the hardcoded `_TRIGGER_PRIORITY_ORDER` list (`math_engine.py:828-833`): collect the True flags in priority order, return `(winner, co-fired)`. The math audit certifies it total + safe + pure (`pillar2-mathimpl.md` §(e)). It has NO tunable input today — the order is a literal list.

**Criterion count (confirmed against wiring, with prior-researcher):** the consensus scores over the SAME FOUR boolean flags `resolve_trigger_priority` takes today — `is_vwap_broken`, `is_tp_hit`, `is_vwap_bleed_broken`, `is_trailing_stop_hit` (`math_engine.py:836-841`). The "six layers" (vol scaling, time-squeeze, parabolic ratchet, breakeven lock) are UPSTREAM scalar computations that shape the trailing-stop's `stop_trigger_level` before `compute_exit_confirmation` turns it into the ONE trailing-stop flag (`alpha_bot_execution.py:1367-1402`); Monte Carlo is a GATE/veto (`math_engine.py:508`, `:569`), not a flag. So the canonical free-parameter count is **4 criterion weights + 1 threshold + 1 N-confirms = 6 tunables**. A "~7-voter" variant that promotes MC/parabolic/breakeven to first-class weighted voters is a DIFFERENT, more invasive design the current wiring does not support without restructuring the upstream primitives — it is the alternative, not what "graft onto the existing engine" yields.

**New primitive** `resolve_consensus_exit(...)` must:
1. Accept the same four boolean signal flags PLUS the per-criterion weights, the confirm threshold, and (per the experiment) the N-confirms count and current per-position consensus tick count.
2. Compute `score = Σ (weight_i · 1[signal_i fired])` over ACTIVE criteria; compare to a tunable `threshold`; that yields a single-tick "confirm". (Graceful-degradation property analyzed in §4/§5.)
3. Return enough to drive the existing downstream: a `reason` label (which criterion/criteria drove the confirm — needed for `_level_map`, Discord, chart events) and the co-fired list, preserving the existing 2-tuple shape so callers need minimal change. The N-confirms debounce is a SEPARATE concern (see §3) — keep the scorer pure and stateless; the temporal debounce lives in the caller's state dict exactly as `below_stop_count` does today.
4. Keep `_TRIGGER_PRIORITY_ORDER` (or a successor ordered list) ONLY as the tie/label-ordering for the `reason` string when several criteria co-fire — the priority list stops being the DECISION mechanism and becomes presentation ordering. **This is the layered-safety subtlety:** the fixed ladder today is itself a safety structure; replacing it with a sum means a single high-weight criterion must still be able to fire alone (its weight ≥ threshold) or the design silently weakens protection. Task #1 owns whether each criterion's weight floor guarantees solo-fire.

**Unchanged in `math_engine.py`:** every upstream signal primitive — `compute_exit_confirmation` (`:457-518`), `compute_tp_confirmation` (`:525-587`), `compute_vwap_breakdown_update` (`:684-789`), `compute_active_trailing_stop`, `compute_breakeven_update`, `compute_time_squeeze_decay`, `compute_para_arm_decision`, `run_monte_carlo`, `compute_regime_match_quality`. The consensus core consumes their outputs; it does not touch their internals. The named-constant / golden-fixture project rules (`CLAUDE.md` Coding Standards) bind the new function: weights, threshold default, and N-confirms default each need a named constant + provenance comment + a golden fixture (math-layer change → `quant-test-writer` per the standing team rule).

### 1b. `alpha_bot_execution.py` — where the score/threshold/N-confirms decision lands

The live decision lands at `alpha_bot_execution.py:1611-1663`, the `if (is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken):` block. Today that `or` is itself a crude "any-criterion" consensus with the priority resolver picking the winner. Under the new design:

- The four flags are still computed at `:1395-1486` (trailing-stop confirm, TP confirm, VWAP breakdown/bleed, grace-window suppression) — UNCHANGED.
- The `or` guard at `:1611-1616` is replaced by a call to `resolve_consensus_exit(...)` whose weighted score ≥ threshold for the required N consecutive ticks.
- `reason` + `also_true` (`:1619-1624`) come from the new function; `_level_map` (`:1625-1631`), the Discord/event log (`:1633-1640`), and the `execution_queue.append` payload (`:1642-1663`) are downstream consumers that only need `reason`/`also_true`/`attempted_level` — they survive unchanged provided the new function still returns a `reason` string and co-fired list.
- The new tunable values (weights, threshold, N-confirms) are read per cycle from `acc_params` exactly like `acc_VWAP_CROSS_HWM_PCT` etc. are today (the `acc_params.get(...)` pattern, e.g. `:1329-1331`, `:1373-1375`). This keeps the engine's read-path identical: params dict → consensus function. **No new blocking I/O** — all params are already in `bot_state`/`acc_params` loaded at cycle start.

### 1c. `autotuner.py` — how tunable weights enter the search

Three wiring points, all already-established patterns:

1. **Search-space bounds:** add `_SS_*` module constants for each weight, the threshold, and N-confirms (sibling to `_SS_TAKE_PROFIT_MC_MIN` … `_SS_MAX_PARA_SQUEEZE_MAX`, `autotuner.py:251-262`) — each named + provenance-commented per the no-magic-numbers rule.
2. **Trial suggestion:** add `trial.suggest_float(...)` / `suggest_int(...)` lines inside the objective closure (`autotuner.py:1841-1869`, alongside the existing six at `:1843-1848`), and add the new keys to `OPTUNA_SEARCH_SPACE_KEYS` (`autotuner.py:121-124`) so the schema-validation gate (`:2004`, `:2007`) recognizes them and the AI-vs-fallback-vs-default cascade (`:2090-2107`) treats them as a coherent param set. Also add to `database.DEFAULT_STRATEGY` and the `DEFAULT_LOCKED_VARS` handling (referenced `autotuner.py:119-120`).
3. **Replay scoring:** `_replay_exit_tick` (`autotuner.py:909-1070`) is what scores each trial's param set on the validation fold via `_collect_sim_returns` (`autotuner.py:1854`). Once it calls `resolve_consensus_exit` with the trial's suggested weights/threshold/N (read from `p` via `p.get(...)`, sibling to `p.get("TAKE_PROFIT_MC_PCT", 5.0)` at `:944-945`), the Optuna objective AUTOMATICALLY reflects consensus behavior — no separate objective rewrite. The CRRA-EU objective math (`compute_crra_eu_objective`, `autotuner.py:1863-1866`) is UNCHANGED; it scores the guard-alpha return series that the consensus exits produce.

**Permission-to-tune layering — RESOLVED with `tuning-methodologist` (task #2): the gate is HAIRCUT-INTERNAL, not cascade-external.** The "warm-start from priors, move only when statistically earned, else stay at prior" gate rides ON TOP of the existing Harvey & Liu BHY selection haircut (`_haircut_select`, `autotuner.py:1184-1272`) + the `compute_n_effective` multiple-testing accounting (`autotuner.py:761-811`), NOT as a wrapper around the OOS AI/fallback/default cascade (`autotuner.py:1916-2107`). Rationale (tuning-methodologist's call):
- The cascade is param-SET-granular — it picks WHICH whole set deploys (AI vs fallback vs default), `:2090-2107`. A per-WEIGHT gate bolted onto it would force re-running the cascade per weight: wrong layer.
- `_haircut_select` + `compute_n_effective` is ALREADY the per-hypothesis significance machinery. "Each free weight is one more hypothesis in the multiple-testing family" belongs where hypotheses are COUNTED (`compute_n_effective`, `:761-811`) and SCORED (the t-stat loop, `:1251`). The cascade is UNCHANGED — the gate only changes what the "AI proposal" is allowed to contain.

So the blast radius for the GATE points at `_haircut_select` (`:1184-1272`) + `compute_n_effective` (`:761-811`) + the call-site wiring (`:1953-1977`), NOT the cascade. Two gate designs exist (tuning-methodologist's report `research/02-tuning-gate-h1.md`): Design A charges N_effective for each weight permitted to float (the whole proposal clears a higher bar the more weights it frees); Design B runs a per-weight BHY test inside the haircut. Both are haircut-internal.

**COUPLING (load-bearing — ties §1c to the §2 blast radius):** each new weight param added to the search space (the `_SS_*` + `trial.suggest_*` + `OPTUNA_SEARCH_SPACE_KEYS` + `DEFAULT_STRATEGY` additions above) MUST ALSO be reflected in the `compute_n_effective` count, or the gate under-corrects (it would score N hypotheses while counting fewer). The search-space widening and the gating count are NOT independent edits — they move together. This is the concrete mechanism by which "6 new tunables on a ~4-day validation fold" (the dimensionality concern, §1a) becomes a higher significance bar rather than silent overfitting.

**Prerequisite blocker:** see §6 (H-1) — `_haircut_select` currently drops its `tstat_fn` argument, so any permission gate riding the haircut t-stat inherits the Sortino-vs-CRRA category error until H-1 is fixed. tuning-methodologist owns H-1 as a HARD critical-path prerequisite (`research/02-tuning-gate-h1.md` §sub-q4).

### 1d. `advisors/` — how tuning state would report

The three Sprint-3 producers (`overfitting_conscience.py`, `spec_critic.py`, `divergence_explainer.py`) all write `AdvisorObservation` dicts to `advisor_observations` keyed by `symphony_id` via `database.insert_advisor_observation`, called post-walk-forward from `autotuner.py`. The natural reporting seam for "this weight is tuned vs still on its prior":

- **Spec Critic** (`advisors/spec_critic.py:1-37`) already inspects `spec_facets` `freeze_discipline` values and BREACHes on `BACKTEST_SELECTION` (it is explicitly NOT in `_ACCEPTABLE_DISCIPLINES`, `spec_critic.py:9-13`). If a weight's PRIOR is stored as a `spec_facet` with discipline `THEORY`/`STYLIZED_FACT` and its TUNED value moves under `BACKTEST_SELECTION` provenance, the existing Spec Critic indicator surfaces the divergence with zero new producer logic — the discipline tag IS the honesty signal (see §4).
- **Overfitting Conscience** (`advisors/overfitting_conscience.py:1-27`) tracks the `researcher_dof_ledger` S-counter / N_effective budget. Each tunable weight adds a degree of freedom to the search → it inflates the multiple-testing count `compute_n_effective` feeds (`autotuner.py:1965-1968`). OC's `S/N_optuna` ratio and drift indicators (`overfitting_conscience.py:8-12`) would naturally report the added DoF cost of widening the search space with weights. Task #2 owns the DoF-accounting detail.

---

## 2. BLAST RADIUS — every affected call site + consumer

Recalling the MC-sentinel lesson (`MEMORY.md` `project_mc_sentinel_consumer_blast_radius`: a return-value change is never `math_engine`-local), the consensus core's return value flows into many consumers. Enumerated:

**Direct decision-function call sites (must change together — parity-pinned):**
| Site | file:line | Role |
|---|---|---|
| Live resolver call | `alpha_bot_execution.py:1619-1624` | production exit decision |
| Replay resolver call | `autotuner.py:1066-1070` | objective-scoring exit decision |
| Priority order list | `math_engine.py:828-833` | becomes label ordering, not decision |
| Resolver definition | `math_engine.py:836-859` | replaced by `resolve_consensus_exit` |

**Downstream consumers of the `(reason, also_true)` return — survive IF the shape is preserved:**
| Consumer | file:line | Dependency |
|---|---|---|
| `_level_map` keyed by reason | `alpha_bot_execution.py:1625-1631` | needs `reason ∈` the criterion label set |
| Discord/event log | `alpha_bot_execution.py:1633-1640`; `reporting.py` | logs `reason.upper()` + level |
| `execution_queue` payload | `alpha_bot_execution.py:1642-1663` | carries `reason`, `also_true`, `attempted_level` |
| `record_exit_trigger` telemetry | `alpha_bot_execution.py:1766-1777` | persists `triggered_reason`, `also_true` |
| Chart event tagging | `alpha_bot_execution.py:1517-1529` | `chart_event` derived from the flags |
| Replay reason string | `autotuner.py:1066`, `synthetic_history.py:253-254,393` | replay records the reason; `_replay_exit_tick` return is `str|None` |

**Parity/contract tests that WILL go RED and must be updated by the test-writer in lockstep (NOT silently):**
`test_c3_replay_exit_parity.py`, `test_c3_replay_internal_lockstep.py`, `test_c3_replay_calls_exit_confirmation.py`, `test_r3b_shared_priority_resolver.py`, `test_trigger_priority_dispatch.py`, `test_c4_haircut_gates_selection.py` (search-space + cascade), plus the persistence tests (`test_autotune_runs_persistence.py`) if new params are persisted. The 60-file grep hit set for the resolver/exit symbols is the upper bound on the regression surface; the production-code subset is the four files above + `synthetic_history.py` (which only references the replay core in comments, `synthetic_history.py:253-254,393` — it builds tick dicts, it does not re-implement the exit).

**Schema/DB blast (additive-first per project rule):** new tunable params are additive columns/keys in `DEFAULT_STRATEGY` + per-symphony params (no destructive migration); priors-as-facets are additive `spec_facets` rows on a NEW frozen bundle (the canonical THEORY bundle is content-hashed + frozen — adding weight facets means a NEW bundle id via `get_or_create_phase1_theory_bundle_id`-style insert, `database.py:1185,1213-1217`, NOT mutating the existing frozen one). `sqlite-specialist` owns the migration shape; flagged, not designed here.

**True scope verdict:** the change is NOT `math_engine`-local. It is a coordinated 4-file production change (`math_engine.py` + `alpha_bot_execution.py` + `autotuner.py` + DB schema/defaults) with a large parity-test update surface, gated by the H-1 fix as a prerequisite. This is a multi-cycle Agent-Teams TDD effort, not a one-shot.

---

## 3. N-CONFIRMS PLACEMENT (temporal debounce across ticks)

**The pattern already exists** — N-confirms is exactly how every existing criterion debounces today, so there is a proven, no-new-I/O home for it.

- Per-position transient counters live in the flat `bot_state[symphony_id]` dict: `below_stop_count`, `above_tp_count`, `vwap_ticks`, `vwap_bleed_ticks`, `hwm_hold_ticks` (initialized `alpha_bot_execution.py:759-767` and `:1201-1209`; reset on mode toggle `:824-832`). These persist across ticks within and across cycles via `load_state`/`save_state`.
- `compute_exit_confirmation` already implements an N=`EXIT_CONFIRM_TICKS`(=3) debounce by threading `current_below_stop_count` in and returning the incremented/reset count (`math_engine.py:463`, `:513-518`); the caller stores it back at `alpha_bot_execution.py:1403`. `compute_tp_confirmation` does the same with `above_tp_count` (N=`TP_CONFIRM_TICKS`=2). VWAP uses `vwap_ticks`/`vwap_bleed_ticks` with `VWAP_BREAK_CONFIRM_TICKS`=3.

**Placement for the consensus N-confirms:** add ONE new transient counter `consensus_confirm_count` to the `bot_state[symphony_id]` dict (same init/reset sites as above). The pure `resolve_consensus_exit` takes `current_consensus_confirm_count` in and returns the updated count plus an `is_confirmed` boolean — identical contract to `compute_exit_confirmation`. The caller stores it back. The exit fires only when `is_confirmed` (count ≥ N). **No blocking I/O** (constraint 1): it is in-memory integer arithmetic on an already-loaded dict, persisted by the SAME single terminal `save_state` (`alpha_bot_execution.py:1826-1848`) that already serializes all transient counters. **No new state row, no new lock.**

**Replay mirror:** `_replay_exit_tick` already carries the identical counters in its `state` dict (`autotuner.py:1005,1011` thread `below_stop_count`; `:1019-1027` thread `above_tp_count`); adding `consensus_confirm_count` to the replay `state` dict keeps live/replay parity for free.

**Subtlety to flag for task #1:** stacking an N-confirms debounce on TOP of the per-criterion confirms (trailing stop already needs 3 ticks, VWAP needs 3, TP needs 2) means a consensus exit could require N consecutive ticks where the score ≥ threshold, where each contributing signal ALREADY took its own confirm ticks to flip. The audit's H-3 / over-exit analysis (`pillar4-runtime.md` §C) shows the current design is biased toward under-protection, not over-exit; a second debounce layer pushes further toward under-protection (slower to exit). Whether that is desired vs. whether the per-criterion confirms should collapse into the single consensus debounce is a design question task #1 owns — but it MUST be decided explicitly, because the layered confirms are a documented safety mechanism (`risk-engine-specialist` charter: never collapse layered exit logic into one condition without proving equivalence).

---

## 4. HONESTY SIGNAL — "tuned vs still on its prior" to operator/advisor/dashboard

The seam exists and needs no new producer if priors are stored with a provenance tag.

**Storage (proposed, pending `prior-researcher` confirmation):**
- PRIOR (warm-start anchor) → a `spec_facet` row on the frozen bundle with `freeze_discipline = THEORY` or `STYLIZED_FACT` (sibling to `gamma`/`utility_family`, `database.py:1213-1217`). Frozen + content-hashed.
- TUNED current value → the per-symphony params dict in the state DB (`save_symphony_strategy`/`get_symphony_strategy`), read live via `acc_params.get(...)`. Effectively `BACKTEST_SELECTION` provenance once the autotuner has moved it.

**Surfacing — three already-built layers:**
1. **Advisor (Spec Critic):** `advisors/spec_critic.py` already BREACHes when a facet carries `BACKTEST_SELECTION` discipline (`spec_critic.py:9-13`), and WATCHes on stale freeze ages. A weight that has moved off its THEORY prior under tuning is exactly the divergence its I-2 indicator is built to catch — zero new producer code; it writes an `advisor_observations` row keyed by `symphony_id`.
2. **Dashboard:** the read-only `/ai-advisor` route (`app.py:2198-2224`) renders the last N `advisor_observations` across roles via `get_advisor_observations_for_role` into `ai_advisor.html`. A "tuned-off-prior" observation surfaces there automatically. The route is GET + read-only accessors (constraint 2 + 5 preserved).
3. **Per-run audit string:** `autotuner.py:2143-2151` already assembles an `overfitting_verdict` string (`NN1_HONEST … n_effective=…`) persisted by `save_autotune_run`. Each tuned weight adds a DoF; OC's verdict already encodes that. The operator sees how much search width the weights cost.

**Honest framing (per the audit's "sound but honest about limits" theme):** the divergence signal is a GOVERNANCE/diagnostic surface — it tells the operator "the engine moved this weight off its theory anchor under a 4-day validation fold." It does NOT certify the move was correct (the data-scale wall from `pillar2-optmethod.md` / `project_cvar_divergence_validation_wall` still binds). The signal's job is transparency, not validation. This matches the twice-rejected-CVaR-detector precedent: surface as diagnostic, never as an action-giving certifier.

---

## 5. H-3 NOTE — does weighted consensus fix the fail-safe gap?

**Verified against code: PARTIALLY, and only if designed for it. The graceful-degradation property is real at the SCORING layer but the H-3 root cause is at the ARMING layer, which a consensus scorer does not touch.**

**What consensus genuinely fixes (the scoring layer):** Today `resolve_trigger_priority` takes four already-computed booleans; a missing signal contributes a `False` and the priority ladder silently drops that rung. Under a weighted sum, a criterion whose input is unavailable contributes ZERO weight while the others still sum toward the threshold — this IS graceful degradation, and it is strictly better than the current silent rung-drop **provided the surviving criteria's weights can still reach the threshold alone.** That proviso is the catch: if MC-dependent criteria carry most of the weight, losing them still starves the score. Task #1 must set weight floors so the MC-INDEPENDENT criteria (VWAP Breakdown System A, VWAP Bleed System B — `compute_vwap_breakdown_update`, MC-independent per `pillar4-runtime.md` §C and `math_engine.py:744-787`) sum to ≥ threshold on their own. If they do, a no-MC position still exits on VWAP alone with no special-casing — a clean improvement.

**What consensus does NOT fix by itself (the arming layer — the actual H-3):** H-3 is NOT in the resolver. It is in the ARMING gate at `alpha_bot_execution.py:1292-1303`: the ONLY path that sets `armed=True` requires `mc_available` (`:1294`), and `compute_exit_confirmation` returns early `(count, False)` when `(not armed)` (`math_engine.py:503-504`). So when MC is PERMANENTLY absent (insufficient history, or the regime-match guard forcing `prob_beating=None` at `:1271-1272`), the trailing-stop criterion can never even produce a `True` flag to feed the consensus scorer — it is gated out one layer upstream of where consensus lives. **Confirmed by reading the code:** the trailing-stop signal (`compute_exit_confirmation`, fed at `alpha_bot_execution.py:1395-1402`) is armed-gated; the consensus layer at `:1619` is downstream of it. A weighted sum over the four flags inherits the same upstream gate.

**Therefore:** weighted consensus makes the *signal-combination* layer fail gracefully (a real, citable improvement over the silent rung-drop), but the documented H-3 fail-to-arm defect (`pillar4-runtime.md` §C, §I; `audit/00-SYNTHESIS.md` H-3) is a SEPARATE arming-path fix. The two interact favorably: IF H-3 is fixed by adding a no-MC fail-safe arming path (the audit's recommended fix-shape (a)), THEN the trailing-stop flag can fire under no-MC, AND THEN the consensus scorer combines it gracefully with VWAP. The honest statement for Gate-1: **"consensus improves graceful degradation at signal combination, but does not on its own close H-3 — H-3 is an upstream arming-gate fix that the consensus work should either depend on or bundle, and weight floors must guarantee MC-independent solo-fire regardless."** Do not let a Gate-1 narrative claim "consensus fixes H-3" unqualified — that overstates it.

---

## 6. H-1 PREREQUISITE (cross-reference, owned by `tuning-methodologist` task #2)

`_haircut_select` (`autotuner.py:1184-1272`) hardcodes `compute_sortino_tstat(series, seed=trial_idx)` in its loop (`autotuner.py:1251`) and never calls its `tstat_fn` parameter, despite the call site correctly passing `tstat_fn=compute_crra_eu_tstat` for the canonical CRRA bundle (`autotuner.py:1953-1954`, `:1976-1977`) and the docstring asserting swapping `tstat_fn` is "the ONLY permitted change" (`:1206-1207`). Runtime-confirmed LIVE on the canonical THEORY-bundle path (`pillar4-runtime.md` §H). **Relevance to this experiment:** any permission-to-tune gate that decides "move weight off prior only when statistically earned" by riding the haircut t-stat will compute that earned-ness with the WRONG (Sortino) t-statistic until H-1 is fixed. H-1 is a strict prerequisite to a trustworthy permission gate. Detail + fix shape: `tuning-methodologist`'s file.

---

## 7. Summary for the synthesizer

- Consensus core = ONE new pure `math_engine.resolve_consensus_exit`, swapped in at BOTH `alpha_bot_execution.py:1619` and `autotuner.py:1066` together (parity-pinned; non-negotiable).
- Tunable weights/threshold/N enter the search via the established `_SS_*` + `trial.suggest_*` + `OPTUNA_SEARCH_SPACE_KEYS` + `DEFAULT_STRATEGY` pattern (`autotuner.py:121,251-262,1843-1848`); the objective auto-reflects them through `_replay_exit_tick`.
- N-confirms = one new transient int counter in `bot_state[symphony_id]` (sibling to `below_stop_count`), threaded through the pure function exactly like the existing confirm counters; no new I/O, no new lock, persisted by the existing terminal `save_state`.
- Honesty signal = PRIOR-as-`spec_facet` (THEORY discipline) vs TUNED-as-params (BACKTEST_SELECTION); Spec Critic + Overfitting Conscience already surface the divergence to `/ai-advisor` with no new producer logic.
- H-3: consensus improves graceful degradation at the COMBINATION layer (zero-weight for missing signal beats silent rung-drop) IF weight floors guarantee MC-independent solo-fire — but does NOT close H-3's upstream ARMING-gate defect on its own. Bundle or depend-on the H-3 arming fix.
- H-1 is a prerequisite for a trustworthy permission gate (cross-ref task #2).
- Blast radius: 4 production files + DB schema/defaults + a large parity-test update surface; multi-cycle Agent-Teams TDD effort, not `math_engine`-local.

**Open alignment (sent, non-blocking):** asked `tuning-methodologist` whether the permission gate sits inside `_haircut_select` or wraps the OOS cascade (changes §1c/§2 pointers); asked `prior-researcher` to confirm the spec_facet-vs-params storage split in §4. This file states my defensible default; will reconcile if they diverge.
