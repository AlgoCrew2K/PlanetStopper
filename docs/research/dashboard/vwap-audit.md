# VWAP system audit — operational anomaly + math correctness

**Date:** 2026-05-15
**Author:** risk-engine-specialist (read-only audit; no code changes)
**Scope:** All 11 symphonies showed `triggered=True` / `triggered_reason="VWAP Breakdown"` on the live dashboard. Audit determines whether this is stale state, a genuine signal, or a logic bug; full audit of the VWAP math surface follows.

**TL;DR — verdict for the operator:**

| Item | Verdict |
|------|---------|
| Are today's 11 triggers stale state from yesterday? | **NO** — ruled out by SQLite + chart_history + symphony_logs evidence |
| Are today's 11 triggers a market-wide real signal? | **CONFIRMED** — all 11 satisfied the documented state-machine in real-time today |
| Is the underlying VWAP math correct against its inline producer spec? | **CORRECT** (every formula matches the verbatim pre-extraction inline producer, with strict-boundary semantics pinned by golden fixtures) |
| Is there a logic bug that flips `triggered` outside the gated path? | **NO** — `triggered=True` is set in exactly ONE site (`alpha_bot_execution.py:936`), inside the action-phase success branch |
| Is there a **threshold-sensitivity problem** causing all 11 to fire simultaneously? | **YES — this is the real finding.** System A (VWAP profit-protection break) gates at `safe_hwm >= 1.0%` and confirms in 3 ticks. With the strong open today (~10:30 ET) all 11 symphonies crossed HWM ≥ 1% within minutes; market-wide retracement of < 1% on each then satisfied `current_return < safe_hwm` for 3 consecutive cycles. The trigger is doing exactly what the code says — but the code's calibration is much more aggressive than typically assumed |

---

## Section 1 — Part A: operational anomaly diagnosis

### 1.1 Direct evidence from live state

Pulled directly from `alphabot_state.db` (SQLite, `bot_state` row id=1) at audit time:

```
date                     = "2026-05-15"
last_execution_mode      = False                          (LIVE_EXECUTION off — DRY RUN)
last_successful_cycle_at = 2026-05-15T10:54:01.583291-04:00
post_mortem_run          = "2026-05-14"                   (yesterday's EOD ran)
```

All 11 symphonies show:

```
triggered            = True
triggered_reason     = "VWAP Breakdown"
triggered_at_time    = "10:35" or "10:36"  (TODAY, not yesterday)
high_water_mark      = -999.0               (sentinel — set by execution success branch)
vwap_ticks           = 3                    (exactly the confirm-ticks threshold)
vwap_bleed_ticks     = 0                    (Bleed System B never armed today)
```

The `triggered_at_time` field (`alpha_bot_execution.py:941`) and `triggered_at_return` were last written today, after the new-day wipe ran. Per-symphony `triggered_at_return` values are positive (+0.92% to +3.29%), consistent with the dashboard.

### 1.2 Chart history corroborates today's trigger sequence

`chart_history` row (date="2026-05-15") shows for each of the 11 symphonies:
- First action-phase cycle at 10:30 ET (matches `EXECUTION_START_TIME=10:30` from `.env`)
- All 11 PARA-ARMED at 10:30 cycle (velocity ≥ 2% over 1 cycle — strong open ratchet)
- HWM ratcheted to its day-high within the first 1–2 cycles (e.g. one symphony hit HWM=3.53 at 10:31)
- `vwap_diff < 0` from the first cycle — every symphony's allocation-weighted current price was already below intraday VWAP at 10:30
- `current_return < safe_hwm` on the 3rd–5th cycle as returns mean-reverted from the open spike
- `event = "VWAP Breakdown"` recorded at 10:35 or 10:36 cycle

### 1.3 Symphony event log corroborates

`symphony_logs.json` shows the exact transition pair for each of the 11 symphonies:

```
2026-05-15T14:30:09Z  para-armed   PARA-ARMED (Velocity: ≥2.0%)
2026-05-15T14:35:04Z  triggered    VWAP BREAKDOWN HIT FOR <symphony>. Level: <hwm>
or
2026-05-15T14:36:04Z  triggered    VWAP BREAKDOWN HIT FOR <symphony>. Level: <hwm>
```

(`14:30Z` ET = 10:30, `14:35Z` ET = 10:35 — UTC offset confirmed for ET DST.) The 5–6 minute interval between PARA-ARMED and triggered is consistent with: 1 cycle to set HWM, 2 cycles to advance `vwap_ticks` from 0→1→2→3.

### 1.4 Hypothesis verdicts

**Hypothesis 1 — stale state persistence from yesterday: RULED OUT.**
- Yesterday's `post_mortem_2026-05-14.json` shows all 11 symphonies triggered yesterday for `exit_reason="Trailing Stop"` at `time_triggered="14:44"` (not VWAP Breakdown). Today's reasons are different, today's times are different — these are NEW triggers, not stale ones.
- The new-day wipe path (`alpha_bot_execution.py:373-378`) detected `bot_state.get("date") != current_date_str` at the first cycle of today and called `database.wipe_transient_state(bot_state)` (`database.py:134`), which sets `triggered=False`, `vwap_ticks=0`, `vwap_bleed_ticks=0`, `high_water_mark=-999.0`, and DELETES the trigger-snapshot keys for all symphonies.
- Evidence the wipe DID run: `chart_history.date` is `"2026-05-15"` (was overwritten by line 384 of execution); chart entries start fresh at 10:30; `vwap_ticks=3` (would be much higher if yesterday's count had survived); the symphony_logs.json events start fresh at 14:30Z today.

**Hypothesis 2 — real market-wide event: CONFIRMED.**
- All 11 symphonies registered velocity ≥ 2.0% over 1 cycle at 10:30 ET (PARA-ARMED). This is a market-wide open-spike followed by intraday mean-reversion.
- All 11 had `vwap_diff < 0` from the first cycle — i.e., they were trading below VWAP across the day's accumulated volume. The first cycle's VWAP integrates from 09:30 ET (4 hours before action phase opens at 10:30, given `start_et = current_et.replace(hour=9, minute=30, ...)` at `alpha_bot_execution.py:240`). The open spike pushed prices well above the 9:30 VWAP, then prices dropped back toward VWAP as the spike faded — exactly the pattern that produces negative `vwap_diff` AND a strong HWM that current_return cannot hold.
- The Bleed System B did NOT fire on any of the 11 (`vwap_bleed_ticks=0`). The threshold (per-symphony, dynamic) was something like `-2.3%` for sym_vol=1.55 — returns stayed positive all day. System A (profit-protection break) was the trigger source for every one.

**Hypothesis 3 — over-sensitive threshold / logic bug: PARTIAL.**
- No logic bug. There is exactly ONE call site that sets `triggered=True` in `alpha_bot_execution.py` (line 936, inside `if success:` after the execution-queue chunk loop). That branch is reachable only when the action-phase loop appended an item to `execution_queue` at line 836, which is gated by `if is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken:` (line 819). All four of those signals derive from `math_engine` pure functions called inside the action phase. Verified via `grep`.
- The DATA phase (`alpha_bot_execution.py:393-477`) never sets `triggered`. It refreshes Composer fields, advances HWM, recomputes `symphony_vol`, and writes state — but the action-phase `is_vwap_broken` flag is never computed in the data phase.
- **HOWEVER, there is a threshold-sensitivity finding.** System A's gate is `safe_hwm >= VWAP_CROSS_HWM_PCT` (default = 1.0%). Once any symphony's intraday HWM crosses 1%, System A is permanently armed for the day (HWM is monotonic at line 458). A 1% intraday move is trivial. The confirmation window is only 3 cycles (~3 minutes). When the open is strong-and-fading on a market-wide basis, this design produces simultaneous trips across the entire fleet — there is no fleet-level decorrelation. **This is what the operator should focus on**: not a bug, but a calibration choice with portfolio-wide blast-radius.

---

## Section 2 — Part B: VWAP math audit (file:line evidence)

### 2.1 `compute_vwap_signals` (`math_engine.py:280-321`)

**Formula:**
```
weighted_vwap_diff += allocation * ((last_price - vwap) / vwap)   # line 319
valid_vwap_weight  += allocation                                  # line 320
```

- **Sign convention:** `(p - v) / v` is the **fractional** deviation of price from VWAP. Positive when price > VWAP, negative when price < VWAP. Consumer's gate (`compute_vwap_breakdown_update` line 426) demands `weighted_vwap_diff < 0` — i.e., the portfolio is trading below VWAP on a weighted basis. Sign is consistent.
- **Division order:** `(p - v) / v` first, THEN multiplied by `allocation`. This is the same evaluation order as the original inline producer (commented on line 319). Reordering to `allocation * (p - v) / v` (without parentheses) would change float behavior at the IEEE-754 limit — DO NOT touch.
- **Units:** Fractional (decimal). `0.005` here means "0.5% above VWAP." NOT percentage points. The chart history reflects this: today's typical vwap_diff was `-0.008` to `-0.015` (i.e., -0.8% to -1.5% — well within the gate's `<0` requirement).
- **Normalization:** The raw sum is passed through to the consumer **un-normalized by `valid_vwap_weight`**. The consumer compares `weighted_vwap_diff < 0` strictly — sign-only — so normalization does not change the gate result. But if you ever consume `weighted_vwap_diff` as a magnitude (you don't yet, but if you start charting it as "% below VWAP"), it will under-report by the missing-coverage factor. **Flag for future work, not a current bug.**
- **Edge: ticker missing from `live_vwaps`** (line 314) — skipped silently. The missing holdings contribute zero to BOTH accumulators. This means a holding with 30% allocation that's missing VWAP data drops both the numerator and denominator by 0.30. The gate (`valid_vwap_weight > 0.5`) is the safety check: if too much allocation is missing, no signal is emitted. **Correct.**
- **Edge: `vwap <= 0` rejected** (line 318) — same behavior as missing ticker: both accumulators skip. **Correct.**
- **Edge: non-finite prices** — rejected at entry by `_reject_non_finite` (line 306). **Correct.**
- **Allocation-sum dilution risk:** with one holding missing, the surviving holdings' weights do NOT re-normalize. A symphony that's 50%-covered would emit a `weighted_vwap_diff` whose magnitude is roughly half of what a fully-covered symphony with the same per-ticker deviations would emit. The downstream gate uses strict `<` against zero, so dilution doesn't change a sign — but it does affect the MAGNITUDE for any future magnitude-sensitive consumer. **Documented hazard.**

### 2.2 `compute_vwap_bleed_arm_threshold` (`math_engine.py:329-355`)

**Formula:**
```
raw = -(symphony_vol * bleed_multiplier)
result = max(VWAP_BLEED_ARM_MIN, min(VWAP_BLEED_ARM_MAX, raw))
where VWAP_BLEED_ARM_MIN = -3.0 (line 325)
      VWAP_BLEED_ARM_MAX = -0.5 (line 326)
```

- **Sign convention:** Always negative (clamp window is `[-3.0, -0.5]`). Pinned by `tests/math_engine/test_vwap_bleed_arm.py:53-55`.
- **Units of `symphony_vol`:** Percentage points. Source: `math_engine.py:550` — `daily_returns = returns_matrix.dot(weights) * PCT_SCALAR`, then `np.std(daily_returns)` at line 555. So a `symphony_vol = 1.55` from yesterday's post-mortem means "1.55 pp daily-return standard deviation." With default `bleed_multiplier = 1.5` (line 559 of `alpha_bot_execution.py`), raw = `-(1.55 * 1.5) = -2.325`. In range, so result = `-2.325`. **Interpretation: bleed arms when current_return ≤ −2.3%.**
- **Units of clamp endpoints:** Percentage points. Source: name (`VWAP_BLEED_ARM_MIN/MAX`) matches `current_return` units, where `current_return = sym.get("last_percent_change", 0.0) * 100` (`alpha_bot_execution.py:565`).
- **Default multiplier:** `1.5` (line 559) — `acc_params.get("VWAP_BLEED_MULTIPLIER", 1.5)`.
- **Clamp endpoint semantics:**
  - `VWAP_BLEED_ARM_MIN = -3.0` is the "most permissive arm" — a symphony with very high vol (e.g., sym_vol=5.0 × multiplier=1.5 = 7.5, clamped at -3.0) will not arm bleed unless return drops to -3%. This makes sense: high-vol symphonies are EXPECTED to swing wider, so the cut should be deeper.
  - `VWAP_BLEED_ARM_MAX = -0.5` is the "most cautious arm" — a symphony with very low vol (e.g., sym_vol=0.2 × multiplier=1.5 = 0.3, clamped at -0.5) will arm bleed at -0.5%. Low-vol symphonies shouldn't normally swing this far; if they do, exit.
- **Out-of-clamp signal interpretation:** A raw value below -3.0 means vol×multiplier was huge — usually high vol (~2.0+ pp daily). The clamp prevents the arm threshold from being TOO permissive. A raw value above -0.5 means vol×multiplier < 0.5 — usually low-vol cash-like baskets. The clamp prevents the arm threshold from being too cautious. **Neither endpoint masks a data issue; they implement an explicit risk policy.**

### 2.3 `compute_vwap_breakdown_update` (`math_engine.py:363-445`)

**State-machine has THREE outer branches:**

1. **BRANCH 1: `is_triggered=True`** (line 423) — return input counters unchanged, both signals False. The function does NOT advance or reset counters when already triggered. This is intentional: once triggered, the engine should be in shadow-tracking mode, and the math signals are dormant.
2. **BRANCH 2: gate fails** (line 426) — `valid_vwap_weight > 0.5 AND weighted_vwap_diff < 0` is the gate. If EITHER part is False (i.e., not enough allocation coverage OR portfolio is above VWAP), BOTH counters RESET to 0. Both signals False.
3. **BRANCH 3: gate passes** — run System A and System B INDEPENDENTLY (lines 429-443).

**System A (profit-protection break):**
- Condition: `safe_hwm >= vwap_cross_hwm_pct AND current_return < safe_hwm` (line 430)
- Met → `new_vwap_ticks = current + 1`, `is_vwap_broken = (new_vwap_ticks >= VWAP_BREAK_CONFIRM_TICKS)` (default 3, line 360)
- Miss → `new_vwap_ticks = 0`, signal False
- **Boundary `safe_hwm >= 1.0`** uses `>=` — cross-exact arms (pinned by test).
- **Boundary `current_return < safe_hwm`** uses strict `<` — equal to HWM does NOT trigger (so the cycle when HWM is set does NOT immediately tick).

**System B (bleed):**
- Condition: `current_return <= vwap_bleed_arm_pct` (line 438) — `<=` (inclusive)
- Met → `new_vwap_bleed_ticks = current + 1`, signal if `new >= acc_VWAP_BLEED_TICKS` (default 10, line 560)
- Miss → reset to 0

**Boundary correctness:** all 5 boundary semantics in the function docstring (lines 404-408) are pinned by golden fixtures in `tests/fixtures/math_engine/vwap_breakdown/`. Verified.

**`safe_hwm` input:** Computed at `alpha_bot_execution.py:632` as `high_water_mark if high_water_mark != -999.0 else current_return`. This handles the post-trigger sentinel correctly — if HWM has been zeroed by a prior trigger this day (which would now be wiped by new-day reset), falls back to current_return.

**`is_triggered` input:** Passed from `bot_state[symphony_id]['triggered']` at line 755. Verified: feeding `True` short-circuits to BRANCH 1, counters preserved. The consumer correctly passes the LIVE state, so a symphony already triggered this day will NOT advance counters on subsequent cycles.

**Bleed counter independence:** System A and System B are TWO separate `if/else` blocks (lines 430-435 vs 438-443) operating on TWO separate state fields. They share the outer gate but otherwise do not interact. Confirmed.

**Edge `valid_vwap_weight < 0.5` no-signal:** The task brief described this as `< 0.5`, but the code uses STRICT `>` (line 426) for the gate — so exactly `0.5` does NOT pass. Verified by docstring (line 404: "Gate weight uses strict `>` (0.5 exact does NOT pass)") and by `tests/math_engine/test_vwap_breakdown.py`. When the gate fails, counters RESET (not preserved). This is the correct behavior for "no-signal," BUT note: reset-on-gate-fail is DIFFERENT from preserve-on-gate-fail. A symphony that gates-out for a single noisy cycle loses all accumulated ticks. **Operator should be aware: this is conservatively defensive against false positives but is somewhat lossy.**

### 2.4 Trigger-set call sites (audit)

Searched `alpha_bot_execution.py` for every place that sets `triggered=True`:

```
$ grep -n 'triggered.*=.*True' alpha_bot_execution.py
881: comment only
936: bot_state[sym_id]["triggered"] = True   ← THE ONE AND ONLY assignment
```

Line 936 is inside `if success:` (line 933), which is reachable only when:
1. Action-phase loop has appended an item to `execution_queue` (line 836)
2. The append is gated by `if is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken:` (line 819)
3. All four signals are returns from pure-function math layer calls
4. In DRY-RUN mode, `success=True` is hardcoded (line 931); in LIVE mode, it's the return from `execute_sell_to_cash` (line 897). Either way, the gate at line 819 must have passed.

**The DATA phase (lines 393-477) cannot set `triggered=True`.** It never computes `is_vwap_broken` or related signals; it only refreshes Composer stats, HWM, sym_vol, and current_holdings. The data-vs-action split from commit 46fe019 is correctly isolating action-only side effects to the action phase.

### 2.5 State persistence semantics for `triggered`

| Lifecycle event | Effect on `triggered` |
|---|---|
| Cycle write within same day, post-trigger | `triggered=True` is persisted at `alpha_bot_execution.py:979` via `database.save_state(bot_state)` |
| New trading day (`bot_state["date"] != current_date_str`) | `database.wipe_transient_state(bot_state)` at line 376 → sets `triggered=False`, `vwap_ticks=0`, `vwap_bleed_ticks=0`, deletes trigger-snapshot keys (`database.py:134-158`). Runs on the FIRST cycle of a new ET calendar day |
| Execution-mode toggle (`LIVE_EXECUTION` env-var changes between cycles) | Same wipe path at line 366 |
| Daemon restart | `bot_state` loaded from SQLite — `triggered=True` survives restart for the current trading day (correct) |
| Post-mortem run | Sets `bot_state["post_mortem_run"] = current_date_str` at line 505, but does NOT touch any symphony's `triggered` field — the trigger persists for the rest of the day to suppress re-entry |

The new-day wipe is the ONLY way `triggered` gets cleared. **This is correct.** The wipe is gated by `date != current_date_str`, and `date` is updated to `current_date_str` immediately after (line 375). So the wipe runs once per ET calendar day, then `triggered` accumulates again during that day.

**Operator clarity:** `triggered=True` at 10:48 ET today will persist all the way until the FIRST cycle after midnight ET tomorrow (or, if the daemon was paused over the weekend, until the first cycle after midnight Monday). Until then, the dashboard will continue to show `triggered=True` for these 11 symphonies — that is correct, not a bug.

### 2.6 Test coverage analysis (cross-reference)

- `tests/math_engine/test_vwap_breakdown.py` — golden fixtures and properties for the state-machine. Pins all 5 boundary semantics. Strong coverage.
- `tests/math_engine/test_vwap_signals.py` — golden fixtures for the deviation aggregator. Pins inline-producer semantics.
- `tests/math_engine/test_vwap_bleed_arm.py` — golden fixtures for the clamp + sign convention.
- `tests/database/test_wipe_state.py` — pins `wipe_transient_state` resets every transient key, deletes trigger-snapshot keys, preserves non-transient keys. Strong coverage.
- `tests/execution/test_data_action_split.py` — pins the data-phase / action-phase boundary. Most tests stub `wipe_transient_state.side_effect = lambda s: s` (identity) to focus on phase isolation. **GAP: no integration test exercises "yesterday triggered → today fresh" end-to-end through the real wipe path.**
- `tests/execution/test_reliability_expansion.py` — same identity-stub pattern for the wipe.
- **GAP: no test exercises the "11 symphonies trigger simultaneously" scenario** — the engine would behave correctly (the math is per-symphony and stateless across symphonies), but a fleet-wide property test would have caught the calibration sensitivity earlier.
- **GAP: no test exercises the cross-day persistence of a still-triggered symphony being cleared by the next day's first cycle** — this is the most operationally important untested path.

---

## Section 3 — Recommendations (DO NOT IMPLEMENT — operator approval required)

### 3.1 Calibration review (highest priority)

The math is correct against its spec. The OPERATIONAL ISSUE is that the spec is aggressive:
- `VWAP_CROSS_HWM_PCT` default = `1.0` (in `.env` it's currently unset → falls through to `1.0` default at `alpha_bot_execution.py:51`).
- `VWAP_BREAK_CONFIRM_TICKS` = `3` (constant at `math_engine.py:360`).
- With a market-wide morning gap-up, EVERY positive-correlation symphony will:
  1. Push HWM ≥ 1% within 1–2 cycles
  2. Trade below intraday VWAP (because VWAP integrates from 09:30 ET and the open spike is the first bar)
  3. Mean-revert ≤ HWM for 3 cycles
  4. Trigger System A within 5 minutes

Consider:
- Raising `VWAP_CROSS_HWM_PCT` to e.g. 2.0% or 3.0% to require a more substantial profit envelope before profit-protection arms
- Raising `VWAP_BREAK_CONFIRM_TICKS` to 5 or 7 to require a more sustained breakdown
- Adding a **time-of-day gate**: do not allow System A to arm during the first 30 minutes of the action phase (open-volatility window) — the open's VWAP-vs-price imbalance is a known artifact, not a real risk signal
- Adding a **fleet-decorrelation circuit-breaker**: if N >= 5 symphonies all signal `is_vwap_broken` in the same cycle, defer all of them by 1–2 cycles and re-evaluate (because correlated false-positives are more likely than 5 simultaneous true regime breaks)

### 3.2 Test additions (gate the calibration change behind these tests)

Add to `tests/execution/`:

1. `test_cross_day_trigger_clearance.py` — seed bot_state with `triggered=True` for symphony X, set `bot_state["date"]` to yesterday, run main, assert symphony X has `triggered=False` and `triggered_at_*` keys deleted after wipe. End-to-end through the real (non-stubbed) `wipe_transient_state`.

2. `test_eleven_simultaneous_vwap_break.py` — synthetic fixture with 11 symphonies, all with HWM ≥ 1.5% and current_return = HWM - 0.3 for 3 cycles, gate passing on all 11. Assert all 11 produce `is_vwap_broken=True`. Then (after a calibration-change PR) re-run the same fixture with the new constants and document the new expected behavior. This is the regression test that pins today's calibration.

Add to `tests/math_engine/`:

3. `test_vwap_signals_dilution.py` — property test: same per-ticker deviations, vary the missing-ticker percentage from 0% to 49% coverage missing, assert that `weighted_vwap_diff` magnitude scales linearly with `valid_vwap_weight`. Documents the dilution behavior so any future consumer that depends on magnitude knows the contract.

### 3.3 Documentation additions (no code change)

- Add to `docs/runbooks/`: an entry "What `triggered=True` means and when it clears." Currently the operator has to read code to know this; promote it to a runbook.
- Add to `docs/math_engine/`: a one-pager describing the System A vs System B distinction (profit-protection break vs bleed cut) with the threshold formulas spelled out in operator-friendly language.

### 3.4 No code-correctness fixes recommended

The math IS correct against its spec. No formula change is recommended. Every constant has a source comment. Every boundary semantic is pinned by a golden fixture. Recommendation 3.1 is a CALIBRATION decision for the operator, not a correctness fix.

---

## Section 4 — Open questions / unverified items

Items I could not determine read-only and what's needed to close them:

1. **Why are cycles at 10:33, 10:34 missing from `chart_history`?** The action-phase append site (line 807) runs on every action-phase cycle, so a gap implies either: (a) the cycle was skipped by the daemon scheduler, (b) the lock was contended (`database.acquire_lock` at line 288 returned False), or (c) the action phase returned early. Reading `alphabot_daemon.log` would resolve this — outside the scope of static audit. **Speculation, not finding.**

2. **Is `data_phase_history` cached correctly across data-phase + action-phase calls?** The action phase at line 528 re-calls `fetch_alpaca_history(...)` which checks the cache by date+tickers (line 155). If the data phase populated the cache, the action phase should be a cache hit. But if the data phase's `all_tickers` set DIFFERS from the action phase's (which would happen if some symphonies are triggered and thus only contribute frozen tickers, not Composer-holdings tickers), the cache key won't match and Alpaca will be re-fetched. **Speculation, not finding.** Verification needs a wire-trace.

3. **Live VWAP minute-bar coverage at 10:30 ET:** the first action-phase cycle was at 10:30, but `fetch_intraday_vwaps` integrates minute bars from 09:30 ET (line 240). Some tickers may have had thin or zero minute-bar volume in the 09:30 → 10:30 hour, producing `vwap` values dominated by a few large bars (and thus large `(p - v)` discrepancies). **Speculation.** A snapshot of `live_vwaps` at the 10:30 cycle would confirm; it is not persisted to SQLite.

4. **Did the new-day wipe actually run today, or did the persistence flow rely on a fresh bot_state from yesterday's EOD?** The first cycle today would have seen `bot_state["date"] == "2026-05-14"` (yesterday) and `current_date_str == "2026-05-15"` (today), tripping line 374 and calling `wipe_transient_state`. After the wipe, `bot_state["date"]` becomes `"2026-05-15"`. But I cannot confirm from static state alone WHEN the wipe ran — only that the state I observed is consistent with a successful wipe followed by 11 fresh triggers. The first cycle's log line (`"New trading day detected (2026-05-15 ET). Wiping transient state keys and chart memory."`) would be in `alphabot_daemon.log`, which I have not read.

5. **Is the parabolic-arm "PARA-ARMED at velocity ≥ 2% over 1 cycle" actually load-bearing here?** PARA-ARM doesn't change the VWAP gate — it only multiplies the ATR-stop distance via `active_trailing_stop` (line 687). But all 11 symphonies fired PARA-ARMED at the 10:30 cycle, which means velocity from `prev_return` (set by prior day's last cycle, line 673) to today's first cycle's `current_return` was ≥ 2%. **This is a separate concern**: cross-day velocity uses yesterday's final return as `prev_return`, which is wiped to `0.0` by `database.py:140`. So `velocity = current_return - 0.0 = current_return ≥ 2.0%` for any symphony opening above 2% today. **That's not a velocity signal — that's a "opened above 2%" signal.** A wipe of `prev_return=0.0` means the first cycle of every day always reports `velocity = current_return`, which guarantees PARA-ARMED for any symphony opening >= the threshold. **Flag for a separate audit.** This is speculation pending a read of the para-arm test fixtures.

---

## Files referenced (absolute paths)

- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\math_engine.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alpha_bot_execution.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\database.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\alphabot_state.db` (read-only inspection)
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\post_mortem_2026-05-14.json`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\symphony_logs.json`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_vwap_signals.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_vwap_bleed_arm.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\math_engine\test_vwap_breakdown.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\database\test_wipe_state.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\execution\test_data_action_split.py`
- `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\tests\execution\test_reliability_expansion.py`
