# Feature: Daily regime label producer for the live trailing-stop confirmation
Status: ready
Story: none: the PM's defect finding of 2026-11-03, saved verbatim with the intake answers in feature-plans/regime-label-producer.source.md
Type: fix
Suite: none
Repos: AlphaBotPM
Base: dead-table-partial-and-header-css (branch feat/regime-label-producer at ff161d72, which carries that slice)
Ref: AlphaBotPM@ff161d72cb6efc73dcc51ef1cc5e1aa12c1b44cc
Sensitive: money (AC-1, AC-2, AC-3, AC-4, AC-5, AC-6)
Depends on: the dead-table-partial slice, already in this branch's base (G-22)

## Trigger
The PM found that the regime label cache the live engine reads has never been filled: `database.save_regime_label` has no production caller and the droplet's `regime_label_cache` table holds 0 rows (PM live read, 2026-11-03). The source and the PM's intake answers are saved verbatim in `feature-plans/regime-label-producer.source.md` and quoted in Requirements as Given (G-1 to G-27).

Known facts at the ref this plan is written on:
1. The engine resolves the trailing-stop confirmation count per symphony per tick as `math_engine.apply_regime_exit_adjustment(regime_label=database.get_cached_regime_label(symphony_id), base_ticks=math_engine.EXIT_CONFIRM_TICKS)` (`alpha_bot_execution.py:1526-1529`), where `symphony_id` is the Composer hash `sym["id"]` (`alpha_bot_execution.py:1239`), the same key the stored bot state uses (`alpha_bot_execution.py:792-796`).
2. The engine's read passes no staleness argument, so `get_cached_regime_label` returns any stored label whatever its date (`database.py:1780-1781`). Its existing `staleness_cutoff_days` parameter counts UTC calendar days: 0 rejects every label and N accepts a label up to N days old (`database.py:1783-1796`), so no value of it means "only the session the label was computed for".
3. `apply_regime_exit_adjustment` maps trending to 2, mean-reverting to 5 and high-vol to 3 ticks, and None or any other string to `base_ticks` (3) (`math_engine.py:733-744`, `math_engine.py:779-785`).
4. The autotuner replay resolves each replay day's count from `regime_classifier.classify_regime` over the last `MIN_LABEL_SERIES_LENGTH` (20, `regime_classifier.py:63`) replay days strictly before the day, reading each day's last-tick `return` and dividing by `RETURN_PCT_TO_FRACTION` (100.0, `autotuner.py:727`) (`autotuner.py:1630-1660`). The replay's "days" are the dates present in the history dict it is given (`autotuner.py:1503`, `autotuner.py:1975`), and a date is present only when the symphony's first stored holding has 1-minute IEX bars that UTC date and the day produced ticks (`synthetic_history.py:806-807`, `synthetic_history.py:838-840`); a skipped session makes the window reach further back. A day's ticks are indexed by the first holding's 1-minute bars (`synthetic_history.py:809`). The last tick's `return` is, times 100, the allocation-weighted sum over the stored holdings of each holding's change from its previous daily close (`synthetic_history.py:794-798`) to the close of its 1-minute bar at the same position as the first holding's last bar; a holding with fewer bars that day adds nothing (`synthetic_history.py:414-427`, `synthetic_history.py:474`). The day value is therefore not a close-to-close return: IEX 1-minute bars exist only for minutes with an IEX trade, so the last tick often falls before 16:00 ET, and the holdings are aligned by bar position, not by minute (audit evidence: a local cache file of 11 symphonies showed a median of 7 to 18 ticks per day for 9 of them and a last tick before 15:50 ET on 39 to 105 of 250 days each, and none after 15:59). The replay applies the holdings stored in bot state at generation time to every day of its history (`synthetic_history.py:508-513`, `synthetic_history.py:804-822`).
5. `classify_regime` returns None for fewer than 20 finite values, `high-vol` when the sample standard deviation of the fractions exceeds 0.04, else `trending` for positive lag-1 autocorrelation and `mean-reverting` otherwise, including a constant series (`regime_classifier.py:91`, `regime_classifier.py:110-144`).
6. `regime_label_cache` has `symphony_id` as primary key and `label` and `as_of_date` NOT NULL (`migrations/027_regime_label_cache.sql:13-18`); `save_regime_label` upserts one row per symphony (`database.py:1717-1742`).
7. The daemon registers its off-hours jobs in `run_scheduler` at 02:00, 03:00, 03:30, 03:45 and 04:15 host time (`app.py:2747-2762`); 04:15 is the model warm-up. The 03:30 and 03:45 jobs return at once and do their work on a daemon thread (`app.py:1926-1935`, `app.py:2629-2646`). The code's own text calls the 03:45 slot "03:45 UTC" (`app.py:7078`).
8. The engine's 16:00 ET post-mortem pass rewrites `current_holdings` for every symphony Composer reports (`alpha_bot_execution.py:1037-1047`), so the stored holdings at an overnight run are the ones Composer reported at the last close.
9. The droplet runs with `LIVE_EXECUTION` false (PM live read), so on the droplet the count changes simulated triggers only.

## Summary
Every trading session, each symphony's trailing stop confirms after the number of ticks its regime label calls for, using a label computed off-hours from that symphony's trailing daily close-to-close returns as the design specifies, for the operator whose live exits then follow the regime rule the autotune tunes under; three replay-side gaps remain and are named (the replay's day value is not a close-to-close return; its window's members are the dates in its history, not the sessions the job counts, so the two windows hold different sessions; and its validation and frozen-fold head days run at the default count; R-3, R-6).
- A daily off-hours job writes each symphony's label for the next trading session (AC-1).
- The label is computed on the design basis (daily close-to-close returns of the stored holdings) over a 20-session trailing window of computable sessions, with no lookahead (AC-2).
- The engine applies the session's label to the trailing-stop confirmation count (AC-3).
- A label not computed for the current session is never applied; the engine uses the default count (AC-4).
- A symphony the job cannot label stays unlabelled without stopping the others (AC-5).
- The job stays off the 1-minute path, off every route and away from every order path, and the protected modules stay unchanged (AC-6).
- Each run logs one line per symphony and one summary line (AC-7).

## Requirements as Given
[G-1] Part 1, operator standing rule for logic/methodology calls (verbatim): "it should do what it was designed to do" regime-label-producer.source.md:2
[G-2] Part 2, design record in code, database.py save_regime_label docstring at the ref (verbatim): "Persist the most-recent offline regime label for a symphony (latest wins). regime-label-producer.source.md:3
[G-3] Called by the OFFLINE daily job after running regime_classifier.classify_regime over the symphony's daily return series. regime-label-producer.source.md:3
[G-4] The live 1-minute execution path never calls the classifier; it reads this cached row via get_cached_regime_label." regime-label-producer.source.md:3
[G-5] Part 3, alpha_bot_execution.py comment at the engine's read (verbatim): "Phase 3c — regime-conditional exit lever (single knob: exit_confirm_ticks). regime-label-producer.source.md:4
[G-6] The regime label is read from the PRECOMPUTED daily cache (offline diagnostic — never calls classify_regime() on this 1-minute path)." regime-label-producer.source.md:4
[G-7] Part 4, the PM's defect finding (2026-11-03, verbatim): "database.save_regime_label has zero production callers; the droplet's regime_label_cache table has 0 rows (read-only query SELECT COUNT(*), MAX(as_of_date) FROM regime_label_cache returned (0, None)). regime-label-producer.source.md:5
[G-8] So the live engine's get_cached_regime_label always returns None and apply_regime_exit_adjustment always returns EXIT_CONFIRM_TICKS = 3. regime-label-producer.source.md:5
[G-9] Since Math Remediation R1 (DECISIONS.md AC-4/F5, 2026-07-17) the autotuner replay recomputes the regime label per replay day from trailing daily returns strictly before the day and applies 2 (trending), 5 (mean-reverting) or 3 (high-vol). regime-label-producer.source.md:5
[G-10] Live and replay therefore disagree: the weekly autotune tunes parameters under an exit rule the live engine never applies. regime-label-producer.source.md:5
[G-11] Before R1 both used 3. regime-label-producer.source.md:5
[G-12] Build the missing off-hours daily producer so live reads a fresh label computed on the replay's basis, with no lookahead and never on the 1-minute path; a failed producer must fall back to the default, not to a stale label." regime-label-producer.source.md:5
[G-13] (Intake, Building 1) Makes true: on every trading day each live symphony's exit confirmation uses the regime label the design specifies, computed off-hours from that symphony's trailing daily returns on the same basis and window as the autotuner replay, so live and replay apply the same exit rule. regime-label-producer.source.md:10
[G-14] (Intake, Building 1) Trigger: Part 4. regime-label-producer.source.md:10
[G-15] (Intake, Building 2) Surface: an off-hours daily scheduler job inside the daemon (app.py run_scheduler, beside the existing 03:00 / 03:30 / 03:45 ET slots); the consumer is the engine's existing per-tick cached read. regime-label-producer.source.md:11
[G-16] (Intake, Building 2) No page, route or button. regime-label-producer.source.md:11
[G-17] (Intake, Building 3) Screens and flows: none for an operator; the job runs unattended. regime-label-producer.source.md:12
[G-18] (Intake, Building 3) Each run writes one log line per symphony (label or the reason there is none). regime-label-producer.source.md:12
[G-19] (Intake, Building 4) Negatives: a symphony with fewer trailing trading days than regime_classifier.MIN_LABEL_SERIES_LENGTH gets no label (engine default 3); a failed price fetch or any error for one symphony gets no label for that symphony and does not stop the others; a run that does not happen (daemon down, job error) must not leave a stale label in force (default 3 instead); a repeated run on the same day replaces the day's labels idempotently; weekends and NYSE holidays: the label applies to the next trading day; a symphony added after the run has no label until the next run; a symphony removed keeps no effect; the job never runs on the 1-minute path and adds no I/O to the tick beyond the existing cached read; the engine's behaviour with no label is exactly today's. regime-label-producer.source.md:13
[G-20] (Intake, Building 5) Data and contracts: the existing regime_label_cache table (migration 027: symphony_id primary key, label, as_of_date, updated_at) and database.save_regime_label / get_cached_regime_label; whether the engine's read must pass a staleness rule (an engine-file change) or the producer guarantees freshness is a design question (Open Questions). regime-label-producer.source.md:14
[G-21] (Intake, Building 5) No new config key unless required. regime-label-producer.source.md:14
[G-22] (Intake, Building 6) Repos: AlphaBotPM only; lands after the dead-table-partial slice (already on this branch's base). regime-label-producer.source.md:15
[G-23] (Intake, Building 7) Sensitive: yes (live exit timing; money). regime-label-producer.source.md:16
[G-24] (Intake, Not building 1) No change to regime_classifier.classify_regime, its thresholds, math_engine.apply_regime_exit_adjustment, the 2/5/3 tick table, EXIT_CONFIRM_TICKS, or the autotuner replay; no dashboard display of the label; no backfill of past days; no Optuna retune; no change to any other exit layer. regime-label-producer.source.md:18
[G-25] (Intake, Not building 2) Must stay: the engine's decisions when no label exists; the 1-minute path's I/O; the replay's no-lookahead rule (replay never reads the live cache). regime-label-producer.source.md:19
[G-26] (Intake, Not building 3) Deferred: showing the regime label on the dashboard; a retune once live and replay agree. regime-label-producer.source.md:20
[G-27] (Intake, Not building 4) Consumed, not changed: regime_classifier.py, math_engine.apply_regime_exit_adjustment, autotuner._replay_resolve_regime_exit_ticks, synthetic_history's replay basis, migration 027's schema (an additive migration only if the design truly needs one). regime-label-producer.source.md:21

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | An off-hours daily job inside the daemon computes and stores each live symphony's regime label | G-3, G-7, G-12, G-14, G-15 | AC-1 | full |
| R-2 | The label is `classify_regime`'s output over the symphony's daily return series | G-3 | AC-2 | full |
| R-3 | The return series uses the replay's basis and window | G-9, G-12, G-13 | AC-2 | narrowed: per PM ruling D-5, each session's value is the design basis (allocation-weighted close-to-close return from consolidated SIP daily closes, D-11), not the replay's own day value (the first holding's last 1-minute IEX bar with positional matching, built from IEX bars, Known fact 4); the window length (20), units, holdings and strictly-before rule match the replay, but window membership does not: the job counts NYSE sessions on which every holding has both SIP closes, within 30 sessions (D-8), while the replay counts the dates in its history, which exist only when the first holding has 1-minute IEX bars and have no look-back bound (`autotuner.py:1651`, `synthetic_history.py:806-807`, `synthetic_history.py:838-840`); labels agree with the replay only where the day values and the window members coincide (S-2.12; Known-Unconfirmed 1) |
| R-4 | No lookahead: only sessions strictly before the labelled session count | G-9, G-12 | AC-2 | full |
| R-5 | The engine reads the label through its existing cached read and applies 2, 5 or 3 ticks | G-4, G-5, G-6, G-8 | AC-3 | full |
| R-6 | Live and replay apply the same exit rule | G-10, G-11, G-13 | AC-2, AC-3 | narrowed: both apply the same label-to-count rule and the same window length (20), but not the same window members, and (a) the replay's day value is not the design's close-to-close return, so a label can differ near a threshold (Known-Unconfirmed 1, D-5), and (b) the replay's validation and frozen-fold head days resolve the default count, because those paths hand the replay fold-sliced history dicts whose first 20 days have no trailing window (`autotuner.py:2529-2535`, `autotuner.py:2940-2945`, `autotuner.py:2993-2999`, `autotuner.py:3012-3018`; Known-Unconfirmed 10, D-7), and (c) window membership differs (R-3; Known-Unconfirmed 1); fixing the replay is excluded by G-24 (Follow-Ups) |
| R-7 | The job never runs on the 1-minute path and adds no tick I/O beyond the existing cached read | G-4, G-6, G-12, G-19 | AC-6, NB-9 | full |
| R-8 | A failed producer, or a run that does not happen, leaves the default count in force, never a stale label | G-12, G-19 | AC-4, AC-5 | full |
| R-9 | Fewer trailing sessions than `MIN_LABEL_SERIES_LENGTH` gives no label (default 3) | G-19 | AC-5 | full |
| R-10 | A failure for one symphony leaves it unlabelled and does not stop the others | G-19 | AC-5 | full |
| R-11 | A repeated run for the same session replaces that session's labels idempotently, latest wins | G-2, G-19 | AC-1 | full |
| R-12 | On weekends and NYSE holidays the label applies to the next trading day | G-19 | AC-1 | full |
| R-13 | A symphony added after the run has no label until the next run | G-19 | AC-4 | full |
| R-14 | A removed symphony's label has no effect | G-19 | AC-4 | full |
| R-15 | With no label the engine behaves exactly as at the ref | G-19, G-25 | AC-3, NB-8 | full |
| R-16 | Each run writes one log line per symphony, with the label or the reason there is none | G-18 | AC-7 | full |
| R-17 | The existing table and accessors are used; no new config key unless required; an additive migration only if truly needed | G-20, G-21, G-27 | AC-1, NB-6, NB-18 | full |
| R-18 | Where freshness is enforced (the engine's read or the producer) is a design question | G-20 | AC-4 | full |
| R-19 | No page, route or button; the job runs unattended | G-16, G-17 | AC-6, NB-1 | full |
| R-20 | No change to `classify_regime`, its thresholds, `apply_regime_exit_adjustment`, the 2/5/3 table, `EXIT_CONFIRM_TICKS` or the replay | G-24, G-27 | AC-6, NB-15, NB-16, NB-17 | full |
| R-21 | No dashboard display of the label; deferred | G-24, G-26 | NB-13 | full |
| R-22 | No backfill of past days | G-24 | NB-3 | full |
| R-23 | No Optuna retune; a retune is deferred until live and replay agree | G-24, G-26 | NB-4, NB-14 | full |
| R-24 | No change to any other exit layer; the label moves one knob | G-5, G-24 | AC-3, NB-16 | full |
| R-25 | The replay's no-lookahead rule stays: the replay never reads the live cache | G-25 | AC-6, NB-10 | full |
| R-26 | AlphaBotPM only, landing after the dead-table-partial slice | G-22 | NB-21 | full |
| R-27 | Live exit timing is money-sensitive | G-23 | AC-3, AC-4 | full |
| R-28 | The cache does what it was designed for: an offline daily job fills it and the live path only reads it | G-1, G-3 | AC-1, AC-3 | full |

G-12 and G-13 also carry clauses other than the basis. R-1 (an off-hours daily producer), R-4 (no lookahead), R-7 (never on the 1-minute path) and R-8 (a failed producer falls back to the default) map those clauses, which this plan keeps whole, so they read `full`. The "replay's basis" clause of G-12 and G-13 is carried by R-3 and R-6, which are narrowed with the gaps named (D-5, D-7). [PM-ASSUMED] accepted by the PM.

## User Tasks
none: no screen; the job runs unattended (G-16, G-17).

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (including the `.completed.md` and `.obsolete.md` families and the suite folders), `DECISIONS.md`, `docs/generated/*.md`, `docs/audit/` and `docs/audit-inputs/` for `regime_label`, `get_cached_regime_label`, `save_regime_label`, `regime_label_cache`, `regime-conditional`, `exit_confirm_ticks` and the daemon's daily slot times, and read the tests that encode the Phase 3c cache contract (its design file, PHASE3_DECISIONS.md, is not in the repository; Known-Unconfirmed 8).

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/math-r1.md` AC-4 (F5: the replay resolves the same regime-dependent count production resolves; an absent label degrades to production's default) | `autotuner.py:1630-1660` | kept | The replay is unchanged (S-6.4); this plan makes production resolve labels at all, which AC-4 assumed |
| `feature-plans/math-r1.md` AC-6 (replay and production parity battery, including regime-tick-variation days) | the exit confirmation in both paths | kept | S-2.12 checks parity with the replay where the two day values coincide; the shared confirmation logic is unchanged |
| `feature-plans/math-r2.md` AC-4 (the undated search path reuses `_replay_resolve_regime_exit_ticks`) | `autotuner.py:1503-1522` | kept | No replay change (S-6.4) |
| `DECISIONS.md` DE-MATH-R1-001 ADDENDUM 2 (replay code never reads `get_cached_regime_label`; `DECISIONS.md:6268-6284`) | `autotuner.py` | kept | S-6.3 keeps the existing zero-reference scan |
| `DECISIONS.md` DE-MATH-R1-001 ADDENDUM 6 ("production computes it from true trailing calendar days", `DECISIONS.md:6816-6825`) | the live label and the replay's regime window | kept | For production, at the ref no computation exists (G-7) and this plan makes the statement true. For the replay, the code meets the addendum's "true global chronology" ruling only on the CPCV path (`_cpcv_history = history_train`, `autotuner.py:2607`); the validation and frozen-fold paths pass fold-sliced dicts (`autotuner.py:2529-2535`), so their first 20 days resolve the default count. This plan does not change the replay (G-24); the gap is Known-Unconfirmed 10 and a Follow-Up |
| `tests/execution/test_regime_cache_isolation.py` contract items 1 and 2 (engine and `math_engine` never import `regime_classifier`) | `alpha_bot_execution.py`, `math_engine.py` | kept | S-6.2 |
| `tests/execution/test_regime_cache_isolation.py` item 11 (`:354-368`: the accessor with no cutoff returns a label whatever its age) | `database.get_cached_regime_label` | kept | The Q-4 default puts the same-session rule on the engine's read; if Gate 2 places it in the accessor's default instead, this row becomes changed and the approach raises it to the PM before approval |
| `feature-plans/mdd-window-truth.md` AC-7 and its pin `tests/test_mdd_window_truth_zero_diff_guard.py:72-73` (engine and `math_engine` byte-unchanged), with `tests/engine/test_close_snapshot_builder_scope.py:28` (engine unchanged outside the close-snapshot builder; re-pin only on the PM's word) | `alpha_bot_execution.py`, `math_engine.py` | kept | `math_engine.py` stays pinned (S-6.4); the engine pins stay unless Q-4's answer edits the engine's read call, which re-pins both with the PM's approval (Q-4) |
| `feature-plans/strategy-incubation-gate.md` scheduling (a daily slot whose wrapper spawns a daemon thread and returns) | `app.py` `run_scheduler` | kept | S-6.7 requires the same non-blocking shape |
| `feature-plans/retirement-visibility.md` (03:45 tick) and the 04:15 model warm-up slot (`app.py:2762`) | `app.py:2747-2762` | kept | The new slot takes a free time (Q-3); NB-12 keeps every existing slot |

## Acceptance Criteria (Building)

### AC-1: Each trading session gets a label row written before it opens
Requirement items: R-1, R-11, R-12, R-17, R-28
Rule: When the daemon's daily regime label slot fires, the regime label job shall write to `regime_label_cache`, for each symphony entry in the stored bot state that has current holdings, one row keyed by the symphony's Composer hash (the key the engine reads with, `alpha_bot_execution.py:1239`, `alpha_bot_execution.py:1527`) whose label is the label AC-2 defines and whose `as_of_date` is the target session D in YYYY-MM-DD form, D being the first NYSE trading day (`market_calendar.is_trading_day`) whose 09:30 ET open is after the instant the job starts; and the job shall write no row at or after D's 09:30 ET open, so a session's count never changes once the session has begun.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the stored bot state holds symphonies A and B, keyed by Composer hash, each with current holdings; price data covers at least 21 completed sessions before trading day D | the slot fires on the night before D | `regime_label_cache` holds one row for A and one for B, keyed by the hash, each with `as_of_date` D and a label among trending, mean-reverting and high-vol equal to the label AC-2 defines for that symphony |
| S-1.2 | N1 | as S-1.1, the run instant is a Saturday or a Sunday in ET | the slot fires | `as_of_date` is the following Monday, or the first trading day after it when that Monday is an NYSE holiday |
| S-1.3 | N1 | as S-1.1, the run instant is the night before a weekday NYSE holiday | the slot fires | `as_of_date` is the first trading day after the holiday, and the AC-2 window holds no holiday date |
| S-1.4 | N1 | as S-1.1, the host clock is UTC and the run is at 04:30 host time, once in EST (ET 23:30 on the day before D) and once in EDT (ET 00:30 on D) | the slot fires in each case | both runs write `as_of_date` D |
| S-1.5 | N1 | as S-1.1, the job starts after 09:30 ET on trading day D | the job runs | `as_of_date` is the next trading day after D, never D, because D's session has opened |
| S-1.6 | N5 | the job already wrote the rows for D | the job runs a second time for D with the same stored state and prices | each symphony still has exactly one row, dated D, with the same label; only `updated_at` is later |
| S-1.7 | N5 | A and B have rows dated the previous trading day | the run for D completes | each row carries `as_of_date` D and D's label; no row dated the previous trading day remains for A or B |
| S-1.8 | N1 | the stored bot state also holds the five top-level metadata keys `date`, `last_execution_mode`, `last_market_close_snapshot`, `last_successful_cycle_at` and `post_mortem_run` | the slot fires | no row and no per-symphony log line exist for any of them |
| S-1.9 | N7 | as S-1.1 | the slot fires | the stored bot state, every other table and the post-mortem files are unchanged; the job's only database write is to `regime_label_cache` |
| S-1.10 | N8 | the job started before D's 09:30 ET open with D fixed, and is still in progress (a slow price fetch) when D's session opens; A was written before the open and B was not | the job finishes B after 09:30 ET | A keeps its row dated D; B gets no row dated D and its log line carries the reason `after_open`; on D the engine requires 3 ticks for B for the whole session (AC-4) |

Negative coverage: N1 S-1.2, S-1.3, S-1.4, S-1.5, S-1.8 · N2 n/a: the job answers no request, so it has nothing to refuse · N3 covered by AC-5 (S-5.2, S-5.3, S-5.7) · N4 covered by AC-6 (S-6.5) · N5 S-1.6, S-1.7 · N6 covered by AC-5 (S-5.1, S-5.4, S-5.5) · N7 S-1.9 · N8 S-1.10
Questions: Q-3, Q-9, Q-10, Q-12

### AC-2: The label uses the design basis over 20 computable sessions (not the replay's window members), with no lookahead
Requirement items: R-2, R-3, R-4, R-6
Rule: When the regime label job computes symphony S's label for session D, the job shall pass `regime_classifier.classify_regime` S's daily returns as decimal fractions for the `MIN_LABEL_SERIES_LENGTH` (20) most recent NYSE sessions strictly before D, within the 30 sessions before D, on which S's return is computable, each return being the allocation-weighted close-to-close return of S's stored current holdings from the consolidated (SIP) daily closes from Alpaca's daily-bar request in the shape the replay's daily fetch uses (`/v2/stocks/bars`, `timeframe=1Day`, `adjustment=split`) but with `feed=sip` (`synthetic_history.py:304-367` is the replay's request with `feed=iex`), requested with an end bound given as an explicit UTC timestamp at least 15 minutes before the request (D-12), a session being computable when every holding has a SIP daily close on that session and on the session before it.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | captured-shape SIP daily bars for S's two holdings covering 21 computable sessions before D | the job labels S for D | the series the job passes `classify_regime` equals, within 1e-12, the allocation-weighted close-to-close returns the test computes from the fixture's closes for the 20 most recent sessions, as fractions, and the row's label equals `classify_regime` of that series |
| S-2.2 | B | three price histories whose return series are those of `tests/fixtures/math/regime_classifier/` `trending_basic.json`, `mean_reverting_basic.json` and `high_vol_stressed_basic.json` | the job labels each | the rows carry trending, mean-reverting and high-vol respectively, each equal to `classify_regime`'s own output for that series |
| S-2.3 | N1 | a price history whose daily returns have a sample standard deviation of 0.02 as fractions (2 in percent) | the job labels S | the label is not high-vol: the 0.04 threshold (`regime_classifier.py:91`) is compared with fractions |
| S-2.4 | N1 | the price source also returns a daily bar dated D (partial or complete) and bars after D | the job labels S for D | the series ends with the session before D; the label equals the label computed with those bars removed |
| S-2.5 | N1 | 25 computable sessions before D | the job labels S | the series has exactly 20 values; changing the closes of the 4 oldest sessions leaves the series and the label unchanged |
| S-2.6 | N1 | S holds two tickers with allocations 0.6 and 0.4 | the job labels S | each session's return is 0.6 times the first ticker's close-to-close return plus 0.4 times the second's, not an equal-weighted mean and not an intraday value |
| S-2.7 | N1 | S's stored holdings changed at the last close (the engine's 16:00 ET pass writes them, `alpha_bot_execution.py:1037-1047`) | the job labels S for D | every session in the window uses the holdings stored at the run, as the replay applies its stored holdings to its whole history (`synthetic_history.py:508-513`) |
| S-2.8 | N1 | the SIP daily request answers without error but returns no bar for one of S's holdings on any session in the 30 before D | the job labels S | no session is computable, so S gets no row dated D and the reason is `insufficient_history`; no zero return is substituted for the unpriced holding |
| S-2.10 | N1 | a 200 response omits one holding's SIP daily bar on one session inside the 20 most recent before D, and in a second case omits the bar of the session before it | the job labels S | in both cases that session is not computable and is left out, and the window reaches back to the 21st most recent computable session; no zero is substituted for the missing close |
| S-2.11 | N1 | the 30 sessions before D hold only 19 computable sessions, while more exist further back | the job labels S | no row dated D for S; the reason is `insufficient_history` (the window never reaches beyond 30 sessions) |
| S-2.12 | B | 1-minute IEX bars and IEX and SIP daily bars in which, every session, each holding's IEX and SIP daily closes are equal and its last 1-minute bar sits at the same position and closes at that daily close (the case where the replay's day value equals the job's close-to-close return), and the replay's day records built by `synthetic_history.build_replay_day` from those bars into one history dict holding every date before D | the job labels S for D | `apply_regime_exit_adjustment` of the job's label equals `_replay_resolve_regime_exit_ticks` for D (live and replay agree where the two day values coincide) |
| S-2.9 | N7 | as S-2.12 | the job labels S | the replay's per-day returns and resolved counts for the existing replay fixtures are unchanged (S-6.4 holds the byte pins) |

Negative coverage: N1 S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.10, S-2.11 · N2 n/a: computing a label answers no request · N3 covered by AC-5 (S-5.2, S-5.6, S-5.10) · N4 n/a: no user reaches the computation; it runs only inside the job (S-6.5) · N5 n/a: the label is a pure function of the stored holdings and the prices, so a repeated run gives the same label (S-1.6) · N6 covered by AC-5 (S-5.1, S-5.5) · N7 S-2.9 · N8 n/a: no edit is held open
Questions: Q-1, Q-2, Q-8, Q-9, Q-13

### AC-3: The engine applies the session's label to the trailing-stop confirmation
Requirement items: R-5, R-6, R-15, R-24, R-27, R-28
Rule: While the engine evaluates symphony S's trailing stop during trading day D, if `regime_label_cache` holds S's row with `as_of_date` D, then the engine shall require the number of consecutive qualifying 1-minute ticks that `math_engine.apply_regime_exit_adjustment` maps the row's label to (trending 2, mean-reverting 5, high-vol 3; `math_engine.py:733-744`) before the trailing stop fires, and otherwise `EXIT_CONFIRM_TICKS` (3).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | S's row is (S, trending, D); S is armed and untriggered, and each tick's return is below its stop level with the Monte Carlo condition passing | the engine runs consecutive cycles on D | the trailing stop fires on the 2nd qualifying tick, not the 3rd |
| S-3.2 | B | as S-3.1 with label mean-reverting | the engine runs consecutive cycles on D | no fire on the 2nd, 3rd or 4th qualifying tick; it fires on the 5th |
| S-3.3 | B | as S-3.1 with label high-vol | the engine runs consecutive cycles on D | it fires on the 3rd qualifying tick |
| S-3.4 | N3 | as S-3.1 with no row for S | the engine runs consecutive cycles on D | it fires on the 3rd qualifying tick, as at the ref |
| S-3.5 | N1 | S's row dated D carries a label outside the three, once each: `Trending` (other case), the empty string, and `sideways` | the engine runs consecutive cycles on D | it fires on the 3rd qualifying tick (`math_engine.py:779-782`) |
| S-3.6 | N1 | as S-3.2, a tick above the stop follows 4 qualifying ticks | the engine continues | the count resets as at the ref and the stop fires only after 5 new consecutive qualifying ticks |
| S-3.7 | N6 | the cache read fails (table missing, or the database file locked) | the engine evaluates S on D | it requires 3 ticks, the cycle completes and no exception escapes (`database.py:1772-1773`) |
| S-3.8 | N7 | S's row is (S, trending, D) | the engine evaluates S on D | the take-profit confirmation, the stop distance, the Monte Carlo thresholds and every other exit layer give the same outcomes as with no row for the same inputs; only the trailing-stop count differs |
| S-3.9 | N7 | no row for any symphony | a recorded sequence of engine cycles runs | every exit decision equals the decision the engine makes at the ref for the same inputs |
| S-3.10 | N1 | as S-3.1, S's `live_mode` is false or `LIVE_EXECUTION` is false (dry run, as on the droplet) | the engine runs consecutive cycles on D | the simulated trailing-stop trigger fires on the 2nd qualifying tick |
| S-3.11 | N1 | as S-3.1, `LIVE_EXECUTION` and S's `live_mode` are true, and the Composer sell request is faked at the HTTP layer | the engine runs consecutive cycles on D | the sell request is sent after the 2nd qualifying tick, not before, through the engine's existing sell path only |

Negative coverage: N1 S-3.5, S-3.6, S-3.10, S-3.11 · N2 n/a: the engine answers no request on this path; the rule sets a confirmation count · N3 S-3.4 · N4 n/a: no user acts on this path; `LIVE_EXECUTION` and `live_mode` gating is unchanged (NB-11) · N5 n/a: the daemon runs one engine process at a time (`app.py:1423-1441`) · N6 S-3.7 · N7 S-3.8, S-3.9 · N8 n/a: no edit is held open
Questions: Q-4, Q-7

### AC-4: A label not computed for the current session is never applied
Requirement items: R-8, R-13, R-14, R-18, R-27
Rule: If S has no row in `regime_label_cache`, or S's row has an `as_of_date` other than the engine's current ET trading day D, then the engine shall require `EXIT_CONFIRM_TICKS` (3) for S's trailing stop on D.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | S's row is (S, mean-reverting, the previous trading day); the job did not run for D | the engine evaluates S as in S-3.1 on D | the trailing stop fires on the 3rd qualifying tick |
| S-4.2 | N6 | the slot fired for D and the job failed before writing any row | the engine evaluates every symphony on D | each requires 3 ticks, including those whose rows carry the previous trading day |
| S-4.3 | N6 | the daemon was down through the slot and restarted before 09:30 ET on D | the engine evaluates every symphony on D | each requires 3 ticks |
| S-4.4 | N1 | S's row is dated the Friday before; D is the Monday | the engine evaluates S on D | 3 ticks |
| S-4.5 | N1 | S's row is dated after D (the next trading day or any later date) | the engine evaluates S on D | 3 ticks |
| S-4.6 | N1 | S's `as_of_date` is not a YYYY-MM-DD date, once empty and once malformed | the engine evaluates S on D | 3 ticks and no exception escapes |
| S-4.7 | N3 | symphony T entered the stored bot state after the run for D, so T has no row | the engine evaluates T on D | 3 ticks; T's first row comes from the next run |
| S-4.8 | N1 | symphony R has a row dated D but Composer no longer reports R | the engine runs on D | R is not evaluated, and no other symphony's count is read from R's row |
| S-4.9 | N5 | S's row is replaced while an engine cycle reads it | the engine evaluates S | it applies either the old row or the new row under this rule, never a label from one and a date from the other |
| S-4.10 | N7 | any row state | the engine evaluates S | the engine writes nothing to `regime_label_cache` (its read opens a read-only connection, `database.py:1750-1751`, `database.py:1763-1771`) |
| S-4.11 | N1 | as S-4.1, dry run (`LIVE_EXECUTION` or S's `live_mode` false) | the engine evaluates S on D | the simulated trigger fires on the 3rd qualifying tick |
| S-4.12 | N1 | as S-4.1, `LIVE_EXECUTION` and S's `live_mode` true, the Composer sell request faked at the HTTP layer | the engine evaluates S on D | the sell request is sent after the 3rd qualifying tick, not before |

Negative coverage: N1 S-4.4, S-4.5, S-4.6, S-4.8, S-4.11, S-4.12 · N2 n/a: the engine answers no request; it falls back to the default count · N3 S-4.7 · N4 n/a: no user acts on this path · N5 S-4.9 · N6 S-4.2, S-4.3 · N7 S-4.10 · N8 n/a: no edit is held open
Questions: Q-4, Q-10

### AC-5: A symphony the job cannot label gets no label, and the others are labelled
Requirement items: R-8, R-9, R-10
Rule: If the regime label job cannot compute symphony S's label for session D (fewer than 20 computable sessions within the 30 sessions before D, no current holdings, a failed or incomplete price fetch for any of S's tickers, `classify_regime` returning None, a failed write, or any other error while handling S), then the job shall write no row dated D for S, log the reason, and still label every other symphony.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | symphonies A, B and C; the price request for B's only ticker fails after its retries | the slot fires | A and C have rows dated D; B has no row dated D; B's log line names the fetch failure |
| S-5.2 | N1 | the 30 sessions before D hold 19 computable sessions, and in a second case 20 | the slot fires | with 19, no row dated D for S and the reason is `insufficient_history`; with 20, a row dated D is written |
| S-5.3 | N1 | S's stored current holdings are an empty list, and in a second case the key is absent | the slot fires | no row dated D for S in either case; the reason is no holdings |
| S-5.4 | N6 | the price source is unreachable for every ticker | the slot fires | no symphony has a row dated D; the summary line reports zero labelled; on D every symphony requires 3 ticks (S-4.2) |
| S-5.5 | N6 | the job's SIP request covers S's tickers in several batches; one batch request fails (a transport error, or a non-200 status after its retries) while the others return 200, and in a second case a 200 response omits one of S's tickers for one session | the slot fires | in the first case every symphony holding a ticker in the failed batch, S included, gets no row dated D, whatever data that batch returned, and its line carries `price_fetch_failed` with the HTTP status code only; symphonies whose tickers were all in successful batches are labelled; in the second case the omitted bar is a missing bar, so that session is incomplete for S (S-2.10); no zero is ever counted for a missing ticker |
| S-5.6 | N1 | S's 20 sessions include a non-finite return, so `classify_regime` receives 19 finite values and returns None (`regime_classifier.py:128-130`) | the slot fires | no row dated D for S; the reason is no classifier label |
| S-5.7 | N3 | S has a row from the previous trading day and this run writes none for S | the engine evaluates S on D | 3 ticks; the older row is never read as D's label |
| S-5.8 | N6 | handling S raises an unexpected error (a malformed holding entry) | the slot fires | the other symphonies are labelled, S has no row dated D, and the summary line is written |
| S-5.9 | N6 | the cache write for S fails (database locked) | the slot fires | S has no row dated D; the symphonies after S are still written; the reason is logged |
| S-5.10 | N1 | no prices exist for any of S's tickers across the window, so a zero-filled series would be constant | the slot fires | S has no row dated D, never mean-reverting (a constant series classifies as mean-reverting, `regime_classifier.py:138-144`, which would impose 5 ticks) |
| S-5.11 | N7 | any failure above | the job ends | no row carries a label outside trending, mean-reverting and high-vol, and no row is dated D unless its label was computed for D |
| S-5.12 | N6 | the SIP daily request is refused (HTTP 403 "subscription does not permit querying recent SIP data", or any other non-200 status after the request's retries) | the slot fires | no row dated D for any symphony whose tickers the refused request covered; each such line carries `price_fetch_failed` with the HTTP status only; the engine requires 3 ticks for them on D (AC-4) |

Negative coverage: N1 S-5.2, S-5.3, S-5.6, S-5.10 · N2 n/a: the job answers no request · N3 S-5.7 · N4 n/a: no user reaches the job (S-6.5) · N5 covered by AC-1 (S-1.6) · N6 S-5.4, S-5.5, S-5.8, S-5.9, S-5.12 · N7 S-5.11 · N8 n/a: no edit is held open
Questions: Q-5

### AC-6: The job stays off the 1-minute path, off every route and away from every order path
Requirement items: R-7, R-19, R-20, R-24, R-25
Rule: When an engine cycle evaluates a symphony's trailing stop, the engine shall obtain the regime label only through its existing single cached read for that symphony, and the regime label job shall run only on its own thread started from the daemon's daily slot, never inside an engine process or an HTTP request.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | S has a row dated D | an engine cycle evaluates S | exactly one `regime_label_cache` read for S in that cycle, no price request and no `classify_regime` call caused by the label, and the cycle's other I/O is the same as with no row |
| S-6.2 | N7 | the GREEN tree | the structural tests run | `alpha_bot_execution.py` and `math_engine.py` still do not import `regime_classifier` (`tests/execution/test_regime_cache_isolation.py` tests 1 and 2) |
| S-6.3 | N7 | the GREEN tree | the source scan runs | `autotuner.py` still has no `get_cached_regime_label` reference (`tests/autotuner/test_ac4_regime_conditional_exit_ticks.py:165-186`) |
| S-6.4 | N7 | the GREEN tree | the content checks run | `math_engine.py`, `regime_classifier.py`, `autotuner.py`, `synthetic_history.py` and `migrations/027_regime_label_cache.sql` are byte-unchanged from the ref; `alpha_bot_execution.py` is byte-unchanged except, when Q-4's answer places the rule there, at the cached-read call (`alpha_bot_execution.py:1526-1529`) and nowhere else |
| S-6.5 | N4 | the Flask app's URL map and each route function's call graph | the boundary scan runs | no route reaches the job or `database.save_regime_label` |
| S-6.6 | N7 | the job's call graph | a no-trade boundary scan modelled on `tests/security/test_model_warmup_no_trade_boundary.py` runs, also against sources with a forbidden call planted at several depths | no order, sell, liquidation, Composer write or `LIVE_EXECUTION` read is reachable from the job, and the scan fails on each planted call |
| S-6.7 | N6 | the job's price fetch is held for 3 minutes | the scheduler keeps running | the :00 engine spawn and the :00 account refresh still fire at every minute boundary meanwhile; the slot's callable returns at once, as the 03:30 and 03:45 slots do (`app.py:1926-1935`, `app.py:2629-2646`) |
| S-6.8 | N1 | the slot's host time and the host timezones UTC and America/New_York | the slot time is mapped to ET for a date in EST and a date in EDT | the ET instant is outside 09:30 to 16:00 in all four cases |

Negative coverage: N1 S-6.8 · N2 n/a: no request is refused · N3 n/a: the read's absent case is AC-4 (S-4.7) · N4 S-6.5 · N5 n/a: a repeated run is AC-1 (S-1.6) · N6 S-6.7 · N7 S-6.2, S-6.3, S-6.4, S-6.6 · N8 n/a: no edit is held open
Questions: Q-3, Q-4

### AC-7: Each run logs one line per symphony and one summary line
Requirement items: R-16
Rule: When the regime label job finishes handling a symphony, the job shall write one line to the daemon log (the `alphabot` logger, `app.py:619-623`) naming the symphony's Composer hash and display name, the target session D, and either the label with the window's first and last session dates and session count or a reason code, and when the run ends it shall write one summary line with D and the counts of symphonies seen, labelled, and not labelled per reason code.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | two symphonies labelled and one with insufficient history | the run completes | three per-symphony lines and one summary line whose counts are 3 seen, 2 labelled and 1 `insufficient_history` |
| S-7.2 | N1 | one symphony per reason: `insufficient_history`, `no_holdings`, `price_fetch_failed`, `no_classifier_label`, `write_failed`, `after_open`, `unexpected_error` | the run completes | each line carries its own reason code and the summary counts each once |
| S-7.3 | N6 | the job fails before handling any symphony (the stored bot state cannot be read) | the run ends | one summary line naming the failure by exception class name only, with zero labelled |
| S-7.4 | N7 | any run, failures included | the lines are written | no line contains an API key, a request header, a response body or an exception message; failures appear as class names only |
| S-7.5 | N3 | the stored bot state holds no symphony | the run completes | no per-symphony line; the summary reports zero seen |

Negative coverage: N1 S-7.2 · N2 n/a: the job answers no request · N3 S-7.5 · N4 n/a: the log is read by the operator on the host, no route serves it · N5 n/a: a repeated run writes its own lines (S-1.6) · N6 S-7.3 · N7 S-7.4 · N8 n/a: no edit is held open
Questions: Q-6

## Rule-by-Entry-Point Matrix
The regime label job has one entry point, its scheduler slot (S-6.5 proves no route reaches it), and the cache has one reader, the engine's trailing-stop check (`alpha_bot_execution.py:1526-1529`). The engine rules are stated per execution state, because the count must not depend on whether orders go out.

| Rule | Engine cycle, dry run (`LIVE_EXECUTION` false or `live_mode` false) | Engine cycle, live (`LIVE_EXECUTION` true and `live_mode` true) |
|---|---|---|
| AC-3 the session's label sets the count | S-3.10 | S-3.11 |
| AC-4 a label not dated D is ignored | S-4.11 | S-4.12 |

## Not Building
Non-goals:
- NB-1: No page, route, button or command to run or view the job; it runs only from its daily slot (R-19; the command part approved [PM-ASSUMED]).
- NB-2: No Discord, post-mortem or report text naming the label (approved [PM-ASSUMED]).
- NB-3: No backfill of labels for past days (excludes R-22; approved [PM-ASSUMED]).
- NB-4: No Optuna retune in this plan (excludes R-23; approved [PM-ASSUMED]).
- NB-5: No catch-up run at daemon start when the slot was missed; that session runs on the default count (approved [PM-ASSUMED]; Q-10).
- NB-6: No new configuration key and no off switch (R-17; approved [PM-ASSUMED]; Q-11).
- NB-7: The engine's console text "Awaiting 3-tick confirmation" (`alpha_bot_execution.py:1546`) is not changed by this plan (approved [PM-ASSUMED]; Q-7).

Behavior that stays exactly as it is:
- NB-8: The engine's decisions when no current label exists (R-15; S-3.4, S-3.9).
- NB-9: The 1-minute path's I/O (R-7; S-6.1).
- NB-10: The replay's no-lookahead rule; the replay never reads the live cache (R-25; S-6.3).
- NB-11: `LIVE_EXECUTION` and per-symphony `live_mode` gating; `is_live` is never a default; neither is settings-writable; the panic-sell route and the Managed Sleeves order path are untouched.
- NB-12: Every existing scheduler slot and its time (02:00, 03:00, 03:30, 03:45, 04:15; `app.py:2747-2762`).

Deferred:
- NB-13: Showing the regime label on the dashboard (excludes R-21; approved [PM-ASSUMED]).
- NB-14: A retune once live and replay apply the same rule (excludes R-23; approved [PM-ASSUMED]).

Protected surfaces this consumes but does not change:
- NB-15: `regime_classifier.py`, including `classify_regime`, `MIN_LABEL_SERIES_LENGTH` and the 0.04 threshold (R-20; S-6.4).
- NB-16: `math_engine.py`, including `apply_regime_exit_adjustment`, the 2/5/3 table, `EXIT_CONFIRM_TICKS` and every other exit layer (R-20, R-24; S-6.4, S-3.8).
- NB-17: `autotuner.py` (the replay and `_replay_resolve_regime_exit_ticks`) and `synthetic_history.py` (the replay's basis) (R-20; S-6.4).
- NB-18: The `regime_label_cache` schema; no migration (R-17; S-6.4).
- NB-19: `alpha_bot_execution.py` beyond the cached-read call Q-4's answer may touch (S-6.4).
- NB-20: The existing boundary tests stay green: `tests/security/test_model_warmup_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_frontrunner_no_trade_boundary.py` and the `LIVE_EXECUTION` reference check at `tests/test_mdd_window_truth_zero_diff_guard.py:118`; S-6.6 adds the job's own scan.
- NB-21: No repository other than AlphaBotPM changes (R-26).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | The freshness rule enforced at one reader and missing at another | N1 | grep: `get_cached_regime_label\|regime_label_cache` over every `*.py` outside `tests/` in AlphaBotPM; stop when only the engine read and the job remain | S-4.1, S-4.4, S-4.5, S-4.11, S-4.12 | none |
| DC-2 | An absent or not-computed label stored as a plausible one: missing prices filled with zero give a constant series, which classifies as mean-reverting | N3 | grep: `.get("return", 0.0)\|or 0.0\|fillna\|default=0` in the job and every helper it calls; read: what each missing-price branch returns | S-2.8, S-5.5, S-5.10, S-5.11 | none |
| DC-3 | Date edges mishandled: weekend, holiday, DST, host timezone, a run after the open, a row dated before or after D | N1 | grep: every `date.today\|datetime.now\|weekday()\|timedelta(days` in the job and the read path; read: each against `market_calendar` | S-1.2, S-1.3, S-1.4, S-1.5, S-4.4, S-4.5, S-6.8 | none |
| DC-4 | Percent and fraction confused, so the 0.04 threshold compares with percent | N1 | grep: `RETURN_PCT_TO_FRACTION\|\* 100\|/ 100` in the job and its helpers | S-2.3 | none |
| DC-5 | Lookahead: the window includes session D or later | N1 | grep: every date filter over bars in the job; read: strict versus inclusive comparisons | S-2.4, S-2.5 | none |
| DC-6 | Blocking work on the scheduler thread or the engine path | N6 | grep: `schedule.every\|threading.Thread` in `app.py`; read: whether each slot's callable returns at once | S-6.1, S-6.7 | none |
| DC-7 | Silent partial data: a failed fetch batch returns a smaller result with no error | N6 | grep: `def fetch_bars\|def fetch_alpaca_history` and every caller; read: how a failed batch reaches the caller (`synthetic_history.py:348-353`) | S-5.5 | none |
| DC-8 | Non-symphony bot state keys treated as symphonies | N1 | grep: `load_state(\|bot_state.items()\|\.values()` in the job; read: the symphony test used against `app.py:6142-6150` | S-1.8 | none |
| DC-9 | The label keyed by the wrong identifier (the normalized display name, or Composer's `symphony_id` field instead of `id`), so the engine never finds it | B | grep: `normalize_name\|symphony_id\|\["id"\]` in the job; read: the key written against `alpha_bot_execution.py:1239` | S-1.1, S-3.1 | none |
| DC-10 | A narrow assertion licensing siblings: one label, one reason, one execution state tested | B | read: the tests against S-2.2, S-3.1 to S-3.3, S-3.5, S-7.2 and the matrix | S-2.2, S-3.5, S-7.2, S-3.10, S-3.11 | none |
| DC-12 | The job's day value drifts from the design basis: it copies the replay's last-1-minute-tick value, uses `feed=iex` or mixes IEX and SIP closes, substitutes zero for a missing close, or counts calendar days instead of computable sessions | B, N1 | grep: `1Min\|feed=\|daily_ret\|or 0.0\|\.get(.*0.0)\|timedelta(days` in the job and its helpers; read: each against the AC-2 Rule | S-2.1, S-2.6, S-2.10, S-2.11 | none |
| DC-11 | A contract change not carried to its consumers: tests and docs that describe the cache as never written or the engine as byte-pinned | B | grep: `save_regime_label\|get_cached_regime_label\|regime_label_cache\|_PINNED_HASH` across `tests/`, `docs/generated/`, `README.md` | S-6.4 | none |

## Worked Example
The symphony "Example Growth" (Composer hash `xR4tQ9mZ2bL7vN1cK8pW`, invented) holds TICKA at allocation 0.6 and TICKB at 0.4 in the stored bot state after the close of Friday 2026-11-06. The daemon's host clock is UTC.
- The slot fires at 04:30 UTC on Saturday 2026-11-07, which is 23:30 ET on Friday. The first trading day whose 09:30 ET open is after that instant is Monday 2026-11-09, so D is 2026-11-09 (S-1.2, S-1.4).
- The window is the 20 sessions 2026-10-12 to 2026-11-06, all computable; the first return uses the SIP daily closes of 2026-10-09, fetched with an end of 2026-11-07T04:10:00Z, 20 minutes before the request (D-12). Each session's return is 0.6 times TICKA's close-to-close return plus 0.4 times TICKB's, as fractions (S-2.5, S-2.6, S-2.3).
- The series alternates in sign with a standard deviation near 0.012, so `classify_regime` returns mean-reverting. The job writes the row (`xR4tQ9mZ2bL7vN1cK8pW`, mean-reverting, 2026-11-09) and logs one line with the hash, the name, D, the label and the window 2026-10-12 to 2026-11-06 (20 sessions) (S-1.1, S-7.1).
- On Monday 2026-11-09 the symphony is armed and dips below its stop. The trailing stop fires only after 5 consecutive qualifying ticks instead of 3; on the droplet the trigger is simulated (S-3.2, S-3.10).

Main failure: the Alpaca data API is unreachable when the slot fires at 04:30 UTC on Tuesday 2026-11-10.
- No symphony gets a row dated 2026-11-10; the summary line reports zero labelled with `price_fetch_failed` for each (S-5.4, S-7.1).
- Example Growth's row still reads (mean-reverting, 2026-11-09). On Tuesday 2026-11-10 the engine finds a date other than the session's and requires 3 ticks (S-4.1, S-5.7).

## Fail-Closed Behaviour
The job answers no request and the engine's fallback is the existing default count, so neither returns a typed refusal to a user. The write path the job uses can refuse, and each refusal ends in no row dated D for that symphony:
- `sqlite3.OperationalError` "database is locked" after the 10 s busy timeout (`database.py:96`, upsert at `database.py:1731-1738`): reason `write_failed`, S-5.9.
- `sqlite3.OperationalError` "no such table" when migration 027 is absent (`database.py:1731`, `migrations/027_regime_label_cache.sql:13`): reason `write_failed`, S-5.9.
- `sqlite3.IntegrityError` on the NOT NULL `label` or `as_of_date` (`migrations/027_regime_label_cache.sql:15-16`): unreachable, because the job writes only a classifier label and a formatted D (S-5.11).
- A primary-key conflict is not a refusal: the upsert replaces the row (`database.py:1734-1737`; S-1.6, S-1.7).
- The SIP daily request can be refused: HTTP 403 "subscription does not permit querying recent SIP data" when the end bound is within 15 minutes of the request or a date-only end equals the current date (D-12), or any other non-200 status (D-11): reason `price_fetch_failed`, no row dated D for the symphonies it covered, S-5.12.

What now falls back instead of using a value:
- The engine's read uses the default count instead of a stored label when the row's `as_of_date` is not the current ET trading day (S-4.1, S-4.4, S-4.5, S-4.6; per execution state S-4.11, S-4.12). At the ref it uses any stored label (Known fact 2).
- The job writes no row for S instead of a label computed from partial or missing prices (S-5.5, S-5.10) or from fewer than 20 sessions (S-5.2), and no row whose write would land at or after D's 09:30 ET open (S-1.10).
- An unreadable cache keeps its existing fallback to the default count (`database.py:1772-1773`; S-3.7).

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| Symphony S's regime label for session D | the job's `classify_regime` call on AC-2's series | nothing: no producer existed (G-7) | S-2.1 (the design-basis series), S-2.12 (equal to the replay's label where the day values coincide), S-6.1 and S-6.2 (the engine computes none) |
| Which day is a trading session, and D | `market_calendar.is_trading_day` and `market_calendar.session_open` (`market_calendar.py:42`, `market_calendar.py:64`) | none | S-1.2, S-1.3; DC-3's search for weekday arithmetic |
| The count a label means | `math_engine.apply_regime_exit_adjustment` | unchanged | S-6.4 |
| S's holdings and weights | the stored bot state's `current_holdings`, the source the replay also reads (`synthetic_history.py:508-513`) | none | S-2.7 |

## Contract Changes and Consumers
- `regime_label_cache` gains its first production writer. `as_of_date` means the ET trading session the label governs, consistent with migration 027's "the label's effective date" (`migrations/027_regime_label_cache.sql:10-12`). The schema is unchanged and backward compatible.
- The engine's read gains the same-session rule (AC-4). Its only production consumer is the engine (`alpha_bot_execution.py:1527`). Test consumers: `tests/execution/test_regime_cache_isolation.py` (item 11 at `:354-368` changes only if Gate 2 changes the accessor's default; Earlier criteria), and the write-symbol lists in `tests/test_calibration_sweep_advisory_invariant.py:64` and `tests/app/test_guard_alpha_summary_route.py:315`, whose meaning is unchanged.

## Sibling Repos and Landing Order
none: AlphaBotPM only (G-22); it lands after the dead-table-partial slice, already in this branch's base.

## Migrations Required
none: migration 027's table carries everything the criteria need (`migrations/027_regime_label_cache.sql:13-18`).

## Configuration and Enablement
none: no new key (G-21, Q-11). The job is active from the deploy; the first labels govern the first session after the first slot run. On the droplet the engine runs dry (`LIVE_EXECUTION` false), so the change first shows in simulated triggers.

## Breaking Changes
- From the first labelled session, a trending symphony's trailing stop confirms after 2 ticks (earlier exits) and a mean-reverting symphony's after 5 (later exits, so a deeper move can occur before the exit). Simulated triggers on the droplet, and the post-mortem saved-dollar figures built from them, change the same way.
- The engine's console text still says "Awaiting 3-tick confirmation" when the count is 2 or 5 (Q-7).

## Security Considerations
- Input validation and injection: the job writes only `classify_regime`'s three strings (S-5.11) through the existing parameterized upsert (`database.py:1731-1738`).
- Authorization and access control: no route reaches the job or the write (S-6.5); no order, liquidation, Composer write or `LIVE_EXECUTION` read is reachable from it (S-6.6); `LIVE_EXECUTION` and `live_mode` gating are unchanged (NB-11).
- Data exposure: log lines carry no key, header, response body or exception message (S-7.4); Alpaca credentials stay in the daemon environment (`synthetic_history.py:20-22`).
- Client-side rendering: none; no UI.
- Abuse and rate limits: one off-hours fetch per night over the symphonies' tickers, while the engine is idle (S-6.8); the engine's autotune runs at the 16:00 ET pass (`alpha_bot_execution.py:1184`), not overnight.
- Money: the label changes when a real or simulated exit fires (Sensitive header; AC-3, AC-4).

## Prerequisites
- Alpaca data API credentials (`ALPACA_KEY`, `ALPACA_SECRET`, `ALPACA_BASE_URL`; `synthetic_history.py:20-22`) in the daemon environment, on a subscription that serves SIP daily bars through the previous session's close (served on the droplet's credentials for an explicit end 20 minutes before the request, returning the previous session's bar, D-12). Without them every fetch fails, the job writes no rows and logs `price_fetch_failed`, and the engine uses the default count (S-5.4).
- The NYSE calendar library behind `market_calendar` (`market_calendar.py:25`).

## Design-System Mapping
none: no screen or element changes.

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10 | unit with a fixed clock and fixture bars, plus slot integration | the callable `run_scheduler` registers for the slot, invoked as the scheduler invokes it, against a temporary state database |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12 | unit (golden fixtures and replay parity) | the slot callable, compared with `_replay_resolve_regime_exit_ticks` on the same bars |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11 | engine integration with a temporary state database, Composer and Alpaca faked at the HTTP layer | real engine cycles through `alpha_bot_execution.main`; S-3.11 asserts the outgoing sell request |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12 | engine integration | real engine cycles; S-4.12 asserts the outgoing sell request |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12 | unit plus slot integration; S-5.7 engine integration | the slot callable with the Alpaca HTTP layer faked per case |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8 | structural scans, content checks and a scheduler timing test | a real `_scheduler_tick` pass with the job's fetch held |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5 | log capture on the `alphabot` logger | the slot callable |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run (never `-n auto` and never more than 4 workers) over `tests/execution/`, `tests/engine/`, `tests/math_engine/`, the regime tests in `tests/autotuner/`, `tests/security/`, the scheduler tests in `tests/app/`, `tests/test_mdd_window_truth_zero_diff_guard.py` and the new tests; CI green on the exact PR head SHA; `/review` on the PR; then the PM's live functional check on the droplet after the review: after the first slot run, the read-only query `SELECT symphony_id, label, as_of_date, updated_at FROM regime_label_cache` shows one row per labelled symphony dated the next session, the daemon log holds one line per symphony and the summary line, and during that session the engine log shows a labelled symphony's trailing-stop confirmation at the label's count.

Fixture provenance: the Alpaca bars response shape is captured once from the real data endpoint with `/api-fixture`, keeping one record per case the job handles (a `feed=sip` daily-bar page, a page with `next_page_token`, the HTTP 403 recent-SIP refusal, a response missing a requested symbol, and a `feed=iex` 1-minute-bar page and daily-bar page for S-2.12). The price series for the three labels are built in that captured shape with values derived from the existing classifier golden series in `tests/fixtures/math/regime_classifier/`, and each is checked by a validator written from the captured shape. One stored bot state symphony entry is captured from the droplet's state with its account id replaced. Expected series for S-2.1 are computed in the test from the fixture's closes, never copied from a run. The replay's day records for S-2.12 come from the real `synthetic_history.build_replay_day` run on the same bars, never hand-built. Expected labels and counts come from calling the real `classify_regime` and `apply_regime_exit_adjustment`, never from literals copied from a run.

Data evidence: the first live run's summary line and each symphony's window count and dates, read from the daemon log.

## Success Measures
- Before each session's open, the read-only query `SELECT COUNT(*), MIN(as_of_date), MAX(as_of_date) FROM regime_label_cache` returns the labelled count from that night's summary line, with both dates equal to the session.
- Over the first week, each night's summary line shows every symphony with 21 or more sessions of prices labelled, and any unlabelled one carries a reason code.
- The engine log over the first week shows trailing-stop confirmations at 2 or 5 ticks for symphonies labelled trending or mean-reverting, and at 3 for every other.

## Known-Unconfirmed and Standing Risk
1. Replay fidelity (standing risk, per D-5): the replay's regime input deviates from its own design ("trailing EOD daily returns", `DECISIONS.md:6276-6278`). Its day value is the first holding's last 1-minute IEX bar, with the other holdings matched by bar position and a holding with fewer bars dropping out (Known fact 4). The audit's read of a local cache file (11 symphonies, 250 days each) found a median of 7 to 18 ticks per day for 9 symphonies, a last tick before 15:50 ET on 39 to 105 days per symphony, and no tick after 15:59 ET. The replay's synthetic history is built from `feed=iex` bars (`synthetic_history.py:318`), which explains those sparse ticks and adds IEX price noise to every replayed return, not only to the regime input (D-11 gives the measured IEX versus SIP gaps). The window's members also differ: the replay's window is the dates present in its history, which exist only when the first holding has 1-minute IEX bars (the local cache lacked 15 of 250 dates for 6 of 11 symphonies), with no look-back bound, while the job's window is the sessions on which every holding has both SIP closes, within 30 sessions (D-8). The live label uses the design basis (AC-2), so live and replay can resolve different labels for the same day near a threshold. Unconfirmed: how often that happens on the droplet. Settle with a read-only comparison on the droplet: for each symphony in the newest `cache/synthetic_history_v4_*.json`, classify each date's trailing 20 replay day values and the trailing 20 close-to-close returns from the SIP daily closes for the same dates, and count the dates whose labels differ. The fix is a replay change (Follow-Ups).
2. The daemon host's timezone, which sets the slot's wall-clock meaning; the code's own text calls the 03:45 slot UTC (`app.py:7078`). Settle with `timedatectl` (read-only) on the droplet. S-6.8 holds for UTC and ET hosts.
3. How many stored symphony entries carry non-empty holdings and whether each has 21 or more sessions of daily bars for every ticker. Settle with a read-only count over `database.load_state(read_only=True)` of entries that are dicts with `name` and non-empty `current_holdings`, and with the first run's summary line.
4. Whether thin tickers miss SIP daily bars on some sessions often enough to push a symphony below 20 computable sessions within 30 (S-2.10, S-2.11); settled by the first run's per-symphony window counts and reasons.
5. Alpaca rate limits at the slot are not published per key; the engine is idle then and the autotune runs at the 16:00 ET pass (`alpha_bot_execution.py:1184`).
6. On the droplet `LIVE_EXECUTION` is false (PM live read), so the change moves simulated triggers and the post-mortem figures built from them, and real orders only once live execution is enabled.
7. The 2/5/3 table has never governed a live session (G-8); since R1 the autotune has selected parameters under it (G-9, G-10). Its real-money effect is unmeasured until it runs (Success Measures).
8. PHASE3_DECISIONS.md, cited as the design record at `regime_classifier.py:11` and `tests/execution/test_regime_cache_isolation.py:9`, is not in the repository; this plan's design record is the code (G-2 to G-6) and DE-MATH-R1-001 in `DECISIONS.md`.
9. A symphony whose 16:00 ET post-mortem pass did not refresh its holdings (a failed Composer fetch then) keeps older stored holdings; the job uses them, as the replay does.
10. Live and replay still differ on the replay's own validation and frozen-fold head days: the adoption cascade, the validation metric and the frozen evaluation pass fold-sliced history dicts (`autotuner.py:2529-2535`, `autotuner.py:2940-2945`, `autotuner.py:2993-2999`, `autotuner.py:3012-3018`), and the replay builds its window from the keys of the dict it receives (`autotuner.py:1975`), so the first 20 days of each fold (about 20 of 50) resolve the default count while live, after this plan, applies the session's label. The CPCV selection path uses the full train chronology (`autotuner.py:2607`). This plan does not change the replay (G-24); R-6 is narrowed and a Follow-Up records the fix.
11. removed: settled by the PM's live reads and recorded as D-11 (IEX closes differ materially from SIP closes on thin ETFs) and D-12 (which SIP request ends are served).
12. Settled (PM live read, 2026-11-04): all 50 distinct `current_holdings` tickers on the droplet are plain 1 to 5 letter equity or ETF symbols, with no cash or crypto line, so no separate reason code for a non-equity holding is needed; a holding with no SIP bar makes a session incomplete (S-2.8, S-2.10).
13. removed: settled by the PM's second live read and recorded as D-12.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | What exactly is a session's return in the label's series? | AC-2 | [PM-ASSUMED, D-5, D-11] The design basis: the allocation-weighted close-to-close return of S's stored current holdings from SIP daily closes, as fractions, over the 20 most recent computable sessions strictly before D within 30 sessions (D-8); not a copy of the replay's last-1-minute-tick value; `autotuner.py` and `synthetic_history.py` stay byte-unchanged (S-6.4) | Default approved as the PM ruling recorded (D-5, D-8, D-11). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-2 | Where do the job's prices come from? | AC-2, AC-5 | [PM-ASSUMED, D-11] A fresh fetch in the job's slot of the consolidated (SIP) daily closes from Alpaca's daily-bar request in the shape the replay's daily fetch uses (`/v2/stocks/bars`, `timeframe=1Day`, `adjustment=split`) but with `feed=sip`, with an end bound given as an explicit UTC timestamp at least 15 minutes before the request (D-12), covering at least the 31 sessions before D. The weekly synthetic-history cache cannot serve it: its newest day is the day before the run that wrote it (`synthetic_history.py:686-687`) and it is written about weekly (`synthetic_history.py:539-545`), so most nights it lacks the sessions just before D. The engine's daily-history cache (`alpha_bot_execution.py:318-329`) is fetched at the previous session's open and lacks that session's close | Default approved as the PM ruling recorded (D-11, D-12); a session not completed by the SIP request's end bound never counts, and no partial bar is ever used. [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-3 | When does the job run? | AC-1, AC-6 | Once a day at 04:30 host time in `run_scheduler`, after the 03:45 slot and the 04:15 model warm-up (the brief's 04:15 example is taken, `app.py:2762`); D is computed in ET whatever the host timezone (S-1.4, S-6.8) | Default approved. [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-4 | Where is the same-session rule enforced, and what happens to the engine pins? | AC-3, AC-4, AC-6 | A label is used only on the ET trading day equal to its `as_of_date`, enforced at the engine's existing read, because a producer-only rule cannot cover a run that never happens (the scheduler runs no missed slot after a restart, and a job that fails before writing leaves the previous rows). Gate 2 chooses between an argument at the engine's call (`alpha_bot_execution.py:1527`; re-pins `tests/test_mdd_window_truth_zero_diff_guard.py:72` and `tests/engine/test_close_snapshot_builder_scope.py:28`) and a change to the accessor's default in `database.py` (no engine diff; changes `tests/execution/test_regime_cache_isolation.py:354-368`). [PM-ASSUMED] The PM gives its word that either option may move its pins, provided the engine diff is limited to the regime-label read. The existing `staleness_cutoff_days` cannot express the rule (Known fact 2) | The Gate-2 approach chose the engine call site: `database.get_cached_regime_label` gains a keyword-only `session_date`, and the engine passes `session_date=current_date_str`; the pins `tests/test_mdd_window_truth_zero_diff_guard.py:72` and `tests/engine/test_close_snapshot_builder_scope.py:28` move under the PM's word, with the engine diff limited to the regime-label read. [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-5 | What does the job do for a symphony it cannot label? | AC-5 | Write nothing dated D for it; the Q-4 rule makes any older row inert; deleting the older row as well is allowed but not required | Default approved; additionally, a non-finite or non-positive close gives no label with reason `no_classifier_label` (as S-5.6). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-6 | What does the job log, and where? | AC-7 | One line per symphony and one summary line per run, to the daemon log file through the `alphabot` logger, with the reason codes of S-7.2; no UI | Default approved. [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-7 | The engine's console text says "Awaiting 3-tick confirmation" (`alpha_bot_execution.py:1546`) and becomes wrong for counts 2 and 5. Fix it here? | AC-3 | No: it is console text, not a decision, and fixing it adds an engine diff; one backlog line at PR creation | Default approved (NB-7). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-8 | The replay's day value is not a close-to-close return (Known fact 4), while the design record speaks of a daily return series (G-3) and DE-MATH-R1-001 of trailing EOD daily returns. Does live follow the replay's construction or the design? | AC-2 | [PM-ASSUMED, D-5] The design: copying the replay would carry its deviation into live exits, and a nightly fetch cannot reproduce the weekly cache's 1-minute bars anyway; the replay's deviation is a standing risk (Known-Unconfirmed 1) and a Follow-Up for a math-layer cycle | Default approved as the PM ruling recorded (D-5). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-13 | How is a holding treated that the SIP request never prices in the fetched span? | AC-2 | [PM-ASSUMED, PM ruling: the zero-return fill is overruled] As any other missing close: every session is then incomplete and the symphony gets no label (`insufficient_history`). The PM's live read found no cash or crypto line among the 50 droplet tickers (Known-Unconfirmed 12), so no special case is needed | The zero-return fill is overruled; the recorded default (no label, `insufficient_history`) stands. [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-12 | Which instant fixes D, and what happens to a row whose write lands at or after D's open? | AC-1 | D is fixed when the job starts; a row is written only before D's 09:30 ET open, and one that would land at or after the open is not written (reason `after_open`), so a session's count never changes partway through (S-1.10) | Default approved as the PM ruling recorded (D-9). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-9 | Which symphonies does the job label? | AC-1, AC-2 | Every stored bot state entry that is a dict with `name` and has non-empty `current_holdings`, in dry run and live alike, including symphonies Composer no longer reports, whose rows nothing reads (S-4.8) | Default approved. [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-10 | Does the daemon run a missed slot when it starts? | AC-1, AC-4 | No: that session runs on the default count (S-4.3, NB-5) | Default approved (NB-5). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |
| Q-11 | Is there an off switch? | plan | No new key (G-21); rollback is a revert, after which every row stops applying within one session by the Q-4 rule | Default approved (NB-6). [PM-ASSUMED] approved by the PM (session 8055360f, 2026-11-04T03:15Z) |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | The weekly synthetic-history cache is not the label's price source | Its newest day precedes its generation date and it is written about weekly (`synthetic_history.py:539-545`, `:686-687`), so it lacks the sessions just before D on most nights |
| D-2 | The same-session rule needs new read behavior on whichever side Gate 2 chooses | `staleness_cutoff_days` counts calendar days: 1 accepts the previous day's label and 0 rejects all (`database.py:1783-1796`) |
| D-3 | Freshness is enforced at the read, not by the producer alone | G-19 requires a run that never happens to leave no stale label in force; only the read sees that case |
| D-4 | removed: superseded by D-5 | The PM ruled the design basis instead of the replay's day value |
| D-5 | [PM-ASSUMED, PM ruling R1] The live label uses the design basis: each session's return is the allocation-weighted close-to-close return of the stored holdings, in decimal; the replay's deviations (last 1-minute tick instead of the close, positional matching, holdings with fewer bars dropping out) are a replay-fidelity defect, recorded and not reproduced | The design record says "over the symphony's daily return series" (G-3) and DE-MATH-R1-001 says "trailing EOD daily returns" (`DECISIONS.md:6276-6278`); reproducing the replay would carry its defect into live exits, and a nightly fetch could not reproduce the weekly cache's bars |
| D-6 | removed: superseded by D-11 | PM ruling R1b replaced R1a's IEX closes with SIP closes |
| D-7 | [PM-ASSUMED, PM ruling R3] The replay's fold slicing, which gives each validation and frozen fold's first 20 days the default count, is a standing risk and a Follow-Up, not fixed here | G-24 excludes replay changes; R-6 is narrowed |
| D-8 | [PM-ASSUMED, PM ruling R2] The window is the 20 most recent NYSE sessions strictly before D on which every holding has its two SIP closes, reaching back at most 30 sessions; fewer than 20 gives no label | Skipping a session keeps the series made of real returns, and the bound keeps a label from resting on stale history |
| D-9 | [PM-ASSUMED, PM ruling R4] D is fixed when the job starts; a write that would land at or after 09:30 ET on D is not made, and that session runs on the default count, logged | A session's count never changes partway through |
| D-10 | [PM-ASSUMED, PM ruling R5] The write path's database errors are listed in Fail-Closed Behaviour | The audit's refusal enumeration |
| D-11 | [PM-ASSUMED, PM ruling R1b] The closes are consolidated (SIP) daily closes from Alpaca's `/v2/stocks/bars` daily request (`timeframe=1Day`, `adjustment=split`, `feed=sip`), requested with the end bound of D-12 | PM live read 2026-11-04, read-only, on the droplet's credentials: `feed=sip` with end 2026-11-02T00:00:00Z returned 29 daily bars for each of STPZ, DULL, XMPT, RCG, KMLM, DHS and QQQ, the same 29 sessions as `feed=iex`; `feed=sip` with end 2026-11-03 returned HTTP 403 "subscription does not permit querying recent SIP data". The largest relative IEX versus SIP close difference over those 29 sessions: DULL 12.8%, RCG 5.9%, XMPT 1.9%, DHS 0.60%, STPZ 0.36%, KMLM 0.20%, QQQ 0.06%. IEX closes are unfit as the regime basis for thin ETFs |
| D-12 | [PM-ASSUMED] The job's SIP request ends at an explicit UTC timestamp at least 15 minutes before the request; a date-only end is never sent; the 403 refusal stays covered as a fail-safe by S-5.12 | PM live read 2026-11-04 near 02:35Z, read-only Alpaca `/v2/stocks/bars`, droplet credentials, `timeframe=1Day`, `adjustment=split`, `feed=sip`, symbols QQQ and DULL, start 2026-10-27: end `2026-11-03` (date-only, the current ET date) returned HTTP 403 "subscription does not permit querying recent SIP data"; end `2026-11-02` (date-only, a past date) returned 200 with last bar 2026-11-02; an explicit RFC 3339 UTC end 20 minutes before the request returned 200 with last bar 2026-11-03, the previous session's close; an end 5 minutes before the request returned 403. The end must be at least 15 minutes before the request time, and a date-only end equal to the current date is refused |

## Docs to Update
- `docs/generated/app.md`: the new daily slot and the job.
- `docs/generated/database.md`: the cache now has a producer, and the same-session read rule.
- `docs/generated/alpha_bot_execution.md`: the trailing-stop count now varies by session label (and the call-site change, if Q-4's answer puts it there).
- `docs/generated/autotuner.md`: live and replay now share the label's basis.
- A `docs/generated/` file for the module that holds the job, and `docs/generated/INDEX.md`.
- `README.md`: the daemon's off-hours jobs.
- An appended `DECISIONS.md` entry, minted at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row for the job's module, for the PM's approval.

## Follow-Ups
- NB-13 (dashboard display of the label): one line in `feature-plans/BACKLOG.md` at PR creation.
- NB-14 (retune once live and replay agree): one line in `feature-plans/BACKLOG.md` at PR creation.
- NB-7 and Q-7 (the engine's "3-tick" console text): one line in `feature-plans/BACKLOG.md` at PR creation.
- NB-5 (catch-up run for a missed slot): one line in `feature-plans/BACKLOG.md` if the operator wants it.
- Known-Unconfirmed 10 (the replay's validation and frozen-fold paths resolve the default count on their first 20 days, against DE-MATH-R1-001 ADDENDUM 6): one line in `feature-plans/BACKLOG.md` at PR creation, for a replay fix that hands those paths the full chronology.
- Known-Unconfirmed 1 and D-5 (replay-fidelity defect: the replay's regime input uses the first holding's last 1-minute IEX tick, often before the close, instead of the daily close; matches the other holdings by bar position, not by minute; drops a holding that has fewer bars that day; builds its synthetic history from `feed=iex` bars, which makes ticks sparse and adds IEX price noise to every replayed return (D-11); and takes its window from the dates present in its history (only those with first-holding 1-minute IEX bars, no look-back bound) rather than sessions on which every holding has both daily closes within 30 sessions): one line in `feature-plans/BACKLOG.md` at PR creation, for a math-layer cycle that moves the replay's regime input onto SIP daily closes over the same window rule and reviews the replay's IEX price basis.
