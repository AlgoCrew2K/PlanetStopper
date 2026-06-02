<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. Runtime/process findings: H-2 RT-01 resolved (reclassified as non-race in H-2 fix, see memory/project_adaptive_exit_direction.md); H-3 OQ-1 resolved as FAIL-OPEN (merged 2f67504). -->
# Pillar 4 — Runtime / Trading Process: Soundness + Safety Audit

**Auditor:** runtime-auditor (Agent Team audit-soundness)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2
**Scope:** minute scheduler -> subprocess spawn -> per-cycle execution -> exit decision -> order/dismiss path; the five hard architecture constraints; the fail-safe / never fail-open claim; _DISMISS_EXECUTOR + _FLUSH_STATE_LOCK race/partial-write hazards.
**Method:** static end-to-end trace. Live daemon NOT run (read-only mandate). Every concrete claim maps to file:line.

## Verdict (one line)
The out-of-process subprocess model is sound and keeps blocking I/O off the daemon scheduler thread, and four of the five architecture constraints hold. BUT the headline fail-safe promise is materially overstated: the trailing-stop ARMING gate is fail-CLOSED on missing MC data, contradicting the vision claim that the protective stop fires anyway. And there is one real cross-process lost-update race on the single-row bot_state JSON blob between the dashboard flush_resync writer and the live engine.

## A. Runtime trace (end-to-end, every hop cited)

1. Schedule: schedule.every().minute.at(:00).do(threaded_trigger) -- app.py:302
2. Fire (non-blocking): threaded_trigger spawns a daemon thread running trigger_alpha_bot -- app.py:222-223
3. Subprocess spawn: subprocess.run([sys.executable, alpha_bot_execution.py]) blocks the daemon thread, NOT the scheduler loop -- app.py:211-217
4. Overlap guard: if not database.acquire_lock(): return (DB-row advisory lock, 60s stale-expiry) -- alpha_bot_execution.py:593-595; database.py:182-196
5. Time gates: fully-closed gate, rebalance-blackout (15:53-16:00), action gate (EXECUTION_START_TIME), data gate (09:30 ET) -- alpha_bot_execution.py:613-666, 934-940
6. Data phase: Composer fetch_symphony_stats per account + Alpaca fetch_alpaca_history (cached same-day) warms HWM/vol -- alpha_bot_execution.py:722-745
7. Per-symphony math: MC run_monte_carlo -> regime-match guard -> arm/disarm -> para-arm -> time-squeeze -> active stop -> breakeven -> exit-confirm -> TP-confirm -> VWAP/bleed -- alpha_bot_execution.py:1249-1479
8. CVaR telemetry (synchronous, on-path): compute_portfolio_cvar + database.record_cvar_diagnostic -- diagnostic only, no trigger reads it -- alpha_bot_execution.py:1574-1609
9. Trigger resolve: resolve_trigger_priority(...) -> enqueue -- alpha_bot_execution.py:1611-1663; math_engine.py:836-859
10. Execution queue: chunks of 25, time.sleep(60) between chunks; per-item execute_sell_to_cash gated on LIVE_EXECUTION -- alpha_bot_execution.py:1670-1752
11. Order: requests.post(.../go-to-cash) with backoff [1,2,4,10] + 429 Retry-After -- alpha_bot_execution.py:257-294
12. State commit: single terminal save_state(bot_state) after fleet-correlation; release_lock() in finally -- alpha_bot_execution.py:1826-1848

The subprocess model means engine heavy/blocking I/O (Composer, Alpaca, MC, order POST, the 60s chunk sleeps) executes in a CHILD process, so it never blocks the Flask scheduler thread. This is the architecturally correct way to satisfy constraint 1, and it holds.

## B. Architecture-constraint scorecard

C1 No blocking I/O on the 1-min execution path -- PASS (daemon-level) with caveat. Engine runs out-of-process (app.py:211-217,222-223); blocking calls are inside the child. Caveat RT-04: a cycle slower than 60s makes the next-minute spawn hit the held lock and skip the tick (alpha_bot_execution.py:594); the 60s inter-chunk sleep (:1680) + per-order retry budget (~17s, :259) make this reachable when many symphonies fire.

C2 Dashboard read-only, never a live-trade action surface -- PASS for trades, PARTIAL for state. Manual-trigger disabled (app.py:1552-1556); no route calls execute_sell_to_cash or /go-to-cash. BUT flush_resync (POST) and fleet_alert_dismiss (POST) perform DB writes from the request surface (app.py:2081-2166, :1534-1549). flush_resync rewrites the live bot_state blob (see RT-01). Not a trade action surface, but IS a live-state action surface.

C3 Two-DB pattern, no cross-DB joins in app code -- PASS (not contradicted on this path). No cross-DB join observed on the traced path. State writes go through database.save_state/record_*; Optuna DB touched only by the autotuner (out of pillar scope). Not exhaustively proven across all of database.py (see ASSUMPTION-02).

C4 is_live=True explicit, never defaulted -- PASS. LIVE_EXECUTION defaults to safe DRY-RUN (alpha_bot_execution.py:69). Order send gated if LIVE_EXECUTION: (:1690); else branch sets success=True with DRY RUN Execution bypassed (:1750-1752). Mode toggle wipes transient state (:681-690).

C5 Templates open SQLite read-only; UI never reruns engine -- PASS. Read API routes use database.get_ro_connection() (app.py:1511,1505-1506). Engine re-run explicitly disabled (app.py:1554-1556).

## C. The fail-safe claim -- traced in code (most important finding)

Vision claim (audit/01-reconstructed-vision.md:32, README 4.7): It will fail safe, never fail open. If any supporting signal is unavailable, the protective stop still fires.

This is TRUE at the confirmation layer but FALSE at the arming layer. The trailing stop is a two-stage gate; the two stages handle missing MC data oppositely.

STAGE 1 -- ARMING (fail-CLOSED on missing MC). The ONLY path that sets armed=True requires mc_available (alpha_bot_execution.py:1292-1303):
    mc_available = prob_beating is not None
    if mc_available and acc_TAKE_PROFIT_MC_PCT <= prob_beating < acc_TRIGGER_THRESHOLD_PCT: should_arm = True
    if should_arm and not armed and not triggered: bot_state[sym][armed] = True
When MC is unavailable, should_arm stays False, so the symphony never arms.

STAGE 2 -- CONFIRMATION (fail-SAFE on missing MC). compute_exit_confirmation treats prob_beating is None as a passing sanity gate (math_engine.py:503-511):
    if (not armed) or is_triggered: return current_below_stop_count, False
    mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD
    below_stop_condition = (current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT) and mc_sanity_ok

THE CONTRADICTION: Stage 2 fail-safe is UNREACHABLE when Stage 1 never armed. Because arming requires MC, a position whose MC is PERMANENTLY unavailable -- insufficient history, OR the regime-match guard forcing prob_beating=None when today is unprecedented (alpha_bot_execution.py:1271-1272) -- NEVER arms, so the trailing stop never fires at all. The fail-safe docstring at math_engine.py:482-487 is correct GIVEN the position is already armed, but does not hold across a position whose MC was absent for its whole life.

Net safety picture when MC is missing:
- Trailing Stop: does NOT protect (cannot arm). GAP.
- Take-Profit: correctly disabled (an absent opinion is not an exceptional-gain signal); this one SHOULD be off -- sound (math_engine.py:553-559, :1417-1419).
- VWAP Breakdown (System A) and VWAP Bleed (System B): DO protect -- independent of armed and of MC; they fire on their own tick counters (math_engine.py:744-787, called :1464-1477).

So the operator is NOT wholly unprotected when MC is missing (VWAP/Bleed remain live), but the HEADLINE protective mechanism (the trailing stop) is silently off -- the opposite of the protective stop fires anyway. This is the single most important soundness gap on the runtime path. It is NOT a harmful over-exit/false-liquidation risk; it is the reverse -- a fail-to-exit/under-protection risk in exactly the regime (unprecedented/thin history) the vision says matters most (audit/01-reconstructed-vision.md:118,124).

Counter-direction check (over-exit on late/missing data):
- A missing/late prob_beating (None) RELAXES the MC veto but cannot itself create a below_stop_condition; the magnitude condition must independently hold AND the position must already be armed AND EXIT_CONFIRM_TICKS consecutive ticks must accrue (math_engine.py:509-516). A single late tick does not liquidate.
- VWAP grace window suppresses VWAP/Bleed for the first VWAP_OPEN_WINDOW_GRACE_MINUTES (alpha_bot_execution.py:1481-1486), guarding open-volatility false exits.
- Conclusion: over-exit risk on late data is LOW; confirmation-tick + magnitude floor bound it. The real asymmetry is under-protection, not over-exit.

## D. Race / partial-write hazards (_DISMISS_EXECUTOR, _FLUSH_STATE_LOCK, two-process state)

bot_state is a SINGLE JSON blob in one row (bot_state WHERE id=1), read-modify-written wholesale by load_state/save_state (database.py:211-225). No per-field update -- every writer does full-blob RMW. Two independent locking schemes guard it and DO NOT coordinate:
- Engine (subprocess): database.acquire_lock() row-advisory lock held across the whole cycle (alpha_bot_execution.py:593 -> release_lock() :1848).
- Dashboard (in-process): _FLUSH_STATE_LOCK threading.Lock (app.py:73), used by _flush_state_async (app.py:2146-2161).

### RT-01 (HIGH) -- cross-process lost-update race on bot_state via flush_resync
flush_resync background writer does load_state() -> mutate -> save_state() (app.py:2147-2161) WITHOUT calling database.acquire_lock(). The engine holds acquire_lock() but _flush_state_async never checks it. The two RMW cycles can interleave on the same single-row blob -> classic lost update. If an operator clicks flush-resync while a live cycle is mid-flight, the flush can overwrite freshly-triggered=True state (or the engine can overwrite the reset). HIGH because triggered is the double-charge guard -- clobbering it back to un-triggered while the position is actually in cash invites a re-evaluation next tick. _FLUSH_STATE_LOCK only serializes flush-vs-flush; ZERO protection against the out-of-process engine. Evidence: app.py:2144-2166 (no acquire_lock) vs alpha_bot_execution.py:593 / database.py:182-196.

### RT-02 (MEDIUM) -- acquire_lock is a non-atomic check-then-set
acquire_lock does SELECT is_locked then a separate UPDATE on its own connection (database.py:184-194) with no BEGIN IMMEDIATE enclosing read+write. Two near-simultaneous spawns could both read is_locked=0 and both proceed. The scheduler fires one spawn per minute so concurrent acquisition is unlikely, but the lock is ADVISORY and racy, not a true mutex -- and nothing outside the engine honors it (see RT-01). The 60s stale-expiry (:188) also lets a cycle exceeding 60s have its lock stolen by the next tick mid-write.

### RT-03 (LOW) -- _DISMISS_EXECUTOR single-worker pool; fire-and-forget writes
_DISMISS_EXECUTOR = ThreadPoolExecutor(max_workers=1) (app.py:65) serializes fleet_alert_dismiss (writes fleet_alert_state, a SEPARATE table, app.py:1539-1548) and _flush_state_async. max_workers=1 means dismiss and flush cannot run concurrently with each other (good). fleet_alert_dismiss targets a different table than the engine writes -- no bot_state collision. Risk LOW and confined to RT-01 path. The handler returns 200 OK before the background write lands (app.py:1548-1549, :2166) -- a write failure is logged, not surfaced; operator gets success for a write that may later fail.

### Partial-write integrity (engine side) -- SOUND
The engine funnels all triggered-field mutations into a SINGLE terminal save_state so a mid-loop crash cannot half-persist (alpha_bot_execution.py:932-933 comment all triggered fields in one write; :1826-1843). The narrow try/except around execute_sell_to_cash (:1713-1749) was added specifically to stop an executor exception unwinding past save_state and rolling back earlier iterations triggered=True (the documented double-charge bug, :1695-1701). Correct WITHIN the subprocess. Telemetry writes (record_exit_trigger :1766, record_cvar_diagnostic :1598) open their own connections and swallow failures -- never join the state transaction, so telemetry failure cannot corrupt state. Sound.

## E. Secondary observations

### RT-04 (MEDIUM) -- scheduler has no market-hours gate; fires every minute 24/7
run_scheduler registers threaded_trigger for every().minute.at(:00) with NO calendar/market gate at the scheduler level (app.py:301-307). A subprocess is spawned every minute, all day, every day; gating happens INSIDE the child (alpha_bot_execution.py:624-636) which then sleeps/returns. Functionally safe but spawns a full interpreter + load_state + (off-hours) a Composer fetch_symphony_stats round-trip every minute around the clock (:629-635). Wasteful, and the closed-market path still makes live Composer calls. Efficiency/blast-radius observation, not a correctness bug.

### RT-05 (LOW) -- _refresh_account_totals runs on the scheduler thread itself
Unlike threaded_trigger, _refresh_account_totals is registered directly (app.py:303) and executes requests.get(timeout=10) (app.py:269) ON the scheduler loop thread. A slow/hung Composer endpoint can stall run_pending() up to 10s each minute. Wrapped to never raise (:293-298) and timeout-bounded, but the one place a network call sits on the daemon own loop rather than a child. Worth noting against constraint 1 spirit.

### RT-06 (INFORMATIONAL) -- WAL/SHM persistence on Windows SIGTERM
Confirmed pre-existing/intentional per project memory project_wal_shm_persist_on_windows_sigterm and app.py:190-194: CPython converts SIGTERM to KeyboardInterrupt on native Windows, so _sigterm_handler cleanup is POSIX-only; atexit is the reliable Windows path. Not re-flagged as a defect per the memory directive.

## F. Open Questions (batched)

[ASSUMPTION-01] (Non-blocking): Live daemon not run (read-only mandate), so RT-01 interleave is shown by static code structure, not a reproduced lost-update. The structural absence of acquire_lock() in _flush_state_async is conclusive; the operational frequency (how often an operator flushes mid-cycle) is unquantified.

[QUESTION-01] (Non-blocking): Is flush_resync intended to be operable while the market is open / a cycle is live? If off-hours-only maintenance, RT-01 drops to LOW and the fix is a guard, not a lock. If clickable any time, RT-01 needs the engine row-lock (or a single shared lock).

[QUESTION-02] (Blocking for the fail-safe verdict): Is the trailing-stop arming-requires-MC behavior (Section C) INTENDED? The vision states the opposite. Either the vision wording is too strong (should read: VWAP/Bleed protect when MC is absent; the trailing stop does not arm without MC) OR the arming gate needs a fail-safe arming path when MC is permanently unavailable. Design-intent question -- synthesizer must route to PM; I cannot resolve which side is the spec.

[ASSUMPTION-02] (Non-blocking): Constraint 3 marked PASS as not-contradicted-on-traced-path. Did not exhaustively read every function in database.py; a full two-DB join scan is out of this pillar trace scope.

## G. Evidence appendix (file citation index)

- Scheduler / spawn / non-blocking: app.py:65,67,73,211-217,222-223,301-307
- Time gates: alpha_bot_execution.py:69,613-666,722-745,934-940
- MC arming (fail-closed): alpha_bot_execution.py:1249-1317
- Regime-match MC suppression: alpha_bot_execution.py:1267-1272
- Exit confirmation (fail-safe): math_engine.py:457-518
- TP confirmation: math_engine.py:525-588; alpha_bot_execution.py:1417-1439
- VWAP/Bleed (MC-independent protection): math_engine.py:684-789; alpha_bot_execution.py:1464-1486
- Trigger resolve: math_engine.py:826-859; alpha_bot_execution.py:1611-1663
- Order send (LIVE gate + backoff): alpha_bot_execution.py:257-294,1690-1752
- State commit / lock lifecycle: alpha_bot_execution.py:593-595,1826-1848; database.py:182-225
- Dashboard writers / race: app.py:1534-1549,2081-2179
- Read-only template/API surface: app.py:1505-1531,1552-1556

## H. Cross-track corroboration for OPT-INVALID-1 (requested by synthesizer)

optmethod-auditor found OPT-INVALID-1: _haircut_select hardcodes compute_sortino_tstat (autotuner.py:1251) even when the call site passes tstat_fn=compute_crra_eu_tstat. Blast radius hinges on the runtime objective_kind for the canonical THEORY bundle. RUNTIME TRACE CONFIRMS THE DEFECT IS LIVE, NOT THEORETICAL:

1. Production call chain: alpha_bot_execution.py:1096-1098 calls run_autotuner(..., spec_bundle_id=database.get_or_create_phase1_theory_bundle_id()). (Also app.py per the docstring at database.py:1197.)
2. The canonical Phase-1 THEORY bundle inserts ONLY three facets — gamma, utility_family, wealth_argument (database.py:1213-1217); NO explicit objective_kind facet. utility_family = PHASE1_THEORY_UTILITY_FAMILY = CRRA (database.py:1185).
3. run_autotuner derives objective_kind from the bundle facets (autotuner.py:1717-1733): _objective_kind = _facets_by_name.get(objective_kind, ) returns empty; the fallback at :1732-1733 then sets _objective_kind = crra_eu BECAUSE _utility_family.upper() == CRRA. So RUNTIME objective_kind == crra_eu for the canonical production bundle.
4. The crra_eu branch correctly selects _tstat_fn = compute_crra_eu_tstat (autotuner.py:1953-1954) and passes it: _haircut_select(haircut_trials, n_effective=n_eff, tstat_fn=_tstat_fn, gamma=_gamma) (autotuner.py:1976-1977).
5. BUT _haircut_select IGNORES tstat_fn: the loop body literally calls compute_sortino_tstat(series, seed=trial_idx) (autotuner.py:1251), never referencing the tstat_fn parameter. The parameter is dead (signature :1184; docstring promises Pass compute_crra_eu_tstat :1205 and Swapping tstat_fn is the ONLY permitted change :1206-1207). The CRRA gamma U-transform setup (_crra_gamma at :1243, per the CRRA-001 comment :1238-1242) is likewise computed but never applied before the tstat call.

VERDICT for synthesizer: OPT-INVALID-1 is LIVE in production. The selection-bias haircut t-statistic deployed for every canonical CRRA-EU symphony is computed with the Sortino-ratio t-stat (the H-6 category error the project explicitly tried to retire), NOT the intended CRRA-EU t-stat. The call-site routing is correct; the defect is entirely inside _haircut_select dropping its tstat_fn argument. RUNTIME-CONFIRMED-LIVE: alpha_bot_execution.py:1096-1098 -> database.py:1185,1213-1217 -> autotuner.py:1731-1733 -> :1953-1954 -> :1976-1977 -> :1251.

## I. Post-review refinement (QUESTION-02 reclassified: documented self-contradiction)

On a synthesizer cross-check I located explicit INTENT evidence for the fail-safe behavior. The comment directly above the arming logic (alpha_bot_execution.py:1284-1291) states the designer model verbatim: Insufficient MC = the MC second opinion is absent: no arm, no disarm, no TP -- and no MC veto of the trailing stop (compute_exit_confirmation handles None). The protective stop still fires on its ticks-below-stop condition alone.

This comment ASSERTS the fail-safe holds, but is internally self-contradictory: it says no arm AND the protective stop still fires. Because compute_exit_confirmation returns early when (not armed) (math_engine.py:503-504), a position that never armed cannot fire its stop. The author evidently believed the confirmation-layer None handling was sufficient for fail-safe and did not account for the arming gate at :1292 ALSO requiring mc_available.

RECLASSIFICATION: QUESTION-02 is therefore NOT a design question with no answer. The intent is unambiguous and documented (fail-safe / stop fires anyway); the code does not deliver it. This is a genuine BUG -- the code contradicts its own stated intent at :1284-1291 -- and should be ranked as a defect (fail-to-EXIT / under-protection), not merely a BLOCKING open question. The only item still needing PM sign-off is the FIX shape (add a no-MC arming path vs. accept VWAP/Bleed-only protection in the no-MC regime), not whether the behavior is intended. No test was found asserting the no-MC arming behavior in either direction.

RT-01 confirmation: full read of app.py:2081-2194 confirms flush_resync has NO market-hours gate, NO cycle-active check, and NO acquire_lock() in either the route or _flush_state_async (app.py:2144-2166). Only the in-process _FLUSH_STATE_LOCK (flush-vs-flush) guards it. QUESTION-01 off-hours-only branch does NOT apply -- no such gate exists in code. RT-01 stays HIGH.
