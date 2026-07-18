# Feature: Math R3-c — MA-11 Wire MAX_SQUEEZE_FLOOR (post-squeeze stop-distance floor, production + replay parity)
Status: ready
Created: 2026-07-18

> Part of the math-remediation program (charter `feature-plans/math-remediation-program.md`, audit DE-MATH-AUDIT-001). R3 gated sequence: R3-a (shipped @ 7e0b7778) → R3-b (MA-4, shipped @ f3c7e050 + deployed) → **R3-c (this, MA-11, LIVE-execution-path)** → R3-d (retune, operator-gated). Scope basis: `feature-plans/math-r3c-scoping.md` (r3c-scout, file:line-verified @ `f3c7e050`, **adversarially falsified by r3c-falsifier — all 8 claims CONFIRMED**; see its ADDENDUM incl. the 3 build caveats + the PM no-widening ruling). **Base: `origin/main @ f3c7e050` — the worktree `.claude/worktrees/math-r3c` (branch `fix/math-r3c`) already exists, forked + base-SHA-verified (local main is a stale pre-R0 husk — never fork it).**

## Summary
MA-11: `MAX_SQUEEZE_FLOOR` is a proven dead knob — assigned (`alpha_bot_execution.py:1236` `acc_MAX_SQUEEZE_FLOOR`, exactly ONE repo-wide hit) but never consumed, while being operator-writable (Settings UI via `_ALGO_PARAM_META`/`_SETTINGS_WRITE_ALLOWLIST`), advisor-suggestible (`_SUGGESTIBLE_ALLOWLIST`, ai_advisor.py:1979), and `.env`-configurable. Meanwhile the parabolic/breakeven squeeze (`math_engine.compute_active_trailing_stop:497-498`) multiplies the stop distance by `parabolic_squeeze_multiplier` (default 0.50, tunable 0.1–0.8) AFTER the `dynamic_min_stop` floor is applied — collapsing the effective stop distance to as little as 0.015 pp, i.e. the stop sits on the high-water mark and a single noise tick exits the position prematurely. **OPERATOR RULED (2026-07-18, verbatim "Wire it"): wire the floor, do not remove it.** R3-c makes `MAX_SQUEEZE_FLOOR` the real post-squeeze lower clamp on the stop DISTANCE (PM semantic ruling: Option A, pp — the human-facing Settings text is the design intent; the multiplier reading is inert at defaults), scoped INSIDE the squeeze branch, with a no-widening invariant, passed identically by production and the autotuner replay through the ONE existing shared seam (parity is structural — R3-d retunes by replay). Also reconciles the lying metadata (unit "×"/kind "mult" → pp/distance) and folds in the deferred DE-MATH-R3B-001 SHIPPED stamp.

## Acceptance Criteria
- [ ] **AC-1 (seam floor, squeeze-scoped):** `math_engine.compute_active_trailing_stop` gains an OPTIONAL trailing param `squeeze_floor: float | None = None` (None/`<=0` ⟹ no clamp — every existing 6-arg call keeps byte-identical behavior). When `para_armed or breakeven_locked` AND `squeeze_floor` is a positive finite number: `active = max(active * parabolic_squeeze_multiplier, min(squeeze_floor, pre_squeeze_active))`. The NO-squeeze path is untouched (falsifier caveat 1: fixture 06's 0.001 no-squeeze output MUST still pass — the floor never applies outside the squeeze branch).
- [ ] **AC-2 (no-widening invariant):** the floor can only limit SHRINKAGE — the clamped result never exceeds the pre-squeeze `active` (the `min(squeeze_floor, pre_squeeze_active)` bound; PM ruling in the scoping ADDENDUM: at defaults floor 0.20 > `dynamic_min_stop` 0.15 near close, and a naive `max(squeezed, floor)` would invert the squeeze into a widener). Property test across the operating ranges.
- [ ] **AC-3 (production wiring — the knob comes alive):** `alpha_bot_execution.py:1467` passes `squeeze_floor=acc_MAX_SQUEEZE_FLOOR` (the `:1236` assignment gains its first-ever reader). No other production exit logic changes.
- [ ] **AC-4 (replay parity — HARD):** `autotuner.py:1246` passes `squeeze_floor=p.get("MAX_SQUEEZE_FLOOR", alpha_bot_execution.MAX_SQUEEZE_FLOOR)` — the default sourced from the SAME module attribute as production (the R1/F6 `EXECUTION_START_TIME` idiom; never a replay-local mirror). Optuna continues to NOT tune it (not in `OPTUNA_SEARCH_SPACE_KEYS` — unchanged). Parity test: production and replay produce identical clamped stops for identical inputs incl. a floor-BINDING case; `test_c3_replay_exit_parity.py` lockstep extended.
- [ ] **AC-5 (behavioral golden — the noise-dip survives):** a squeezed position (para_armed) whose unclamped stop distance (e.g. 0.15 × 0.50 = 0.075 pp) is breached by a small noise dip that does NOT breach the clamped floor (0.20-capped-at-pre-squeeze) must NOT exit under the new code — and demonstrably DOES exit under the old (RED-on-old non-vacuity). Plus the converse: a dip breaching the clamped floor still exits (the floor protects against noise, not against genuine breakdown).
- [ ] **AC-6 (existing pins root-caused, not blind-made-green):** `test_either_flag_set_applies_squeeze_exactly_once` (tests/math_engine/test_active_trailing_stop.py:243-280) and fixture 12's derivation prose explicitly pin "no re-applied floor after the squeeze" — root-cause-update their contracts/docstrings for the new floor semantics (falsifier caveat 2: their NUMBERS still pass at defaults, so this is an honesty update, not a numeric fix). New RED fixtures where post-squeeze < floor (clamped) and where floor > pre-squeeze (no-widening).
- [ ] **AC-7 (metadata honesty — distance semantics):** `app.py:3536-3538` — help text stays distance-worded; `unit` "×" → "pp" (match MIN_STOP-style), `kind` "mult" → the pct/distance kind used by comparable params; `ai_advisor.py:165-172` definition re-worded to "floor on the post-squeeze stop DISTANCE (pp)"; risk-polarity "raising loosens risk" UNCHANGED (still correct: higher floor ⟹ wider stop ⟹ looser); the 0.05–0.50 range KEPT numerically (plausible as pp, brackets MIN_STOP 0.15–0.30) — re-based in wording only. Affected advisor/UI tests updated with root-cause verdicts.
- [ ] **AC-8 (docs + the deferred R3-b stamp):** DE-MATH-R3C-001 entry + charter MA-11 append (FIXED-note convention, historical prose preserved) + docs/generated updates; **fold in the deferred DE-MATH-R3B-001 SHIPPED+DEPLOYED stamp** (shipped @ f3c7e050, droplet-deployed + verified 2026-07-18, PM live E2E passed — supersedes its "pending PM live E2E" caveat).
- [ ] **AC-9 (scope guard):** TP-disarm, arm/disarm seam, VWAP, MC gating, `MAX_PARABOLIC_SQUEEZE` + its [0.1,0.8] Optuna range all UNTOUCHED (the range re-examination is R3-d's, noted in the DE only). `database.py:45` DEFAULT_STRATEGY seed value 0.20 unchanged. Zero diff outside the named files.

## Architecture
LIVE-execution-path change to `math_engine.py` (seam) + `alpha_bot_execution.py` (1-line pass-through) + `autotuner.py` (1-line pass-through) + metadata (`app.py`, `ai_advisor.py`) + docs.
- **Extend the ONE existing shared seam** `compute_active_trailing_stop` — r3c-scout §4 + falsifier C3 confirmed no duplication exists (unlike R3-b's disarm), so parity is automatic once both call sites pass the same value. NO new seam.
- **Optional-trailing signature** (falsifier caveat 2): `squeeze_floor: float | None = None`; None/`<=0` = no clamp (back-compat); reject non-finite via the existing `_reject_non_finite` idiom (M-2).
- **Settled clamp form** (PM rulings, do not re-litigate): squeeze-branch-scoped; `max(active*sq, min(floor, pre_squeeze_active))`; distance (pp) semantics.
- Constants: no new constants (the floor is the existing per-symphony param); no magic numbers introduced.

## Edge Cases
- `squeeze_floor` None / 0 / negative → no clamp, byte-identical legacy behavior (all existing callers).
- `squeeze_floor` > pre-squeeze active (defaults near close: 0.20 > 0.15) → clamps AT pre-squeeze (no widening), squeeze fully neutralized for low-vol symphonies — intended.
- High-vol symphony (large `safe_vol*mult`) → floor rarely binds; property test covers non-binding pass-through (`active*sq` > floor ⟹ unclamped).
- Non-finite floor (NaN/inf) → reject loudly (M-2 idiom), never coerce.
- `parabolic_squeeze_multiplier` ≥ 1 (hand-set) → existing rejection tests unchanged; clamp math still safe (min(floor, pre) ≤ pre ≤ active*sq case → max picks active*sq).
- Replay determinism: pure-fn extension, no I/O/state — R1/R2 replay-fidelity invariants hold.
- Legacy droplet `bot_state`/params lacking the key → `.get` defaults chain to the module const (0.20) identically in prod + replay.

## Security Considerations
No new external boundary/route/credential. The floor value is operator/advisor-writable through the EXISTING allowlisted, CSRF-protected paths — wiring it live means a written value now changes live exit behavior, which is the DESIGN INTENT of those guarded paths (advisor suggestions still pass the C2 gates; `POST /api/settings` still allowlist-enforced). Real risk is correctness: covered by goldens + parity + no-widening property + the PM live E2E + the operator before/after.

## Testing Strategy
- **RED (quant-test-writer, adversarial):** AC-1/2 seam goldens (clamped, non-binding, no-widening, None-passthrough, non-finite-reject) as JSON fixtures under `tests/fixtures/math_engine/`; AC-5 behavioral noise-dip golden RED-on-old; AC-4 parity incl. floor-binding; AC-6 root-cause rewrites; AC-7 metadata tests.
- **Non-vacuity:** the noise-dip golden must FAIL on the old (unclamped) code; the parity test must fail if only one site passes the floor; fixture 06 must survive UNCHANGED (proves squeeze-scoping).
- **PM battery (targeted `-n0`):** hot-file guards `tests/execution/` + `tests/math_engine/` + `tests/autotuner/` + `tests/error_handling/` + the advisor/UI surfaces touched (`tests/ai_advisor/` allowlist/metadata tests, `tests/app/` param-meta tests) + both ruff + credential-less. NEVER full/uncapped/-n>4.
- **PM live E2E + OPERATOR BEFORE/AFTER (committed):** first-hand bare-python drive of the squeezed noise-dip through the real primitives at production config, old-vs-new; **the PM presents a clear before/after artifact to the operator BEFORE the droplet deploy** (operator is at the keyboard; commitment made 2026-07-18).

## Decisions
| Decision | Rationale |
|----------|-----------|
| WIRE, not remove | **Operator ruling, verbatim "Wire it" (2026-07-18)** — realizes the design intent; guards against premature noise exits. |
| Option A — stop-DISTANCE floor (pp) | PM ruling on the delegated semantic fork: every operator-facing string is distance-worded; the multiplier reading is inert at defaults (max(0.50,0.20)) = a theater wire. Falsifier C6 arithmetic confirms only A binds. |
| Squeeze-branch-scoped clamp | Falsifier caveat 1: an always-on floor breaks the no-squeeze fixture 06 (0.001) and contradicts the "during Time Squeeze" wording. |
| No-widening invariant (`min(floor, pre_squeeze)`) | PM ruling: floor bounds SHRINKAGE; naive max() would make arming the squeeze WIDEN the stop near close (0.20 > 0.15), inverting its purpose. |
| Optional-trailing `squeeze_floor=None` param | Falsifier caveat 2: a required param TypeErrors every existing 6-arg call; optional keeps the blast radius to new tests only. |
| Replay default from `alpha_bot_execution.MAX_SQUEEZE_FLOOR` module attr | R1/F6 idiom — never a replay-local mirror; keeps prod⇄replay defaults structurally identical. |
| Advisor range 0.05–0.50 kept numerically, re-worded to pp | Plausible as pp (brackets MIN_STOP 0.15–0.30); range re-TUNING is R3-d's, not R3-c's. |
| MAX_PARABOLIC_SQUEEZE [0.1,0.8] range untouched | Charter assigns its re-examination to R3-d "once non-inert" — DE note only. |

## Scope Boundaries
- **IN:** the seam extension (AC-1/2) + two 1-line pass-throughs (AC-3/4) + behavioral/parity/property goldens (AC-5) + root-caused pin updates (AC-6) + metadata honesty (AC-7) + docs incl. the deferred R3-b stamp (AC-8).
- **OUT:** the R3-d retune; MAX_PARABOLIC_SQUEEZE range changes; any Optuna search-space change; removing/renaming the param; new constants; TP/disarm/VWAP/MC logic; `database.py` seed value changes; `.env.example` value changes (comment wording only if touched by AC-7).
