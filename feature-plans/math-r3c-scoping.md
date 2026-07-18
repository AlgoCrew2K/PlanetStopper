# R3-c / MA-11 MAX_SQUEEZE_FLOOR — scoping report (r3c-scout, 2026-07-18) — feeds feature-plans/math-r3c.md

> Persisted by the PM from r3c-scout's returned report. All citations verified file:line against `.claude/worktrees/math-r3c` @ **f3c7e050** (fork base == origin/main, post-R3-b). READ-ONLY scope — NOT a fix proposal.

**Headline:** The dead-knob claim is TRUE and proven from source. The squeeze math already lives in ONE shared pure seam (`math_engine.compute_active_trailing_stop`) called by BOTH production and replay — so a WIRE fix is parity-clean via the existing seam (no new seam needed). The single biggest decision is NOT wire-vs-remove first — it's resolving a **semantic conflict in what MAX_SQUEEZE_FLOOR is supposed to floor** (stop-distance vs multiplier), because the two readings produce very different live-behavior magnitudes.

## 1. Every occurrence + consumed-vs-assigned
59 code hits across 23 `.py` files; rest are docs/fixtures. Production surfaces:
- `alpha_bot_execution.py:89` — module default `MAX_SQUEEZE_FLOOR = float(os.getenv("MAX_SQUEEZE_FLOOR","0.20"))`. Only use: fallback in the `.get` at :1236.
- `alpha_bot_execution.py:1236` — `acc_MAX_SQUEEZE_FLOOR = acc_params.get("MAX_SQUEEZE_FLOOR", MAX_SQUEEZE_FLOOR)`. **ASSIGNED, NEVER READ AGAIN** (grep `acc_MAX_SQUEEZE_FLOOR` = exactly ONE code hit). Never passed to `compute_active_trailing_stop` (:1467), never in any min/max/clamp. **Dead knob confirmed.**
- `database.py:45` — `DEFAULT_STRATEGY["MAX_SQUEEZE_FLOOR"]=0.20`. Seeds per-symphony params; never consumed by squeeze math.
- `ai_advisor.py:115`/`:165-172`/`:192`(range 0.05–0.50)/`:1384`/`:1979`(allowlist union)/`:1397`. Advisory surface only.
- `app.py:3535-3539` `_ALGO_PARAM_META` UI metadata → in `_SETTINGS_WRITE_ALLOWLIST` (:3628-3634) → writable via `POST /api/settings`; `app.py:5686` dev-only demo.
- `.env.example:122` `MAX_SQUEEZE_FLOOR=0.20`.
- **Consumed by squeeze math anywhere? NO.** Fully operator/advisor-wired, zero engine consumption.

## 2. Squeeze computation + exact missing-clamp line
`math_engine.compute_active_trailing_stop` (:456-499):
```
495  safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
496  active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)   # floored at dynamic_min_stop
497  if para_armed or breakeven_locked:
498      active *= parabolic_squeeze_multiplier                       # TIGHTENS below the floor
499  return float(active)                                             # <-- NO re-floor here
```
Squeeze tightens at :498; the missing clamp (`active = max(active, squeeze_floor)`) belongs at :498/:499. Signature (:456-463) has NO floor param. sqrt-time curve `1-sqrt(1-t)` in `compute_time_squeeze_decay` (:450).

## 3. Min-stop floor the squeeze bypasses + real-money consequence
- Floor = `dynamic_min_stop`, interpolated `MIN_STOP_OPEN=0.3 → MIN_STOP_CLOSE=0.15` pp (`math_engine.py:261-262`, computed :452).
- Bypass: :496 floors BEFORE the multiply; :498 multiplies by `parabolic_squeeze_multiplier` (default 0.50, Optuna range 0.1–0.8) → effective stop drops FAR below `dynamic_min_stop` (e.g. 0.15 × 0.1 = 0.015 pp).
- **Consequence:** when para_armed OR breakeven_locked, the trailing-stop DISTANCE collapses → stop sits on the high-water mark → **premature exit on the first noise tick**. Wiring the floor makes squeezed-regime stops WIDER (fewer premature exits).

## 4. MA-4-style duplication / parity — GOOD NEWS
- Squeeze/floor math is **NOT duplicated** — ONE shared seam `math_engine.compute_active_trailing_stop`, called by production `alpha_bot_execution.py:1467-1476` AND replay `autotuner.py:1246-1253`.
- Both sites can source the floor per-symphony identically (production `acc_MAX_SQUEEZE_FLOOR`; replay `p.get("MAX_SQUEEZE_FLOOR", default)` via `autotuner.py:2480/2440`).
- **Verdict:** no new seam — EXTEND the existing one (add optional `squeeze_floor` param, clamp post-squeeze, pass from both sites). Parity automatic. `tests/autotuner/test_c3_replay_exit_parity.py` guards lockstep; add a floor-binding case.

## 5. Optuna + advisor + UI surfaces
- **Optuna:** NOT in `OPTUNA_SEARCH_SPACE_KEYS` (`autotuner.py:157-166`). Charter's "[0.1,0.8] squeeze range" = `_SS_MAX_PARA_SQUEEZE_MIN/MAX` (`autotuner.py:308-309`) on **MAX_PARABOLIC_SQUEEZE** (the multiplier), NOT the floor — needs R3-d re-examination once the floor binds.
- **Advisor:** suggestible — `_SUGGESTIBLE_ALLOWLIST = _OPTUNA_SEARCH_SPACE_KEYS | {MAX_SQUEEZE_FLOOR}` (`ai_advisor.py:1979`); range 0.05–0.50; polarity "raising loosens risk".
- **UI/allowlist:** `_ALGO_PARAM_META` (`app.py:3535`) → `_SETTINGS_WRITE_ALLOWLIST`; `.env.example:122`; dev demo :5686.

**⚠ SEMANTIC CONFLICT — resolve before implementation. What does MAX_SQUEEZE_FLOOR floor?**
- **Option A — stop-DISTANCE floor (pp).** Human-facing text `app.py:3536` "tightest the stop distance can shrink"; units 0.20 pp comparable to `dynamic_min_stop`. `active = max(active*squeeze, floor)`.
- **Option B — MULTIPLIER floor (dimensionless).** `ai_advisor.py:167` "floor on the squeeze **multiplier**"; `app.py:3537` `unit="×"`. `eff = max(parabolic_squeeze_multiplier, floor)`.
- Dominant human-facing intent = **Option A**; the name + `×` unit muddy toward B; magnitudes differ sharply. Either reading needs a small UI text/unit cleanup.

## 6. Existing tests — classification
- **Pin the 7-item allowlist/suggestibility/persistence** (BREAK on REMOVE → root-cause rewrite; SURVIVE on WIRE): `tests/ai_advisor/test_advisor_cleanup_cycle.py:372`, `test_cycle1_multilens_foundation.py:803-814` (allowlist == exactly 7), `test_ai_advisor_safety.py`, `tests/app/test_ai_advisor_tab.py:475`, `tests/security/test_a1_settings_allowlist.py:351`, `tests/ui/test_cycle_6_settings.py`, `tests/database/test_symphony_strategy.py`, + fixtures.
- **WIRE — math blast radius** (`tests/math_engine/test_active_trailing_stop.py`, 13 fixtures + property tests): all current squeeze fixtures output ≥ 0.6 (> 0.20 default) so they survive numerically, BUT fixture 12 + `test_either_flag_set_applies_squeeze_exactly_once` + docstrings EXPLICITLY assert "min_stop floor NOT re-applied after squeeze" — semantic dead-knob pins to root-cause-update + add RED fixtures where post-squeeze < floor.
- **Signature strategy decides blast radius:** OPTIONAL trailing `squeeze_floor` (default None/0.0 = no floor) → all existing calls survive; only new RED exercises the floor. REQUIRED/positional breaks all. **Recommend optional-trailing.**

## 7. WIRE vs REMOVE — evidence (PM decides)
**(a) WIRE** — post-squeeze lower clamp: add `squeeze_floor` to signature; after :498 clamp `active = max(active, squeeze_floor)` INSIDE the `if para_armed or breakeven_locked:` block (unconditional would raise the non-squeezed stop + break the no-flags property test). Pass `acc_MAX_SQUEEZE_FLOOR` (prod) + `p.get(...)` (replay). **Changes live exit behavior: YES** — squeezed-regime stops get clamped wider, fewer premature exits; magnitude depends on the §5 A/B choice (Option A binds materially at default squeeze; Option B barely binds at default). Must land BEFORE R3-d retune. Realizes design intent. Needs golden root-cause + PM live E2E + operator awareness.
**(b) REMOVE** — delete `alpha_bot_execution.py:89`+`:1236`, `database.py:45`, `ai_advisor.py` surfaces (allowlist 7→6), `app.py:3535-3539`+`:5686`, `.env.example:122`; rewrite the allowlist/UI tests. **Purely internal — NO live exit-behavior change, zero live-money risk.** Does NOT satisfy the charter's design intent; leaves the squeeze able to collapse the stop with no lower bound.
**Lower-risk:** REMOVE (no live-path change). WIRE realizes design intent but is a live exit-behavior change needing golden root-cause + live E2E + operator sign-off + a MAX_PARABOLIC_SQUEEZE range re-examination. Both entail a small UI text/unit fix regardless.

---

## ADDENDUM — r3c-falsifier adversarial verification (2026-07-18, @ f3c7e050)
Per Rule-7 the PM dispatched an independent falsifier briefed to REFUTE this report. **All 8 claims CONFIRMED with file:line evidence** (C1 dead knob — `acc_MAX_SQUEEZE_FLOOR` has exactly ONE hit repo-wide, the :1236 assignment; C2 no post-squeeze clamp, no floor param in the :456-463 signature; C3 ONE shared seam, no inline replay duplicate; C4 replay params carry the key passively, Optuna never suggests it; C5 all three surfaces (advisor allowlist :1979/:2026, `_ALGO_PARAM_META` :3535-3539 → `_SETTINGS_WRITE_ALLOWLIST`, .env.example:122); C6 semantic conflict verbatim-quoted — and at defaults ONLY the distance-floor reading binds (post-squeeze distance 0.075–0.15 < 0.20; multiplier reading max(0.50,0.20) inert); C7 fixture-12 + `test_either_flag_set_applies_squeeze_exactly_once` (:243-280, docstring :257-258) explicitly pin no-post-floor; C8 R3-b's old disarm gone, seam live both sites). **Bottom line: SAFE basis for the WIRE build; distance-floor (Option A) confirmed as the human-facing intent** (all operator-facing strings distance-worded; multiplier framing is the minority view AND inert at defaults).

**Three build caveats (bound into the plan):**
1. **Floor scoped INSIDE the squeeze branch** (`if para_armed or breakeven_locked:`) — fixture 06 (`06_symphony_vol_tiny_positive_not_clamped`, expected 0.001, no-squeeze) breaks under an always-on floor; the "during Time Squeeze" wording mandates squeeze-scoped placement.
2. **Optional/internally-defaulted signature param** — a new REQUIRED param TypeErrors every existing 6-arg call; and `test_either_flag_set_applies_squeeze_exactly_once` + fixture-12 prose must be root-cause-updated for honesty even where their numbers still pass (their documented contract — "no re-applied floor after squeeze" — is exactly what the WIRE changes).
3. **Metadata reconciliation to distance semantics** — `app.py:3537-3538` unit "×"/kind "mult" wrong for a distance floor (→ pct, like MIN_STOP); `ai_advisor.py:166-171` "floor on the squeeze multiplier" wording + the 0.05–0.50 range need re-wording/re-basing to pp (range is numerically plausible as pp, comparable to MIN_STOP 0.15–0.30); risk-polarity "raising loosens" stays correct under distance semantics.

**PM ruling folded in (no-widening invariant):** at defaults the floor (0.20) EXCEEDS `dynamic_min_stop` near close (0.15) — a naive `max(squeezed, floor)` would WIDEN the stop above its pre-squeeze value, inverting the squeeze. The help text frames the floor as a bound on SHRINKAGE ⟹ the settled form is `active = max(active*squeeze, min(squeeze_floor, pre_squeeze_active))` inside the squeeze branch — clamps shrinkage, never widens.
