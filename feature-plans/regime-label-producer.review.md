# Approval packet: Daily regime label producer for the live trailing-stop confirmation

## What is being built

Every trading session, each symphony's trailing stop confirms after the number of ticks its regime label calls for, using a label computed off-hours from that symphony's trailing daily close-to-close returns as the design specifies, for the operator whose live exits then follow the regime rule the autotune tunes under; three replay-side gaps remain and are named (the replay's day value is not a close-to-close return; its window's members are the dates in its history, not the sessions the job counts, so the two windows hold different sessions; and its validation and frozen-fold head days run at the default count; R-3, R-6).
- A daily off-hours job writes each symphony's label for the next trading session (AC-1).
- The label is computed on the design basis (daily close-to-close returns of the stored holdings) over a 20-session trailing window of computable sessions, with no lookahead (AC-2).
- The engine applies the session's label to the trailing-stop confirmation count (AC-3).
- A label not computed for the current session is never applied; the engine uses the default count (AC-4).
- A symphony the job cannot label stays unlabelled without stopping the others (AC-5).
- The job stays off the 1-minute path, off every route and away from every order path, and the protected modules stay unchanged (AC-6).
- Each run logs one line per symphony and one summary line (AC-7).

## Acceptance criteria and scenarios

### AC-1: Each trading session gets a label row written before it opens
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

Negative coverage: N1 S-1.2, S-1.3, S-1.4, S-1.5, S-1.8 | N2 n/a: the job answers no request, so it has nothing to refuse | N3 covered by AC-5 (S-5.2, S-5.3, S-5.7) | N4 covered by AC-6 (S-6.5) | N5 S-1.6, S-1.7 | N6 covered by AC-5 (S-5.1, S-5.4, S-5.5) | N7 S-1.9 | N8 S-1.10

### AC-2: The label uses the design basis over 20 computable sessions (not the replay's window members), with no lookahead
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

Negative coverage: N1 S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.10, S-2.11 | N2 n/a: computing a label answers no request | N3 covered by AC-5 (S-5.2, S-5.6, S-5.10) | N4 n/a: no user reaches the computation; it runs only inside the job (S-6.5) | N5 n/a: the label is a pure function of the stored holdings and the prices, so a repeated run gives the same label (S-1.6) | N6 covered by AC-5 (S-5.1, S-5.5) | N7 S-2.9 | N8 n/a: no edit is held open

### AC-3: The engine applies the session's label to the trailing-stop confirmation
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

Negative coverage: N1 S-3.5, S-3.6, S-3.10, S-3.11 | N2 n/a: the engine answers no request on this path; the rule sets a confirmation count | N3 S-3.4 | N4 n/a: no user acts on this path; `LIVE_EXECUTION` and `live_mode` gating is unchanged (NB-11) | N5 n/a: the daemon runs one engine process at a time (`app.py:1423-1441`) | N6 S-3.7 | N7 S-3.8, S-3.9 | N8 n/a: no edit is held open

### AC-4: A label not computed for the current session is never applied
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

Negative coverage: N1 S-4.4, S-4.5, S-4.6, S-4.8, S-4.11, S-4.12 | N2 n/a: the engine answers no request; it falls back to the default count | N3 S-4.7 | N4 n/a: no user acts on this path | N5 S-4.9 | N6 S-4.2, S-4.3 | N7 S-4.10 | N8 n/a: no edit is held open

### AC-5: A symphony the job cannot label gets no label, and the others are labelled
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

Negative coverage: N1 S-5.2, S-5.3, S-5.6, S-5.10 | N2 n/a: the job answers no request | N3 S-5.7 | N4 n/a: no user reaches the job (S-6.5) | N5 covered by AC-1 (S-1.6) | N6 S-5.4, S-5.5, S-5.8, S-5.9, S-5.12 | N7 S-5.11 | N8 n/a: no edit is held open

### AC-6: The job stays off the 1-minute path, off every route and away from every order path
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

Negative coverage: N1 S-6.8 | N2 n/a: no request is refused | N3 n/a: the read's absent case is AC-4 (S-4.7) | N4 S-6.5 | N5 n/a: a repeated run is AC-1 (S-1.6) | N6 S-6.7 | N7 S-6.2, S-6.3, S-6.4, S-6.6 | N8 n/a: no edit is held open

### AC-7: Each run logs one line per symphony and one summary line
Rule: When the regime label job finishes handling a symphony, the job shall write one line to the daemon log (the `alphabot` logger, `app.py:619-623`) naming the symphony's Composer hash and display name, the target session D, and either the label with the window's first and last session dates and session count or a reason code, and when the run ends it shall write one summary line with D and the counts of symphonies seen, labelled, and not labelled per reason code.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | two symphonies labelled and one with insufficient history | the run completes | three per-symphony lines and one summary line whose counts are 3 seen, 2 labelled and 1 `insufficient_history` |
| S-7.2 | N1 | one symphony per reason: `insufficient_history`, `no_holdings`, `price_fetch_failed`, `no_classifier_label`, `write_failed`, `after_open`, `unexpected_error` | the run completes | each line carries its own reason code and the summary counts each once |
| S-7.3 | N6 | the job fails before handling any symphony (the stored bot state cannot be read) | the run ends | one summary line naming the failure by exception class name only, with zero labelled |
| S-7.4 | N7 | any run, failures included | the lines are written | no line contains an API key, a request header, a response body or an exception message; failures appear as class names only |
| S-7.5 | N3 | the stored bot state holds no symphony | the run completes | no per-symphony line; the summary reports zero seen |

Negative coverage: N1 S-7.2 | N2 n/a: the job answers no request | N3 S-7.5 | N4 n/a: the log is read by the operator on the host, no route serves it | N5 n/a: a repeated run writes its own lines (S-1.6) | N6 S-7.3 | N7 S-7.4 | N8 n/a: no edit is held open

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-3: The return series uses the replay's basis and window; maps to AC-2; left out: per PM ruling D-5, each session's value is the design basis (allocation-weighted close-to-close return from consolidated SIP daily closes, D-11), not the replay's own day value (the first holding's last 1-minute IEX bar with positional matching, built from IEX bars, Known fact 4); the window length (20), units, holdings and strictly-before rule match the replay, but window membership does not: the job counts NYSE sessions on which every holding has both SIP closes, within 30 sessions (D-8), while the replay counts the dates in its history, which exist only when the first holding has 1-minute IEX bars and have no look-back bound (`autotuner.py:1651`, `synthetic_history.py:806-807`, `synthetic_history.py:838-840`); labels agree with the replay only where the day values and the window members coincide (S-2.12; Known-Unconfirmed 1)
R-6: Live and replay apply the same exit rule; maps to AC-2, AC-3; left out: both apply the same label-to-count rule and the same window length (20), but not the same window members, and (a) the replay's day value is not the design's close-to-close return, so a label can differ near a threshold (Known-Unconfirmed 1, D-5), and (b) the replay's validation and frozen-fold head days resolve the default count, because those paths hand the replay fold-sliced history dicts whose first 20 days have no trailing window (`autotuner.py:2529-2535`, `autotuner.py:2940-2945`, `autotuner.py:2993-2999`, `autotuner.py:3012-3018`; Known-Unconfirmed 10, D-7), and (c) window membership differs (R-3; Known-Unconfirmed 1); fixing the replay is excluded by G-24 (Follow-Ups)

## Open questions

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

Sensitive: money (AC-1, AC-2, AC-3, AC-4, AC-5, AC-6). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
