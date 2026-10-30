# Approval packet: Server state truth: one dated, consistent `/api/state` and a flat close snapshot

## What is being built

The operator sees the same, dated numbers from the server render and from every poll in every market state, because `/api/state` computes the portfolio once, states the day and moment of its data, keeps after-close figures fixed, never mixes two engine cycles, and serves a close snapshot that no longer nests every earlier one.
- The close snapshot holds only symphonies, and the stored chain is repaired once, after a backup of the pre-repair row (AC-1).
- One portfolio computation per response; the server render equals the first poll (AC-2).
- The frozen branch states its trading day and flags an earlier session (AC-3).
- Every freshness stamp names the data's ET date and time, never the request time (AC-4).
- After-close figures and the window routes stay fixed on the snapshot (AC-5).
- The frozen branch carries each card's state, values and verdict (AC-6).
- No response pairs two engine cycles (AC-7).
- Dead client writers and unread server fields are removed (AC-8).
- The verdict label says whether its figure moves (AC-9).

## Acceptance criteria and scenarios

### AC-1: The close snapshot holds only symphonies, and the stored chain is repaired once
Rule: When the engine's EOD step writes the close snapshot for a trading day, the snapshot's account map shall hold only symphony entries (a dict with a `name`), never `last_market_close_snapshot` or any other non-symphony key at any depth; and once, after this plan deploys, at daemon start before the scheduler thread starts, the daemon shall first save a copy of the pre-repair state row and then rewrite the stored snapshot without its nested chain while every other value in it stays byte-equal; if the copy cannot be saved, it shall not repair that start, shall log the reason with the exception type name only, and shall try again at the next start.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | a stored snapshot for day D-1 that nests earlier snapshots, and 12 symphony entries in `bot_state` | the EOD step writes day D's snapshot | its account map lists exactly the 12 symphony entries; no entry has `id` "last_market_close_snapshot"; no key `last_market_close_snapshot` appears at any depth inside it |
| S-1.2 | B | the stored snapshot captured from production, 86 snapshots deep | the one-time repair runs | the stored snapshot holds no nested snapshot; its `trading_day`, `captured_at_et`, `data_as_of`, `portfolio_strip`, `shadow_divergence` and every symphony entry are byte-equal to before; its account map has no "Unknown" key when the phantom was that key's only entry |
| S-1.3 | B | the repaired state, after the close | `GET /api/state` is served and `/` polls | `bot_state` and `state` hold one entry per symphony and no `last_market_close_snapshot` key; the page requests no `/api/chart/last_market_close_snapshot` |
| S-1.4 | N1 | a real symphony with no `account` field | the EOD step writes the snapshot | the symphony is kept, under "Unknown", as at the ref |
| S-1.5 | N1 | `bot_state` also holds a dict-valued key with no `name` (for example `fleet_correlation_alert`) | the EOD step writes the snapshot | that key is not in the account map |
| S-1.6 | N3 | a stored state with no close snapshot | the repair runs | the state row is unchanged |
| S-1.7 | N5 | the repair has already run | the daemon restarts and the repair runs again | the state row is unchanged |
| S-1.8 | N5 | an engine cycle saves `bot_state` while the repair runs | both writes finish | the stored snapshot is flat and every field the engine cycle wrote is present |
| S-1.9 | N7 | any EOD step | it runs | the trading-day guard, `post_mortem_run`, `last_successful_cycle_at`, the post-mortem file, the divergence values and the snapshot's `portfolio_strip` equal the ref's for the same inputs; `math_engine.py` has zero diff and `alpha_bot_execution.py` differs only inside `alpha_bot_execution.py:1115-1163` |
| S-1.10 | N6 | the state row is locked when the repair writes | the write fails | the stored state is unchanged, one ERROR line names the failure, the daemon serves as at the ref, and the repair runs again at the next daemon start |
| S-1.11 | N1 | a flat stored snapshot for D-1 | the EOD step writes D | the new snapshot is flat; the stored row does not carry D-1's snapshot inside D's |
| S-1.12 | B | the nested stored state of S-1.2 | the repair runs at daemon start | before the state row is written, a copy byte-equal to the pre-repair row is saved (to a file beside the state database or an additive table, a Gate-2 choice), and restoring that copy by hand gives back the pre-repair row exactly |
| S-1.13 | N6 | the backup copy cannot be written (for example a full disk or a read-only directory) | the daemon starts | the state row is not touched; one log line gives the reason as the exception type name only, never a path, value or traceback; the daemon serves as at the ref; the next start tries the copy and the repair again |
| S-1.14 | N7 | the repaired, flat stored snapshot | the flush-resync background reset saves `bot_state` | the stored snapshot is still flat and byte-equal to before the reset |
| S-1.15 | N7 | the repaired, flat stored snapshot | the startup seed runs | the stored snapshot is still flat and byte-equal; the seed adds no entry for a reserved key |
| S-1.16 | N7 | the repaired, flat stored snapshot | the engine's closed-path and per-cycle saves run | the stored snapshot is still flat and byte-equal until the next EOD step writes a new flat one |

Negative coverage: N1 S-1.4, S-1.5, S-1.11 | N2 n/a: the EOD step and the repair refuse no input | N3 S-1.6 | N4 n/a: engine and daemon-start paths with no caller identity | N5 S-1.7, S-1.8 | N6 S-1.10, S-1.13 | N7 S-1.9, S-1.14, S-1.15, S-1.16 | N8 n/a: no edit is held open

### AC-2: One portfolio computation per response, and the server render equals the first poll
Rule: When `/api/state` is served in any market state, every portfolio figure in it (`meta.portfolio` and `portfolio_strip`) shall come from one computation over one read of state, and when `GET /` and the first poll are served with no engine cycle between them, every value the page renders from them shall be identical.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the market is open | `GET /api/state` | `meta.portfolio.data_as_of` equals `portfolio_strip.data_as_of`; `meta.portfolio.guard_alpha` equals `portfolio_strip.guard_alpha`; `meta.portfolio.tc` and `tc_if_held` equal `portfolio_strip.today_change.dry_run` and `.if_held`; `cr` and `cr_if_held` equal the cumulative pair; `meta.portfolio.account_value` equals `portfolio_strip.account_value` rounded to two places |
| S-2.2 | B | `closed_frozen` with a snapshot | `GET /api/state` | the S-2.1 relations hold |
| S-2.3 | B | `pre_market` with a snapshot | `GET /api/state` | the S-2.1 relations hold |
| S-2.4 | B | each of `open`, `closed_frozen` and `pre_market` | `GET /` and then one poll, with no engine cycle between | the Today, Cumulative and Max DD row texts, the guard-alpha headline, the tracked, armed and triggered counts and the as-of text are identical on the render and after the poll |
| S-2.5 | N3 | the portfolio computation raises | `GET /api/state` | `meta.portfolio` and `portfolio_strip` carry the same null figures, and the stamp reads `--` (AC-4) |
| S-2.6 | N7 | the same stored state as at the ref | `GET /api/state` | each row and headline figure equals the ref's `portfolio_strip` value for that state in every branch |
| S-2.7 | B | any market state | `GET /api/state` | the response makes exactly one portfolio-strip computation (a counting seam), and the frozen branch makes no live one |
| S-2.8 | N6 | account totals unavailable (cache masked and no last-good) | `GET /api/state` | both `meta.portfolio` and `portfolio_strip` carry the value-weighted figures and `portfolio_strip.basis` reads "value_weighted", as at the ref |
| S-2.9 | N5 | two polls in flight at once | both are served | each response satisfies the S-2.1 relations on its own |

Negative coverage: N1 n/a: the route takes no input that changes the computation besides `account`, whose effect is ux-overhaul 05 AC-6's and is unchanged | N2 n/a: the route refuses nothing | N3 S-2.5 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-2.9 | N6 S-2.8 | N7 S-2.6 | N8 n/a: no edit state

### AC-3: The frozen branch states its trading day and flags an earlier session
Rule: When `/api/state` is served in `closed_frozen` or `pre_market`, the response shall carry the snapshot's `trading_day`, its `frozen_at` and `data_as_of` shall name that date (SD-1), and if the snapshot's trading day is earlier than the most recent completed session, then `data_as_of` shall end with " | previous session" and the response shall carry `snapshot_is_previous_session: true`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | 18:00 ET on trading day D, the snapshot for D captured at 16:00:42 ET | `GET /api/state`, then `/` renders it | `trading_day` is D; `frozen_at` reads "D 16:00:42 ET"; `data_as_of` reads "D 16:00 ET"; `snapshot_is_previous_session` is false; `#hero-data-as-of` reads "data as of D 16:00 ET" |
| S-3.2 | N1 | 16:00:20 ET on D, before the EOD step commits, the snapshot for D-1 | `GET /api/state` | `trading_day` is D-1; `data_as_of` reads "D-1 16:00 ET | previous session"; `snapshot_is_previous_session` is true |
| S-3.3 | B | 08:00 ET on a Monday, Friday's snapshot | `GET /api/state` | `trading_day` is Friday's date; no previous-session marker, because Friday is the most recent completed session |
| S-3.4 | N1 | a weekday market holiday evening, the snapshot from the session before it | `GET /api/state` | no previous-session marker |
| S-3.5 | N3 | a legacy snapshot with no `trading_day` | `GET /api/state` | `trading_day` is null and `data_as_of` reads "16:00 ET (date not recorded)" |
| S-3.6 | N3 | no snapshot exists | `GET /api/state` after the close | the waiting response with its `notice` returns as at the ref (AC-DM.3.4) |
| S-3.7 | N6 | the daemon was down from 15:59 to 16:06, so no snapshot for D was written | `/api/state` is polled through the evening | every response carries D-1's figures with the previous-session marker |
| S-3.8 | N1 | a half-day whose session closes at 13:00 | `GET /api/state` at 13:00:20 before the EOD step commits | the D-1 snapshot carries the previous-session marker |
| S-3.9 | N7 | any frozen response | it is served | the row, headline and card figures are the ones AC-5 and AC-6 fix; only the date fields and the marker are added |
| S-3.10 | N5 | two polls at 18:00 and 18:10 with no new snapshot | both are served | the date fields are identical |

Negative coverage: N1 S-3.2, S-3.4, S-3.8 | N2 n/a: the route refuses nothing | N3 S-3.5, S-3.6 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-3.10 | N6 S-3.7 | N7 S-3.9 | N8 n/a: no edit state

### AC-4: Every freshness stamp names the data's moment, never the request's
Rule: When `/api/state` or the server render of `/` emits a freshness stamp, the stamp shall read the ET date and time of the newest engine observation of the completed cycle whose values the response shows (the newest `shadow_history` row of that cycle, in SD-1's form), shall read `--` when no such moment exists, and shall never fall back to the request time.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | on day D the newest recorded observation is at 10:43 ET and `last_successful_cycle_at` is 10:43 ET | `GET /api/state` | `data_as_of`, `meta.portfolio.data_as_of` and `portfolio_strip.data_as_of` read "D 10:43 ET" |
| S-4.2 | N6 | cycles at 10:44 and 10:45 recorded no observation because every Composer fetch failed, while `last_successful_cycle_at` reads 10:45 | `GET /api/state` | the stamps read "D 10:43 ET" |
| S-4.3 | N3 | no observation and no cycle time exist | `GET /api/state` and `GET /` | the stamps read `--`; `#hero-data-as-of` reads "data as of --" |
| S-4.4 | N3 | `last_successful_cycle_at` is unparseable | `GET /api/state` | the stamps name the newest observation's moment, or `--` when none exists |
| S-4.5 | N6 | the portfolio computation raises | `GET /api/state` | the stamps read `--`, never the request time |
| S-4.6 | N1 | 09:30:05 ET on day D, before the first cycle completes, the newest observation from D-1 at 15:52 ET | `GET /api/state` | the stamps read "D-1 15:52 ET" |
| S-4.7 | N1 | the newest observation is at 23:59 ET on D-1 and the request is at 00:01 ET on D | `GET /api/state` | the stamp names D-1 |
| S-4.8 | N7 | account totals were read at 10:41:07 ET | `GET /api/state` with the cache stale | `portfolio_strip.account_basis_as_of` reads "D 10:41:07 ET" in its existing form, and the chip markers are as at the ref |
| S-4.9 | N5 | two requests in the same minute with no new observation | both are served | the stamps are identical |
| S-4.10 | B | `closed_frozen` at 21:00 ET on D with D's snapshot captured at 16:00:42 ET | `GET /api/state` | every stamp names "D 16:00 ET", the snapshot's moment, never 21:00 |
| S-4.11 | B | `pre_market` at 07:00 ET on D+1 with D's snapshot | `GET /api/state` | every stamp names "D 16:00 ET" |
| S-4.12 | N8 | minute M's shadow rows are committed at 10:44:05 and its `bot_state` is not (AC-7), the M-1 cycle's newest observation at 10:43:05 | `GET /api/state` | the stamps read "D 10:43 ET", the cycle whose values the response shows, never 10:44 |

Negative coverage: N1 S-4.6, S-4.7 | N2 n/a: the route refuses nothing | N3 S-4.3, S-4.4 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-4.9 | N6 S-4.2, S-4.5 | N7 S-4.8 | N8 S-4.12

### AC-5: After the close, the figures stay fixed on the snapshot and the window routes agree
Rule: While `/api/state` serves one close snapshot, its Today and Cumulative figures, account value and "Account | all-time" figure (`account_all_time_cr`) shall be identical on every poll, computed with the account totals of the snapshot's moment (Q-1), and `/api/strip/{window}` and `/api/hero-chart/{window}` requested in `closed_frozen` or `pre_market` shall compute from the same snapshot state, so the 30d strip's guard alpha equals the frozen poll's.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | after the close, one snapshot, and an account-totals refresh with different values between two polls ten minutes apart | both polls are served | `portfolio_strip.today_change`, `cumulative_return` and `account_value` are identical in both |
| S-5.2 | B | after the close | `GET /api/strip/30d` | its `guard_alpha` equals the frozen poll's `portfolio_strip.guard_alpha` |
| S-5.3 | B | after the close, live `bot_state` differing from the snapshot's entries | `GET /api/strip/1y` | the strip is computed from the snapshot's symphony entries, not from live `bot_state` |
| S-5.4 | B | after the close | `GET /api/hero-chart/30d` | the series ends at the snapshot's trading day and equals the series the frozen poll's hero uses for that window |
| S-5.5 | N3 | no account-totals read has succeeded since the snapshot's capture | the frozen poll is served | the figures use the last-good totals with `account_basis_stale` true and its as-of, as at the ref, and stay fixed across polls until the first read after the capture succeeds |
| S-5.6 | N6 | Composer fails all evening | polls are served every 30 s | the figures stay fixed on the last-good totals with the stale chip |
| S-5.7 | N7 | the market is open | polls are served | the live branch reads the account totals per request, as at the ref |
| S-5.8 | N1 | 08:00 ET Monday | the pre-market poll is served | the figures equal the ones served on Friday evening after Friday's account read |
| S-5.9 | N5 | a new snapshot is written for the next trading day | the next poll is served | the figures move to the new snapshot and its own account totals |
| S-5.10 | N1 | an unknown window token after the close | `GET /api/strip/{window}` | 404 `{"error": "unknown window token"}`, as at the ref |
| S-5.11 | B | after the close, an account-totals refresh after the snapshot's first poll | `GET /` | the Today and Cumulative rows and the account value equal the frozen poll's fixed figures |
| S-5.12 | B | after the close, two polls ten minutes apart with an account-totals refresh between them | both are served | `account_all_time_cr` is identical in both (at the ref the frozen branch reads it from the live cache at request time, `app.py:3001`) |

Negative coverage: N1 S-5.8, S-5.10 | N2 n/a: the routes refuse only unknown tokens, unchanged (S-5.10) | N3 S-5.5 | N4 n/a: the routes are behind the global auth hook, unchanged | N5 S-5.9 | N6 S-5.6 | N7 S-5.7 | N8 n/a: no edit state

### AC-6: The frozen branch carries every card's state, values and verdict
Rule: When `/api/state` is served in `closed_frozen` or `pre_market`, it shall carry a `symphonies` card list built from the snapshot's entries (status, Today, Cumulative and Max DD legs per symphony, with its account) and, for each triggered entry, the `guard_alpha` the live branch's rule gives for that entry; and the server render of `/` in the same state shall render its cards from the same values.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | a snapshot entry T, triggered, with `current_return` c, and T's latest exit row with `at_return` a | `GET /api/state` after the close | `bot_state[T].guard_alpha` equals a minus c rounded to six places; the `symphonies` entry for T has status "triggered" and the Today, Cumulative and Max DD legs the branch computes for T |
| S-6.2 | B | the same snapshot | `GET /` after the close, then one poll | each card's status pill, Today, Cumulative and Max DD values, section placement and verdict text equal the poll's `symphonies` entry and `guard_alpha` |
| S-6.3 | N3 | a triggered snapshot entry with no exit row | `GET /api/state` | no `guard_alpha` for it; its verdict renders the dash on both paths |
| S-6.4 | N3 | a triggered entry whose `current_return` is absent | `GET /api/state` in either branch | no `guard_alpha` for it, never one computed against 0.0 (the live branch stops coercing at `app.py:3416`) |
| S-6.5 | N1 | 08:00 ET pre-market with yesterday's snapshot | `GET /` and the first poll | each card's Today legs equal the frozen poll's, never a new-day Bot dash beside yesterday's Held |
| S-6.6 | N7 | the market is open | `GET /api/state` | the live `symphonies` list and every `guard_alpha` equal the ref's for the same state, except S-6.4's absent case |
| S-6.7 | N7 | an account picked on `/` after the close | `GET /api/state?account=` | the frozen branch still ignores the filter; each `symphonies` entry carries its account id, so the client narrowing of ux-overhaul 05 S-6.8 keeps working |
| S-6.8 | N6 | the exit-rows read fails | `GET /api/state` after the close | 200 with no `guard_alpha` on any entry |
| S-6.9 | N5 | three polls on one snapshot | they are served | each triggered entry's `guard_alpha` and card values are identical |

Negative coverage: N1 S-6.5 | N2 n/a: the route refuses nothing | N3 S-6.3, S-6.4 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-6.9 | N6 S-6.8 | N7 S-6.6, S-6.7 | N8 n/a: no edit state

### AC-7: No response pairs two engine cycles
Rule: If `/api/state` is served while an engine cycle has committed its `shadow_history` rows but not its `bot_state`, then every card's Bot and Held legs, its alpha badges, the pills and the hero series' last point shall come from the same, completed cycle.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the shadow rows for minute M are committed and `bot_state` for M is not | `GET /api/state` | every card's Bot and Held legs and the hero's last point come from minute M-1 |
| S-7.2 | B | minute M's `bot_state` is committed | `GET /api/state` | they come from minute M |
| S-7.3 | N3 | a symphony with no shadow row before minute M | `GET /api/state` mid-cycle | its Bot leg is absent and renders the dash, as at the ref |
| S-7.4 | N7 | the poll the cycle event triggers, after the engine exits | it is served | the values equal the ref's for the same committed state |
| S-7.5 | N6 | the state read or the request's committed-read connection fails | the route answers | 500 with its error body, never a partial mix; a read that fails inside one analytics helper still renders that leg as the dash, as at the ref (`analytics.py:618-659`), and never mixes cycles |
| S-7.6 | N5 | a cycle event and a timer poll arrive within the same cycle | both are served | each response's legs come from one cycle |

Negative coverage: N1 n/a: the route takes no input bearing on the cycle | N2 n/a: the route refuses nothing | N3 S-7.3 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-7.6 | N6 S-7.5 | N7 S-7.4 | N8 n/a: no edit state

### AC-8: Dead client writers and unread server fields are removed
Rule: When `/` refreshes, the dashboard script shall write only elements the dashboard template contains, and when `/api/state` is served, it shall compute no field that no client reads, apart from the documented fields Q-4 keeps.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | `static/index.js` and `templates/index.html` | a scan lists every element id and selector the script writes | each exists in the template; `updateStatusStrip`, `updateMarketDot` and the `#hero-ticker` writer are gone |
| S-8.2 | B | any market state | `GET /api/state` | `meta.triggers_today` is absent and its query is not run |
| S-8.3 | B | the frozen branch | `GET /api/state` | the per-symphony Today, Cumulative and Max DD values it computes are read by the `symphonies` list (AC-6) |
| S-8.4 | N7 | every other `meta` and top-level field | `GET /api/state` | present with the same meaning as at the ref, the Q-4 fields included |
| S-8.5 | B | `static/index.js` | it is read | the comment beside the poll interval states 30 s, the value `POLL_INTERVAL_MS` holds |

Negative coverage: N1 n/a: no input | N2 n/a: nothing is refused | N3 n/a: the removed field had no reader, so no absent state is rendered | N4 n/a: no access change | N5 n/a: removing a computation has no concurrent path | N6 n/a: no dependency is added | N7 S-8.4 | N8 n/a: no edit state

### AC-9: The verdict label says whether its figure moves
Rule: When a triggered card shows its guard-alpha verdict, the label beside the figure shall read "vs held now" while the market is open, because the figure is the exit return minus the live held return and moves each cycle, and "at close" in the frozen branch, and never "at exit"; the figure's computation stays as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the market is open and a triggered symphony's held return moves between two cycles | `GET /` and polls across both cycles | the figure changes and the label reads "vs held now" on the render and on every poll |
| S-9.2 | B | after the close | `GET /` and three polls | the label reads "at close" and the figure is identical on each |
| S-9.3 | N1 | the figure crosses zero between cycles | the next poll lands | the text switches between "Early exit | gave up" and "Good call | saved" and the label still reads "vs held now" |
| S-9.4 | N3 | no guard alpha | the card renders | the verdict reads the dash with no label |
| S-9.5 | N7 | any triggered symphony | the figure is computed | it equals the latest exit's `at_return` minus `current_return`, as at the ref (`app.py:3416-3418`) |
| S-9.6 | N5 | the market is open and three polls land with no engine cycle between them | they land | the verdict text is identical on each |
| S-9.7 | N1 | a guard alpha of exactly 0 | the card renders | "Early exit | gave up 0.0%\u03b1" with the label, on both paths |

Negative coverage: N1 S-9.3, S-9.7 | N2 n/a: display only | N3 S-9.4 | N4 n/a: `/` is behind the global auth hook, unchanged | N5 S-9.6 | N6 n/a: a failed poll leaves the verdict as it was (plan 03 AC-2) | N7 S-9.5 | N8 n/a: the card holds no edit state

## Rule-by-entry-point matrix

| Rule | `GET /api/state` open | `GET /api/state` closed_frozen | `GET /api/state` pre_market | `GET /` server render | `GET /api/strip/{window}` | `GET /api/hero-chart/{window}` |
|---|---|---|---|---|---|---|
| AC-2 one computation, render equals poll | S-2.1 | S-2.2 | S-2.3 | S-2.4 | n/a: the strip route makes its own windowed computation by design | n/a: the chart route returns a series, no portfolio figures |
| AC-3 dated frozen stamp and marker | n/a: the live branch has no snapshot | S-3.1, S-3.2 | S-3.3 | S-3.1 | n/a: carries no stamp at the ref (`static/index.js:997-1018` relies on that) | n/a: carries no stamp |
| AC-4 stamp names the data's moment | S-4.1, S-4.2 | S-4.10 | S-4.11 | S-4.3 | n/a: carries no stamp | n/a: carries no stamp |
| AC-5 frozen figures fixed on the snapshot | S-5.7 | S-5.1 | S-5.8 | S-5.11 | S-5.2, S-5.3 | S-5.4 |
| AC-6 cards and verdicts from one source | S-6.6 | S-6.1 | S-6.5 | S-6.2 | n/a: no card data | n/a: no card data |
| AC-7 one cycle per response | S-7.1 | n/a: the snapshot is one moment | n/a: the snapshot is one moment | S-7.1 | n/a: no card legs | n/a: no card legs |
| AC-1 close snapshot holds only symphonies | n/a: the live branch serves no snapshot | S-1.3 | S-1.3 | S-1.3 | n/a: reads no snapshot | n/a: reads no snapshot |

| Rule | Engine EOD close-snapshot builder | Daemon-start snapshot repair | Engine per-cycle `bot_state` save | Flush-resync background reset | Startup seed | Account-totals refresh job |
|---|---|---|---|---|---|---|
| AC-1 close snapshot holds only symphonies, repaired once | S-1.1, S-1.11 | S-1.2, S-1.12, S-1.13 | S-1.8, S-1.16 | S-1.14 | S-1.15 | n/a: it writes only the in-memory account totals, never `bot_state` |
| AC-5 frozen figures fixed on the snapshot | n/a: writes no account totals | n/a: writes no account totals | n/a: writes no account totals | n/a: its refresh is the account-totals job's case | n/a: writes no account totals | S-5.1, S-5.5 |

## Not Building

Non-goals:
- NB-2: Removing the `/api/state` fields no static script reads (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`); they are part of the route's documented contract and its tests, and with the nested chain gone and compression on (plan 05) their size no longer drives the poll's cost (narrows R-54; approved by the PM, [PM-ASSUMED]; Q-4).
- NB-6: Fixing post-mortem stage 1's `total_monitored` count of non-symphony keys (`reporting.py:51-55`) and the EOD divergence loop's false "coverage gap" ERROR for the snapshot key (`alpha_bot_execution.py:1066-1075`); both lie outside the builder the PM allowed (overview NB-16; approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-1: The engine's exit decisions, orders and cadence: `math_engine.py` has zero diff and `alpha_bot_execution.py` changes only inside the close-snapshot builder (overview NB-5, NB-8).
- NB-3: The account-filter behaviour of `/api/state` (ux-overhaul 05 AC-6) and the frozen branch's indifference to `?account=` (S-6.7).
- NB-4: Every metric's formula. Three value changes are made, and only these: one computation replaces two (AC-2, inside G-57's exception); the frozen figures read their account totals as of the snapshot (AC-5); and a guard alpha whose `current_return` is absent is left absent instead of computed against 0.0 (S-6.4). The last two go beyond G-57 and are raised for the operator's ruling (overview NB-10, SD-12; approved by the PM, [PM-ASSUMED]).

Deferred:
- NB-5: The covered findings TA-11 and TA-43 to TA-52 stay with their plans (overview NB-12).

Protected surfaces consumed, not changed:
- NB-7: `POST /api/sell_account`, `POST /api/trigger`, `LIVE_EXECUTION`, the settings allowlist and the no-trade boundary tests (overview NB-14).

## Narrowed mappings

R-1: Every timing issue the refresh-timing audit found is resolved; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9; left out: this plan's part is the server findings G-63 assigns it
R-2: No shown value changes, reverts or flickers when no new data arrived; maps to AC-2, AC-5, AC-7, AC-9; left out: this plan's part is the server's values; the client writers are plan 03's
R-4: Every number, chart, label and status on every page shows the newest data the engine has produced; maps to AC-3, AC-4, AC-6; left out: this plan's part is `/api/state` and the server render of `/`
R-5: Stale or wrong-day data is shown only with a dated, honest cue; maps to AC-3, AC-4; left out: the server's stamps; the client's age cue is plan 03's
R-7: The suite covers the pages, the scheduler, the close snapshot, `/api/state` and the polled GET routes; maps to AC-1, AC-2, AC-5; left out: this plan's surfaces are the close snapshot, `/api/state`, the server render of `/`, `/api/strip/{window}` and `/api/hero-chart/{window}`
R-9: A whole trading day on `/` shows correct, current values; maps to AC-3, AC-4, AC-5, AC-6, AC-7; left out: the server's part; the 09:30 and 15:53 header labels are plan 02's
R-13: Every freshness stamp carries a date and its true age; maps to AC-3, AC-4; left out: the date and the data moment of the server's stamps; "true age" on screen is plan 03's age cue
R-54: No renderer writes to a missing element, and the server stops computing what no client reads; maps to AC-8, NB-2; left out: the unread but documented `/api/state` fields (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`) stay (Q-4)

## User tasks

Workflow (Dashboard `/`): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Read the dashboard after the close and before the open
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/` at 18:00 ET on a trading day | that day's close snapshot | The as-of reads that day's date and 16:0x ET; the rows, headline and cards are the same on the page load and on every poll | S-3.1, S-2.4, S-6.2 |
| 2 | Leave it open ten minutes | account totals refresh twice a minute | No row, card or verdict changes | S-5.1, S-6.9 |
| 3 | Open `/` at 08:00 ET on Monday | Friday's snapshot | The as-of names Friday's date; cards match the rows | S-3.3, S-6.5 |

### T-2: Read a triggered card during the day
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Watch a triggered card across two engine cycles | its held return moves | The verdict figure moves and its label reads "vs held now", never "at exit" | S-9.1 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | After the close, which account totals should the frozen Today and Cumulative figures use, so they stop drifting all evening? | AC-5 | The first successful account-totals read at or after the snapshot's capture, kept for that snapshot; until it exists, the last-good totals with the stale chip; a daemon restart re-reads once | default approved by the PM, [PM-ASSUMED] |
| Q-2 | The verdict figure moves every cycle while its label says "at exit". Label it truthfully, or freeze the figure at the moment of exit? | AC-9 | Label it truthfully: "vs held now" while the market is open and "at close" after it. Freezing at exit would read 0 for every card, because the exit row's `at_return` is the symphony's `current_return` at that moment (`alpha_bot_execution.py:1949`) | default approved by the PM, [PM-ASSUMED] |
| Q-3 | When does the one-time repair run? | AC-1 | At daemon start, before the scheduler thread starts, so no engine cycle runs during it; a failed write leaves the state unchanged and retries at the next start | default approved by the PM, with a pre-repair backup added (G-83 to G-86, S-1.12, S-1.13), [PM-ASSUMED] |
| Q-4 | Should `/api/state` stop sending the fields no static script reads (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`)? | AC-8 | No: keep them; they are the route's documented contract and are pinned by tests, and their cost falls with AC-1 and plan 05's compression | default approved by the PM, [PM-ASSUMED] |

Sensitive: money (AC-1). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
